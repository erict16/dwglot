# -*- mode: python ; coding: utf-8 -*-
"""Windows one-file desktop build. ODA File Converter is NOT in this payload."""
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

spec_dir = os.path.dirname(os.path.abspath(SPEC))
glossary_files = [
    "translation_abbreviations.yaml",
    "translation_context.yaml",
    "translation_context_fr_to_zh.yaml",
    "translation_context_zh_to_en.yaml",
    "translation_context_en_to_zh.yaml",
    "translation_corrections.yaml",
]
datas = [(os.path.join(spec_dir, "changelog.json"), ".")]
datas += [(os.path.join(spec_dir, "glossaries", name), "glossaries") for name in glossary_files]
font = os.path.join(spec_dir, "fonts", "NotoSansSC-Regular.otf")
if os.path.isfile(font):
    datas.append((font, "fonts"))
for folder, _, names in os.walk(os.path.join(spec_dir, "frontend", "dist")):
    for name in names:
        source = os.path.join(folder, name)
        dest = os.path.join(
            "frontend",
            "dist",
            os.path.relpath(folder, os.path.join(spec_dir, "frontend", "dist")),
        )
        datas.append((source, dest))
datas += collect_data_files("ezdxf")
datas += collect_data_files("matplotlib")

hiddenimports = [
    "backend.api",
    "backend.app_meta",
    "backend.cad",
    "backend.cli",
    "backend.drawings",
    "backend.language_assets",
    "backend.languages",
    "backend.mtext_runs",
    "backend.providers.azure",
    "backend.providers.base",
    "backend.providers.deepl_provider",
    "backend.providers.ollama",
    "backend.providers.openai_compat",
    "backend.queue",
    "backend.storage",
    "backend.styles",
    "backend.text_cleaning",
    "backend.translator",
    "backend.updates",
    "desktop.launcher",
    "desktop.native_bridge",
    "python_multipart",
    "ezdxf.addons.odafc",
] + collect_submodules("uvicorn") + collect_submodules("starlette")

icon = [os.path.join(spec_dir, "ico.ico")]

a = Analysis(["run.py"], pathex=[spec_dir], binaries=[], datas=datas, hiddenimports=hiddenimports)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Dwglot",
    console=False,
    icon=icon,
)

# Console CLI next to the GUI. Named dwglot-cli so Windows NTFS does not
# collide with Dwglot.exe. Same payload; ODA is still not bundled.
cli_a = Analysis(["backend/cli.py"], pathex=[spec_dir], binaries=[], datas=datas, hiddenimports=hiddenimports)
cli_pyz = PYZ(cli_a.pure)
cli_exe = EXE(
    cli_pyz,
    cli_a.scripts,
    cli_a.binaries,
    cli_a.datas,
    [],
    name="dwglot-cli",
    console=True,
    icon=icon,
)
