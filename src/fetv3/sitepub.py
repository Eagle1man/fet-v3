"""Публикация расписания на сайт вуза.

Формат совпадает с действующим API iubip.ru:
  POST /local/templates/univer/include/schedule/ajax/read-file-groups.php
    do=groups            -> {"Академия": {"Группа": 1}}
    do=schedule&group=X  -> {"X": ["дд.мм.гггг - дд.мм.гггг", {"10": [...]}]}

Экспорт — это каталог с JSON + автономный просмотрщик, который открывается
на любой ВМ и не требует PHP/Bitrix. Тот же каталог можно выгрузить по FTP
или отдать веб-приложению как API.
"""

from __future__ import annotations

import datetime as dt
import ftplib
import hashlib
import json
import shutil
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import db
from .config import Settings
from .db import LessonRow
from .normalize import split_groups

_PAYLOAD_CACHE: dict[tuple[str, float, float, str | None], dict[str, Any]] = {}

MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

WEEKDAYS_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")

SOURCE_HTML = Path(__file__).resolve().parent / "web" / "export"


@dataclass
class ExportResult:
    version: str
    path: Path
    zip_path: Path
    groups: int
    lessons: int
    faculties: dict[str, int]
    stats: dict[str, Any]


def _ru_date(value: dt.date) -> str:
    return f"{value.day} {MONTHS_GENITIVE[value.month - 1]} {value.year} г."


def _key(name: str) -> str:
    return "".join(name.split()).casefold()


def _scope_label(rows: list[LessonRow]) -> str:
    dates = sorted({row.iso_date for row in rows})
    first = dt.date.fromisoformat(dates[0]).strftime("%d.%m.%Y")
    last = dt.date.fromisoformat(dates[-1]).strftime("%d.%m.%Y")
    return f"{first} - {last}"


def _cache_key(settings: Settings, scope: str | None) -> tuple[str, float, float, str | None]:
    def mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    mapping = settings.site_groups if settings.site_groups is not None else Path("")
    return (str(settings.db_path), mtime(settings.db_path), mtime(mapping), scope)


def _week_meta(week: int, year: int) -> dict[str, Any]:
    monday = dt.date.fromisocalendar(year, week, 1)
    today = dt.date.today()
    days: list[str] = []
    for offset in range(7):
        current = monday + dt.timedelta(days=offset)
        days.append("Сегодня" if current == today else _ru_date(current))
    sunday = monday + dt.timedelta(days=6)
    return {
        "per": f"{monday.strftime('%d.%m.%Y')} - {sunday.strftime('%d.%m.%Y')}",
        "today": _ru_date(today),
        "days": days,
    }


def _lesson(row: LessonRow, lesson_id: int, group: str) -> dict[str, Any]:
    date = dt.date.fromisoformat(row.iso_date)
    return {
        "GROUP": group,
        "DAY": str(date.isoweekday()),
        "LES": str(row.pair),
        "AUD": row.room,
        "WEEK": str(date.isocalendar().week),
        "SUBG": row.subgroup or "0",
        "POST": "-",
        "NAME": row.teacher,
        "SUBJECT": row.subject,
        "SUBJ_TYPE": row.subject_type_raw or row.subject_type,
        "DATE": date.strftime("%d-%m-%Y"),
        "CAFEDRA": "-",
        "THEME": "-",
        "ID": str(lesson_id),
        "COURSE": "0",
        "FACULTY": "-",
        "FORMOBUCH": "-",
        "deleted": 0,
    }


def load_group_faculties(settings: Settings) -> dict[str, str]:
    """Сопоставление «группа → академия» из снимка сайта (если он есть)."""
    if settings.site_groups is None or not settings.site_groups.exists():
        return {}
    payload = json.loads(settings.site_groups.read_text(encoding="utf-8-sig"))
    mapping: dict[str, str] = {}
    for faculty, groups in payload.items():
        for name in list(groups):
            mapping[_key(str(name))] = str(faculty)
    return mapping


def faculty_of(mapping: dict[str, str], group: str, default: str) -> str:
    return mapping.get(_key(group), default)


def group_schedule(rows: list[LessonRow], label: str) -> list[Any]:
    """Формат сайта: [метка периода, {"неделя": [мета, {день: {пара: [...]}}]}]."""
    weeks: dict[tuple[int, int], list[LessonRow]] = defaultdict(list)
    for row in rows:
        date = dt.date.fromisoformat(row.iso_date)
        iso = date.isocalendar()
        weeks[(iso.year, iso.week)].append(row)

    payload: dict[str, Any] = {}
    for (year, week), items in sorted(weeks.items()):
        grid: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for index, row in enumerate(sorted(items, key=lambda r: (r.iso_date, r.pair)), start=1):
            date = dt.date.fromisoformat(row.iso_date)
            day = str(date.isoweekday())
            pair = str(row.pair)
            grid.setdefault(day, {}).setdefault(pair, []).append(
                _lesson(row, index, split_groups(row.groups_raw)[0] if row.groups_raw else "")
            )
        payload[str(week)] = [_week_meta(week, year), grid]
    return [label, payload]


