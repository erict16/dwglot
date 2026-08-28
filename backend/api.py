"""FastAPI backend for the React web UI."""

import asyncio
from collections import deque
import json
import os
import queue
import shutil
import sys
import threading
import time
import urllib.request
from uuid import uuid4
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.app_meta import APP_TITLE, APP_VERSION, GITHUB_URL, default_output_dir as dwglot_output_dir
from backend.providers.azure import AzureFreeQuotaExceededError
from backend.providers.base import TranslationProviderError
from backend.queue import BatchQueue
from backend.cad import ODA_OUTPUT_VERSIONS, analyze_source, dwg_unavailable_short, odafc_available, odafc_status, output_path_for
from backend.translator import CADChineseTranslator, CONFIG_PATH, load_yaml_data, output_prefix, resource_path
from backend.language_assets import LanguageAssets
from backend.storage import atomic_write_json, quarantine_corrupt_file
from backend.updates import check_github_release, unavailable_payload
from backend.drawings import ensure_output_dir, extract_preview, export_pdf, print_pdf, translate_cjk_filename_stem, translate_rows, writeback_rows
from backend.languages import split_mode

ENGINE_PROVIDERS = {"deepl", "azure", "openai", "ollama"}


def _chinese_detail(exc: BaseException, fallback: str) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    if text and "Traceback" not in text and any("\u4e00" <= char <= "\u9fff" for char in text):
        return text
    return fallback


def _frontend_dist() -> Path:
    bundled = Path(resource_path("frontend/dist"))
    if bundled.is_dir():
        return bundled
    return Path(__file__).resolve().parents[1] / "frontend" / "dist"


FRONTEND_DIST = _frontend_dist()
API_PORT = 8765
SSE_QUEUE_SIZE = 500
DROPPED_FILE_RETENTION_SECONDS = 30 * 24 * 60 * 60
BUILTIN_GLOSSARIES = {
    "zh_to_fr": ("glossaries/translation_context.yaml", "context_zh_to_fr"),
    "fr_to_zh": ("glossaries/translation_context_fr_to_zh.yaml", "context_fr_to_zh"),
    "zh_to_en": ("glossaries/translation_context_zh_to_en.yaml", "context_zh_to_en"),
    "en_to_zh": ("glossaries/translation_context_en_to_zh.yaml", "context_en_to_zh"),
}
SYSTEM_ACCENT_FALLBACK = (0.56, 0.56, 0.58)  # macOS Graphite-like neutral fallback


def system_accent_theme() -> dict:
    """Read the active macOS accent colour without making it a user setting."""
    rgb = SYSTEM_ACCENT_FALLBACK
    if sys.platform == "darwin":
        try:
            from AppKit import NSColor, NSColorSpace

            color = NSColor.controlAccentColor().colorUsingColorSpace_(NSColorSpace.sRGBColorSpace())
            if color is not None:
                rgb = (float(color.redComponent()), float(color.greenComponent()), float(color.blueComponent()))
        except Exception:
            pass
    return {"color": [round(channel, 4) for channel in rgb]}


def builtin_terms() -> list[dict]:
    """Expose the shipped YAML glossary as a read-only asset list."""
    entries = []
    for mode, (filename, key) in BUILTIN_GLOSSARIES.items():
        for index, (source, target) in enumerate(load_yaml_data(filename).get(key, {}).items()):
            entries.append({"id": f"{mode}:{index}", "scope": "builtin", "mode": mode, "source": source, "target": target, "layer_contains": ""})
    return entries


class ConfigBody(BaseModel):
    deepl_key: str = ""
    provider: str = "deepl"
    azure_key: str = ""
    azure_region: str = ""
    output_dir: str = ""
    project_package_path: Optional[str] = None
    openai_key: str = ""
    openai_base: str = ""
    openai_model: str = ""
    ollama_host: str = ""
    ollama_model: str = ""


class TranslateBody(BaseModel):
    input_file: str
    output_dir: str = ""
    output_name: str = ""
    translation_mode: str = "zh_to_en"
    translate_blocks: bool = False
    deepl_key: str = ""
    provider: str = "deepl"
    azure_key: str = ""
    azure_region: str = ""
    project_package_path: str = ""
    openai_key: str = ""
    openai_base: str = ""
    openai_model: str = ""
    ollama_host: str = ""
    ollama_model: str = ""


class BatchBody(BaseModel):
    files: list[str]


