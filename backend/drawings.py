"""Extract, translate, write-back, and PDF export for the 常规处理 grid."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import ezdxf

from backend.app_meta import default_output_dir
from backend.cad import (
    CadConversionSession,
    analyze_source,
    dwg_to_work_dxf,
    dwg_unavailable_short,
    odafc_available,
    output_path_for,
)
from backend.languages import split_mode
from backend.mtext_runs import map_translatable
from backend.styles import register_cjk_font, rewrite_shx_styles
from backend.table_csv import apply_table_rows, export_table_csv, parse_table_csv
from backend.translator import CADChineseTranslator, output_prefix
from backend.storage import atomic_output_path


V01_TYPES = {"TEXT", "MTEXT", "ATTDEF", "ATTRIB", "MULTILEADER"}
V02_TYPES = {"DIMENSION", "ACAD_TABLE"}
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_UNSAFE_FILENAME = re.compile(r'[/\\:*?"<>|]+')
ENGINE_KEYS = (
    "deepl_key",
    "azure_key",
    "azure_region",
    "openai_key",
    "openai_base",
    "openai_model",
    "ollama_host",
    "ollama_model",
)
# 常规处理 defaults (App.jsx params + filters).
REGULAR_EXTRACT = {
    "include_blocks": False,
    "include_model": True,
    "include_paper": True,
    "include_attribs": True,
    "include_frozen": False,
    "include_locked": False,
    "include_off": False,
    "enable_v02": True,
    "skip_numbers": True,
    "skip_dupes": True,
    "skip_nonsource": True,
}


def sanitize_filename_stem(name: str) -> str:
    cleaned = _UNSAFE_FILENAME.sub("", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "drawing"


def _translate_cjk_chunk(chunk: str, mode: str, translator: CADChineseTranslator) -> str:
    hit = translator.glossary_hit(chunk, mode)
    if hit and str(hit).strip():
        return str(hit).strip()
    n = len(chunk)
    i = 0
    parts: list[str] = []
    unmatched: list[str] = []

    def flush_unmatched() -> None:
        if not unmatched:
            return
        rest = "".join(unmatched)
        unmatched.clear()
        if translator.has_mt():
            try:
                text = str(translator.translate_text(rest, mode, "")).strip()
            except Exception:
                text = rest
            parts.append(text or rest)
        else:
            parts.append(rest)

    while i < n:
        found = None
        end = None
        for j in range(n, i, -1):
            piece = chunk[i:j]
            hit = translator.glossary_hit(piece, mode)
            if hit and str(hit).strip():
                found = str(hit).strip()
                end = j
                break
        if found:
            flush_unmatched()
            parts.append(found)
            i = end
        else:
            unmatched.append(chunk[i])
            i += 1
    flush_unmatched()
    return "".join(parts) or chunk


def translate_cjk_filename_stem(stem: str, *, mode: str, translator: CADChineseTranslator) -> str:
    stem = Path(str(stem or "")).name
    if "." in stem and stem.rsplit(".", 1)[-1].lower() in {"dwg", "dxf"}:
        stem = stem.rsplit(".", 1)[0]
    if not stem:
        return "drawing"

    def replace(match: re.Match[str]) -> str:
        chunk = match.group(0)
        return _translate_cjk_chunk(chunk, mode, translator) or chunk

    return sanitize_filename_stem(_CJK_RUN.sub(replace, stem))


def strip_cad_suffix(name: str) -> str:
    text = str(name or "").strip()
    lower = text.lower()
    if lower.endswith(".dxf") or lower.endswith(".dwg"):
        return text[: text.rfind(".")]
    return text


def build_output_name(
    mode: str,
    base: str = "",
    *,
    translate_filename: bool = False,
    translator: CADChineseTranslator | None = None,
) -> str:
    prefix = output_prefix(mode)
    ts = datetime.now().strftime("%Hh%M_%d-%m-%y")
    stem = strip_cad_suffix(base)
    if translate_filename and stem:
        if translator is None:
            translator = CADChineseTranslator()
        stem = translate_cjk_filename_stem(stem, mode=mode, translator=translator)
    return f"{prefix}_{stem}_{ts}" if stem else f"translated_cad_{ts}"


def _as_text(value, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


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
            raise RuntimeError(dwg_unavailable_short())
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


def _read_dxf(path: str):
    try:
        return ezdxf.readfile(path)
    except Exception as exc:
        raise ValueError("无法读取DXF文件") from exc


def ensure_output_dir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        raise ValueError("输出目录无法创建") from exc


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


def keep_extracted_item(
    doc,
    item: dict,
    *,
    include_model: bool = True,
    include_paper: bool = True,
    include_frozen: bool = False,
    include_locked: bool = False,
    include_off: bool = False,
) -> bool:
    location = item.get("location") or ""
    model = _is_model(location)
    if model and not include_model:
        return False
    if not model and not include_paper:
        return False
    layer = _as_text(item.get("layer"), "0")
    return _layer_allowed(
        doc,
        layer,
        include_frozen=include_frozen,
        include_locked=include_locked,
        include_off=include_off,
    )


def item_passes_text_filters(
    item: dict,
    *,
    skip_numbers: bool = True,
    skip_dupes: bool = True,
    skip_nonsource: bool = True,
    translation_mode: str = "zh_to_en",
) -> bool:
    source = _as_text(item.get("source") or item.get("original_text"))
    if skip_dupes and item.get("duplicate"):
        return False
    if skip_numbers and source and re.fullmatch(r"[\d.\-\s]+", source):
        return False
    if skip_nonsource and source:
        try:
            source_lang, _unused = split_mode(translation_mode)
        except ValueError:
            source_lang = "zh-Hans"
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", source))
        if str(source_lang).startswith("zh") and not has_cjk:
            return False
        if source_lang in {"en", "de", "fr"} and has_cjk:
            return False
    return True


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
    skip_numbers: bool = False,
    skip_dupes: bool = False,
    skip_nonsource: bool = False,
) -> dict:
    translator = CADChineseTranslator()
    translator.enable_v02_entities = bool(enable_v02)
    with open_work_dxf(path) as work_dxf:
        doc = _read_dxf(work_dxf)
        raw = translator.extract_text_entities(
            doc,
            mode,
            include_blocks=include_blocks,
            include_attribs=include_attribs,
            include_model=include_model,
            include_paper=include_paper,
            include_frozen=include_frozen,
            include_locked=include_locked,
            include_off=include_off,
            skip_numbers=False,
            skip_dupes=False,
            skip_nonsource=False,
        )
        rows = []
        seen = {}
        for index, item in enumerate(raw):
            source = _as_text(item.get("original_text"))
            kind = _as_text(item.get("type")).upper()
            allowed = V01_TYPES | V02_TYPES if enable_v02 else V01_TYPES
            if kind not in allowed:
                continue
            if not include_attribs and kind in {"ATTRIB", "ATTDEF"}:
                continue
            if not keep_extracted_item(
                doc,
                item,
                include_model=include_model,
                include_paper=include_paper,
                include_frozen=include_frozen,
                include_locked=include_locked,
                include_off=include_off,
            ):
                continue
            location = item.get("location") or ""
            layer = _as_text(item.get("layer"), "0")
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
        rows = [
            row
            for row in rows
            if item_passes_text_filters(
                row,
                skip_numbers=skip_numbers,
                skip_dupes=skip_dupes,
                skip_nonsource=skip_nonsource,
                translation_mode=mode,
            )
        ]
        for index, row in enumerate(rows):
            row["id"] = index
        seen = {row["source"]: index for index, row in enumerate(rows) if not row.get("duplicate")}
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
    skip_numbers: bool = False,
    skip_dupes: bool = False,
    skip_nonsource: bool = False,
    use_glossary: bool = True,
) -> dict:
    split_mode(mode)
    items = [
        item
        for item in items
        if item_passes_text_filters(
            item,
            skip_numbers=skip_numbers,
            skip_dupes=skip_dupes,
            skip_nonsource=skip_nonsource,
            translation_mode=mode,
        )
    ]
    translator = CADChineseTranslator()
    translator.configure_language_assets(project_package_path)
    translator.use_glossary = use_glossary
    engine = engine or {}
    translator.configure_engine(provider, **{k: engine.get(k, "") for k in ENGINE_KEYS})
    has_mt = translator.has_mt()
    out = []
    glossary = 0
    mt = 0
    skipped = 0
    for item in items:
        row = dict(item)
        source = _as_text(row.get("source"))
        layer = _as_text(row.get("layer"))
        kind = _as_text(row.get("type")).upper()
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

        if kind in {"MTEXT", "MULTILEADER"} and raw:
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
    style: str = "纯译文",
) -> dict:
    if not path or not os.path.isfile(path):
        raise FileNotFoundError("图纸不存在")
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
    style = normalize_pdf_style(style)
    output_dir = output_dir or default_output_dir()
    ensure_output_dir(output_dir)
    meta = analyze_source(path)
    if not output_name.strip():
        output_name = f"{output_prefix(mode)}_{Path(path).stem}"
    output_file = output_path_for(meta, output_dir, output_name.strip(), mode=mode)
    translator = CADChineseTranslator()
    translator.enable_v02_entities = False

    def log(message, level="INFO"):
        _ = level
        translator.safe_log(message)

    with CadConversionSession(path, log, "source", "") as session:
        work_input = session.work_input
        work_output = session.work_output_path() or output_file
        doc = _read_dxf(work_input)
        written = 0
        missing = 0
        if style != "纯译文":
            missing = sum(1 for item in writable if _entity_from_handle(doc, item.get("handle") or "") is None)
            written = apply_pdf_style(doc, writable, style)
        else:
            for item in writable:
                entity = _entity_from_handle(doc, item.get("handle") or "")
                if entity is None:
                    missing += 1
                    continue
                field = item.get("field") or "text"
                kind = entity.dxftype()
                target = item.get("target") or ""
                target_raw = item.get("target_raw") or ""
                formatted = (
                    bool(target_raw)
                    and ("\\" in target_raw or "{" in target_raw)
                    and item.get("via") != "edit"
                )
                if kind == "MTEXT" and formatted:
                    translator._write_mtext_entity(entity, target_raw)
                elif kind == "MULTILEADER" and formatted:
                    translator.write_back_translation(entity, target_raw, field)
                else:
                    translator.write_back_translation(entity, target, field)
                if field == "tag":
                    translator._sync_attrib_tags(doc, item.get("source") or "", target)
                written += 1
        rewrite_shx_styles(doc, translator.safe_log)
        try:
            with atomic_output_path(work_output) as temporary_output:
                doc.saveas(temporary_output)
            if session.meta.is_dwg or session.output_needs_oda:
                session.finalize(work_output, output_file)
        except Exception:
            raise ValueError("文件保存失败") from None
    return {
        "path": output_file,
        "written": written,
        "missing": missing,
        "skipped": len(items) - len(writable),
        "style": style,
    }


def table_csv_for_path(path: str, items: list[dict] | None = None, *, mode: str = "zh_to_en") -> dict:
    if items is None:
        items = extract_preview(path, mode=mode, **REGULAR_EXTRACT)["items"]
    name = Path(path).name if path else "drawing.dxf"
    csv_text = export_table_csv(items, name)
    stem = strip_cad_suffix(Path(name).name) or "drawing"
    return {"csv": csv_text, "filename": f"{stem}.csv", "count": len(items), "items": items}


def preview_table_csv(items: list[dict], csv_text: str, file_name: str = "") -> dict:
    imported = parse_table_csv(csv_text)
    if not imported:
        raise ValueError("表格是空的")
    filled, applied = apply_table_rows(items, imported, file_name=file_name)
    return {"items": filled, "applied": applied, "imported": len(imported)}


def import_table_writeback(
    files: list[str],
    csv_text: str,
    *,
    output_dir: str = "",
    mode: str = "zh_to_en",
    style: str = "纯译文",
    translate_filename: bool = False,
) -> dict:
    imported = parse_table_csv(csv_text)
    if not imported:
        raise ValueError("表格是空的")
    paths = [path for path in files if path]
    if not paths:
        raise ValueError("请选择 CAD 文件")
    results = []
    written_total = 0
    for path in paths:
        if not os.path.isfile(path):
            raise FileNotFoundError("图纸不存在")
        preview = extract_preview(path, mode=mode, **REGULAR_EXTRACT)
        filled, applied = apply_table_rows(preview["items"], imported, file_name=Path(path).name)
        if applied == 0:
            results.append({"file": Path(path).name, "written": 0, "applied": 0, "message": "表格对不上这张图"})
            continue
        named = build_output_name(mode, Path(path).stem, translate_filename=translate_filename)
        written = writeback_rows(
            path,
            filled,
            output_dir=output_dir,
            output_name=named,
            mode=mode,
            include_blocks=REGULAR_EXTRACT["include_blocks"],
            style=style,
        )
        written_total += int(written.get("written") or 0)
        results.append({**written, "file": Path(path).name, "applied": applied})
    if written_total == 0:
        raise ValueError("没有可写回的译文")
    return {"results": results, "written": written_total, "files": len(results)}


def translate_drawing(
    path: str,
    *,
    mode: str = "zh_to_en",
    output_dir: str = "",
    output_name: str = "",
    translate_filename: bool = False,
    project_package_path: str = "",
    provider: str = "deepl",
    engine: dict | None = None,
    style: str = "纯译文",
) -> dict:
    """Open → extract → glossary-first translate → write-back. Same as 常规 写回."""
    split_mode(mode)
    engine = engine or {}
    preview = extract_preview(path, mode=mode, **REGULAR_EXTRACT)
    translated = translate_rows(
        preview["items"],
        mode=mode,
        provider=provider,
        project_package_path=project_package_path,
        engine=engine,
        skip_numbers=REGULAR_EXTRACT["skip_numbers"],
        skip_dupes=REGULAR_EXTRACT["skip_dupes"],
        skip_nonsource=REGULAR_EXTRACT["skip_nonsource"],
    )
    named = strip_cad_suffix(output_name)
    if not named:
        translator = CADChineseTranslator(log_callback=lambda *args, **kwargs: None)
        translator.configure_language_assets(project_package_path)
        translator.configure_engine(provider, **{k: engine.get(k, "") for k in ENGINE_KEYS})
        named = build_output_name(
            mode,
            Path(path).stem,
            translate_filename=translate_filename,
            translator=translator,
        )
    if not any((item.get("target") or "").strip() for item in translated["items"]):
        if translated.get("has_engine"):
            raise ValueError("没有可写回的译文")
        raise ValueError("没有可写回的译文。术语表没命中，也没有翻译引擎")
    written = writeback_rows(
        path,
        translated["items"],
        output_dir=output_dir,
        output_name=named,
        mode=mode,
        include_blocks=REGULAR_EXTRACT["include_blocks"],
        style=style,
    )
    return {
        "path": written["path"],
        "extracted": preview["count"],
        "translated": written["written"],
        "glossary": translated["glossary"],
        "mt": translated["mt"],
        "skipped": translated["skipped"],
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


PDF_STYLES = {"纯译文", "原译对照", "译原对照"}


def normalize_pdf_style(style: str) -> str:
    text = str(style or "").strip() or "纯译文"
    return text if text in PDF_STYLES else "纯译文"


def _pair_labels(source: str, target: str, style: str) -> tuple[str, str] | None:
    src = str(source or "").strip()
    tgt = str(target or "").strip()
    if not tgt or src == tgt:
        return None
    if style == "译原对照":
        return tgt, src or tgt
    if style == "原译对照":
        return src or tgt, tgt
    return None


def _label_payload(item: dict, side: str) -> str:
    if side == "source":
        coded = str(item.get("raw") or item.get("raw_source") or "").strip()
        plain = str(item.get("source") or "").strip()
    else:
        coded = str(item.get("target_raw") or "").strip()
        plain = str(item.get("target") or "").strip()
    if coded and ("\\" in coded or "{" in coded):
        return coded
    return plain or coded


def _stack_text_entity(entity, first: str, second: str) -> None:
    entity.dxf.text = first
    try:
        layout = entity.get_layout()
    except Exception:
        layout = None
    if layout is None:
        entity.dxf.text = f"{first} / {second}"
        return
    height = float(getattr(entity.dxf, "height", 2.5) or 2.5)
    insert = entity.dxf.insert
    attribs = {
        "insert": (insert.x, insert.y - height * 1.3, getattr(insert, "z", 0)),
        "height": height,
        "layer": entity.dxf.layer,
    }
    try:
        attribs["rotation"] = entity.dxf.rotation
    except Exception:
        pass
    try:
        attribs["style"] = entity.dxf.style
    except Exception:
        pass
    layout.add_text(second, dxfattribs=attribs)


def apply_pdf_style(doc, items: list[dict] | None, style: str) -> int:
    """Stamp 对照 labels onto an in-memory drawing (PDF or 写回)."""
    style = normalize_pdf_style(style)
    if style == "纯译文" or not items:
        return 0
    table_sources = {
        str(item.get("source") or "")
        for item in items
        if str(item.get("type") or "").upper() == "ACAD_TABLE"
    }
    applied = 0
    writer = None
    for item in items:
        pair = _pair_labels(item.get("source") or "", item.get("target") or "", style)
        if not pair:
            continue
        entity = _entity_from_handle(doc, item.get("handle") or "")
        if entity is None:
            continue
        first, second = pair
        kind = entity.dxftype()
        field = str(item.get("field") or "text")
        location = str(item.get("location") or "")
        source = str(item.get("source") or "")
        if (
            kind in {"TEXT", "MTEXT"}
            and source in table_sources
            and location.startswith("*")
        ):
            # *T preview of a table we already stamp via group-code 302.
            continue
        if kind in {"MTEXT", "MULTILEADER"}:
            if style == "译原对照":
                first, second = _label_payload(item, "target"), _label_payload(item, "source")
            else:
                first, second = _label_payload(item, "source"), _label_payload(item, "target")
        two = f"{first}\\P{second}"
        try:
            if kind == "TEXT" and field == "text":
                _stack_text_entity(entity, first, second)
            elif kind == "MTEXT" and field == "text":
                entity.dxf.text = two
            elif kind == "MULTILEADER" and field == "mtext":
                if hasattr(entity, "set_mtext_content"):
                    entity.set_mtext_content(two)
                else:
                    continue
            elif kind in {"ATTRIB", "ATTDEF"} and field == "text":
                # ATTRIB/ATTDEF are single-line AutoCAD attributes; \P is not valid here.
                entity.dxf.text = f"{first} / {second}"
            elif kind == "DIMENSION" and field == "text":
                entity.dxf.text = f"{first} / {second}"
            elif kind == "ACAD_TABLE" and field.startswith("table:"):
                if writer is None:
                    writer = CADChineseTranslator()
                writer._write_acad_table_text_slot(entity, field[6:], f"{first} / {second}")
            else:
                continue
        except Exception:
            continue
        applied += 1
    return applied


def export_pdf(path: str, output_path: str = "", layout_name: str = "", *, style: str = "纯译文", items=None) -> dict:
    """DWG → ODA → DXF → PDF via ezdxf drawing (matplotlib). Not AutoCAD plot quality."""
    dest = Path(output_path) if output_path else Path(default_output_dir()) / f"{Path(path).stem}.pdf"
    ensure_output_dir(str(dest.parent))
    style = normalize_pdf_style(style)
    register_cjk_font()
    with open_work_dxf(path) as work_dxf:
        doc = _read_dxf(work_dxf)
        rewrite_shx_styles(doc)
        if style != "纯译文":
            apply_pdf_style(doc, items or [], style)
        pages = _layout_pages(doc, layout_name)
        _render_pdf(pages, dest)
    if not dest.is_file() or dest.stat().st_size < 8:
        raise RuntimeError("PDF 导出失败")
    return {
        "path": str(dest),
        "pages": len(pages),
        "bytes": dest.stat().st_size,
        "cad_path": str(path),
        "style": style,
    }


def _render_pdf(layouts, dest: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    register_cjk_font()
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from ezdxf.addons.drawing import Frontend
    from ezdxf.addons.drawing.config import Configuration
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.properties import LayoutProperties, RenderContext

    dpi = 150
    try:
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
    except (ValueError, RuntimeError):
        raise
    except OSError:
        raise ValueError("文件保存失败") from None


PRINT_TIMEOUT = 5.0
NO_PRINTER = "系统没有打印命令，已留下 PDF"


def _print_binaries() -> list[str]:
    names = ("lpr", "lp") if sys.platform == "darwin" else ("lp", "lpr")
    found = []
    for name in names:
        path = shutil.which(name)
        if path:
            found.append(path)
    return found


def _print_fail_message(stderr: str = "", stdout: str = "") -> str:
    text = (stderr or stdout or "").strip()
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    if line and "Traceback" not in line and "Errno" not in line and any("\u4e00" <= char <= "\u9fff" for char in line):
        return f"打印失败: {line[:200]}"
    return "打印失败，PDF 已留下"


def print_pdf(pdf_path: str) -> dict:
    if not pdf_path or not os.path.isfile(pdf_path):
        raise FileNotFoundError("PDF 不存在")
    if sys.platform == "win32":
        try:
            os.startfile(pdf_path, "print")  # type: ignore[attr-defined]
            return {"ok": True, "path": pdf_path, "command": "print"}
        except OSError:
            return {"ok": False, "path": pdf_path, "message": NO_PRINTER}
    binaries = _print_binaries()
    if not binaries:
        return {"ok": False, "path": pdf_path, "message": NO_PRINTER}
    last = NO_PRINTER
    for binary in binaries:
        try:
            result = subprocess.run(
                [binary, pdf_path],
                check=False,
                capture_output=True,
                text=True,
                timeout=PRINT_TIMEOUT,
            )
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return {"ok": False, "path": pdf_path, "message": "系统打印超时，PDF 已留下"}
        except OSError:
            continue
        if result.returncode == 0:
            return {"ok": True, "path": pdf_path, "command": f"{binary} {pdf_path}"}
        last = _print_fail_message(result.stderr, result.stdout)
    return {"ok": False, "path": pdf_path, "message": last}
