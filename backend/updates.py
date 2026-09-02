"""GitHub Releases update check + in-place zip apply. No Setup.exe, no telemetry."""

from __future__ import annotations

import hashlib
import json
import os
import platform as py_platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from backend.app_meta import APP_VERSION, GITHUB_OWNER, GITHUB_REPO, GITHUB_URL

RELEASES_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
APPCAST_URL = f"{GITHUB_URL}/releases/latest/download/appcast.xml"
LATEST_YML_URL = f"{GITHUB_URL}/releases/latest/download/latest.yml"
MAX_UPDATE_BYTES = 800 * 1024 * 1024
WINDOWS_ZIP = "Tuyi_v{version}_windows_x64.zip"
MAC_ZIP = "Tuyi_v{version}_macOS_{arch}.zip"

_state_lock = threading.Lock()
_state = {
    "phase": "idle",
    "percent": 0.0,
    "message": "",
    "latest": "",
    "restarting": False,
}


class ApplyError(Exception):
    """User-facing apply failure. Message is Chinese, no traceback."""


def _parse_version(value: str) -> tuple[int, ...]:
    parts = []
    for chunk in (value or "").lstrip("v").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts or (0,))


def is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", None))


def current_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "other"


def current_arch() -> str:
    machine = (py_platform.machine() or "").lower()
    if machine in {"amd64", "x86_64"}:
        return "x86_64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return machine or "x86_64"


def install_dir() -> Path | None:
    if not is_frozen():
        return None
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        if exe.parent.name == "MacOS" and len(exe.parents) >= 2 and exe.parents[1].name == "Contents":
            return exe.parents[2]
        return exe.parent
    return exe.parent


def launch_path(dest: Path) -> Path:
    if dest.suffix.lower() == ".app" or dest.name.endswith(".app"):
        return dest
    return dest / "Tuyi.exe"


def unavailable_payload(message: str) -> dict:
    return {
        "current": APP_VERSION,
        "latest": APP_VERSION,
        "available": False,
        "can_apply": False,
        "message": message,
        "html_url": f"{GITHUB_URL}/releases",
        "appcast_url": APPCAST_URL,
        "latest_yml_url": LATEST_YML_URL,
        "assets": [],
        "asset": None,
    }


def is_allowed_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if host in {"github.com", "www.github.com"}:
        return path.startswith(f"/{GITHUB_OWNER}/{GITHUB_REPO}/")
    return False


def asset_sha256(asset: dict) -> str:
    digest = str(asset.get("digest") or "")
    if digest.lower().startswith("sha256:"):
        return digest.split(":", 1)[1].strip().lower()
    return str(asset.get("sha256") or "").strip().lower()


def zip_name_for(version: str, plat: str, arch: str) -> str:
    if plat == "windows":
        return WINDOWS_ZIP.format(version=version)
    return MAC_ZIP.format(version=version, arch=arch)


def _is_platform_zip(name: str, plat: str, arch: str) -> bool:
    lower = (name or "").lower()
    if not lower.endswith(".zip"):
        return False
    if plat == "windows":
        return lower.endswith("_windows_x64.zip") and lower.startswith("tuyi_v")
    if plat == "macos":
        return lower.startswith("tuyi_v") and "_macos_" in lower and arch.lower() in lower
    return False


def pick_update_asset(assets: list[dict], plat: str, arch: str, version: str) -> dict | None:
    if plat not in {"windows", "macos"}:
        return None
    wanted = zip_name_for(version, plat, arch).lower()
    named = []
    for item in assets or []:
        name = str(item.get("name") or "")
        url = str(item.get("url") or "")
        if not name or not url:
            continue
        if not is_allowed_url(url):
            continue
        named.append(item)
    for item in named:
        if str(item.get("name") or "").lower() == wanted:
            return item
    for item in named:
        if _is_platform_zip(str(item.get("name") or ""), plat, arch):
            return item
    return None


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
            "sha256": asset_sha256(item),
            "size": int(item.get("size") or 0),
        }
        for item in payload.get("assets") or []
        if item.get("browser_download_url")
    ]
    available = bool(tag) and is_newer(tag, APP_VERSION)
    asset = pick_update_asset(assets, current_platform(), current_arch(), tag) if available else None
    can_apply = bool(available and asset and is_frozen() and current_platform() in {"windows", "macos"})
    return {
        "current": APP_VERSION,
        "latest": tag or APP_VERSION,
        "available": available,
        "can_apply": can_apply,
        "message": "",
        "html_url": payload.get("html_url") or f"{GITHUB_URL}/releases",
        "notes": payload.get("body") or "",
        "assets": assets,
        "asset": asset,
        "appcast_url": APPCAST_URL,
        "latest_yml_url": LATEST_YML_URL,
    }


