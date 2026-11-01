from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from pathlib import Path

import pytest

from fetv3 import db, journal
from fetv3.config import DEFAULT_HOURS, Settings
from fetv3.db import LessonRow


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        avtor_out=tmp_path,
        db_path=tmp_path / "ui.db",
        out_dir=tmp_path / "out",
        docs_dir=tmp_path / "docs",
        fet_cl=None,
        school="Тест",
        hours=DEFAULT_HOURS,
        fet_time_limit=1,
        constraints=None,
        room_capacity=999,
    )


def test_ui_shows_lessons(tmp_path: Path, make_lesson: Callable[..., LessonRow]) -> None:
    settings = _settings(tmp_path)
    conn = db.connect(settings.db_path)
    db.init_schema(conn)
    journal.init_journal(conn)
    info = db.PeriodInfo(
        name="P1", source_dir="x", date_min="2026-01-12", date_max="2026-01-12", rows_total=1
    )
    db.replace_period(conn, info, [make_lesson(period="P1")], [])
    conn.close()

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tkinter недоступен в этой среде")
    root.withdraw()

    from fetv3.ui.app import App

    app = App(root, settings)
    try:
        assert len(app.tree.get_children()) == 1
        app.group_var.set("Г1")
        app.refresh()
        assert len(app.tree.get_children()) == 1
        app.reset()
    finally:
        app.close()
