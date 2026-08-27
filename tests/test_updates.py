"""Auto-update helper: version compare + GitHub 404 is not a crash."""

from unittest.mock import patch
import urllib.error

from backend.updates import check_github_release, is_newer


def test_is_newer():
    assert is_newer("0.2.0", "0.1.0")
    assert not is_newer("0.1.0", "0.1.0")
    assert not is_newer("0.1.0", "0.2.0")


def test_check_handles_no_releases():
    error = urllib.error.HTTPError("https://api.github.com/", 404, "Not Found", hdrs={}, fp=None)
    with patch("backend.updates.urllib.request.urlopen", side_effect=error):
        payload = check_github_release()
    assert payload["available"] is False
    assert payload["current"]
    assert "erict16/dwglot" in payload["html_url"]
    assert payload["appcast_url"].endswith("appcast.xml")


if __name__ == "__main__":
    test_is_newer()
    test_check_handles_no_releases()
    print("updates ok")
