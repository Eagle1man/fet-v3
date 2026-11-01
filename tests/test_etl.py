from __future__ import annotations

from pathlib import Path

import pytest

from fetv3 import db
from fetv3.config import DEFAULT_HOURS, Settings
from fetv3.etl import EtlError, build_lesson, read_csv_rows, run_etl

HEADER = (
    "Group;StudInLesson;Day;Les;Aud;Week;Subg;Name;CafID;Subject;Subj_type;Date;"
    "Subj_CafID;PrepID;Themas;Substitution_Name;Substitution_PrepID;"
    "Substitution_Subject;Substitution_Subj_type;Substitution_Subj_CafID;"
    "Lesson_ID;Lesson_Num"
)

ROW = (
    " ЭД301;33;1;5; 506;26;0;Чмут Г.А.;14;Регион. налоги;ПРАКТИКА;"
    "25-02-2026;0;0; ;;;;;;1_26;3"
)

ROW_DUP = ROW.replace("25-02-2026", "25-02-2026")


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        avtor_out=tmp_path / "OUT",
        db_path=tmp_path / "schedule.db",
        out_dir=tmp_path / "out",
        docs_dir=tmp_path / "docs",
        fet_cl=None,
        school="Тест",
        hours=DEFAULT_HOURS,
        fet_time_limit=1,
        constraints=None,
        room_capacity=999,
    )


def _write_period(root: Path, name: str, rows: list[str]) -> None:
    period = root / name
    period.mkdir(parents=True)
    content = "\n".join([HEADER, *rows]) + "\n"
    (period / "rasp2.csv").write_bytes(content.encode("cp1251"))


def test_build_lesson_normalizes(make_lesson: object) -> None:
    raw = {column: "" for column in HEADER.split(";")}
    raw.update(
        Group=" ЭД301,  МД103 ",
        StudInLesson="33",
        Les="5",
        Aud=" 506 ",
        Week="26",
        Subg="0",
        Name="Чмут Г.А.",
        Subject="Регион. налоги",
        Subj_type="ПРАКТИКА",
        Date="25-02-2026",
    )
    row = build_lesson(raw, period="P", source_file="rasp2.csv", source_row=2)
    assert row.groups_raw == "ЭД301, МД103"
    assert row.iso_date == "2026-02-25"
    assert row.subject_type == "практика"
    assert row.is_remote is False
    assert row.dedup_key


def test_build_lesson_rejects_bad_date() -> None:
    raw = {column: "" for column in HEADER.split(";")}
    raw.update(Group="Г1", Les="1", Name="Иванов", Subject="Математика", Date="31-02-2026")
    with pytest.raises(EtlError):
        build_lesson(raw, period="P", source_file="rasp2.csv", source_row=2)


def test_run_etl_is_idempotent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_period(settings.avtor_out, "Rasp_1", [ROW, ROW])
    first = run_etl(settings)
    second = run_etl(settings)

    assert first.rows_total == 2
    assert first.duplicates == 1
    assert second.rows_loaded == 2

    conn = db.connect(settings.db_path)
    assert db.lesson_count(conn) == 2
    assert len(db.list_periods(conn)) == 1
    assert len(db.list_duplicates(conn)) == 1
    conn.close()


def test_read_csv_rows_detects_missing_column(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_bytes("Group;Les\nГ1;1\n".encode("cp1251"))
    with pytest.raises(EtlError):
        read_csv_rows(path)
