"""CSV table for 批量导入: extract → fill targets → write back."""

from __future__ import annotations

import csv
import io
from pathlib import Path

CSV_FIELDS = ("file", "handle", "field", "source", "target", "layer", "type")
_ALIASES = {
    "file": {"file", "filename", "path", "文件"},
    "handle": {"handle", "句柄"},
    "field": {"field", "字段"},
    "source": {"source", "src", "原文"},
    "target": {"target", "dst", "译文", "translation"},
    "layer": {"layer", "图层"},
    "type": {"type", "kind", "类型"},
}


def _norm(value) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _cell(value) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def _header_map(row: list[str]) -> dict[str, int] | None:
    lowered = [_norm(cell) for cell in row]
    mapping: dict[str, int] = {}
    for field, aliases in _ALIASES.items():
        for index, cell in enumerate(lowered):
            if cell in aliases:
                mapping[field] = index
                break
    if "source" in mapping or "target" in mapping:
        return mapping
    return None


def export_table_csv(items: list[dict], file_name: str = "") -> str:
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_FIELDS)
    for item in items or []:
        writer.writerow(
            [
                file_name or _cell(item.get("file")),
                _cell(item.get("handle")),
                _cell(item.get("field") or "text"),
                _cell(item.get("source")),
                _cell(item.get("target")),
                _cell(item.get("layer")),
                _cell(item.get("type")),
            ]
        )
    return buffer.getvalue()


def parse_table_csv(text: str) -> list[dict]:
    raw = (text or "").lstrip("\ufeff")
    if not raw.strip():
        return []
    reader = csv.reader(io.StringIO(raw))
    rows = [row for row in reader if any(str(cell).strip() for cell in row)]
    if not rows:
        return []
    header = _header_map(rows[0])
    body = rows[1:] if header else rows
    if header is None:
        header = {"source": 0, "target": 1 if rows[0] and len(rows[0]) > 1 else 0}
        body = rows
    imported = []
    for row in body:
        source = row[header["source"]].strip() if header.get("source") is not None and header["source"] < len(row) else ""
        target = row[header["target"]].strip() if header.get("target") is not None and header["target"] < len(row) else ""
        if not source or not target:
            continue
        if _norm(source) in {"source", "原文"} and _norm(target) in {"target", "译文"}:
            continue
        item = {
            "file": row[header["file"]].strip() if header.get("file") is not None and header["file"] < len(row) else "",
            "handle": row[header["handle"]].strip() if header.get("handle") is not None and header["handle"] < len(row) else "",
            "field": row[header["field"]].strip() if header.get("field") is not None and header["field"] < len(row) else "text",
            "source": source,
            "target": target,
            "layer": row[header["layer"]].strip() if header.get("layer") is not None and header["layer"] < len(row) else "",
            "type": row[header["type"]].strip() if header.get("type") is not None and header["type"] < len(row) else "",
        }
        imported.append(item)
    return imported


def _file_matches(imported_file: str, file_name: str) -> bool:
    if not imported_file or not file_name:
        return True
    left = Path(imported_file.replace("\\", "/")).name
    right = Path(file_name.replace("\\", "/")).name
    return left.casefold() == right.casefold()


def apply_table_rows(items: list[dict], imported: list[dict], file_name: str = "") -> tuple[list[dict], int]:
    """Fill extract rows from a table. Handle+field first, then exact source."""
    scoped = [row for row in imported if _file_matches(row.get("file") or "", file_name)]
    by_handle: dict[str, dict] = {}
    by_source: dict[str, dict] = {}
    for row in scoped:
        handle = (row.get("handle") or "").strip()
        field = (row.get("field") or "text").strip() or "text"
        if handle:
            by_handle[f"{handle}\t{field}"] = row
        source_key = _norm(row.get("source"))
        if source_key and source_key not in by_source:
            by_source[source_key] = row
    filled = []
    applied = 0
    for item in items or []:
        row = dict(item)
        handle = (row.get("handle") or "").strip()
        field = (row.get("field") or "text").strip() or "text"
        match = None
        if handle:
            match = by_handle.get(f"{handle}\t{field}")
        if match is None:
            match = by_source.get(_norm(row.get("source")))
        if match and (match.get("target") or "").strip():
            row["target"] = match["target"]
            row["via"] = "edit"
            row["selected"] = True
            applied += 1
        filled.append(row)
    return filled, applied
