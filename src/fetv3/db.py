"""Слой SQLite: схема, идемпотентная загрузка, выборки.

Загрузка периода сделана как «удалить и вставить заново» в одной транзакции:
повторный прогон ETL не должен плодить строки — это проверяется тестом.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS periods (
    name        TEXT PRIMARY KEY,
    source_dir  TEXT NOT NULL,
    date_min    TEXT NOT NULL,
    date_max    TEXT NOT NULL,
    rows_total  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS lessons (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    period           TEXT NOT NULL,
    source_file      TEXT NOT NULL,
    source_row       INTEGER NOT NULL,
    date_raw         TEXT NOT NULL,
    iso_date         TEXT NOT NULL,
    week             INTEGER NOT NULL,
    pair             INTEGER NOT NULL,
    groups_raw       TEXT NOT NULL,
    subgroup         TEXT NOT NULL,
    subject          TEXT NOT NULL,
    subject_type_raw TEXT NOT NULL,
    subject_type     TEXT NOT NULL,
    teacher          TEXT NOT NULL,
    teacher_key      TEXT NOT NULL,
    room             TEXT NOT NULL,
    is_remote        INTEGER NOT NULL,
    stud_in_lesson   INTEGER NOT NULL,
    lesson_id        TEXT NOT NULL,
    lesson_num       TEXT NOT NULL,
    dedup_key        TEXT NOT NULL,
    raw_json         TEXT NOT NULL,
    UNIQUE (period, source_row)
);

CREATE INDEX IF NOT EXISTS idx_lessons_period ON lessons(period);
CREATE INDEX IF NOT EXISTS idx_lessons_date ON lessons(iso_date, pair);
CREATE INDEX IF NOT EXISTS idx_lessons_teacher ON lessons(teacher_key);

CREATE TABLE IF NOT EXISTS duplicates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    period       TEXT NOT NULL,
    source_row   INTEGER NOT NULL,
    kept_period  TEXT NOT NULL,
    kept_row     INTEGER NOT NULL,
    dedup_key    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_duplicates_period ON duplicates(period);

CREATE TABLE IF NOT EXISTS ref_teacher_lessons (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    period      TEXT NOT NULL,
    teacher     TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_row  INTEGER NOT NULL,
    date_raw    TEXT NOT NULL,
    iso_date    TEXT,
    time_begin  TEXT NOT NULL,
    time_end    TEXT NOT NULL,
    room        TEXT NOT NULL,
    group_name  TEXT NOT NULL,
    subject     TEXT NOT NULL,
    kind        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ref_teacher ON ref_teacher_lessons(period, teacher);

CREATE TABLE IF NOT EXISTS ref_rooms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    period      TEXT NOT NULL,
    name        TEXT NOT NULL,
    source_file TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ref_rooms ON ref_rooms(period, name);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class LessonRow:
    period: str
    source_file: str
    source_row: int
    date_raw: str
    iso_date: str
    week: int
    pair: int
    groups_raw: str
    subgroup: str
    subject: str
    subject_type_raw: str
    subject_type: str
    teacher: str
    teacher_key: str
    room: str
    is_remote: bool
    stud_in_lesson: int
    lesson_id: str
    lesson_num: str
    dedup_key: str
    raw_json: str


@dataclass(frozen=True)
class PeriodInfo:
    name: str
    source_dir: str
    date_min: str
    date_max: str
    rows_total: int


@dataclass(frozen=True)
class DuplicateRow:
    period: str
    source_row: int
    kept_period: str
    kept_row: int
    dedup_key: str


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def replace_period(
    conn: sqlite3.Connection,
    info: PeriodInfo,
    lessons: list[LessonRow],
    duplicates: list[DuplicateRow],
) -> None:
    """Полная замена данных периода в одной транзакции."""
    with conn:
        conn.execute("DELETE FROM lessons WHERE period = ?", (info.name,))
        conn.execute("DELETE FROM duplicates WHERE period = ?", (info.name,))
        conn.execute("DELETE FROM periods WHERE name = ?", (info.name,))
        conn.execute(
            "INSERT INTO periods(name, source_dir, date_min, date_max, rows_total)"
            " VALUES (?, ?, ?, ?, ?)",
            (info.name, info.source_dir, info.date_min, info.date_max, info.rows_total),
        )
        conn.executemany(
            """INSERT INTO lessons(
                   period, source_file, source_row, date_raw, iso_date, week, pair,
                   groups_raw, subgroup, subject, subject_type_raw, subject_type,
                   teacher, teacher_key, room, is_remote, stud_in_lesson,
                   lesson_id, lesson_num, dedup_key, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    row.period,
                    row.source_file,
                    row.source_row,
                    row.date_raw,
                    row.iso_date,
                    row.week,
                    row.pair,
                    row.groups_raw,
                    row.subgroup,
                    row.subject,
                    row.subject_type_raw,
                    row.subject_type,
                    row.teacher,
                    row.teacher_key,
                    row.room,
                    int(row.is_remote),
                    row.stud_in_lesson,
                    row.lesson_id,
                    row.lesson_num,
                    row.dedup_key,
                    row.raw_json,
                )
                for row in lessons
            ],
        )
        conn.executemany(
            """INSERT INTO duplicates(period, source_row, kept_period, kept_row, dedup_key)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (d.period, d.source_row, d.kept_period, d.kept_row, d.dedup_key)
                for d in duplicates
            ],
        )


def load_lessons(conn: sqlite3.Connection, period: str | None = None) -> list[LessonRow]:
    query = "SELECT * FROM lessons"
    params: tuple[object, ...] = ()
    if period is not None:
        query += " WHERE period = ?"
        params = (period,)
    query += " ORDER BY iso_date, pair, period, source_row"
    rows = conn.execute(query, params).fetchall()
    return [
        LessonRow(
            period=r["period"],
            source_file=r["source_file"],
            source_row=int(r["source_row"]),
            date_raw=r["date_raw"],
            iso_date=r["iso_date"],
            week=int(r["week"]),
            pair=int(r["pair"]),
            groups_raw=r["groups_raw"],
            subgroup=r["subgroup"],
            subject=r["subject"],
            subject_type_raw=r["subject_type_raw"],
            subject_type=r["subject_type"],
            teacher=r["teacher"],
            teacher_key=r["teacher_key"],
            room=r["room"],
            is_remote=bool(r["is_remote"]),
            stud_in_lesson=int(r["stud_in_lesson"]),
            lesson_id=r["lesson_id"],
            lesson_num=r["lesson_num"],
            dedup_key=r["dedup_key"],
            raw_json=r["raw_json"],
        )
        for r in rows
    ]


def list_periods(conn: sqlite3.Connection) -> list[PeriodInfo]:
    rows = conn.execute(
        "SELECT name, source_dir, date_min, date_max, rows_total FROM periods ORDER BY date_min, name"
    ).fetchall()
    return [
        PeriodInfo(
            name=r["name"],
            source_dir=r["source_dir"],
            date_min=r["date_min"],
            date_max=r["date_max"],
            rows_total=int(r["rows_total"]),
        )
        for r in rows
    ]


def list_duplicates(conn: sqlite3.Connection) -> list[DuplicateRow]:
    rows = conn.execute(
        "SELECT period, source_row, kept_period, kept_row, dedup_key FROM duplicates"
    ).fetchall()
    return [
        DuplicateRow(
            period=r["period"],
            source_row=int(r["source_row"]),
            kept_period=r["kept_period"],
            kept_row=int(r["kept_row"]),
            dedup_key=r["dedup_key"],
        )
        for r in rows
    ]


def lesson_count(conn: sqlite3.Connection, period: str | None = None) -> int:
    if period is None:
        row = conn.execute("SELECT COUNT(*) AS n FROM lessons").fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) AS n FROM lessons WHERE period = ?", (period,)).fetchone()
    return int(row["n"])


def set_meta(conn: sqlite3.Connection, key: str, value: object) -> None:
    payload = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, payload),
    )


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])
