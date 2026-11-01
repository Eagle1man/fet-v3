from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from fetv3 import db, journal
from fetv3.ai import tools
from fetv3.config import DEFAULT_HOURS, Settings
from fetv3.db import LessonRow


@pytest.fixture
def settings(tmp_path: Path, make_lesson: Callable[..., LessonRow]) -> Settings:
    config = Settings(
        avtor_out=tmp_path,
        db_path=tmp_path / "ai.db",
        out_dir=tmp_path / "out",
        docs_dir=tmp_path / "docs",
        fet_cl=None,
        school="Тест",
        hours=DEFAULT_HOURS,
        fet_time_limit=1,
        constraints=None,
        room_capacity=999,
    )
    conn = db.connect(config.db_path)
    db.init_schema(conn)
    journal.init_journal(conn)
    info = db.PeriodInfo(
        name="P1", source_dir="x", date_min="2026-01-12", date_max="2026-01-12", rows_total=1
    )
    db.replace_period(conn, info, [make_lesson(period="P1")], [])
    conn.close()
    return config


def test_query_lessons(settings: Settings) -> None:
    result = tools.query_lessons(settings, group="Г1")
    assert result["found"] == 1
    lesson = result["lessons"][0]
    assert lesson["предмет"] == "Математика"
    assert lesson["преподаватель"] == "Иванов И.И."


def test_query_lessons_empty(settings: Settings) -> None:
    result = tools.query_lessons(settings, group="нет такой")
    assert result["found"] == 0
    assert "Ничего не найдено" in result["note"]


def test_query_lessons_accepts_dotted_date(settings: Settings) -> None:
    result = tools.query_lessons(settings, group="Г1", date_from="12.01.2026", date_to="12.01.2026")
    assert result["found"] == 1


def test_query_lessons_rejects_bad_date(settings: Settings) -> None:
    result = tools.call(settings, "query_lessons", {"group": "Г1", "date_from": "12/01/26"})
    assert "error" in result
    assert "date_from" in result["error"]


def test_edit_is_dry_run_by_default(settings: Settings) -> None:
    result = tools.edit_lesson(
        settings, period="P1", source_row=1, changes={"pair": 4}
    )
    assert result["applied"] is False
    assert result["requires_confirmation"] is True
    assert result["diff"]["pair"] == {"было": 1, "станет": 4}
    assert tools.query_lessons(settings, group="Г1")["lessons"][0]["пара"] == 1


def test_edit_applied_after_confirmation(settings: Settings) -> None:
    result = tools.edit_lesson(
        settings, period="P1", source_row=1, changes={"pair": 4}, confirm=True
    )
    assert result["applied"] is True
    assert result["version"] == 1
    assert tools.query_lessons(settings, group="Г1")["lessons"][0]["пара"] == 4


def test_edit_rejects_unknown_field(settings: Settings) -> None:
    result = tools.call(
        settings,
        "edit_lesson",
        {
            "period": "P1",
            "source_row": 1,
            "changes": {"id": 3},
            "confirm": True,
        },
    )
    assert "error" in result


def test_edit_by_selector(settings: Settings) -> None:
    result = tools.edit_lesson(
        settings,
        selector={"group": "Г1", "date": "2026-01-12", "subject": "Математика"},
        changes={"pair": 3},
    )
    assert result["applied"] is False
    assert result["requires_confirmation"] is True
    assert result["target"]["строка"] == 1


def test_edit_by_selector_not_found(settings: Settings) -> None:
    result = tools.edit_lesson(
        settings,
        selector={"group": "Нет такой", "date": "2026-01-12"},
        changes={"pair": 3},
    )
    assert result["applied"] is False
    assert result["found"] == 0


def test_edit_by_selector_ambiguous(settings: Settings) -> None:
    conn = db.connect(settings.db_path)
    conn.execute(
        """INSERT INTO lessons(period, source_file, source_row, date_raw, iso_date, week, pair,
               groups_raw, subgroup, subject, subject_type_raw, subject_type, teacher,
               teacher_key, room, is_remote, stud_in_lesson, lesson_id, lesson_num,
               dedup_key, raw_json)
           SELECT period, source_file, 99, date_raw, iso_date, week, 2, groups_raw, subgroup,
               'Физика', subject_type_raw, subject_type, teacher, teacher_key, room,
               is_remote, stud_in_lesson, lesson_id, lesson_num, 'x2', raw_json
           FROM lessons WHERE source_row = 1"""
    )
    conn.commit()
    conn.close()
    result = tools.edit_lesson(
        settings, selector={"group": "Г1", "date": "2026-01-12"}, changes={"pair": 3}
    )
    assert result["ambiguous"] is True
    assert len(result["candidates"]) == 2


def test_unknown_tool_returns_error(settings: Settings) -> None:
    result = tools.call(settings, "delete_everything", {})
    assert "error" in result


def test_schemas_have_required_names() -> None:
    names = {item["function"]["name"] for item in tools.schemas()}
    assert names == {
        "query_lessons",
        "edit_lesson",
        "build_fet",
        "validate_fet",
        "export_report",
        "build_status",
    }
