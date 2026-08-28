"""Desktop application entry point."""
import os
import sys


def _frozen_cli() -> bool:
    if not getattr(sys, "frozen", False):
        return False
    name = sys.argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name in {"dwglot-cli.exe", "dwglot-cli"}


def main() -> None:
    if _frozen_cli():
        from backend.cli import main as cli_main

        raise SystemExit(cli_main())
    if "--legacy" in sys.argv:
        print("The legacy Tkinter interface has been removed.")
        return
    from backend.cad import configure_odafc
    from desktop.launcher import run_web_app

    configure_odafc()
    run_web_app()


if __name__ == "__main__":
    main()
