"""Product names, version, and user-data paths for 图译 / Tuyi."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME_ZH = "图译"
APP_NAME_EN = "Tuyi"
APP_TITLE = "图译"
APP_TAGLINE = "DWG / DXF Translator"
APP_VERSION = "0.1.2"
APP_PUBLISHER = "Eric Tan"
GITHUB_OWNER = "erict16"
GITHUB_REPO = "tuyi"
GITHUB_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"

CONFIG_PATH = Path.home() / ".tuyi_config.json"
QUEUE_PATH = Path.home() / ".tuyi_queue.json"
ASSETS_PATH = Path.home() / ".tuyi_language_assets.sqlite3"
OUTPUT_DIR_NAME = "Tuyi output"

# One-time copy from the Dwglot filenames, then from Honsen.
PREVIOUS_CONFIG_PATH = Path.home() / ".dwglot_config.json"
PREVIOUS_QUEUE_PATH = Path.home() / ".dwglot_queue.json"
PREVIOUS_ASSETS_PATH = Path.home() / ".dwglot_language_assets.sqlite3"
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


def migrate_user_data() -> None:
    migrate_legacy_file(PREVIOUS_CONFIG_PATH, CONFIG_PATH)
    migrate_legacy_file(LEGACY_CONFIG_PATH, CONFIG_PATH)
    migrate_legacy_file(PREVIOUS_QUEUE_PATH, QUEUE_PATH)
    migrate_legacy_file(LEGACY_QUEUE_PATH, QUEUE_PATH)
    migrate_legacy_file(PREVIOUS_ASSETS_PATH, ASSETS_PATH)
    migrate_legacy_file(LEGACY_ASSETS_PATH, ASSETS_PATH)


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
