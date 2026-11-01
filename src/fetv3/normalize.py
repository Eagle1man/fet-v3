"""Нормализация значений AVTOR.

Ключевое правило: ничего не «угадывать». Если значение неизвестно, оно
попадает в категорию «другое» и видно в отчёте, а не превращается в догадку.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Final

# Признак виртуальной (дистанционной) аудитории в AVTOR: имя начинается с точки.
REMOTE_PREFIX: Final = "."

# Суффикс подгруппы в имени студенческого набора FET.
SUBGROUP_SEP: Final = " п"

_WS = re.compile(r"[\s\u00a0]+")

# Вид занятия из AVTOR -> канонический код.
# Каждый вариант перечислен явно: эвристика по подстроке уже приводила к ошибкам.
SUBJECT_TYPE_MAP: Final[dict[str, str]] = {
    "": "другое",
    "УРОК": "урок",
    "ЛЕК": "лек",
    "ЛЕК.": "лек",
    "ЛЕКЦИЯ": "лек",
    "ПР": "пр",
    "ПР.": "пр",
    "ПРАКТ": "пр",
    "ПРАКТИКА": "практика",
    "ЛАБ": "лаб",
    "ЛАБ.": "лаб",
    "КОМП.ЛАБ.": "лаб",
    "КОМПЬЮТЕРНАЯ ЛАБ.": "лаб",
    "ЭКЗ": "экзамен",
    "ЭКЗ.": "экзамен",
    "ЭКЗАМЕН": "экзамен",
    "ЗАЧЕТ": "зачёт",
    "ЗАЧЁТ": "зачёт",
    "ЗАЧЕТ С ОЦЕНКОЙ": "зачёт",
    "АТТЕСТАЦИЯ": "аттестация",
    "ЗАЩ.КУРС.": "курсовая",
    "ЗАЩИТА ВКР": "вкр",
    "ФК": "фк",
}


def clean(value: object) -> str:
    """Убирает NBSP, лишние пробелы и переводы строк."""
    if value is None:
        return ""
    return _WS.sub(" ", str(value)).strip()


def teacher_key(name: object) -> str:
    """Ключ сравнения ФИО: регистр и пробелы не значимы, цифры значимы.

    В AVTOR есть «Агаркова  Н.Ю.» и «Агаркова1  Н.Ю.» — это разные люди,
    поэтому суффикс-цифру сохраняем.
    """
    return clean(name).casefold()


def subject_type(raw: object) -> str:
    """Канонический код вида занятия. Неизвестное значение -> «другое»."""
    key = clean(raw).upper()
    if key in SUBJECT_TYPE_MAP:
        return SUBJECT_TYPE_MAP[key]
    # «ЛЕК. (что-то)» и подобные хвосты: берём только первое слово, если оно известно.
    head = key.split(" ")[0]
    if head in SUBJECT_TYPE_MAP:
        return SUBJECT_TYPE_MAP[head]
    return "другое"


def subject_type_display(code: str) -> str:
    return CODE_TO_DISPLAY.get(code, code)


CODE_TO_DISPLAY: Final[dict[str, str]] = {
    "лек": "лек",
    "пр": "пр",
    "практика": "практика",
    "лаб": "лаб",
    "экзамен": "экзамен",
    "зачёт": "зачёт",
    "аттестация": "аттестация",
    "курсовая": "курсовая",
    "вкр": "вкр",
    "урок": "урок",
    "фк": "фк",
    "другое": "другое",
}


def parse_date(raw: object) -> dt.date:
    """Дата AVTOR: `д-м-гггг` или `дд-мм-гггг`."""
    value = clean(raw)
    for fmt in ("%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Не разобрана дата: {raw!r}")


def split_groups(raw: object) -> list[str]:
    """Группы в одной строке AVTOR разделены запятой."""
    value = clean(raw)
    if not value:
        return []
    result: list[str] = []
    for part in value.split(","):
        name = clean(part)
        if name and name not in result:
            result.append(name)
    return result


def normalize_subgroup(raw: object) -> str:
    """`0` и пусто — вся группа; `1`/`2` — ветка подгруппы."""
    value = clean(raw)
    if value in ("", "0"):
        return ""
    return value


def is_remote_room(room: object) -> bool:
    return clean(room).startswith(REMOTE_PREFIX)


def room_name(room: object) -> str:
    return clean(room)


def parse_stud(raw: object) -> int:
    value = clean(raw)
    if not value:
        return 0
    try:
        return int(float(value.replace(",", ".")))
    except ValueError:
        return 0


def parse_pair(raw: object) -> int:
    value = clean(raw)
    if not value:
        return 0
    try:
        return int(float(value.replace(",", ".")))
    except ValueError:
        return 0


def day_label(date: dt.date) -> str:
    """Подпись дня FET: `12.01 Пн` — уникальна внутри семестра."""
    weekday = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")[date.weekday()]
    return f"{date.strftime('%d.%m')} {weekday}"
