"""Сборка активностей FET из строк AVTOR.

Правила (см. LOGIC_AVTOR_TO_FET.md и ТЗ):
  * объединённая лекция — одна активность с несколькими `<Students>`;
  * `Subg` — параллельная ветка группы, а не номер;
  * сдвоенная пара — `Duration=2`;
  * дистант — без аудитории;
  * конфликты не выбрасываются: активность помечается `locked=False` и
    попадает в отчёт с привязкой к строкам источника.
"""

from __future__ import annotations

import collections
import dataclasses
from dataclasses import dataclass, field

from .db import LessonRow
from .normalize import SUBGROUP_SEP, split_groups


@dataclass(frozen=True)
class SourceRef:
    period: str
    file: str
    row: int


@dataclass
class Activity:
    id: int
    date: str
    pair: int
    duration: int
    subject: str
    subject_type: str
    teachers: tuple[str, ...]
    students: tuple[str, ...]
    room: str
    is_remote: bool
    refs: tuple[SourceRef, ...]
    locked: bool = True
    room_final: str = ""


@dataclass(frozen=True)
class Conflict:
    kind: str  # student | teacher | room
    entity: str
    date: str
    pair: int
    activity_ids: tuple[int, ...]
    detail: str


@dataclass(frozen=True)
class RoomOverload:
    """Слот, в котором исходные данные ставят в одну аудиторию больше одной пары."""

    room: str
    date: str
    pair: int
    activity_ids: tuple[int, ...]
    clone: str


@dataclass(frozen=True)
class Revision:
    """Одна и та же пара в двух выгрузках отличается аудиторией."""

    date: str
    pair: int
    groups: str
    subject: str
    teacher: str
    kept_period: str
    kept_row: int
    kept_room: str
    other_period: str
    other_row: int
    other_room: str


@dataclass
class BuildResult:
    activities: list[Activity] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    overloads: list[RoomOverload] = field(default_factory=list)
    revisions: list[Revision] = field(default_factory=list)
    subgroups: dict[str, tuple[str, ...]] = field(default_factory=dict)
    group_students: dict[str, int] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)


def identity_key(row: LessonRow) -> tuple[str, int, str, str, str, str, str]:
    """Тождество занятия: без аудитории и числа студентов.

    Аудитория в разных выгрузках может быть исправлена — это версия данных,
    а не второе занятие. Расхождение фиксируется отдельно.
    """
    return (
        row.iso_date,
        row.pair,
        row.groups_raw.casefold(),
        row.subgroup,
        row.subject.casefold(),
        row.subject_type,
        row.teacher_key,
    )


def drop_superseded(rows: list[LessonRow]) -> tuple[list[LessonRow], list[LessonRow]]:
    """Даты, попавшие в две выгрузки, берутся из более поздней.

    Периоды — снимки на разную дату. Если 2.03 содержит 25.02, значит это
    более свежая версия этого дня: старые строки заменены, а не добавлены.
    Иначе на стыке периодов появляются ложные конфликты.
    """
    period_start: dict[str, str] = {}
    for row in rows:
        current = period_start.get(row.period)
        if current is None or row.iso_date < current:
            period_start[row.period] = row.iso_date
    order = {
        period: index
        for index, period in enumerate(sorted(period_start, key=lambda p: (period_start[p], p)))
    }
    latest: dict[str, int] = {}
    for row in rows:
        rank = order[row.period]
        if rank > latest.get(row.iso_date, -1):
            latest[row.iso_date] = rank
    kept: list[LessonRow] = []
    dropped: list[LessonRow] = []
    for row in rows:
        if order[row.period] == latest[row.iso_date]:
            kept.append(row)
        else:
            dropped.append(row)
    return kept, dropped


def _room_revisions(dropped: list[LessonRow], kept: list[LessonRow]) -> list[Revision]:
    """Расхождения по аудитории между заменённой и актуальной версией занятия."""
    index = {identity_key(row): row for row in kept}
    revisions: list[Revision] = []
    for row in dropped:
        current = index.get(identity_key(row))
        if current is None or current.room == row.room:
            continue
        revisions.append(
            Revision(
                date=current.iso_date,
                pair=current.pair,
                groups=current.groups_raw,
                subject=current.subject,
                teacher=current.teacher,
                kept_period=current.period,
                kept_row=current.source_row,
                kept_room=current.room,
                other_period=row.period,
                other_row=row.source_row,
                other_room=row.room,
            )
        )
    return revisions


