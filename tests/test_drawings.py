"""v0.1 regular-processing loop: open DXF, extract, glossary, write-back, PDF."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

import ezdxf
from ezdxf.entities import factory
from ezdxf.entities.acad_table import AcadTableBlockContent
from ezdxf.math import Vec2
from fastapi.testclient import TestClient

from backend.api import app, service
from unittest.mock import patch

from backend.drawings import (
    PRINT_TIMEOUT,
    apply_pdf_style,
    extract_preview,
    export_pdf,
    print_pdf,
    translate_rows,
    writeback_rows,
)
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


def _multileader_dxf(path: Path) -> Path:
    doc = ezdxf.new("R2013")
    msp = doc.modelspace()
    builder = msp.add_multileader_mtext("Standard")
    builder.quick_leader(r"{\C1;天花图}", target=Vec2(0, 0), segment1=Vec2(30, 10))
    paper = doc.layouts.new("A1")
    paper.add_text("接地", dxfattribs={"insert": (10, 10)})
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
        attdef = next(entity for entity in doc.blocks.get("TITLE") if entity.dxftype() == "ATTDEF")
        self.assertEqual(attdef.dxf.text, "distribution board")

    def test_translate_cad_file_keeps_mtext_codes(self):
        dest = self.root / "en_batch_mtext.dxf"
        translator = CADChineseTranslator(log_callback=lambda *_a, **_k: None)
        translator.configure_engine("deepl")
        self.assertFalse(translator.has_mt())
        translator.translate_cad_file(str(self.dxf), str(dest), "zh_to_en", False)
        mtext = next(entity for entity in ezdxf.readfile(dest).modelspace() if entity.dxftype() == "MTEXT")
        self.assertIn("\\C1;", mtext.dxf.text)
        self.assertIn("ceiling", mtext.dxf.text.lower())

    def test_translate_cad_file_bilingual_style(self):
        dest = self.root / "en_batch_bilingual.dxf"
        translator = CADChineseTranslator(log_callback=lambda *_a, **_k: None)
        translator.configure_engine("deepl")
        translator.translate_cad_file(str(self.dxf), str(dest), "zh_to_en", False, style="原译对照")
        reread = extract_preview(str(dest), include_attribs=True, include_paper=True)
        sources = {item["source"] for item in reread["items"]}
        self.assertIn("天花图", sources)
        self.assertIn("reflected ceiling plan", sources)
        mtext = next(entity for entity in ezdxf.readfile(dest).modelspace() if entity.dxftype() == "MTEXT")
        self.assertIn("\\C1;", mtext.dxf.text)
        self.assertIn("\\P", mtext.dxf.text)
        self.assertIn("天花", mtext.dxf.text)
        self.assertIn("ceiling", mtext.dxf.text.lower())

    def test_include_blocks_does_not_double_stamp_modelspace(self):
        dest = self.root / "en_blocks_bilingual.dxf"
        translator = CADChineseTranslator(log_callback=lambda *_a, **_k: None)
        translator.configure_engine("deepl")
        translator.translate_cad_file(
            str(self.dxf), str(dest), "zh_to_en", True, style="原译对照"
        )
        texts = [
            entity.dxf.text
            for entity in ezdxf.readfile(dest).modelspace()
            if entity.dxftype() == "TEXT"
        ]
        self.assertEqual(texts.count("天花图"), 1)
        self.assertEqual(texts.count("reflected ceiling plan"), 1)
        self.assertEqual(texts.count("剪力墙"), 1)
        self.assertEqual(texts.count("shear wall"), 1)

    def test_multileader_glossary_writeback_keeps_mtext_codes(self):
        dxf = _multileader_dxf(self.root / "mleader.dxf")
        preview = extract_preview(str(dxf), include_attribs=True, include_paper=True)
        kinds = {item["type"] for item in preview["items"]}
        self.assertIn("MULTILEADER", kinds)
        self.assertIn("TEXT", kinds)
        leader = next(item for item in preview["items"] if item["type"] == "MULTILEADER")
        self.assertEqual(leader["source"], "天花图")
        self.assertEqual(leader["field"], "mtext")
        self.assertIn("\\C1;", leader["raw"])
        paper = next(item for item in preview["items"] if item["source"] == "接地")
        self.assertEqual(paper["location"], "A1")

        translated = translate_rows(preview["items"], mode="zh_to_en", provider="deepl", engine={})
        by_source = {item["source"]: item for item in translated["items"]}
        self.assertEqual(by_source["天花图"]["target"], "reflected ceiling plan")
        self.assertIn("\\C1;", by_source["天花图"]["target_raw"])
        self.assertEqual(by_source["接地"]["target"], "grounding")

        output = writeback_rows(
            str(dxf),
            translated["items"],
            output_dir=str(self.root),
            output_name="en_mleader",
            mode="zh_to_en",
        )
        self.assertGreaterEqual(output["written"], 2)
        reread = extract_preview(output["path"], include_paper=True)
        sources = {item["source"] for item in reread["items"]}
        self.assertIn("reflected ceiling plan", sources)
        self.assertIn("grounding", sources)
        leader_out = next(item for item in reread["items"] if item["type"] == "MULTILEADER")
        self.assertIn("\\C1;", leader_out["raw"])

    def test_pdf_style_changes_bytes_and_labels(self):
        preview = extract_preview(str(self.dxf), include_attribs=True, include_paper=True)
        translated = translate_rows(preview["items"], mode="zh_to_en", provider="deepl", engine={})
        output = writeback_rows(
            str(self.dxf),
            translated["items"],
            output_dir=str(self.root),
            output_name="en_style",
            mode="zh_to_en",
        )
        written = output["path"]
        items = translated["items"]
        plain = export_pdf(written, str(self.root / "plain.pdf"), style="纯译文", items=items)
        source_first = export_pdf(written, str(self.root / "src_tgt.pdf"), style="原译对照", items=items)
        target_first = export_pdf(written, str(self.root / "tgt_src.pdf"), style="译原对照", items=items)
        self.assertEqual(plain["style"], "纯译文")
        self.assertEqual(source_first["style"], "原译对照")
        self.assertEqual(target_first["style"], "译原对照")
        self.assertEqual(plain["cad_path"], written)
        plain_bytes = Path(plain["path"]).read_bytes()
        src_bytes = Path(source_first["path"]).read_bytes()
        tgt_bytes = Path(target_first["path"]).read_bytes()
        self.assertEqual(plain_bytes[:5], b"%PDF-")
        self.assertNotEqual(plain_bytes, src_bytes)
        self.assertNotEqual(src_bytes, tgt_bytes)

        labeled = ezdxf.readfile(written)
        applied = apply_pdf_style(labeled, items, "原译对照")
        self.assertGreater(applied, 0)
        texts = [entity.dxf.text for entity in labeled.modelspace() if entity.dxftype() == "TEXT"]
        self.assertIn("天花图", texts)
        self.assertIn("reflected ceiling plan", texts)

        reversed_doc = ezdxf.readfile(written)
        apply_pdf_style(reversed_doc, items, "译原对照")
        ceiling = next(entity for entity in reversed_doc.modelspace() if entity.dxftype() == "TEXT" and "ceiling" in entity.dxf.text)
        self.assertEqual(ceiling.dxf.text, "reflected ceiling plan")

        source_pdf = export_pdf(str(self.dxf), str(self.root / "source_style.pdf"), style="原译对照", items=items)
        self.assertEqual(source_pdf["style"], "原译对照")

    def test_writeback_bilingual_style_rereads_two_lines(self):
        preview = extract_preview(str(self.dxf), include_attribs=True, include_paper=True)
        translated = translate_rows(preview["items"], mode="zh_to_en", provider="deepl", engine={})
        output = writeback_rows(
            str(self.dxf),
            translated["items"],
            output_dir=str(self.root),
            output_name="bilingual",
            mode="zh_to_en",
            style="原译对照",
        )
        self.assertEqual(output["style"], "原译对照")
        self.assertGreater(output["written"], 0)
        reread = extract_preview(output["path"], include_attribs=True, include_paper=True)
        sources = {item["source"] for item in reread["items"]}
        self.assertIn("天花图", sources)
        self.assertIn("reflected ceiling plan", sources)
        mtext = next(entity for entity in ezdxf.readfile(output["path"]).modelspace() if entity.dxftype() == "MTEXT")
        self.assertIn("\\C1;", mtext.dxf.text)
        self.assertIn("\\P", mtext.dxf.text)
        self.assertIn("天花", mtext.dxf.text)
        self.assertIn("ceiling", mtext.dxf.text.lower())

        reversed_out = writeback_rows(
            str(self.dxf),
            translated["items"],
            output_dir=str(self.root),
            output_name="bilingual_rev",
            mode="zh_to_en",
            style="译原对照",
        )
        rev = ezdxf.readfile(reversed_out["path"])
        ceiling = next(entity for entity in rev.modelspace() if entity.dxftype() == "TEXT" and "ceiling" in entity.dxf.text)
        self.assertEqual(ceiling.dxf.text, "reflected ceiling plan")
        mtext_rev = next(entity for entity in rev.modelspace() if entity.dxftype() == "MTEXT")
        self.assertTrue(mtext_rev.dxf.text.lower().startswith("{\\c1;ceiling") or "ceiling" in mtext_rev.dxf.text.split("\\P", 1)[0].lower())

    def test_export_pdf_is_real_pdf(self):
        dest = self.root / "sample.pdf"
        result = export_pdf(str(self.dxf), str(dest))
        self.assertTrue(Path(result["path"]).is_file())
        header = Path(result["path"]).read_bytes()[:5]
        self.assertEqual(header, b"%PDF-")
        self.assertGreater(result["bytes"], 200)
        self.assertGreaterEqual(result["pages"], 1)

    def test_export_pdf_unknown_layout_is_400(self):
        dest = self.root / "nope.pdf"
        with self.assertRaises(ValueError) as raised:
            export_pdf(str(self.dxf), str(dest), "NOPE")
        self.assertIn("没有这个布局", str(raised.exception))
        self.assertNotIn("Traceback", str(raised.exception))
        self.assertFalse(dest.exists())

    def test_export_pdf_named_a1_layout(self):
        dxf = FIXTURES / "floor_plan.dxf"
        self.assertTrue(dxf.is_file(), "tests/fixtures/floor_plan.dxf")
        names = [layout.name for layout in ezdxf.readfile(dxf).layouts]
        self.assertEqual(set(names), {"Model", "Layout1", "A1"}, names)
        all_pages = export_pdf(str(dxf), str(self.root / "floor_all.pdf"))
        a1 = export_pdf(str(dxf), str(self.root / "floor_a1.pdf"), "A1")
        self.assertEqual(a1["pages"], 1, a1)
        self.assertGreater(all_pages["pages"], a1["pages"])
        self.assertEqual(Path(a1["path"]).read_bytes()[:5], b"%PDF-")
        self.assertGreater(a1["bytes"], 800)
        _assert_cjk_pdf(self, Path(a1["path"]), min_ink=800)

    def test_export_pdf_named_model_layout(self):
        dxf = FIXTURES / "floor_plan.dxf"
        self.assertTrue(dxf.is_file(), "tests/fixtures/floor_plan.dxf")
        model = ezdxf.readfile(dxf).layouts.get("Model")
        self.assertEqual(model.name, "Model")
        self.assertTrue(model.is_modelspace)
        self.assertFalse(model.is_any_paperspace)
        texts = [entity.dxf.text for entity in model if entity.dxftype() == "TEXT"]
        self.assertIn("天花图", texts)
        all_pages = export_pdf(str(dxf), str(self.root / "floor_all_for_model.pdf"))
        named = export_pdf(str(dxf), str(self.root / "floor_model.pdf"), "Model")
        self.assertEqual(named["pages"], 1, named)
        self.assertGreater(all_pages["pages"], named["pages"])
        self.assertEqual(Path(named["path"]).read_bytes()[:5], b"%PDF-")
        self.assertGreater(named["bytes"], 800)
        _assert_cjk_pdf(self, Path(named["path"]), min_ink=800)

    def test_export_pdf_named_empty_layout1(self):
        dxf = FIXTURES / "floor_plan.dxf"
        self.assertTrue(dxf.is_file(), "tests/fixtures/floor_plan.dxf")
        paper = ezdxf.readfile(dxf).layouts.get("Layout1")
        self.assertEqual(paper.name, "Layout1")
        self.assertEqual(list(paper), [], "Layout1 must stay empty paperspace")
        empty = export_pdf(str(dxf), str(self.root / "floor_layout1.pdf"), "Layout1")
        self.assertEqual(empty["pages"], 1, empty)
        self.assertEqual(Path(empty["path"]).read_bytes()[:5], b"%PDF-")
        self.assertGreater(empty["bytes"], 200)
        self.assertNotIn("Traceback", str(empty))

    def test_floor_plan_glossary_writeback_pdf_and_paperspace(self):
        committed = FIXTURES / "floor_plan.dxf"
        dxf = committed if committed.is_file() else _floor_plan_dxf(self.root / "floor_plan.dxf")
        preview = extract_preview(str(dxf), include_attribs=True, include_paper=True, skip_dupes=False)
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

        pdf = export_pdf(output["path"], str(self.root / "floor_plan.pdf"))
        self.assertEqual(pdf["cad_path"], output["path"])
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

        pdf = export_pdf(output["path"], str(self.root / "dims_tables.pdf"))
        self.assertEqual(pdf["cad_path"], output["path"])
        self.assertGreater(pdf["bytes"], 800)
        self.assertGreaterEqual(pdf["pages"], 1)
        _assert_cjk_pdf(self, Path(pdf["path"]), min_ink=800)

    def test_writeback_bilingual_stamps_table_cells(self):
        committed = FIXTURES / "dims_tables.dxf"
        dxf = committed if committed.is_file() else _dims_tables_dxf(self.root / "dims_tables.dxf")
        preview = extract_preview(str(dxf), enable_v02=True)
        translated = translate_rows(preview["items"], mode="zh_to_en", provider="deepl", engine={})
        output = writeback_rows(
            str(dxf),
            translated["items"],
            output_dir=str(self.root),
            output_name="bilingual_tables",
            mode="zh_to_en",
            style="原译对照",
        )
        self.assertGreater(output["written"], 0)
        doc = ezdxf.readfile(output["path"])
        dim_texts = [entity.dxf.text for entity in doc.modelspace() if entity.dxftype() == "DIMENSION"]
        self.assertTrue(any("安装高度" in text and "installation height" in text for text in dim_texts), dim_texts)
        table_cells = []
        for entity in doc.modelspace():
            if entity.dxftype() != "ACAD_TABLE":
                continue
            tags = entity.xtags.get_subclass("AcDbTable")
            table_cells.extend(tag.value for tag in tags if tag.code == 302)
        self.assertTrue(any("墙体拆除图" in cell and "wall demolition plan" in cell for cell in table_cells), table_cells)
        self.assertTrue(any("材料表" in cell and "bill of materials" in cell for cell in table_cells), table_cells)
        reread = extract_preview(output["path"], enable_v02=True)
        table_sources = {item["source"] for item in reread["items"] if item["type"] == "ACAD_TABLE"}
        self.assertTrue(any("墙体拆除图" in source and "wall demolition plan" in source for source in table_sources), table_sources)

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

    def test_digits_empty_layer_and_odd_types_stay_calm(self):
        path = self.root / "digits.dxf"
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_text("1234", dxfattribs={"insert": (0, 0)})
        msp.add_text("-5.0", dxfattribs={"insert": (0, 20)})
        msp.add_text("天花", dxfattribs={"insert": (0, 40)})
        doc.saveas(path)
        preview = extract_preview(str(path))
        sources = {item["source"] for item in preview["items"]}
        self.assertIn("1234", sources)
        self.assertIn("-5.0", sources)
        skipped = extract_preview(str(path), skip_numbers=True)
        skipped_sources = {item["source"] for item in skipped["items"]}
        self.assertNotIn("1234", skipped_sources)
        self.assertNotIn("-5.0", skipped_sources)
        self.assertIn("天花", skipped_sources)
        self.assertTrue(all(isinstance(item["layer"], str) and item["layer"] for item in preview["items"]))
        self.assertIsInstance(preview["count"], int)
        self.assertIsInstance(preview["unique"], int)

        translated = translate_rows(
            [
                {"source": "天花", "type": "TEXT", "layer": ""},
                {"source": "1234", "type": "TEXT", "layer": None},
                {"source": "天花", "type": "TEXT", "layer": 0},
            ],
            mode="zh_to_en",
            provider="deepl",
            engine={},
        )
        self.assertEqual(translated["items"][0]["target"], "ceiling")
        self.assertEqual(translated["items"][2]["target"], "ceiling")
        self.assertFalse(translated["has_engine"])
        self.assertNotIn("Traceback", str(translated))

        translator = CADChineseTranslator(log_callback=lambda *_a, **_k: None)
        self.assertEqual(translator.glossary_hit("天花", "zh_to_en", 0), "ceiling")
        self.assertEqual(translator.glossary_hit("天花", "zh_to_en", ""), "ceiling")
        self.assertIsNone(translator.get_layer_glossary_translation("alimentation", "fr_to_zh", 0))

        from backend.language_assets import LanguageAssets

        assets = LanguageAssets(self.root / "assets.sqlite3")
        self.assertIsNone(assets.lookup_term("天花", "zh_to_en", 0))
        self.assertIsNone(assets.lookup_memory("天花", "zh_to_en", None))

    def test_extract_text_filters_skip_digits_dupes_nonsource(self):
        path = self.root / "filter_rows.dxf"
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_text("天花图", dxfattribs={"insert": (0, 0)})
        msp.add_text("天花图", dxfattribs={"insert": (0, 20)})
        msp.add_text("1234", dxfattribs={"insert": (0, 40)})
        msp.add_text("HELLO", dxfattribs={"insert": (0, 60)})
        doc.saveas(path)
        preview = extract_preview(
            str(path), skip_numbers=True, skip_dupes=True, skip_nonsource=True, mode="zh_to_en"
        )
        self.assertEqual([item["source"] for item in preview["items"]], ["天花图"])
        full = extract_preview(
            str(path), skip_numbers=False, skip_dupes=False, skip_nonsource=False
        )
        self.assertEqual(len(full["items"]), 4)

    def test_translate_skips_digits_dupes_nonsource(self):
        items = [
            {"source": "天花图", "type": "TEXT", "layer": "0", "duplicate": False, "handle": "A"},
            {"source": "天花图", "type": "TEXT", "layer": "0", "duplicate": True, "handle": "B"},
            {"source": "1234", "type": "TEXT", "layer": "0", "duplicate": False, "handle": "C"},
            {"source": "HELLO", "type": "TEXT", "layer": "0", "duplicate": False, "handle": "D"},
        ]
        full = translate_rows(items, mode="zh_to_en", provider="deepl", engine={})
        self.assertEqual(len(full["items"]), 4)
        skipped = translate_rows(
            items,
            mode="zh_to_en",
            provider="deepl",
            engine={},
            skip_numbers=True,
            skip_dupes=True,
            skip_nonsource=True,
        )
        self.assertEqual([item["source"] for item in skipped["items"]], ["天花图"])
        self.assertEqual(skipped["items"][0]["target"], "reflected ceiling plan")

    def test_writeback_skip_dupes_leaves_paperspace_copy(self):
        committed = FIXTURES / "floor_plan.dxf"
        dxf = committed if committed.is_file() else _floor_plan_dxf(self.root / "floor_plan.dxf")
        preview = extract_preview(
            str(dxf),
            include_attribs=True,
            include_paper=True,
            skip_dupes=True,
            skip_numbers=False,
            skip_nonsource=False,
        )
        sources = [item["source"] for item in preview["items"] if item["source"] == "平面布置图"]
        self.assertEqual(len(sources), 1)
        translated = translate_rows(preview["items"], mode="zh_to_en", provider="deepl", engine={})
        output = writeback_rows(
            str(dxf),
            translated["items"],
            output_dir=str(self.root),
            output_name="skip_dupes_paper",
            mode="zh_to_en",
        )
        doc = ezdxf.readfile(output["path"])
        paper = [entity.dxf.text for entity in doc.layouts.get("A1") if entity.dxftype() == "TEXT"]
        model = [entity.dxf.text for entity in doc.modelspace() if entity.dxftype() == "TEXT"]
        self.assertIn("平面布置图", paper)
        self.assertNotIn("floor plan", paper)
        self.assertIn("grounding", paper)
        self.assertIn("floor plan", model)
        self.assertIn("reflected ceiling plan", model)

    def test_frontend_dims_label_is_honest(self):
        source = Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.jsx"
        text = source.read_text(encoding="utf-8")
        self.assertIn("标注、表格", text)
        self.assertNotIn("标注（v0.2）", text)
        self.assertIn("enable_v02: params.dims", text)
        self.assertIn("dims: true", text)
        self.assertIn("enable_v02: params.dims,", text)
        self.assertNotIn("translate_blocks: params.attribs", text)
        self.assertIn("translate_blocks: false", text)
        self.assertIn("include_attribs: params.attribs", text)
        self.assertIn("include_model: params.model", text)
        self.assertIn("include_paper: params.paper", text)
        self.assertIn("include_frozen: params.frozen", text)
        self.assertIn("include_locked: params.locked", text)
        self.assertIn("include_off: params.off", text)
        self.assertIn("skip_numbers: filters.numbers", text)
        self.assertIn("skip_dupes: filters.dupes", text)
        self.assertIn("skip_nonsource: filters.nonsource", text)
        self.assertIn("items: visibleRows", text)
        self.assertIn("if (!visibleRows.length)", text)


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
        written_path = written.json()["path"]
        self.assertTrue(Path(written_path).is_file())
        reread = extract_preview(written_path)
        self.assertTrue(any(item["source"] == "reflected ceiling plan" for item in reread["items"]))
        self.assertFalse(any(item["source"] == "天花图" for item in reread["items"]))

        source_pdf = self.client.post(
            "/api/drawings/export-pdf",
            json={"path": path, "output_dir": self.tmp.name, "output_name": "source.pdf"},
        )
        self.assertEqual(source_pdf.status_code, 200, source_pdf.text)
        pdf = self.client.post(
            "/api/drawings/export-pdf",
            json={"path": written_path, "output_dir": self.tmp.name, "output_name": "api.pdf"},
        )
        self.assertEqual(pdf.status_code, 200, pdf.text)
        body = pdf.json()
        pdf_path = Path(body["path"])
        self.assertTrue(pdf_path.is_file())
        self.assertEqual(pdf_path.read_bytes()[:5], b"%PDF-")
        self.assertEqual(body["cad_path"], written_path)
        self.assertNotEqual(pdf_path.read_bytes(), Path(source_pdf.json()["path"]).read_bytes())
        extracted = _pdf_text(pdf_path)
        if extracted.strip():
            self.assertIn("ceiling", extracted.lower())
            self.assertNotIn("天花", extracted)

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
        gone = str(Path(self.tmp.name) / "gone.dxf")
        extracted = self.client.post("/api/drawings/extract", json={"path": gone, "translation_mode": "zh_to_en"})
        self.assertEqual(extracted.status_code, 404, extracted.text)
        self.assertEqual(extracted.json()["detail"], "图纸不存在")
        self.assertNotIn("Traceback", extracted.text)
        pdf = self.client.post(
            "/api/drawings/export-pdf",
            json={"path": gone, "output_dir": self.tmp.name, "output_name": "gone.pdf"},
        )
        self.assertEqual(pdf.status_code, 404, pdf.text)
        self.assertIn("不存在", pdf.json()["detail"])
        self.assertNotIn("Traceback", pdf.text)

    def test_print_without_drawing_is_400(self):
        empty = self.client.post("/api/drawings/print", json={})
        self.assertEqual(empty.status_code, 400, empty.text)
        self.assertIn("图纸", empty.json()["detail"])
        self.assertNotIn("Traceback", empty.text)
        missing = self.client.post("/api/drawings/print", json={"path": str(Path(self.tmp.name) / "gone.dxf")})
        self.assertEqual(missing.status_code, 400, missing.text)
        self.assertEqual(missing.json()["detail"], "图纸不存在")
        self.assertNotIn("Errno", missing.text)
        self.assertNotIn("Traceback", missing.text)

    def test_export_pdf_and_print_unknown_layout_is_400(self):
        pdf = self.client.post(
            "/api/drawings/export-pdf",
            json={"path": str(self.dxf), "output_dir": self.tmp.name, "output_name": "nope.pdf", "layout": "NOPE"},
        )
        self.assertEqual(pdf.status_code, 400, pdf.text)
        self.assertIn("没有这个布局", pdf.json()["detail"])
        self.assertNotIn("Traceback", pdf.text)
        printed = self.client.post(
            "/api/drawings/print",
            json={"path": str(self.dxf), "output_dir": self.tmp.name, "layout": "NOPE"},
        )
        self.assertEqual(printed.status_code, 400, printed.text)
        self.assertIn("没有这个布局", printed.json()["detail"])
        self.assertNotIn("Traceback", printed.text)

    def test_export_pdf_and_print_a1_layout(self):
        dxf = FIXTURES / "floor_plan.dxf"
        self.assertTrue(dxf.is_file(), "tests/fixtures/floor_plan.dxf")
        pdf = self.client.post(
            "/api/drawings/export-pdf",
            json={
                "path": str(dxf),
                "output_dir": self.tmp.name,
                "output_name": "a1.pdf",
                "layout": "A1",
            },
        )
        self.assertEqual(pdf.status_code, 200, pdf.text)
        self.assertNotIn("Traceback", pdf.text)
        body = pdf.json()
        self.assertEqual(body["pages"], 1)
        self.assertGreater(body["bytes"], 800)
        _assert_cjk_pdf(self, Path(body["path"]), min_ink=800)
        with patch("backend.drawings.shutil.which", return_value=None):
            printed = self.client.post(
                "/api/drawings/print",
                json={
                    "path": str(dxf),
                    "output_dir": self.tmp.name,
                    "output_name": "a1_print.pdf",
                    "layout": "A1",
                },
            )
        self.assertEqual(printed.status_code, 200, printed.text)
        self.assertNotIn("Traceback", printed.text)
        printed_body = printed.json()
        self.assertEqual(printed_body["pages"], 1)
        self.assertFalse(printed_body["print"]["ok"])
        _assert_cjk_pdf(self, Path(printed_body["path"]), min_ink=800)

    def test_export_pdf_and_print_model_layout(self):
        dxf = FIXTURES / "floor_plan.dxf"
        self.assertTrue(dxf.is_file(), "tests/fixtures/floor_plan.dxf")
        model = ezdxf.readfile(dxf).layouts.get("Model")
        self.assertTrue(model.is_modelspace)
        self.assertFalse(model.is_any_paperspace)
        pdf = self.client.post(
            "/api/drawings/export-pdf",
            json={
                "path": str(dxf),
                "output_dir": self.tmp.name,
                "output_name": "model.pdf",
                "layout": "Model",
            },
        )
        self.assertEqual(pdf.status_code, 200, pdf.text)
        self.assertNotIn("Traceback", pdf.text)
        body = pdf.json()
        self.assertEqual(body["pages"], 1)
        self.assertGreater(body["bytes"], 800)
        _assert_cjk_pdf(self, Path(body["path"]), min_ink=800)
        with patch("backend.drawings.shutil.which", return_value=None):
            printed = self.client.post(
                "/api/drawings/print",
                json={
                    "path": str(dxf),
                    "output_dir": self.tmp.name,
                    "output_name": "model_print.pdf",
                    "layout": "Model",
                },
            )
        self.assertEqual(printed.status_code, 200, printed.text)
        self.assertNotIn("Traceback", printed.text)
        printed_body = printed.json()
        self.assertEqual(printed_body["pages"], 1)
        self.assertFalse(printed_body["print"]["ok"])
        _assert_cjk_pdf(self, Path(printed_body["path"]), min_ink=800)

    def test_export_pdf_and_print_empty_layout1(self):
        dxf = FIXTURES / "floor_plan.dxf"
        self.assertTrue(dxf.is_file(), "tests/fixtures/floor_plan.dxf")
        self.assertEqual(list(ezdxf.readfile(dxf).layouts.get("Layout1")), [])
        pdf = self.client.post(
            "/api/drawings/export-pdf",
            json={
                "path": str(dxf),
                "output_dir": self.tmp.name,
                "output_name": "layout1.pdf",
                "layout": "Layout1",
            },
        )
        self.assertEqual(pdf.status_code, 200, pdf.text)
        self.assertNotIn("Traceback", pdf.text)
        body = pdf.json()
        self.assertEqual(body["pages"], 1)
        self.assertGreater(body["bytes"], 200)
        self.assertEqual(Path(body["path"]).read_bytes()[:5], b"%PDF-")
        with patch("backend.drawings.shutil.which", return_value=None):
            printed = self.client.post(
                "/api/drawings/print",
                json={
                    "path": str(dxf),
                    "output_dir": self.tmp.name,
                    "output_name": "layout1_print.pdf",
                    "layout": "Layout1",
                },
            )
        self.assertEqual(printed.status_code, 200, printed.text)
        self.assertNotIn("Traceback", printed.text)
        printed_body = printed.json()
        self.assertEqual(printed_body["pages"], 1)
        self.assertEqual(Path(printed_body["path"]).read_bytes()[:5], b"%PDF-")
        self.assertFalse(printed_body["print"]["ok"])
        self.assertIn("打印命令", printed_body["print"]["message"])
        self.assertNotIn("Traceback", printed_body["print"]["message"])

    def test_export_pdf_print_after(self):
        with patch("backend.drawings.shutil.which", return_value=None), patch(
            "backend.drawings.subprocess.run", side_effect=AssertionError("lp must not run")
        ):
            missing = self.client.post(
                "/api/drawings/export-pdf",
                json={
                    "path": str(self.dxf),
                    "output_dir": self.tmp.name,
                    "output_name": "print_after_none.pdf",
                    "print_after": True,
                },
            )
        self.assertEqual(missing.status_code, 200, missing.text)
        self.assertNotIn("Traceback", missing.text)
        none_body = missing.json()
        self.assertIn("print", none_body)
        self.assertFalse(none_body["print"]["ok"])
        self.assertIn("打印命令", none_body["print"]["message"])
        self.assertNotIn("Traceback", none_body["print"]["message"])
        _assert_cjk_pdf(self, Path(none_body["path"]), min_ink=800)

        fake = subprocess.CompletedProcess(["/usr/bin/lp", "print_after_ok.pdf"], 0, "", "")
        with patch("backend.drawings.shutil.which", return_value="/usr/bin/lp"), patch(
            "backend.drawings.subprocess.run", return_value=fake
        ) as run:
            ok = self.client.post(
                "/api/drawings/export-pdf",
                json={
                    "path": str(self.dxf),
                    "output_dir": self.tmp.name,
                    "output_name": "print_after_ok.pdf",
                    "print_after": True,
                },
            )
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertNotIn("Traceback", ok.text)
        ok_body = ok.json()
        self.assertTrue(ok_body["print"]["ok"])
        self.assertIn("lp", ok_body["print"]["command"])
        self.assertEqual(run.call_count, 1)
        _assert_cjk_pdf(self, Path(ok_body["path"]), min_ink=800)

    def test_print_without_lp_keeps_cjk_pdf(self):
        dest = Path(self.tmp.name) / "print.pdf"
        pdf = export_pdf(str(self.dxf), str(dest))
        with patch("backend.drawings.shutil.which", return_value=None), patch(
            "backend.drawings.subprocess.run", side_effect=AssertionError("lp must not run")
        ):
            result = print_pdf(pdf["path"])
        self.assertFalse(result["ok"])
        self.assertIn("打印命令", result["message"])
        self.assertNotIn("Traceback", result["message"])
        self.assertNotIn("Errno", result["message"])
        _assert_cjk_pdf(self, Path(pdf["path"]), min_ink=800)

        with patch("backend.drawings.shutil.which", return_value=None):
            printed = self.client.post(
                "/api/drawings/print",
                json={"path": str(self.dxf), "output_dir": self.tmp.name, "output_name": "api_print.pdf"},
            )
        self.assertEqual(printed.status_code, 200, printed.text)
        self.assertNotIn("Traceback", printed.text)
        body = printed.json()
        self.assertFalse(body["print"]["ok"])
        self.assertIn("打印命令", body["print"]["message"])
        _assert_cjk_pdf(self, Path(body["path"]), min_ink=800)

        committed = FIXTURES / "floor_plan.dxf"
        if committed.is_file():
            floor = export_pdf(str(committed), str(Path(self.tmp.name) / "floor_print.pdf"))
            _assert_cjk_pdf(self, Path(floor["path"]), min_ink=800)

    def test_print_timeout_is_calm(self):
        dest = Path(self.tmp.name) / "timeout.pdf"
        pdf = export_pdf(str(self.dxf), str(dest))
        with patch("backend.drawings.shutil.which", return_value="/usr/bin/lp"), patch(
            "backend.drawings.subprocess.run",
            side_effect=subprocess.TimeoutExpired("lp", 5),
        ):
            result = print_pdf(pdf["path"])
        self.assertFalse(result["ok"])
        self.assertIn("超时", result["message"])
        self.assertNotIn("Traceback", result["message"])
        self.assertLessEqual(PRINT_TIMEOUT, 5.0)

    def test_frontend_print_needs_a_drawing(self):
        source = Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.jsx"
        text = source.read_text(encoding="utf-8")
        self.assertIn("先打开图纸。", text)
        self.assertIn("disabled={busy || !current} onClick={() => exportPdf(true)}", text)
        self.assertIn("已送到系统打印", text)
        self.assertIn("function cadPathForPdf()", text)
        self.assertIn("PDF 已导出（写回图纸）", text)
        self.assertIn("原图，还未写回译文", text)
        self.assertIn("style: layout", text)

    def test_frontend_empty_filter_is_calm(self):
        source = Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.jsx"
        text = source.read_text(encoding="utf-8")
        self.assertIn("过滤后没有可显示的文字。", text)
        self.assertIn("这张图没有可译文字。", text)
        self.assertIn("function asCount(value)", text)
        self.assertIn("function asText(value)", text)
        self.assertIn("/^[\\d.\\-\\s]+$/", text)

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

    def test_extract_digits_only_drawing_is_200(self):
        path = Path(self.tmp.name) / "digits.dxf"
        doc = ezdxf.new("R2010")
        doc.modelspace().add_text("1234", dxfattribs={"insert": (0, 0)})
        doc.saveas(path)
        extracted = self.client.post(
            "/api/drawings/extract",
            json={"path": str(path), "translation_mode": "zh_to_en"},
        )
        self.assertEqual(extracted.status_code, 200, extracted.text)
        self.assertNotIn("Traceback", extracted.text)
        body = extracted.json()
        self.assertIsInstance(body["count"], int)
        self.assertFalse(any(item["source"] == "1234" for item in body["items"]))
        kept = self.client.post(
            "/api/drawings/extract",
            json={"path": str(path), "translation_mode": "zh_to_en", "skip_numbers": False, "skip_nonsource": False},
        )
        self.assertEqual(kept.status_code, 200, kept.text)
        self.assertTrue(any(item["source"] == "1234" for item in kept.json()["items"]))
        self.assertTrue(all(isinstance(item.get("layer"), str) for item in kept.json()["items"]))

    def test_translate_api_skips_digits_dupes_nonsource(self):
        items = [
            {"source": "天花图", "type": "TEXT", "layer": "0", "duplicate": False},
            {"source": "天花图", "type": "TEXT", "layer": "0", "duplicate": True},
            {"source": "1234", "type": "TEXT", "layer": "0", "duplicate": False},
            {"source": "HELLO", "type": "TEXT", "layer": "0", "duplicate": False},
        ]
        translated = self.client.post(
            "/api/drawings/translate",
            json={"items": items, "translation_mode": "zh_to_en", "provider": "deepl"},
        )
        self.assertEqual(translated.status_code, 200, translated.text)
        payload = translated.json()["items"]
        self.assertEqual([item["source"] for item in payload], ["天花图"])
        self.assertEqual(payload[0]["target"], "reflected ceiling plan")
        kept = self.client.post(
            "/api/drawings/translate",
            json={
                "items": items,
                "translation_mode": "zh_to_en",
                "provider": "deepl",
                "skip_numbers": False,
                "skip_dupes": False,
                "skip_nonsource": False,
            },
        )
        self.assertEqual(kept.status_code, 200, kept.text)
        self.assertEqual(len(kept.json()["items"]), 4)

    def test_extract_unreadable_dxf_is_400(self):
        path = Path(self.tmp.name) / "junk.dxf"
        path.write_text("not a dxf at all", encoding="utf-8")
        extracted = self.client.post(
            "/api/drawings/extract",
            json={"path": str(path), "translation_mode": "zh_to_en"},
        )
        self.assertEqual(extracted.status_code, 400, extracted.text)
        self.assertIn("无法读取", extracted.json()["detail"])
        self.assertNotIn("Traceback", extracted.text)


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
                saved_tasks = list(service.batch.tasks)
                try:
                    added = client.post("/api/batch/add", json={"files": [str(dwg)]})
                    self.assertEqual(added.status_code, 200, added.text)
                    self.assertNotIn("Traceback", added.text)
                    queued = added.json()["tasks"]
                    self.assertTrue(any(task["input_file"] == str(dwg) for task in queued))
                    with dwg.open("rb") as handle:
                        dropped = client.post(
                            "/api/batch/drop",
                            files={"files": (dwg.name, handle, "application/acad")},
                        )
                    self.assertEqual(dropped.status_code, 200, dropped.text)
                    self.assertNotIn("Traceback", dropped.text)
                finally:
                    service.batch.tasks = saved_tasks

    def test_updates_helper_survives_404(self):
        payload = check_github_release()
        self.assertIn(payload["current"], {"0.1.0", payload["current"]})
        self.assertIn("html_url", payload)


if __name__ == "__main__":
    unittest.main()
