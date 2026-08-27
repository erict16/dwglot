"""GitHub Releases auto-update check. No telemetry; user-triggered."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from backend.app_meta import APP_VERSION, GITHUB_URL

RELEASES_API = "https://api.github.com/repos/erict16/dwglot/releases/latest"
APPCAST_URL = "https://github.com/erict16/dwglot/releases/latest/download/appcast.xml"
LATEST_YML_URL = "https://github.com/erict16/dwglot/releases/latest/download/latest.yml"


def _parse_version(value: str) -> tuple[int, ...]:
    parts = []
    for chunk in (value or "").lstrip("v").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts or (0,))


def is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def check_github_release(timeout: float = 12.0) -> dict:
    """Return current vs latest. 404 means no release yet, not a crash."""
    request = urllib.request.Request(
        RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Dwglot/{APP_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in {403, 404}:
            return {
                "current": APP_VERSION,
                "latest": APP_VERSION,
                "available": False,
                "message": "还没有 GitHub Release" if exc.code == 404 else "GitHub API 暂不可用，打开 Releases 页查看",
                "html_url": f"{GITHUB_URL}/releases",
                "appcast_url": APPCAST_URL,
                "latest_yml_url": LATEST_YML_URL,
                "assets": [],
            }
        return {
            "current": APP_VERSION,
            "latest": APP_VERSION,
            "available": False,
            "message": f"检查更新失败（HTTP {exc.code}）",
            "html_url": f"{GITHUB_URL}/releases",
            "appcast_url": APPCAST_URL,
            "latest_yml_url": LATEST_YML_URL,
            "assets": [],
        }
    except OSError as exc:
        return {
            "current": APP_VERSION,
            "latest": APP_VERSION,
            "available": False,
            "message": f"检查更新失败: {exc}",
            "html_url": f"{GITHUB_URL}/releases",
            "appcast_url": APPCAST_URL,
            "latest_yml_url": LATEST_YML_URL,
            "assets": [],
        }

    tag = str(payload.get("tag_name") or "").lstrip("v")
    assets = [
        {
            "name": item.get("name") or "",
            "url": item.get("browser_download_url") or "",
        }
        for item in payload.get("assets") or []
        if item.get("browser_download_url")
    ]
    return {
        "current": APP_VERSION,
        "latest": tag or APP_VERSION,
        "available": bool(tag) and is_newer(tag, APP_VERSION),
        "message": "",
        "html_url": payload.get("html_url") or f"{GITHUB_URL}/releases",
        "notes": payload.get("body") or "",
        "assets": assets,
        "appcast_url": APPCAST_URL,
        "latest_yml_url": LATEST_YML_URL,
    }
