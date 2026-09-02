"""CSV table import/export for 批量导入."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import ezdxf
from fastapi.testclient import TestClient

from backend.api import app
from backend.table_csv import apply_table_rows, export_table_csv, parse_table_csv

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TableCsvUnitTests(unittest.TestCase):
    def test_export_parse_roundtrip_and_source_match(self):
        items = [
            {"handle": "A1", "field": "text", "source": "天花图", "target": "", "layer": "0", "type": "TEXT"},
            {"handle": "B2", "field": "text", "source": "接地", "target": "", "layer": "E", "type": "MTEXT"},
        ]
        csv_text = export_table_csv(items, "floor_plan.dxf")
        self.assertIn("天花图", csv_text)
        parsed = parse_table_csv(csv_text.replace(",,", ",reflected ceiling plan,"))
        self.assertTrue(any(row["source"] == "天花图" for row in parsed))

        filled, applied = apply_table_rows(items, [{"source": "天花图", "target": "ceiling"}], "floor_plan.dxf")
        self.assertEqual(applied, 1)
        self.assertEqual(filled[0]["target"], "ceiling")
        self.assertEqual(filled[0]["via"], "edit")
        self.assertEqual(filled[1]["target"], "")

    def test_handle_beats_source_and_file_scope(self):
        items = [
            {"handle": "H1", "field": "text", "source": "天花图", "target": "", "type": "TEXT"},
            {"handle": "H2", "field": "text", "source": "接地", "target": "", "type": "TEXT"},
        ]
        imported = [
            {"file": "a.dxf", "handle": "H1", "field": "text", "source": "天花图", "target": "one"},
            {"file": "b.dxf", "handle": "H2", "field": "text", "source": "接地", "target": "two"},
        ]
        filled, applied = apply_table_rows(items, imported, "a.dxf")
        self.assertEqual(applied, 1)
        self.assertEqual(filled[0]["target"], "one")
        self.assertEqual(filled[1]["target"], "")

    def test_handle_does_not_cross_fields(self):
        items = [
            {"handle": "H1", "field": "text", "source": "天花图", "target": ""},
            {"handle": "H1", "field": "tag", "source": "MJ01", "target": ""},
        ]
        imported = [{"handle": "H1", "field": "tag", "source": "MJ01", "target": "CODE"}]
        filled, applied = apply_table_rows(items, imported, "a.dxf")
        self.assertEqual(applied, 1)
        self.assertEqual(filled[0]["target"], "")
        self.assertEqual(filled[1]["target"], "CODE")

    def test_xlsx_roundtrip(self):
        from backend.table_xlsx import export_table_xlsx, parse_table_xlsx

        items = [
            {"handle": "A1", "field": "text", "source": "天花图", "target": "ceiling", "layer": "0", "type": "TEXT"},
        ]
        payload = export_table_xlsx(items, "floor_plan.dxf")
        parsed = parse_table_xlsx(payload)
        self.assertEqual(parsed[0]["source"], "天花图")
        self.assertEqual(parsed[0]["target"], "ceiling")
        self.assertEqual(parsed[0]["handle"], "A1")

    def test_chinese_headers_and_empty(self):
        parsed = parse_table_csv("原文,译文\n天花图,ceiling\n")
        self.assertEqual(parsed[0]["source"], "天花图")
        self.assertEqual(parsed[0]["target"], "ceiling")
        self.assertEqual(parse_table_csv(""), [])
        self.assertEqual(parse_table_csv("source,target\n"), [])


class TableCsvApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = TestClient(app)
        self.dxf = FIXTURES / "floor_plan.dxf"

    def tearDown(self):
        self.tmp.cleanup()

    def test_export_then_import_writeback(self):
        exported = self.client.post(
            "/api/drawings/export-table",
            json={"path": str(self.dxf), "translation_mode": "zh_to_en"},
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        csv_text = exported.json()["csv"]
        self.assertIn("天花图", csv_text)
        self.assertTrue(exported.json()["filename"].endswith(".csv"))

        filled = "天花图,reflected ceiling plan\n接地,grounding\n"
        preview = self.client.post(
            "/api/drawings/import-table",
            json={"csv": filled, "items": exported.json()["items"], "file": "floor_plan.dxf"},
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertGreaterEqual(preview.json()["applied"], 1)
        self.assertTrue(any(item["target"] == "reflected ceiling plan" for item in preview.json()["items"]))

        written = self.client.post(
            "/api/batch/import",
            json={
                "csv": filled,
                "files": [str(self.dxf)],
                "output_dir": self.tmp.name,
                "translation_mode": "zh_to_en",
            },
        )
        self.assertEqual(written.status_code, 200, written.text)
        self.assertGreaterEqual(written.json()["written"], 1)
        path = written.json()["results"][0]["path"]
        reread = ezdxf.readfile(path)
        blob = " ".join(
            str(getattr(entity.dxf, "text", "") or "")
            for layout in reread.layouts
            for entity in layout
            if hasattr(entity.dxf, "text")
        )
        self.assertIn("reflected ceiling plan", blob)

    def test_import_empty_and_missing_file(self):
        empty = self.client.post("/api/batch/import", json={"csv": "", "files": [str(self.dxf)], "output_dir": self.tmp.name})
        self.assertEqual(empty.status_code, 400, empty.text)
        self.assertIn("空", empty.json()["detail"])

        missing = self.client.post("/api/batch/import", json={"csv": "天花图,ceiling\n", "files": [], "output_dir": self.tmp.name})
        self.assertEqual(missing.status_code, 400, missing.text)
        self.assertIn("CAD", missing.json()["detail"])

    def test_translate_can_skip_glossary(self):
        extracted = self.client.post(
            "/api/drawings/extract",
            json={"path": str(self.dxf), "translation_mode": "zh_to_en", "enable_v02": True},
        )
        self.assertEqual(extracted.status_code, 200, extracted.text)
        items = extracted.json()["items"]
        off = self.client.post(
            "/api/drawings/translate",
            json={"items": items, "translation_mode": "zh_to_en", "provider": "deepl", "use_glossary": False},
        )
        self.assertEqual(off.status_code, 200, off.text)
        self.assertEqual(off.json()["glossary"], 0)
        self.assertFalse(any((item.get("target") or "") == "reflected ceiling plan" for item in off.json()["items"]))


if __name__ == "__main__":
    unittest.main()
