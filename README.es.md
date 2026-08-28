<p align="center">
  <img src="docs/icons/app.png" width="128" alt="Tuyi">
</p>

<h1 align="center">图译 Tuyi</h1>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <b>Español</b>
</p>

<p align="center">
  <a href="https://github.com/erict16/tuyi/releases"><img alt="GitHub release" src="https://img.shields.io/github/v/release/erict16/tuyi?style=flat-square"></a>
  <img alt="Windows 10+" src="https://img.shields.io/badge/Windows-10%2B-blue?style=flat-square">
  <img alt="macOS 11+" src="https://img.shields.io/badge/macOS-11%2B-orange?style=flat-square">
  <img alt="MIT" src="https://img.shields.io/badge/license-MIT-green?style=flat-square">
</p>

<p align="center">
  <img src="docs/screenshots/regular.png" width="920" alt="Tuyi: original a la izquierda, traducción a la derecha">
</p>

Tuyi es una app de escritorio para **Windows** y **macOS**. Abres un plano CAD, saca el texto a una tabla, lo traduces y escribe un plano **nuevo**. El archivo original no se toca.

No hace falta AutoCAD.

Sirve para mandar planos al extranjero, o al revés. Títulos, notas, atributos, cotas, celdas de tabla. Por defecto va de chino a inglés. Se puede cambiar el sentido.

## Instalación

El archivo está en [Releases](https://github.com/erict16/tuyi/releases).

- **Windows:** ejecuta el Setup.
- **Mac (Apple Silicon):** abre el DMG. Los Mac Intel aún no salen en esta versión.

El instalador no está firmado. El sistema te va a parar la primera vez. Es normal.

- **Windows:** Más información → Ejecutar de todas formas.
- **Mac:** clic derecho en la app → Abrir. O Ajustes del Sistema → Privacidad y seguridad → Abrir de todos modos.

## Cómo se usa

1. Abre un `.dwg` o un `.dxf`.
2. Pulsa traducir. Sale una tabla: original | traducción | capa.
3. Si una fila está mal, la cambias ahí mismo.
4. Escribe el plano. El archivo nuevo va a `Documentos/Tuyi output` (en inglés, `Documents/Tuyi output`).

También puedes tirar una carpeta entera y exportarla de una vez.

## DWG pide otro programa

**DXF:** lo abres y lo traduces. No hay que instalar nada más.

**DWG:** instala antes [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter) en el mismo ordenador. Tuyi lo busca en el PATH. También puedes poner `CAD_ODA_EXEC`. No podemos meter ODA dentro de Tuyi. Su licencia no lo permite.

## Quién traduce

Tuyi no tiene cuenta en la nube. Eliges una de estas:

- un glosario que ya tengas (si la palabra coincide, no pasa por la API)
- [Azure Translator](https://azure.microsoft.com/products/ai-services/ai-translator)
- [DeepL](https://www.deepl.com)
- [Ollama](https://ollama.com) en este equipo
- una API compatible con OpenAI que montes tú

La clave se queda en este ordenador. Tuyi no envía telemetría. Es gratis (MIT).

## Línea de comandos

Es el mismo flujo que la ventana:

```bash
python -m tuyi translate drawing.dxf
python -m tuyi translate drawing.dwg -o out.dwg --mode zh_to_en
```

En Windows, `tuyi-cli.exe` está al lado de `Tuyi.exe`. `python -m dwglot` sigue valiendo.

## Compilar desde el código

El entorno de desarrollo y los tests están en [CONTRIBUTING.md](CONTRIBUTING.md). Los PR son bienvenidos. Eric los revisa antes de entrar en `main`.

## Licencia

MIT. Fork de [etianwang/CAD_translator](https://github.com/etianwang/CAD_translator).
