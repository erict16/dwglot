<h4 align="right"><strong><a href="README.md">English</a></strong> | 简体中文</h4>

<p align="center">
  <img src="docs/screenshots/mark.svg" width="88" alt="图译" />
  <h1 align="center">图译 Dwglot</h1>
  <div align="center">
    <a href="https://github.com/erict16/dwglot/releases"><img alt="GitHub release" src="https://img.shields.io/github/v/release/erict16/dwglot?style=flat-square"></a>
    <img alt="macOS 11+" src="https://img.shields.io/badge/macOS-11%2B-orange?style=flat-square">
    <img alt="Windows 10+" src="https://img.shields.io/badge/Windows-10%2B-blue?style=flat-square">
    <img alt="MIT" src="https://img.shields.io/badge/license-MIT-green?style=flat-square">
  </div>
  <div align="center">开源桌面 <strong>CAD translator</strong>：翻译 <strong>DWG</strong> / <strong>DXF</strong> 里的文字，做 <strong>drawing translation</strong> / <strong>图纸翻译</strong>。不依赖 AutoCAD。</div>
</p>

<p align="center">
  <img src="docs/screenshots/regular.png" width="900" alt="图译常规处理：原文译文表" />
</p>
<p align="center">
  <img src="docs/screenshots/export.png" width="48%" alt="批量导出" />
  <img src="docs/screenshots/params.png" width="48%" alt="参数" />
</p>

## 特点

- **准：** 术语表命中不走机翻。引擎是云（Azure / DeepL）、本地（Ollama）、或自定义 OpenAI 兼容接口。
- **全：** 打开 DWG/DXF，勾选原文 | 译文，批量导出，写回图纸。v0.1 覆盖 TEXT / MTEXT / 属性。
- **本地：** 无遥测、无付费授权。密钥只存在你的电脑上。
- **轻：** 不捆绑 ODA File Converter。DXF 直接译；DWG 用你自己装的 ODA。

## 安装

v0.1 **未签名**。从 [GitHub Releases](https://github.com/erict16/dwglot/releases) 下 Mac DMG 或 Windows EXE。应用内 **检查更新** 也指向同一 Releases（以后接 Sparkle / WinSparkle；现在会打开新安装包）。

**Mac 第一次打开（Gatekeeper）：** 按住 Control 点图标，选「打开」。或 系统设置 → 隐私与安全性 → 仍要打开。以后有开发者证书就会安静。

**Windows 第一次打开（SmartScreen）：** 更多信息 → 仍要运行。以后 Authenticode 会消掉这层。

不要用 UPX 打包。只译 DXF 不必装 ODA。译 DWG 请自行安装 [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter)，或设 `CAD_ODA_EXEC`。图译从不把 ODA 打进安装包。

从源码跑：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..
python run.py
```

默认中 → 英。输出在 `~/Documents/Dwglot output`（访达里是「文稿/Dwglot output」）。

## 说明

- 基于 [etianwang/CAD_translator](https://github.com/etianwang/CAD_translator)（MIT）。图译改了品牌、Mac 界面、四种引擎和自动更新。
- 标注 / 表格写回放在 v0.2。
- 落地页在 `landing/`（Tailwind Plus Salient 骨架，可挂 Vercel）。

## 协议

MIT。Copyright Eric Tan / Honsen CAD_translator 贡献者。
