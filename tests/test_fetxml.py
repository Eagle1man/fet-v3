from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fetv3.activities import build
from fetv3.config import DEFAULT_HOURS
from fetv3.db import LessonRow
from fetv3.fetxml import render
from fetv3.validate import static_check


def _render(rows: list[LessonRow], tmp_path: Path) -> tuple[str, Path]:
    result = build(rows)
    xml = render(
        result,
        school="Тестовый колледж",
        title="Тест",
        comment="Комментарий",
        hours=DEFAULT_HOURS,
    )
    path = tmp_path / "test.fet"
    path.write_text(xml, encoding="utf-8")
    return xml, path


def test_schema_passes_static_check(
    make_lesson: Callable[..., LessonRow], tmp_path: Path
) -> None:
    xml, path = _render([make_lesson()], tmp_path)
    assert '<fet version="7.10.5">' in xml
    assert "<Days_List>" in xml
    assert "<Hours_List>" in xml
    check = static_check(path)
    assert check.ok, check.errors
    assert check.counts["Days_List"] == 1
    assert check.counts["Hours_List"] == len(DEFAULT_HOURS)
    assert check.counts["Activities_List"] == 1


def test_day_is_calendar_date(make_lesson: Callable[..., LessonRow], tmp_path: Path) -> None:
    _, path = _render([make_lesson(date="2026-01-12")], tmp_path)
    check = static_check(path)
    assert check.ok
    assert "12.01 Пн" in path.read_text(encoding="utf-8")


def test_subgroup_is_nested_in_group(
    make_lesson: Callable[..., LessonRow], tmp_path: Path
) -> None:
    rows = [
        make_lesson(subgroup="1", subject="Английский"),
        make_lesson(subgroup="2", subject="Немецкий", teacher="Петров П.П."),
    ]
    xml, path = _render(rows, tmp_path)
    assert "<Subgroup>" in xml
    assert "Г1 п1" in xml
    assert "Г1 п2" in xml
    assert static_check(path).ok


def test_unlocked_activity_has_weight_50(
    make_lesson: Callable[..., LessonRow], tmp_path: Path
) -> None:
    rows = [
        make_lesson(subject="Математика", teacher="Иванов И.И."),
        make_lesson(subject="Физика", teacher="Петров П.П."),
    ]
    xml, path = _render(rows, tmp_path)
    assert "<Weight_Percentage>50</Weight_Percentage>" in xml
    assert "ConstraintActivityPreferredStartingTimes" in xml
    assert static_check(path).ok


def test_xml_escaping(make_lesson: Callable[..., LessonRow], tmp_path: Path) -> None:
    xml, path = _render([make_lesson(subject="A&B <C>")], tmp_path)
    assert "A&amp;B &lt;C&gt;" in xml
    assert static_check(path).ok


def test_manual_unavailable_constraints(
    make_lesson: Callable[..., LessonRow], tmp_path: Path
) -> None:
    from fetv3.fetxml import ManualUnavailable

    result = build([make_lesson()])
    xml = render(
        result,
        school="Тест",
        title="Тест",
        comment="",
        hours=DEFAULT_HOURS,
        unavailable=[
            ManualUnavailable(kind="teacher", name="Иванов И.И.", day="12.01 Пн", hour=3),
            ManualUnavailable(kind="room", name="201", day="12.01 Пн", hour=4),
        ],
    )
    path = tmp_path / "manual.fet"
    path.write_text(xml, encoding="utf-8")
    assert "<ConstraintTeacherNotAvailableTimes>" in xml
    assert "<ConstraintRoomNotAvailableTimes>" in xml
    assert "<Not_Available_Time>" in xml
    check = static_check(path)
    assert check.ok, check.errors


def test_load_constraints_from_json(tmp_path: Path) -> None:
    import json

    from fetv3.fetxml import load_constraints

    path = tmp_path / "constraints.json"
    path.write_text(
        json.dumps(
            {
                "unavailable": [
                    {"kind": "teacher", "name": "Иванов И.И.", "day": "12.01 Пн", "hour": 3}
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    items = load_constraints(path)
    assert len(items) == 1
    assert items[0].kind == "teacher"
    assert items[0].hour == 3


def test_missing_section_is_caught(minimal_fet: str, tmp_path: Path) -> None:
    broken = minimal_fet.replace("<Students_List>", "<Students_ListX>").replace(
        "</Students_List>", "</Students_ListX>"
    )
    path = tmp_path / "broken.fet"
    path.write_text(broken, encoding="utf-8")
    check = static_check(path)
    assert not check.ok
    assert any("Students_List" in error for error in check.errors)
