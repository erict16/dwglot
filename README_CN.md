<p align="center">
  <img src="docs/icons/app.png" width="128" alt="图译">
</p>

<h1 align="center">图译 Tuyi - DWG / DXF 图纸翻译</h1>

<p align="center">
  <a href="README.md">English</a> ·
  <b>简体中文</b> ·
  <a href="README.ja.md">日本語</a> ·
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
  <img src="docs/screenshots/regular.png" width="920" alt="图译：左边原文，右边译文">
</p>

图译是装在电脑上的软件，**Windows** 和 **Mac** 都能用。打开一张 CAD 图纸，它把图上的字抽成一张表，你译完，它另存一份新图。原来那张不动。

不用装 AutoCAD。

谁用：要把图纸交给国外客户，或者要把英文图翻成中文。图名、注释、属性、尺寸、表格里的字都能动。默认中译英，方向可以换。

## 怎么装

去 [Releases](https://github.com/erict16/tuyi/releases) 下载。

- **Windows：** 10 或 11，64 位。跑 Setup 安装包。32 位不行。
- **Mac：** Apple 芯片（M1 及以后）一份 DMG。Intel Mac 要另打一份，不是同一个文件。Intel 那份现在还没有。

安装包还没买代码签名，第一次打开系统会拦，这是正常的。

- **Windows：** 点「更多信息」，再点「仍要运行」。
- **Mac：** 右键图标，选「打开」。或到 系统设置 → 隐私与安全性 → 仍要打开。

## 怎么用

1. 打开 `.dwg` 或 `.dxf`。
2. 点翻译。出来一张表：原文 | 译文 | 图层。
3. 哪一行译得不对，直接改。
4. 点写回。新文件在「文档 / Tuyi output」（Mac 访达里是「文稿 / Tuyi output」）。

一夹图纸也可以一起导出。

## DWG 要多装一个东西

**DXF：** 打开就能译，不用另装。

**DWG：** 同一台电脑先装 [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter)。图译会在 PATH 里找，也可以设 `CAD_ODA_EXEC`。ODA 不能打进图译的安装包，对方协议不允许。

## 翻译走谁的引擎

图译自己没有云账号。你自己选一个：

- 手头的术语表（对得上的词直接用，不送去网上）
- [Azure Translator](https://azure.microsoft.com/products/ai-services/ai-translator)
- [DeepL](https://www.deepl.com)
- 这台电脑上的 [Ollama](https://ollama.com)
- 你自己搭的、接口长得像 OpenAI 的服务

密钥只存在这台电脑上。图译不往外打点，也不收费（MIT）。

## 命令行

和窗口里是同一条流水线：

```bash
python -m tuyi translate drawing.dxf
python -m tuyi translate drawing.dwg -o out.dwg --mode zh_to_en
```

Windows 装好后，`Tuyi.exe` 旁边有 `tuyi-cli.exe`。`python -m dwglot` 也能用。

## 从源码跑

开发和测试写在 [CONTRIBUTING.md](CONTRIBUTING.md)。欢迎提 PR，合进 `main` 前 Eric 会看。

## 协议

MIT。从 [etianwang/CAD_translator](https://github.com/etianwang/CAD_translator) fork。
