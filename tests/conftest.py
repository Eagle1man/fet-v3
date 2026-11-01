from __future__ import annotations

from collections.abc import Callable

import pytest

from fetv3.db import LessonRow


def lesson_factory() -> Callable[..., LessonRow]:
    counter = {"n": 0}

    def make(
        *,
        date: str = "2026-01-12",
        pair: int = 1,
        groups: str = "Г1",
        subgroup: str = "",
        subject: str = "Математика",
        subject_type: str = "лек",
        teacher: str = "Иванов И.И.",
        room: str = "201",
        remote: bool = False,
        stud: int = 25,
        period: str = "P1",
    ) -> LessonRow:
        counter["n"] += 1
        row_number = counter["n"]
        key = "|".join(
            [
                date,
                str(pair),
                groups.casefold(),
                subgroup,
                subject.casefold(),
                subject_type,
                teacher.casefold(),
                room.casefold(),
                "1" if remote else "0",
                str(stud),
            ]
        )
        return LessonRow(
            period=period,
            source_file="rasp2.csv",
            source_row=row_number,
            date_raw="",
            iso_date=date,
            week=1,
            pair=pair,
            groups_raw=groups,
            subgroup=subgroup,
            subject=subject,
            subject_type_raw=subject_type,
            subject_type=subject_type,
            teacher=teacher,
            teacher_key=teacher.casefold(),
            room=room,
            is_remote=remote,
            stud_in_lesson=stud,
            lesson_id="",
            lesson_num="",
            dedup_key=key,
            raw_json="{}",
        )

    return make


@pytest.fixture
def make_lesson() -> Callable[..., LessonRow]:
    return lesson_factory()


@pytest.fixture
def minimal_fet(make_lesson: Callable[..., LessonRow]) -> str:
    from fetv3.activities import build
    from fetv3.config import DEFAULT_HOURS
    from fetv3.fetxml import render

    result = build([make_lesson()])
    return render(
        result,
        school="Тест",
        title="Тест",
        comment="",
        hours=DEFAULT_HOURS,
    )
