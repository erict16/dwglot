"""Product names, version, and user-data paths for 图译 / Dwglot."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME_ZH = "图译"
APP_NAME_EN = "Dwglot"
APP_TITLE = "图译 Dwglot"
APP_VERSION = "0.1.1"
APP_PUBLISHER = "Eric Tan"
GITHUB_URL = "https://github.com/erict16/dwglot"

CONFIG_PATH = Path.home() / ".dwglot_config.json"
QUEUE_PATH = Path.home() / ".dwglot_queue.json"
ASSETS_PATH = Path.home() / ".dwglot_language_assets.sqlite3"
OUTPUT_DIR_NAME = "Dwglot output"

# One-time read of Honsen filenames if a user upgrades a previous install.
LEGACY_CONFIG_PATH = Path.home() / ".cad_translator_config.json"
LEGACY_QUEUE_PATH = Path.home() / ".cad_translator_queue.json"
LEGACY_ASSETS_PATH = Path.home() / ".cad_translator_language_assets.sqlite3"


def migrate_legacy_file(legacy: Path, current: Path) -> None:
    if current.exists() or not legacy.exists():
        return
    try:
        current.write_bytes(legacy.read_bytes())
    except OSError:
        pass


def default_output_dir() -> str:
    path = Path.home() / "Documents" / OUTPUT_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def resource_base() -> Path:
    import sys

    if getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def resource_path(relative: str) -> str:
    return str(resource_base() / relative)


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}
