"""Auto-update: version compare, GitHub calm errors, zip apply without Setup.exe."""

from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import tempfile
import time
import unittest
import urllib.error
import zipfile

from fastapi.testclient import TestClient

from backend.api import app
from backend.updates import (
    ApplyError,
    _set_state,
    check_github_release,
    copy_payload,
    download_file,
    is_allowed_url,
    is_newer,
    macos_helper_text,
    pick_update_asset,
    resolve_payload,
    start_apply,
    unavailable_payload,
    update_status,
    windows_helper_text,
    zip_name_for,
)


def _http_error(code: int, reason: str = "Error") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.github.com/repos/erict16/tuyi/releases/latest",
        code,
        reason,
        hdrs={},
        fp=BytesIO(b""),
    )


def _windows_zip_bytes() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("Tuyi.exe", b"new-exe")
        zf.writestr("_internal/runtime.txt", b"ok")
        zf.writestr("tuyi-cli.exe", b"cli")
    return buffer.getvalue()


def _asset(name: str, digest: str = "") -> dict:
    item = {
        "name": name,
        "url": f"https://github.com/erict16/tuyi/releases/download/v0.2.0/{name}",
    }
    if digest:
        item["sha256"] = digest
        item["digest"] = f"sha256:{digest}"
    return item


class FakeResponse:
    def __init__(self, payload: bytes, headers=None):
        self._payload = payload
        self.headers = headers or {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, n=-1):
        if not self._payload:
            return b""
        if n is None or n < 0:
            data = self._payload
            self._payload = b""
            return data
        data = self._payload[:n]
        self._payload = self._payload[n:]
        return data


class UpdateCheckTests(unittest.TestCase):
    def test_is_newer(self):
        self.assertTrue(is_newer("0.2.0", "0.1.0"))
        self.assertFalse(is_newer("0.1.0", "0.1.0"))
        self.assertFalse(is_newer("0.1.0", "0.2.0"))

    def test_check_handles_no_releases(self):
        with patch("backend.updates.urllib.request.urlopen", side_effect=_http_error(404, "Not Found")):
            payload = check_github_release()
        self.assertFalse(payload["available"])
        self.assertFalse(payload["can_apply"])
        self.assertTrue(payload["current"])
        self.assertIn("erict16/tuyi", payload["html_url"])
        self.assertTrue(payload["appcast_url"].endswith("appcast.xml"))
        self.assertEqual(payload["message"], "还没有 GitHub Release")

    def test_check_handles_github_403_calmly(self):
        with patch("backend.updates.urllib.request.urlopen", side_effect=_http_error(403, "rate limit")):
            payload = check_github_release()
        self.assertFalse(payload["available"])
        self.assertEqual(payload["message"], "GitHub API 暂不可用，打开 Releases 页查看")
        self.assertIn("erict16/tuyi", payload["html_url"])
        self.assertNotIn("Traceback", payload["message"])

    def test_check_handles_malformed_json_calmly(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b"<html>nope</html>"

        with patch("backend.updates.urllib.request.urlopen", return_value=Response()):
            payload = check_github_release()
        self.assertFalse(payload["available"])
        self.assertEqual(payload["message"], "GitHub API 暂不可用，打开 Releases 页查看")

    def test_api_updates_check_returns_200_on_github_403(self):
        with patch("backend.api.check_github_release", return_value=unavailable_payload("GitHub API 暂不可用，打开 Releases 页查看")):
            response = TestClient(app).get("/api/updates/check")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["available"])
        self.assertEqual(body["message"], "GitHub API 暂不可用，打开 Releases 页查看")
        self.assertNotIn("Traceback", response.text)

    def test_api_updates_check_survives_unexpected_error(self):
        with patch("backend.api.check_github_release", side_effect=RuntimeError("boom")):
            response = TestClient(app).get("/api/updates/check")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["message"], "GitHub API 暂不可用，打开 Releases 页查看")

    def test_api_status_idle(self):
        response = TestClient(app).get("/api/updates/status")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("phase", response.json())

    def test_apply_source_tree_is_400(self):
        response = TestClient(app).post("/api/updates/apply")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("源码运行", response.json()["detail"])
        self.assertNotIn("Traceback", response.text)

    def test_apply_busy_queue_is_400(self):
        from backend.api import service

        service.batch.started = True
        try:
            response = TestClient(app).post("/api/updates/apply")
        finally:
            service.batch.started = False
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("停止翻译队列", response.json()["detail"])


