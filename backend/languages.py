"""Language pairs for multilingual CAD translation."""

from __future__ import annotations

# UI list. Azure F0 covers these; DeepL covers a subset and errors clearly if not.
LANGUAGES: list[tuple[str, str]] = [
    ("zh-Hans", "中文（简体）"),
    ("zh-Hant", "中文（繁体）"),
    ("en", "English"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("pt", "Português"),
    ("ru", "Русский"),
    ("vi", "Tiếng Việt"),
    ("th", "ไทย"),
    ("ar", "العربية"),
    ("it", "Italiano"),
    ("pl", "Polski"),
    ("nl", "Nederlands"),
    ("tr", "Türkçe"),
    ("id", "Bahasa Indonesia"),
    ("ms", "Bahasa Melayu"),
    ("uk", "Українська"),
    ("cs", "Čeština"),
    ("ro", "Română"),
    ("hu", "Magyar"),
    ("el", "Ελληνικά"),
    ("sv", "Svenska"),
    ("da", "Dansk"),
    ("fi", "Suomi"),
    ("nb", "Norsk"),
    ("sk", "Slovenčina"),
    ("bg", "Български"),
    ("hi", "हिन्दी"),
    ("he", "עברית"),
    ("km", "ខ្មែរ"),
    ("lo", "ລາວ"),
    ("my", "မြန်မာ"),
    ("ne", "नेपाली"),
]

LANGUAGE_LABELS = {code: label for code, label in LANGUAGES}
LANGUAGE_CODES = {code.lower(): code for code, _ in LANGUAGES}

# Old Honsen mode keys stay valid so YAML glossaries and tests keep working.
MODE_ALIASES = {
    "zh_to_en": ("zh-Hans", "en"),
    "en_to_zh": ("en", "zh-Hans"),
    "zh_to_fr": ("zh-Hans", "fr"),
    "fr_to_zh": ("fr", "zh-Hans"),
    "zh-cn_to_en": ("zh-Hans", "en"),
    "zh-cn_to_en-us": ("zh-Hans", "en"),
    "zh-hans_to_en": ("zh-Hans", "en"),
    "en_to_zh-hans": ("en", "zh-Hans"),
    "en_to_zh-cn": ("en", "zh-Hans"),
}

AZURE_ALIASES = {
    "zh-cn": "zh-Hans",
    "zh": "zh-Hans",
    "zh-hans": "zh-Hans",
    "zh-tw": "zh-Hant",
    "zh-hant": "zh-Hant",
    "en-us": "en",
    "en-gb": "en",
    "pt-br": "pt",
    "pt-pt": "pt",
}

DEEPL_ALIASES = {
    "zh-hans": "ZH",
    "zh-cn": "ZH",
    "zh": "ZH",
    "zh-hant": "ZH",
    "en": "EN",
    "en-us": "EN-US",
    "en-gb": "EN-GB",
    "fr": "FR",
    "de": "DE",
    "ja": "JA",
    "ko": "KO",
    "ru": "RU",
    "es": "ES",
    "pt": "PT-PT",
    "pt-br": "PT-BR",
    "it": "IT",
    "nl": "NL",
    "pl": "PL",
    "tr": "TR",
    "uk": "UK",
    "id": "ID",
    "sv": "SV",
    "cs": "CS",
    "ro": "RO",
    "hu": "HU",
    "el": "EL",
    "da": "DA",
    "fi": "FI",
    "nb": "NB",
    "sk": "SK",
    "bg": "BG",
    "lt": "LT",
    "lv": "LV",
    "et": "ET",
    "sl": "SL",
    "ar": "AR",
    "vi": "VI",
}

YAML_GLOSSARY_MODES = {
    ("zh-Hans", "fr"): "zh_to_fr",
    ("fr", "zh-Hans"): "fr_to_zh",
    ("zh-Hans", "en"): "zh_to_en",
    ("en", "zh-Hans"): "en_to_zh",
}


def normalize_lang(code: str) -> str:
    raw = (code or "").strip()
    if not raw:
        return raw
    lower = raw.lower()
    if lower in LANGUAGE_CODES:
        return LANGUAGE_CODES[lower]
    mapped = AZURE_ALIASES.get(lower)
    if mapped:
        return mapped
    return raw


def split_mode(mode: str) -> tuple[str, str]:
    if mode in MODE_ALIASES:
        return MODE_ALIASES[mode]
    if "_to_" in (mode or ""):
        source, target = mode.split("_to_", 1)
        return normalize_lang(source), normalize_lang(target)
    raise ValueError(f"不支持的翻译方向: {mode}")


def mode_key(source: str, target: str) -> str:
    src, tgt = normalize_lang(source), normalize_lang(target)
    yaml_mode = YAML_GLOSSARY_MODES.get((src, tgt))
    if yaml_mode:
        return yaml_mode
    return f"{src}_to_{tgt}"


def output_prefix(mode: str) -> str:
    source, target = split_mode(mode)
    if target.lower().startswith("zh"):
        return "zh"
    return target.split("-")[0][:8] or "out"


def azure_code(lang: str) -> str:
    lower = (lang or "").lower()
    return AZURE_ALIASES.get(lower, lang)


def deepl_code(lang: str) -> str:
    lower = (lang or "").lower()
    if lower not in DEEPL_ALIASES:
        raise ValueError(f"DeepL 不支持语种 {lang}，请改用 Azure、OpenAI 兼容接口或 Ollama")
    return DEEPL_ALIASES[lower]


def language_name(code: str) -> str:
    norm = normalize_lang(code)
    return LANGUAGE_LABELS.get(norm, code)
