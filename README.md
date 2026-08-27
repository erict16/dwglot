<h4 align="right">English | <strong><a href="README_CN.md">简体中文</a></strong></h4>

<p align="center">
  <img src="docs/screenshots/mark.svg" width="88" alt="图译" />
  <h1 align="center">图译 Dwglot</h1>
  <div align="center">
    <a href="https://github.com/erict16/dwglot/releases"><img alt="GitHub release" src="https://img.shields.io/github/v/release/erict16/dwglot?style=flat-square"></a>
    <img alt="macOS 11+" src="https://img.shields.io/badge/macOS-11%2B-orange?style=flat-square">
    <img alt="Windows 10+" src="https://img.shields.io/badge/Windows-10%2B-blue?style=flat-square">
    <img alt="MIT" src="https://img.shields.io/badge/license-MIT-green?style=flat-square">
  </div>
  <div align="center">Open-source desktop <strong>CAD translator</strong> for <strong>DWG</strong> / <strong>DXF</strong> <strong>drawing translation</strong> (图纸翻译). No AutoCAD.</div>
</p>

<p align="center">
  <img src="docs/screenshots/regular.png" width="900" alt="图译 regular processing: original and translation table" />
</p>
<p align="center">
  <img src="docs/screenshots/export.png" width="48%" alt="Batch export" />
  <img src="docs/screenshots/params.png" width="48%" alt="Parameters sheet" />
</p>

## Features

- **Accurate:** CAD glossary hits skip the MT engine. Cloud (Azure / DeepL), local (Ollama), or a custom OpenAI-compatible API.
- **Complete:** Open DWG/DXF, edit 原文 | 译文, batch export, write back. TEXT / MTEXT / attribs in v0.1.
- **Local:** No telemetry. No paid licence. Keys stay on your machine.
- **Light:** We do not bundle ODA File Converter. DXF translates as-is. DWG needs your own ODA install.

## Installation

v0.1 is **unsigned**. Grab the latest Mac DMG or Windows EXE from [GitHub Releases](https://github.com/erict16/dwglot/releases). In-app **检查更新** hits the same Releases feed (Sparkle / WinSparkle when signed later; until then it opens the new package).

**macOS first run (Gatekeeper):** Control-click the app, choose Open. Or System Settings → Privacy & Security → Open Anyway. Later, a Developer ID will quiet this.

**Windows first run (SmartScreen):** More info → Run anyway. Authenticode later will quiet this.

Do not pack with UPX. DXF works without ODA. For DWG, install [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter) yourself (or set `CAD_ODA_EXEC`). We never ship ODA inside the app.

From source:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..
python run.py
```

Default language pair is Chinese → English. Output lands in `~/Documents/Dwglot output`.

## Notes

- Fork of [etianwang/CAD_translator](https://github.com/etianwang/CAD_translator) (MIT). Dwglot rebrands it as 图译, Mac UI, glossary + four MT plugs, auto-update.
- DIMENSION / ACAD_TABLE write-back is v0.2. Do not expect dims and tables to round-trip yet.
- Site copy: `landing/` (Tailwind Plus Salient layout, for Vercel).

## License

MIT. Copyright Eric Tan / Honsen CAD_translator contributors.
