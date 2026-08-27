"""Rewrite SHX / Big Font styles to a Unicode TTF so CJK is not ???."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

CJK_RE = __import__("re").compile(r"[\u3400-\u9fff]")

SHX_NAMES = {
    "txt",
    "simplex",
    "complex",
    "italic",
    "gothice",
    "gothici",
    "gothice",
    "gbcbig",
    "chineset",
    "bigfont",
    "extfont",
}

BUNDLED_FONT_CANDIDATES = (
    "NotoSansSC-Regular.otf",
    "NotoSansSC-Regular.ttf",
    "SourceHanSansSC-Regular.otf",
)


def looks_like_shx(name: str) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return True
    stem = Path(n).stem.lower()
    return n.endswith(".shx") or stem in SHX_NAMES


def bundled_font_path() -> Path | None:
    from backend.app_meta import resource_base

    fonts_dir = resource_base() / "fonts"
    for name in BUNDLED_FONT_CANDIDATES:
        path = fonts_dir / name
        if path.is_file():
            return path
    return None


def _user_fonts_dir() -> Path | None:
    home = Path.home()
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "Microsoft" / "Windows" / "Fonts"
        return home / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts"
    if sys.platform == "darwin":
        return home / "Library" / "Fonts"
    return home / ".local" / "share" / "fonts"


def install_bundled_font() -> Path | None:
    source = bundled_font_path()
    if source is None:
        return None
    dest_dir = _user_fonts_dir()
    if dest_dir is None:
        return source
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / source.name
    if not dest.exists():
        try:
            shutil.copy2(source, dest)
        except OSError:
            return source
    return dest


def unicode_font_filename() -> str:
    installed = install_bundled_font()
    if installed is not None:
        return installed.name
    if sys.platform == "win32":
        return "msyh.ttc"
    if sys.platform == "darwin":
        return "Songti.ttc"
    return "NotoSansSC-Regular.otf"


def rewrite_shx_styles(doc, log=None) -> int:
    """Point SHX / Big Font styles at a Unicode font. Returns how many styles changed."""
    font = unicode_font_filename()
    changed = 0
    for style in doc.styles:
        current = getattr(style.dxf, "font", "") or ""
        big = getattr(style.dxf, "bigfont", "") or ""
        if not (looks_like_shx(current) or looks_like_shx(big)):
            continue
        style.dxf.font = font
        if hasattr(style.dxf, "bigfont"):
            try:
                style.dxf.bigfont = ""
            except Exception:
                pass
        changed += 1
        if log:
            log(f"STYLE {getattr(style.dxf, 'name', '?')}: {current or 'SHX'} → {font}")
    return changed
