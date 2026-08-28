# -*- mode: python ; coding: utf-8 -*-
"""macOS app bundle. GUI and CLI share the runtime. ODA is NOT in this payload."""
import os
import re

from PyInstaller.utils.hooks import collect_data_files

os.environ["MPLBACKEND"] = "Agg"

spec_dir = os.path.dirname(os.path.abspath(SPEC))
codesign_identity = os.environ.get("MACOS_CODESIGN_IDENTITY") or None
meta = open(os.path.join(spec_dir, "backend", "app_meta.py"), encoding="utf-8").read()
app_version = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', meta).group(1)

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


def _keep_ezdxf_data(src):
    path = src.replace("\\", "/")
    if path.endswith((".c", ".h", ".pyx", ".pxd")):
        return False
    if "/resources/" in path:
        return False
    return True


datas += [pair for pair in collect_data_files("ezdxf") if _keep_ezdxf_data(pair[0])]

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
    "ezdxf.addons.drawing",
    "ezdxf.addons.drawing.matplotlib",
    "matplotlib.backends.backend_agg",
    "matplotlib.backends.backend_pdf",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
]

excludes = [
    "IPython",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "tkinter",
    "matplotlib.backends.backend_gtk3agg",
    "matplotlib.backends.backend_gtk4agg",
    "matplotlib.backends.backend_nbagg",
    "matplotlib.backends.backend_qt5agg",
    "matplotlib.backends.backend_qt6agg",
    "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_tkagg",
    "matplotlib.backends.backend_webagg",
    "matplotlib.backends.backend_wxagg",
    "matplotlib.tests",
    "matplotlib.testing",
    "numpy.tests",
    "ezdxf.addons.drawing.pyqt",
    "ezdxf.addons.hpgl2",
    "uvicorn.workers",
    "uvicorn.supervisors.watchfilesreload",
    "uvicorn.supervisors.statreload",
    "watchfiles",
]

a = Analysis(
    ["run.py"],
    pathex=[spec_dir],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
)


def _keep_bundle_entry(entry):
    dest = str(entry[0]) if isinstance(entry, (tuple, list)) else str(entry)
    src = str(entry[1]) if isinstance(entry, (tuple, list)) and len(entry) > 1 else dest
    blob = (src + "|" + dest).replace("\\", "/")
    if "mpl-data/sample_data" in blob:
        return False
    if "mpl-data/plot_directive" in blob:
        return False
    if "mpl-data/images" in blob:
        return False
    if blob.endswith((".c", ".h", ".pyx", ".pxd")):
        return False
    name = dest.lower()
    if any(token in name for token in ("_tkagg", "backend_qt", "backend_gtk")):
        return False
    return True


a.datas = [entry for entry in a.datas if _keep_bundle_entry(entry)]
a.binaries = [entry for entry in a.binaries if _keep_bundle_entry(entry)]

pyz = PYZ(a.pure)

gui = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Dwglot",
    console=False,
    target_arch=None,
    codesign_identity=codesign_identity,
    upx=False,
    contents_directory="_internal",
)
cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="dwglot-cli",
    console=True,
    target_arch=None,
    codesign_identity=codesign_identity,
    upx=False,
    contents_directory="_internal",
)
coll = COLLECT(
    gui,
    cli,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Dwglot",
)
icns = os.path.join(spec_dir, "build", "Dwglot.icns")
app = BUNDLE(
    coll,
    name="Dwglot.app",
    bundle_identifier="com.erict16.dwglot",
    icon=icns if os.path.isfile(icns) else None,
    codesign_identity=codesign_identity,
    info_plist={
        "CFBundleDisplayName": "图译",
        "CFBundleName": "图译",
        "CFBundleShortVersionString": app_version,
        "CFBundleVersion": app_version,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
    },
)
