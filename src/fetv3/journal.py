"""Журнал изменений: правка занятия, версия, откат.

В отличие от ETL, здесь одна транзакция на одно действие: запись в
`lessons` и запись в `change_log` фиксируются вместе. Откат возвращает
предыдущие значения по той же строке источника (период + номер строки),
поэтому перезагрузка ETL не ломает историю.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Any

EDITABLE_FIELDS = ("iso_date", "pair", "room", "teacher", "subject")


class JournalError(RuntimeError):
    """Некорректное изменение."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS change_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    period      TEXT NOT NULL,
    source_row  INTEGER NOT NULL,
    old_json    TEXT,
    new_json    TEXT,
    undone      INTEGER NOT NULL DEFAULT 0,
    undo_of     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_change_log_source ON change_log(period, source_row);
"""


def init_journal(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _row_dict(conn: sqlite3.Connection, period: str, source_row: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM lessons WHERE period = ? AND source_row = ?",
        (period, source_row),
    ).fetchone()
    if row is None:
        raise JournalError(f"Строка не найдена: {period}#{source_row}")
    return dict(row)


def _validate(fields: dict[str, Any]) -> dict[str, Any]:
    unknown = set(fields) - set(EDITABLE_FIELDS)
    if unknown:
        raise JournalError(f"Нельзя менять поля: {sorted(unknown)}")

    result: dict[str, Any] = {}
    if "iso_date" in fields:
        try:
            date = dt.date.fromisoformat(str(fields["iso_date"]))
        except ValueError as exc:
            raise JournalError(f"Некорректная дата: {fields['iso_date']}") from exc
        result["iso_date"] = date.isoformat()
        result["week"] = date.isocalendar().week
    if "pair" in fields:
        try:
            pair = int(fields["pair"])
        except (TypeError, ValueError) as exc:
            raise JournalError(f"Некорректная пара: {fields['pair']}") from exc
        if not 1 <= pair <= 8:
            raise JournalError(f"Пара вне диапазона 1..8: {pair}")
        result["pair"] = pair
    if "teacher" in fields:
        teacher = str(fields["teacher"]).strip()
        if not teacher:
            raise JournalError("Преподаватель не может быть пустым")
        result["teacher"] = teacher
        result["teacher_key"] = teacher.casefold()
    if "subject" in fields:
        subject = str(fields["subject"]).strip()
        if not subject:
            raise JournalError("Предмет не может быть пустым")
        result["subject"] = subject
    if "room" in fields:
        room = str(fields["room"]).strip()
        result["room"] = room
        result["is_remote"] = int(room.startswith("."))
    return result


def apply_update(
    conn: sqlite3.Connection,
    *,
    period: str,
    source_row: int,
    changes: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Меняет занятие и пишет запись в журнал одной транзакцией."""
    if not changes:
        raise JournalError("Нет изменений")
    old = _row_dict(conn, period, source_row)
    new_values = _validate(changes)

    tracked = {key: old[key] for key in (*EDITABLE_FIELDS, "teacher_key", "week", "is_remote")}
    after = {**tracked, **new_values}
    if after == tracked:
        raise JournalError("Значения не изменились")

    assignments = ", ".join(f"{key} = ?" for key in new_values)
    params = [*new_values.values(), period, source_row]
    with conn:
        conn.execute(
            f"UPDATE lessons SET {assignments} WHERE period = ? AND source_row = ?",
            params,
        )
        conn.execute(
            """INSERT INTO change_log(ts, actor, action, period, source_row, old_json, new_json)
               VALUES (?, ?, 'update', ?, ?, ?, ?)""",
            (
                _now(),
                actor,
                period,
                source_row,
                json.dumps(tracked, ensure_ascii=False),
                json.dumps(after, ensure_ascii=False),
            ),
        )
        version = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    return {"version": version, "old": tracked, "new": after}


def history(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM change_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]


def undo_last(conn: sqlite3.Connection, actor: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM change_log WHERE action = 'update' AND undone = 0 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise JournalError("Нечего отменять")
    old = json.loads(row["old_json"])
    new = json.loads(row["new_json"])
    period = str(row["period"])
    source_row = int(row["source_row"])
    _row_dict(conn, period, source_row)

    assignments = ", ".join(f"{key} = ?" for key in EDITABLE_FIELDS)
    params: list[Any] = [old[key] for key in EDITABLE_FIELDS]
    params.extend([old["week"], old["teacher_key"], old["is_remote"], period, source_row])
    with conn:
        conn.execute(
            f"UPDATE lessons SET {assignments}, week = ?, teacher_key = ?, is_remote = ?"
            " WHERE period = ? AND source_row = ?",
            params,
        )
        conn.execute("UPDATE change_log SET undone = 1 WHERE id = ?", (row["id"],))
        conn.execute(
            """INSERT INTO change_log(ts, actor, action, period, source_row, old_json, new_json, undo_of)
               VALUES (?, ?, 'undo', ?, ?, ?, ?, ?)""",
            (
                _now(),
                actor,
                period,
                source_row,
                json.dumps(new, ensure_ascii=False),
                json.dumps(old, ensure_ascii=False),
                row["id"],
            ),
        )
        version = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    return {"version": version, "period": period, "source_row": source_row, "restored": old}


def replay(conn: sqlite3.Connection) -> int:
    """Повторно применяет ручные правки после перезагрузки ETL.

    ETL заменяет строки периода, поэтому правки, сделанные в панели, иначе
    терялись бы при `run.py all`. Активные (не отменённые) изменения
    применяются к новым строкам по ключу «период + строка источника».
    """
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='change_log'"
    ).fetchone() is None:
        return 0
    rows = conn.execute(
        "SELECT * FROM change_log WHERE action = 'update' AND undone = 0 ORDER BY id"
    ).fetchall()
    applied = 0
    with conn:
        for row in rows:
            values = json.loads(row["new_json"])
            assignments = ", ".join(
                f"{key} = ?" for key in (*EDITABLE_FIELDS, "week", "teacher_key", "is_remote")
            )
            params = [
                values.get(key) for key in (*EDITABLE_FIELDS, "week", "teacher_key", "is_remote")
            ]
            params.extend([row["period"], row["source_row"]])
            cursor = conn.execute(
                f"UPDATE lessons SET {assignments} WHERE period = ? AND source_row = ?",
                params,
            )
            applied += cursor.rowcount
    return applied


def search(
    conn: sqlite3.Connection,
    *,
    group: str | None = None,
    teacher: str | None = None,
    room: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM lessons WHERE 1 = 1"
    params: list[Any] = []
    if group:
        query += " AND groups_raw LIKE ?"
        params.append(f"%{group.strip()}%")
    if teacher:
        query += " AND teacher_key = ?"
        params.append(teacher.strip().casefold())
    if room:
        query += " AND room = ?"
        params.append(room.strip())
    if date_from:
        query += " AND iso_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND iso_date <= ?"
        params.append(date_to)
    query += " ORDER BY iso_date, pair, groups_raw LIMIT ?"
    params.append(limit)
    return [dict(row) for row in conn.execute(query, params).fetchall()]