def update_status() -> dict:
    with _state_lock:
        return dict(_state)


def _set_state(**kwargs) -> None:
    with _state_lock:
        _state.update(kwargs)


def _safe_extract(archive: Path, dest: Path) -> None:
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or name.startswith("\\") or ".." in Path(name).parts:
                raise ApplyError("更新包路径不合法")
            target = (dest / name).resolve()
            if dest != target and not str(target).startswith(str(dest) + os.sep):
                raise ApplyError("更新包路径不合法")
        zf.extractall(dest)


def resolve_payload(extracted: Path) -> tuple[str, Path]:
    extracted = Path(extracted)
    windows_root = extracted / "Tuyi.exe"
    if windows_root.is_file() and (extracted / "_internal").is_dir():
        return "windows", extracted
    nested = extracted / "Tuyi"
    if (nested / "Tuyi.exe").is_file() and (nested / "_internal").is_dir():
        return "windows", nested
    app = extracted / "Tuyi.app"
    if (app / "Contents" / "MacOS" / "Tuyi").exists():
        return "macos", app
    if (extracted / "Contents" / "MacOS" / "Tuyi").exists():
        return "macos", extracted
    raise ApplyError("更新包内容不对")


def copy_payload(staging: Path, dest: Path) -> None:
    staging = Path(staging)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if staging.is_file():
        shutil.copy2(staging, dest / staging.name)
        return
    for item in staging.iterdir():
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def windows_helper_text() -> str:
    return """param(
  [Parameter(Mandatory=$true)][int]$WaitPid,
  [Parameter(Mandatory=$true)][string]$Staging,
  [Parameter(Mandatory=$true)][string]$Dest,
  [Parameter(Mandatory=$true)][string]$Exe
)
while (Get-Process -Id $WaitPid -ErrorAction SilentlyContinue) {
  Start-Sleep -Milliseconds 400
}
Start-Sleep -Milliseconds 800
$robocopy = Join-Path $env:SystemRoot "System32\\robocopy.exe"
& $robocopy $Staging $Dest /E /IS /IT /R:3 /W:1 /NFL /NDL /NJH /NJS /NC /NS
if ($LASTEXITCODE -ge 8) { exit $LASTEXITCODE }
if (Test-Path -LiteralPath $Exe) {
  Start-Process -FilePath $Exe
}
"""


def macos_helper_text() -> str:
    return """#!/bin/bash
WaitPid="$1"
Staging="$2"
Dest="$3"
while kill -0 "$WaitPid" 2>/dev/null; do sleep 0.4; done
sleep 0.8
rm -rf "$Dest"
mkdir -p "$(dirname "$Dest")"
ditto "$Staging" "$Dest"
open "$Dest"
"""


def write_helper_script(directory: Path, plat: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    if plat == "windows":
        path = directory / "tuyi-apply.ps1"
        path.write_text(windows_helper_text(), encoding="utf-8")
        return path
    path = directory / "tuyi-apply.sh"
    path.write_text(macos_helper_text(), encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o111)
    return path


def install_dir_writable(path: Path) -> bool:
    probe = Path(path) / ".tuyi-write-test"
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def download_file(url: str, dest: Path, expected_sha: str = "", on_progress=None) -> str:
    if not is_allowed_url(url):
        raise ApplyError("更新地址无效")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"Tuyi/{APP_VERSION}",
            "Accept": "application/octet-stream",
        },
    )
    hasher = hashlib.sha256()
    done = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            if total > MAX_UPDATE_BYTES:
                raise ApplyError("更新包太大")
            with dest.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > MAX_UPDATE_BYTES:
                        raise ApplyError("更新包太大")
                    handle.write(chunk)
                    hasher.update(chunk)
                    if total and on_progress:
                        on_progress(min(done / total, 1.0))
    except ApplyError:
        dest.unlink(missing_ok=True)
        raise
    except (OSError, urllib.error.URLError, ValueError, TypeError):
        dest.unlink(missing_ok=True)
        raise ApplyError("下载更新失败")
    digest = hasher.hexdigest().lower()
    if expected_sha and digest != expected_sha.lower():
        dest.unlink(missing_ok=True)
        raise ApplyError("更新包校验失败")
    if on_progress:
        on_progress(1.0)
    return digest


