"""Offline-verifiable signed licences with network-synchronised time."""

from __future__ import annotations

import base64
import json
import sys
import threading
import urllib.request
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from backend.storage import atomic_write_json, quarantine_corrupt_file

try:
    import winreg
except ImportError:
    winreg = None

TRIAL_DAYS = 30
TIME_REFRESH_SECONDS = 300
LICENSE_PATH = Path.home() / ".cad_translator_license.json"
TIME_SOURCES = ("https://www.microsoft.com", "https://www.cloudflare.com/cdn-cgi/trace")
# Set True only in a separately licensed build. False skips every licence check.
LICENSE_ENFORCEMENT_ENABLED = False
# Fixed R2/custom-domain object URLs, e.g. https://assets.example.com/honsen-cad/wechat.png.
# Upload a replacement under the same key; leave blank to hide its QR code.
SUPPORT_WECHAT_QR_URL = "https://raw.giteeusercontent.com/etianwang/qrcode/raw/main/qr_wx.jpg"
SUPPORT_ALIPAY_QR_URL = "https://raw.giteeusercontent.com/etianwang/qrcode/raw/main/qr_ali.jpg"


def _resource_path(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / name


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _network_now() -> datetime:
    for url in TIME_SOURCES:
        try:
            request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "HonsenCADTranslator/1"})
            with urllib.request.urlopen(request, timeout=5) as response:
                header = response.headers.get("Date")
            if header:
                return parsedate_to_datetime(header).astimezone(timezone.utc)
        except Exception:
            continue
    raise RuntimeError("无法联网校时，请连接网络后重试")


class LicenseManager:
    def __init__(self, path: Path = LICENSE_PATH, public_key_path: Path | None = None):
        self.path = path
        self.public_key_path = public_key_path or _resource_path("license_public_key.txt")
        self._lock = threading.Lock()
        self._cached_now: datetime | None = None
        self._cached_at: datetime | None = None

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except (OSError, ValueError, TypeError):
            quarantine_corrupt_file(self.path)
            return {}

    def _registry_state(self) -> dict:
        if winreg is None or self.path != LICENSE_PATH:
            return {}
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Honsen\CADTranslator\License") as key:
                return {name: winreg.QueryValueEx(key, name)[0] for name in ("first_seen", "last_seen")}
        except OSError:
            return {}

    def _save_registry(self, first_seen: str, last_seen: str) -> None:
        if winreg is not None and self.path == LICENSE_PATH:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Honsen\CADTranslator\License") as key:
                winreg.SetValueEx(key, "first_seen", 0, winreg.REG_SZ, first_seen)
                winreg.SetValueEx(key, "last_seen", 0, winreg.REG_SZ, last_seen)

    def _now(self) -> datetime:
        now = datetime.now(timezone.utc)
        if self._cached_now and self._cached_at and (now - self._cached_at).total_seconds() < TIME_REFRESH_SECONDS:
            return self._cached_now
        self._cached_now = _network_now()
        self._cached_at = now
        return self._cached_now

    def _decode(self, code: str) -> dict:
        payload, signature = code.strip().split(".", 1)
        public_key = _b64decode(self.public_key_path.read_text(encoding="utf-8").strip())
        Ed25519PublicKey.from_public_bytes(public_key).verify(_b64decode(signature), payload.encode())
        decoded = json.loads(_b64decode(payload))
        expiry = date.fromisoformat(decoded["expires_on"])
        if decoded.get("v") != 1:
            raise ValueError("unsupported licence version")
        return {"expires_on": expiry.isoformat(), "plan": str(decoded.get("plan", "授权"))}

    def status(self) -> dict:
        if not LICENSE_ENFORCEMENT_ENABLED:
            return {"usable": True, "state": "disabled", "message": "授权功能未启用"}
        with self._lock:
            try:
                now = self._now()
            except RuntimeError as exc:
                return {"usable": False, "state": "time_error", "message": str(exc)}
            state = self._load()
            registry = self._registry_state()
            first_candidates = [value for value in (state.get("first_seen"), registry.get("first_seen")) if value]
            first_seen = min(first_candidates) if first_candidates else now.date().isoformat()
            last_candidates = [value for value in (state.get("last_seen"), registry.get("last_seen")) if value]
            if last_candidates and now.date() < date.fromisoformat(max(last_candidates)):
                return {"usable": False, "state": "clock_error", "message": "检测到系统时间回退，请联网后重试"}
            result = {"usable": False, "state": "trial", "message": "试用期已结束，请输入激活码", "expires_on": (date.fromisoformat(first_seen) + timedelta(days=TRIAL_DAYS - 1)).isoformat()}
            if state.get("activation_code"):
                try:
                    result = {"usable": now.date() <= date.fromisoformat(self._decode(state["activation_code"])["expires_on"]), "state": "licensed", **self._decode(state["activation_code"])}
                    result["message"] = "授权有效" if result["usable"] else "授权已到期，请输入新的激活码"
                except Exception:
                    result = {"usable": False, "state": "invalid", "message": "激活码无效，请联系销售方"}
            elif now.date() <= date.fromisoformat(result["expires_on"]):
                result.update(usable=True, message="试用期有效")
            state.update(first_seen=first_seen, last_seen=now.date().isoformat())
            atomic_write_json(self.path, state)
            self._save_registry(first_seen, now.date().isoformat())
            return result

    def activate(self, code: str) -> dict:
        if not LICENSE_ENFORCEMENT_ENABLED:
            return self.status()
        with self._lock:
            try:
                decoded = self._decode(code)
            except Exception:
                return {"usable": False, "state": "invalid", "message": "激活码无效，请检查后重试"}
            state = self._load()
            state["activation_code"] = code.strip()
            atomic_write_json(self.path, state)
            self._cached_at = None
        return self.status()
