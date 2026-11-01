"""Настройки проекта: только через .env, никаких литералов путей в коде."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Сетка пар колледжа. Порядок важен: индекс = номер пары.
DEFAULT_HOURS: tuple[str, ...] = (
    "08:20",
    "10:00",
    "11:40",
    "13:30",
    "15:10",
    "17:00",
    "18:40",
    "20:20",
)

DEFAULT_SCHOOL = "Южный университет (ИУБиП)"


def load_env(path: Path) -> None:
    """Читает KEY=VALUE, не перетирая уже заданные переменные окружения."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    avtor_out: Path
    db_path: Path
    out_dir: Path
    docs_dir: Path
    fet_cl: Path | None
    school: str
    hours: tuple[str, ...]
    fet_time_limit: int
    constraints: Path | None
    room_capacity: int

    @property
    def fet_dir(self) -> Path:
        return self.out_dir / "fet"

    @property
    def validation_dir(self) -> Path:
        return self.out_dir / "validation"

    @property
    def conflicts_dir(self) -> Path:
        return self.out_dir / "conflicts"


def _path(value: str | None, default: Path) -> Path:
    if value is None or not value.strip():
        return default
    return Path(value.strip())


def get_settings(env_file: str | Path | None = ".env") -> Settings:
    if env_file is not None:
        candidate = Path(env_file)
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        load_env(candidate)

    data_dir = PROJECT_ROOT / "data"
    out_dir = _path(os.environ.get("FETV3_OUT"), PROJECT_ROOT / "out")
    constraints_raw = os.environ.get("FETV3_CONSTRAINTS", "").strip()

    return Settings(
        avtor_out=_path(
            os.environ.get("AVTOR_OUT"),
            Path(r"D:\AVTOR_CLEAN_20260917_153823\OUT"),
        ),
        db_path=_path(os.environ.get("FETV3_DB"), data_dir / "schedule.db"),
        out_dir=out_dir,
        docs_dir=PROJECT_ROOT / "docs",
        fet_cl=_optional_path(os.environ.get("FET_CL")),
        school=os.environ.get("FETV3_SCHOOL", DEFAULT_SCHOOL).strip() or DEFAULT_SCHOOL,
        hours=DEFAULT_HOURS,
        fet_time_limit=int(os.environ.get("FET_CL_TIMELIMIT", "2") or "2"),
        constraints=Path(constraints_raw) if constraints_raw else None,
        room_capacity=int(os.environ.get("FETV3_ROOM_CAPACITY", "999") or "999"),
    )


def _optional_path(value: str | None) -> Path | None:
    if value is None or not value.strip():
        return None
    path = Path(value.strip())
    return path if path.exists() else None
