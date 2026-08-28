<p align="center">
  <img src="docs/icons/app.png" width="128" alt="图译">
</p>

<h1 align="center">图译 Tuyi</h1>

<p align="center"><strong>DWG / DXF 图纸翻译</strong></p>

<p align="center">
  打开图纸，译里面的字，写出一份新文件。<br>
  Windows / macOS 桌面程序。不需要 AutoCAD。
</p>

<p align="center">
  <a href="https://github.com/erict16/tuyi/releases"><img alt="GitHub release" src="https://img.shields.io/github/v/release/erict16/tuyi?style=flat-square"></a>
  <img alt="Windows 10+" src="https://img.shields.io/badge/Windows-10%2B-blue?style=flat-square">
  <img alt="macOS 11+" src="https://img.shields.io/badge/macOS-11%2B-orange?style=flat-square">
  <img alt="MIT" src="https://img.shields.io/badge/license-MIT-green?style=flat-square">
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <b>简体中文</b> ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.es.md">Español</a>
</p>

<p align="center">
  <img src="docs/screenshots/regular.png" width="920" alt="图译：原文和译文对照表">
</p>

## 安装

去 [Releases](https://github.com/erict16/tuyi/releases) 下。

| | |
| --- | --- |
| Windows 10+ | 安装包在发行页 |
| macOS 11+（Apple Silicon） | DMG，跟版本 tag 一起由 CI 打 |

v0.1 还没签名。

**Windows：** 「更多信息」→「仍要运行」。

**Mac：** 右键图标，选「打开」。或到 系统设置 → 隐私与安全性 → 仍要打开。

软件里的「检查更新」也看同一处 Releases。

## 它做什么

图译译的是图纸里的文字，不是把整张图当图片扫。

- 打开 **DWG** 和 **DXF**
- 原文 / 译文表，能改
- 写出一份新图纸
- 可以批量导出
- v0.1 覆盖 TEXT、MTEXT、块属性、DIMENSION 文字、ACAD_TABLE 单元格

术语表对得上的词直接用。其余可以走 Azure、DeepL、本机 Ollama，或你自己的 OpenAI 兼容接口。

密钥只存在你这台电脑上。没有遥测，也没有付费授权。

默认中译英。输出在 `~/Documents/Tuyi output`（访达里是「文稿/Tuyi output」）。

## DWG 和 DXF

DXF 直接译，不用另装东西。

DWG 要本机有 [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter)，或设 `CAD_ODA_EXEC`。安装包里没有 ODA，也不会打进去。

## 命令行

```bash
python -m tuyi translate drawing.dxf
python -m tuyi translate drawing.dwg -o out.dwg --mode zh_to_en
```

Windows 安装目录里有 `tuyi-cli.exe`，和 `Tuyi.exe` 并排。`python -m dwglot` 还是能用。

## 从源码跑

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..
python run.py
```

不要用 UPX 打包。

## 贡献

欢迎 PR。合进 `main` 前 Eric 会看。细节在 [CONTRIBUTING.md](CONTRIBUTING.md)。

不要把 ODA 打进安装包，也不要加付费授权。

## 协议

MIT。从 [etianwang/CAD_translator](https://github.com/etianwang/CAD_translator) fork。Copyright Eric Tan / Honsen CAD_translator 贡献者。

落地页在 `landing/`，可以挂 Vercel。
