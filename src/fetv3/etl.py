"""ETL: выгрузка AVTOR (rasp2.csv) -> SQLite.

Файлы AVTOR в CP1251, разделитель `;`, дата `д-м-гггг`.
Все строки сохраняются как есть (одна строка источника = одна строка БД);
дубли между периодами только помечаются в таблице `duplicates`, а
устраняются на этапе сборки в пределах выбранного охвата.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import db
from .config import Settings
from .normalize import (
    clean,
    is_remote_room,
    normalize_subgroup,
    parse_date,
    parse_pair,
    parse_stud,
    room_name,
    split_groups,
    subject_type,
    teacher_key,
)

REQUIRED_COLUMNS = (
    "Group",
    "StudInLesson",
    "Day",
    "Les",
    "Aud",
    "Week",
    "Subg",
    "Name",
    "Subject",
    "Subj_type",
    "Date",
    "Lesson_ID",
    "Lesson_Num",
)

ENCODINGS = ("cp1251", "utf-8-sig")


class EtlError(RuntimeError):
    """Ошибка входных данных с указанием файла и строки."""


@dataclass
class EtlResult:
    periods: list[db.PeriodInfo] = field(default_factory=list)
    rows_total: int = 0
    rows_loaded: int = 0
    duplicates: int = 0
    dates: list[str] = field(default_factory=list)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    last_error: Exception | None = None
    for encoding in ENCODINGS:
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                reader = csv.DictReader(handle, delimiter=";")
                fieldnames = reader.fieldnames or []
                missing = [c for c in REQUIRED_COLUMNS if c not in fieldnames]
                if missing:
                    raise EtlError(f"{path.name}: нет колонок {missing}")
                return [dict(row) for row in reader]
        except UnicodeDecodeError as exc:  # пробуем следующую кодировку
            last_error = exc
    raise EtlError(f"{path.name}: не удалось определить кодировку ({last_error})")


def make_dedup_key(row: db.LessonRow) -> str:
    parts = (
        row.iso_date,
        str(row.pair),
        row.groups_raw.casefold(),
        row.subgroup,
        row.subject.casefold(),
        row.subject_type,
        row.teacher_key,
        row.room.casefold(),
        "1" if row.is_remote else "0",
        str(row.stud_in_lesson),
    )
    return "|".join(parts)


def build_lesson(
    raw: dict[str, str],
    *,
    period: str,
    source_file: str,
    source_row: int,
) -> db.LessonRow:
    try:
        date = parse_date(raw.get("Date"))
    except ValueError as exc:
        raise EtlError(f"{source_file}:{source_row}: {exc}") from exc

    groups = split_groups(raw.get("Group"))
    if not groups:
        raise EtlError(f"{source_file}:{source_row}: пустой список групп")

    subject = clean(raw.get("Subject"))
    if not subject:
        raise EtlError(f"{source_file}:{source_row}: пустой предмет")

    teacher = clean(raw.get("Name"))
    if not teacher:
        raise EtlError(f"{source_file}:{source_row}: пустой преподаватель")

    pair = parse_pair(raw.get("Les"))
    if pair < 1:
        raise EtlError(f"{source_file}:{source_row}: некорректная пара {raw.get('Les')!r}")

    room = room_name(raw.get("Aud"))
    row = db.LessonRow(
        period=period,
        source_file=source_file,
        source_row=source_row,
        date_raw=clean(raw.get("Date")),
        iso_date=date.isoformat(),
        week=parse_pair(raw.get("Week")),
        pair=pair,
        groups_raw=", ".join(groups),
        subgroup=normalize_subgroup(raw.get("Subg")),
        subject=subject,
        subject_type_raw=clean(raw.get("Subj_type")),
        subject_type=subject_type(raw.get("Subj_type")),
        teacher=teacher,
        teacher_key=teacher_key(teacher),
        room=room,
        is_remote=is_remote_room(room),
        stud_in_lesson=parse_stud(raw.get("StudInLesson")),
        lesson_id=clean(raw.get("Lesson_ID")),
        lesson_num=clean(raw.get("Lesson_Num")),
        dedup_key="",
        raw_json=json.dumps(raw, ensure_ascii=False),
    )
    return replace(row, dedup_key=make_dedup_key(row))


def discover_periods(avtor_out: Path) -> list[Path]:
    """Каталоги вида `Rasp_*`, в которых есть rasp2.csv."""
    period_dirs = [
        path
        for path in sorted(avtor_out.iterdir())
        if path.is_dir() and path.name.startswith("Rasp_") and (path / "rasp2.csv").exists()
    ]
    if not period_dirs:
        raise EtlError(f"В {avtor_out} не найдено ни одного каталога Rasp_* с rasp2.csv")
    return period_dirs


def run_etl(settings: Settings) -> EtlResult:
    conn = db.connect(settings.db_path)
    try:
        db.init_schema(conn)
        result = EtlResult()
        seen: dict[str, tuple[str, int]] = {}
        all_dates: set[str] = set()

        for period_dir in discover_periods(settings.avtor_out):
            period = period_dir.name
            csv_path = period_dir / "rasp2.csv"
            raw_rows = read_csv_rows(csv_path)

            lessons: list[db.LessonRow] = []
            duplicates: list[db.DuplicateRow] = []
            for index, raw in enumerate(raw_rows, start=2):  # строка 1 — заголовок
                lesson = build_lesson(
                    raw,
                    period=period,
                    source_file=csv_path.name,
                    source_row=index,
                )
                lessons.append(lesson)
                all_dates.add(lesson.iso_date)
                kept = seen.get(lesson.dedup_key)
                if kept is None:
                    seen[lesson.dedup_key] = (period, index)
                else:
                    duplicates.append(
                        db.DuplicateRow(
                            period=period,
                            source_row=index,
                            kept_period=kept[0],
                            kept_row=kept[1],
                            dedup_key=lesson.dedup_key,
                        )
                    )

            dates = sorted({row.iso_date for row in lessons})
            if not dates:
                raise EtlError(f"{period}: нет ни одной строки")
            info = db.PeriodInfo(
                name=period,
                source_dir=str(period_dir),
                date_min=dates[0],
                date_max=dates[-1],
                rows_total=len(raw_rows),
            )
            db.replace_period(conn, info, lessons, duplicates)
            result.periods.append(info)
            result.rows_total += len(raw_rows)
            result.rows_loaded += db.lesson_count(conn, period)
            result.duplicates += len(duplicates)

        result.dates = sorted(all_dates)
        db.set_meta(conn, "etl_completed_at", dt.datetime.now().isoformat(timespec="seconds"))
        db.set_meta(conn, "avtor_out", str(settings.avtor_out))
        conn.commit()
        return result
    finally:
        conn.close()
