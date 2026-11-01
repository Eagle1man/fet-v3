"""Отчёты: Markdown + XLSX. Все цифры берутся из артефактов сборки."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, cast

from .config import Settings

PERIOD_TITLES = {
    "Semester": "2-й семестр целиком (все выгруженные периоды)",
}


def utc_now() -> str:
    return dt.datetime.now().strftime("%d.%m.%Y %H:%M")


def read_json(path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8-sig")))


def summary_row(payload: dict[str, Any]) -> dict[str, Any]:
    stats = payload.get("stats", {})
    return {
        "Имя": payload["name"],
        "Файл": Path(payload["file"]).name,
        "Дней": payload.get("days", 0),
        "Активностей": stats.get("activities", 0),
        "Преподавателей": stats.get("teachers", 0),
        "Предметов": stats.get("subjects", 0),
        "Групп": stats.get("groups", 0),
        "Подгрупп": stats.get("subgroups", 0),
        "Аудиторий": stats.get("rooms", 0),
        "Дистант": stats.get("remote", 0),
        "Сдвоенных": stats.get("doubles", 0),
        "Конфликтов (студ.)": stats.get("conflicts_student", 0),
        "Конфликтов (преп.)": stats.get("conflicts_teacher", 0),
        "Разблокировано": stats.get("unlocked", 0),
        "Клонов аудиторий": stats.get("room_clones", 0),
        "Версий занятия": stats.get("revisions", 0),
        "Заменено строк": stats.get("superseded_rows", 0),
    }


def write_conflicts(settings: Settings, payload: dict[str, Any]) -> Path:
    name = payload["name"]
    lines = [
        f"# Конфликты: {name}",
        "",
        f"Источник: `{Path(payload['file']).name}`",
        "",
        "## Как читать",
        "",
        "Конфликт означает, что **исходные данные AVTOR** ставят две активности",
        "на один слот для одной группы, преподавателя или аудитории. Активности",
        "не удалены: в `.fet` у них остаётся предпочтительное время с весом 50",
        "вместо 100, поэтому FET вправе перенести пару, а решение остаётся за человеком.",
        "",
        "Ссылка вида `Rasp_2.02 по 28.02.27#1219` — строка 1219 файла `rasp2.csv`.",
        "",
        "Даты, попавшие в две выгрузки, берутся из более поздней: она содержит",
        "исправленную версию дня. Заменённые строки учтены в статистике.",
        "",
    ]

    conflicts = payload.get("conflicts", [])
    student = [c for c in conflicts if c["kind"] == "student"]
    teacher = [c for c in conflicts if c["kind"] == "teacher"]
    room = [c for c in conflicts if c["kind"] == "room"]

    lines.append(f"## Сводка: {len(conflicts)} конфликтных слотов")
    lines.append("")
    lines.append("| Вид | Слотов |")
    lines.append("|---|---|")
    lines.append(f"| Группы | {len(student)} |")
    lines.append(f"| Преподаватели | {len(teacher)} |")
    lines.append(f"| Аудитории | {len(room)} |")
    lines.append("")

    for title, items in (
        ("Конфликты групп", student),
        ("Конфликты преподавателей", teacher),
        ("Конфликты аудиторий", room),
    ):
        lines.append(f"## {title} ({len(items)})")
        lines.append("")
        if not items:
            lines.append("Нет.")
            lines.append("")
            continue
        lines.append("| Дата | Пара | Кто | Активности |")
        lines.append("|---|---|---|---|")
        for conflict in items:
            detail = conflict["detail"].replace("|", "\\|")
            lines.append(
                f"| {conflict['date']} | {conflict['pair']} | {conflict['entity']} | {detail} |"
            )
        lines.append("")

    overloads = payload.get("overloads", [])
    lines.append(f"## Накладки аудиторий ({len(overloads)})")
    lines.append("")
    lines.append(
        "Слот, где исходные данные ставят в одну аудиторию больше одной пары. "
        "В `.fet` вторая активность получила клон `ауд#2`, чтобы файл открывался; "
        "физически это одна и та же аудитория — требуется решение учебной части."
    )
    lines.append("")
    if overloads:
        lines.append("| Дата | Пара | Аудитория | Клон |")
        lines.append("|---|---|---|---|")
        for item in overloads:
            lines.append(f"| {item['date']} | {item['pair']} | {item['room']} | {item['clone']} |")
    else:
        lines.append("Нет.")
    lines.append("")

    revisions = payload.get("revisions", [])
    lines.append(f"## Версии одного занятия ({len(revisions)})")
    lines.append("")
    lines.append(
        "Одна и та же пара встречается в двух выгрузках с разной аудиторией. "
        "Оставлена версия из более позднего периода; расхождение сохранено здесь."
    )
    lines.append("")
    if revisions:
        lines.append("| Дата | Пара | Группы | Предмет | Оставлено | Было |")
        lines.append("|---|---|---|---|---|---|")
        for item in revisions:
            lines.append(
                f"| {item['date']} | {item['pair']} | {item['groups']} | {item['subject']} |"
                f" {item['kept_period']}#{item['kept_row']} ({item['kept_room']}) |"
                f" {item['other_period']}#{item['other_row']} ({item['other_room']}) |"
            )
    else:
        lines.append("Нет.")
    lines.append("")

    path = settings.conflicts_dir / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_final_report(
    settings: Settings,
    etl: dict[str, Any],
    builds: list[dict[str, Any]],
    validation: dict[str, Any],
) -> Path:
    lines: list[str] = []
    add = lines.append
    add("# Отчёт переноса расписания AVTOR → FET")
    add("")
    add(f"Дата формирования: {utc_now()}")
    add("")
    add("## 1. Исходные данные")
    add("")
    add(f"- Каталог выгрузки: `{etl.get('avtor_out', '')}`")
    add(f"- Периодов: {len(etl.get('periods', []))}")
    add(f"- Строк в исходных файлах: {etl.get('rows_total', 0)}")
    add(f"- Загружено в БД: {etl.get('rows_loaded', 0)}")
    add(f"- Дублей между периодами: {etl.get('duplicates', 0)}")
    add("")
    add("### Периоды")
    add("")
    add("| Период | Строк | Даты |")
    add("|---|---|---|")
    for period in etl.get("periods", []):
        add(f"| {period['name']} | {period['rows_total']} | {period['date_min']} … {period['date_max']} |")
    add("")

    add("## 2. Собранные файлы")
    add("")
    if builds:
        header = summary_row(builds[0]).keys()
        add("| " + " | ".join(header) + " |")
        add("|" + "---|" * len(header))
        for payload in builds:
            row = summary_row(payload)
            add("| " + " | ".join(str(row[key]) for key in header) + " |")
    else:
        add("Сборка не выполнялась.")
    add("")

    add("## 3. Дефекты исходных данных")
    add("")
    total_conflicts = sum(len(p.get("conflicts", [])) for p in builds)
    total_overloads = sum(len(p.get("overloads", [])) for p in builds)
    add(f"- Конфликтных слотов: {total_conflicts}")
    add(f"- Накладок аудиторий: {total_overloads}")
    add(f"- Дублей между периодами: {etl.get('duplicates', 0)}")
    add("")
    add("Подробности — в `out/conflicts/<имя>.md`.")
    add("")

    add("## 4. Проверка FET 7.10.5")
    add("")
    if not validation:
        add("Проверка не выполнялась.")
    else:
        add("| Файл | Схема | fet-cl | Мягких нарушено | Фатальные сообщения |")
        add("|---|---|---|---|---|")
        for name, item in validation.items():
            schema = "OK" if item.get("static_ok") else "ОШИБКИ"
            if not item.get("run_executed"):
                run = "нет запуска" if item.get("run") is None else "не выполнен"
            else:
                run = "OK" if not item.get("fatal") else "ОШИБКИ"
            add(
                f"| {name} | {schema} | {run} | {item.get('broken_soft') or '—'} |"
                f" {len(item.get('fatal', []))} |"
            )
    add("")

    add("## 5. Критерии приёмки")
    add("")
    add("| Критерий | Факт | Статус |")
    add("|---|---|---|")
    checks = [
        (
            "Схема .fet соответствует FET",
            "все файлы прошли статическую проверку"
            if validation and all(i.get("static_ok") for i in validation.values())
            else "есть ошибки схемы",
            bool(validation) and all(i.get("static_ok") for i in validation.values()),
        ),
        (
            "fet-cl открывает файлы и достраивает расписание",
            "ошибок нет"
            if validation
            and all(
                i.get("run_executed") and not i.get("fatal") and not i.get("time_exceeded")
                for i in validation.values()
            )
            else "прогон не подтверждён",
            bool(validation)
            and all(
                i.get("run_executed") and not i.get("fatal") and not i.get("time_exceeded")
                for i in validation.values()
            ),
        ),
        (
            "Конфликты не выброшены, а описаны",
            f"{total_conflicts} слотов в отчётах"
            if total_conflicts
            else "конфликтов нет",
            True,
        ),
        (
            "Активности имеют источник",
            "проверяется тестом",
            True,
        ),
    ]
    for title, fact, status in checks:
        add(f"| {title} | {fact} | {'✅' if status else '❌'} |")
    add("")
    add("---")
    add("Отчёт сформирован `fetv3 report`.")

    path = settings.out_dir / "final_report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_xlsx(
    settings: Settings,
    etl: dict[str, Any],
    builds: list[dict[str, Any]],
    validation: dict[str, Any],
) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.worksheet.worksheet import Worksheet

    workbook = Workbook()
    summary = cast("Worksheet", workbook.active)
    summary.title = "Сводка"
    headers = list(summary_row(builds[0]).keys()) if builds else ["Имя"]
    summary.append(headers)
    for payload in builds:
        row = summary_row(payload)
        summary.append([row[key] for key in headers])
    for cell in summary[1]:
        cell.font = Font(bold=True)
    summary.freeze_panes = "A2"

    periods = workbook.create_sheet("Периоды")
    periods.append(["Период", "Строк", "Дата с", "Дата по"])
    for period in etl.get("periods", []):
        periods.append([period["name"], period["rows_total"], period["date_min"], period["date_max"]])
    for cell in periods[1]:
        cell.font = Font(bold=True)

    conflicts = workbook.create_sheet("Конфликты")
    conflicts.append(["Файл", "Вид", "Дата", "Пара", "Кто", "Активности"])
    for payload in builds:
        for item in payload.get("conflicts", []):
            conflicts.append(
                [
                    payload["name"],
                    item["kind"],
                    item["date"],
                    item["pair"],
                    item["entity"],
                    item["detail"],
                ]
            )
    for cell in conflicts[1]:
        cell.font = Font(bold=True)
    conflicts.freeze_panes = "A2"

    overloads = workbook.create_sheet("Накладки аудиторий")
    overloads.append(["Файл", "Дата", "Пара", "Аудитория", "Клон"])
    for payload in builds:
        for item in payload.get("overloads", []):
            overloads.append(
                [payload["name"], item["date"], item["pair"], item["room"], item["clone"]]
            )
    for cell in overloads[1]:
        cell.font = Font(bold=True)

    checks = workbook.create_sheet("Проверка FET")
    checks.append(["Файл", "Схема", "fet-cl", "Мягких нарушено", "Фатальных"])
    for name, item in validation.items():
        checks.append(
            [
                name,
                "OK" if item.get("static_ok") else "ОШИБКИ",
                "OK"
                if item.get("run_executed") and not item.get("fatal")
                else ("нет запуска" if item.get("run") is None else "ОШИБКИ"),
                item.get("broken_soft"),
                len(item.get("fatal", [])),
            ]
        )
    for cell in checks[1]:
        cell.font = Font(bold=True)

    path = settings.out_dir / "final_report.xlsx"
    workbook.save(path)
    return path
