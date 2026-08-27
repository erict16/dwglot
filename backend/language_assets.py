"""Local terminology, translation-memory, and provider-usage storage."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from backend.app_meta import ASSETS_PATH, LEGACY_ASSETS_PATH, migrate_legacy_file
from backend.storage import atomic_write_json

migrate_legacy_file(LEGACY_ASSETS_PATH, ASSETS_PATH)
DATABASE_PATH = ASSETS_PATH
AZURE_F0_MONTHLY_CHARACTER_LIMIT = 2_000_000


def _as_text(value) -> str:
    if value is None:
        return ""
    return str(value)


def _normalise(text: str) -> str:
    return " ".join(_as_text(text).strip().casefold().split())


class LanguageAssets:
    """Small SQLite-backed language assets; project terms remain portable JSON."""

    def __init__(self, database_path: str | Path | None = None):
        self.database_path = Path(database_path or DATABASE_PATH)
        self._lock = threading.Lock()
        self._initialise()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialise(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS terms (
                    id INTEGER PRIMARY KEY,
                    mode TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_norm TEXT NOT NULL,
                    target TEXT NOT NULL,
                    layer_contains TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    UNIQUE(mode, source_norm, layer_contains)
                );
                CREATE TABLE IF NOT EXISTS translation_memory (
                    id INTEGER PRIMARY KEY,
                    mode TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_norm TEXT NOT NULL,
                    layer_key TEXT NOT NULL DEFAULT '',
                    target TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(mode, source_norm, layer_key)
                );
                CREATE TABLE IF NOT EXISTS usage_monthly (
                    month TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    characters INTEGER NOT NULL DEFAULT 0,
                    requests INTEGER NOT NULL DEFAULT 0,
                    quota_exceeded INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(month, provider)
                );
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _project_terms(path: str) -> list[dict]:
        if not path:
            return []
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return data.get("terms", []) if isinstance(data, dict) else []
        except (OSError, ValueError, TypeError):
            return []

    def project_info(self, path: str, *, require_terms: bool = False) -> dict:
        if not path:
            return {"path": "", "name": "", "terms": []}
        target = Path(path)
        if not target.is_file():
            raise ValueError("术语表不存在或无法读取")
        try:
            raw = target.read_bytes()
            if not raw.strip():
                raise ValueError("术语表是空的")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("术语表编码无法读取，请另存为 UTF-8") from exc
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("术语表不存在或无法读取")
            terms = data.get("terms") or []
            if require_terms and not terms:
                raise ValueError("术语表是空的")
            return {"path": str(target), "name": str(data.get("name") or target.stem), "terms": terms}
        except json.JSONDecodeError as exc:
            raise ValueError("术语表不存在或无法读取") from exc
        except ValueError:
            raise
        except (OSError, TypeError) as exc:
            raise ValueError("术语表不存在或无法读取") from exc

    def create_project(self, path: str, name: str = "") -> dict:
        target = Path(path)
        if target.suffix.lower() != ".json":
            target = target.with_suffix(".hcterms.json")
        if not target.exists():
            atomic_write_json(target, {"format": "honsen-cad-terms/v1", "name": name or target.stem, "terms": []})
        return self.project_info(str(target))

    def list_terms(self, project_path: str = "") -> list[dict]:
        with self._connect() as connection:
            global_terms = [dict(row, scope="global") for row in connection.execute("SELECT id, mode, source, target, layer_contains, updated_at FROM terms ORDER BY mode, source COLLATE NOCASE")]
        project_terms = []
        for index, term in enumerate(self._project_terms(project_path)):
            if term.get("mode") and term.get("source") and term.get("target"):
                project_terms.append({"id": index, "scope": "project", "mode": term["mode"], "source": term["source"], "target": term["target"], "layer_contains": term.get("layer_contains", ""), "updated_at": term.get("updated_at", "")})
        return project_terms + global_terms

    def _write_project_terms(self, path: str, terms: list[dict]) -> None:
        target = Path(path)
        data = self.project_info(str(target)) if target.exists() else {"name": target.stem}
        data["format"] = "honsen-cad-terms/v1"
        data["terms"] = terms
        atomic_write_json(target, data)

    def upsert_term(self, scope: str, mode: str, source: str, target: str, layer_contains: str = "", project_path: str = "", term_id: int | None = None) -> None:
        if scope not in {"global", "project"} or not mode or not source.strip() or not target.strip():
            raise ValueError("术语、译文和翻译方向不能为空")
        entry = {"mode": mode, "source": source.strip(), "target": target.strip(), "layer_contains": layer_contains.strip(), "updated_at": self._now()}
        if scope == "project":
            if not project_path:
                raise ValueError("请先选择或新建项目术语包")
            terms = self._project_terms(project_path)
            if term_id is not None and 0 <= term_id < len(terms):
                terms[term_id] = entry
            else:
                terms = [term for term in terms if not (term.get("mode") == mode and _normalise(term.get("source", "")) == _normalise(source) and term.get("layer_contains", "") == layer_contains.strip())]
                terms.append(entry)
            self._write_project_terms(project_path, terms)
            return
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO terms(mode, source, source_norm, target, layer_contains, updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(mode, source_norm, layer_contains) DO UPDATE SET source=excluded.source, target=excluded.target, updated_at=excluded.updated_at",
                (mode, entry["source"], _normalise(source), entry["target"], entry["layer_contains"].casefold(), entry["updated_at"]),
            )

    def delete_term(self, scope: str, term_id: int, project_path: str = "") -> None:
        if scope == "project":
            terms = self._project_terms(project_path)
            if 0 <= term_id < len(terms):
                terms.pop(term_id)
                self._write_project_terms(project_path, terms)
            return
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM terms WHERE id=?", (term_id,))

    def lookup_term(self, source: str, mode: str, layer: str = "", project_path: str = "") -> str | None:
        source_norm, layer_norm = _normalise(source), _as_text(layer).casefold()
        candidates = []
        for term in self._project_terms(project_path):
            if term.get("mode") == mode and _normalise(term.get("source", "")) == source_norm:
                candidates.append(term)
        with self._connect() as connection:
            candidates.extend(dict(row) for row in connection.execute("SELECT target, layer_contains FROM terms WHERE mode=? AND source_norm=?", (mode, source_norm)))
        candidates.sort(key=lambda term: bool(term.get("layer_contains")), reverse=True)
        for term in candidates:
            layer_rule = (term.get("layer_contains") or "").casefold()
            if not layer_rule or layer_rule in layer_norm:
                return str(term["target"])
        return None

    def lookup_memory(self, source: str, mode: str, layer: str = "") -> str | None:
        source_norm, layer_key = _normalise(source), _as_text(layer).casefold()
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT id, target FROM translation_memory WHERE mode=? AND source_norm=? AND layer_key IN (?, '') ORDER BY CASE WHEN layer_key='' THEN 1 ELSE 0 END LIMIT 1", (mode, source_norm, layer_key)).fetchone()
            if not row:
                return None
            connection.execute("UPDATE translation_memory SET hit_count=hit_count+1, updated_at=? WHERE id=?", (self._now(), row["id"]))
            return str(row["target"])

    def record_memory(self, source: str, target: str, mode: str, layer: str, provider: str, origin: str = "provider") -> None:
        if not source.strip() or not target.strip():
            return
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO translation_memory(mode, source, source_norm, layer_key, target, provider, origin, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(mode, source_norm, layer_key) DO UPDATE SET target=excluded.target, provider=excluded.provider, origin=excluded.origin, updated_at=excluded.updated_at WHERE translation_memory.origin != 'manual'",
                (mode, source.strip(), _normalise(source), _as_text(layer).casefold(), target.strip(), provider, origin, now, now),
            )

    def list_memory(self) -> list[dict]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute("SELECT id, mode, source, target, layer_key, provider, origin, hit_count, updated_at FROM translation_memory ORDER BY updated_at DESC LIMIT 500")]

    def upsert_memory(self, mode: str, source: str, target: str, layer: str = "", term_id: int | None = None) -> None:
        if not mode or not source.strip() or not target.strip():
            raise ValueError("记忆原文、译文和翻译方向不能为空")
        if term_id is not None:
            with self._lock, self._connect() as connection:
                connection.execute("DELETE FROM translation_memory WHERE id=?", (term_id,))
        self.record_memory(source, target, mode, layer, "manual", "manual")

    def delete_memory(self, term_id: int) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM translation_memory WHERE id=?", (term_id,))

    def record_usage(self, provider: str, characters: int, quota_exceeded: bool = False) -> None:
        if provider not in {"deepl", "azure"}:
            return
        month = datetime.now().strftime("%Y-%m")
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO usage_monthly(month, provider, characters, requests, quota_exceeded) VALUES(?,?,?,?,?) "
                "ON CONFLICT(month, provider) DO UPDATE SET characters=characters+excluded.characters, requests=requests+excluded.requests, quota_exceeded=MAX(quota_exceeded, excluded.quota_exceeded)",
                (month, provider, max(0, characters), 1, int(quota_exceeded)),
            )

    def usage(self) -> dict:
        month = datetime.now().strftime("%Y-%m")
        with self._connect() as connection:
            rows = {row["provider"]: dict(row) for row in connection.execute("SELECT provider, characters, requests, quota_exceeded FROM usage_monthly WHERE month=?", (month,))}
        azure = rows.get("azure", {"characters": 0, "requests": 0, "quota_exceeded": 0})
        deepl = rows.get("deepl", {"characters": 0, "requests": 0, "quota_exceeded": 0})
        return {"month": month, "deepl": deepl, "azure": {**azure, "limit": AZURE_F0_MONTHLY_CHARACTER_LIMIT, "remaining": max(0, AZURE_F0_MONTHLY_CHARACTER_LIMIT - azure["characters"])} }
