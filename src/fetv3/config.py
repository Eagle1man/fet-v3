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
    web_password: str = ""
    web_secret: str = "change-me"
    public_read: bool = False
    site_faculty: str = "Колледж"
    site_groups: Path | None = None
    ftp_host: str = ""
    ftp_user: str = ""
    ftp_password: str = ""
    ftp_dir: str = ""
    cors_origins: tuple[str, ...] = ()

    @property
    def fet_dir(self) -> Path:
        return self.out_dir / "fet"

    @property
    def validation_dir(self) -> Path:
        return self.out_dir / "validation"

    @property
    def conflicts_dir(self) -> Path:
        return self.out_dir / "conflicts"

    @property
    def site_dir(self) -> Path:
        return self.out_dir / "site"


def _path(value: str | None, default: Path) -> Path:
    if value is None or not value.strip():
        return default
    path = Path(value.strip())
    return path if path.is_absolute() else PROJECT_ROOT / path


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
        web_password=os.environ.get("FETV3_WEB_PASSWORD", "").strip(),
        web_secret=os.environ.get("FETV3_WEB_SECRET", "change-me").strip() or "change-me",
        public_read=_bool_env(os.environ.get("FETV3_PUBLIC_READ"), default=False),
        site_faculty=os.environ.get("FETV3_SITE_FACULTY", "Колледж").strip() or "Колледж",
        site_groups=_optional_path(os.environ.get("FETV3_SITE_GROUPS")),
        ftp_host=os.environ.get("FETV3_FTP_HOST", "").strip(),
        ftp_user=os.environ.get("FETV3_FTP_USER", "").strip(),
        ftp_password=os.environ.get("FETV3_FTP_PASSWORD", "").strip(),
        ftp_dir=os.environ.get("FETV3_FTP_DIR", "").strip(),
        cors_origins=tuple(
            item.strip()
            for item in os.environ.get("FETV3_CORS_ORIGINS", "").split(",")
            if item.strip()
        ),
    )


def _bool_env(value: str | None, *, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _optional_path(value: str | None) -> Path | None:
    if value is None or not value.strip():
        return None
    path = Path(value.strip())
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path if path.exists() else None
