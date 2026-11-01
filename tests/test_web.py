from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fetv3 import db, journal
from fetv3.config import DEFAULT_HOURS, Settings
from fetv3.db import LessonRow
from fetv3.web.app import JOBS, create_app


def _settings(tmp_path: Path, *, password: str, public_read: bool = False) -> Settings:
    return Settings(
        avtor_out=tmp_path,
        db_path=tmp_path / "web.db",
        out_dir=tmp_path / "out",
        docs_dir=tmp_path / "docs",
        fet_cl=None,
        school="Тестовый университет",
        hours=DEFAULT_HOURS,
        fet_time_limit=1,
        constraints=None,
        room_capacity=999,
        web_password=password,
        web_secret="test-secret",
        public_read=public_read,
    )


@pytest.fixture(autouse=True)
def _clear_jobs() -> None:
    JOBS.clear()


@pytest.fixture
def settings(tmp_path: Path, make_lesson: Callable[..., LessonRow]) -> Settings:
    config = _settings(tmp_path, password="secret")
    conn = db.connect(config.db_path)
    db.init_schema(conn)
    journal.init_journal(conn)
    info = db.PeriodInfo(
        name="P1", source_dir="x", date_min="2026-01-12", date_max="2026-01-12", rows_total=1
    )
    db.replace_period(conn, info, [make_lesson(period="P1")], [])
    conn.close()
    return config


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def _login(client: TestClient, password: str = "secret") -> None:
    response = client.post(
        "/login", data={"password": password, "next_url": "/"}, follow_redirects=False
    )
    assert response.status_code == 303


def _csrf(client: TestClient) -> str:
    response = client.get("/lessons", params={"period": "P1", "row": "1"})
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, "CSRF-токен не найден"
    return match.group(1)


def test_anonymous_is_redirected_to_login(settings: Settings) -> None:
    client = _client(settings)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_rejects_wrong_password(settings: Settings) -> None:
    client = _client(settings)
    response = client.post("/login", data={"password": "nope", "next_url": "/"})
    assert response.status_code == 401
    assert "Неверный пароль" in response.text


def test_pages_render_after_login(settings: Settings) -> None:
    client = _client(settings)
    _login(client)
    for path, marker in (
        ("/", "Обзор"),
        ("/lessons", "Занятия"),
        ("/week", "Неделя"),
        ("/conflicts", "Конфликты"),
        ("/publish", "Публикация"),
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert marker in response.text, path


def test_public_read_allows_anonymous(settings: Settings) -> None:
    import dataclasses

    client = _client(dataclasses.replace(settings, public_read=True))
    assert client.get("/").status_code == 200
    assert client.get("/lessons").status_code == 200


def test_api_groups_and_schedule(settings: Settings) -> None:
    client = _client(settings)
    _login(client)
    groups = client.get("/api/site/groups")
    assert groups.status_code == 200
    payload = groups.json()
    assert "Колледж" in payload
    assert "Г1" in payload["Колледж"]

    schedule = client.get("/api/site/schedule", params={"group": "Г1"})
    assert schedule.status_code == 200
    body = schedule.json()
    assert "Г1" in body
    label, weeks = body["Г1"]
    assert " - " in label
    assert weeks


def test_api_schedule_unknown_group(settings: Settings) -> None:
    client = _client(settings)
    _login(client)
    response = client.get("/api/site/schedule", params={"group": "нет"})
    assert response.status_code == 404


def test_edit_requires_csrf(settings: Settings) -> None:
    client = _client(settings)
    _login(client)
    response = client.post(
        "/lessons/edit",
        data={
            "csrf_token": "wrong",
            "period": "P1",
            "source_row": 1,
            "iso_date": "2026-01-12",
            "pair": 3,
            "room": "201",
            "teacher": "Иванов И.И.",
            "subject": "Математика",
            "actor": "тест",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "CSRF" in response.headers["location"]
    conn = db.connect(settings.db_path)
    pair = conn.execute("SELECT pair FROM lessons WHERE period='P1' AND source_row=1").fetchone()[
        "pair"
    ]
    conn.close()
    assert pair == 1


def test_edit_applies_with_csrf(settings: Settings) -> None:
    client = _client(settings)
    _login(client)
    token = _csrf(client)
    response = client.post(
        "/lessons/edit",
        data={
            "csrf_token": token,
            "period": "P1",
            "source_row": 1,
            "iso_date": "2026-01-12",
            "pair": 4,
            "room": "305",
            "teacher": "Иванов И.И.",
            "subject": "Математика",
            "actor": "тест",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    conn = db.connect(settings.db_path)
    row = conn.execute(
        "SELECT pair, room FROM lessons WHERE period='P1' AND source_row=1"
    ).fetchone()
    conn.close()
    assert row["pair"] == 4
    assert row["room"] == "305"


def test_health(settings: Settings) -> None:
    client = _client(settings)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert "lessons=1" in response.text


def test_public_schedule_without_export(settings: Settings) -> None:
    client = _client(settings)
    redirect = client.get("/schedule", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "/schedule/"
    response = client.get("/schedule/")
    assert response.status_code == 404
    assert "не опубликовано" in response.text


def test_public_schedule_serves_latest_export(settings: Settings) -> None:
    from fetv3 import sitepub

    sitepub.export_site(settings)
    client = _client(settings)
    index = client.get("/schedule/")
    assert index.status_code == 200
    assert "assets/viewer.js" in index.text

    data = client.get("/schedule/data.json")
    assert data.status_code == 200
    assert '"schedules"' in data.text

    asset = client.get("/schedule/assets/viewer.css")
    assert asset.status_code == 200


def test_public_schedule_blocks_traversal(settings: Settings) -> None:
    from fetv3 import sitepub

    sitepub.export_site(settings)
    client = _client(settings)
    assert client.get("/schedule/..%2f..%2fsecret").status_code == 404