class BatchStartBody(BaseModel):
    output_dir: str = ""
    translation_mode: str = "zh_to_en"
    translate_blocks: bool = False
    output_format: str = "source"
    output_version: str = ""
    deepl_key: str = ""
    provider: str = "deepl"
    azure_key: str = ""
    azure_region: str = ""
    project_package_path: str = ""
    openai_key: str = ""
    openai_base: str = ""
    openai_model: str = ""
    ollama_host: str = ""
    ollama_model: str = ""
    style: str = "纯译文"
    enable_v02: bool = True
    include_attribs: bool = True
    include_model: bool = True
    include_paper: bool = True
    include_frozen: bool = False
    include_locked: bool = False
    include_off: bool = False
    skip_numbers: bool = True
    skip_dupes: bool = True
    skip_nonsource: bool = True
    translate_filename: bool = False


class ExtractBody(BaseModel):
    path: str
    include_blocks: bool = False
    translation_mode: str = "zh_to_en"
    include_model: bool = True
    include_paper: bool = True
    include_attribs: bool = True
    include_frozen: bool = False
    include_locked: bool = False
    include_off: bool = False
    enable_v02: bool = False
    skip_numbers: bool = True
    skip_dupes: bool = True
    skip_nonsource: bool = True


class TranslateRowsBody(BaseModel):
    items: list[dict]
    translation_mode: str = "zh_to_en"
    provider: str = "deepl"
    project_package_path: str = ""
    deepl_key: str = ""
    azure_key: str = ""
    azure_region: str = ""
    openai_key: str = ""
    openai_base: str = ""
    openai_model: str = ""
    ollama_host: str = ""
    ollama_model: str = ""
    skip_numbers: bool = True
    skip_dupes: bool = True
    skip_nonsource: bool = True


class WritebackBody(BaseModel):
    input_file: str
    items: list[dict]
    output_dir: str = ""
    output_name: str = ""
    translation_mode: str = "zh_to_en"
    include_blocks: bool = False
    style: str = "纯译文"
    translate_filename: bool = False


class PdfBody(BaseModel):
    path: str = ""
    output_dir: str = ""
    output_name: str = ""
    layout: str = ""
    style: str = "纯译文"
    items: list[dict] = []
    print_after: bool = False


class AssetTermBody(BaseModel):
    scope: str = "global"
    mode: str
    source: str
    target: str
    layer_contains: str = ""
    project_package_path: str = ""
    id: Optional[int] = None


class AssetDeleteBody(BaseModel):
    scope: str = "global"
    id: int
    project_package_path: str = ""


class ProjectPackageBody(BaseModel):
    path: str
    name: str = ""
    create: bool = False


class UsageBody(BaseModel):
    deepl_key: str = ""


class ImportTermsBody(BaseModel):
    mode: str = "zh_to_en"
    terms: list[dict] = []
    csv: str = ""
    scope: str = "global"
    project_package_path: str = ""


