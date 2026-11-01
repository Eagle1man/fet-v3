"""Tkinter-панель: поиск по расписанию, правка с журналом, пересборка.

UI не пишет `.fet` вручную: правки идут в `schedule.db`, а кнопки вызывают
те же команды CLI, что и конвейер. Поэтому проверка остаётся настоящей.
"""

from __future__ import annotations

import datetime as dt
import subprocess
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk
from typing import Any

from .. import db, journal
from ..config import PROJECT_ROOT, Settings
from ..normalize import split_groups

COLUMNS = (
    ("date", "Дата", 90),
    ("pair", "Пара", 50),
    ("groups", "Группы", 160),
    ("subgroup", "Подгр.", 60),
    ("subject", "Предмет", 260),
    ("kind", "Вид", 80),
    ("teacher", "Преподаватель", 170),
    ("room", "Аудитория", 90),
    ("period", "Период", 170),
    ("row", "Строка", 60),
)

EDIT_FIELDS = (
    ("iso_date", "Дата (ГГГГ-ММ-ДД)"),
    ("pair", "Пара (1-8)"),
    ("room", "Аудитория"),
    ("teacher", "Преподаватель"),
    ("subject", "Предмет"),
)


class App:
    def __init__(self, root: tk.Tk, settings: Settings) -> None:
        self.root = root
        self.settings = settings
        self.conn = db.connect(settings.db_path)
        db.init_schema(self.conn)
        journal.init_journal(self.conn)

        self.selected: dict[str, Any] | None = None
        self.busy = False

        root.title("FET v3 — расписание")
        root.geometry("1360x760")
        root.minsize(1100, 640)

        self.group_var = tk.StringVar()
        self.teacher_var = tk.StringVar()
        self.room_var = tk.StringVar()
        self.from_var = tk.StringVar()
        self.to_var = tk.StringVar()
        self.status_var = tk.StringVar(value=f"База: {settings.db_path}")
        self.actor_var = tk.StringVar(value="сотрудник")

        self._build_filters()
        self._build_table()
        self._build_editor()
        self._build_actions()
        self._load_dictionaries()
        self.refresh()

    # ---------- построение интерфейса ----------

    def _build_filters(self) -> None:
        frame = ttk.LabelFrame(self.root, text="Поиск")
        frame.pack(fill="x", padx=8, pady=(8, 4))

        ttk.Label(frame, text="Группа").grid(row=0, column=0, padx=4, pady=4, sticky="w")
        self.group_box = ttk.Combobox(frame, textvariable=self.group_var, width=18)
        self.group_box.grid(row=0, column=1, padx=4)

        ttk.Label(frame, text="Преподаватель").grid(row=0, column=2, padx=4, sticky="w")
        self.teacher_box = ttk.Combobox(frame, textvariable=self.teacher_var, width=24)
        self.teacher_box.grid(row=0, column=3, padx=4)

        ttk.Label(frame, text="Аудитория").grid(row=0, column=4, padx=4, sticky="w")
        self.room_box = ttk.Combobox(frame, textvariable=self.room_var, width=12)
        self.room_box.grid(row=0, column=5, padx=4)

        ttk.Label(frame, text="С").grid(row=0, column=6, padx=4, sticky="w")
        ttk.Entry(frame, textvariable=self.from_var, width=12).grid(row=0, column=7, padx=4)
        ttk.Label(frame, text="По").grid(row=0, column=8, padx=4, sticky="w")
        ttk.Entry(frame, textvariable=self.to_var, width=12).grid(row=0, column=9, padx=4)

        ttk.Button(frame, text="Найти", command=self.refresh).grid(row=0, column=10, padx=6)
        ttk.Button(frame, text="Сбросить", command=self.reset).grid(row=0, column=11, padx=2)
        ttk.Label(frame, text="Даты — ГГГГ-ММ-ДД").grid(row=0, column=12, padx=8)

    def _build_table(self) -> None:
        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=8, pady=4)

        self.tree = ttk.Treeview(
            frame, columns=[c[0] for c in COLUMNS], show="headings", selectmode="browse"
        )
        for key, title, width in COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    def _build_editor(self) -> None:
        frame = ttk.LabelFrame(self.root, text="Правка занятия")
        frame.pack(fill="x", padx=8, pady=4)

        self.edit_vars: dict[str, tk.StringVar] = {}
        for index, (key, title) in enumerate(EDIT_FIELDS):
            column = (index % 3) * 2
            row = index // 3
            ttk.Label(frame, text=title).grid(row=row, column=column, padx=4, pady=3, sticky="w")
            variable = tk.StringVar()
            self.edit_vars[key] = variable
            width = 40 if key == "subject" else (22 if key == "teacher" else 16)
            ttk.Entry(frame, textvariable=variable, width=width).grid(
                row=row, column=column + 1, padx=4, pady=3, sticky="w"
            )

        ttk.Label(frame, text="Кто правит").grid(row=1, column=4, padx=4, sticky="w")
        ttk.Entry(frame, textvariable=self.actor_var, width=18).grid(
            row=1, column=5, padx=4, sticky="w"
        )

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=6, sticky="w", pady=4)
        ttk.Button(buttons, text="Сохранить правку", command=self.save).pack(side="left", padx=4)
        ttk.Button(buttons, text="Отменить последнюю", command=self.undo).pack(
            side="left", padx=4
        )
        ttk.Button(buttons, text="Пересобрать .fet", command=self.rebuild).pack(
            side="left", padx=4
        )
        ttk.Button(buttons, text="Проверить FET", command=self.validate).pack(side="left", padx=4)

        history_frame = ttk.LabelFrame(self.root, text="Журнал изменений")
        history_frame.pack(fill="both", expand=False, padx=8, pady=(0, 4))
        self.history = tk.Text(history_frame, height=6, wrap="none")
        self.history.pack(fill="both", expand=True, padx=4, pady=4)

    def _build_actions(self) -> None:
        ttk.Label(self.root, textvariable=self.status_var, anchor="w").pack(
            fill="x", padx=8, pady=(0, 6)
        )

    # ---------- данные ----------

    def _load_dictionaries(self) -> None:
        groups: set[str] = set()
        for row in self.conn.execute("SELECT DISTINCT groups_raw FROM lessons"):
            groups.update(split_groups(row["groups_raw"]))
        teachers = [
            row["teacher"]
            for row in self.conn.execute(
                "SELECT teacher, MIN(teacher_key) AS k FROM lessons GROUP BY teacher_key ORDER BY k"
            )
        ]
        rooms = [
            row["room"]
            for row in self.conn.execute(
                "SELECT DISTINCT room FROM lessons WHERE room <> '' ORDER BY room"
            )
        ]
        self.group_box["values"] = sorted(groups)
        self.teacher_box["values"] = teachers
        self.room_box["values"] = rooms

    def refresh(self) -> None:
        try:
            from_date = self._date_or_none(self.from_var.get())
            to_date = self._date_or_none(self.to_var.get())
        except ValueError as exc:
            messagebox.showerror("Дата", str(exc))
            return
        rows = journal.search(
            self.conn,
            group=self.group_var.get() or None,
            teacher=self.teacher_var.get() or None,
            room=self.room_var.get() or None,
            date_from=from_date,
            date_to=to_date,
        )
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    self._display_date(row["iso_date"]),
                    row["pair"],
                    row["groups_raw"],
                    row["subgroup"] or "",
                    row["subject"],
                    row["subject_type"],
                    row["teacher"],
                    row["room"],
                    row["period"],
                    row["source_row"],
                ),
            )
        self._refresh_history()
        self.status_var.set(f"Найдено занятий: {len(rows)}")

    def _refresh_history(self) -> None:
        self.history.configure(state="normal")
        self.history.delete("1.0", "end")
        for row in journal.history(self.conn, limit=20):
            self.history.insert(
                "end",
                f"{row['ts']}  {row['actor']}  {row['action']}  "
                f"{row['period']}#{row['source_row']}  v{row['id']}\n",
            )
        self.history.configure(state="disabled")

    def _on_select(self, _event: object = None) -> None:
        selection = self.tree.selection()
        if not selection:
            self.selected = None
            return
        row = self.conn.execute(
            "SELECT * FROM lessons WHERE id = ?", (int(selection[0]),)
        ).fetchone()
        self.selected = dict(row) if row else None
        if self.selected is None:
            return
        self.edit_vars["iso_date"].set(self.selected["iso_date"])
        self.edit_vars["pair"].set(str(self.selected["pair"]))
        self.edit_vars["room"].set(self.selected["room"])
        self.edit_vars["teacher"].set(self.selected["teacher"])
        self.edit_vars["subject"].set(self.selected["subject"])

    # ---------- действия ----------

    def save(self) -> None:
        if self.selected is None:
            messagebox.showinfo("Правка", "Сначала выберите занятие в таблице.")
            return
        changes: dict[str, Any] = {}
        for key, _ in EDIT_FIELDS:
            value = self.edit_vars[key].get().strip()
            current = self.selected[key]
            if key == "pair":
                if value and int(value) != current:
                    changes[key] = value
            elif value != str(current):
                changes[key] = value
        try:
            result = journal.apply_update(
                self.conn,
                period=self.selected["period"],
                source_row=int(self.selected["source_row"]),
                changes=changes,
                actor=self.actor_var.get().strip() or "сотрудник",
            )
        except journal.JournalError as exc:
            messagebox.showerror("Правка", str(exc))
            return
        self.status_var.set(
            f"Правка сохранена (v{result['version']}). Не забудьте пересобрать .fet."
        )
        self.refresh()

    def undo(self) -> None:
        try:
            result = journal.undo_last(
                self.conn, actor=self.actor_var.get().strip() or "сотрудник"
            )
        except journal.JournalError as exc:
            messagebox.showinfo("Откат", str(exc))
            return
        self.status_var.set(
            f"Откат выполнен: {result['period']}#{result['source_row']} (v{result['version']})"
        )
        self.refresh()

    def rebuild(self) -> None:
        self._run_cli(["build"], "Сборка")

    def validate(self) -> None:
        self._run_cli(["validate"], "Проверка FET")

    def _run_cli(self, arguments: list[str], label: str) -> None:
        if self.busy:
            messagebox.showinfo(label, "Дождитесь завершения текущей операции.")
            return
        self.busy = True
        self.status_var.set(f"{label}: выполняется…")

        def worker() -> None:
            command = [sys.executable, "-X", "utf8", str(PROJECT_ROOT / "run.py"), *arguments]
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                text = (completed.stdout or "").strip().splitlines()
                tail = text[-1] if text else ""
                self.root.after(
                    0,
                    lambda: self._cli_done(label, completed.returncode, tail),
                )
            except OSError as exc:
                message = f"Не удалось запустить: {exc}"
                self.root.after(
                    0, lambda: self._cli_done(label, 1, message)
                )

        threading.Thread(target=worker, daemon=True).start()

    def _cli_done(self, label: str, code: int, tail: str) -> None:
        self.busy = False
        status = "OK" if code == 0 else f"код {code}"
        self.status_var.set(f"{label}: {status}. {tail}")
        if code != 0:
            messagebox.showwarning(label, f"Команда завершилась с кодом {code}.\n{tail}")

    def reset(self) -> None:
        for variable in (self.group_var, self.teacher_var, self.room_var, self.from_var, self.to_var):
            variable.set("")
        self.refresh()

    # ---------- утилиты ----------

    @staticmethod
    def _display_date(iso_date: str) -> str:
        try:
            return dt.date.fromisoformat(iso_date).strftime("%d.%m.%Y")
        except ValueError:
            return iso_date

    @staticmethod
    def _date_or_none(value: str) -> str | None:
        value = value.strip()
        if not value:
            return None
        try:
            return dt.date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError(f"Ожидается дата ГГГГ-ММ-ДД, получено: {value}") from exc

    def close(self) -> None:
        self.conn.close()
        self.root.destroy()


def main(settings: Settings, runner: Callable[[], None] | None = None) -> int:
    root = tk.Tk()
    app = App(root, settings)
    root.protocol("WM_DELETE_WINDOW", app.close)
    if runner is not None:
        runner()
    else:
        root.mainloop()
    return 0
