<p align="center">
  <img src="docs/icons/app.png" width="128" alt="Tuyi">
</p>

<h1 align="center">图译 Tuyi</h1>

<p align="center"><strong>DWG / DXF 도면 번역</strong></p>

<p align="center">
  도면을 열고, 안의 글자를 번역한 뒤 새 파일로 씁니다.<br>
  Windows와 macOS 데스크톱 앱입니다. AutoCAD는 필요 없습니다.
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
  <a href="README.ja.md">日本語</a> ·
  <b>한국어</b> ·
  <a href="README.es.md">Español</a>
</p>

<p align="center">
  <img src="docs/screenshots/regular.png" width="920" alt="Tuyi: 원문과 번역 표">
</p>

## 설치

[Releases](https://github.com/erict16/tuyi/releases)에서 받습니다.

| | |
| --- | --- |
| Windows 10+ | 릴리스 페이지의 설치 파일 |
| macOS 11+ (Apple Silicon) | 버전 태그와 함께 CI가 만드는 DMG |

v0.1은 서명되지 않았습니다.

**Windows:** 추가 정보 → 실행.

**macOS:** 앱을 우클릭한 다음 열기. 또는 시스템 설정 → 개인 정보 보호 및 보안 → 확인 후 열기.

앱의 업데이트 확인도 같은 Releases를 봅니다.

## 하는 일

도면을 그림으로 스캔하지 않습니다. 도면 안의 글자를 번역합니다.

- **DWG**와 **DXF**를 염
- 원문 / 번역 표를 직접 고칠 수 있음
- 새 도면으로 저장
- 폴더 일괄 내보내기
- v0.1은 TEXT, MTEXT, 속성, DIMENSION, ACAD_TABLE 셀을 다룸

용어집에 있는 단어는 그 번역을 씁니다. 나머지는 Azure Translator, DeepL, 로컬 Ollama, 또는 직접 쓰는 OpenAI 호환 API로 넘길 수 있습니다.

API 키는 이 컴퓨터에만 남습니다. 원격 수집도, 유료 라이선스도 없습니다.

기본은 중국어 → 영어입니다. 출력은 `~/Documents/Tuyi output`입니다.

## DWG와 DXF

DXF는 추가 프로그램 없이 번역됩니다.

DWG는 같은 PC에 [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter)가 있거나 `CAD_ODA_EXEC`를 지정해야 합니다. 설치 파일에 ODA는 들어 있지 않습니다.

## CLI

```bash
python -m tuyi translate drawing.dxf
python -m tuyi translate drawing.dwg -o out.dwg --mode zh_to_en
```

Windows 설치 폴더에는 `Tuyi.exe` 옆에 `tuyi-cli.exe`가 있습니다. `python -m dwglot`도 별칭으로 남깁니다.

## 소스에서 실행

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..
python run.py
```

UPX로 압축하지 마세요.

## 라이선스

MIT. [etianwang/CAD_translator](https://github.com/etianwang/CAD_translator)의 포크입니다. Copyright Eric Tan / Honsen CAD_translator 기여자.
