from __future__ import annotations

from collections.abc import Callable

from fetv3.activities import build
from fetv3.db import LessonRow


def test_combined_lecture_becomes_one_activity(make_lesson: Callable[..., LessonRow]) -> None:
    rows = [
        make_lesson(groups="Г1", teacher="Иванов И.И."),
        make_lesson(groups="Г2", teacher="Иванов И.И."),
    ]
    result = build(rows)
    assert len(result.activities) == 1
    assert set(result.activities[0].students) == {"Г1", "Г2"}
    assert len(result.activities[0].refs) == 2


def test_co_teaching_merges_teachers(make_lesson: Callable[..., LessonRow]) -> None:
    rows = [
        make_lesson(groups="Г1", teacher="Иванов И.И."),
        make_lesson(groups="Г1", teacher="Петров П.П."),
    ]
    result = build(rows)
    assert len(result.activities) == 1
    assert set(result.activities[0].teachers) == {"Иванов И.И.", "Петров П.П."}


def test_subgroups_are_parallel_branches(make_lesson: Callable[..., LessonRow]) -> None:
    rows = [
        make_lesson(groups="Г1", subgroup="1", subject="Английский", teacher="Иванов И.И."),
        make_lesson(groups="Г1", subgroup="2", subject="Немецкий", teacher="Петров П.П."),
    ]
    result = build(rows)
    assert result.subgroups == {"Г1": ("1", "2")}
    assert {activity.students[0] for activity in result.activities} == {"Г1 п1", "Г1 п2"}
    for activity in result.activities:
        assert activity.locked is True


def test_double_pair_gets_duration_two(make_lesson: Callable[..., LessonRow]) -> None:
    rows = [
        make_lesson(pair=2),
        make_lesson(pair=3),
    ]
    result = build(rows)
    assert len(result.activities) == 1
    assert result.activities[0].duration == 2
    assert result.stats["doubles"] == 1


def test_double_pair_not_chained_over_foreign_lesson(
    make_lesson: Callable[..., LessonRow],
) -> None:
    rows = [
        make_lesson(pair=2, subject="Математика"),
        make_lesson(pair=3, subject="Математика"),
        make_lesson(pair=3, subject="Физика", teacher="Петров П.П."),
    ]
    result = build(rows)
    assert not any(activity.duration > 1 for activity in result.activities)


def test_remote_lesson_has_no_room(make_lesson: Callable[..., LessonRow]) -> None:
    rows = [make_lesson(room=".Дистанц.", remote=True)]
    result = build(rows)
    assert result.activities[0].is_remote is True
    assert result.activities[0].room_final == ""
    assert result.stats["rooms"] == 0


def test_student_conflict_is_reported_not_dropped(
    make_lesson: Callable[..., LessonRow],
) -> None:
    rows = [
        make_lesson(subject="Математика", teacher="Иванов И.И."),
        make_lesson(subject="Физика", teacher="Петров П.П."),
    ]
    result = build(rows)
    assert len(result.activities) == 2
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.kind == "student"
    assert conflict.entity == "Г1"
    assert all(activity.locked is False for activity in result.activities)


def test_room_overload_uses_clone(make_lesson: Callable[..., LessonRow]) -> None:
    rows = [
        make_lesson(groups="Г1", room="201", subject="Математика"),
        make_lesson(groups="Г2", room="201", subject="Физика", teacher="Петров П.П."),
    ]
    result = build(rows)
    rooms = {activity.room_final for activity in result.activities}
    assert rooms == {"201", "201#2"}
    assert len(result.overloads) == 1
    assert result.overloads[0].clone == "201#2"


def test_duplicates_from_overlapping_periods_are_collapsed(
    make_lesson: Callable[..., LessonRow],
) -> None:
    first = make_lesson(period="Rasp_2.02")
    second = make_lesson(period="Rasp_2.03")
    assert first.dedup_key == second.dedup_key
    result = build([first, second])
    assert len(result.activities) == 1


def test_different_room_in_later_export_is_a_revision(
    make_lesson: Callable[..., LessonRow],
) -> None:
    early = make_lesson(period="Rasp_2.02", room="604б")
    late = make_lesson(period="Rasp_2.03", room="315")
    assert early.dedup_key != late.dedup_key
    result = build([early, late])
    assert len(result.activities) == 1
    assert result.activities[0].room == "315"
    assert len(result.revisions) == 1
    assert result.revisions[0].kept_room == "315"
    assert result.revisions[0].other_room == "604б"


def test_overlapping_date_uses_later_export(
    make_lesson: Callable[..., LessonRow],
) -> None:
    early = make_lesson(period="Rasp_2.02", subject="Угол. право", teacher="Сазанова Е.А.")
    late = make_lesson(period="Rasp_2.03", subject="Гражд. право", teacher="Дмитренко А.В.")
    result = build([early, late])
    assert len(result.activities) == 1
    assert result.activities[0].subject == "Гражд. право"
    assert result.conflicts == []
    assert result.stats["superseded_rows"] == 1


def test_every_activity_has_source_ref(make_lesson: Callable[..., LessonRow]) -> None:
    rows = [make_lesson(), make_lesson(pair=2, subject="Физика", teacher="Петров П.П.")]
    result = build(rows)
    assert all(activity.refs for activity in result.activities)
    assert all(ref.row > 0 for activity in result.activities for ref in activity.refs)
