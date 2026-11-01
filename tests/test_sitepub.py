from __future__ import annotations

import json
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from fetv3 import db, journal, sitepub
from fetv3.config import DEFAULT_HOURS, Settings
from fetv3.db import LessonRow


@pytest.fixture
def settings(tmp_path: Path, make_lesson: Callable[..., LessonRow]) -> Settings:
    site_groups = tmp_path / "site_groups.json"
    site_groups.write_text(
        json.dumps({"Академия тестовая": {"Г1": 1}}, ensure_ascii=False), encoding="utf-8"
    )
    config = Settings(
        avtor_out=tmp_path,
        db_path=tmp_path / "site.db",
        out_dir=tmp_path / "out",
        docs_dir=tmp_path / "docs",
        fet_cl=None,
        school="Тестовый университет",
        hours=DEFAULT_HOURS,
        fet_time_limit=1,
        constraints=None,
        room_capacity=999,
        site_groups=site_groups,
        site_faculty="Колледж",
    )
    conn = db.connect(config.db_path)
    db.init_schema(conn)
    journal.init_journal(conn)
    info = db.PeriodInfo(
        name="P1", source_dir="x", date_min="2026-01-12", date_max="2026-01-12", rows_total=3
    )
    rows = [
        make_lesson(period="P1", groups="Г1", pair=1, subject="Математика"),
        make_lesson(period="P1", groups="Г1", pair=2, subject="Физика", teacher="Петров П.П."),
        make_lesson(period="P1", groups="Г2", pair=1, subject="Математика"),
    ]
    db.replace_period(conn, info, rows, [])
    conn.close()
    return config


def test_export_creates_site_contract(settings: Settings) -> None:
    result = sitepub.export_site(settings)
    assert result.path.is_dir()
    assert result.groups == 2
    assert result.zip_path.exists()

    groups = json.loads((result.path / "groups.json").read_text(encoding="utf-8"))
    assert groups["Академия тестовая"] == {"Г1": 1}
    assert groups["Колледж"] == {"Г2": 1}

    data = json.loads((result.path / "data.json").read_text(encoding="utf-8"))
    schedule = data["schedules"]["Г1"]
    assert isinstance(schedule, list) and len(schedule) == 2
    label, weeks = schedule
    assert " - " in label
    week = next(iter(weeks))
    meta, grid = weeks[week]
    assert set(meta) == {"per", "today", "days"}
    assert len(meta["days"]) == 7
    lesson = grid["1"]["1"][0]
    assert set(lesson) == {
        "GROUP",
        "DAY",
        "LES",
        "AUD",
        "WEEK",
        "SUBG",
        "POST",
        "NAME",
        "SUBJECT",
        "SUBJ_TYPE",
        "DATE",
        "CAFEDRA",
        "THEME",
        "ID",
        "COURSE",
        "FACULTY",
        "FORMOBUCH",
        "deleted",
    }
    assert lesson["DATE"] == "12-01-2026"
    assert lesson["DAY"] == "1"
    assert lesson["LES"] == "1"
    assert lesson["WEEK"] == week
    assert lesson["deleted"] == 0


def test_manifest_and_zip(settings: Settings) -> None:
    result = sitepub.export_site(settings)
    manifest = json.loads((result.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == result.version
    assert manifest["stats"]["groups"] == 2
    names = {item["name"] for item in manifest["files"]}
    assert {"data.json", "groups.json", "index.html"} <= names
    with zipfile.ZipFile(result.zip_path) as archive:
        assert "data.json" in archive.namelist()
        assert "index.html" in archive.namelist()


def test_viewer_assets_are_copied(settings: Settings) -> None:
    result = sitepub.export_site(settings)
    assert (result.path / "assets" / "viewer.css").exists()
    assert (result.path / "assets" / "viewer.js").exists()
    html = (result.path / "index.html").read_text(encoding="utf-8")
    assert "assets/viewer.js" in html


def test_list_exports_and_latest(settings: Settings) -> None:
    first = sitepub.export_site(settings)
    second = sitepub.export_site(settings)
    exports = sitepub.list_exports(settings)
    versions = {item["version"] for item in exports}
    assert {first.version, second.version} <= versions
    assert sitepub.latest_version(settings) == second.version


def test_resolve_rejects_traversal(settings: Settings) -> None:
    with pytest.raises(ValueError):
        sitepub.resolve_export(settings, "../secret")


def test_upload_requires_configuration(settings: Settings) -> None:
    result = sitepub.export_site(settings)
    with pytest.raises(RuntimeError):
        sitepub.upload_ftp(settings, result.version)
