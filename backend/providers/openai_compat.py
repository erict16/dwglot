"""OpenAI-compatible chat completions (DeepSeek, 通义-compatible gateways, etc.)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from backend.languages import language_name
from backend.providers.base import TranslationProvider, TranslationProviderError

DEFAULT_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


class OpenAICompatProvider(TranslationProvider):
    name = "openai"
    needs_key = True

    def __init__(self, api_key: str, base_url: str = "", model: str = ""):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_BASE).rstrip("/")
        self.model = (model or DEFAULT_MODEL).strip()
        if not self.api_key:
            raise TranslationProviderError("请配置 OpenAI 兼容接口的 API Key")

    def translate_text(self, text: str, source_lang: str, target_lang: str) -> str:
        src = language_name(source_lang)
        tgt = language_name(target_lang)
        payload = {
            "model": self.model,
            "temperature": 0,
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
        url = f"{self.base_url}/chat/completions"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            error = TranslationProviderError(f"OpenAI 兼容接口失败 ({exc.code}): {detail[:300]}")
            error.retryable = exc.code not in {400, 401, 403}
            raise error from exc
        except (OSError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise TranslationProviderError(f"OpenAI 兼容接口失败: {exc}") from exc
