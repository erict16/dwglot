<p align="center">
  <img src="docs/icons/app.png" width="128" alt="Tuyi">
</p>

<h1 align="center">Tuyi 图译 - DWG / DXF 図面翻訳</h1>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <b>日本語</b> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.es.md">Español</a>
</p>

<p align="center">
  <a href="https://github.com/erict16/tuyi/releases"><img alt="GitHub release" src="https://img.shields.io/github/v/release/erict16/tuyi?style=flat-square"></a>
  <img alt="Windows 10/11 x64" src="https://img.shields.io/badge/Windows-10%2F11%20x64-blue?style=flat-square">
  <img alt="macOS Apple Silicon" src="https://img.shields.io/badge/macOS-Apple%20Silicon-orange?style=flat-square">
  <img alt="MIT" src="https://img.shields.io/badge/license-MIT-green?style=flat-square">
</p>

<p align="center">
  <img src="docs/screenshots/regular.png" width="920" alt="Tuyi：左が原文、右が訳文">
</p>

Tuyi は **Windows** と **macOS** 用のデスクトップアプリです。CAD 図面を開くと、図面上の文字が表に並びます。訳してから「書き戻し」すると、**新しい**図面ができます。元のファイルはいじりません。

AutoCAD は不要です。

海外の客先に図面を渡すとき、または英語図を中国語にするときに使います。図名、注記、属性、寸法、表の中の文字が対象です。初期設定は中国語→英語です。向きは変えられます。

## 入れ方

[Releases](https://github.com/erict16/tuyi/releases) からファイルを取ってください。

- **Windows:** 10 または 11、64 ビット。Setup の exe を実行。32 ビットは不可。
- **Mac:** Apple シリコン（M1 以降）が一つの DMG。Intel Mac は別ファイル。同じパッケージではない。Intel 版はまだない。

今のインストーラはコード署名していません。初回は OS に止められます。想定どおりです。

- **Windows:** 「詳細情報」→「実行」。
- **Mac:** アプリを右クリックして「開く」。または システム設定 → プライバシーとセキュリティ → このまま開く。

## 使い方

1. `.dwg` か `.dxf` を開く。
2. 翻訳を押す。原文 | 訳文 | 画層 の表が出る。
3. おかしい行はその場で直す。
4. 書き戻す。新しいファイルは `書類/Tuyi output`（英語環境では `Documents/Tuyi output`）に入ります。

フォルダごとまとめて出すこともできます。

## DWG はもう一つ入れる

**DXF:** 開いて訳すだけです。追加ソフトは不要です。

**DWG:** 同じパソコンに先に [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter) を入れてください。Tuyi は PATH を見ます。`CAD_ODA_EXEC` で場所を指定しても構いません。ODA を Tuyi の中に同梱することはできません。先方のライセンスが許しません。

## 翻訳エンジン

Tuyi 自身のクラウド口座はありません。どれか一つを自分で用意します。

- 手元の用語集（完全一致はその訳を使い、API に送りません）
- [Azure Translator](https://azure.microsoft.com/products/ai-services/ai-translator)
- [DeepL](https://www.deepl.com)
- このマシンの [Ollama](https://ollama.com)
- 自分で動かしている OpenAI 互換の API

キーはこのパソコンにだけ残ります。Tuyi は外に通信ログを送りません。無料です（MIT）。

## コマンドライン

画面と同じ処理です。

```bash
python -m tuyi translate drawing.dxf
python -m tuyi translate drawing.dwg -o out.dwg --mode zh_to_en
```

Windows では `Tuyi.exe` の隣に `tuyi-cli.exe` があります。`python -m dwglot` も使えます。

## ソースから動かす

開発とテストは [CONTRIBUTING.md](CONTRIBUTING.md) に書いてあります。PR は歓迎します。`main` に入る前に Eric が確認します。

## ライセンス

MIT。[etianwang/CAD_translator](https://github.com/etianwang/CAD_translator) のフォークです。
