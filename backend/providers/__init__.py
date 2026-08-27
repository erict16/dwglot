"""Translation provider integrations."""

from backend.providers.azure import AzureFreeQuotaExceededError, AzureTranslator, AzureTranslatorError
from backend.providers.base import QuotaExceededError, TranslationProvider, TranslationProviderError
from backend.providers.deepl_provider import DeepLProvider
from backend.providers.ollama import OllamaProvider
from backend.providers.openai_compat import OpenAICompatProvider

PROVIDERS = {
    "azure": AzureTranslator,
    "deepl": DeepLProvider,
    "openai": OpenAICompatProvider,
    "ollama": OllamaProvider,
}

__all__ = [
    "AzureFreeQuotaExceededError",
    "AzureTranslator",
    "AzureTranslatorError",
    "DeepLProvider",
    "OllamaProvider",
    "OpenAICompatProvider",
    "PROVIDERS",
    "QuotaExceededError",
    "TranslationProvider",
    "TranslationProviderError",
]