class UpdateAssetTests(unittest.TestCase):
    def test_zip_name(self):
        self.assertEqual(zip_name_for("0.1.3", "windows", "x86_64"), "Tuyi_v0.1.3_windows_x64.zip")
        self.assertEqual(zip_name_for("0.1.3", "macos", "arm64"), "Tuyi_v0.1.3_macOS_arm64.zip")

    def test_pick_exact_windows_zip_and_ignore_setup(self):
        assets = [
            _asset("Tuyi_v0.2.0_Setup.exe"),
            _asset("Tuyi_v0.2.0_windows_x64.zip", "abc"),
        ]
        picked = pick_update_asset(assets, "windows", "x86_64", "0.2.0")
        self.assertEqual(picked["name"], "Tuyi_v0.2.0_windows_x64.zip")
        self.assertEqual(picked["sha256"], "abc")

    def test_pick_macos_arch_zip(self):
        assets = [
            _asset("Tuyi_v0.2.0_macOS_x86_64.zip"),
            _asset("Tuyi_v0.2.0_macOS_arm64.zip", "def"),
        ]
        picked = pick_update_asset(assets, "macos", "arm64", "0.2.0")
        self.assertEqual(picked["name"], "Tuyi_v0.2.0_macOS_arm64.zip")

    def test_reject_foreign_url(self):
        self.assertTrue(is_allowed_url("https://github.com/erict16/tuyi/releases/download/v0.2.0/Tuyi_v0.2.0_windows_x64.zip"))
        self.assertFalse(is_allowed_url("https://evil.example/Tuyi.zip"))
        self.assertFalse(is_allowed_url("http://github.com/erict16/tuyi/releases/download/v0.2.0/x.zip"))
        assets = [{"name": "Tuyi_v0.2.0_windows_x64.zip", "url": "https://evil.example/x.zip"}]
        self.assertIsNone(pick_update_asset(assets, "windows", "x86_64", "0.2.0"))

    def test_check_exposes_zip_but_not_apply_when_not_frozen(self):
        payload = {
            "tag_name": "v9.9.9",
            "html_url": "https://github.com/erict16/tuyi/releases/tag/v9.9.9",
            "body": "",
            "assets": [
                {
                    "name": "Tuyi_v9.9.9_windows_x64.zip",
                    "browser_download_url": "https://github.com/erict16/tuyi/releases/download/v9.9.9/Tuyi_v9.9.9_windows_x64.zip",
                    "digest": "sha256:aa",
                    "size": 12,
                }
            ],
        }

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                import json

                return json.dumps(payload).encode("utf-8")

        with (
            patch("backend.updates.urllib.request.urlopen", return_value=Response()),
            patch("backend.updates.current_platform", return_value="windows"),
            patch("backend.updates.is_frozen", return_value=False),
        ):
            result = check_github_release()
        self.assertTrue(result["available"])
        self.assertFalse(result["can_apply"])
        self.assertEqual(result["asset"]["name"], "Tuyi_v9.9.9_windows_x64.zip")