class TranslationService:
    def __init__(self):
        self.status = "idle"
        self.last_message = ""
        self._log_queues: list[queue.Queue] = []
        self._logs = deque(maxlen=5000)
        self._lock = threading.Lock()
        self._output_lock = threading.Lock()
        self._reserved_outputs: set[str] = set()
        self.language_assets = LanguageAssets()
        self.dropped_files_dir = Path(CONFIG_PATH).parent / "cad_translator_dropped_files"
        self.dropped_files_dir.mkdir(exist_ok=True)
        self.batch = BatchQueue(self._run_batch, self.emit_log, lambda task: self.load_config().get(f"{task.get('provider', 'deepl')}_key", ""))
        self.cleanup_dropped_files()

    def save_dropped_files(self, files: list[UploadFile], *, require_oda: bool = True) -> list[str]:
        paths = []
        for upload in files:
            name = Path(upload.filename or "").name
            if not name.lower().endswith((".dxf", ".dwg")):
                raise HTTPException(status_code=400, detail=f"无效 CAD 文件: {name or '未命名文件'}")
            if require_oda and name.lower().endswith(".dwg") and not odafc_available():
                raise HTTPException(status_code=400, detail=dwg_unavailable_short())
            target = self.dropped_files_dir / uuid4().hex / name
            target.parent.mkdir(parents=True)
            with target.open("wb") as output:
                shutil.copyfileobj(upload.file, output)
            upload.file.close()
            paths.append(str(target))
        self.cleanup_dropped_files()
        return paths

    def cleanup_dropped_files(self):
        tasks = getattr(getattr(self, "batch", None), "snapshot", lambda: {"tasks": []})()["tasks"]
        active_paths = {Path(task["input_file"]).parent.resolve() for task in tasks if task["status"] in {"queued", "retrying", "running"}}
        cutoff = time.time() - DROPPED_FILE_RETENTION_SECONDS
        root = self.dropped_files_dir.resolve()
        for candidate in self.dropped_files_dir.iterdir():
            if candidate.is_dir() and candidate.resolve().parent == root and candidate.resolve() not in active_paths and candidate.stat().st_mtime < cutoff:
                shutil.rmtree(candidate)

    def _run_batch(self, task: dict, log, resume_event, cancel_event) -> str:
        path = task.get("input_file") or ""
        problem = batch_path_problem(path)
        if problem:
            raise _fatal_batch_error(problem)
        if task.get("output_format") == "dwg" and not odafc_available():
            raise _fatal_batch_error(dwg_unavailable_short())
        provider = task.get("provider", "deepl")
        config = self.load_config()
        engine = self._engine_from(config, task)
        fmt = task.get("output_format", "source")
        ext = os.path.splitext(path)[1] if fmt == "source" else f".{fmt}"
        translator = CADChineseTranslator(log_callback=log)
        translator.configure_language_assets(task.get("project_package_path") or config.get("project_package_path", ""))
        translator.configure_engine(provider, **engine)
        stem = Path(path).stem
        if task.get("translate_filename"):
            stem = translate_cjk_filename_stem(stem, mode=task["translation_mode"], translator=translator)
        name = f"{output_prefix(task['translation_mode'])}_{stem}"
        output = self.reserve_output(task, name, ext)
        try:
            translator.translate_cad_file(
                path,
                output,
                task["translation_mode"],
                task["translate_blocks"],
                fmt,
                task.get("output_version", ""),
                resume_event,
                cancel_event,
                style=task.get("style") or "纯译文",
                enable_v02=task.get("enable_v02", True),
                include_attribs=task.get("include_attribs", True),
                include_model=task.get("include_model", True),
                include_paper=task.get("include_paper", True),
                include_frozen=task.get("include_frozen", False),
                include_locked=task.get("include_locked", False),
                include_off=task.get("include_off", False),
                skip_numbers=task.get("skip_numbers", True),
                skip_dupes=task.get("skip_dupes", True),
                skip_nonsource=task.get("skip_nonsource", True),
            )
        except FileNotFoundError as exc:
            raise _fatal_batch_error("图纸不存在") from exc
        except (ValueError, RuntimeError) as exc:
            raise _fatal_batch_error(_chinese_detail(exc, "翻译失败")) from exc
        return output

    @staticmethod
    def _engine_from(config: dict, extra: dict | None = None) -> dict:
        extra = extra or {}

        def pick(*names, default=""):
            for name in names:
                value = extra.get(name)
                if value not in (None, ""):
                    return value
            for name in names:
                value = config.get(name)
                if value not in (None, ""):
                    return value
            return default

        provider = extra.get("provider") or config.get("provider") or "deepl"
        deepl_key = pick("deepl_key")
        azure_key = pick("azure_key")
        openai_key = pick("openai_key")
        fallback_key = extra.get("_key") or extra.get("api_key") or ""
        if fallback_key:
            if provider == "deepl" and not deepl_key:
                deepl_key = fallback_key
            elif provider == "azure" and not azure_key:
                azure_key = fallback_key
            elif provider == "openai" and not openai_key:
                openai_key = fallback_key
        return {
            "deepl_key": deepl_key,
            "azure_key": azure_key,
            "azure_region": pick("azure_region"),
            "openai_key": openai_key,
            "openai_base": pick("openai_base"),
            "openai_model": pick("openai_model"),
            "ollama_host": pick("ollama_host"),
            "ollama_model": pick("ollama_model"),
        }

    @staticmethod
    def _engine_ready(provider: str, engine: dict) -> bool:
        if provider == "ollama":
            from backend.providers.ollama import ollama_reachable

            return ollama_reachable(engine.get("ollama_host") or "")
        if provider == "azure":
            return bool(engine.get("azure_key", "").strip())
        if provider == "openai":
            key = str(engine.get("openai_key") or "").strip()
            base = str(engine.get("openai_base") or "").strip()
            if not key or not base:
                return False
            from backend.providers.openai_compat import openai_reachable

            return openai_reachable(base)
        return bool(engine.get("deepl_key", "").strip())

    @staticmethod
    def _engine_missing_message(provider: str, engine: dict | None = None) -> str:
        engine = engine or {}
        if provider == "azure":
            return "请配置 Azure Translator Key"
        if provider == "openai":
            if not str(engine.get("openai_base") or "").strip():
                return "请配置自定义接口地址"
            if not str(engine.get("openai_key") or "").strip():
                return "请配置自定义接口的 API Key"
            return "无法连接自定义接口"
        if provider == "ollama":
            return "请先启动 Ollama"
        return "请配置 DeepL API Key"

    def reserve_output(self, task: dict, name: str, ext: str) -> str:
        """Reserve a distinct output path before concurrent work starts."""
        if task.get("_output_path"):
            return task["_output_path"]
        base = os.path.join(task["output_dir"], name + ext)
        candidate = base
        with self._output_lock:
            if candidate in self._reserved_outputs or os.path.exists(candidate):
                candidate = os.path.join(task["output_dir"], f"{name}_{task['id'][:8]}{ext}")
            suffix = 1
            while candidate in self._reserved_outputs or os.path.exists(candidate):
                candidate = os.path.join(task["output_dir"], f"{name}_{task['id'][:8]}_{suffix}{ext}")
                suffix += 1
            self._reserved_outputs.add(candidate)
        task["_output_path"] = candidate
        return candidate

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=SSE_QUEUE_SIZE)
        with self._lock:
            self._log_queues.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            if q in self._log_queues:
                self._log_queues.remove(q)

    def emit_log(self, message: str, level: str = "INFO"):
        with self._lock:
            self._logs.append(str(message))
            queues = list(self._log_queues)
        _ = level  # 兼容 main.safe_log；UI 暂不按级别分色
        for q in queues:
            try:
                q.put_nowait(message)
            except queue.Full:
                pass

    def clear_logs(self):
        with self._lock:
            self._logs.clear()

    def export_logs(self, file_path: str):
        with self._lock:
            content = "\n".join(self._logs)
        Path(file_path).write_text(content, encoding="utf-8-sig")

    def shutdown(self):
        self.batch.shutdown()

    def set_status(self, status: str, message: str = ""):
        with self._lock:
            self.status = status
            self.last_message = message
            queues = list(self._log_queues)
        payload = json.dumps({"type": "status", "status": status, "message": message}, ensure_ascii=False)
        for q in queues:
            try:
                q.put_nowait(f"__EVENT__:{payload}")
            except queue.Full:
                pass

    @staticmethod
    def default_output_dir() -> str:
        """Return the user-facing default output directory.

        macOS localizes ``Documents`` as “文稿” in Finder.
        """
        return dwglot_output_dir()

    def save_config(self, deepl_key: str, output_dir: str = "", provider: str = "deepl", azure_key: str = "", azure_region: str = "", project_package_path: Optional[str] = None, **extra):
        config = self.load_config()
        config["deepl_key"] = deepl_key.strip()
        config["provider"] = provider
        config["azure_key"] = azure_key.strip()
        config["azure_region"] = azure_region.strip()
        if project_package_path is not None:
            config["project_package_path"] = project_package_path.strip()
        if output_dir:
            config["output_dir"] = output_dir
        for key in ("openai_key", "openai_base", "openai_model", "ollama_host", "ollama_model"):
            if key in extra and extra[key] is not None:
                config[key] = str(extra[key]).strip()
        config.setdefault("output_dir", self.default_output_dir())
        atomic_write_json(CONFIG_PATH, config)

    def load_config(self) -> dict:
        defaults = {
            "deepl_key": "",
            "provider": "deepl",
            "azure_key": "",
            "azure_region": "",
            "output_dir": self.default_output_dir(),
            "project_package_path": "",
            "openai_key": "",
            "openai_base": "",
            "openai_model": "",
            "ollama_host": "",
            "ollama_model": "",
        }

        def _normalize(raw: object) -> dict:
            data = dict(defaults)
            if not isinstance(raw, dict):
                raise ValueError("config must be an object")
            for key, fallback in defaults.items():
                value = raw.get(key, fallback)
                if value is None:
                    data[key] = fallback
                    continue
                text = str(value).strip()
                if key == "provider":
                    data[key] = text if text in ENGINE_PROVIDERS else "deepl"
                else:
                    data[key] = text
            return data

        if not os.path.exists(CONFIG_PATH):
            return dict(defaults)
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
                return _normalize(json.load(handle))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            quarantine_corrupt_file(CONFIG_PATH)
            return dict(defaults)

    @staticmethod
    def deepl_usage(key: str) -> dict:
        if not key.strip():
            return {"available": False, "message": "未配置 DeepL Key"}
        endpoint = "https://api-free.deepl.com/v2/usage" if key.strip().endswith(":fx") else "https://api.deepl.com/v2/usage"
        request = urllib.request.Request(endpoint, headers={"Authorization": f"DeepL-Auth-Key {key.strip()}"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return {"available": True, "characters": payload.get("character_count", 0), "limit": payload.get("character_limit", 0)}
        except Exception as exc:
            return {"available": False, "message": f"DeepL 用量读取失败: {exc}"}

    def validate(self, body: TranslateBody) -> Optional[str]:
        if not body.input_file:
            return "请选择输入文件"
        if not os.path.exists(body.input_file):
            return "输入文件不存在"
        if not body.input_file.lower().endswith((".dxf", ".dwg")):
            return "请选择 DXF 或 DWG 文件"
        if body.input_file.lower().endswith(".dwg") and not odafc_available():
            return dwg_unavailable_short()
        if not body.output_dir:
            return "请选择输出目录"
        if not os.path.exists(body.output_dir):
            return "输出目录不存在"
        if not body.output_name.strip():
            return "请输入输出文件名"
        try:
            split_mode(body.translation_mode)
        except ValueError:
            return "不支持的翻译方向"
        if body.provider not in ENGINE_PROVIDERS:
            return "不支持的翻译服务"
        engine = self._engine_from(self.load_config(), body.model_dump())
        if not self._engine_ready(body.provider, engine):
            return self._engine_missing_message(body.provider, engine)
        return None

    def start_translation(self, body: TranslateBody):
        with self._lock:
            if self.status == "running":
                raise HTTPException(status_code=409, detail="翻译任务正在进行中")

        err = self.validate(body)
        if err:
            raise HTTPException(status_code=400, detail=err)

        self.save_config(
            body.deepl_key,
            provider=body.provider,
            azure_key=body.azure_key,
            azure_region=body.azure_region,
            project_package_path=body.project_package_path,
            openai_key=body.openai_key,
            openai_base=body.openai_base,
            openai_model=body.openai_model,
            ollama_host=body.ollama_host,
            ollama_model=body.ollama_model,
        )
        self.set_status("running", "翻译中...")
        self.emit_log("=" * 40)
        self.emit_log("开始翻译任务")

        def worker():
            translator = CADChineseTranslator(log_callback=self.emit_log)
            translator.configure_language_assets(body.project_package_path)
            engine = self._engine_from(self.load_config(), body.model_dump())
            translator.configure_engine(body.provider, **engine)
            if not translator.has_mt():
                message = self._engine_missing_message(body.provider, engine)
                self.emit_log(message)
                self.set_status("error", message)
                return

            try:
                meta = analyze_source(body.input_file)
                output_file = output_path_for(
                    meta, body.output_dir, body.output_name.strip()
                )
                translator.translate_cad_file(
                    body.input_file,
                    output_file,
                    body.translation_mode,
                    body.translate_blocks,
                )
                self.emit_log("=" * 40)
                self.set_status("success", "翻译完成！")
            except AzureFreeQuotaExceededError as exc:
                self.emit_log(str(exc))
                self.set_status("error", str(exc))
            except Exception as exc:
                text = _chinese_detail(exc, "翻译失败")
                self.emit_log(text)
                self.set_status("error", text)

        threading.Thread(target=worker, daemon=True).start()


service = TranslationService()
app = FastAPI(title=APP_TITLE)


@app.get("/api/health")
def health():
    return {"ok": True, "status": service.status, "name": APP_TITLE, "version": APP_VERSION}


@app.get("/api/meta")
def app_meta():
    return {
        "name_zh": "图译",
        "name_en": "Dwglot",
        "title": APP_TITLE,
        "version": APP_VERSION,
        "github": GITHUB_URL,
        "licensing_enabled": False,
    }


@app.get("/api/updates/check")
def updates_check():
    try:
        return check_github_release()
    except Exception:
        return unavailable_payload("GitHub API 暂不可用，打开 Releases 页查看")


@app.post("/api/drawings/open")
async def drawings_open(files: list[UploadFile] = File(default=[])):
    if not files:
        raise HTTPException(status_code=400, detail="请选择 CAD 文件")
    paths = service.save_dropped_files(files)
    return {
        "files": [
            {"path": path, "name": Path(path).name, "ext": Path(path).suffix[1:].upper()}
            for path in paths
        ]
    }


@app.post("/api/drawings/extract")
def drawings_extract(body: ExtractBody):
    try:
        split_mode(body.translation_mode)
        return extract_preview(
            body.path,
            include_blocks=body.include_blocks,
            mode=body.translation_mode,
            include_model=body.include_model,
            include_paper=body.include_paper,
            include_attribs=body.include_attribs,
            include_frozen=body.include_frozen,
            include_locked=body.include_locked,
            include_off=body.include_off,
            enable_v02=body.enable_v02,
            skip_numbers=body.skip_numbers,
            skip_dupes=body.skip_dupes,
            skip_nonsource=body.skip_nonsource,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_chinese_detail(exc, "图纸不存在")) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=_chinese_detail(exc, "提取失败")) from exc
    except Exception:
        raise HTTPException(status_code=400, detail="提取失败") from None


@app.post("/api/drawings/translate")
def drawings_translate(body: TranslateRowsBody):
    if body.provider not in ENGINE_PROVIDERS:
        raise HTTPException(status_code=400, detail="不支持的翻译服务")
    try:
        split_mode(body.translation_mode)
        config = service.load_config()
        engine = service._engine_from(config, body.model_dump())
        return translate_rows(
            body.items,
            mode=body.translation_mode,
            provider=body.provider,
            project_package_path=body.project_package_path or config.get("project_package_path", ""),
            engine=engine,
            skip_numbers=body.skip_numbers,
            skip_dupes=body.skip_dupes,
            skip_nonsource=body.skip_nonsource,
        )
    except (ValueError, TranslationProviderError) as exc:
        raise HTTPException(status_code=400, detail=_chinese_detail(exc, "翻译失败")) from exc
    except Exception:
        raise HTTPException(status_code=400, detail="翻译失败") from None


@app.post("/api/drawings/writeback")
def drawings_writeback(body: WritebackBody):
    try:
        split_mode(body.translation_mode)
        output_dir = body.output_dir or service.load_config().get("output_dir") or service.default_output_dir()
        ensure_output_dir(output_dir)
        named = body.output_name.strip()
        if body.translate_filename:
            named = default_output_name(
                body.translation_mode, Path(body.input_file).stem, translate_filename=True
            )["name"]
        elif not named:
            named = default_output_name(body.translation_mode, Path(body.input_file).stem)["name"]
        return writeback_rows(
            body.input_file,
            body.items,
            output_dir=output_dir,
            output_name=named,
            mode=body.translation_mode,
            include_blocks=body.include_blocks,
            style=body.style,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="图纸不存在")
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=_chinese_detail(exc, "写回失败")) from exc
    except Exception:
        raise HTTPException(status_code=400, detail="写回失败") from None


