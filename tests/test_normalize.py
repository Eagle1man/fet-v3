from __future__ import annotations

import datetime as dt

import pytest

from fetv3.normalize import (
    day_label,
    normalize_subgroup,
    parse_date,
    parse_stud,
    room_name,
    split_groups,
    subject_type,
    teacher_key,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("11.01.2026", dt.date(2026, 1, 11)),
        ("7-03-2026", dt.date(2026, 3, 7)),
        ("26-01-2026", dt.date(2026, 1, 26)),
    ],
)
def test_parse_date(raw: str, expected: dt.date) -> None:
    assert parse_date(raw) == expected


def test_parse_date_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_date("не дата")


def test_teacher_key_keeps_digit_suffix() -> None:
    assert teacher_key("Агаркова  Н.Ю.") != teacher_key("Агаркова1  Н.Ю.")
    assert teacher_key("Агаркова  Н.Ю.") == teacher_key("агаркова н.ю.")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ЛЕК.", "лек"),
        ("Лек.", "лек"),
        ("ПРАКТИКА", "практика"),
        ("пр.", "пр"),
        ("Комп.лаб.", "лаб"),
        ("КОМПЬЮТЕРНАЯ ЛАБ.", "лаб"),
        ("Зачет с оценкой", "зачёт"),
        ("ЗАЩИТА ВКР", "вкр"),
        ("ЗАЩ.КУРС.", "курсовая"),
        ("", "другое"),
        ("Нечто новое", "другое"),
    ],
)
def test_subject_type(raw: str, expected: str) -> None:
    assert subject_type(raw) == expected


def test_split_groups_normalizes_spaces() -> None:
    assert split_groups(" ЭД301,  МД103 ,,") == ["ЭД301", "МД103"]


def test_subgroup_zero_is_empty() -> None:
    assert normalize_subgroup("0") == ""
    assert normalize_subgroup(0) == ""
    assert normalize_subgroup("2") == "2"


def test_room_and_remote() -> None:
    assert room_name("  .Дистанц. ") == ".Дистанц."
    assert room_name(" 506 ") == "506"


def test_stud_and_day_label() -> None:
    assert parse_stud("33") == 33
    assert parse_stud("") == 0
    assert parse_stud("12,0") == 12
    assert day_label(dt.date(2026, 1, 12)) == "12.01 Пн"