def dedupe_lessons(rows: list[LessonRow]) -> tuple[list[LessonRow], list[Revision]]:
    """Схлопывает повторы и версии одного занятия.

    Побеждает более поздняя выгрузка (период и строка больше) — она сделана
    позже и содержит исправленные значения. Все расхождения попадают в отчёт.
    """
    grouped: dict[tuple[str, int, str, str, str, str, str], list[LessonRow]] = collections.defaultdict(list)
    for row in rows:
        grouped[identity_key(row)].append(row)

    result: list[LessonRow] = []
    revisions: list[Revision] = []
    for items in grouped.values():
        items.sort(key=lambda r: (r.period, r.source_row))
        kept = items[-1]
        result.append(kept)
        rooms = {item.room for item in items}
        if len(rooms) > 1:
            for other in items[:-1]:
                revisions.append(
                    Revision(
                        date=kept.iso_date,
                        pair=kept.pair,
                        groups=kept.groups_raw,
                        subject=kept.subject,
                        teacher=kept.teacher,
                        kept_period=kept.period,
                        kept_row=kept.source_row,
                        kept_room=kept.room,
                        other_period=other.period,
                        other_row=other.source_row,
                        other_room=other.room,
                    )
                )
    result.sort(key=lambda r: (r.iso_date, r.pair, r.period, r.source_row))
    return result, revisions


def group_subgroups(rows: list[LessonRow]) -> dict[str, tuple[str, ...]]:
    """Группы, у которых в источнике есть ветки `Subg` 1/2."""
    found: dict[str, set[str]] = collections.defaultdict(set)
    for row in rows:
        if not row.subgroup:
            continue
        for group in split_groups(row.groups_raw):
            found[group].add(row.subgroup)
    return {group: tuple(sorted(values)) for group, values in sorted(found.items())}


def _student_names(
    groups: list[str],
    subgroup: str,
    groups_with_subgroups: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    names: list[str] = []
    for group in groups:
        if subgroup and group in groups_with_subgroups:
            names.append(f"{group}{SUBGROUP_SEP}{subgroup}")
        else:
            names.append(group)
    return tuple(names)


@dataclass
class _Merged:
    date: str
    pair: int
    subject: str
    subject_type: str
    subgroup: str
    room: str
    is_remote: bool
    teachers: dict[str, str] = field(default_factory=dict)
    groups: list[str] = field(default_factory=list)
    refs: list[SourceRef] = field(default_factory=list)
    stud: int = 0


def _merge_rows(
    rows: list[LessonRow],
    groups_with_subgroups: dict[str, tuple[str, ...]],
) -> list[Activity]:
    """Первый проход: склейка строк в одну активность по преподавателю."""
    merged: dict[tuple[object, ...], _Merged] = {}
    for row in rows:
        key = (
            row.iso_date,
            row.pair,
            row.subject.casefold(),
            row.subject_type,
            row.subgroup,
            row.room.casefold(),
            row.is_remote,
            row.teacher_key,
        )
        entry = merged.get(key)
        if entry is None:
            entry = _Merged(
                date=row.iso_date,
                pair=row.pair,
                subject=row.subject,
                subject_type=row.subject_type,
                subgroup=row.subgroup,
                room=row.room,
                is_remote=row.is_remote,
            )
            merged[key] = entry
        entry.teachers[row.teacher_key] = row.teacher
        for group in split_groups(row.groups_raw):
            if group not in entry.groups:
                entry.groups.append(group)
        entry.refs.append(SourceRef(period=row.period, file=row.source_file, row=row.source_row))
        entry.stud = max(entry.stud, row.stud_in_lesson)

    activities: list[Activity] = []
    for entry in merged.values():
        activities.append(
            Activity(
                id=0,
                date=entry.date,
                pair=entry.pair,
                duration=1,
                subject=entry.subject,
                subject_type=entry.subject_type,
                teachers=tuple(entry.teachers.values()),
                students=_student_names(entry.groups, entry.subgroup, groups_with_subgroups),
                room=entry.room,
                is_remote=entry.is_remote,
                refs=tuple(entry.refs),
            )
        )
    return activities


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, left: int, right: int) -> None:
        self.parent[self.find(left)] = self.find(right)


