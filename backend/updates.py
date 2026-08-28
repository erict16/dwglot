"""GitHub Releases auto-update check. No telemetry; user-triggered."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from backend.app_meta import APP_VERSION, GITHUB_OWNER, GITHUB_REPO, GITHUB_URL

RELEASES_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
APPCAST_URL = f"{GITHUB_URL}/releases/latest/download/appcast.xml"
LATEST_YML_URL = f"{GITHUB_URL}/releases/latest/download/latest.yml"


def _parse_version(value: str) -> tuple[int, ...]:
    parts = []
    for chunk in (value or "").lstrip("v").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts or (0,))


def is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def unavailable_payload(message: str) -> dict:
    return {
        "current": APP_VERSION,
        "latest": APP_VERSION,
        "available": False,
        "message": message,
        "html_url": f"{GITHUB_URL}/releases",
        "appcast_url": APPCAST_URL,
        "latest_yml_url": LATEST_YML_URL,
        "assets": [],
    }


def check_github_release(timeout: float = 12.0) -> dict:
    """Return current vs latest. 403/404 are a calm payload, not a crash."""
    request = urllib.request.Request(
        RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Tuyi/{APP_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return unavailable_payload("还没有 GitHub Release")
        if exc.code == 403:
            return unavailable_payload("GitHub API 暂不可用，打开 Releases 页查看")
        return unavailable_payload(f"检查更新失败（HTTP {exc.code}）")
    except (OSError, json.JSONDecodeError, ValueError, TypeError, UnicodeDecodeError):
        return unavailable_payload("GitHub API 暂不可用，打开 Releases 页查看")

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
