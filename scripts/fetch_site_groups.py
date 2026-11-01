"""Обновляет снимок групп сайта iubip.ru для сопоставления «группа → академия».

Использование:
    python scripts/fetch_site_groups.py [путь]

Сайт закрыт JS-cookie `beget=begetok`, поэтому запрос идёт с браузерными
заголовками. Сеть нужна только при обновлении справочника; сама панель
работает офлайн по сохранённому файлу.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://www.iubip.ru/local/templates/univer/include/schedule/ajax/read-file-groups.php"
DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "site_groups.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.iubip.ru/schedule/",
    "Cookie": "beget=begetok",
    "X-Requested-With": "XMLHttpRequest",
}


def fetch_groups() -> dict[str, dict[str, int]]:
    data = urllib.parse.urlencode({"do": "groups"}).encode("utf-8")
    request = urllib.request.Request(BASE, data=data, headers=HEADERS, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    if not isinstance(payload, dict) or not payload:
        raise RuntimeError("Сайт вернул неожиданный ответ")
    return payload


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    payload = fetch_groups()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(len(items) for items in payload.values())
    print(f"Сохранено: {target}")
    print(f"Академий: {len(payload)}, групп: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
