"""CLI: etl → build → validate → report → audit."""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
import traceback
from pathlib import Path
from typing import Any

from . import db
from .activities import BuildResult, build
from .audit import write_audit
from .config import Settings, get_settings
from .etl import EtlResult, run_etl
from .fetxml import FetXmlError, load_constraints, render, write
from .report import read_json, write_conflicts, write_final_report, write_xlsx
from .validate import ValidationResult, validate_file, validation_markdown

SEMESTER = "Semester_2_2026_2027"


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-я]+", "_", value).strip("_")
    return cleaned or "scope"


def etl_payload(settings: Settings, result: EtlResult) -> dict[str, Any]:
    return {
        "avtor_out": str(settings.avtor_out),
        "periods": [dataclasses.asdict(period) for period in result.periods],
        "rows_total": result.rows_total,
        "rows_loaded": result.rows_loaded,
        "duplicates": result.duplicates,
        "dates": result.dates,
    }


def build_payload(name: str, path: Path, result: BuildResult) -> dict[str, Any]:
    return {
        "name": name,
        "file": str(path),
        "days": len({activity.date for activity in result.activities}),
        "stats": result.stats,
        "subgroups": {group: list(codes) for group, codes in result.subgroups.items()},
        "conflicts": [dataclasses.asdict(item) for item in result.conflicts],
        "overloads": [dataclasses.asdict(item) for item in result.overloads],
        "revisions": [dataclasses.asdict(item) for item in result.revisions],
    }


