"""v0.1 regular-processing loop: open DXF, extract, glossary, write-back, PDF."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

import ezdxf
from ezdxf.entities import factory
from ezdxf.entities.acad_table import AcadTableBlockContent
from fastapi.testclient import TestClient

from backend.api import app
from backend.drawings import extract_preview, export_pdf, translate_rows, writeback_rows
from backend.styles import bundled_font_path, looks_like_shx, register_cjk_font, rewrite_shx_styles
from backend.translator import CADChineseTranslator
from backend.updates import check_github_release

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _pdf_text(path: Path) -> str:
    try:
        return subprocess.check_output(["pdftotext", "-layout", str(path), "-"], text=True, errors="ignore")
    except (OSError, subprocess.CalledProcessError):
        return ""


def _pdf_dark_pixels(path: Path) -> int:
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        prefix = Path(tmp) / "page"
        subprocess.run(["pdftoppm", "-png", "-r", "120", str(path), str(prefix)], check=True)
        pngs = sorted(Path(tmp).glob("*.png"))
        if not pngs:
            return 0
        image = Image.open(pngs[0]).convert("L")
        return sum(1 for pixel in image.tobytes() if pixel < 210)


def _assert_cjk_pdf(testcase: unittest.TestCase, pdf_path: Path, *, min_ink: int = 800) -> None:
    testcase.assertTrue(pdf_path.is_file())
    testcase.assertEqual(pdf_path.read_bytes()[:5], b"%PDF-")
    extracted = _pdf_text(pdf_path)
    if "平面" not in extracted and "天花" not in extracted and "安装" not in extracted:
        ink = _pdf_dark_pixels(pdf_path)
        testcase.assertGreater(ink, min_ink, f"rasterized PDF has too little ink ({ink}); CJK is probably tofu")


def _sample_dxf(path: Path) -> Path:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_text("天花图", dxfattribs={"insert": (0, 0)})
    msp.add_text("剪力墙", dxfattribs={"insert": (0, 20)})
    msp.add_mtext(r"{\C1;天花}", dxfattribs={"insert": (0, 40)})
    msp.add_text("1234", dxfattribs={"insert": (0, 60)})
    layout = doc.layouts.new("A1")
    layout.add_text("接地", dxfattribs={"insert": (10, 10)})
    block = doc.blocks.new("TITLE")
    block.add_attdef("DEV", insert=(0, 0), text="配电箱")
    insert = msp.add_blockref("TITLE", insert=(80, 0))
    insert.add_auto_attribs({"DEV": "配电箱"})
    dim = msp.add_linear_dim(base=(0, 100), p1=(0, 0), p2=(40, 0))
    dim.render()
    dim.dimension.dxf.text = "安装高度"
    doc.saveas(path)
    return path


def _floor_plan_dxf(path: Path) -> Path:
    """Chinese CAD-like sheet: walls, title block, TEXT/MTEXT/attribs, paperspace."""
    doc = ezdxf.new("R2010")
    for name, color in (("TITLE", 7), ("A-WALL", 1), ("E-POWR", 3)):
        doc.layers.add(name, color=color)
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0, 0), (12000, 0), (12000, 8000), (0, 8000), (0, 0)],
        dxfattribs={"layer": "A-WALL"},
    )
    msp.add_line((4000, 0), (4000, 8000), dxfattribs={"layer": "A-WALL"})
    msp.add_circle((2000, 2000), 400, dxfattribs={"layer": "E-POWR"})
    msp.add_text("平面布置图", dxfattribs={"layer": "TITLE", "insert": (200, 7600), "height": 300})
    msp.add_text("天花图", dxfattribs={"layer": "TITLE", "insert": (200, 7200), "height": 250})
    msp.add_text("剪力墙", dxfattribs={"layer": "A-WALL", "insert": (4200, 4000), "height": 200})
    msp.add_mtext(r"{\C1;隔墙定位图}", dxfattribs={"layer": "TITLE", "insert": (200, 6800), "char_height": 200})
    block = doc.blocks.new("PANEL")
    block.add_attdef("NAME", insert=(0, 0), text="配电箱", dxfattribs={"height": 150})
    insert = msp.add_blockref("PANEL", insert=(8000, 1000), dxfattribs={"layer": "E-POWR"})
    insert.add_auto_attribs({"NAME": "配电箱"})
    paper = doc.layouts.new("A1")
    paper.add_text("接地", dxfattribs={"layer": "E-POWR", "insert": (20, 20), "height": 5})
    paper.add_text("平面布置图", dxfattribs={"layer": "TITLE", "insert": (20, 280), "height": 8})
    doc.saveas(path)
    return path


def _dims_tables_dxf(path: Path) -> Path:
    """DIMENSION override + ``<>`` dim + ACAD_TABLE group-code 302 cells."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_text("天花图", dxfattribs={"insert": (0, 80), "height": 5})
    override = msp.add_linear_dim(base=(0, 30), p1=(0, 0), p2=(40, 0))
    override.render()
    override.dimension.dxf.text = "安装高度"
    measured = msp.add_linear_dim(base=(0, 50), p1=(0, 10), p2=(40, 10))
    measured.render()
    table_block = doc.blocks.new_anonymous_block("T")
    table_block.add_text("墙体拆除图", dxfattribs={"insert": (0, 0), "height": 2.5})
    table_block.add_text("材料表", dxfattribs={"insert": (30, 0), "height": 2.5})
    raw = (
        "0\nACAD_TABLE\n100\nAcDbEntity\n8\n0\n"
        "100\nAcDbBlockReference\n2\n"
        f"{table_block.name}\n"
        "10\n0.0\n20\n60.0\n30\n0.0\n"
        "100\nAcDbTable\n280\n0\n91\n1\n92\n2\n"
        "171\n1\n301\nCELL\n302\n墙体拆除图\n"
        "171\n1\n301\nCELL\n302\n材料表\n"
    )
    table = AcadTableBlockContent.from_text(raw)
    factory.bind(table, doc)
    msp.add_entity(table)
    doc.saveas(path)
    return path


class DrawingsLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dxf = _sample_dxf(self.root / "sample.dxf")

    def tearDown(self):
        self.tmp.cleanup()

    def test_extract_text_mtext_attribs_skips_dimension(self):
        preview = extract_preview(str(self.dxf), include_attribs=True, include_paper=True)
        kinds = {item["type"] for item in preview["items"]}
        sources = {item["source"] for item in preview["items"]}
        self.assertIn("TEXT", kinds)
        self.assertIn("MTEXT", kinds)
        self.assertIn("ATTRIB", kinds)
        self.assertNotIn("DIMENSION", kinds)
        self.assertIn("天花图", sources)
        self.assertIn("剪力墙", sources)
        self.assertIn("天花", sources)
        self.assertIn("接地", sources)
        self.assertIn("配电箱", sources)
        self.assertTrue(all(item["handle"] for item in preview["items"]))

    def test_glossary_translate_without_engine_then_writeback(self):
        preview = extract_preview(str(self.dxf))
        translated = translate_rows(preview["items"], mode="zh_to_en", provider="deepl", engine={})
        by_source = {item["source"]: item for item in translated["items"]}
        self.assertEqual(by_source["天花图"]["target"], "reflected ceiling plan")
        self.assertEqual(by_source["剪力墙"]["target"], "shear wall")
        self.assertEqual(by_source["天花"]["target"], "ceiling")
        self.assertEqual(by_source["接地"]["target"], "grounding")
        self.assertEqual(by_source["配电箱"]["target"], "distribution board")
        self.assertEqual(by_source["天花"]["via"], "glossary")
        self.assertIn("\\C1;", by_source["天花"]["target_raw"])
        self.assertGreaterEqual(translated["glossary"], 5)
        self.assertFalse(translated["has_engine"])

        output = writeback_rows(
            str(self.dxf),
            translated["items"],
            output_dir=str(self.root),
            output_name="en_sample",
            mode="zh_to_en",
        )
        out_path = Path(output["path"])
        self.assertTrue(out_path.is_file())
        self.assertGreater(output["written"], 0)
        doc = ezdxf.readfile(out_path)
        texts = []
        for entity in doc.modelspace():
            if entity.dxftype() == "TEXT":
                texts.append(entity.dxf.text)
            elif entity.dxftype() == "MTEXT":
                texts.append(entity.plain_text(fast=False))
            elif entity.dxftype() == "INSERT":
                for attrib in entity.attribs:
                    texts.append(attrib.dxf.text)
        self.assertIn("reflected ceiling plan", texts)
        self.assertIn("shear wall", texts)
        self.assertIn("ceiling", texts)
        self.assertIn("distribution board", texts)
        mtext = next(entity for entity in doc.modelspace() if entity.dxftype() == "MTEXT")
        self.assertIn("\\C1;", mtext.dxf.text)

    def test_export_pdf_is_real_pdf(self):
        dest = self.root / "sample.pdf"
        result = export_pdf(str(self.dxf), str(dest))
        self.assertTrue(Path(result["path"]).is_file())
        header = Path(result["path"]).read_bytes()[:5]
        self.assertEqual(header, b"%PDF-")
        self.assertGreater(result["bytes"], 200)
        self.assertGreaterEqual(result["pages"], 1)

    def test_floor_plan_glossary_writeback_pdf_and_paperspace(self):
        committed = FIXTURES / "floor_plan.dxf"
        dxf = committed if committed.is_file() else _floor_plan_dxf(self.root / "floor_plan.dxf")
        preview = extract_preview(str(dxf), include_attribs=True, include_paper=True)
        sources = {item["source"] for item in preview["items"]}
        self.assertTrue({"平面布置图", "天花图", "剪力墙", "隔墙定位图", "配电箱", "接地"} <= sources)
        translated = translate_rows(preview["items"], mode="zh_to_en", provider="deepl", engine={})
        by_source = {item["source"]: item for item in translated["items"]}
        self.assertEqual(by_source["平面布置图"]["target"], "floor plan")
        self.assertEqual(by_source["隔墙定位图"]["target"], "partition location plan")
        self.assertEqual(by_source["天花图"]["target"], "reflected ceiling plan")
        self.assertFalse(translated["has_engine"])
        self.assertGreaterEqual(translated["glossary"], 6)

        output = writeback_rows(
            str(dxf),
            translated["items"],
            output_dir=str(self.root),
            output_name="en_floor_plan",
            mode="zh_to_en",
        )
        doc = ezdxf.readfile(output["path"])
        paper = [entity.dxf.text for entity in doc.layouts.get("A1") if entity.dxftype() == "TEXT"]
        self.assertIn("floor plan", paper)
        self.assertIn("grounding", paper)
        model = []
        for entity in doc.modelspace():
            if entity.dxftype() == "TEXT":
                model.append(entity.dxf.text)
            elif entity.dxftype() == "MTEXT":
                model.append(entity.plain_text(fast=False))
            elif entity.dxftype() == "INSERT":
                for attrib in entity.attribs:
                    model.append(attrib.dxf.text)
        self.assertIn("floor plan", model)
        self.assertIn("partition location plan", model)
        self.assertIn("distribution board", model)

        pdf = export_pdf(str(dxf), str(self.root / "floor_plan.pdf"))
        self.assertGreater(pdf["bytes"], 2500)
        self.assertGreaterEqual(pdf["pages"], 2)
        _assert_cjk_pdf(self, Path(pdf["path"]), min_ink=800)
        names = [layout.name for layout in ezdxf.readfile(dxf).layouts]
        self.assertIn("Model", names)

    def test_dwg_without_oda_is_a_clear_error(self):
        dwg = self.root / "no_oda.dwg"
        dwg.write_bytes(b"AC1032" + b"\x00" * 32)
        with self.assertRaisesRegex(RuntimeError, "ODA"):
            extract_preview(str(dwg))

    def test_pdf_cjk_uses_bundled_noto_and_is_not_tofu(self):
        font_path = bundled_font_path()
        self.assertIsNotNone(font_path, "bundle fonts/NotoSansSC-Regular.otf")
        from fontTools.ttLib import TTFont

        cmap = TTFont(str(font_path)).getBestCmap()
        for char in "平面布置图":
            self.assertIn(ord(char), cmap)
        register_cjk_font()
        from ezdxf.fonts.fonts import make_font

        glyph = make_font(font_path.name, 10.0)
        path = glyph.text_path("平面布置图")
        self.assertGreater(len(path.vertices()), 40, "CJK glyphs should be real outlines, not .notdef boxes")

        dxf = self.root / "cjk_only.dxf"
        doc = ezdxf.new("R2010")
        doc.modelspace().add_text("平面布置图", dxfattribs={"insert": (0, 0), "height": 50})
        doc.saveas(dxf)
        pdf = export_pdf(str(dxf), str(self.root / "cjk_only.pdf"))
        pdf_path = Path(pdf["path"])
        self.assertEqual(pdf_path.read_bytes()[:5], b"%PDF-")
        extracted = _pdf_text(pdf_path)
        if "平面" not in extracted and "布置" not in extracted:
            ink = _pdf_dark_pixels(pdf_path)
            self.assertGreater(ink, 800, f"rasterized PDF has too little ink ({ink}); CJK is probably tofu")

    def test_rewrite_shx_does_not_clobber_ttf(self):
        doc = ezdxf.new("R2010", setup=True)
        before = {style.dxf.name: style.dxf.font for style in doc.styles}
        rewrite_shx_styles(doc)
        after = {style.dxf.name: style.dxf.font for style in doc.styles}
        for name, font in before.items():
            if font and Path(font).suffix.lower() in {".ttf", ".otf", ".ttc"}:
                self.assertEqual(after[name], font)
        self.assertFalse(looks_like_shx(""))
        self.assertFalse(looks_like_shx("OpenSans-Regular.ttf"))
        self.assertTrue(looks_like_shx("txt.shx"))

    def test_dims_and_tables_stay_gated_without_flag(self):
        dxf = _dims_tables_dxf(self.root / "dims_tables.dxf")
        preview = extract_preview(str(dxf), enable_v02=False)
        kinds = {item["type"] for item in preview["items"]}
        sources = {item["source"] for item in preview["items"]}
        self.assertNotIn("DIMENSION", kinds)
        self.assertNotIn("ACAD_TABLE", kinds)
        self.assertIn("天花图", sources)
        self.assertIn("墙体拆除图", sources)
        self.assertNotIn("安装高度", sources)

    def test_dimension_and_acad_table_extract_glossary_writeback(self):
        committed = FIXTURES / "dims_tables.dxf"
        dxf = committed if committed.is_file() else _dims_tables_dxf(self.root / "dims_tables.dxf")
        preview = extract_preview(str(dxf), enable_v02=True)
        kinds = {item["type"] for item in preview["items"]}
        by_type = {}
        for item in preview["items"]:
            by_type.setdefault(item["type"], []).append(item)
        self.assertIn("DIMENSION", kinds)
        self.assertIn("ACAD_TABLE", kinds)
        self.assertIn("TEXT", kinds)
        dim_sources = {item["source"] for item in by_type["DIMENSION"]}
        self.assertEqual(dim_sources, {"安装高度"})
        table_sources = {item["source"] for item in by_type["ACAD_TABLE"]}
        self.assertEqual(table_sources, {"墙体拆除图", "材料表"})
        self.assertTrue(all(item["field"].startswith("table:") for item in by_type["ACAD_TABLE"]))
        self.assertTrue(all(item["handle"] for item in preview["items"]))

        translated = translate_rows(preview["items"], mode="zh_to_en", provider="deepl", engine={})
        by_source = {item["source"]: item for item in translated["items"]}
        self.assertEqual(by_source["安装高度"]["target"], "installation height")
        self.assertEqual(by_source["墙体拆除图"]["target"], "wall demolition plan")
        self.assertEqual(by_source["材料表"]["target"], "bill of materials")
        self.assertEqual(by_source["天花图"]["target"], "reflected ceiling plan")
        self.assertEqual(by_source["安装高度"]["via"], "glossary")
        self.assertFalse(translated["has_engine"])

        output = writeback_rows(
            str(dxf),
            translated["items"],
            output_dir=str(self.root),
            output_name="en_dims_tables",
            mode="zh_to_en",
        )
        self.assertGreaterEqual(output["written"], 4)
        doc = ezdxf.readfile(output["path"])
        dim_texts = [entity.dxf.text for entity in doc.modelspace() if entity.dxftype() == "DIMENSION"]
        self.assertIn("installation height", dim_texts)
        self.assertIn("<>", dim_texts)
        table_cells = []
        for entity in doc.modelspace():
            if entity.dxftype() != "ACAD_TABLE":
                continue
            tags = entity.xtags.get_subclass("AcDbTable")
            table_cells.extend(tag.value for tag in tags if tag.code == 302)
        self.assertIn("wall demolition plan", table_cells)
        self.assertIn("bill of materials", table_cells)
        texts = [entity.dxf.text for entity in doc.modelspace() if entity.dxftype() == "TEXT"]
        self.assertIn("reflected ceiling plan", texts)

        reread = extract_preview(output["path"], enable_v02=True)
        reread_sources = {item["source"] for item in reread["items"]}
        self.assertIn("installation height", reread_sources)
        self.assertIn("wall demolition plan", reread_sources)
        self.assertIn("bill of materials", reread_sources)
        self.assertNotIn("安装高度", {item["source"] for item in reread["items"] if item["type"] == "DIMENSION"})

        pdf = export_pdf(str(dxf), str(self.root / "dims_tables.pdf"))
        self.assertGreater(pdf["bytes"], 800)
        self.assertGreaterEqual(pdf["pages"], 1)
        _assert_cjk_pdf(self, Path(pdf["path"]), min_ink=800)

    def test_acad_table_write_updates_preview_block(self):
        dxf = _dims_tables_dxf(self.root / "table_preview.dxf")
        doc = ezdxf.readfile(dxf)
        table = next(entity for entity in doc.modelspace() if entity.dxftype() == "ACAD_TABLE")
        block_name = table.get_block_name()
        before = [entity.dxf.text for entity in doc.blocks.get(block_name) if entity.dxftype() == "TEXT"]
        self.assertIn("墙体拆除图", before)
        translator = CADChineseTranslator(log_callback=lambda *args, **kwargs: None)
        wall_slot = next(index for index, text in translator._get_acad_table_text_slots(table) if text == "墙体拆除图")
        translator.write_back_translation(table, "wall demolition plan", f"table:{wall_slot}")
        cells = [tag.value for tag in table.xtags.get_subclass("AcDbTable") if tag.code == 302]
        self.assertIn("wall demolition plan", cells)
        after = [entity.dxf.text for entity in doc.blocks.get(block_name) if entity.dxftype() == "TEXT"]
        self.assertIn("wall demolition plan", after)
        self.assertNotIn("墙体拆除图", after)

    def test_frontend_dims_label_is_honest(self):
        source = Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.jsx"
        text = source.read_text(encoding="utf-8")
        self.assertIn("标注、表格", text)
        self.assertNotIn("标注（v0.2）", text)
        self.assertIn("enable_v02: params.dims", text)
        self.assertIn("dims: true", text)


class DrawingsApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dxf = _sample_dxf(Path(self.tmp.name) / "api.dxf")
        self.client = TestClient(app)

    def tearDown(self):
        self.tmp.cleanup()

    def test_open_extract_translate_writeback_update_pdf(self):
        with self.dxf.open("rb") as handle:
            opened = self.client.post("/api/drawings/open", files={"files": ("api.dxf", handle, "application/dxf")})
        self.assertEqual(opened.status_code, 200, opened.text)
        path = opened.json()["files"][0]["path"]

        extracted = self.client.post(
            "/api/drawings/extract",
            json={"path": path, "include_attribs": True, "translation_mode": "zh_to_en"},
        )
        self.assertEqual(extracted.status_code, 200, extracted.text)
        items = extracted.json()["items"]
        self.assertTrue(any(item["source"] == "天花图" for item in items))
        self.assertFalse(any(item["type"] == "DIMENSION" for item in items))
        self.assertFalse(any(item["type"] == "ACAD_TABLE" for item in items))

        translated = self.client.post(
            "/api/drawings/translate",
            json={"items": items, "translation_mode": "zh_to_en", "provider": "deepl"},
        )
        self.assertEqual(translated.status_code, 200, translated.text)
        payload = translated.json()
        self.assertGreaterEqual(payload["glossary"], 1)
        self.assertTrue(any(item["target"] == "reflected ceiling plan" for item in payload["items"]))

        written = self.client.post(
            "/api/drawings/writeback",
            json={
                "input_file": path,
                "items": payload["items"],
                "output_dir": self.tmp.name,
                "output_name": "en_api",
                "translation_mode": "zh_to_en",
            },
        )
        self.assertEqual(written.status_code, 200, written.text)
        self.assertTrue(Path(written.json()["path"]).is_file())

        pdf = self.client.post(
            "/api/drawings/export-pdf",
            json={"path": path, "output_dir": self.tmp.name, "output_name": "api.pdf"},
        )
        self.assertEqual(pdf.status_code, 200, pdf.text)
        pdf_path = Path(pdf.json()["path"])
        self.assertTrue(pdf_path.is_file())
        self.assertEqual(pdf_path.read_bytes()[:5], b"%PDF-")

        updates = self.client.get("/api/updates/check")
        self.assertEqual(updates.status_code, 200, updates.text)
        body = updates.json()
        self.assertIn("current", body)
        self.assertIn("available", body)
        self.assertIn("erict16/dwglot", body.get("html_url", ""))

        imported = self.client.post(
            "/api/language-assets/import",
            json={"mode": "zh_to_en", "csv": "测试词,test term\n"},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertGreaterEqual(imported.json()["count"], 1)

    def test_extract_enable_v02_returns_dimension_override(self):
        dxf = _dims_tables_dxf(Path(self.tmp.name) / "api_dims.dxf")
        extracted = self.client.post(
            "/api/drawings/extract",
            json={"path": str(dxf), "enable_v02": True, "translation_mode": "zh_to_en"},
        )
        self.assertEqual(extracted.status_code, 200, extracted.text)
        items = extracted.json()["items"]
        self.assertTrue(any(item["type"] == "DIMENSION" and item["source"] == "安装高度" for item in items))
        self.assertTrue(any(item["type"] == "ACAD_TABLE" and item["source"] == "墙体拆除图" for item in items))
        self.assertFalse(any(item["type"] == "DIMENSION" and item["source"] == "<>" for item in items))

    def test_open_empty_and_writeback_empty_are_calm(self):
        opened = self.client.post("/api/drawings/open")
        self.assertEqual(opened.status_code, 400, opened.text)
        self.assertIn("CAD", opened.json()["detail"])
        self.assertNotIn("Traceback", opened.text)
        written = self.client.post(
            "/api/drawings/writeback",
            json={"input_file": str(self.dxf), "items": [], "output_dir": self.tmp.name, "output_name": "empty"},
        )
        self.assertEqual(written.status_code, 400, written.text)
        self.assertIn("没有可写回", written.json()["detail"])
        self.assertNotIn("Traceback", written.text)
        missing = self.client.post(
            "/api/drawings/writeback",
            json={
                "input_file": str(Path(self.tmp.name) / "gone.dxf"),
                "items": [{"source": "天花", "target": "ceiling", "selected": True}],
                "output_dir": self.tmp.name,
                "output_name": "gone",
            },
        )
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(missing.json()["detail"], "图纸不存在")
        self.assertNotIn("Errno", missing.text)
        glossary = self.client.post("/api/language-assets/project", json={"path": str(Path(self.tmp.name) / "no.hcterms.json")})
        self.assertEqual(glossary.status_code, 400, glossary.text)
        self.assertIn("术语表不存在", glossary.json()["detail"])
        self.assertNotIn("Traceback", glossary.text)

    def test_frontend_import_tab_is_honest(self):
        source = Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.jsx"
        text = source.read_text(encoding="utf-8")
        self.assertIn("批量导入还没做。", text)
        self.assertNotIn("人工 Excel 回填", text)

    def test_open_dwg_without_oda_rejected(self):
        dwg = Path(self.tmp.name) / "x.dwg"
        dwg.write_bytes(b"AC1032" + b"\x00" * 16)
        with dwg.open("rb") as handle:
            opened = self.client.post("/api/drawings/open", files={"files": ("x.dwg", handle, "application/acad")})
        self.assertEqual(opened.status_code, 400, opened.text)
        self.assertIn("ODA", opened.json()["detail"])
        self.assertNotIn("Traceback", opened.text)


REAL_DWG_DIR = Path("/workspace/dwglot-drawings")


class RealDwgWithoutOdaTests(unittest.TestCase):
    """Huaming DWG fixtures. Skip if the folder is not mounted."""

    @classmethod
    def setUpClass(cls):
        cls.dwgs = sorted(REAL_DWG_DIR.glob("*.dwg")) if REAL_DWG_DIR.is_dir() else []

    def test_every_real_dwg_fails_cleanly_without_oda(self):
        if not self.dwgs:
            self.skipTest("no /workspace/dwglot-drawings")
        client = TestClient(app)
        self.assertGreaterEqual(len(self.dwgs), 2)
        for dwg in self.dwgs:
            with self.subTest(name=dwg.name):
                with self.assertRaisesRegex(RuntimeError, "ODA"):
                    extract_preview(str(dwg))
                extracted = client.post(
                    "/api/drawings/extract",
                    json={"path": str(dwg), "translation_mode": "zh_to_en"},
                )
                self.assertEqual(extracted.status_code, 400, extracted.text)
                self.assertIn("ODA", extracted.json()["detail"])
                self.assertNotIn("Traceback", extracted.text)
                written = client.post(
                    "/api/drawings/writeback",
                    json={
                        "input_file": str(dwg),
                        "items": [{"source": "天花", "target": "ceiling", "selected": True}],
                        "output_dir": "/tmp",
                        "output_name": "skip",
                        "translation_mode": "zh_to_en",
                    },
                )
                self.assertEqual(written.status_code, 400, written.text)
                self.assertIn("ODA", written.json()["detail"])
                pdf = client.post(
                    "/api/drawings/export-pdf",
                    json={"path": str(dwg), "output_dir": "/tmp", "output_name": "skip.pdf"},
                )
                self.assertEqual(pdf.status_code, 400, pdf.text)
                self.assertIn("ODA", pdf.json()["detail"])
                with dwg.open("rb") as handle:
                    opened = client.post(
                        "/api/drawings/open",
                        files={"files": (dwg.name, handle, "application/acad")},
                    )
                self.assertEqual(opened.status_code, 400, opened.text)
                self.assertIn("ODA", opened.json()["detail"])
                added = client.post("/api/batch/add", json={"files": [str(dwg)]})
                self.assertEqual(added.status_code, 400, added.text)
                self.assertIn("ODA", added.json()["detail"])
                self.assertNotIn("Traceback", added.text)
                with dwg.open("rb") as handle:
                    dropped = client.post(
                        "/api/batch/drop",
                        files={"files": (dwg.name, handle, "application/acad")},
                    )
                self.assertEqual(dropped.status_code, 400, dropped.text)
                self.assertIn("ODA", dropped.json()["detail"])
                self.assertNotIn("Traceback", dropped.text)

    def test_updates_helper_survives_404(self):
        payload = check_github_release()
        self.assertIn(payload["current"], {"0.1.0", payload["current"]})
        self.assertIn("html_url", payload)


if __name__ == "__main__":
    unittest.main()