def _merge_co_teaching(activities: list[Activity]) -> list[Activity]:
    """Совместное занятие: один слот, один предмет, разные преподаватели, общие группы."""
    if len(activities) < 2:
        return activities
    dsu = _DisjointSet(len(activities))
    for i, left in enumerate(activities):
        for j in range(i + 1, len(activities)):
            right = activities[j]
            if (
                left.date == right.date
                and left.pair == right.pair
                and left.subject.casefold() == right.subject.casefold()
                and left.subject_type == right.subject_type
                and left.room.casefold() == right.room.casefold()
                and left.is_remote == right.is_remote
                and set(left.students) & set(right.students)
            ):
                dsu.union(i, j)

    merged: dict[int, Activity] = {}
    for index, activity in enumerate(activities):
        root = dsu.find(index)
        current = merged.get(root)
        if current is None:
            merged[root] = dataclasses.replace(activity)
            continue
        teachers = list(current.teachers)
        for teacher in activity.teachers:
            if teacher not in teachers:
                teachers.append(teacher)
        students = list(current.students)
        for student in activity.students:
            if student not in students:
                students.append(student)
        current.teachers = tuple(teachers)
        current.students = tuple(students)
        current.refs = current.refs + activity.refs
    return list(merged.values())


def _slot_entities(
    activity: Activity,
    children_of: dict[str, tuple[str, ...]],
) -> dict[str, set[str]]:
    students: set[str] = set()
    for name in activity.students:
        students.add(name)
        # Активность всей группы занимает и все её подгруппы, но подгруппа
        # НЕ занимает родительскую группу: иначе параллельные ветки ложно
        # считались бы конфликтом.
        students.update(children_of.get(name, ()))
    return {
        "student": students,
        "teacher": set(activity.teachers),
        "room": {activity.room_final} if activity.room_final else set(),
    }


def _chain_doubles(activities: list[Activity]) -> list[Activity]:
    """Склеивает подряд идущие пары одного занятия в Duration>1.

    Склейка запрещена, если следующий слот занят другой активностью тех же
    студентов/преподавателя/аудитории: иначе вторая пара «съест» чужое занятие.
    """
    signature = collections.defaultdict(list)
    for activity in activities:
        key = (
            activity.date,
            activity.subject.casefold(),
            activity.subject_type,
            tuple(sorted(activity.teachers)),
            tuple(sorted(activity.students)),
            activity.room.casefold(),
            activity.is_remote,
        )
        signature[key].append(activity)

    owner: dict[tuple[str, str, str, int], set[int]] = collections.defaultdict(set)
    for activity in activities:
        for slot in range(activity.pair, activity.pair + activity.duration):
            for kind, names in (
                ("student", activity.students),
                ("teacher", activity.teachers),
                ("room", (activity.room,) if activity.room else ()),
            ):
                for name in names:
                    owner[(kind, name, activity.date, slot)].add(id(activity))

    result: list[Activity] = []
    for items in signature.values():
        items.sort(key=lambda a: a.pair)
        index = 0
        while index < len(items):
            first = items[index]
            end = index
            while end + 1 < len(items):
                candidate = items[end + 1]
                if candidate.pair != items[end].pair + 1:
                    break
                chain_ids = {id(item) for item in items[index : end + 1]}
                blocked = False
                for kind, names in (
                    ("student", first.students),
                    ("teacher", first.teachers),
                    ("room", (first.room,) if first.room else ()),
                ):
                    for name in names:
                        others = owner.get((kind, name, first.date, candidate.pair), set())
                        if others - chain_ids - {id(candidate)}:
                            blocked = True
                            break
                    if blocked:
                        break
                if blocked:
                    break
                end += 1
            chained = items[index : end + 1]
            if len(chained) > 1:
                first.duration = len(chained)
                first.refs = tuple(ref for act in chained for ref in act.refs)
            result.append(first)
            index = end + 1
    return result


