"""Licensing is quarantined: not on the default 图译 product path."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_has_no_licensing_module():
    assert not (ROOT / "backend" / "licensing.py").exists()
    assert not (ROOT / "license_public_key.txt").exists()
    assert not (ROOT / "tools" / "license_issuer.py").exists()


def test_quarantine_holds_old_files():
    held = ROOT / "quarantine"
    assert (held / "licensing.py").is_file()
    assert (held / "license_public_key.txt").is_file()
    assert (held / "license_issuer.py").is_file()


def test_api_has_no_license_routes():
    from backend import api as web_api

    paths = {getattr(route, "path", "") for route in web_api.app.routes}
    assert "/api/license/status" not in paths
    assert "/api/license/activate" not in paths
    assert "/api/support/qrcode/{kind}" not in paths
    assert "/api/updates/check" in paths
    assert "/api/meta" in paths


def test_meta_disables_licensing():
    from backend.api import app_meta

    meta = app_meta()
    assert meta["licensing_enabled"] is False
    assert meta["version"]
    assert "erict16/dwglot" in meta["github"]


if __name__ == "__main__":
    test_runtime_has_no_licensing_module()
    test_quarantine_holds_old_files()
    test_api_has_no_license_routes()
    test_meta_disables_licensing()
    print("license path ok")
