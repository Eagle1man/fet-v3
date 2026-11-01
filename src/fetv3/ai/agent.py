"""Ассистент: локальная модель Ollama + инструменты проекта.

Модель не генерирует XML и не придумывает данные: она выбирает функцию,
результат берётся из БД и отчётов. Если Ollama недоступен, ассистент честно
сообщает об этом, а не отвечает «по памяти».

`Conversation` хранит историю сообщений, поэтому подтверждение правки
(«да, подтверждаю») работает в интерактивном режиме `run.py chat`.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, cast

from ..config import Settings
from . import tools

Transport = Callable[[str, dict[str, Any]], dict[str, Any]]

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
# qwen2.5:1.5b инструменты игнорирует и отвечает «по памяти»; 7b вызывает их стабильно.
DEFAULT_MODEL = "qwen2.5:7b-instruct"
DEFAULT_TIMEOUT = 180
DEFAULT_MAX_STEPS = 8

SYSTEM_PROMPT = """Ты — помощник по расписанию колледжа. Твоя задача — отвечать
на вопросы о занятиях и предлагать изменения.

Жёсткие правила:
1. Не придумывай занятия, преподавателей, аудитории и даты. Любой факт
   бери только из результата инструмента.
2. Если инструмент ничего не нашёл, так и скажи: «В данных нет».
3. Любую правку сначала показывай как предложение (edit_lesson с confirm=false)
   и объясняй, что изменится. Занятие ищи через selector (группа, дата,
   предмет, пара), а не по номеру строки. Применяй только после явного
   согласия человека — повторным вызовом с confirm=true.
4. После применённой правки напомни, что нужно пересобрать .fet (build_fet)
   и проверить его (validate_fet).
5. Отвечай по-русски, коротко и по делу. Не выводи JSON целиком, если
   пользователь не просил.
"""


class AssistantError(RuntimeError):
    """Ассистент не может ответить (нет модели, сеть, лимит шагов)."""


def _default_transport(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return cast("dict[str, Any]", json.loads(response.read().decode("utf-8")))


class Conversation:
    """Диалог с моделью: история сообщений и вызовы инструментов."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: Transport | None = None,
        model: str | None = None,
    ) -> None:
        if os.environ.get("OLLAMA_ENABLED", "true").strip().lower() in ("0", "false", "no"):
            raise AssistantError("OLLAMA_ENABLED=false в .env")
        self.settings = settings
        self.base_url = os.environ.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
        self.timeout = int(os.environ.get("OLLAMA_TIMEOUT", str(DEFAULT_TIMEOUT)) or DEFAULT_TIMEOUT)
        self.max_steps = int(os.environ.get("AI_MAX_STEPS", str(DEFAULT_MAX_STEPS)) or DEFAULT_MAX_STEPS)
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._send = transport or (
            lambda url, payload: _default_transport(url, payload, self.timeout)
        )
        self.last_tool_calls: list[str] = []

    def send(self, question: str) -> str:
        self.messages.append({"role": "user", "content": question})
        return self._loop()

    def _loop(self) -> str:
        url = f"{self.base_url}/api/chat"
        self.last_tool_calls = []
        nudged = False

        for _ in range(self.max_steps):
            payload = {
                "model": self.model,
                "messages": self.messages,
                "tools": tools.schemas(),
                "stream": False,
                "options": {"temperature": 0},
            }
            try:
                response = self._send(url, payload)
            except urllib.error.URLError as exc:
                raise AssistantError(
                    f"Ollama недоступен по {self.base_url}: {exc.reason}. Запустите `ollama serve`."
                ) from exc
            except TimeoutError as exc:
                raise AssistantError(f"Ollama не ответил за {self.timeout} с.") from exc

            if "error" in response:
                raise AssistantError(f"Ollama: {response['error']}")

            message = response.get("message") or {}
            calls = message.get("tool_calls") or []
            if not calls:
                text = str(message.get("content") or "").strip()
                # Модель ответила «по памяти», не заглянув в данные. Один раз
                # требуем вызвать инструмент: иначе ответ будет выдумкой.
                if not self.last_tool_calls and not nudged and text:
                    nudged = True
                    self.messages.append({"role": "assistant", "content": text})
                    self.messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Ты ответил, не вызвав инструмент. Это запрещено: факты о "
                                "расписании берутся только из инструментов. Вызови подходящий "
                                "инструмент (обычно query_lessons) и ответь по его результату."
                            ),
                        }
                    )
                    continue
                if not text:
                    raise AssistantError("Модель вернула пустой ответ.")
                self.messages.append({"role": "assistant", "content": text})
                return text

            self.messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": calls,
                }
            )
            for call in calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                arguments = function.get("arguments") or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                self.last_tool_calls.append(name)
                result = tools.call(self.settings, name, dict(arguments))
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_name": name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        raise AssistantError(
            "Модель не завершила ответ за "
            f"{self.max_steps} шагов (вызовы: {', '.join(self.last_tool_calls) or 'нет'})."
        )


def ask(
    settings: Settings,
    question: str,
    *,
    transport: Transport | None = None,
    model: str | None = None,
) -> str:
    return Conversation(settings, transport=transport, model=model).send(question)
