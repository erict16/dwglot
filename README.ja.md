<p align="center">
  <img src="docs/icons/app.png" width="128" alt="Tuyi">
</p>

<h1 align="center">图译 Tuyi</h1>

<p align="center"><strong>DWG / DXF 図面翻訳</strong></p>

<p align="center">
  図面を開き、中の文字を訳して、新しいファイルに書き出します。<br>
  Windows と macOS のデスクトップアプリです。AutoCAD は不要です。
</p>

<p align="center">
  <a href="https://github.com/erict16/tuyi/releases"><img alt="GitHub release" src="https://img.shields.io/github/v/release/erict16/tuyi?style=flat-square"></a>
  <img alt="Windows 10+" src="https://img.shields.io/badge/Windows-10%2B-blue?style=flat-square">
  <img alt="macOS 11+" src="https://img.shields.io/badge/macOS-11%2B-orange?style=flat-square">
  <img alt="MIT" src="https://img.shields.io/badge/license-MIT-green?style=flat-square">
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <b>日本語</b> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.es.md">Español</a>
</p>

<p align="center">
  <img src="docs/screenshots/regular.png" width="920" alt="Tuyi：原文と訳文の対照表">
</p>

## インストール

[Releases](https://github.com/erict16/tuyi/releases) から入手してください。

| | |
| --- | --- |
| Windows 10+ | リリースページのセットアップ exe |
| macOS 11+（Apple Silicon） | バージョンタグで CI が打つ DMG |

v0.1 は未署名です。

**Windows:** 「詳細情報」→「実行」。

**macOS:** アプリを右クリックして「開く」。または システム設定 → プライバシーとセキュリティ → このまま開く。

アプリ内の更新確認も同じ Releases を見ます。

## できること

図面を画像として読み取るのではなく、図面の中の文字を訳します。

- **DWG** と **DXF** を開く
- 原文 / 訳文テーブルを編集できる
- 新しい図面として書き出す
- フォルダ単位の一括書き出し
- v0.1 は TEXT、MTEXT、属性、DIMENSION、ACAD_TABLE のセルに対応

用語集に載っている語はその訳を使います。それ以外は Azure Translator、DeepL、ローカルの Ollama、または自分の OpenAI 互換 API に回せます。

API キーはこのパソコンにだけ残ります。テレメトリも有料ライセンスもありません。

既定は中国語 → 英語です。出力先は `~/Documents/Tuyi output` です。

## DWG と DXF

DXF はそのまま訳せます。

DWG は同じマシンに [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter) を入れるか、`CAD_ODA_EXEC` を指定してください。インストーラに ODA は入っていません。

## CLI

```bash
python -m tuyi translate drawing.dxf
python -m tuyi translate drawing.dwg -o out.dwg --mode zh_to_en
```

Windows の導入先には `Tuyi.exe` の隣に `tuyi-cli.exe` があります。`python -m dwglot` も別名として残しています。

## ソースから実行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..
python run.py
```

UPX で固めないでください。

## ライセンス

MIT。[etianwang/CAD_translator](https://github.com/etianwang/CAD_translator) のフォークです。Copyright Eric Tan / Honsen CAD_translator の貢献者。
