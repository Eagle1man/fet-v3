"""Проверка .fet: статические инварианты + реальный запуск fet-cl.

Статическая проверка ловит то, что FET отвергнет молчаливым «Cannot read»,
поэтому она повторяет требования схемы, а не ограничивается разбором XML.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from .config import Settings

FATAL_MARKERS = (
    "Cannot read",
    "Invalid",
    "data is wrong",
    "aborting",
    "Cannot precompute",
    "impossible",
)

REQUIRED_SECTIONS = (
    "Days_List",
    "Hours_List",
    "Subjects_List",
    "Activity_Tags_List",
    "Teachers_List",
    "Students_List",
    "Rooms_List",
    "Activities_List",
    "Time_Constraints_List",
    "Space_Constraints_List",
)


@dataclass
class StaticCheck:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


@dataclass
class FetRun:
    executed: bool
    returncode: int | None
    stdout: str
    stderr: str
    workdir: str
    files: list[str] = field(default_factory=list)
    fatal: list[str] = field(default_factory=list)
    time_exceeded: bool = False
    soft_conflicts: int | None = None
    broken_soft: int | None = None


@dataclass
class ValidationResult:
    name: str
    file: str
    static: StaticCheck
    run: FetRun | None

    @property
    def ok(self) -> bool:
        if not self.static.ok or self.static.errors:
            return False
        if self.run is None:
            return True
        return self.run.executed and not self.run.fatal


def static_check(path: Path) -> StaticCheck:
    result = StaticCheck(ok=False)
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        result.errors.append(f"XML ParseError: {exc}")
        return result
    except OSError as exc:
        result.errors.append(f"Файл не читается: {exc}")
        return result

    if root.tag != "fet":
        result.errors.append(f"Корневой тег {root.tag!r}, ожидался 'fet'")
    if not root.get("version"):
        result.errors.append("У корня нет атрибута version")

    sections = {}
    for name in REQUIRED_SECTIONS:
        element = root.find(name)
        if element is None:
            result.errors.append(f"Нет раздела {name}")
        else:
            sections[name] = element
    result.counts = {name: _count_children(name, element) for name, element in sections.items()}

    days = {
        _text(day.find("Name"))
        for day in sections.get("Days_List", ET.Element("empty")).findall("Day")
    }
    hours = {
        _text(hour.find("Name"))
        for hour in sections.get("Hours_List", ET.Element("empty")).findall("Hour")
    }

    student_names = _student_names(sections.get("Students_List"))
    teacher_names = {
        _text(item.find("Name"))
        for item in sections.get("Teachers_List", ET.Element("empty")).findall("Teacher")
    }
    room_names = {
        _text(item.find("Name"))
        for item in sections.get("Rooms_List", ET.Element("empty")).findall("Room")
    }
    subject_names = {
        _text(item.find("Name"))
        for item in sections.get("Subjects_List", ET.Element("empty")).findall("Subject")
    }
    tag_names = {
        _text(item.find("Name"))
        for item in sections.get("Activity_Tags_List", ET.Element("empty")).findall("Activity_Tag")
    }

    activity_ids: set[int] = set()
    for activity in sections.get("Activities_List", ET.Element("empty")).findall("Activity"):
        activity_id = _text(activity.find("Id"))
        if not activity_id.isdigit():
            result.errors.append("Активность без числового Id")
            continue
        number = int(activity_id)
        if number in activity_ids:
            result.errors.append(f"Повтор Id активности: {number}")
        activity_ids.add(number)
        if len(activity.findall("Students")) == 0:
            result.errors.append(f"Id{number}: нет Students")
        if len(activity.findall("Teacher")) == 0:
            result.errors.append(f"Id{number}: нет Teacher")
        for student in activity.findall("Students"):
            name = _text(student)
            if name not in student_names:
                result.errors.append(f"Id{number}: неизвестный Students {name!r}")
        for teacher in activity.findall("Teacher"):
            name = _text(teacher)
            if name not in teacher_names:
                result.errors.append(f"Id{number}: неизвестный Teacher {name!r}")
        subject = _text(activity.find("Subject"))
        if subject not in subject_names:
            result.errors.append(f"Id{number}: неизвестный Subject {subject!r}")
        tag = _text(activity.find("Activity_Tag"))
        if tag and tag not in tag_names:
            result.errors.append(f"Id{number}: неизвестный Activity_Tag {tag!r}")
        room = _text(activity.find("Room"))
        if room and room not in room_names:
            result.errors.append(f"Id{number}: неизвестная Room {room!r}")
        duration = _text(activity.find("Duration"))
        if not duration.isdigit() or int(duration) < 1:
            result.errors.append(f"Id{number}: некорректная Duration {duration!r}")

    for constraint in sections.get("Time_Constraints_List", ET.Element("empty")).findall(
        "ConstraintActivityPreferredStartingTimes"
    ):
        activity_id = _text(constraint.find("Activity_Id"))
        if not activity_id.isdigit() or int(activity_id) not in activity_ids:
            result.errors.append(f"PreferredStartingTimes ссылается на Id {activity_id!r}")
        times = constraint.findall("Preferred_Starting_Time")
        if not times:
            result.errors.append("PreferredStartingTimes без Preferred_Starting_Time")
        for preferred in times:
            day = _text(preferred.find("Preferred_Starting_Day"))
            if day not in days:
                result.errors.append(f"PreferredStartingTimes: неизвестный день {day!r}")
            hour = _text(preferred.find("Preferred_Starting_Hour"))
            if hour not in hours:
                result.errors.append(f"PreferredStartingTimes: неизвестная пара {hour!r}")

    for constraint in sections.get("Time_Constraints_List", ET.Element("empty")).findall(
        "ConstraintTeacherNotAvailableTimes"
    ):
        name = _text(constraint.find("Teacher"))
        if name not in teacher_names:
            result.errors.append(f"TeacherNotAvailableTimes: неизвестный преподаватель {name!r}")
        _check_unavailable_times(result, constraint, days, hours)

    for constraint in sections.get("Time_Constraints_List", ET.Element("empty")).findall(
        "ConstraintRoomNotAvailableTimes"
    ):
        name = _text(constraint.find("Room"))
        if name not in room_names:
            result.errors.append(f"RoomNotAvailableTimes: неизвестная аудитория {name!r}")
        _check_unavailable_times(result, constraint, days, hours)

    for constraint in sections.get("Space_Constraints_List", ET.Element("empty")).findall(
        "ConstraintActivityPreferredRooms"
    ):
        activity_id = _text(constraint.find("Activity_Id"))
        if not activity_id.isdigit() or int(activity_id) not in activity_ids:
            result.errors.append(f"PreferredRooms ссылается на Id {activity_id!r}")
        room = _text(constraint.find("Preferred_Room"))
        if room not in room_names:
            result.errors.append(f"PreferredRooms: неизвестная аудитория {room!r}")

    if result.counts.get("Activities_List", 0) == 0:
        result.errors.append("Раздел Activities_List пуст")
    if result.counts.get("Teachers_List", 0) == 0:
        result.errors.append("Раздел Teachers_List пуст")
    if result.counts.get("Students_List", 0) == 0:
        result.errors.append("Раздел Students_List пуст")

    result.ok = not result.errors
    return result


def _text(element: ET.Element | None) -> str:
    if element is None or element.text is None:
        return ""
    return element.text.strip()


_COUNTED_CHILD = {
    "Days_List": "Day",
    "Hours_List": "Hour",
    "Subjects_List": "Subject",
    "Activity_Tags_List": "Activity_Tag",
    "Teachers_List": "Teacher",
    "Students_List": "Year",
    "Rooms_List": "Room",
    "Activities_List": "Activity",
}


def _count_children(section: str, element: ET.Element) -> int:
    child = _COUNTED_CHILD.get(section)
    if child is None:
        return len(list(element))
    return len(element.findall(child))


def _check_unavailable_times(
    result: StaticCheck,
    constraint: ET.Element,
    days: set[str],
    hours: set[str],
) -> None:
    declared = _text(constraint.find("Number_of_Not_Available_Times"))
    times = constraint.findall("Not_Available_Time")
    if not declared.isdigit() or int(declared) != len(times):
        result.errors.append("Not_Available_Times: число не совпадает со списком")
    for item in times:
        day = _text(item.find("Day"))
        if day not in days:
            result.errors.append(f"Not_Available_Time: неизвестный день {day!r}")
        hour = _text(item.find("Hour"))
        if hour not in hours:
            result.errors.append(f"Not_Available_Time: неизвестная пара {hour!r}")


def _student_names(section: ET.Element | None) -> set[str]:
    names: set[str] = set()
    if section is None:
        return names
    for year in section.findall("Year"):
        names.add(_text(year.find("Name")))
        for group in year.findall("Group"):
            names.add(_text(group.find("Name")))
            for subgroup in group.findall("Subgroup"):
                names.add(_text(subgroup.find("Name")))
    return names


def find_fet_cl(settings: Settings) -> Path | None:
    if settings.fet_cl is not None and settings.fet_cl.exists():
        return settings.fet_cl
    fallback = shutil.which("fet-cl")
    return Path(fallback) if fallback else None


def run_fet_cl(settings: Settings, path: Path, workdir: Path) -> FetRun | None:
    fet_cl = find_fet_cl(settings)
    if fet_cl is None:
        return None
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    command = [
        str(fet_cl),
        f"--inputfile={path}",
        f"--outputdir={workdir}",
        f"--timelimitseconds={settings.fet_time_limit}",
        "--writetimetableconflicts=true",
        "--language=ru",
    ]
    env = dict(os.environ)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(60, settings.fet_time_limit * 20),
            env=env,
            check=False,
        )
        stdout, stderr, code = completed.stdout, completed.stderr, completed.returncode
        executed = True
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or "" if isinstance(exc.stdout, str) else ""
        stderr = f"Таймаут запуска fet-cl ({exc.timeout} с)"
        code = None
        executed = True
    except OSError as exc:
        stdout, stderr, code = "", f"Не удалось запустить fet-cl: {exc}", None
        executed = False

    text = f"{stdout}\n{stderr}"
    fatal = [line.strip() for line in text.splitlines() if _is_fatal(line)]
    files = sorted(item.name for item in workdir.iterdir()) if workdir.exists() else []
    soft, broken = _read_soft_conflicts(workdir)
    return FetRun(
        executed=executed,
        returncode=code,
        stdout=stdout,
        stderr=stderr,
        workdir=str(workdir),
        files=files,
        fatal=fatal,
        time_exceeded="time exceeded" in text.casefold(),
        soft_conflicts=soft,
        broken_soft=broken,
    )


_SOFT_BROKEN = re.compile(r"Number of broken soft constraints:\s*(\d+)", re.IGNORECASE)
_SOFT_TOTAL = re.compile(r"Total soft conflicts:\s*(\d+)", re.IGNORECASE)


def _read_soft_conflicts(workdir: Path) -> tuple[int | None, int | None]:
    if not workdir.exists():
        return None, None
    for path in workdir.rglob("*soft_conflicts*.txt"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        broken = _SOFT_BROKEN.search(text)
        total = _SOFT_TOTAL.search(text)
        return (
            int(total.group(1)) if total else None,
            int(broken.group(1)) if broken else None,
        )
    return None, None


def _is_fatal(line: str) -> bool:
    return any(re.search(re.escape(marker), line, re.IGNORECASE) for marker in FATAL_MARKERS)


def validate_file(settings: Settings, name: str, path: Path) -> ValidationResult:
    static = static_check(path)
    workdir = settings.validation_dir / name
    run: FetRun | None = None
    if static.ok:
        run = run_fet_cl(settings, path, workdir)
    return ValidationResult(name=name, file=str(path), static=static, run=run)


def validation_markdown(result: ValidationResult) -> str:
    lines = [f"# Проверка {result.name}", "", f"Файл: `{result.file}`", ""]
    lines.append("## Статическая проверка схемы")
    lines.append("")
    lines.append(f"- Результат: {'OK' if result.static.ok else 'ОШИБКИ'}")
    if result.static.counts:
        lines.append("")
        lines.append("| Раздел | Элементов |")
        lines.append("|---|---|")
        for name, count in result.static.counts.items():
            lines.append(f"| {name} | {count} |")
    if result.static.errors:
        lines.append("")
        lines.append("### Ошибки")
        for error in result.static.errors:
            lines.append(f"- {error}")
    if result.static.warnings:
        lines.append("")
        lines.append("### Предупреждения")
        for warning in result.static.warnings:
            lines.append(f"- {warning}")

    lines.append("")
    lines.append("## Запуск fet-cl")
    if result.run is None:
        lines.append("")
        lines.append("fet-cl не найден или статическая проверка не прошла — прогон пропущен.")
    else:
        lines.append("")
        lines.append(f"- Код возврата: {result.run.returncode}")
        lines.append(f"- Каталог результатов: `{result.run.workdir}`")
        lines.append(f"- Файлов на выходе: {len(result.run.files)}")
        if result.run.time_exceeded:
            lines.append("- Генерация остановлена по лимиту времени (файл при этом корректен)")
        if result.run.broken_soft is not None:
            lines.append(
                f"- Нарушено мягких ограничений: {result.run.broken_soft} "
                f"(суммарный вес {result.run.soft_conflicts})"
            )
        if result.run.fatal:
            lines.append("")
            lines.append("### Фатальные сообщения")
            for line in result.run.fatal[:50]:
                lines.append(f"- {line}")
        else:
            lines.append("- Фатальных сообщений нет")
        if result.run.stderr.strip():
            lines.append("")
            lines.append("### stderr")
            lines.append("```")
            lines.append(result.run.stderr.strip()[:4000])
            lines.append("```")
    return "\n".join(lines) + "\n"
