<h4 align="right">English | <strong><a href="README_CN.md">简体中文</a></strong></h4>

<p align="center">
  <img src="docs/icons/app.png" width="88" alt="图译" />
  <h1 align="center">图译 Dwglot</h1>
  <div align="center">
    <a href="https://github.com/erict16/dwglot/releases"><img alt="GitHub release" src="https://img.shields.io/github/v/release/erict16/dwglot?style=flat-square"></a>
    <img alt="macOS 11+" src="https://img.shields.io/badge/macOS-11%2B-orange?style=flat-square">
    <img alt="Windows 10+" src="https://img.shields.io/badge/Windows-10%2B-blue?style=flat-square">
    <img alt="MIT" src="https://img.shields.io/badge/license-MIT-green?style=flat-square">
  </div>
  <div align="center">Dwglot is an open-source desktop CAD translator. Open a DWG or DXF and translate the text on the drawing (图纸翻译 / drawing translation). You don't need AutoCAD.</div>
</p>

<p align="center">
  <img src="docs/screenshots/regular.png" width="900" alt="图译 regular processing: original and translation table" />
</p>
<p align="center">
  <img src="docs/screenshots/export.png" width="48%" alt="Batch export" />
  <img src="docs/screenshots/params.png" width="48%" alt="Parameters sheet" />
</p>

## Features

- **Glossary:** If the drawing text is in the glossary, we use that translation. Everything else can use Azure or DeepL, Ollama on your machine, or your own OpenAI-compatible API.
- **Drawings:** Open a DWG or DXF, edit the original and the translation side by side, then batch-export and write back. v0.1 covers TEXT, MTEXT, and attributes.
- **Keys:** No telemetry, no paid licence. API keys stay on your computer.
- **ODA:** The installer does not include ODA File Converter. DXF translates without it. For DWG, install ODA yourself.

## Installation

v0.1 is unsigned. Download the latest Mac DMG or Windows EXE from [GitHub Releases](https://github.com/erict16/dwglot/releases). In-app **检查更新** uses the same Releases page. Sparkle / WinSparkle come later, after signing; for now it opens the new package.

**macOS first run (Gatekeeper):** Right-click the app, choose Open. Or System Settings → Privacy & Security → Open Anyway. A Developer ID later will stop this warning.

**Windows first run (SmartScreen):** More info → Run anyway. Authenticode later will stop this warning.

Do not pack with UPX. DXF works without ODA. For DWG, install [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter) yourself, or set `CAD_ODA_EXEC`. We never put ODA inside the app.

From source:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..
python run.py
```

Default language pair is Chinese → English. Output goes to `~/Documents/Dwglot output`.

## Notes

- Fork of [etianwang/CAD_translator](https://github.com/etianwang/CAD_translator) (MIT). Dwglot is the 图译 fork. It has a Mac UI, four translation engines, and auto-update.
- DIMENSION / ACAD_TABLE write-back is v0.1. Dims and tables round-trip now.
- The website is in `landing/` (for Vercel).

## License

MIT. Copyright Eric Tan / Honsen CAD_translator contributors.
