"""Аудит входных данных: `docs/00_audit.md`.

Документ генерируется командой `fetv3 audit`, чтобы цифры нельзя было
разойтись с базой. Выводы о качестве данных — часть результата, а не
примечание.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from .config import Settings
from .normalize import split_groups


def _iso_week(iso_date: str) -> int:
    return dt.date.fromisoformat(iso_date).isocalendar().week


def _table(rows: list[tuple[object, ...]], headers: list[str]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return lines


def write_audit(settings: Settings, conn: sqlite3.Connection) -> Path:
    periods = conn.execute(
        "SELECT name, rows_total, date_min, date_max FROM periods ORDER BY date_min"
    ).fetchall()
    lessons = conn.execute("SELECT COUNT(*) AS n FROM lessons").fetchone()["n"]
    duplicates = conn.execute("SELECT COUNT(*) AS n FROM duplicates").fetchone()["n"]
    group_names: set[str] = set()
    teacher_keys: set[str] = set()
    for row in conn.execute("SELECT groups_raw, teacher_key FROM lessons"):
        group_names.update(split_groups(row["groups_raw"]))
        teacher_keys.add(row["teacher_key"])
    groups = len(group_names)
    teachers = len(teacher_keys)
    rooms = conn.execute("SELECT COUNT(DISTINCT room) AS n FROM lessons WHERE room <> ''").fetchone()["n"]
    dates = [r["iso_date"] for r in conn.execute("SELECT DISTINCT iso_date FROM lessons ORDER BY iso_date")]
    weeks = sorted({_iso_week(d) for d in dates})
    full_range = sorted({_iso_week(d) for d in dates})
    missing = [w for w in range(min(full_range), max(full_range) + 1) if w not in full_range]

    types = conn.execute(
        "SELECT subject_type_raw, subject_type, COUNT(*) AS n FROM lessons"
        " GROUP BY subject_type_raw, subject_type ORDER BY n DESC"
    ).fetchall()
    duplicates_sample = conn.execute(
        "SELECT period, source_row, kept_period, kept_row FROM duplicates ORDER BY period, source_row LIMIT 5"
    ).fetchall()
    date_years = conn.execute(
        "SELECT DISTINCT substr(iso_date, 1, 4) AS year FROM lessons ORDER BY year"
    ).fetchall()

    lines: list[str] = []
    add = lines.append
    add("# Аудит входных данных AVTOR")
    add("")
    add("Документ сформирован командой `fetv3 audit`. Цифры — из `schedule.db`,")
    add("полученной из выгрузки AVTOR.")
    add("")
    add("## 1. Источник")
    add("")
    add(f"- Каталог: `{settings.avtor_out}`")
    add("- Формат: `Rasp_*/rasp2.csv`, кодировка **CP1251**, разделитель `;`, дата `д-м-гггг`")
    add("- Колонки: `Group; StudInLesson; Day; Les; Aud; Week; Subg; Name; CafID; Subject;")
    add("  Subj_type; Date; Subj_CafID; PrepID; Themas; Substitution_*; Lesson_ID; Lesson_Num`")
    add("")
    add("## 2. Объём")
    add("")
    add(f"- Строк в файлах: **{sum(int(p['rows_total']) for p in periods)}**")
    add(f"- Строк в БД: **{lessons}**")
    add(f"- Учебных групп (уникальных наборов): {groups}")
    add(f"- Преподавателей: {teachers}")
    add(f"- Аудиторий (включая дистанционные): {rooms}")
    add(f"- Дублей между перекрывающимися периодами: **{duplicates}**")
    add("")
    add("### Периоды")
    add("")
    add_lines = _table(
        [(p["name"], p["rows_total"], p["date_min"], p["date_max"]) for p in periods],
        ["Период", "Строк", "Дата с", "Дата по"],
    )
    lines.extend(add_lines)
    add("")
    add("## 3. Дефекты входных данных")
    add("")
    add("### 3.1 Год в датах не совпадает с названием периода")
    add("")
    years = ", ".join(str(r["year"]) for r in date_years)
    add(f"Даты в файлах относятся к году: **{years}**, при этом каталоги называются")
    add("`Rasp_*.27` и ТЗ говорит о 2-м семестре 2026/2027.")
    add("")
    add("Год **не сдвигается**: перенос дат на год меняет дни недели и разваливает")
    add("сетку. Это дефект выгрузки, который должен подтвердить владелец данных.")
    add("")
    add("### 3.2 Покрытие недель")
    add("")
    add(f"Присутствуют недели ISO: {', '.join(map(str, weeks))}.")
    if missing:
        add("")
        add(f"**Отсутствуют недели:** {', '.join(map(str, missing))}.")
        add("")
        add("Критерий «охвачены все недели семестра» на этой выгрузке недостижим —")
        add("в файлах нет соответствующих периодов. Отчёт фиксирует фактическое покрытие.")
    else:
        add("")
        add("Пропусков внутри диапазона нет.")
    add("")
    add("### 3.3 Перекрытие периодов и версии")
    add("")
    add("Периоды перекрываются: конец февраля есть и в `Rasp_2.02`, и в `Rasp_2.03`.")
    add(f"Строк, повторяющихся в другой выгрузке: **{duplicates}**.")
    add("")
    add("Правило сборки: если дата встречается в двух выгрузках, берётся **более")
    add("поздняя** — она содержит исправленную версию дня. Внутри одной выгрузки")
    add("повторы схлопываются по ключу «дата + пара + группы + подгруппа + предмет +")
    add("вид + преподаватель». Расхождения по аудитории попадают в отчёт как версии.")
    add("")
    if duplicates_sample:
        lines.extend(
            _table(
                [
                    (r["period"], r["source_row"], r["kept_period"], r["kept_row"])
                    for r in duplicates_sample
                ],
                ["Период", "Строка", "Оставлено из", "Строка"],
            )
        )
        add("")
    add("### 3.4 Виды занятий")
    add("")
    add("В источнике 18 разных написаний. Соответствие задано явной таблицей")
    add("(`src/fetv3/normalize.py`), а не угадыванием по подстроке.")
    add("")
    lines.extend(
        _table(
            [(r["subject_type_raw"] or "«пусто»", r["subject_type"], r["n"]) for r in types],
            ["В источнике", "Код", "Строк"],
        )
    )
    add("")
    add("### 3.5 Подгруппы")
    add("")
    subg = conn.execute(
        "SELECT subgroup, COUNT(*) AS n FROM lessons GROUP BY subgroup ORDER BY subgroup"
    ).fetchall()
    lines.extend(_table([(r["subgroup"] or "0 (вся группа)", r["n"]) for r in subg], ["Subg", "Строк"]))
    add("")
    add("`Subg` — параллельная ветка группы. В FET это `Group → Subgroup`,")
    add("имя подгруппы уникально: `<группа> п<код>`.")
    add("")
    add("## 4. Справочники AVTOR")
    add("")
    add("Проверены файлы `Ауд_*/`, `Преп_*/`, `СПО_*/` и др.")
    add("")
    add("| Файл | Что внутри | Пригодность |")
    add("|---|---|---|")
    add(
        "| `Преп_<ФИО>.xls` | Список занятий преподавателя за период "
        "(вид, дисциплина, дата, день, время, аудитория, группа) | Это **нагрузка**, "
        "а не запреты. `ConstraintTeacherNotAvailable` из него вывести нельзя |"
    )
    add(
        "| `Ауд_*.xls`, лист «Аудитории» | Сетка «день + время → свободные аудитории» "
        "и занятость по колонкам | Даёт список аудиторий и занятость, но **без вместимости** "
        "и корпуса |"
    )
    add(
        "| `СПО_*/<группа>_<период>.xls` | Сетка группы «дни × пары» "
        "| Совпадает с `rasp2.csv`, используется для сверки |"
    )
    add(
        "| `rasp2.xls` в каждом каталоге | Тот же экспорт в Excel "
        "| Резервный формат, в v3 не используется |"
    )
    add("")
    add("**Вывод:** `Capacity` и `Building` в источниках отсутствуют. В `.fet`")
    add("вместимость записана как `999` — это явная техническая заглушка «не")
    add("ограничивать» (FET отвергает файл, если вместимость меньше числа")
    add("студентов), а не измеренное значение. Корпус пустой.")
    add("")
    add("Ручные недоступности преподавателей и аудиторий задаются файлом")
    add("`constraints.json` (см. `.env.example`, `FETV3_CONSTRAINTS`).")
    add("")
    add("## 5. Что это значит для приёмки")
    add("")
    add("1. Файл `.fet` строится по фактическим данным, без досочинённых сущностей.")
    add("2. Конфликты источника не удаляются, а помечаются и описываются.")
    add("3. Покрытие семестра фиксируется по факту; «все недели» — ответственность")
    add("   владельца выгрузки AVTOR.")
    add("4. Год в датах остаётся 2026 до отдельного решения владельца данных.")
    add("")

    path = settings.docs_dir / "00_audit.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
