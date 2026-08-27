"""Minimal Azure Translator Text v3 client for the F0 plan."""

import json
import urllib.error
import urllib.parse
import urllib.request


from backend.languages import azure_code as map_azure_code
from backend.providers.base import QuotaExceededError, TranslationProvider, TranslationProviderError

AZURE_ENDPOINT = "https://api.cognitive.microsofttranslator.com/translate"
AZURE_LANGUAGE_CODES = {"zh-cn": "zh-Hans", "en": "en", "en-us": "en", "fr": "fr"}


class AzureTranslatorError(TranslationProviderError):
    retryable = True


class AzureFreeQuotaExceededError(QuotaExceededError, AzureTranslatorError):
    retryable = False


class AzureTranslator(TranslationProvider):
    name = "azure"
    needs_key = True

    def __init__(self, key: str, region: str = ""):
        self.key = key.strip()
        self.region = region.strip()
        if not self.key:
            error = AzureTranslatorError("请配置 Azure Translator Key")
            error.retryable = False
            raise error

    def translate_text(self, text: str, source_lang: str, target_lang: str) -> str:
        source = AZURE_LANGUAGE_CODES.get(source_lang.lower()) or map_azure_code(source_lang)
        target = AZURE_LANGUAGE_CODES.get(target_lang.lower()) or map_azure_code(target_lang)
        query = urllib.parse.urlencode({"api-version": "3.0", "from": source, "to": target, "textType": "plain"})
        headers = {"Ocp-Apim-Subscription-Key": self.key, "Content-Type": "application/json; charset=UTF-8"}
        if self.region:
            headers["Ocp-Apim-Subscription-Region"] = self.region
        request = urllib.request.Request(
            f"{AZURE_ENDPOINT}?{query}",
            data=json.dumps([{"Text": text}], ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))[0]["translations"][0]["text"]
        except urllib.error.HTTPError as exc:
            try:
                error = json.loads(exc.read().decode("utf-8")).get("error", {})
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                error = {}
            code, message = error.get("code", exc.code), error.get("message", str(exc))
            if str(code) == "403001":
                raise AzureFreeQuotaExceededError("Azure Translator F0 免费额度已用尽，请等待下月额度重置或升级 Azure 资源。") from exc
            error = AzureTranslatorError(f"Azure Translator 请求失败 ({code}): {message}")
            error.retryable = exc.code not in {400, 401, 403}
            raise error from exc
        except (OSError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AzureTranslatorError(f"Azure Translator 请求失败: {exc}") from exc
