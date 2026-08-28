# -*- mode: python ; coding: utf-8 -*-
"""macOS application bundle; the Windows spec remains independent."""
import os

from PyInstaller.utils.hooks import collect_submodules

spec_dir = os.path.dirname(os.path.abspath(SPEC))
codesign_identity = os.environ.get("MACOS_CODESIGN_IDENTITY") or None
data_files = ["changelog.json", "license_public_key.txt"]
glossary_files = [
    "translation_abbreviations.yaml", "translation_context.yaml",
    "translation_context_fr_to_zh.yaml", "translation_context_zh_to_en.yaml",
    "translation_context_en_to_zh.yaml", "translation_corrections.yaml",
]
datas = [(os.path.join(spec_dir, name), ".") for name in data_files]
datas += [(os.path.join(spec_dir, "glossaries", name), "glossaries") for name in glossary_files]
frontend_dist = os.path.join(spec_dir, "frontend", "dist")
for folder, _, names in os.walk(frontend_dist):
    for name in names:
        source = os.path.join(folder, name)
        destination = os.path.join("frontend", "dist", os.path.relpath(folder, frontend_dist))
        datas.append((source, destination))

hiddenimports = [
    "backend.api", "backend.cad", "backend.language_assets", "backend.licensing",
    "backend.providers.azure", "backend.queue", "backend.storage",
    "backend.text_cleaning", "backend.translator", "desktop.launcher",
    "desktop.native_bridge", "python_multipart", "ezdxf.addons.odafc",
] + collect_submodules("uvicorn") + collect_submodules("starlette")

a = Analysis(
    ["run.py"], pathex=[spec_dir], binaries=[], datas=datas,
    hiddenimports=hiddenimports, excludes=["tkinter", "_tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Honsen_CAD_Translator_v1.8.8",
    console=False,
    target_arch=None,
    codesign_identity=codesign_identity,
)
collection = COLLECT(
    exe, a.binaries, a.datas,
    name="Honsen_CAD_Translator_v1.8.8",
)
app = BUNDLE(
    collection,
    name="Honsen CAD Translator.app",
    bundle_identifier="com.honsen.cad-translator",
    codesign_identity=codesign_identity,
    info_plist={
        "CFBundleDisplayName": "Honsen CAD Translator",
        "CFBundleShortVersionString": "1.8.8",
        "CFBundleVersion": "1.8.8",
        "NSHighResolutionCapable": True,
    },
)
