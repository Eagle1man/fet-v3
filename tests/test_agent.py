from __future__ import annotations

import urllib.error
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from fetv3 import db, journal
from fetv3.ai import agent
from fetv3.config import DEFAULT_HOURS, Settings
from fetv3.db import LessonRow


@pytest.fixture
def settings(tmp_path: Path, make_lesson: Callable[..., LessonRow]) -> Settings:
    config = Settings(
        avtor_out=tmp_path,
        db_path=tmp_path / "agent.db",
        out_dir=tmp_path / "out",
        docs_dir=tmp_path / "docs",
        fet_cl=None,
        school="Тест",
        hours=DEFAULT_HOURS,
        fet_time_limit=1,
        constraints=None,
        room_capacity=999,
    )
    conn = db.connect(config.db_path)
    db.init_schema(conn)
    journal.init_journal(conn)
    info = db.PeriodInfo(
        name="P1", source_dir="x", date_min="2026-01-12", date_max="2026-01-12", rows_total=1
    )
    db.replace_period(conn, info, [make_lesson(period="P1")], [])
    conn.close()
    return config


def test_agent_executes_tool_and_returns_text(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OLLAMA_ENABLED", "true")
    calls: list[dict[str, Any]] = []

    def transport(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        if len(calls) == 1:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "query_lessons",
                                "arguments": {"group": "Г1"},
                            }
                        }
                    ],
                }
            }
        return {"message": {"content": "У группы Г1 одно занятие: математика."}}

    answer = agent.ask(settings, "Что у группы Г1?", transport=transport)
    assert "одно занятие" in answer
    assert len(calls) == 2
    tool_messages = [m for m in calls[1]["messages"] if m.get("role") == "tool"]
    assert tool_messages and "Математика" in tool_messages[0]["content"]


def test_agent_parses_string_arguments(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OLLAMA_ENABLED", "true")
    calls: list[dict[str, Any]] = []

    def transport(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        if len(calls) == 1:
            return {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "query_lessons",
                                "arguments": '{"group": "Г1"}',
                            }
                        }
                    ]
                }
            }
        return {"message": {"content": "Готово."}}

    assert agent.ask(settings, "вопрос", transport=transport) == "Готово."


def test_agent_nudges_model_to_use_tools(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OLLAMA_ENABLED", "true")
    calls: list[dict[str, Any]] = []

    def transport(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        if len(calls) == 1:
            return {"message": {"content": "Наверное, занятий нет."}}
        if len(calls) == 2:
            return {
                "message": {
                    "tool_calls": [
                        {"function": {"name": "query_lessons", "arguments": {"group": "Г1"}}}
                    ]
                }
            }
        return {"message": {"content": "У группы Г1 есть математика."}}

    answer = agent.ask(settings, "Что у Г1?", transport=transport)
    assert "математика" in answer
    assert len(calls) == 3
    assert any(
        "не вызвав инструмент" in str(message.get("content", ""))
        for message in calls[1]["messages"]
    )


def test_conversation_keeps_history(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_ENABLED", "true")
    calls: list[dict[str, Any]] = []

    def transport(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        return {"message": {"content": f"ответ {len(calls)}"}}

    conversation = agent.Conversation(settings, transport=transport)
    conversation.send("первый вопрос")
    conversation.send("второй вопрос")

    second_messages = calls[1]["messages"]
    contents = [message.get("content") for message in second_messages]
    assert "первый вопрос" in contents
    assert "второй вопрос" in contents


def test_agent_reports_unavailable_ollama(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_ENABLED", "true")

    def transport(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise urllib.error.URLError("connection refused")

    with pytest.raises(agent.AssistantError) as error:
        agent.ask(settings, "вопрос", transport=transport)
    assert "Ollama недоступен" in str(error.value)


def test_agent_respects_disabled_flag(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_ENABLED", "false")
    with pytest.raises(agent.AssistantError):
        agent.ask(settings, "вопрос")


def test_agent_stops_after_max_steps(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_ENABLED", "true")
    monkeypatch.setenv("AI_MAX_STEPS", "2")

    def transport(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "message": {
                "tool_calls": [
                    {"function": {"name": "build_status", "arguments": {}}}
                ]
            }
        }

    with pytest.raises(agent.AssistantError) as error:
        agent.ask(settings, "вопрос", transport=transport)
    assert "не завершила ответ" in str(error.value)
