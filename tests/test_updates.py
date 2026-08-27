"""Auto-update helper: version compare + GitHub 403/404 is not a crash."""

from io import BytesIO
from unittest.mock import patch
import unittest
import urllib.error

from fastapi.testclient import TestClient

from backend.api import app
from backend.updates import check_github_release, is_newer, unavailable_payload


def _http_error(code: int, reason: str = "Error") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.github.com/repos/erict16/dwglot/releases/latest",
        code,
        reason,
        hdrs={},
        fp=BytesIO(b""),
    )


class UpdateCheckTests(unittest.TestCase):
    def test_is_newer(self):
        self.assertTrue(is_newer("0.2.0", "0.1.0"))
        self.assertFalse(is_newer("0.1.0", "0.1.0"))
        self.assertFalse(is_newer("0.1.0", "0.2.0"))

    def test_check_handles_no_releases(self):
        with patch("backend.updates.urllib.request.urlopen", side_effect=_http_error(404, "Not Found")):
            payload = check_github_release()
        self.assertFalse(payload["available"])
        self.assertTrue(payload["current"])
        self.assertIn("erict16/dwglot", payload["html_url"])
        self.assertTrue(payload["appcast_url"].endswith("appcast.xml"))
        self.assertEqual(payload["message"], "还没有 GitHub Release")

    def test_check_handles_github_403_calmly(self):
        with patch("backend.updates.urllib.request.urlopen", side_effect=_http_error(403, "rate limit")):
            payload = check_github_release()
        self.assertFalse(payload["available"])
        self.assertEqual(payload["message"], "GitHub API 暂不可用，打开 Releases 页查看")
        self.assertIn("erict16/dwglot", payload["html_url"])
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


if __name__ == "__main__":
    unittest.main()