def _assign_rooms(result: BuildResult) -> None:
    """Разводит реальные накладки аудиторий клонами `ауд#2`, не теряя информацию."""
    occupancy: dict[tuple[str, str, int], int] = {}
    ordered = sorted(
        result.activities,
        key=lambda a: (a.date, a.pair, a.room, a.subject, a.teachers, a.students),
    )
    for activity in ordered:
        if not activity.room or activity.is_remote:
            activity.room_final = ""
            continue
        attempt = 0
        while True:
            clone = activity.room if attempt == 0 else f"{activity.room}#{attempt + 1}"
            slots = range(activity.pair, activity.pair + activity.duration)
            if all((clone, activity.date, slot) not in occupancy for slot in slots):
                for slot in slots:
                    occupancy[(clone, activity.date, slot)] = activity.id
                activity.room_final = clone
                if attempt > 0:
                    result.overloads.append(
                        RoomOverload(
                            room=activity.room,
                            date=activity.date,
                            pair=activity.pair,
                            activity_ids=(activity.id,),
                            clone=clone,
                        )
                    )
                break
            attempt += 1


def _find_conflicts(
    result: BuildResult,
    children_of: dict[str, tuple[str, ...]],
) -> None:
    occupancy: dict[tuple[str, str, str, int], list[int]] = collections.defaultdict(list)
    detail: dict[int, Activity] = {}
    for activity in result.activities:
        detail[activity.id] = activity
        entities = _slot_entities(activity, children_of)
        for slot in range(activity.pair, activity.pair + activity.duration):
            for kind, names in entities.items():
                for name in names:
                    occupancy[(kind, name, activity.date, slot)].append(activity.id)

    conflicted: set[int] = set()
    for (kind, entity, date, slot), ids in sorted(occupancy.items()):
        unique = sorted(set(ids))
        if len(unique) < 2:
            continue
        conflicted.update(unique)
        parts = []
        for activity_id in unique:
            activity = detail[activity_id]
            refs = ", ".join(f"{ref.period}#{ref.row}" for ref in activity.refs[:3])
            parts.append(f"Id{activity_id} «{activity.subject}» ({', '.join(activity.teachers)}) [{refs}]")
        result.conflicts.append(
            Conflict(
                kind=kind,
                entity=entity,
                date=date,
                pair=slot,
                activity_ids=tuple(unique),
                detail="; ".join(parts),
            )
        )

    for activity in result.activities:
        activity.locked = activity.id not in conflicted


def build(rows: list[LessonRow]) -> BuildResult:
    kept, dropped = drop_superseded(rows)
    kept, revisions = dedupe_lessons(kept)
    revisions.extend(_room_revisions(dropped, kept))
    superseded = len(dropped)
    rows = kept
    groups_with_subgroups = group_subgroups(rows)

    group_students: dict[str, int] = {}
    for row in rows:
        for group in split_groups(row.groups_raw):
            group_students[group] = max(group_students.get(group, 0), row.stud_in_lesson)

    parent_of: dict[str, str] = {}
    children_of: dict[str, tuple[str, ...]] = {}
    for group, subgroups in groups_with_subgroups.items():
        names = []
        for code in subgroups:
            name = f"{group}{SUBGROUP_SEP}{code}"
            parent_of[name] = group
            names.append(name)
        children_of[group] = tuple(names)

    activities = _merge_rows(rows, groups_with_subgroups)
    activities = _merge_co_teaching(activities)
    activities = _chain_doubles(activities)
    activities.sort(key=lambda a: (a.date, a.pair, a.subject, a.teachers, a.students))
    for index, activity in enumerate(activities, start=1):
        activity.id = index

    result = BuildResult(
        activities=activities,
        subgroups=groups_with_subgroups,
        group_students=group_students,
        revisions=revisions,
    )
    _assign_rooms(result)
    _find_conflicts(result, children_of)

    result.stats = {
        "activities": len(result.activities),
        "teachers": len({t for a in result.activities for t in a.teachers}),
        "subjects": len({a.subject for a in result.activities}),
        "groups": len({s for a in result.activities for s in a.students if s not in parent_of}),
        "subgroups": len(parent_of),
        "rooms": len({a.room_final for a in result.activities if a.room_final}),
        "remote": sum(1 for a in result.activities if a.is_remote),
        "doubles": sum(1 for a in result.activities if a.duration > 1),
        "conflicts_student": sum(1 for c in result.conflicts if c.kind == "student"),
        "conflicts_teacher": sum(1 for c in result.conflicts if c.kind == "teacher"),
        "conflicts_room": sum(1 for c in result.conflicts if c.kind == "room"),
        "unlocked": sum(1 for a in result.activities if not a.locked),
        "room_clones": len(result.overloads),
        "revisions": len(result.revisions),
        "superseded_rows": superseded,
    }
    return result
