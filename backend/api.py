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

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.app_meta import APP_TITLE, APP_VERSION, GITHUB_URL, default_output_dir as dwglot_output_dir
from backend.providers.azure import AzureFreeQuotaExceededError
from backend.queue import BatchQueue
from backend.cad import ODA_OUTPUT_VERSIONS, analyze_source, dwg_unavailable_short, odafc_available, odafc_status, output_path_for
from backend.translator import CADChineseTranslator, CONFIG_PATH, load_yaml_data, output_prefix, resource_path
from backend.language_assets import LanguageAssets
from backend.storage import atomic_write_json, quarantine_corrupt_file
from backend.updates import check_github_release
from backend.drawings import extract_preview
from backend.languages import split_mode


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


class TranslateBody(BaseModel):
    input_file: str
    output_dir: str
    output_name: str
    translation_mode: str = "zh_to_en"
    translate_blocks: bool = False
    deepl_key: str
    provider: str = "deepl"
    azure_key: str = ""
    azure_region: str = ""
    project_package_path: str = ""


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

    def save_dropped_files(self, files: list[UploadFile]) -> list[str]:
        paths = []
        for upload in files:
            name = Path(upload.filename or "").name
            if not name.lower().endswith((".dxf", ".dwg")):
                raise HTTPException(status_code=400, detail=f"无效 CAD 文件: {name or '未命名文件'}")
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
        provider = task.get("provider", "deepl")
        config = self.load_config()
        key = task.get("_key") or config.get(f"{provider}_key", "")
        if not key:
            raise RuntimeError(f"请配置 {'Azure Translator' if provider == 'azure' else 'DeepL'} API Key 后继续队列")
        fmt = task.get("output_format", "source")
        ext = os.path.splitext(task["input_file"])[1] if fmt == "source" else f".{fmt}"
        name = f"{output_prefix(task['translation_mode'])}_{Path(task['input_file']).stem}"
        output = self.reserve_output(task, name, ext)
        translator = CADChineseTranslator(log_callback=log)
        translator.configure_language_assets(task.get("project_package_path") or config.get("project_package_path", ""))
        if provider == "azure":
            translator.configure_azure(key, task.get("azure_region") or config.get("azure_region", ""))
        else:
            translator.deepl_api_key = key
            if not translator.deepl_translator:
                raise RuntimeError("DeepL 初始化失败，请检查 API Key")
        translator.translate_cad_file(task["input_file"], output, task["translation_mode"], task["translate_blocks"], fmt, task.get("output_version", ""), resume_event, cancel_event)
        return output

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

    def save_config(self, deepl_key: str, output_dir: str = "", provider: str = "deepl", azure_key: str = "", azure_region: str = "", project_package_path: Optional[str] = None):
        config = self.load_config()
        config["deepl_key"] = deepl_key.strip()
        config["provider"] = provider
        config["azure_key"] = azure_key.strip()
        config["azure_region"] = azure_region.strip()
        if project_package_path is not None:
            config["project_package_path"] = project_package_path.strip()
        if output_dir:
            config["output_dir"] = output_dir
        config.setdefault("output_dir", self.default_output_dir())
        atomic_write_json(CONFIG_PATH, config)

    def load_config(self) -> dict:
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except (OSError, ValueError, TypeError):
                quarantine_corrupt_file(CONFIG_PATH)
                config = {}
            config.setdefault("output_dir", self.default_output_dir())
            config.setdefault("provider", "deepl")
            config.setdefault("deepl_key", "")
            config.setdefault("azure_key", "")
            config.setdefault("azure_region", "")
            config.setdefault("project_package_path", "")
            return config
        return {"deepl_key": "", "provider": "deepl", "azure_key": "", "azure_region": "", "output_dir": self.default_output_dir(), "project_package_path": ""}

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
        if body.translation_mode not in {"zh_to_fr", "fr_to_zh", "zh_to_en", "en_to_zh"}:
            return "不支持的翻译方向"
        if body.provider not in {"deepl", "azure"}:
            return "不支持的翻译服务"
        if not (body.azure_key if body.provider == "azure" else body.deepl_key).strip():
            return f"请配置 {'Azure Translator' if body.provider == 'azure' else 'DeepL'} API Key"
        return None

    def start_translation(self, body: TranslateBody):
        with self._lock:
            if self.status == "running":
                raise HTTPException(status_code=409, detail="翻译任务正在进行中")

        err = self.validate(body)
        if err:
            raise HTTPException(status_code=400, detail=err)

        self.save_config(body.deepl_key, provider=body.provider, azure_key=body.azure_key, azure_region=body.azure_region, project_package_path=body.project_package_path)
        self.set_status("running", "翻译中...")
        self.emit_log("=" * 40)
        self.emit_log("开始翻译任务")

        def worker():
            translator = CADChineseTranslator(log_callback=self.emit_log)
            translator.configure_language_assets(body.project_package_path)
            if body.provider == "azure":
                translator.configure_azure(body.azure_key, body.azure_region)
            else:
                translator.deepl_api_key = body.deepl_key.strip()
                if not translator.deepl_translator:
                    self.emit_log("DeepL 初始化失败，请检查 API Key")
                    self.set_status("error", "DeepL 初始化失败")
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
            except Exception:
                import traceback
                self.emit_log(f"ERROR: {traceback.format_exc()}")
                self.set_status("error", "翻译失败")

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
    return check_github_release()


