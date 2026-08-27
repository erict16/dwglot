"""Small no-network checks for persistent batch scheduling."""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from collections import deque
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ezdxf
from fastapi.testclient import TestClient

from backend import queue as batch_queue
from backend.drawings import extract_preview
from backend.providers.azure import AzureFreeQuotaExceededError
from backend.storage import atomic_output_path
from backend.api import DROPPED_FILE_RETENTION_SECONDS, SSE_QUEUE_SIZE, TranslationService, app, service
from backend.queue import _calm_error, _retryable

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EMPTY_ENGINE = {
    "deepl_key": "",
    "azure_key": "",
    "azure_region": "",
    "openai_key": "",
    "openai_base": "",
    "openai_model": "",
    "ollama_host": "",
    "ollama_model": "",
    "provider": "deepl",
    "output_dir": "",
    "project_package_path": "",
}


class BatchQueueTests(unittest.TestCase):
    def test_queue_recovery_settings_cleanup_and_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            def wait_for_terminal(queue):
                deadline = time.monotonic() + 2
                while queue.snapshot()["tasks"][0]["status"] in batch_queue.ACTIVE and time.monotonic() < deadline:
                    time.sleep(.01)
                assert queue.snapshot()["tasks"][0]["status"] not in batch_queue.ACTIVE

            batch_queue.STATE_PATH = Path(tmp) / "queue.json"
            batch_queue.STATE_PATH.write_text(json.dumps({"tasks": [{"id": "old", "status": "running", "input_file": str(Path(tmp) / "alive.dxf")}]}), encoding="utf-8")
            (Path(tmp) / "alive.dxf").write_bytes(b"0\nEOF\n")
            probe = object.__new__(batch_queue.BatchQueue)
            assert batch_queue.BatchQueue._load(probe)[0]["status"] == "queued"
            gone = Path(tmp) / "gone.dxf"
            batch_queue.STATE_PATH.write_text(
                json.dumps({"tasks": [{"id": "ghost", "status": "queued", "input_file": str(gone), "message": "等待中"}]}),
                encoding="utf-8",
            )
            ghost = batch_queue.BatchQueue(lambda *_: "out.dxf", lambda _: None, lambda _: "k")
            assert ghost.tasks[0]["status"] == "failed"
            assert "图纸不存在" in ghost.tasks[0]["message"]
            batch_queue.STATE_PATH.write_text("{not json", encoding="utf-8")
            assert batch_queue.BatchQueue._load(probe) == []
            assert list(Path(tmp).glob("queue.json.corrupt-*"))
            batch_queue.STATE_PATH.write_text("[]", encoding="utf-8")
            assert batch_queue.BatchQueue._load(probe) == []
            batch_queue.STATE_PATH.write_text("null", encoding="utf-8")
            assert batch_queue.BatchQueue._load(probe) == []
            batch_queue.STATE_PATH.write_text(
                json.dumps({"tasks": [{"id": "old", "status": "running", "input_file": str(Path(tmp) / "alive.dxf")}]}),
                encoding="utf-8",
            )
            ran = []
            def run(task, log, resume_event, cancel_event):
                resume_event.wait()
                log("进度: 1/1 (100.0%)", level="INFO")
                ran.append(task["id"])
                return "out.dxf"
            q = batch_queue.BatchQueue(run, lambda _: None, lambda _: "secret")
            assert q.resumable  # recovered work requires an explicit continue
            q.tasks = []
            q.pause(True)
            settings = {"output_dir": tmp, "translation_mode": "zh_to_en", "translate_blocks": False, "output_format": "source", "output_version": "", "deepl_key": "secret"}
            one = Path(tmp) / "one.dxf"
            two = Path(tmp) / "two.dxf"
            one.write_bytes(b"0\nEOF\n")
            two.write_bytes(b"0\nEOF\n")
            q.add([str(one), str(two)])
            first = q.snapshot()["tasks"][0]["id"]
            q.remove(first)
            assert len(q.snapshot()["tasks"]) == 1  # queued items can be removed
            assert "secret" not in str(q.snapshot())
            assert "provider" not in q.snapshot()["tasks"][0]  # settings are applied only at start
            task_id = q.snapshot()["tasks"][0]["id"]
            q.pause(False)
            assert q.snapshot()["tasks"][0]["status"] == "queued" and not ran
            q.pause(True)
            assert not q.resume_event.is_set()
            q.pause(False)
            assert q.resume_event.is_set()
            q.start(settings)
            wait_for_terminal(q)
            assert q.snapshot()["tasks"][0]["status"] == "succeeded"
            q.retry(task_id)
            wait_for_terminal(q)
            assert q.snapshot()["tasks"][0]["status"] == "succeeded"
            q.tasks[0]["status"] = "failed"
            replacement = {"output_dir": tmp, "translation_mode": "en_to_zh", "output_format": "dwg", "output_version": "ACAD2018", "translate_blocks": False, "provider": "azure", "azure_region": "eastus", "api_key": "azure-key"}
            q.start(replacement)
            assert q.tasks[0]["translation_mode"] == "en_to_zh" and q.tasks[0]["output_format"] == "dwg" and q.tasks[0]["provider"] == "azure"
            # The persisted model is allowed to contain task inputs, never the key.
            q._save()
            assert "secret" not in batch_queue.STATE_PATH.read_text(encoding="utf-8")
            assert "azure-key" not in batch_queue.STATE_PATH.read_text(encoding="utf-8")
            q.shutdown()
            assert q.cancel_event.is_set() and not q.started
            q.clear()
            assert not q.tasks

            def fail(*_):
                raise OSError("temporary network error")
            retry_queue = batch_queue.BatchQueue(fail, lambda _: None, lambda _: "secret")
            retry_dxf = Path(tmp) / "retry.dxf"
            retry_dxf.write_bytes(b"0\nEOF\n")
            retry_queue.add([str(retry_dxf)])
            retry_queue.start(settings)
            deadline = time.monotonic() + 1
            while retry_queue.snapshot()["tasks"][0]["status"] != "retrying" and time.monotonic() < deadline:
                time.sleep(.01)
            assert retry_queue.snapshot()["tasks"][0]["status"] == "retrying"
            started = time.monotonic()
            retry_queue.stop()
            assert time.monotonic() - started < .5  # retry backoff must not hold the queue lock
            time.sleep(.1)  # let the cancelled worker complete its final state save

            quota_queue = batch_queue.BatchQueue(lambda *_: (_ for _ in ()).throw(AzureFreeQuotaExceededError("F0 quota exceeded")), lambda _: None, lambda _: "azure-key")
            quota_dxf = Path(tmp) / "quota.dxf"
            quota_dxf.write_bytes(b"0\nEOF\n")
            quota_queue.add([str(quota_dxf)])
            quota_queue.start({**settings, "provider": "azure", "api_key": "azure-key"})
            deadline = time.monotonic() + 1
            while quota_queue.snapshot()["tasks"][-1]["status"] != "failed" and time.monotonic() < deadline:
                time.sleep(.01)
            quota_task = quota_queue.snapshot()["tasks"][-1]
            assert quota_task["status"] == "failed" and quota_task["retries"] == 0
            assert "azure-key" not in batch_queue.STATE_PATH.read_text(encoding="utf-8")

            providers = []
            recovered_queue = batch_queue.BatchQueue(run, lambda _: None, lambda task: providers.append(task["provider"]) or "azure-key")
            recovered_queue.tasks = []
            azure_dxf = Path(tmp) / "azure.dxf"
            azure_dxf.write_bytes(b"0\nEOF\n")
            recovered_queue.add([str(azure_dxf)])
            recovered_queue.start({**settings, "provider": "azure", "deepl_key": "", "api_key": ""})
            wait_for_terminal(recovered_queue)
            assert providers == ["azure"]

            dropped_service = object.__new__(TranslationService)
            dropped_service.dropped_files_dir = Path(tmp) / "dropped"
            dropped = TranslationService.save_dropped_files(
                dropped_service, [SimpleNamespace(filename="plan.dxf", file=BytesIO(b"dxf"))]
            )
            assert Path(dropped[0]).name == "plan.dxf" and Path(dropped[0]).read_bytes() == b"dxf"

            output_service = object.__new__(TranslationService)
            output_service._output_lock = threading.Lock()
            output_service._reserved_outputs = set()
            first_output = TranslationService.reserve_output(
                output_service, {"id": "firsttask", "output_dir": tmp}, "fr_plan", ".dxf"
            )
            second_output = TranslationService.reserve_output(
                output_service, {"id": "secondtask", "output_dir": tmp}, "fr_plan", ".dxf"
            )
            assert first_output != second_output

            target = Path(tmp) / "atomic-output.dxf"
            target.write_text("old", encoding="utf-8")
            with atomic_output_path(target) as temporary_output:
                Path(temporary_output).write_text("new", encoding="utf-8")
            assert target.read_text(encoding="utf-8") == "new"
            try:
                with atomic_output_path(target) as temporary_output:
                    Path(temporary_output).write_text("partial", encoding="utf-8")
                    raise RuntimeError("simulate interrupted output")
            except RuntimeError:
                pass
            assert target.read_text(encoding="utf-8") == "new"

            stream_service = object.__new__(TranslationService)
            stream_service._lock = threading.Lock()
            stream_service._log_queues = []
            assert TranslationService.subscribe(stream_service).maxsize == SSE_QUEUE_SIZE

            cleanup_service = object.__new__(TranslationService)
            cleanup_service.dropped_files_dir = Path(tmp) / "cleanup"
            stale = cleanup_service.dropped_files_dir / "stale"
            stale.mkdir(parents=True)
            os.utime(stale, (time.time() - DROPPED_FILE_RETENTION_SECONDS - 1,) * 2)
            cleanup_service.batch = SimpleNamespace(snapshot=lambda: {"tasks": []})
            TranslationService.cleanup_dropped_files(cleanup_service)
            assert not stale.exists()

            log_service = object.__new__(TranslationService)
            log_service._lock = threading.Lock()
            log_service._logs = deque(maxlen=2)
            log_service._log_queues = []
            TranslationService.emit_log(log_service, "first log")
            TranslationService.emit_log(log_service, "second log")
            TranslationService.emit_log(log_service, "third log")
            log_path = Path(tmp) / "logs.txt"
            TranslationService.export_logs(log_service, str(log_path))
            assert log_path.read_text(encoding="utf-8-sig") == "second log\nthird log"

            # Task status becomes terminal immediately before its final durable state save.
            # Keep the temporary test directory alive until those daemon workers exit.
            time.sleep(.2)


class BatchApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._tasks = list(service.batch.tasks)
        self._started = service.batch.started
        service.batch.tasks = []
        service.batch.started = False
        self.client = TestClient(app)

    def tearDown(self):
        service.batch.stop()
        service.batch.tasks = self._tasks
        service.batch.started = self._started
        self.tmp.cleanup()

    def test_add_empty_is_400(self):
        response = self.client.post("/api/batch/add", json={"files": []})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("CAD", response.json()["detail"])
        self.assertNotIn("Traceback", response.text)

    def test_add_missing_path_is_400(self):
        response = self.client.post("/api/batch/add", json={"files": [str(Path(self.tmp.name) / "nope.dxf")]})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("不存在", response.json()["detail"])
        self.assertNotIn("Traceback", response.text)

    def test_add_and_drop_dwg_without_oda_is_queued(self):
        from backend.cad import odafc_available

        if odafc_available():
            self.skipTest("ODA is installed")
        dwg = Path(self.tmp.name) / "x.dwg"
        dwg.write_bytes(b"AC1032" + b"\x00" * 16)
        added = self.client.post("/api/batch/add", json={"files": [str(dwg)]})
        self.assertEqual(added.status_code, 200, added.text)
        self.assertNotIn("Traceback", added.text)
        started = self.client.post(
            "/api/batch/start",
            json={"provider": "deepl", "deepl_key": "", "output_dir": self.tmp.name, "translation_mode": "zh_to_en"},
        )
        self.assertEqual(started.status_code, 400, started.text)
        self.assertIn("CAD", started.json()["detail"])
        self.assertEqual(service.batch.tasks[0]["status"], "failed")
        self.assertIn("ODA", service.batch.tasks[0]["message"])
        with dwg.open("rb") as handle:
            dropped = self.client.post("/api/batch/drop", files={"files": ("x.dwg", handle, "application/acad")})
        self.assertEqual(dropped.status_code, 200, dropped.text)
        self.assertNotIn("Traceback", dropped.text)

    def test_batch_import_is_not_ready(self):
        response = self.client.post("/api/batch/import", json={})
        self.assertEqual(response.status_code, 501, response.text)
        self.assertIn("还没做", response.json()["detail"])
        self.assertNotIn("Traceback", response.text)

    def test_start_empty_is_400(self):
        response = self.client.post("/api/batch/start", json={"deepl_key": "key", "output_dir": self.tmp.name})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("CAD", response.json()["detail"])
        self.assertNotIn("Traceback", response.text)

    def test_glossary_only_floor_plan_succeeds_without_engine(self):
        fixture = FIXTURES / "floor_plan.dxf"
        self.assertTrue(fixture.is_file(), "tests/fixtures/floor_plan.dxf")
        src = Path(self.tmp.name) / "floor_plan.dxf"
        shutil.copy(fixture, src)
        config = dict(EMPTY_ENGINE, output_dir=self.tmp.name)
        with patch.object(service, "load_config", return_value=config), patch.object(service, "save_config"), patch(
            "urllib.request.urlopen"
        ) as open_url:
            open_url.side_effect = AssertionError("batch glossary-only must not call the network")
            added = self.client.post("/api/batch/add", json={"files": [str(src)]})
            self.assertEqual(added.status_code, 200, added.text)
            started = self.client.post(
                "/api/batch/start",
                json={
                    "provider": "deepl",
                    "deepl_key": "",
                    "output_dir": self.tmp.name,
                    "translation_mode": "zh_to_en",
                    "output_format": "source",
                },
            )
            self.assertEqual(started.status_code, 200, started.text)
            self.assertNotIn("Traceback", started.text)
            task = None
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                task = self.client.get("/api/batch").json()["tasks"][0]
                if task["status"] not in {"queued", "retrying", "running"}:
                    break
                time.sleep(0.05)
            open_url.assert_not_called()
        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "succeeded", task)
        out = Path(task["output_file"])
        self.assertTrue(out.is_file(), task)
        self.assertTrue(out.name.startswith("en_"))
        preview = extract_preview(str(out), include_attribs=True, include_paper=True)
        sources = {item["source"] for item in preview["items"]}
        self.assertIn("reflected ceiling plan", sources)
        self.assertIn("floor plan", sources)
        self.assertNotIn("天花图", sources)
        self.assertNotIn("平面布置图", sources)
        mtext = next(item for item in preview["items"] if item["type"] == "MTEXT")
        self.assertIn("partition", mtext["source"].lower())
        self.assertIn("\\C1;", mtext["raw"])

    def test_batch_dims_tables_writes_dimension_and_table(self):
        fixture = FIXTURES / "dims_tables.dxf"
        self.assertTrue(fixture.is_file(), "tests/fixtures/dims_tables.dxf")
        src = Path(self.tmp.name) / "dims_tables.dxf"
        shutil.copy(fixture, src)
        config = dict(EMPTY_ENGINE, output_dir=self.tmp.name)
        with patch.object(service, "load_config", return_value=config), patch.object(service, "save_config"), patch(
            "urllib.request.urlopen"
        ) as open_url:
            open_url.side_effect = AssertionError("batch dims/tables must not call the network")
            added = self.client.post("/api/batch/add", json={"files": [str(src)]})
            self.assertEqual(added.status_code, 200, added.text)
            started = self.client.post(
                "/api/batch/start",
                json={
                    "provider": "deepl",
                    "deepl_key": "",
                    "output_dir": self.tmp.name,
                    "translation_mode": "zh_to_en",
                    "output_format": "source",
                },
            )
            self.assertEqual(started.status_code, 200, started.text)
            task = None
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                task = self.client.get("/api/batch").json()["tasks"][0]
                if task["status"] not in {"queued", "retrying", "running"}:
                    break
                time.sleep(0.05)
            open_url.assert_not_called()
        self.assertEqual(task["status"], "succeeded", task)
        out = Path(task["output_file"])
        self.assertTrue(out.is_file(), task)
        reread = extract_preview(str(out), enable_v02=True)
        by_type = {}
        for item in reread["items"]:
            by_type.setdefault(item["type"], []).append(item["source"])
        self.assertIn("installation height", by_type.get("DIMENSION", []))
        self.assertNotIn("安装高度", by_type.get("DIMENSION", []))
        table = set(by_type.get("ACAD_TABLE", []))
        self.assertIn("wall demolition plan", table)
        self.assertIn("bill of materials", table)
        self.assertNotIn("墙体拆除图", table)
        self.assertNotIn("材料表", table)
        self.assertIn("reflected ceiling plan", set(by_type.get("TEXT", [])))

    def test_batch_bilingual_style_writes_two_lines(self):
        fixture = FIXTURES / "floor_plan.dxf"
        src = Path(self.tmp.name) / "floor_plan.dxf"
        shutil.copy(fixture, src)
        config = dict(EMPTY_ENGINE, output_dir=self.tmp.name)
        with patch.object(service, "load_config", return_value=config), patch.object(service, "save_config"), patch(
            "urllib.request.urlopen"
        ) as open_url:
            open_url.side_effect = AssertionError("batch 对照 must not call the network")
            added = self.client.post("/api/batch/add", json={"files": [str(src)]})
            self.assertEqual(added.status_code, 200, added.text)
            started = self.client.post(
                "/api/batch/start",
                json={
                    "provider": "deepl",
                    "deepl_key": "",
                    "output_dir": self.tmp.name,
                    "translation_mode": "zh_to_en",
                    "output_format": "source",
                    "style": "原译对照",
                },
            )
            self.assertEqual(started.status_code, 200, started.text)
            task = None
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                task = self.client.get("/api/batch").json()["tasks"][0]
                if task["status"] not in {"queued", "retrying", "running"}:
                    break
                time.sleep(0.05)
            open_url.assert_not_called()
        self.assertEqual(task["status"], "succeeded", task)
        out = Path(task["output_file"])
        self.assertTrue(out.is_file(), task)
        sources = {item["source"] for item in extract_preview(str(out), include_attribs=True, include_paper=True)["items"]}
        self.assertIn("天花图", sources)
        self.assertIn("reflected ceiling plan", sources)
        self.assertIn("平面布置图", sources)
        self.assertIn("floor plan", sources)
        mtext = next(entity for entity in ezdxf.readfile(out).modelspace() if entity.dxftype() == "MTEXT")
        self.assertIn("\\C1;", mtext.dxf.text)
        self.assertIn("\\P", mtext.dxf.text)

    def test_unreadable_dxf_fails_calmly_without_retry(self):
        src = Path(self.tmp.name) / "junk.dxf"
        src.write_text("not a dxf at all", encoding="utf-8")
        config = dict(EMPTY_ENGINE, output_dir=self.tmp.name)
        with patch.object(service, "load_config", return_value=config), patch.object(service, "save_config"):
            added = self.client.post("/api/batch/add", json={"files": [str(src)]})
            self.assertEqual(added.status_code, 200, added.text)
            started = self.client.post(
                "/api/batch/start",
                json={
                    "provider": "deepl",
                    "deepl_key": "",
                    "output_dir": self.tmp.name,
                    "translation_mode": "zh_to_en",
                    "output_format": "source",
                },
            )
            self.assertEqual(started.status_code, 200, started.text)
            task = None
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                task = self.client.get("/api/batch").json()["tasks"][0]
                if task["status"] not in {"queued", "retrying", "running"}:
                    break
                time.sleep(0.05)
        self.assertEqual(task["status"], "failed", task)
        self.assertIn("无法读取", task["message"])
        self.assertNotIn("Traceback", task["message"])
        self.assertEqual(task.get("retries") or 0, 0)

    def test_start_skips_stale_missing_paths(self):
        live = Path(self.tmp.name) / "live.dxf"
        shutil.copy(FIXTURES / "floor_plan.dxf", live)
        gone = Path(self.tmp.name) / "gone.dxf"
        gone.write_bytes(b"0\nEOF\n")
        config = dict(EMPTY_ENGINE, output_dir=self.tmp.name)
        with patch.object(service, "load_config", return_value=config), patch.object(service, "save_config"):
            added = self.client.post("/api/batch/add", json={"files": [str(gone), str(live)]})
            self.assertEqual(added.status_code, 200, added.text)
            gone.unlink()
            started = self.client.post(
                "/api/batch/start",
                json={
                    "provider": "deepl",
                    "deepl_key": "",
                    "output_dir": self.tmp.name,
                    "translation_mode": "zh_to_en",
                    "output_format": "source",
                },
            )
            self.assertEqual(started.status_code, 200, started.text)
            deadline = time.monotonic() + 20
            tasks = []
            while time.monotonic() < deadline:
                tasks = self.client.get("/api/batch").json()["tasks"]
                if tasks and all(task["status"] not in {"queued", "retrying", "running"} for task in tasks):
                    break
                time.sleep(0.05)
        by_path = {task["input_file"]: task for task in tasks}
        self.assertEqual(by_path[str(gone)]["status"], "failed")
        self.assertIn("图纸不存在", by_path[str(gone)]["message"])
        self.assertEqual(by_path[str(live)]["status"], "succeeded", by_path[str(live)])
        sources = {
            item["source"]
            for item in extract_preview(by_path[str(live)]["output_file"], include_attribs=True, include_paper=True)["items"]
        }
        self.assertIn("reflected ceiling plan", sources)

    def test_start_skips_dwg_without_oda_and_runs_dxf(self):
        from backend.cad import odafc_available

        if odafc_available():
            self.skipTest("ODA is installed")
        drawings = Path("/workspace/dwglot-drawings")
        dwgs = sorted(drawings.glob("*.dwg"))
        if not dwgs:
            self.skipTest("no DWG fixtures in /workspace/dwglot-drawings")
        live = Path(self.tmp.name) / "live.dxf"
        shutil.copy(FIXTURES / "floor_plan.dxf", live)
        dwg = Path(self.tmp.name) / dwgs[0].name
        shutil.copy(dwgs[0], dwg)
        config = dict(EMPTY_ENGINE, output_dir=self.tmp.name)
        with patch.object(service, "load_config", return_value=config), patch.object(service, "save_config"):
            added = self.client.post("/api/batch/add", json={"files": [str(dwg), str(live)]})
            self.assertEqual(added.status_code, 200, added.text)
            started = self.client.post(
                "/api/batch/start",
                json={
                    "provider": "deepl",
                    "deepl_key": "",
                    "output_dir": self.tmp.name,
                    "translation_mode": "zh_to_en",
                    "output_format": "source",
                },
            )
            self.assertEqual(started.status_code, 200, started.text)
            deadline = time.monotonic() + 20
            tasks = []
            while time.monotonic() < deadline:
                tasks = self.client.get("/api/batch").json()["tasks"]
                if tasks and all(task["status"] not in {"queued", "retrying", "running"} for task in tasks):
                    break
                time.sleep(0.05)
        by_path = {task["input_file"]: task for task in tasks}
        self.assertEqual(by_path[str(dwg)]["status"], "failed", by_path[str(dwg)])
        self.assertIn("ODA", by_path[str(dwg)]["message"])
        self.assertIn("未检测到 ODA", by_path[str(dwg)]["message"])
        self.assertEqual(by_path[str(live)]["status"], "succeeded", by_path[str(live)])
        sources = {
            item["source"]
            for item in extract_preview(by_path[str(live)]["output_file"], include_attribs=True, include_paper=True)["items"]
        }
        self.assertIn("reflected ceiling plan", sources)

    def test_missing_file_and_oda_are_not_retried(self):
        self.assertFalse(_retryable(FileNotFoundError("图纸不存在")))
        self.assertFalse(_retryable(ValueError("无法读取DXF文件")))
        self.assertFalse(_retryable(RuntimeError("未检测到 ODA，无法处理 DWG；请安装 ODA 或将 DWG 另存为 DXF")))
        fatal = RuntimeError("请配置 DeepL API Key")
        fatal.retryable = False
        self.assertFalse(_retryable(fatal))
        self.assertTrue(_retryable(OSError("temporary network error")))
        self.assertNotIn("Traceback", _calm_error(RuntimeError("boom\nTraceback (most recent call last):\n x")))


if __name__ == "__main__":
    unittest.main()
