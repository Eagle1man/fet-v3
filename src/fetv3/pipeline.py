"""Конвейер без побочного вывода: используется CLI, UI и ИИ-инструментами.

Вся печать остаётся в `cli.py`. Так один и тот же код вызывается из
командной строки, панели и ассистента, и не дублируется.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Any

from . import db
from .activities import BuildResult, build
from .audit import write_audit
from .config import Settings
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


def run_etl_step(settings: Settings) -> dict[str, Any]:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_etl(settings)
    payload = etl_payload(settings, result)
    settings.out_dir.mkdir(parents=True, exist_ok=True)
    (settings.out_dir / "etl_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def build_fet(settings: Settings, scope: str | None = None) -> list[dict[str, Any]]:
    conn = db.connect(settings.db_path)
    try:
        if db.lesson_count(conn) == 0:
            raise RuntimeError("База пуста. Сначала выполните etl.")
        periods = db.list_periods(conn)
        if not periods:
            raise RuntimeError("Нет загруженных периодов.")

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
                continue
            result = build(rows)
            xml = render(
                result,
                school=settings.school,
                title=f"Расписание: {name}",
                comment=(
                    "Точная копия выгрузки AVTOR. Год дат — как в источнике. "
                    "Конфликтные активности не удалены (вес 50 вместо 100)."
                ),
                hours=settings.hours,
                unavailable=unavailable,
                room_capacity=settings.room_capacity,
            )
            path = settings.fet_dir / f"{safe_name(name)}.fet"
            write(path, xml)
            payload = build_payload(name, path, result)
            write_conflicts(settings, payload)
            payloads.append(payload)

        settings.out_dir.mkdir(parents=True, exist_ok=True)
        (settings.out_dir / "build_summary.json").write_text(
            json.dumps({"builds": payloads}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return payloads
    except FetXmlError as exc:
        raise RuntimeError(f"Ошибка сборки .fet: {exc}") from exc
    finally:
        conn.close()


def validate_fet(settings: Settings) -> dict[str, Any]:
    builds_path = settings.out_dir / "build_summary.json"
    if not builds_path.exists():
        raise RuntimeError("Нет сборки. Сначала выполните build.")
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
    (settings.validation_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def generate_reports(settings: Settings) -> tuple[Path, Path]:
    etl_path = settings.out_dir / "etl_summary.json"
    builds_path = settings.out_dir / "build_summary.json"
    validation_path = settings.validation_dir / "summary.json"
    if not builds_path.exists():
        raise RuntimeError("Нет сборки. Сначала выполните build.")
    etl = read_json(etl_path) if etl_path.exists() else {}
    builds = read_json(builds_path)["builds"]
    validation = read_json(validation_path) if validation_path.exists() else {}
    md = write_final_report(settings, etl, builds, validation)
    xlsx = write_xlsx(settings, etl, builds, validation)
    return md, xlsx


def generate_audit(settings: Settings) -> Path:
    conn = db.connect(settings.db_path)
    try:
        if db.lesson_count(conn) == 0:
            raise RuntimeError("База пуста. Сначала выполните etl.")
        return write_audit(settings, conn)
    finally:
        conn.close()


def build_stats(settings: Settings) -> dict[str, Any]:
    """Краткая сводка последней сборки — для ассистента и панели."""
    path = settings.out_dir / "build_summary.json"
    if not path.exists():
        return {"builds": []}
    data = read_json(path)
    builds = data.get("builds", [])
    return {
        "files": len(builds),
        "activities": sum(int(b["stats"].get("activities", 0)) for b in builds),
        "conflicts": sum(len(b.get("conflicts", [])) for b in builds),
    }
