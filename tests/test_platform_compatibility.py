"""Regression checks for the shared Windows/macOS desktop paths."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend import cad
from backend.api import system_accent_theme
from desktop.launcher import _webview_gui
from desktop.native_bridge import NativeBridge
from backend.api import TranslationService


class PlatformCompatibilityTests(unittest.TestCase):
    def test_development_app_dir_is_repository_root(self):
        self.assertEqual(cad.get_app_dir(), Path(__file__).resolve().parents[1])

    def test_windows_keeps_edgechromium_and_exe_candidates(self):
        with patch("desktop.launcher.sys.platform", "win32"):
            self.assertEqual(_webview_gui(), "edgechromium")
        with patch("backend.cad.sys.platform", "win32"), patch.dict(os.environ, {}, clear=True):
            candidates = cad.odafc_candidate_paths()
        self.assertTrue(any(path.name == "ODAFileConverter.exe" for path in candidates))
        self.assertIn(Path(cad.ODA_SYSTEM_EXE), candidates)

    def test_macos_uses_native_webview_and_unix_oda_candidates(self):
        with patch("desktop.launcher.sys.platform", "darwin"):
            self.assertIsNone(_webview_gui())
        with (
            patch("backend.cad.sys.platform", "darwin"),
            patch("backend.cad.shutil.which", return_value=None),
            patch.dict(os.environ, {}, clear=True),
        ):
            candidates = cad.odafc_candidate_paths()
        expected_local_app = cad.get_app_dir() / "ODAFileConverter.app" / "Contents" / "MacOS" / "ODAFileConverter"
        self.assertIn(expected_local_app, candidates)
        self.assertTrue(any(str(path).endswith(".app/Contents/MacOS/ODAFileConverter") for path in candidates))
        self.assertTrue(any(path.name == "ODAFileConverter" for path in candidates))

    def test_frozen_macos_app_finds_adjacent_oda_app(self):
        executable = "/tmp/cad-dist/Honsen CAD Translator.app/Contents/MacOS/Honsen_CAD_Translator_v1.8.8"
        expected = Path("/tmp/cad-dist/ODAFileConverter.app/Contents/MacOS/ODAFileConverter").resolve()
        with (
            patch("backend.cad.sys.platform", "darwin"),
            patch("backend.cad.sys.executable", executable),
            patch.object(cad.sys, "frozen", True, create=True),
            patch("backend.cad.shutil.which", return_value=None),
            patch.dict(os.environ, {}, clear=True),
        ):
            candidates = cad.odafc_candidate_paths()
            self.assertIn(expected, candidates)
            self.assertIn(
                Path("/private/tmp/cad-dist/Honsen CAD Translator.app/Contents/Helpers/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"),
                candidates,
            )

    def test_embedded_read_only_dmg_has_highest_local_priority(self):
        mounted = Path("/private/tmp/oda-volume/ODAFileConverter.app/Contents/MacOS/ODAFileConverter")
        with patch("backend.cad._mount_embedded_macos_odafc", return_value=mounted), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(cad.odafc_candidate_paths()[0], mounted)

    def test_reveal_file_uses_finder_on_macos(self):
        with tempfile.NamedTemporaryFile() as output, patch("desktop.native_bridge.sys.platform", "darwin"), patch("desktop.native_bridge.subprocess.Popen") as popen:
            self.assertEqual(NativeBridge().reveal_file(output.name), {"ok": True})
        popen.assert_called_once_with(["open", "-R", os.path.normpath(output.name)])

    def test_macos_default_output_is_in_documents(self):
        with tempfile.TemporaryDirectory() as home, patch("backend.api.sys.platform", "darwin"), patch("backend.api.Path.home", return_value=Path(home)), patch("backend.app_meta.Path.home", return_value=Path(home)):
            self.assertEqual(
                TranslationService.default_output_dir(),
                str(Path(home) / "Documents" / "Dwglot output"),
            )
            self.assertTrue((Path(home) / "Documents" / "Dwglot output").is_dir())

    def test_macos_system_theme_uses_control_accent_colour(self):
        color = SimpleNamespace(
            colorUsingColorSpace_=lambda _: color,
            redComponent=lambda: 0.1,
            greenComponent=lambda: 0.2,
            blueComponent=lambda: 0.3,
        )
        color_class = SimpleNamespace(controlAccentColor=lambda: color)
        color_space = SimpleNamespace(sRGBColorSpace=lambda: object())
        with patch("backend.api.sys.platform", "darwin"), patch.dict("sys.modules", {"AppKit": SimpleNamespace(NSColor=color_class, NSColorSpace=color_space)}):
            self.assertEqual(system_accent_theme(), {"color": [0.1, 0.2, 0.3]})

    def test_macos_oda_stages_unicode_filename_as_ascii(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "Plan Mât.dwg"
            destination = Path(root) / "output.dxf"
            source.write_bytes(b"dwg")
            def fake_open(command, **_):
                args_index = command.index("--args")
                output_dir = Path(command[args_index + 2])
                (output_dir / "input.dxf").write_bytes(b"dxf")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with (
                patch("backend.cad.sys.platform", "darwin"),
                patch("backend.cad.resolve_odafc_path", return_value="/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"),
                patch("backend.cad.subprocess.run", side_effect=fake_open) as run,
            ):
                cad.convert_with_odafc(str(source), str(destination), version="ACAD2010", replace=True)
            command = run.call_args.args[0]
            self.assertEqual(command[:6], ["open", "-g", "-j", "-W", "-n", "-a"])
            self.assertEqual(command[-1], "input.dwg")
            self.assertEqual(destination.read_bytes(), b"dxf")

    def test_oda_working_dxf_uses_an_oda_output_identifier(self):
        self.assertEqual(cad.WORK_DXF_VERSION, "ACAD2010")
        self.assertIn(cad.WORK_DXF_VERSION, cad.ODA_OUTPUT_VERSIONS)


if __name__ == "__main__":
    unittest.main()
