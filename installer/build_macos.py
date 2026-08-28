"""Build the unsigned macOS app. ODA is not copied into the bundle."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pack_version import ROOT, app_version

OUTPUT_APP = ROOT / "dist" / "Dwglot.app"
APP_EXECUTABLE = OUTPUT_APP / "Contents" / "MacOS" / "Dwglot"
CLI_EXECUTABLE = OUTPUT_APP / "Contents" / "MacOS" / "dwglot-cli"
ICNS_PATH = ROOT / "build" / "Dwglot.icns"
PNG_ICON = ROOT / "docs" / "icons" / "app.png"


def run(*command: str, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def architectures(binary: Path) -> set[str]:
    output = subprocess.check_output(["lipo", "-archs", str(binary)], text=True)
    return set(output.strip().split())


def write_icns() -> None:
    if sys.platform != "darwin" or not PNG_ICON.is_file():
        return
    iconset = ROOT / "build" / "Dwglot.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True, exist_ok=True)
    for size, retina in (
        (16, False),
        (16, True),
        (32, False),
        (32, True),
        (128, False),
        (128, True),
        (256, False),
        (256, True),
        (512, False),
        (512, True),
    ):
        pixel = size * (2 if retina else 1)
        name = f"icon_{size}x{size}{'@2x' if retina else ''}.png"
        run("sips", "-z", str(pixel), str(pixel), str(PNG_ICON), "--out", str(iconset / name))
    ICNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    run("iconutil", "-c", "icns", "-o", str(ICNS_PATH), str(iconset))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--identity",
        default="-",
        help="Developer ID Application identity; '-' creates a local ad-hoc signature",
    )
    parser.add_argument("--skip-frontend", action="store_true")
    parser.add_argument("--dmg", action="store_true", help="Create a compressed distributable DMG after building the app")
    parser.add_argument("--dmg-output", type=Path, help="DMG output path (requires --dmg)")
    args = parser.parse_args()

    if sys.platform != "darwin":
        raise SystemExit("macOS pack must run on macOS")
    if "--oda-dmg" in sys.argv:
        raise SystemExit("ODA is not packed. Install ODA File Converter yourself.")

    version = app_version()

    if not args.skip_frontend:
        run("npm", "ci", cwd=ROOT / "frontend")
        run("npm", "run", "build", cwd=ROOT / "frontend")

    try:
        write_icns()
    except subprocess.CalledProcessError:
        if ICNS_PATH.exists():
            ICNS_PATH.unlink()

    build_env = os.environ.copy()
    if args.identity != "-":
        build_env["MACOS_CODESIGN_IDENTITY"] = args.identity
    else:
        build_env.pop("MACOS_CODESIGN_IDENTITY", None)
    run(
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "Dwglot_macos.spec",
        env=build_env,
    )
    if not APP_EXECUTABLE.is_file():
        raise SystemExit(f"macOS build output is missing: {APP_EXECUTABLE}")
    if not CLI_EXECUTABLE.is_file():
        raise SystemExit(f"macOS CLI is missing: {CLI_EXECUTABLE}")
    if (OUTPUT_APP / "Contents" / "Resources" / "ODAFileConverter.dmg").exists():
        raise SystemExit("ODA DMG must not be inside the app")

    sign_command = ["codesign", "--force", "--deep", "--sign", args.identity]
    if args.identity != "-":
        sign_command.extend(["--options", "runtime", "--timestamp"])
    sign_command.append(str(OUTPUT_APP))
    run(*sign_command)
    run("codesign", "--verify", "--deep", "--strict", "--verbose=2", str(OUTPUT_APP))

    app_arches = architectures(APP_EXECUTABLE)
    if args.dmg_output and not args.dmg:
        raise SystemExit("--dmg-output requires --dmg")
    if args.dmg:
        arch = next(iter(sorted(app_arches)))
        dmg_output = (args.dmg_output or ROOT / "dist" / f"Dwglot_v{version}_macOS_{arch}.dmg")
        dmg_output = dmg_output.expanduser().resolve()
        if dmg_output.suffix.lower() != ".dmg":
            raise SystemExit(f"DMG output must end in .dmg: {dmg_output}")
        dmg_output.parent.mkdir(parents=True, exist_ok=True)
        run(
            "hdiutil",
            "create",
            "-volname",
            "图译",
            "-srcfolder",
            str(OUTPUT_APP),
            "-ov",
            "-format",
            "UDZO",
            str(dmg_output),
        )
        print(f"DMG: {dmg_output}")

    print(f"Built: {OUTPUT_APP}")
    print(f"Version: {version}")
    print(f"App architectures: {', '.join(sorted(app_arches))}")


if __name__ == "__main__":
    main()
