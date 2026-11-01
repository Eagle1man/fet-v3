"""Инструменты ассистента: только проверенные операции над БД и сборкой.

Модель не пишет `.fet` и не придумывает расписание: она выбирает инструмент.
Любая правка сначала возвращается как предложение (dry-run), и применяется
только при `confirm=true` — решение остаётся за человеком.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections.abc import Callable
from typing import Any

from .. import db, journal, pipeline
from ..config import Settings

MAX_ROWS = 100


class ToolError(RuntimeError):
    """Ошибка вызова инструмента."""


def _connect(settings: Settings) -> sqlite3.Connection:
    conn = db.connect(settings.db_path)
    db.init_schema(conn)
    journal.init_journal(conn)
    return conn


def _norm_date(value: str | None, field: str) -> str | None:
    """Принимает ГГГГ-ММ-ДД и ДД.ММ.ГГГГ; иначе — понятная ошибка."""
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise ToolError(f"{field}: ожидается дата ГГГГ-ММ-ДД или ДД.ММ.ГГГГ, получено {text!r}")


def query_lessons(
    settings: Settings,
    *,
    group: str | None = None,
    teacher: str | None = None,
    room: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), MAX_ROWS))
    date_from = _norm_date(date_from, "date_from")
    date_to = _norm_date(date_to, "date_to")
    conn = _connect(settings)
    try:
        rows = journal.search(
            conn,
            group=group,
            teacher=teacher,
            room=room,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )
    finally:
        conn.close()
    if not rows:
        return {"found": 0, "lessons": [], "note": "Ничего не найдено по этим условиям."}
    lessons = [
        {
            "период": row["period"],
            "строка": row["source_row"],
            "дата": row["iso_date"],
            "пара": row["pair"],
            "группы": row["groups_raw"],
            "подгруппа": row["subgroup"],
            "предмет": row["subject"],
            "вид": row["subject_type"],
            "преподаватель": row["teacher"],
            "аудитория": row["room"],
        }
        for row in rows
    ]
    return {"found": len(lessons), "lessons": lessons}


def _resolve_target(
    conn: sqlite3.Connection,
    *,
    period: str | None,
    source_row: int | None,
    selector: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Находит занятие по строке источника или по набору условий."""
    if period is not None and source_row is not None:
        row = conn.execute(
            "SELECT * FROM lessons WHERE period = ? AND source_row = ?",
            (period, int(source_row)),
        ).fetchone()
        return (dict(row) if row else None), []

    if not selector:
        raise ToolError("Нужен source_row (с period) или selector")

    query = "SELECT * FROM lessons WHERE 1 = 1"
    params: list[Any] = []
    if selector.get("group"):
        query += " AND groups_raw LIKE ?"
        params.append(f"%{str(selector['group']).strip()}%")
    if selector.get("date"):
        query += " AND iso_date = ?"
        params.append(_norm_date(str(selector["date"]), "selector.date"))
    if selector.get("subject"):
        query += " AND subject LIKE ?"
        params.append(f"%{str(selector['subject']).strip()}%")
    if selector.get("pair") is not None:
        query += " AND pair = ?"
        params.append(int(selector["pair"]))
    if selector.get("teacher"):
        query += " AND teacher_key = ?"
        params.append(str(selector["teacher"]).strip().casefold())
    if selector.get("room"):
        query += " AND room = ?"
        params.append(str(selector["room"]).strip())
    rows = [dict(row) for row in conn.execute(query, params).fetchall()]
    if len(rows) == 1:
        return rows[0], []
    return None, rows


def _brief(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "период": row["period"],
        "строка": row["source_row"],
        "дата": row["iso_date"],
        "пара": row["pair"],
        "группы": row["groups_raw"],
        "предмет": row["subject"],
        "преподаватель": row["teacher"],
        "аудитория": row["room"],
    }