def spawn_helper(script: Path, args: list[str], plat: str, elevate: bool = False) -> None:
    if plat == "windows":
        powershell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        ps_args = [
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *args,
        ]
        if elevate:
            import ctypes

            params = subprocess.list2cmdline(ps_args)
            rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", powershell, params, None, 0)
            if rc <= 32:
                raise ApplyError("未获得管理员权限，无法写入安装目录")
            return
        flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
        subprocess.Popen(
            [powershell, *ps_args],
            close_fds=False,
            creationflags=flags,
        )
        return
    subprocess.Popen(
        ["bash", str(script), *args],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def start_apply() -> dict:
    if not is_frozen():
        raise ApplyError("当前是源码运行，请安装后再使用应用内更新")
    if current_platform() not in {"windows", "macos"}:
        raise ApplyError("当前系统不支持应用内更新")
    dest = install_dir()
    if dest is None:
        raise ApplyError("找不到安装目录")
    with _state_lock:
        if _state["phase"] in {"checking", "downloading", "verifying", "applying"}:
            return {"ok": True, "started": True}
        _state.update(phase="checking", percent=0.0, message="正在检查…", latest="", restarting=False)
    thread = threading.Thread(target=_apply_worker, daemon=True)
    thread.start()
    return {"ok": True, "started": True}


def _apply_worker() -> None:
    archive = None
    extract_dir = None
    try:
        info = check_github_release()
        if not info.get("available"):
            raise ApplyError(info.get("message") or "已是最新版本")
        asset = info.get("asset") or {}
        url = str(asset.get("url") or "")
        if not url:
            raise ApplyError("这个版本没有应用内更新包，请打开 Releases 下载安装包")
        plat = current_platform()
        dest = install_dir()
        if dest is None:
            raise ApplyError("找不到安装目录")
        _set_state(phase="downloading", percent=0.0, message="正在下载更新…", latest=info.get("latest") or "")
        work = Path(tempfile.mkdtemp(prefix="tuyi-update-"))
        archive = work / str(asset.get("name") or "tuyi-update.zip")
        download_file(
            url,
            archive,
            expected_sha=str(asset.get("sha256") or ""),
            on_progress=lambda value: _set_state(phase="downloading", percent=value, message="正在下载更新…"),
        )
        _set_state(phase="verifying", percent=1.0, message="正在校验…")
        extract_dir = work / "extracted"
        _safe_extract(archive, extract_dir)
        kind, payload = resolve_payload(extract_dir)
        if kind != plat:
            raise ApplyError("更新包和当前系统不匹配")
        helper = write_helper_script(work, plat)
        exe = launch_path(dest)
        _set_state(phase="applying", message="准备重启…")
        elevate = plat == "windows" and not install_dir_writable(dest)
        spawn_helper(
            helper,
            [str(os.getpid()), str(payload), str(dest), str(exe)],
            plat,
            elevate=elevate,
        )
        time.sleep(0.2)
        _set_state(phase="restarting", percent=1.0, message="正在重启…", restarting=True)
    except ApplyError as exc:
        _set_state(phase="error", message=str(exc), restarting=False)
        if archive:
            shutil.rmtree(archive.parent, ignore_errors=True)
    except Exception:
        _set_state(phase="error", message="更新失败", restarting=False)
        if archive:
            shutil.rmtree(archive.parent, ignore_errors=True)
