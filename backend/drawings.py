"""Extract, translate, write-back, and PDF export for the 常规处理 grid."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import ezdxf

from backend.app_meta import default_output_dir
from backend.cad import (
    CadConversionSession,
    analyze_source,
    dwg_to_work_dxf,
    odafc_available,
    output_path_for,
)
from backend.languages import split_mode
from backend.mtext_runs import map_translatable
from backend.styles import rewrite_shx_styles
from backend.translator import CADChineseTranslator, output_prefix
from backend.storage import atomic_output_path


V01_TYPES = {"TEXT", "MTEXT", "ATTDEF", "ATTRIB", "MULTILEADER"}


@contextmanager
def open_work_dxf(path: str):
    if not path or not os.path.isfile(path):
        raise FileNotFoundError("图纸不存在")
    suffix = Path(path).suffix.lower()
    if suffix not in {".dxf", ".dwg"}:
        raise ValueError("只支持 DWG / DXF")
    tmp = None
    work = path
    if suffix == ".dwg":
        if not odafc_available():
            raise RuntimeError("未检测到 ODA，无法打开 DWG。请安装 ODA 或另存为 DXF。")
        handle, tmp = tempfile.mkstemp(suffix=".dxf")
        os.close(handle)
        dwg_to_work_dxf(path, tmp)
        work = tmp
    try:
        yield work
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _flag(layer, name: str) -> bool:
    value = getattr(layer, name, False)
    try:
        value = value() if callable(value) else value
    except Exception:
        return False
    return bool(value)


def _layer_allowed(doc, layer_name: str, *, include_frozen: bool, include_locked: bool, include_off: bool) -> bool:
    try:
        layer = doc.layers.get(layer_name)
    except Exception:
        return True
    if not include_frozen and _flag(layer, "is_frozen"):
        return False
    if not include_locked and _flag(layer, "is_locked"):
        return False
    if not include_off and _flag(layer, "is_off"):
        return False
    return True


def _is_model(location: str) -> bool:
    name = (location or "").split("|", 1)[0].strip().lower()
    return name in {"model", "modelspace", "*model_space"}


def extract_preview(
    path: str,
    *,
    include_blocks: bool = False,
    mode: str = "zh_to_en",
    include_model: bool = True,
    include_paper: bool = True,
    include_attribs: bool = True,
    include_frozen: bool = False,
    include_locked: bool = False,
    include_off: bool = False,
    enable_v02: bool = False,
) -> dict:
    translator = CADChineseTranslator()
    translator.enable_v02_entities = bool(enable_v02)
    with open_work_dxf(path) as work_dxf:
        doc = ezdxf.readfile(work_dxf)
        raw = translator.extract_text_entities(doc, mode, include_blocks=include_blocks)
        rows = []
        seen = {}
        for index, item in enumerate(raw):
            source = item.get("original_text") or ""
            kind = (item.get("type") or "").upper()
            if not enable_v02 and kind not in V01_TYPES:
                continue
            if not include_attribs and kind in {"ATTRIB", "ATTDEF"}:
                continue
            location = item.get("location") or ""
            model = _is_model(location)
            if model and not include_model:
                continue
            if not model and not include_paper:
                continue
            layer = item.get("layer") or "0"
            if not _layer_allowed(
                doc,
                layer,
                include_frozen=include_frozen,
                include_locked=include_locked,
                include_off=include_off,
            ):
                continue
            duplicate = source in seen
            if not duplicate:
                seen[source] = index
            rows.append(
                {
                    "id": len(rows),
                    "source": source,
                    "target": "",
                    "target_raw": "",
                    "layer": layer,
                    "type": kind,
                    "location": location,
                    "handle": item.get("handle") or "",
                    "field": item.get("field") or "text",
                    "raw": item.get("raw_source") or "",
                    "duplicate": duplicate,
                    "selected": not duplicate,
                    "via": "",
                }
            )
    return {"path": path, "count": len(rows), "unique": len(seen), "items": rows}


def _plain_mtext(value: str) -> str:
    try:
        doc = ezdxf.new()
        entity = doc.modelspace().add_mtext(value or "")
        return entity.plain_text(fast=False) or value
    except Exception:
        return value or ""


def translate_rows(
    items: list[dict],
    *,
    mode: str = "zh_to_en",
    provider: str = "deepl",
    project_package_path: str = "",
    engine: dict | None = None,
) -> dict:
    split_mode(mode)
    translator = CADChineseTranslator()
    translator.configure_language_assets(project_package_path)
    engine = engine or {}
    translator.configure_engine(provider, **{k: engine.get(k, "") for k in (
        "deepl_key", "azure_key", "azure_region", "openai_key", "openai_base",
        "openai_model", "ollama_host", "ollama_model",
    )})
    has_mt = translator.has_mt()
    out = []
    glossary = 0
    mt = 0
    skipped = 0
    for item in items:
        row = dict(item)
        source = row.get("source") or ""
        layer = row.get("layer") or ""
        kind = (row.get("type") or "").upper()
        raw = row.get("raw") or row.get("raw_source") or ""
        if row.get("via") == "edit" and (row.get("target") or "").strip():
            out.append(row)
            continue

        used = {"glossary": False, "mt": False, "needs": False}

        def translate_run(text: str) -> str:
            hit = translator.glossary_hit(text, mode, layer)
            if hit:
                used["glossary"] = True
                return hit
            memory = translator.language_assets.lookup_memory(text, mode, layer)
            if memory:
                used["glossary"] = True
                return memory
            if not has_mt:
                used["needs"] = True
                return text
            used["mt"] = True
            return translator.translate_text(text, mode, layer)

        if kind == "MTEXT" and raw:
            translated_raw = map_translatable(raw, translate_run)
            row["target_raw"] = translated_raw
            row["target"] = translator.cleaner.full_clean(_plain_mtext(translated_raw))
        else:
            translated = translate_run(source)
            row["target"] = translated
            row["target_raw"] = translated
        if used["needs"] and not used["glossary"] and not used["mt"]:
            row["target"] = ""
            row["target_raw"] = ""
            row["via"] = "needs_engine"
            skipped += 1
        elif used["mt"]:
            row["via"] = translator.translation_provider or "mt"
            mt += 1
        else:
            row["via"] = "glossary"
            glossary += 1
        out.append(row)
    return {
        "items": out,
        "glossary": glossary,
        "mt": mt,
        "skipped": skipped,
        "has_engine": has_mt,
    }


def _entity_from_handle(doc, handle: str):
    if not handle:
        return None
    try:
        return doc.entitydb.get(str(handle))
    except Exception:
        return None


def writeback_rows(
    path: str,
    items: list[dict],
    *,
    output_dir: str = "",
    output_name: str = "",
    mode: str = "zh_to_en",
    include_blocks: bool = False,
) -> dict:
    if not items:
        raise ValueError("没有可写回的译文")
    def _should_write(item: dict) -> bool:
        if not (item.get("target") or "").strip():
            return False
        if item.get("selected", True):
            return True
        # Grid unchecks duplicates; they still live on the drawing.
        return bool(item.get("duplicate"))

    writable = [item for item in items if _should_write(item)]
    if not writable:
        raise ValueError("没有勾选且已填译文的条目")
    output_dir = output_dir or default_output_dir()
    os.makedirs(output_dir, exist_ok=True)
    meta = analyze_source(path)
    if not output_name.strip():
        output_name = f"{output_prefix(mode)}_{Path(path).stem}"
    output_file = output_path_for(meta, output_dir, output_name.strip())
    translator = CADChineseTranslator()
    translator.enable_v02_entities = False

    def log(message, level="INFO"):
        _ = level
        translator.safe_log(message)

    with CadConversionSession(path, log, "source", "") as session:
        work_input = session.work_input
        work_output = session.work_output_path() or output_file
        doc = ezdxf.readfile(work_input)
        written = 0
        missing = 0
        for item in writable:
            entity = _entity_from_handle(doc, item.get("handle") or "")
            if entity is None:
                missing += 1
                continue
            field = item.get("field") or "text"
            kind = entity.dxftype()
            target = item.get("target") or ""
            target_raw = item.get("target_raw") or ""
            if kind == "MTEXT" and target_raw and ("\\" in target_raw or "{" in target_raw) and item.get("via") != "edit":
                translator._write_mtext_entity(entity, target_raw)
            else:
                translator.write_back_translation(entity, target, field)
            if field == "tag":
                translator._sync_attrib_tags(doc, item.get("source") or "", target)
            written += 1
        rewrite_shx_styles(doc, translator.safe_log)
        with atomic_output_path(work_output) as temporary_output:
            doc.saveas(temporary_output)
        if session.meta.is_dwg or session.output_needs_oda:
            session.finalize(work_output, output_file)
    return {
        "path": output_file,
        "written": written,
        "missing": missing,
        "skipped": len(items) - len(writable),
    }


def _layout_has_entities(layout) -> bool:
    try:
        return next(iter(layout), None) is not None
    except Exception:
        return False


def _layout_pages(doc, layout_name: str = ""):
    if layout_name:
        try:
            return [doc.layouts.get(layout_name)]
        except Exception as exc:
            raise ValueError(f"没有这个布局: {layout_name}") from exc
    pages = [layout for layout in doc.layouts if _layout_has_entities(layout)]
    return pages or [doc.modelspace()]


def export_pdf(path: str, output_path: str = "", layout_name: str = "") -> dict:
    """DWG → ODA → DXF → PDF via ezdxf drawing (matplotlib). Not AutoCAD plot quality."""
    dest = Path(output_path) if output_path else Path(default_output_dir()) / f"{Path(path).stem}.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open_work_dxf(path) as work_dxf:
        doc = ezdxf.readfile(work_dxf)
        pages = _layout_pages(doc, layout_name)
        _render_pdf(pages, dest)
    if not dest.is_file() or dest.stat().st_size < 8:
        raise RuntimeError("PDF 导出失败")
    return {"path": str(dest), "pages": len(pages), "bytes": dest.stat().st_size}


def _render_pdf(layouts, dest: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from ezdxf.addons.drawing import Frontend
    from ezdxf.addons.drawing.config import Configuration
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.properties import LayoutProperties, RenderContext

    dpi = 150
    with atomic_output_path(str(dest)) as temporary_output, PdfPages(temporary_output) as pdf:
        for layout in layouts:
            fig = plt.figure(dpi=dpi)
            ax = fig.add_axes((0, 0, 1, 1))
            ctx = RenderContext(layout.doc)
            props = LayoutProperties.from_layout(layout)
            backend = MatplotlibBackend(ax)
            Frontend(ctx, backend, Configuration()).draw_layout(
                layout,
                finalize=True,
                layout_properties=props,
            )
            pdf.savefig(fig, dpi=dpi, facecolor=ax.get_facecolor())
            plt.close(fig)


def print_pdf(pdf_path: str) -> dict:
    if not pdf_path or not os.path.isfile(pdf_path):
        raise FileNotFoundError("PDF 不存在")
    if sys.platform == "darwin":
        cmd = ["lpr", pdf_path]
    elif sys.platform == "win32":
        os.startfile(pdf_path, "print")  # type: ignore[attr-defined]
        return {"ok": True, "path": pdf_path, "command": "print"}
    else:
        cmd = ["lp", pdf_path]
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        return {"ok": False, "path": pdf_path, "message": "系统没有打印命令，已留下 PDF"}
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        return {"ok": False, "path": pdf_path, "message": f"打印失败: {detail}"}
    return {"ok": True, "path": pdf_path, "command": " ".join(cmd)}
