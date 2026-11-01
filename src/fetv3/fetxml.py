"""Запись .fet по схеме FET 7.10.x.

Схема сверена с эталонным файлом, который открывает FET 7.10.5
(`fet_raspisanie/s10.fet`): обязательны `Days_List`, `Hours_List`,
`*_List`-разделы и `ConstraintActivityPreferredStartingTime`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from .activities import BuildResult
from .normalize import SUBGROUP_SEP, day_label, parse_date

YEAR_NAME = "Колледж"

CONSTRAINT_WEIGHT_LOCKED = 100
# Эмпирика из прошлой итерации: незаблокированное ограничение с весом 100
# подвешивает генератор FET. Конфликтные активности получают вес 50.
CONSTRAINT_WEIGHT_UNLOCKED = 50


@dataclass(frozen=True)
class ManualUnavailable:
    kind: str  # teacher | room
    name: str
    day: str
    hour: int


class FetXmlError(RuntimeError):
    """Некорректные данные для записи .fet."""


def load_constraints(path: Path | None) -> list[ManualUnavailable]:
    """Ручные недоступности из JSON.

    Формат:
        {"unavailable": [
            {"kind": "teacher", "name": "Иванов И.И.", "day": "12.01 Пн", "hour": 3}
        ]}
    """
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    items = payload.get("unavailable", [])
    result: list[ManualUnavailable] = []
    for item in items:
        kind = str(item["kind"]).strip().lower()
        if kind not in ("teacher", "room"):
            raise FetXmlError(f"Неизвестный вид ограничения: {kind}")
        hour = int(item["hour"])
        if not 1 <= hour <= 8:
            raise FetXmlError(f"Некорректная пара в ограничении: {hour}")
        result.append(
            ManualUnavailable(
                kind=kind,
                name=str(item["name"]).strip(),
                day=str(item["day"]).strip(),
                hour=hour,
            )
        )
    return result


def _tag(subject_type: str) -> str:
    return subject_type or "другое"


def render(
    result: BuildResult,
    *,
    school: str,
    title: str,
    comment: str,
    hours: tuple[str, ...],
    unavailable: list[ManualUnavailable] | None = None,
    room_capacity: int = 999,
) -> str:
    if not result.activities:
        raise FetXmlError("Нет активностей для записи")

    activities = sorted(result.activities, key=lambda a: a.id)
    dates = sorted({a.date for a in activities})
    day_names = {date: day_label(parse_date(date)) for date in dates}
    for date, name in day_names.items():
        if not name:
            raise FetXmlError(f"Пустая подпись дня для {date}")

    teachers = sorted({teacher for a in activities for teacher in a.teachers})
    subjects = sorted({a.subject for a in activities})
    tags = sorted({_tag(a.subject_type) for a in activities})
    rooms = sorted({a.room_final for a in activities if a.room_final})
    students = result.group_students

    lines: list[str] = []
    add = lines.append
    add('<?xml version="1.0" encoding="UTF-8"?>')
    add('<fet version="7.10.5">')
    add(f"<Institution_Name>{escape(school)}</Institution_Name>")
    add(f"<Comments>{escape(comment)}</Comments>")

    add("<Days_List>")
    add(f"<Number_of_Days>{len(dates)}</Number_of_Days>")
    for date in dates:
        add(f"<Day><Name>{escape(day_names[date])}</Name></Day>")
    add("</Days_List>")

    add("<Hours_List>")
    add(f"<Number_of_Hours>{len(hours)}</Number_of_Hours>")
    for hour in hours:
        add(f"<Hour><Name>{escape(hour)}</Name></Hour>")
    add("</Hours_List>")

    add("<Subjects_List>")
    for subject in subjects:
        add(f"<Subject><Name>{escape(subject)}</Name><Comments></Comments></Subject>")
    add("</Subjects_List>")

    add("<Activity_Tags_List>")
    for tag in tags:
        add(f"<Activity_Tag><Name>{escape(tag)}</Name></Activity_Tag>")
    add("</Activity_Tags_List>")

    add("<Teachers_List>")
    for teacher in teachers:
        add(f"<Teacher><Name>{escape(teacher)}</Name><Comments></Comments></Teacher>")
    add("</Teachers_List>")

    add("<Students_List>")
    add(f"<Year><Name>{escape(YEAR_NAME)}</Name><Number_of_Students>0</Number_of_Students>")
    subgroup_groups = sorted(result.subgroups)
    plain_groups = sorted(students)
    for group in plain_groups:
        add("<Group>")
        add(f"<Name>{escape(group)}</Name>")
        add(f"<Number_of_Students>{max(0, students.get(group, 0))}</Number_of_Students>")
        add("<Comments></Comments>")
        if group in subgroup_groups:
            for code in result.subgroups[group]:
                add("<Subgroup>")
                add(f"<Name>{escape(_subgroup_name(group, code))}</Name>")
                add("<Number_of_Students>0</Number_of_Students>")
                add("<Comments></Comments>")
                add("</Subgroup>")
        add("</Group>")
    add("</Year>")
    add("</Students_List>")

    add("<Buildings_List>")
    add("</Buildings_List>")

    add("<Rooms_List>")
    for room in rooms:
        add(f"<Room><Name>{escape(room)}</Name><Building></Building><Capacity>{room_capacity}</Capacity>")
        add("<Comments></Comments></Room>")
    add("</Rooms_List>")

    add("<Activities_List>")
    for activity in activities:
        add("<Activity>")
        for teacher in activity.teachers:
            add(f"<Teacher>{escape(teacher)}</Teacher>")
        add(f"<Subject>{escape(activity.subject)}</Subject>")
        add(f"<Activity_Tag>{escape(_tag(activity.subject_type))}</Activity_Tag>")
        for student in activity.students:
            add(f"<Students>{escape(student)}</Students>")
        add(f"<Duration>{activity.duration}</Duration>")
        add(f"<Total_Duration>{activity.duration}</Total_Duration>")
        add(f"<Id>{activity.id}</Id>")
        add(f"<Activity_Group_Id>{activity.id}</Activity_Group_Id>")
        add("<Active>true</Active>")
        add("<Comments></Comments>")
        add("</Activity>")
    add("</Activities_List>")

    add("<Time_Constraints_List>")
    add("<ConstraintBasicCompulsoryTime>")
    add("<Weight_Percentage>100</Weight_Percentage>")
    add("<Active>true</Active>")
    add("<Comments></Comments>")
    add("</ConstraintBasicCompulsoryTime>")
    for activity in activities:
        weight = CONSTRAINT_WEIGHT_LOCKED if activity.locked else CONSTRAINT_WEIGHT_UNLOCKED
        add("<ConstraintActivityPreferredStartingTimes>")
        add(f"<Weight_Percentage>{weight}</Weight_Percentage>")
        add(f"<Activity_Id>{activity.id}</Activity_Id>")
        add("<Number_of_Preferred_Starting_Times>1</Number_of_Preferred_Starting_Times>")
        add("<Preferred_Starting_Time>")
        add(f"<Preferred_Starting_Day>{escape(day_names[activity.date])}</Preferred_Starting_Day>")
        add(f"<Preferred_Starting_Hour>{escape(_hour_name(hours, activity.pair))}</Preferred_Starting_Hour>")
        add("</Preferred_Starting_Time>")
        add("<Active>true</Active>")
        add("<Comments></Comments>")
        add("</ConstraintActivityPreferredStartingTimes>")
    _emit_unavailable_time(lines, unavailable or [], day_names, hours, kind="teacher")
    _emit_unavailable_time(lines, unavailable or [], day_names, hours, kind="room")
    add("</Time_Constraints_List>")

    add("<Space_Constraints_List>")
    add("<ConstraintBasicCompulsorySpace>")
    add("<Weight_Percentage>100</Weight_Percentage>")
    add("<Active>true</Active>")
    add("<Comments></Comments>")
    add("</ConstraintBasicCompulsorySpace>")
    for activity in activities:
        if not activity.room_final:
            continue
        add("<ConstraintActivityPreferredRooms>")
        add("<Weight_Percentage>100</Weight_Percentage>")
        add(f"<Activity_Id>{activity.id}</Activity_Id>")
        add("<Number_of_Preferred_Rooms>1</Number_of_Preferred_Rooms>")
        add(f"<Preferred_Room>{escape(activity.room_final)}</Preferred_Room>")
        add("<Active>true</Active>")
        add("<Comments></Comments>")
        add("</ConstraintActivityPreferredRooms>")
    add("</Space_Constraints_List>")

    add("</fet>")
    return "\n".join(lines) + "\n"


def _subgroup_name(group: str, code: str) -> str:
    return f"{group}{SUBGROUP_SEP}{code}"


def _hour_name(hours: tuple[str, ...], pair: int) -> str:
    if not 1 <= pair <= len(hours):
        raise FetXmlError(f"Пара {pair} вне сетки 1..{len(hours)}")
    return hours[pair - 1]


def _emit_unavailable_time(
    lines: list[str],
    items: list[ManualUnavailable],
    day_names: dict[str, str],
    hours: tuple[str, ...],
    *,
    kind: str,
) -> None:
    selected = [item for item in items if item.kind == kind]
    if not selected:
        return
    grouped: dict[str, list[ManualUnavailable]] = {}
    names: dict[str, str] = {}
    for item in selected:
        if item.day not in day_names.values():
            raise FetXmlError(f"Неизвестный день в ограничении: {item.day}")
        names.setdefault(item.name.casefold(), item.name)
        grouped.setdefault(item.name.casefold(), []).append(item)
    tag = (
        "ConstraintTeacherNotAvailableTimes"
        if kind == "teacher"
        else "ConstraintRoomNotAvailableTimes"
    )
    field = "Teacher" if kind == "teacher" else "Room"
    for key in sorted(grouped):
        items_for_name = sorted(grouped[key], key=lambda i: (i.day, i.hour))
        lines.append(f"<{tag}>")
        lines.append("<Weight_Percentage>100</Weight_Percentage>")
        lines.append(f"<{field}>{escape(names[key])}</{field}>")
        lines.append(
            f"<Number_of_Not_Available_Times>{len(items_for_name)}</Number_of_Not_Available_Times>"
        )
        for item in items_for_name:
            lines.append("<Not_Available_Time>")
            lines.append(f"<Day>{escape(item.day)}</Day>")
            lines.append(f"<Hour>{escape(_hour_name(hours, item.hour))}</Hour>")
            lines.append("</Not_Available_Time>")
        lines.append("<Active>true</Active>")
        lines.append("<Comments></Comments>")
        lines.append(f"</{tag}>")


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
