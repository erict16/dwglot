#!/usr/bin/env python3
"""Build Sparkle appcast.xml and WinSparkle latest.yml from a release tag.

Usage:
  APP_VERSION=0.1.2 DMG=dist/Tuyi.dmg EXE=dist/Tuyi.exe python tools/gen_appcast.py

Unsigned first. Sign the Sparkle EdDSA key / Authenticode later.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app_meta import APP_VERSION, GITHUB_URL  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    version = os.environ.get("APP_VERSION") or APP_VERSION
    tag = os.environ.get("RELEASE_TAG") or f"v{version}"
    dmg = Path(os.environ["DMG"]) if os.environ.get("DMG") else None
    exe = Path(os.environ["EXE"]) if os.environ.get("EXE") else None
    out = Path(os.environ.get("OUT") or "dist")
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    html = f"{GITHUB_URL}/releases/tag/{tag}"

    items = []
    if dmg and dmg.is_file():
        url = f"{GITHUB_URL}/releases/download/{tag}/{dmg.name}"
        items.append(
            f"""    <item>
      <title>图译 {version}</title>
      <pubDate>{now}</pubDate>
      <link>{html}</link>
      <sparkle:version>{version}</sparkle:version>
      <sparkle:shortVersionString>{version}</sparkle:shortVersionString>
      <enclosure url="{url}" length="{dmg.stat().st_size}" type="application/octet-stream"/>
    </item>"""
        )
    appcast = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <title>图译 Tuyi</title>
    <link>{GITHUB_URL}/releases</link>
    <description>GitHub Releases appcast</description>
{chr(10).join(items)}
  </channel>
</rss>
"""
    (out / "appcast.xml").write_text(appcast, encoding="utf-8")

    if exe and exe.is_file():
        yml = (
            f"version: {version}\n"
            f"path: {exe.name}\n"
            f"sha256: {_sha256(exe)}\n"
            f"releaseUrl: {html}\n"
        )
        (out / "latest.yml").write_text(yml, encoding="utf-8")
    print(f"wrote {out / 'appcast.xml'}")


if __name__ == "__main__":
    main()
