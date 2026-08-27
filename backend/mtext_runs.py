"""Split AutoCAD MTEXT into formatting codes vs translatable runs."""

from __future__ import annotations

import re

# Keep \\P, \\~, braces, and parameterised codes such as \\C1; \\fSimSun|...;
TOKEN = re.compile(
    r"("
    r"\\\\"
    r"|\\P"
    r"|\\p[^;]*;"
    r"|\\~"
    r"|\\[{}\\]"
    r"|\\[A-Za-z][^;\\]*;"
    r"|[{}]"
    r")"
)


def is_format_token(part: str) -> bool:
    if not part:
        return True
    if part.isspace():
        return True
    return TOKEN.fullmatch(part) is not None


def map_translatable(raw: str, translate_fn) -> str:
    """Translate visible runs and leave MTEXT codes untouched."""
    if raw is None:
        return ""
    pieces = TOKEN.split(str(raw))
    out = []
    for piece in pieces:
        if piece is None or piece == "":
            continue
        if is_format_token(piece):
            out.append(piece)
        else:
            out.append(translate_fn(piece))
    return "".join(out)
