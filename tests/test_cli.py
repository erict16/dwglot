"""Agent CLI: 常规 写回 without the GUI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import ezdxf

from backend.cad import odafc_available
from backend.cli import main
from backend.drawings import translate_cjk_filename_stem
from backend.translator import CADChineseTranslator

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
FLOOR = FIXTURES / "floor_plan.dxf"
LIVE_DWG_DIR = Path("/workspace/dwglot-drawings")


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "tuyi", *args],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        env=env,
        **kwargs,
    )


def _dxf_texts(path: Path) -> list[str]:
    doc = ezdxf.readfile(path)
    texts: list[str] = []
    for layout in doc.layouts:
        for entity in layout:
            kind = entity.dxftype()
            if kind == "TEXT":
                texts.append(entity.dxf.text)
            elif kind == "MTEXT":
                texts.append(entity.plain_text(fast=False))
            elif kind == "INSERT":
                for attrib in entity.attribs:
                    texts.append(attrib.dxf.text)
    return texts


class CliTranslateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_translate_floor_plan_writes_glossary_english(self):
        output = self.root / "en_floor_plan.dxf"
        result = _run(["translate", str(FLOOR), "-o", str(output)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(output), result.stdout)
        self.assertRegex(result.stdout, r"extracted:\s+\d+")
        self.assertRegex(result.stdout, r"translated:\s+\d+")
        self.assertNotIn("Traceback", result.stderr)
        self.assertTrue(output.is_file())
        texts = _dxf_texts(output)
        self.assertIn("reflected ceiling plan", texts)
        self.assertIn("shear wall", texts)
        self.assertIn("floor plan", texts)
        self.assertIn("grounding", texts)

    def test_translate_filename_off_keeps_chinese_stem(self):
        source = self.root / "工作位置表及接线原理图10191W-CV2.dxf"
        shutil.copy(FLOOR, source)
        result = _run(["translate", str(source), "--output-dir", str(self.root)])
        self.assertEqual(result.returncode, 0, result.stderr)
        written = Path(result.stdout.strip().splitlines()[0])
        self.assertTrue(written.is_file(), result.stdout)
        self.assertTrue(written.name.startswith("en_工作位置表及接线原理图10191W-CV2_"), written.name)
        self.assertIn("工作位置表", written.name)
        self.assertNotIn("Traceback", result.stderr)

    def test_translate_filename_uses_helper(self):
        source = self.root / "天花10191W-CV2.dxf"
        shutil.copy(FLOOR, source)
        stem = translate_cjk_filename_stem(
            "天花10191W-CV2", mode="zh_to_en", translator=CADChineseTranslator()
        )
        self.assertEqual(stem, "ceiling10191W-CV2")
        result = _run(
            ["translate", str(source), "--output-dir", str(self.root), "--translate-filename"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        written = Path(result.stdout.strip().splitlines()[0])
        self.assertTrue(written.name.startswith(f"en_{stem}_"), written.name)
        self.assertNotIn("天花", written.name)
        self.assertIn("10191W-CV2", written.name)

    def test_no_glossary_hit_without_engine_is_chinese_nonzero(self):
        src = self.root / "novel.dxf"
        doc = ezdxf.new()
        doc.modelspace().add_text("这是不会在术语表里的句子XYZ", dxfattribs={"height": 2.5})
        doc.saveas(src)
        result = _run(["translate", str(src), "-o", str(self.root / "out.dxf")])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("没有可写回的译文", result.stderr)
        self.assertNotIn("勾选", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_unreadable_dxf_is_chinese_nonzero(self):
        junk = self.root / "junk.dxf"
        junk.write_bytes(b"not a dxf")
        result = _run(["translate", str(junk), "-o", str(self.root / "out.dxf")])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("无法读取", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_missing_drawing_is_chinese_nonzero(self):
        result = _run(["translate", str(self.root / "missing.dxf")])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("图纸不存在", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_dwg_without_oda_is_chinese_nonzero(self):
        dwg = self.root / "blank.dwg"
        dwg.write_bytes(b"AC1021")
        stdout, stderr = StringIO(), StringIO()
        with patch("backend.drawings.odafc_available", return_value=False):
            with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                code = main(["translate", str(dwg), "-o", str(self.root / "out.dwg")])
        self.assertNotEqual(code, 0)
        self.assertIn("未检测到 ODA", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertEqual(stdout.getvalue().strip(), "")

    def test_version_flag(self):
        result = _run(["--version"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"tuyi\s+0\.\d+")
        self.assertNotIn("Traceback", result.stderr)

    def test_two_inputs_reject_dash_o(self):
        other = self.root / "b.dxf"
        shutil.copy(FLOOR, other)
        result = _run(["translate", str(FLOOR), str(other), "-o", str(self.root / "out.dxf")])
        self.assertEqual(result.returncode, 2)
        self.assertIn("多个输入", result.stderr)

    def test_dash_o_same_path_does_not_overwrite(self):
        src = self.root / "same.dxf"
        shutil.copy(FLOOR, src)
        before = src.read_bytes()
        result = _run(["translate", str(src), "-o", str(src)])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("不能覆盖原图", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(src.read_bytes(), before)

    def test_glossary_override(self):
        package = self.root / "terms.hcterms.json"
        package.write_text(
            json.dumps(
                {
                    "format": "honsen-cad-terms/v1",
                    "name": "cli",
                    "terms": [{"mode": "zh_to_en", "source": "天花图", "target": "CUSTOM_CEILING"}],
                }
            ),
            encoding="utf-8",
        )
        output = self.root / "custom.dxf"
        result = _run(
            ["translate", str(FLOOR), "-o", str(output), "--glossary", str(package)]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CUSTOM_CEILING", _dxf_texts(output))

    def test_missing_glossary_is_chinese_nonzero(self):
        result = _run(["translate", str(FLOOR), "--glossary", str(self.root / "nope.json")])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("术语表不存在", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


@unittest.skipUnless(odafc_available(), "ODA not on PATH")
class CliLiveDwgTests(unittest.TestCase):
    def test_live_dwg_uses_oda(self):
        if not LIVE_DWG_DIR.is_dir():
            self.skipTest("no /workspace/dwglot-drawings")
        dwgs = sorted(LIVE_DWG_DIR.glob("工作位置表*.dwg")) or sorted(LIVE_DWG_DIR.glob("*.dwg"))
        if not dwgs:
            self.skipTest("no live DWG")
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(
                ["translate", str(dwgs[0]), "--output-dir", tmp],
                timeout=180,
            )
            self.assertNotIn("Traceback", result.stderr)
            self.assertNotIn("未检测到 ODA", result.stderr)
            if result.returncode == 0:
                written = Path(result.stdout.strip().splitlines()[0])
                self.assertTrue(written.is_file(), result.stdout)
                self.assertEqual(written.suffix.lower(), ".dwg")
            else:
                self.assertTrue(
                    any("\u4e00" <= char <= "\u9fff" for char in result.stderr),
                    result.stderr,
                )


if __name__ == "__main__":
    unittest.main()
