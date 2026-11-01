"""Запуск без установки пакета: python run.py <команда>."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from fetv3.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
