<p align="center">
  <img src="docs/icons/app.png" width="128" alt="Tuyi">
</p>

<h1 align="center">图译 Tuyi</h1>

<p align="center">
  <b>English</b> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.es.md">Español</a>
</p>

<p align="center">
  <a href="https://github.com/erict16/tuyi/releases"><img alt="GitHub release" src="https://img.shields.io/github/v/release/erict16/tuyi?style=flat-square"></a>
  <img alt="Windows 10+" src="https://img.shields.io/badge/Windows-10%2B-blue?style=flat-square">
  <img alt="macOS 11+" src="https://img.shields.io/badge/macOS-11%2B-orange?style=flat-square">
  <img alt="MIT" src="https://img.shields.io/badge/license-MIT-green?style=flat-square">
</p>

<p align="center">
  <img src="docs/screenshots/regular.png" width="920" alt="Tuyi: original text on the left, translation on the right">
</p>

Tuyi is a desktop app for **Windows** and **macOS**. You open a CAD drawing, it lists every piece of text on the sheet, you translate that list, and it writes a **new** drawing. The original file is left alone.

You do not need AutoCAD.

It is for people who send drawings overseas (or the other way): title blocks, notes, attributes, dimensions, table cells. Default direction is Chinese to English. You can switch.

## Install

Get the file from [Releases](https://github.com/erict16/tuyi/releases).

- **Windows:** run the Setup exe.
- **Mac (Apple Silicon):** open the DMG. Intel Macs are not in this build yet.

The current build is not code-signed, so the OS will warn you the first time. That is expected.

- **Windows:** More info → Run anyway.
- **Mac:** right-click the app → Open. Or System Settings → Privacy & Security → Open Anyway.

## How to use it

1. Open a `.dwg` or `.dxf`.
2. Click translate. You get a table: original | translation | layer.
3. Edit any row you do not like.
4. Write back. Tuyi saves a new file under `Documents/Tuyi output`.

You can also drop a folder of drawings and export them in one go.

## DWG needs one extra program

**DXF:** open and translate. Nothing else to install.

**DWG:** install [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter) on the same computer first. Tuyi looks for it on PATH, or you can set `CAD_ODA_EXEC`. We cannot put ODA inside Tuyi. Their licence does not allow that.

## Who does the translating

Tuyi does not have its own cloud account. You pick one:

- a glossary you already keep (exact matches skip the API)
- [Azure Translator](https://azure.microsoft.com/products/ai-services/ai-translator)
- [DeepL](https://www.deepl.com)
- [Ollama](https://ollama.com) on this machine
- any OpenAI-compatible URL you run yourself

The key stays in a local file. Tuyi does not phone home, and it is free (MIT).

## Command line

Same pipeline as the window:

```bash
python -m tuyi translate drawing.dxf
python -m tuyi translate drawing.dwg -o out.dwg --mode zh_to_en
```

A Windows install also has `tuyi-cli.exe` next to `Tuyi.exe`. `python -m dwglot` still works.

## Run from source

Dev setup and tests are in [CONTRIBUTING.md](CONTRIBUTING.md). Pull requests are welcome; Eric reviews before they land on `main`.

## License

MIT. Fork of [etianwang/CAD_translator](https://github.com/etianwang/CAD_translator).