def edit_lesson(
    settings: Settings,
    *,
    changes: dict[str, Any],
    period: str | None = None,
    source_row: int | None = None,
    selector: dict[str, Any] | None = None,
    confirm: bool = False,
    actor: str = "ассистент",
) -> dict[str, Any]:
    conn = _connect(settings)
    try:
        current, candidates = _resolve_target(
            conn, period=period, source_row=source_row, selector=selector
        )
        if current is None:
            if candidates:
                return {
                    "applied": False,
                    "ambiguous": True,
                    "candidates": [_brief(row) for row in candidates[:20]],
                    "note": "Найдено несколько занятий. Уточните группу, дату, пару или предмет.",
                }
            return {
                "applied": False,
                "found": 0,
                "note": "Занятие не найдено по этим условиям. Проверьте группу, дату и предмет.",
            }

        before = {key: current[key] for key in journal.EDITABLE_FIELDS}
        unknown = set(changes) - set(journal.EDITABLE_FIELDS)
        if unknown:
            raise ToolError(f"Нельзя менять поля: {sorted(unknown)}")
        proposed = {**before, **{k: v for k, v in changes.items()}}
        diff = {k: {"было": before[k], "станет": proposed[k]} for k in changes}
        if proposed == before:
            return {"applied": False, "note": "Значения не изменились.", "diff": diff}
        if not confirm:
            return {
                "applied": False,
                "requires_confirmation": True,
                "target": _brief(current),
                "before": before,
                "diff": diff,
                "note": "Изменение не применено. Подтвердите у человека и вызовите с confirm=true.",
            }
        result = journal.apply_update(
            conn,
            period=current["period"],
            source_row=int(current["source_row"]),
            changes=changes,
            actor=actor,
        )
    finally:
        conn.close()
    return {
        "applied": True,
        "version": result["version"],
        "target": _brief(current),
        "diff": diff,
        "note": "Правка сохранена. Для обновления .fet вызовите build_fet.",
    }


def build_fet(settings: Settings, *, scope: str | None = None) -> dict[str, Any]:
    payloads = pipeline.build_fet(settings, scope)
    return {"built": [p["name"] for p in payloads], "stats": pipeline.build_stats(settings)}


def validate_fet(settings: Settings) -> dict[str, Any]:
    summary = pipeline.validate_fet(settings)
    return {
        "files": {
            name: {
                "схема": item["static_ok"],
                "fet_cl": item["run_executed"],
                "фатальных": len(item["fatal"]),
                "нарушено_мягких": item["broken_soft"],
            }
            for name, item in summary.items()
        }
    }


def export_report(settings: Settings) -> dict[str, Any]:
    md, xlsx = pipeline.generate_reports(settings)
    return {"markdown": str(md), "xlsx": str(xlsx)}


def build_status(settings: Settings) -> dict[str, Any]:
    return pipeline.build_stats(settings)


TOOLS: dict[str, tuple[Callable[..., dict[str, Any]], dict[str, Any]]] = {
    "query_lessons": (
        query_lessons,
        {
            "type": "object",
            "properties": {
                "group": {"type": "string", "description": "Название группы, например К1А1(9)"},
                "teacher": {"type": "string", "description": "ФИО преподавателя"},
                "room": {"type": "string", "description": "Аудитория"},
                "date_from": {"type": "string", "description": "Дата с, ГГГГ-ММ-ДД"},
                "date_to": {"type": "string", "description": "Дата по, ГГГГ-ММ-ДД"},
                "limit": {"type": "integer", "description": "Не больше 100 строк"},
            },
        },
    ),
    "edit_lesson": (
        edit_lesson,
        {
            "type": "object",
            "properties": {
                "changes": {
                    "type": "object",
                    "description": (
                        "Поля: iso_date (ГГГГ-ММ-ДД), pair (1-8), room, teacher, subject"
                    ),
                },
                "selector": {
                    "type": "object",
                    "description": (
                        "Как найти занятие: group, date, subject, pair, teacher, room. "
                        "Предпочтительнее, чем source_row."
                    ),
                    "properties": {
                        "group": {"type": "string"},
                        "date": {"type": "string"},
                        "subject": {"type": "string"},
                        "pair": {"type": "integer"},
                        "teacher": {"type": "string"},
                        "room": {"type": "string"},
                    },
                },
                "period": {"type": "string", "description": "Период (если известен source_row)"},
                "source_row": {"type": "integer", "description": "Номер строки rasp2.csv"},
                "confirm": {
                    "type": "boolean",
                    "description": "false — только предложение, true — применить",
                },
            },
            "required": ["changes"],
        },
    ),
    "build_fet": (
        build_fet,
        {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "Один период; пусто — все и семестр"}
            },
        },
    ),
    "validate_fet": (validate_fet, {"type": "object", "properties": {}}),
    "export_report": (export_report, {"type": "object", "properties": {}}),
    "build_status": (build_status, {"type": "object", "properties": {}}),
}


def schemas() -> list[dict[str, Any]]:
    """Схемы в формате Ollama tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": (func.__doc__ or name).strip().splitlines()[0],
                "parameters": parameters,
            },
        }
        for name, (func, parameters) in TOOLS.items()
    ]


def call(settings: Settings, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    entry = TOOLS.get(name)
    if entry is None:
        return {"error": f"Неизвестный инструмент: {name}"}
    func, _ = entry
    try:
        return func(settings, **arguments)
    except (ToolError, journal.JournalError, RuntimeError, TypeError, ValueError) as exc:
        return {"error": str(exc)}


def describe_result(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)
