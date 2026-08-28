# Contributing

PRs are welcome. Eric reviews every change before it lands on `main`.

中文：欢迎提 PR。合进 `main` 前 Eric 会看一遍。别把 ODA 打进安装包，也别加付费授权或遥测。

## Before you start

Open an [issue](https://github.com/erict16/tuyi/issues) first if the change is more than a small fix. Questions can go to [Discussions](https://github.com/erict16/tuyi/discussions).

## Dev setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..
python run.py
```

Windows: `pip install -r requirements.txt`. macOS: `requirements-macos.txt`.

```bash
python -m unittest discover -s tests
```

DXF tests do not need ODA. Live DWG tests skip unless ODA File Converter is on the machine.

## Rules

- Do not bundle [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter). DXF works without it; DWG needs the user to install it.
- Do not add a paid licence, trial, activation code, or telemetry.
- Do not pack with UPX.
- Keep the UI Chinese-first. Default pair is Chinese → English.
- `python -m tuyi` is the CLI. `python -m dwglot` stays as an alias.

## Pull requests

1. Fork, branch off `main`.
2. Keep the diff on one problem.
3. Run the unittest suite.
4. Open a PR against `main` and fill the template.

Eric is the reviewer. There is no CLA. MIT applies to contributions the same as the rest of the tree.
