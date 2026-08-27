"""Persistent, bounded batch scheduler for CAD translations."""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Callable

from backend.app_meta import QUEUE_PATH, LEGACY_QUEUE_PATH, migrate_legacy_file
from backend.storage import atomic_write_json, quarantine_corrupt_file

migrate_legacy_file(LEGACY_QUEUE_PATH, QUEUE_PATH)
STATE_PATH = QUEUE_PATH
ACTIVE = {"queued", "retrying", "running"}
MAX_TASK_HISTORY = 100


class BatchQueue:
    def __init__(self, run: Callable[[dict, Callable[[str], None], threading.Event, threading.Event], str], emit: Callable[[str], None], key_for: Callable[[dict], str]):
        self.run, self.emit, self.key_for = run, emit, key_for
        self.lock = threading.RLock()
        self.oda_lock = threading.Lock()
        self.key_locks: dict[str, threading.BoundedSemaphore] = {}
        self.tasks: list[dict] = self._load()
        self.paused = False
        self.started = False
        self.resumable = any(task["status"] in {"queued", "retrying"} for task in self.tasks)
        self.resume_event = threading.Event()
        self.resume_event.set()
        self.cancel_event = threading.Event()

    def _load(self) -> list[dict]:
        try:
            payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("queue state must be an object")
            tasks = payload.get("tasks", [])
            if not isinstance(tasks, list):
                raise ValueError("queue tasks must be a list")
        except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
            quarantine_corrupt_file(STATE_PATH)
            return []
        recovered = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            if task.get("status") == "running":
                task["status"] = "queued"
                task["message"] = "应用重启后等待继续"
            recovered.append(task)
        return recovered

    def _save(self):
        # API keys intentionally never enter the persisted task model.
        self._prune_history()
        tasks = [{k: v for k, v in task.items() if not k.startswith("_")} for task in self.tasks]
        atomic_write_json(STATE_PATH, {"tasks": tasks})

    def _prune_history(self):
        finished = [task for task in self.tasks if task["status"] not in ACTIVE]
        if len(finished) > MAX_TASK_HISTORY:
            keep = {task["id"] for task in finished[-MAX_TASK_HISTORY:]}
            self.tasks[:] = [task for task in self.tasks if task["status"] in ACTIVE or task["id"] in keep]

    def snapshot(self):
        with self.lock:
            total = len(self.tasks)
            done = sum(t["status"] in {"succeeded", "failed"} for t in self.tasks)
            tasks = [{k: v for k, v in task.items() if not k.startswith("_")} for task in self.tasks]
            return {"tasks": tasks, "paused": self.paused, "started": self.started, "resumable": self.resumable, "progress": round(done * 100 / total) if total else 0}

    def add(self, files: list[str]):
        with self.lock:
            for path in files:
                self.tasks.append({
                    "id": uuid.uuid4().hex, "input_file": path,
                    "status": "queued", "progress": 0, "retries": 0, "output_file": "", "message": "等待中", "logs": [],
                })
            self._save()
        return self.snapshot()

    def remove(self, task_id: str):
        with self.lock:
            self.tasks = [t for t in self.tasks if not (t["id"] == task_id and t["status"] != "running")]
            self._save()
        return self.snapshot()

    def retry(self, task_id: str):
        with self.lock:
            task = self._task(task_id)
            if task and task["status"] in {"failed", "succeeded", "cancelled"}:
                task.pop("_output_path", None)
                task.update(status="queued", progress=0, message="等待重翻", output_file="")
                if self.cancel_event.is_set():
                    self.cancel_event = threading.Event()
                self.started = True
                self.paused = False
                self.resumable = False
                self.resume_event.set()
                self._save()
        if self.started:
            self._schedule()
        return self.snapshot()

    def pause(self, paused: bool):
        with self.lock:
            self.paused = paused
            if paused:
                self.resume_event.clear()
            else:
                self.resume_event.set()
            self._save()
        if not paused and self.started:
            self._schedule()
        return self.snapshot()

    def start(self, settings: dict | None = None):
        with self.lock:
            if self.cancel_event.is_set():
                self.cancel_event = threading.Event()
            if settings:
                for task in self.tasks:
                    if task["status"] in {"queued", "retrying", "cancelled", "failed"}:
                        task.update(
                            output_dir=settings["output_dir"], output_format=settings["output_format"],
                            output_version=settings["output_version"], translation_mode=settings["translation_mode"],
                            translate_blocks=settings["translate_blocks"], provider=settings.get("provider", "deepl"),
                            azure_region=settings.get("azure_region", ""), style=settings.get("style") or "纯译文",
                            status="queued", progress=0,
                            retries=0, output_file="", message="等待中", logs=[], _key=settings.get("api_key") or settings.get("deepl_key", ""),
                        )
                        task.pop("_output_path", None)
            self.started = True
            self.paused = False
            self.resumable = False
            self.resume_event.set()
        self._schedule()
        return self.snapshot()

    def shutdown(self):
        """Stop all work when the desktop window closes; keep it resumable."""
        with self.lock:
            self.started = False
            self.paused = True
            self.resumable = True
            self.cancel_event.set()
            self.resume_event.set()
            for task in self.tasks:
                if task["status"] == "running":
                    task.update(status="queued", message="应用关闭，可重新开始")
            self._save()

    def stop(self):
        with self.lock:
            self.started = False
            self.paused = False
            self.resumable = False
            self.cancel_event.set()
            self.resume_event.set()
            for task in self.tasks:
                if task["status"] in ACTIVE:
                    task.update(status="cancelled", message="已停止")
            self._save()
        return self.snapshot()

    def clear(self):
        with self.lock:
            if self.started or any(task["status"] == "running" for task in self.tasks):
                raise RuntimeError("请先停止队列")
            self.tasks.clear()
            self.resumable = False
            self._save()
        return self.snapshot()

    def _task(self, task_id: str):
        return next((t for t in self.tasks if t["id"] == task_id), None)

    def _schedule(self):
        with self.lock:
            if not self.started or self.paused or sum(t["status"] == "running" for t in self.tasks) >= 3:
                return
            for task in self.tasks:
                if task["status"] in {"queued", "retrying"}:
                    task["status"] = "running"
                    task["message"] = "运行中"
                    self._save()
                    threading.Thread(target=self._work, args=(task["id"],), daemon=True).start()
                    if sum(t["status"] == "running" for t in self.tasks) >= 3:
                        break

    def _work(self, task_id: str):
        with self.lock:
            task = self._task(task_id)
            if not task:
                return
            task["progress"] = 1
        def log(message: str, level: str = "INFO"):
            _ = level
            with self.lock:
                current = self._task(task_id)
                if current:
                    current["logs"] = (current["logs"] + [message])[-500:]
                    if "进度:" in message:
                        try: current["progress"] = int(float(message.rsplit("(", 1)[1].split("%", 1)[0]))
                        except (IndexError, ValueError): pass
                    self._save()
            self.emit(f"[{Path(task['input_file']).name}] {message}")
        try:
            cancel_event = self.cancel_event
            self.resume_event.wait()
            if cancel_event.is_set():
                raise InterruptedError("应用已关闭")
            key = task.get("_key") or self.key_for(task)
            limiter = self.key_locks.setdefault(key, threading.BoundedSemaphore(2))
            with limiter:
                # ponytail: one ODA lock for all DWG phases; split locks only if conversion throughput matters.
                lock = self.oda_lock if task["input_file"].lower().endswith(".dwg") or task.get("output_format") == "dwg" else _NullLock()
                with lock:
                    output = self.run(task, log, self.resume_event, cancel_event)
            if cancel_event.is_set():
                raise InterruptedError("translation stopped")
            with self.lock:
                task.update(status="succeeded", progress=100, output_file=output, message="成功")
        except Exception as exc:
            retry_delay = None
            retryable = _retryable(exc)
            message = _calm_error(exc)
            with self.lock:
                if cancel_event.is_set():
                    if task["status"] != "cancelled":
                        task.update(status="queued", message="应用关闭，可重新开始")
                    return
                if not retryable:
                    task.update(status="failed", message=message)
                else:
                    task["retries"] += 1
                if retryable and task["retries"] <= 3:
                    retry_delay = 2 ** task["retries"]
                    task.update(status="retrying", message=f"失败，{retry_delay} 秒后重试: {message}")
                    self._save()
                elif retryable:
                    task.update(status="failed", message=message)
            if retry_delay:
                if cancel_event.wait(retry_delay):
                    return
                with self.lock:
                    if task["status"] == "retrying":
                        task["status"] = "queued"
        finally:
            with self.lock:
                task = self._task(task_id)
                if task: task.pop("_key", None)
                if not any(task["status"] in ACTIVE for task in self.tasks):
                    self.started = False
                self._save()
            self._schedule()

def _retryable(exc: BaseException) -> bool:
    flagged = getattr(exc, "retryable", None)
    if flagged is not None:
        return bool(flagged)
    if isinstance(exc, (FileNotFoundError, PermissionError, ValueError)):
        return False
    text = str(exc)
    if "ODA" in text or "图纸不存在" in text or "无效 CAD" in text or "无法读取DXF" in text:
        return False
    return True


def _calm_error(exc: BaseException) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    first = text.splitlines()[0].strip()
    if "Traceback" in text or len(first) > 240:
        if "ODA" in text:
            return "未检测到 ODA，无法处理 DWG；请安装 ODA 或将 DWG 另存为 DXF"
        return first[:240]
    return first


class _NullLock:
    def __enter__(self): return self
    def __exit__(self, *args): return False
