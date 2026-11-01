from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fetv3 import db
from fetv3.db import LessonRow


def test_replace_period_is_idempotent(
    tmp_path: Path, make_lesson: Callable[..., LessonRow]
) -> None:
    conn = db.connect(tmp_path / "test.db")
    db.init_schema(conn)
    info = db.PeriodInfo(
        name="P1",
        source_dir="x",
        date_min="2026-01-12",
        date_max="2026-01-12",
        rows_total=1,
    )
    lesson = make_lesson(period="P1")
    db.replace_period(conn, info, [lesson], [])
    db.replace_period(conn, info, [lesson], [])
    assert db.lesson_count(conn) == 1
    assert len(db.list_periods(conn)) == 1

    loaded = db.load_lessons(conn, "P1")
    assert len(loaded) == 1
    assert loaded[0].subgroup == ""
    assert loaded[0].teacher_key == "иванов и.и."
    conn.close()
