"""CLI: etl → build → validate → report → audit (+ ui, ask)."""

from __future__ import annotations

import argparse
import sys
import traceback
from typing import Any

from . import pipeline
from .config import Settings, get_settings


def _print_builds(payloads: list[dict[str, Any]]) -> None:
    for payload in payloads:
        stats = payload["stats"]
        conflicts = (
            stats.get("conflicts_student", 0)
            + stats.get("conflicts_teacher", 0)
            + stats.get("conflicts_room", 0)
        )
        print(
            f"{payload['name']}: активностей {stats.get('activities', 0)}, "
            f"групп {stats.get('groups', 0)}, подгрупп {stats.get('subgroups', 0)}, "
            f"конфликтов {conflicts}, накладок аудиторий {stats.get('room_clones', 0)}"
        )


def command_etl(settings: Settings) -> int:
    payload = pipeline.run_etl_step(settings)
    print(
        f"ETL: периодов {len(payload['periods'])}, строк {payload['rows_total']}, "
        f"дублей {payload['duplicates']}"
    )
    return 0


def command_build(settings: Settings, scope: str | None) -> int:
    payloads = pipeline.build_fet(settings, scope)
    if not payloads:
        print("Нечего собирать.", file=sys.stderr)
        return 1
    _print_builds(payloads)
    return 0


def command_validate(settings: Settings) -> int:
    summary = pipeline.validate_fet(settings)
    for name, item in summary.items():
        status = "OK" if item["static_ok"] and not item["fatal"] else "ОШИБКИ"
        print(
            f"{name}: схема {'OK' if item['static_ok'] else 'ОШИБКИ'}, "
            f"fet-cl {'запущен' if item['run'] else 'нет'}, статус {status}"
        )
    return 0 if all(item["static_ok"] for item in summary.values()) else 2


def command_report(settings: Settings) -> int:
    md, xlsx = pipeline.generate_reports(settings)
    print(f"Отчёт: {md}")
    print(f"Отчёт: {xlsx}")
    return 0


def command_audit(settings: Settings) -> int:
    print(f"Аудит: {pipeline.generate_audit(settings)}")
    return 0


def command_ui(settings: Settings) -> int:
    from .ui.app import main as run_ui

    return run_ui(settings)


def command_ask(settings: Settings, question: str) -> int:
    from .ai.agent import AssistantError, ask

    try:
        answer = ask(settings, question)
    except AssistantError as exc:
        print(f"Ассистент недоступен: {exc}", file=sys.stderr)
        return 2
    print(answer)
    return 0


def command_chat(settings: Settings) -> int:
    from .ai.agent import AssistantError, Conversation

    try:
        conversation = Conversation(settings)
    except AssistantError as exc:
        print(f"Ассистент недоступен: {exc}", file=sys.stderr)
        return 2
    print("Диалог с локальной моделью. Пустая строка — пропуск, «выход» — конец.")
    while True:
        try:
            line = input("вы> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line.lower() in {"выход", "exit", "quit"}:
            break
        if not line:
            continue
        try:
            print(conversation.send(line))
        except AssistantError as exc:
            print(f"Ошибка ассистента: {exc}", file=sys.stderr)
    return 0


def command_all(settings: Settings) -> int:
    command_etl(settings)
    if command_build(settings, None):
        return 1
    code = command_validate(settings)
    command_report(settings)
    command_audit(settings)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fetv3", description="Перенос расписания AVTOR -> FET")
    parser.add_argument("--env", default=".env", help="Путь к .env (по умолчанию .env в корне)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("etl", help="Загрузить выгрузку AVTOR в SQLite")
    build_parser = sub.add_parser("build", help="Собрать .fet")
    build_parser.add_argument("--scope", default=None, help="Только один период (имя каталога)")
    sub.add_parser("validate", help="Проверить .fet схемой и fet-cl")
    sub.add_parser("report", help="Сформировать md/xlsx отчёты")
    sub.add_parser("audit", help="Сформировать docs/00_audit.md")
    sub.add_parser("ui", help="Открыть панель Tkinter")
    ask_parser = sub.add_parser("ask", help="Спросить локального ИИ-ассистента")
    ask_parser.add_argument("question", help="Вопрос по расписанию")
    sub.add_parser("chat", help="Диалог с ассистентом (подтверждение правок)")
    sub.add_parser("all", help="etl + build + validate + report + audit")

    args = parser.parse_args(argv)
    settings = get_settings(args.env)
    try:
        if args.command == "etl":
            return command_etl(settings)
        if args.command == "build":
            return command_build(settings, args.scope)
        if args.command == "validate":
            return command_validate(settings)
        if args.command == "report":
            return command_report(settings)
        if args.command == "audit":
            return command_audit(settings)
        if args.command == "ui":
            return command_ui(settings)
        if args.command == "ask":
            return command_ask(settings, args.question)
        if args.command == "chat":
            return command_chat(settings)
        if args.command == "all":
            return command_all(settings)
    except Exception as exc:  # noqa: BLE001 - CLI обязан показать причину
        print(f"Ошибка: {exc}", file=sys.stderr)
        if "--traceback" in sys.argv:
            traceback.print_exc()
        return 1
    parser.print_help()
    return 1