@app.post("/api/drawings/export-pdf")
def drawings_export_pdf(body: PdfBody):
    try:
        output_dir = body.output_dir or service.load_config().get("output_dir") or service.default_output_dir()
        ensure_output_dir(output_dir)
        name = (body.output_name or Path(body.path).stem).strip()
        if not name.lower().endswith(".pdf"):
            name = f"{name}.pdf"
        dest = str(Path(output_dir) / Path(name).name)
        result = export_pdf(body.path, dest, body.layout, style=body.style, items=body.items)
        if body.print_after:
            result["print"] = print_pdf(result["path"])
        return result
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_chinese_detail(exc, "图纸不存在")) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=_chinese_detail(exc, "导出 PDF 失败")) from exc
    except Exception:
        raise HTTPException(status_code=400, detail="导出 PDF 失败") from None


@app.post("/api/drawings/print")
def drawings_print(body: PdfBody):
    if not (body.path or "").strip():
        raise HTTPException(status_code=400, detail="先打开图纸。")
    if not os.path.isfile(body.path):
        raise HTTPException(status_code=400, detail="图纸不存在")
    try:
        exported = drawings_export_pdf(
            PdfBody(
                path=body.path,
                output_dir=body.output_dir,
                output_name=body.output_name,
                layout=body.layout,
                style=body.style,
                items=body.items,
                print_after=False,
            )
        )
        printed = print_pdf(exported["path"])
        exported["print"] = printed
        if not printed.get("ok"):
            exported["message"] = printed.get("message") or "系统打印失败，PDF 已留下"
        return exported
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=400, detail="图纸不存在") from exc
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=_chinese_detail(exc, "图纸不存在")) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=_chinese_detail(exc, "打印失败")) from exc
    except Exception:
        raise HTTPException(status_code=400, detail="打印失败") from None