class ExtractBody(BaseModel):
    path: str
    include_blocks: bool = False
    translation_mode: str = "zh_to_en"


@app.post("/api/drawings/extract")
def drawings_extract(body: ExtractBody):
    try:
        split_mode(body.translation_mode)
        return extract_preview(body.path, include_blocks=body.include_blocks, mode=body.translation_mode)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"提取失败: {exc}") from exc


@app.get("/api/config")
def get_config():
    return service.load_config()


@app.post("/api/config")
def post_config(body: ConfigBody):
    service.save_config(body.deepl_key, body.output_dir, body.provider, body.azure_key, body.azure_region, body.project_package_path)
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
        project = service.language_assets.create_project(body.path, body.name) if body.create else service.language_assets.project_info(body.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config = service.load_config()
    service.save_config(config["deepl_key"], config["output_dir"], config["provider"], config["azure_key"], config["azure_region"], project["path"])
    return project


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


@app.post("/api/batch/add")
def add_batch(body: BatchBody):
    if not body.files:
        raise HTTPException(status_code=400, detail="请选择 CAD 文件")
    for path in body.files:
        if not os.path.isfile(path) or not path.lower().endswith((".dxf", ".dwg")):
            raise HTTPException(status_code=400, detail=f"无效 CAD 文件: {path}")
    return service.batch.add(body.files)


@app.post("/api/batch/drop")
async def drop_batch(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="请选择 CAD 文件")
    return service.batch.add(service.save_dropped_files(files))


@app.post("/api/batch/start")
def start_batch(body: BatchStartBody):
    output_dir = body.output_dir or service.load_config().get("output_dir") or service.default_output_dir()
    os.makedirs(output_dir, exist_ok=True)
    try:
        split_mode(body.translation_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if body.provider not in {"deepl", "azure", "openai", "ollama"}:
        raise HTTPException(status_code=400, detail="不支持的翻译服务")
    if body.output_format not in {"source", "dxf", "dwg"}:
        raise HTTPException(status_code=400, detail="不支持的输出格式")
    if body.output_version not in {"", *ODA_OUTPUT_VERSIONS}:
        raise HTTPException(status_code=400, detail="不支持的输出版本")
    if body.provider == "azure" and not body.azure_key.strip():
        raise HTTPException(status_code=400, detail="请配置 Azure Translator Key")
    if body.provider == "deepl" and not body.deepl_key.strip():
        raise HTTPException(status_code=400, detail="请配置 DeepL API Key")
    for task in service.batch.snapshot()["tasks"]:
        if task["status"] in {"queued", "retrying", "cancelled", "failed"} and (task["input_file"].lower().endswith(".dwg") or body.output_format == "dwg") and not odafc_available():
            raise HTTPException(status_code=400, detail=dwg_unavailable_short())
    service.save_config(body.deepl_key, output_dir, body.provider, body.azure_key, body.azure_region, body.project_package_path)
    settings = body.model_dump()
    settings["output_dir"] = output_dir
    settings["api_key"] = body.azure_key if body.provider == "azure" else body.deepl_key
    return service.batch.start(settings)


@app.post("/api/batch/pause")
def pause_batch(paused: bool = True):
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
    return service.batch.remove(task_id)


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
def default_output_name(mode: str = "zh_to_en", base: str = ""):
    prefix = output_prefix(mode)
    ts = datetime.now().strftime("%Hh%M_%d-%m-%y")
    name = f"{prefix}_{base}_{ts}" if base else f"translated_cad_{ts}"
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
