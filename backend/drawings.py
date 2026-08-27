"""Extract a JSON-safe text table from a DWG/DXF for the 常规处理 grid."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import ezdxf

from backend.cad import dwg_to_work_dxf, odafc_available
from backend.translator import CADChineseTranslator


def extract_preview(path: str, *, include_blocks: bool = False, mode: str = "zh_to_en") -> dict:
    if not path or not os.path.isfile(path):
        raise FileNotFoundError("图纸不存在")
    suffix = Path(path).suffix.lower()
    if suffix not in {".dxf", ".dwg"}:
        raise ValueError("只支持 DWG / DXF")

    translator = CADChineseTranslator()
    work_dxf = path
    tmp = None
    if suffix == ".dwg":
        if not odafc_available():
            raise RuntimeError("未检测到 ODA，无法打开 DWG。请安装 ODA 或另存为 DXF。")
        handle, tmp = tempfile.mkstemp(suffix=".dxf")
        os.close(handle)
        dwg_to_work_dxf(path, tmp)
        work_dxf = tmp
    try:
        doc = ezdxf.readfile(work_dxf)
        raw = translator.extract_text_entities(doc, mode, include_blocks=include_blocks)
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass

    rows = []
    seen = {}
    for index, item in enumerate(raw):
        source = item.get("original_text") or ""
        skip = False
        if source in seen:
            skip = True
        else:
            seen[source] = index
        rows.append(
            {
                "id": index,
                "source": source,
                "target": "",
                "layer": item.get("layer") or "0",
                "type": item.get("type") or "",
                "location": item.get("location") or "",
                "duplicate": skip,
            }
        )
    return {"path": path, "count": len(rows), "unique": len(seen), "items": rows}
