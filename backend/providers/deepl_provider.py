"""DeepL Free / Pro. Free keys end with :fx (500k chars/mo)."""

from __future__ import annotations

import deepl

from backend.languages import deepl_code
from backend.providers.base import TranslationProvider, TranslationProviderError


class DeepLProvider(TranslationProvider):
    name = "deepl"
    needs_key = True

    def __init__(self, api_key: str):
        self.api_key = (api_key or "").strip()
        if not self.api_key:
            error = TranslationProviderError("请配置 DeepL API Key")
            error.retryable = False
            raise error
        try:
            self.client = deepl.Translator(self.api_key)
        except Exception as exc:
            raise TranslationProviderError("DeepL 初始化失败") from exc

    def translate_text(self, text: str, source_lang: str, target_lang: str) -> str:
        try:
            result = self.client.translate_text(
                text,
                source_lang=deepl_code(source_lang).split("-")[0],
                target_lang=deepl_code(target_lang),
            )
            return result.text
        except Exception as exc:
            error = TranslationProviderError("DeepL 翻译失败")
            message = str(exc).lower()
            error.retryable = "auth" not in message and "403" not in message and "401" not in message
            raise error from exc
