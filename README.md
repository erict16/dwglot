<p align="center">
  <img src="docs/icons/app.png" width="128" alt="Tuyi">
</p>

<h1 align="center">图译 Tuyi</h1>

<p align="center"><strong>DWG / DXF Translator</strong></p>

<p align="center">
  Open a CAD drawing, translate the text on it, write a new file.<br>
  Desktop app for Windows and macOS. You don't need AutoCAD.
</p>

<p align="center">
  <a href="https://github.com/erict16/tuyi/releases"><img alt="GitHub release" src="https://img.shields.io/github/v/release/erict16/tuyi?style=flat-square"></a>
  <img alt="Windows 10+" src="https://img.shields.io/badge/Windows-10%2B-blue?style=flat-square">
  <img alt="macOS 11+" src="https://img.shields.io/badge/macOS-11%2B-orange?style=flat-square">
  <img alt="MIT" src="https://img.shields.io/badge/license-MIT-green?style=flat-square">
</p>

<p align="center">
  <b>English</b> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.es.md">Español</a>
</p>

<p align="center">
  <img src="docs/screenshots/regular.png" width="920" alt="Tuyi: source and translation side by side">
</p>

## Install

Download from [Releases](https://github.com/erict16/tuyi/releases).

| | |
| --- | --- |
| Windows 10+ | Setup exe on the release page |
| macOS 11+ (Apple Silicon) | DMG, built by CI on a version tag |

v0.1 is unsigned.

**Windows:** More info → Run anyway.

**macOS:** Right-click the app → Open. Or System Settings → Privacy & Security → Open Anyway.

In-app **检查更新** opens the same Releases page.

## What it does

Tuyi translates text *inside* the drawing, not a screenshot of it.

- Opens **DWG** and **DXF**
- Fills a source / translation table you can edit
- Writes a new drawing
- Batch export for a folder of files
- v0.1 covers TEXT, MTEXT, attributes, DIMENSION overrides, and ACAD_TABLE cells

Glossary hits are used as-is. The rest can go through Azure Translator, DeepL, a local Ollama model, or your own OpenAI-compatible API.

API keys stay on your computer. No telemetry, no paid licence.

Default pair is Chinese → English. Output goes to `~/Documents/Tuyi output`.

## DWG and DXF

DXF opens with nothing extra.

DWG needs [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter) on the same machine, or `CAD_ODA_EXEC` pointing at it. The installer does not ship ODA, and we will not put it in the package.

## CLI

```bash
python -m tuyi translate drawing.dxf
python -m tuyi translate drawing.dwg -o out.dwg --mode zh_to_en
```

Packed Windows build: `tuyi-cli.exe` next to `Tuyi.exe`. `python -m dwglot` still works as an alias.

## Build from source

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..
python run.py
```

Do not pack with UPX.

## Contributing

PRs welcome. Eric reviews before anything hits `main`. See [CONTRIBUTING.md](CONTRIBUTING.md).

Do not bundle ODA. Do not add a paid licence.

## License

MIT. Fork of [etianwang/CAD_translator](https://github.com/etianwang/CAD_translator). Copyright Eric Tan and Honsen CAD_translator contributors.

Site files live in `landing/` if you want to host the page on Vercel.