class UpdatePayloadTests(unittest.TestCase):
    def test_resolve_windows_onedir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Tuyi.exe").write_bytes(b"exe")
            (root / "_internal").mkdir()
            (root / "_internal" / "x").write_text("1", encoding="utf-8")
            kind, payload = resolve_payload(root)
            self.assertEqual(kind, "windows")
            self.assertEqual(payload, root)

    def test_resolve_nested_tuyi_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "Tuyi"
            nested.mkdir()
            (nested / "Tuyi.exe").write_bytes(b"exe")
            (nested / "_internal").mkdir()
            kind, payload = resolve_payload(Path(tmp))
            self.assertEqual(kind, "windows")
            self.assertEqual(payload, nested)

    def test_resolve_macos_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Tuyi.app" / "Contents" / "MacOS"
            app.mkdir(parents=True)
            (app / "Tuyi").write_bytes(b"bin")
            kind, payload = resolve_payload(Path(tmp))
            self.assertEqual(kind, "macos")
            self.assertTrue(str(payload).endswith("Tuyi.app"))

    def test_copy_payload_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "new"
            dest = Path(tmp) / "old"
            staging.mkdir()
            dest.mkdir()
            (staging / "Tuyi.exe").write_bytes(b"new")
            (staging / "_internal").mkdir()
            (staging / "_internal" / "a.txt").write_text("n", encoding="utf-8")
            (dest / "Tuyi.exe").write_bytes(b"old")
            (dest / "_internal").mkdir()
            (dest / "_internal" / "a.txt").write_text("o", encoding="utf-8")
            copy_payload(staging, dest)
            self.assertEqual((dest / "Tuyi.exe").read_bytes(), b"new")
            self.assertEqual((dest / "_internal" / "a.txt").read_text(encoding="utf-8"), "n")

    def test_zip_slip_rejected(self):
        from backend.updates import _safe_extract

        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../evil.exe", b"nope")
            dest = Path(tmp) / "out"
            with self.assertRaises(ApplyError):
                _safe_extract(archive, dest)

    def test_sha_mismatch_deletes_file(self):
        payload = b"hello-zip"
        dest = None
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "Tuyi.zip"
            with patch("backend.updates.urllib.request.urlopen", return_value=FakeResponse(payload)):
                with self.assertRaises(ApplyError) as caught:
                    download_file(
                        "https://github.com/erict16/tuyi/releases/download/v0.2.0/Tuyi_v0.2.0_windows_x64.zip",
                        dest,
                        expected_sha="deadbeef",
                    )
            self.assertIn("校验失败", str(caught.exception))
            self.assertFalse(dest.exists())

    def test_helpers_replace_files_not_setup(self):
        win = windows_helper_text()
        mac = macos_helper_text()
        self.assertIn("robocopy", win)
        self.assertIn("Start-Process", win)
        self.assertNotIn("Setup.exe", win)
        self.assertNotIn("/VERYSILENT", win)
        self.assertIn("ditto", mac)
        self.assertIn("open", mac)
        self.assertNotIn("Setup.exe", mac)

    def test_start_apply_spawns_helper_not_setup(self):
        payload = _windows_zip_bytes()
        spawned = []
        _set_state(phase="idle", percent=0.0, message="", latest="", restarting=False)

        def fake_download(url, dest, expected_sha="", on_progress=None):
            Path(dest).write_bytes(payload)
            if on_progress:
                on_progress(1.0)
            return "ok"

        try:
            with tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp) / "install"
                dest.mkdir()
                (dest / "Tuyi.exe").write_bytes(b"old")
                (dest / "_internal").mkdir()
                with (
                    patch("backend.updates.is_frozen", return_value=True),
                    patch("backend.updates.current_platform", return_value="windows"),
                    patch("backend.updates.install_dir", return_value=dest),
                    patch("backend.updates.check_github_release", return_value={
                        "available": True,
                        "latest": "9.9.9",
                        "asset": _asset("Tuyi_v9.9.9_windows_x64.zip"),
                        "message": "",
                    }),
                    patch("backend.updates.download_file", side_effect=fake_download),
                    patch("backend.updates.install_dir_writable", return_value=True),
                    patch("backend.updates.spawn_helper", side_effect=lambda *args, **kwargs: spawned.append((args, kwargs))),
                ):
                    result = start_apply()
                    self.assertTrue(result["started"])
                    for _ in range(80):
                        status = update_status()
                        if status["phase"] in {"restarting", "error"}:
                            break
                        time.sleep(0.05)
                    status = update_status()
                self.assertEqual(status["phase"], "restarting", status)
                self.assertEqual(len(spawned), 1)
                script, args, plat = spawned[0][0][:3]
                self.assertEqual(plat, "windows")
                self.assertTrue(str(script).endswith(".ps1"))
                self.assertEqual(args[2], str(dest))
                self.assertNotIn("Setup.exe", str(script) + "".join(args))
        finally:
            _set_state(phase="idle", percent=0.0, message="", latest="", restarting=False)


if __name__ == "__main__":
    unittest.main()
