"""Print APP_VERSION from backend/app_meta.py. Used by pack scripts and CI."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_META = ROOT / "backend" / "app_meta.py"
_PATTERN = re.compile(r'APP_VERSION\s*=\s*"([^"]+)"')


def app_version() -> str:
    text = _META.read_text(encoding="utf-8")
    match = _PATTERN.search(text)
    if not match:
        raise SystemExit(f"APP_VERSION not found in {_META}")
    return match.group(1)


if __name__ == "__main__":
    print(app_version())