@app.get("/api/config")
def get_config():
    return service.load_config()


@app.post("/api/config")
def post_config(body: ConfigBody):
    service.save_config(
        body.deepl_key,
        body.output_dir,
        body.provider,
        body.azure_key,
        body.azure_region,
        body.project_package_path,
        openai_key=body.openai_key,
        openai_base=body.openai_base,
        openai_model=body.openai_model,
        ollama_host=body.ollama_host,
        ollama_model=body.ollama_model,
    )
    return {"ok": True}


@app.get("/api/language-assets")
def get_language_assets():
    config = service.load_config()
    project_path = config.get("project_package_path", "")
    try:
        project = service.language_assets.project_info(project_path) if project_path else {"path": "", "name": "", "terms": []}
    except ValueError as exc:
        project = {"path": project_path, "name": "", "terms": [], "error": str(exc)}
    return {"project": project, "terms": service.language_assets.list_terms(project_path), "builtin_terms": builtin_terms(), "memory": service.language_assets.list_memory(), "usage": service.language_assets.usage()}


@app.post("/api/language-assets/project")
def select_project_package(body: ProjectPackageBody):
    try:
        project = service.language_assets.create_project(body.path, body.name) if body.create else service.language_assets.project_info(body.path, require_terms=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config = service.load_config()
    service.save_config(config["deepl_key"], config["output_dir"], config["provider"], config["azure_key"], config["azure_region"], project["path"])
    return project


@app.post("/api/language-assets/import")
def import_language_terms(body: ImportTermsBody):
    try:
        split_mode(body.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rows = list(body.terms)
    for line in (body.csv or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.lower().startswith("source"):
            continue
        if "," in line:
            source, target = line.split(",", 1)
        elif "\t" in line:
            source, target = line.split("\t", 1)
        else:
            continue
        rows.append({"source": source.strip(), "target": target.strip(), "mode": body.mode})
    count = 0
    for term in rows:
        source = str(term.get("source") or "").strip()
        target = str(term.get("target") or "").strip()
        if not source or not target:
            continue
        try:
            service.language_assets.upsert_term(
                body.scope,
                str(term.get("mode") or body.mode),
                source,
                target,
                str(term.get("layer_contains") or ""),
                body.project_package_path,
            )
            count += 1
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if count == 0:
        raise HTTPException(status_code=400, detail="术语表是空的")
    return {"ok": True, "count": count}


@app.post("/api/language-assets/terms")
def save_language_term(body: AssetTermBody):
    try:
        service.language_assets.upsert_term(body.scope, body.mode, body.source, body.target, body.layer_contains, body.project_package_path, body.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/language-assets/terms/delete")
def remove_language_term(body: AssetDeleteBody):
    service.language_assets.delete_term(body.scope, body.id, body.project_package_path)
    return {"ok": True}


@app.post("/api/language-assets/memory")
def save_translation_memory(body: AssetTermBody):
    try:
        service.language_assets.upsert_memory(body.mode, body.source, body.target, body.layer_contains, body.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/language-assets/memory/delete")
def remove_translation_memory(body: AssetDeleteBody):
    service.language_assets.delete_memory(body.id)
    return {"ok": True}


@app.post("/api/language-assets/usage")
def get_usage(body: UsageBody):
    config = service.load_config()
    return {"local": service.language_assets.usage(), "deepl_remote": service.deepl_usage(body.deepl_key or config.get("deepl_key", ""))}


@app.get("/api/changelog")
def get_changelog():
    path = resource_path("changelog.json")
    if not os.path.exists(path):
        return {"changelog": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/status")
def get_status():
    return {"status": service.status, "message": service.last_message}


@app.get("/api/system-theme")
def get_system_theme():
    return system_accent_theme()


@app.get("/api/odafc-status")
def get_odafc_status():
    return odafc_status()


@app.post("/api/translate")
def start_translate(body: TranslateBody):
    service.start_translation(body)
    return {"ok": True, "status": "running"}


@app.get("/api/batch")
def get_batch():
    return service.batch.snapshot()


@app.post("/api/logs/clear")
def clear_logs():
    service.clear_logs()
    return {"ok": True}


def batch_add_problem(path: str) -> str | None:
    if not path or not str(path).lower().endswith((".dxf", ".dwg")):
        return f"无效 CAD 文件: {path or '未命名文件'}"
    if not os.path.isfile(path):
        return "图纸不存在"
    return None


def batch_path_problem(path: str) -> str | None:
    problem = batch_add_problem(path)
    if problem:
        return problem
    if str(path).lower().endswith(".dwg") and not odafc_available():
        return dwg_unavailable_short()
    return None


def _fatal_batch_error(message: str) -> RuntimeError:
    error = RuntimeError(message)
    error.retryable = False
    return error


@app.post("/api/batch/add")
def add_batch(body: BatchBody):
    if not body.files:
        raise HTTPException(status_code=400, detail="请选择 CAD 文件")
    try:
        for path in body.files:
            problem = batch_add_problem(path)
            if problem:
                raise HTTPException(status_code=400, detail=problem)
        return service.batch.add(body.files)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc) or "无法加入队列") from exc


BATCH_IMPORT_NOT_READY = "批量导入还没做，请用常规处理写回"


@app.post("/api/batch/import")
def batch_import():
    raise HTTPException(status_code=501, detail=BATCH_IMPORT_NOT_READY)


@app.post("/api/batch/drop")
async def drop_batch(files: list[UploadFile] = File(default=[])):
    if not files:
        raise HTTPException(status_code=400, detail="请选择 CAD 文件")
    try:
        return service.batch.add(service.save_dropped_files(files, require_oda=False))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc) or "无法加入队列") from exc


@app.post("/api/batch/start")
def start_batch(body: BatchStartBody):
    output_dir = body.output_dir or service.load_config().get("output_dir") or service.default_output_dir()
    try:
        ensure_output_dir(output_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        split_mode(body.translation_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if body.provider not in ENGINE_PROVIDERS:
        raise HTTPException(status_code=400, detail="不支持的翻译服务")
    if body.output_format not in {"source", "dxf", "dwg"}:
        raise HTTPException(status_code=400, detail="不支持的输出格式")
    if body.output_version not in {"", *ODA_OUTPUT_VERSIONS}:
        raise HTTPException(status_code=400, detail="不支持的输出版本")
    service.batch.fail_missing(body.output_format)
    tasks = service.batch.snapshot()["tasks"]
    runnable = [
        task
        for task in tasks
        if not service.batch._unrunnable_reason(task, body.output_format)
    ]
    unfinished = [task for task in runnable if task["status"] in {"queued", "retrying", "cancelled", "failed"}]
    succeeded = [task for task in runnable if task["status"] == "succeeded"]
    running = any(task["status"] == "running" for task in tasks)
    if not unfinished and not succeeded:
        if running:
            snap = service.batch.snapshot()
            snap["message"] = "没有待处理的图纸"
            return snap
        raise HTTPException(status_code=400, detail="请选择 CAD 文件")
    include_succeeded = not unfinished and not running and bool(succeeded)
    service.save_config(
        body.deepl_key,
        output_dir,
        body.provider,
        body.azure_key,
        body.azure_region,
        body.project_package_path,
        openai_key=body.openai_key,
        openai_base=body.openai_base,
        openai_model=body.openai_model,
        ollama_host=body.ollama_host,
        ollama_model=body.ollama_model,
    )
    settings = body.model_dump()
    settings["output_dir"] = output_dir
    if body.provider == "azure":
        settings["api_key"] = body.azure_key
    elif body.provider == "openai":
        settings["api_key"] = body.openai_key
    elif body.provider == "ollama":
        settings["api_key"] = "ollama"
    else:
        settings["api_key"] = body.deepl_key
    return service.batch.start(settings, include_succeeded=include_succeeded)


@app.post("/api/batch/pause")
async def pause_batch(request: Request, paused: bool = True):
    try:
        payload = await request.json()
    except Exception:
        payload = None
    if isinstance(payload, dict) and "paused" in payload:
        paused = bool(payload["paused"])
    return service.batch.pause(paused)


@app.post("/api/batch/stop")
def stop_batch():
    return service.batch.stop()


@app.post("/api/batch/clear")
def clear_batch():
    try:
        return service.batch.clear()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/batch/{task_id}/remove")
def remove_batch_task(task_id: str):
    try:
        return service.batch.remove(task_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/batch/{task_id}/retry")
def retry_batch_task(task_id: str):
    return service.batch.retry(task_id)


@app.get("/api/logs/stream")
async def stream_logs():
    q = service.subscribe()

    async def generate():
        try:
            while True:
                try:
                    msg = await asyncio.get_event_loop().run_in_executor(None, q.get, True, 1.0)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                if isinstance(msg, str) and msg.startswith("__EVENT__:"):
                    yield f"data: {msg[10:]}\n\n"
                else:
                    payload = json.dumps({"type": "log", "message": str(msg)}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
        finally:
            service.unsubscribe(q)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/api/default-output-name")
def default_output_name(mode: str = "zh_to_en", base: str = "", translate_filename: bool = False):
    prefix = output_prefix(mode)
    ts = datetime.now().strftime("%Hh%M_%d-%m-%y")
    stem = base
    if translate_filename and base:
        config = service.load_config()
        translator = CADChineseTranslator()
        translator.configure_language_assets(config.get("project_package_path", ""))
        engine = service._engine_from(config, {})
        translator.configure_engine(config.get("provider") or "deepl", **engine)
        stem = translate_cjk_filename_stem(base, mode=mode, translator=translator)
    name = f"{prefix}_{stem}_{ts}" if stem else f"translated_cad_{ts}"
    return {"name": name}


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/")
    async def index():
        return FileResponse(FRONTEND_DIST / "index.html")

    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        file_path = FRONTEND_DIST / path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIST / "index.html")
