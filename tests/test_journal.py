from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from fetv3 import db, journal
from fetv3.db import LessonRow


@pytest.fixture
def conn(tmp_path: Path, make_lesson: Callable[..., LessonRow]):
    connection = db.connect(tmp_path / "journal.db")
    db.init_schema(connection)
    journal.init_journal(connection)
    info = db.PeriodInfo(
        name="P1",
        source_dir="x",
        date_min="2026-01-12",
        date_max="2026-01-12",
        rows_total=1,
    )
    db.replace_period(connection, info, [make_lesson(period="P1")], [])
    yield connection
    connection.close()


def test_update_writes_journal(conn) -> None:
    result = journal.apply_update(
        conn, period="P1", source_row=1, changes={"pair": 3}, actor="тест"
    )
    assert result["version"] == 1
    row = conn.execute("SELECT pair FROM lessons WHERE period='P1' AND source_row=1").fetchone()
    assert row["pair"] == 3
    assert len(journal.history(conn)) == 1


def test_undo_restores_previous_values(conn) -> None:
    journal.apply_update(conn, period="P1", source_row=1, changes={"pair": 3}, actor="тест")
    undone = journal.undo_last(conn, actor="тест")
    assert undone["source_row"] == 1
    row = conn.execute("SELECT pair FROM lessons WHERE period='P1' AND source_row=1").fetchone()
    assert row["pair"] == 1
    assert len(journal.history(conn)) == 2


def test_undo_without_changes_raises(conn) -> None:
    with pytest.raises(journal.JournalError):
        journal.undo_last(conn, actor="тест")


def test_unknown_field_rejected(conn) -> None:
    with pytest.raises(journal.JournalError):
        journal.apply_update(conn, period="P1", source_row=1, changes={"id": 5}, actor="тест")


def test_invalid_pair_rejected(conn) -> None:
    with pytest.raises(journal.JournalError):
        journal.apply_update(conn, period="P1", source_row=1, changes={"pair": 99}, actor="тест")


def test_same_values_rejected(conn) -> None:
    with pytest.raises(journal.JournalError):
        journal.apply_update(
            conn, period="P1", source_row=1, changes={"pair": 1}, actor="тест"
        )


def test_move_room_makes_remote(conn) -> None:
    journal.apply_update(
        conn, period="P1", source_row=1, changes={"room": ".Дистанц."}, actor="тест"
    )
    row = conn.execute(
        "SELECT room, is_remote FROM lessons WHERE period='P1' AND source_row=1"
    ).fetchone()
    assert row["room"] == ".Дистанц."
    assert row["is_remote"] == 1


def test_search_by_room_and_teacher(conn, make_lesson: Callable[..., LessonRow]) -> None:
    journal.apply_update(
        conn, period="P1", source_row=1, changes={"room": "305"}, actor="тест"
    )
    assert len(journal.search(conn, room="305")) == 1
    assert len(journal.search(conn, teacher="Иванов И.И.")) == 1
    assert journal.search(conn, group="нет такой") == []
