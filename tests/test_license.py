"""Licensing is quarantined: not on the default 图译 product path."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LicensePathTests(unittest.TestCase):
    def test_runtime_has_no_licensing_module(self):
        self.assertFalse((ROOT / "backend" / "licensing.py").exists())
        self.assertFalse((ROOT / "license_public_key.txt").exists())
        self.assertFalse((ROOT / "tools" / "license_issuer.py").exists())

    def test_quarantine_holds_old_files(self):
        held = ROOT / "quarantine"
        self.assertTrue((held / "licensing.py").is_file())
        self.assertTrue((held / "license_public_key.txt").is_file())
        self.assertTrue((held / "license_issuer.py").is_file())

    def test_api_has_no_license_routes(self):
        from backend import api as web_api

        paths = {getattr(route, "path", "") for route in web_api.app.routes}
        self.assertNotIn("/api/license/status", paths)
        self.assertNotIn("/api/license/activate", paths)
        self.assertNotIn("/api/support/qrcode/{kind}", paths)
        self.assertIn("/api/updates/check", paths)
        self.assertIn("/api/meta", paths)

    def test_meta_disables_licensing(self):
        from backend.api import app_meta

        meta = app_meta()
        self.assertFalse(meta["licensing_enabled"])
        self.assertTrue(meta["version"])
        self.assertIn("erict16/dwglot", meta["github"])


if __name__ == "__main__":
    unittest.main()
