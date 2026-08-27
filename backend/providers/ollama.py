"""Local Ollama. No cloud key. Default host 127.0.0.1:11434."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from backend.languages import language_name
from backend.providers.base import TranslationProvider, TranslationProviderError

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "llama3.1"
PROBE_TIMEOUT = 2.0
TRANSLATE_TIMEOUT = 15.0


def ollama_reachable(host: str = "", timeout: float = PROBE_TIMEOUT) -> bool:
    target = (host or DEFAULT_HOST).rstrip("/")
    try:
        urllib.request.urlopen(f"{target}/api/tags", timeout=timeout)
        return True
    except Exception:
        return False


class OllamaProvider(TranslationProvider):
    name = "ollama"
    needs_key = False

    def __init__(self, host: str = "", model: str = ""):
        self.host = (host or DEFAULT_HOST).rstrip("/")
        self.model = (model or DEFAULT_MODEL).strip()

    def translate_text(self, text: str, source_lang: str, target_lang: str) -> str:
        src = language_name(source_lang)
        tgt = language_name(target_lang)
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"You are a CAD drawing translator. Translate from {src} to {tgt}. "
                        "Return only the translation, no quotes, no notes. "
                        "Keep numbers, drawing IDs, and units unchanged."
                    ),
                },
                {"role": "user", "content": text},
            ],
        }
        request = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=TRANSLATE_TIMEOUT) as response:
                body = json.loads(response.read().decode("utf-8"))
            message = body.get("message") or {}
            content = message.get("content") or body.get("response") or ""
            if not str(content).strip():
                raise TranslationProviderError("Ollama 返回空译文")
            return str(content).strip()
        except urllib.error.URLError as exc:
            raise TranslationProviderError(
                f"无法连接 Ollama ({self.host})。请先启动 ollama serve，并确认已拉取模型 {self.model}。"
            ) from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            error = TranslationProviderError("Ollama 请求失败")
            error.retryable = exc.code >= 500
            raise error from exc
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise TranslationProviderError("Ollama 请求失败") from exc
