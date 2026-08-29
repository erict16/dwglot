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
    def test_windows_pack_is_tuyi_without_oda(self):
        root = Path(__file__).resolve().parents[1]
        spec = (root / "Dwglot.spec").read_text(encoding="utf-8")
        iss = (root / "installer" / "Dwglot_Setup.iss").read_text(encoding="utf-8")
        run_py = (root / "run.py").read_text(encoding="utf-8")
        self.assertIn('name="Tuyi"', spec)
        self.assertIn("console=False", spec)
        self.assertIn("backend.cli", spec)
        self.assertIn('name="tuyi-cli"', spec)
        self.assertIn("console=True", spec)
        self.assertEqual(spec.count("Analysis("), 1)
        self.assertIn("COLLECT", spec)
        self.assertIn("exclude_binaries=True", spec)
        self.assertIn("_internal", spec)
        self.assertNotIn("collect_submodules", spec)
        self.assertNotRegex(spec, r'(?m)^\s*datas \+= collect_data_files\("matplotlib"\)')
        self.assertIn("tuyi-cli.exe", run_py)
        self.assertIn("Tuyi.exe", iss)
        self.assertIn("tuyi-cli.exe", iss)
        self.assertIn("_internal", iss)
        self.assertIn("Tuyi_v{#MyAppVersion}_Setup", iss)
        self.assertIn('#define MyAppName "图译"', iss)
        self.assertNotIn("[UninstallDelete]", iss)
        self.assertNotIn("backend.licensing", spec)
        self.assertNotIn("ODAFileConverter", iss)
        self.assertNotIn("Honsen_CAD_Translator", spec)
        self.assertNotIn("Honsen_CAD_Translator", iss)

    def test_macos_pack_is_tuyi_without_oda(self):
        root = Path(__file__).resolve().parents[1]
        spec = (root / "Dwglot_macos.spec").read_text(encoding="utf-8")
        script = (root / "installer" / "build_macos.py").read_text(encoding="utf-8")
        self.assertIn('name="Tuyi.app"', spec)
        self.assertIn('name="Tuyi"', spec)
        self.assertIn('name="tuyi-cli"', spec)
        self.assertIn('bundle_identifier="com.erict16.tuyi"', spec)
        self.assertIn('"CFBundleDisplayName": "图译"', spec)
        self.assertIn("fonts", spec)
        self.assertIn("backend.cli", spec)
        self.assertIn("backend.updates", spec)
        self.assertNotIn("backend.licensing", spec)
        self.assertNotIn("license_public_key.txt", spec)
        self.assertNotIn("Honsen", spec)
        self.assertNotIn("license_public_key", spec)
        self.assertIn("ODA is not packed", script)
        self.assertIn("ODA DMG must not be inside the app", script)
        self.assertIn('if "--oda-dmg" in sys.argv', script)
        self.assertNotIn('add_argument("--oda-dmg"', script)
        self.assertIn("--deep", script)

    def test_release_workflows_use_app_version_and_tag_only(self):
        root = Path(__file__).resolve().parents[1]
        win = (root / ".github" / "workflows" / "windows-release.yml").read_text(encoding="utf-8")
        mac = (root / ".github" / "workflows" / "macos-release.yml").read_text(encoding="utf-8")
        self.assertIn("pack_version.py", win)
        self.assertIn("pack_version.py", mac)
        self.assertIn("if: github.ref_type == 'tag'", win)
        self.assertIn("if: github.ref_type == 'tag'", mac)
        self.assertNotIn("v0.1.2", win)
        self.assertNotIn("|| 'v0.1.2'", win)
        self.assertIn("macos-latest", mac)
        self.assertIn("build_macos.py", mac)

    def test_frozen_cli_exe_dispatches_to_cli(self):
        import run

        with (
            patch.object(run.sys, "frozen", True, create=True),
            patch.object(run.sys, "argv", [r"C:\Program Files\Tuyi\tuyi-cli.exe", "translate", "a.dxf"]),
            patch("backend.cli.main", return_value=0) as cli_main,
        ):
            with self.assertRaises(SystemExit) as caught:
                run.main()
        self.assertEqual(caught.exception.code, 0)
        cli_main.assert_called_once()

    def test_unfrozen_run_is_not_cli(self):
        import run

        self.assertFalse(run._frozen_cli())

    def test_development_app_dir_is_repository_root(self):
        self.assertEqual(cad.get_app_dir(), Path(__file__).resolve().parents[1])

    def test_windows_keeps_edgechromium_and_exe_candidates(self):
        with patch("desktop.launcher.sys.platform", "win32"):
            self.assertEqual(_webview_gui(), "edgechromium")
        with patch("backend.cad.sys.platform", "win32"), patch.dict(os.environ, {}, clear=True):
            candidates = cad.odafc_candidate_paths()
        self.assertTrue(any(path.name == "ODAFileConverter.exe" for path in candidates))
        self.assertIn(Path(cad.ODA_SYSTEM_EXE), candidates)

    def test_windows_finds_oda_on_path(self):
        found = r"C:\Apps\ODAFileConverter\ODAFileConverter.exe"
        with (
            patch("backend.cad.sys.platform", "win32"),
            patch("backend.cad.shutil.which", return_value=found),
            patch.dict(os.environ, {}, clear=True),
        ):
            candidates = cad.odafc_candidate_paths()
        self.assertIn(Path(found), candidates)

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
        self.assertTrue(any(path.as_posix().endswith(".app/Contents/MacOS/ODAFileConverter") for path in candidates))
        self.assertTrue(any(path.name == "ODAFileConverter" for path in candidates))

    def test_frozen_macos_app_finds_adjacent_oda_app(self):
        executable = "/tmp/cad-dist/Tuyi.app/Contents/MacOS/Tuyi"
        exe = Path(executable).resolve()
        app_root = next(parent for parent in exe.parents if parent.suffix == ".app")
        adjacent = (app_root.parent / "ODAFileConverter.app" / "Contents" / "MacOS" / "ODAFileConverter").resolve()
        helpers = (
            app_root / "Contents" / "Helpers" / "ODAFileConverter.app" / "Contents" / "MacOS" / "ODAFileConverter"
        ).resolve()
        with (
            patch("backend.cad.sys.platform", "darwin"),
            patch("backend.cad.sys.executable", executable),
            patch.object(cad.sys, "frozen", True, create=True),
            patch("backend.cad.shutil.which", return_value=None),
            patch.dict(os.environ, {}, clear=True),
        ):
            candidates = [path.resolve() for path in cad.odafc_candidate_paths()]
            self.assertIn(adjacent, candidates)
            self.assertIn(helpers, candidates)

    def test_embedded_oda_dmg_is_not_mounted(self):
        self.assertIsNone(cad._mount_embedded_macos_odafc())

    def test_reveal_file_uses_finder_on_macos(self):
        with tempfile.NamedTemporaryFile() as output, patch("desktop.native_bridge.sys.platform", "darwin"), patch("desktop.native_bridge.subprocess.Popen") as popen:
            self.assertEqual(NativeBridge().reveal_file(output.name), {"ok": True})
        popen.assert_called_once_with(["open", "-R", os.path.normpath(output.name)])

    def test_macos_default_output_is_in_documents(self):
        with tempfile.TemporaryDirectory() as home, patch("backend.api.sys.platform", "darwin"), patch("backend.api.Path.home", return_value=Path(home)), patch("backend.app_meta.Path.home", return_value=Path(home)):
            self.assertEqual(
                TranslationService.default_output_dir(),
                str(Path(home) / "Documents" / "Tuyi output"),
            )
            self.assertTrue((Path(home) / "Documents" / "Tuyi output").is_dir())

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

    def test_dropped_files_dir_is_tuyi_not_honsen(self):
        from backend.app_meta import DROPPED_FILES_DIR

        self.assertEqual(DROPPED_FILES_DIR.name, ".tuyi_dropped_files")
        api_src = (Path(__file__).resolve().parents[1] / "backend" / "api.py").read_text(encoding="utf-8")
        self.assertIn("DROPPED_FILES_DIR", api_src)
        self.assertNotIn("cad_translator_dropped_files", api_src)

    def test_migrate_legacy_dropped_dir(self):
        from backend.app_meta import migrate_legacy_dir

        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            legacy = home_path / "cad_translator_dropped_files"
            current = home_path / ".tuyi_dropped_files"
            marker = legacy / "abc" / "a.dxf"
            marker.parent.mkdir(parents=True)
            marker.write_text("x", encoding="utf-8")
            migrate_legacy_dir(legacy, current)
            self.assertTrue((current / "abc" / "a.dxf").is_file())
            self.assertFalse(legacy.exists())
            migrate_legacy_dir(legacy, current)
            self.assertTrue((current / "abc" / "a.dxf").is_file())


if __name__ == "__main__":
    unittest.main()