def command_etl(settings: Settings) -> int:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_etl(settings)
    payload = etl_payload(settings, result)
    settings.out_dir.mkdir(parents=True, exist_ok=True)
    (settings.out_dir / "etl_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"ETL: периодов {len(result.periods)}, строк {result.rows_total}, дублей {result.duplicates}")
    return 0


def command_build(settings: Settings, scope: str | None) -> int:
    conn = db.connect(settings.db_path)
    try:
        if db.lesson_count(conn) == 0:
            print("База пуста. Сначала выполните `etl`.", file=sys.stderr)
            return 1
        periods = db.list_periods(conn)
        if not periods:
            print("Нет загруженных периодов.", file=sys.stderr)
            return 1

        if scope:
            targets: list[tuple[str, str | None]] = [(scope, scope)]
        else:
            targets = [(period.name, period.name) for period in periods]
            targets.append((SEMESTER, None))

        unavailable = load_constraints(settings.constraints)
        payloads: list[dict[str, Any]] = []
        for name, period in targets:
            rows = db.load_lessons(conn, period)
            if not rows:
                print(f"Пропуск {name}: нет строк", file=sys.stderr)
                continue
            result = build(rows)
            title = f"Расписание: {name}"
            comment = (
                "Точная копия выгрузки AVTOR. Год дат — как в источнике. "
                "Конфликтные активности не удалены (locked=false, вес 50)."
            )
            xml = render(
                result,
                school=settings.school,
                title=title,
                comment=comment,
                hours=settings.hours,
                unavailable=unavailable,
                room_capacity=settings.room_capacity,
            )
            path = settings.fet_dir / f"{safe_name(name)}.fet"
            write(path, xml)
            payload = build_payload(name, path, result)
            write_conflicts(settings, payload)
            payloads.append(payload)
            stats = result.stats
            print(
                f"{name}: активностей {stats['activities']}, групп {stats['groups']}, "
                f"подгрупп {stats['subgroups']}, конфликтов "
                f"{stats['conflicts_student'] + stats['conflicts_teacher'] + stats['conflicts_room']}, "
                f"накладок аудиторий {stats['room_clones']}"
            )

        settings.out_dir.mkdir(parents=True, exist_ok=True)
        (settings.out_dir / "build_summary.json").write_text(
            json.dumps({"builds": payloads}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return 0
    except FetXmlError as exc:
        print(f"Ошибка сборки .fet: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()


def command_validate(settings: Settings) -> int:
    builds_path = settings.out_dir / "build_summary.json"
    if not builds_path.exists():
        print("Нет сборки. Сначала выполните `build`.", file=sys.stderr)
        return 1
    payload = read_json(builds_path)
    summary: dict[str, Any] = {}
    for item in payload["builds"]:
        name = item["name"]
        result: ValidationResult = validate_file(settings, name, Path(item["file"]))
        summary[name] = {
            "file": result.file,
            "static_ok": result.static.ok,
            "errors": result.static.errors,
            "warnings": result.static.warnings,
            "counts": result.static.counts,
            "run": result.run is not None,
            "run_executed": bool(result.run and result.run.executed),
            "returncode": result.run.returncode if result.run else None,
            "fatal": result.run.fatal if result.run else [],
            "time_exceeded": result.run.time_exceeded if result.run else False,
            "soft_conflicts": result.run.soft_conflicts if result.run else None,
            "broken_soft": result.run.broken_soft if result.run else None,
            "workdir": result.run.workdir if result.run else "",
        }
        settings.validation_dir.mkdir(parents=True, exist_ok=True)
        (settings.validation_dir / f"{safe_name(name)}.md").write_text(
            validation_markdown(result), encoding="utf-8"
        )
        status = "OK" if result.ok else "ОШИБКИ"
        print(
            f"{name}: схема {'OK' if result.static.ok else 'ОШИБКИ'}, "
            f"fet-cl {'запущен' if result.run else 'нет'}, статус {status}"
        )
    (settings.validation_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if all(item["static_ok"] for item in summary.values()) else 2


def command_report(settings: Settings) -> int:
    etl_path = settings.out_dir / "etl_summary.json"
    builds_path = settings.out_dir / "build_summary.json"
    validation_path = settings.validation_dir / "summary.json"
    if not builds_path.exists():
        print("Нет сборки. Сначала выполните `build`.", file=sys.stderr)
        return 1
    etl = read_json(etl_path) if etl_path.exists() else {}
    builds = read_json(builds_path)["builds"]
    validation = read_json(validation_path) if validation_path.exists() else {}
    md = write_final_report(settings, etl, builds, validation)
    xlsx = write_xlsx(settings, etl, builds, validation)
    print(f"Отчёт: {md}")
    print(f"Отчёт: {xlsx}")
    return 0


def command_audit(settings: Settings) -> int:
    conn = db.connect(settings.db_path)
    try:
        if db.lesson_count(conn) == 0:
            print("База пуста. Сначала выполните `etl`.", file=sys.stderr)
            return 1
        path = write_audit(settings, conn)
    finally:
        conn.close()
    print(f"Аудит: {path}")
    return 0


def command_all(settings: Settings) -> int:
    code = command_etl(settings)
    if code:
        return code
    code = command_build(settings, None)
    if code:
        return code
    code = command_validate(settings)
    command_report(settings)
    command_audit(settings)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fetv3", description="Перенос расписания AVTOR -> FET")
    parser.add_argument("--env", default=".env", help="Путь к .env (по умолчанию .env в корне)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("etl", help="Загрузить выгрузку AVTOR в SQLite")
    build_parser = sub.add_parser("build", help="Собрать .fet")
    build_parser.add_argument("--scope", default=None, help="Только один период (имя каталога)")
    sub.add_parser("validate", help="Проверить .fet схемой и fet-cl")
    sub.add_parser("report", help="Сформировать md/xlsx отчёты")
    sub.add_parser("audit", help="Сформировать docs/00_audit.md")
    sub.add_parser("all", help="etl + build + validate + report + audit")

    args = parser.parse_args(argv)
    settings = get_settings(args.env)
    try:
        if args.command == "etl":
            return command_etl(settings)
        if args.command == "build":
            return command_build(settings, args.scope)
        if args.command == "validate":
            return command_validate(settings)
        if args.command == "report":
            return command_report(settings)
        if args.command == "audit":
            return command_audit(settings)
        if args.command == "all":
            return command_all(settings)
    except Exception as exc:  # noqa: BLE001 - CLI обязан показать причину
        print(f"Ошибка: {exc}", file=sys.stderr)
        if "--traceback" in sys.argv:
            traceback.print_exc()
        return 1
    parser.print_help()
    return 1