def build_site_payload(settings: Settings, scope: str | None = None) -> dict[str, Any]:
    key = _cache_key(settings, scope)
    cached = _PAYLOAD_CACHE.get(key)
    if cached is not None:
        return cached
    conn = db.connect(settings.db_path)
    try:
        rows = db.load_lessons(conn, scope)
    finally:
        conn.close()
    if not rows:
        raise RuntimeError("Нет строк для публикации")

    mapping = load_group_faculties(settings)
    per_group: dict[str, list[LessonRow]] = defaultdict(list)
    for row in rows:
        for group in split_groups(row.groups_raw):
            per_group[group].append(row)

    groups: dict[str, dict[str, int]] = defaultdict(dict)
    schedules: dict[str, Any] = {}
    label = _scope_label(rows)
    for group in sorted(per_group):
        faculty = faculty_of(mapping, group, settings.site_faculty)
        groups[faculty][group] = 1
        schedules[group] = group_schedule(per_group[group], label)

    weeks = {str(dt.date.fromisoformat(r.iso_date).isocalendar().week) for r in rows}
    payload = {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "scope": scope or "все периоды",
        "school": settings.school,
        "groups": {faculty: dict(sorted(items.items())) for faculty, items in sorted(groups.items())},
        "schedules": schedules,
        "stats": {
            "groups": len(per_group),
            "faculties": len(groups),
            "lessons": sum(len(items) for items in per_group.values()),
            "rows": len(rows),
            "weeks": len(weeks),
        },
    }
    _PAYLOAD_CACHE.clear()
    _PAYLOAD_CACHE[key] = payload
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_site(settings: Settings, scope: str | None = None) -> ExportResult:
    payload = build_site_payload(settings, scope)
    settings.site_dir.mkdir(parents=True, exist_ok=True)

    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    version = stamp
    suffix = 2
    while (settings.site_dir / version).exists():
        version = f"{stamp}_{suffix}"
        suffix += 1

    target = settings.site_dir / version
    target.mkdir(parents=True)

    (target / "groups.json").write_text(
        json.dumps(payload["groups"], ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (target / "data.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )

    assets = target / "assets"
    assets.mkdir()
    for name in ("index.html", "viewer.css", "viewer.js"):
        shutil.copyfile(SOURCE_HTML / name, assets / name)
    shutil.move(str(assets / "index.html"), str(target / "index.html"))

    files = []
    for path in sorted(target.rglob("*")):
        if path.is_file():
            files.append(
                {
                    "name": str(path.relative_to(target)).replace("\\", "/"),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )

    manifest = {
        "version": version,
        "generated": payload["generated"],
        "scope": payload["scope"],
        "school": payload["school"],
        "stats": payload["stats"],
        "files": files,
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    zip_path = settings.site_dir / f"site_{version}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(target))

    (settings.site_dir / "latest.txt").write_text(version, encoding="utf-8")
    return ExportResult(
        version=version,
        path=target,
        zip_path=zip_path,
        groups=int(payload["stats"]["groups"]),
        lessons=int(payload["stats"]["lessons"]),
        faculties={faculty: len(items) for faculty, items in payload["groups"].items()},
        stats=payload["stats"],
    )


def list_exports(settings: Settings) -> list[dict[str, Any]]:
    if not settings.site_dir.exists():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(settings.site_dir.iterdir(), reverse=True):
        if not path.is_dir():
            continue
        manifest_path = path / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        result.append(
            {
                "version": manifest.get("version", path.name),
                "generated": manifest.get("generated", ""),
                "scope": manifest.get("scope", ""),
                "stats": manifest.get("stats", {}),
                "zip": (settings.site_dir / f"site_{path.name}.zip").exists(),
            }
        )
    return result


def resolve_export(settings: Settings, version: str) -> Path:
    if not version or any(symbol in version for symbol in "/\\.."):
        raise ValueError("Некорректная версия выгрузки")
    path = settings.site_dir / version
    if not path.is_dir():
        raise FileNotFoundError(version)
    return path


def upload_ftp(settings: Settings, version: str) -> dict[str, Any]:
    """Выгрузка каталога на FTP вуза (пароль — только в .env)."""
    if not settings.ftp_host:
        raise RuntimeError("FTP не настроен: задайте FETV3_FTP_HOST/USER/PASSWORD")
    source = resolve_export(settings, version)
    uploaded = 0
    with ftplib.FTP(settings.ftp_host, timeout=60) as ftp:
        ftp.login(settings.ftp_user, settings.ftp_password)
        if settings.ftp_dir:
            ftp.cwd(settings.ftp_dir)
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source)
            parent = relative.parent
            if str(parent) != ".":
                _ftp_makedirs(ftp, str(parent).replace("\\", "/"))
            with path.open("rb") as handle:
                ftp.storbinary(f"STOR {relative.as_posix()}", handle)
            uploaded += 1
    return {"version": version, "uploaded": uploaded, "host": settings.ftp_host}


def _ftp_makedirs(ftp: ftplib.FTP, directory: str) -> None:
    current = ""
    for part in directory.split("/"):
        current = f"{current}/{part}" if current else part
        try:
            ftp.mkd(current)
        except ftplib.error_perm:
            continue


def latest_version(settings: Settings) -> str | None:
    marker = settings.site_dir / "latest.txt"
    if marker.exists():
        return marker.read_text(encoding="utf-8").strip() or None
    exports = list_exports(settings)
    return exports[0]["version"] if exports else None
