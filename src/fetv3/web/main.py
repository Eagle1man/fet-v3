"""Точка входа для uvicorn: `fetv3.web.main:app`.

Настройки читаются из окружения (в Docker) или из `.env` (на ВМ).
"""

from __future__ import annotations

import os

from ..config import get_settings
from .app import create_app

app = create_app(get_settings(os.environ.get("FETV3_ENV", ".env")))
