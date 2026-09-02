"""Agent CLI for the 常规 写回 pipeline. No TUI, no prompts."""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
from pathlib import Path


def _chinese_error(exc: BaseException, fallback: str = "翻译失败") -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    if text and "Traceback" not in text and any("\u4e00" <= char <= "\u9fff" for char in text):
        return text
    return fallback


def _split_output(output: str, output_dir: str) -> tuple[str, str]:
    from backend.drawings import strip_cad_suffix

    path = Path(output)
    name = strip_cad_suffix(path.name)
    if path.is_absolute() or len(path.parts) > 1:
        return str(path.parent), name
    return output_dir, name


def _emit_result(result: dict) -> None:
    print(result["path"])
    print(f"extracted: {result['extracted']}")
    print(f"translated: {result['translated']}")


def translate_one(
    path: str,
    *,
    mode: str,
    output_dir: str,
    output_name: str,
    translate_filename: bool,
    glossary: str,
    provider: str = "",
    style: str = "纯译文",
) -> dict:
    from backend.api import service
    from backend.drawings import translate_drawing
    from backend.language_assets import LanguageAssets

    config = service.load_config()
    provider = (provider or "").strip() or config.get("provider") or "deepl"
    engine = service._engine_from({**config, "provider": provider}, {})
    project = glossary.strip() if glossary else config.get("project_package_path", "")
    if glossary.strip():
        LanguageAssets().project_info(glossary.strip())
    directory = output_dir or config.get("output_dir") or service.default_output_dir()
    name = output_name
    if name:
        directory, name = _split_output(name, directory)
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
        return translate_drawing(
            path,
            mode=mode,
            output_dir=directory,
            output_name=name,
            translate_filename=translate_filename and not name,
            project_package_path=project or "",
            provider=provider,
            engine=engine,
            style=style,
        )


def build_parser() -> argparse.ArgumentParser:
    from backend.app_meta import APP_VERSION

    parser = argparse.ArgumentParser(
        prog="tuyi",
        add_help=True,
        epilog="DWG needs ODA File Converter on PATH or CAD_ODA_EXEC. DXF does not.",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {APP_VERSION}")
    sub = parser.add_subparsers(dest="command")
    translate = sub.add_parser("translate", help="open → extract → glossary-first translate → write-back")
    translate.add_argument("inputs", nargs="+", help="DWG / DXF path")
    translate.add_argument("-o", "--output", default="", help="output file (single input only)")
    translate.add_argument("--mode", default="zh_to_en")
    translate.add_argument("--provider", default="", help="deepl, azure, ollama, or openai (default: saved config)")
    translate.add_argument("--translate-filename", action="store_true", default=False)
    translate.add_argument("--output-dir", default="")
    translate.add_argument("--glossary", default="", help="optional project terminology JSON")
    translate.add_argument("--style", default="纯译文", help="纯译文 / 原译对照 / 译原对照")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "translate":
        parser.print_help(sys.stderr)
        return 2
    if args.output and len(args.inputs) > 1:
        print("多个输入时请用 --output-dir，不要用 -o", file=sys.stderr)
        return 2
    try:
        from backend.cad import configure_odafc
        from backend.languages import split_mode

        split_mode(args.mode)
        configure_odafc()
    except Exception as exc:
        print(_chinese_error(exc, "翻译失败"), file=sys.stderr)
        return 1

    for index, raw in enumerate(args.inputs):
        path = os.path.abspath(raw)
        output_name = args.output if index == 0 else ""
        try:
            result = translate_one(
                path,
                mode=args.mode,
                output_dir=args.output_dir,
                output_name=output_name,
                translate_filename=args.translate_filename,
                glossary=args.glossary,
                provider=args.provider,
                style=args.style,
            )
        except FileNotFoundError as exc:
            print(_chinese_error(exc, "图纸不存在"), file=sys.stderr)
            return 1
        except Exception as exc:
            print(_chinese_error(exc, "翻译失败"), file=sys.stderr)
            return 1
        _emit_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
