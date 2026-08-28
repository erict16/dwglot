<p align="center">
  <img src="docs/icons/app.png" width="128" alt="Tuyi">
</p>

<h1 align="center">图译 Tuyi</h1>

<p align="center"><strong>Traductor DWG / DXF</strong></p>

<p align="center">
  Abre un plano CAD, traduce el texto que lleva dentro y escribe un archivo nuevo.<br>
  App de escritorio para Windows y macOS. No hace falta AutoCAD.
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
  <a href="README.ko.md">한국어</a> ·
  <b>Español</b>
</p>

<p align="center">
  <img src="docs/screenshots/regular.png" width="920" alt="Tuyi: tabla de original y traducción">
</p>

## Instalación

Descárgalo en [Releases](https://github.com/erict16/tuyi/releases).

| | |
| --- | --- |
| Windows 10+ | El instalador está en la página de la versión |
| macOS 11+ (Apple Silicon) | DMG que CI genera con cada tag de versión |

v0.1 no está firmado.

**Windows:** Más información → Ejecutar de todas formas.

**macOS:** clic derecho en la app → Abrir. O Ajustes del Sistema → Privacidad y seguridad → Abrir de todos modos.

La comprobación de actualizaciones de la app mira la misma página de Releases.

## Qué hace

Tuyi traduce el texto *dentro* del plano, no una captura del dibujo.

- Abre **DWG** y **DXF**
- Tabla original / traducción, editable
- Escribe un plano nuevo
- Exportación por lotes
- v0.1 cubre TEXT, MTEXT, atributos, DIMENSION y celdas de ACAD_TABLE

Si el glosario tiene la palabra, usa esa traducción. El resto puede ir a Azure Translator, DeepL, Ollama en tu máquina o una API compatible con OpenAI que tú pongas.

Las claves API se quedan en tu ordenador. No hay telemetría ni licencia de pago.

El par por defecto es chino → inglés. La salida va a `~/Documents/Tuyi output`.

## DWG y DXF

DXF se traduce sin software extra.

DWG necesita [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter) en el mismo equipo, o `CAD_ODA_EXEC`. El instalador no incluye ODA.

## CLI

```bash
python -m tuyi translate drawing.dxf
python -m tuyi translate drawing.dwg -o out.dwg --mode zh_to_en
```

En Windows, `tuyi-cli.exe` está junto a `Tuyi.exe`. `python -m dwglot` sigue como alias.

## Compilar desde el código

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..
python run.py
```

No empaquetes con UPX.

## Licencia

MIT. Fork de [etianwang/CAD_translator](https://github.com/etianwang/CAD_translator). Copyright Eric Tan y colaboradores de Honsen CAD_translator.
