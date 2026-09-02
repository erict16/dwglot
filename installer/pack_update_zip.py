"""Zip dist/Tuyi for in-app update. Setup.exe is first install only."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from pack_version import ROOT, app_version

PAYLOAD = ROOT / "dist" / "Tuyi"


def zip_path(version: str | None = None) -> Path:
    return ROOT / "dist" / f"Tuyi_v{version or app_version()}_windows_x64.zip"


def main() -> None:
    version = app_version()
    if not (PAYLOAD / "Tuyi.exe").is_file():
        raise SystemExit(f"missing {PAYLOAD / 'Tuyi.exe'}")
    if not (PAYLOAD / "_internal").is_dir():
        raise SystemExit(f"missing {PAYLOAD / '_internal'}")
    target = zip_path(version)
    if target.exists():
        target.unlink()
    shutil.make_archive(str(target.with_suffix("")), "zip", root_dir=PAYLOAD)
    if not target.is_file():
        raise SystemExit(f"zip was not written: {target}")
    print(target)


if __name__ == "__main__":
    sys.exit(main())
