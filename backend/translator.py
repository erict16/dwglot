"""CAD text translation core."""
import ezdxf
import re
import time
import os
import sys
import json
import threading
import queue
import urllib.request
from datetime import datetime
from pathlib import Path

import deepl
import yaml

try:  # The removed legacy GUI is retained only for config compatibility tests.
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except ImportError:  # Homebrew Python on macOS does not include Tk by default.
    tk = ttk = filedialog = messagebox = None

from backend.language_assets import LanguageAssets
from backend.storage import atomic_output_path, atomic_write_json
from backend.text_cleaning import TextCleaner

try:
    import winreg
except ImportError:
    winreg = None

from backend.app_meta import APP_VERSION, CONFIG_PATH as DWGLOT_CONFIG_PATH
from backend.languages import mode_key, split_mode, language_name
from backend.mtext_runs import map_translatable
from backend.providers.azure import AzureFreeQuotaExceededError, AzureTranslator
from backend.providers.deepl_provider import DeepLProvider
from backend.providers.ollama import OllamaProvider
from backend.providers.openai_compat import OpenAICompatProvider
from backend.styles import rewrite_shx_styles



# ODA can export legacy SHX/GBK text as ``\M+5C6BD`` rather than Unicode.
# The leading nibble identifies the legacy codepage; the following four hex
# digits are the two-byte GBK value (C6BD = "平", C3E6 = "面").
ODA_MBCS_ESCAPE_RE = re.compile(r"\\M\+[0-9A-Fa-f]([0-9A-Fa-f]{4})")


def decode_oda_mbcs_escapes(value: str) -> str:
    """Decode ODA's legacy M+ codepage escapes without touching other CAD codes."""
    def decode_match(match):
        try:
            return bytes.fromhex(match.group(1)).decode("gbk")
        except UnicodeDecodeError:
            return match.group(0)
    return ODA_MBCS_ESCAPE_RE.sub(decode_match, str(value or ""))

def resource_path(relative_path):
    """
    获取资源文件路径，兼容开发环境和 PyInstaller 打包后的路径。
    """
    try:
        base_path = sys._MEIPASS  # PyInstaller 临时目录
    except AttributeError:
        base_path = Path(__file__).resolve().parents[1]
    return os.path.join(base_path, relative_path)

def load_yaml_data(filename):
    full_path = resource_path(filename)
    if os.path.exists(full_path):
        with open(full_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}

def get_installed_fonts():
    fonts = set()
    if winreg is None:
        return fonts
    try:
        reg_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path) as key:
            for i in range(0, winreg.QueryInfoKey(key)[1]):
                name, _, _ = winreg.EnumValue(key, i)
                fonts.add(name.split(" (")[0].strip())
    except Exception as e:
        print(f"获取字体失败: {e}")
    return fonts
preferred_fonts = [
    "SimSun",        # 宋体，Win默认有
    "Microsoft YaHei",  # 微软雅黑，清晰
    "SimHei",        # 黑体
    "Arial Unicode MS", # 英文+中文兼容
    "Arial",
    "Tahoma",
]

def pick_available_font():
    installed_fonts = get_installed_fonts()
    for font in preferred_fonts:
        if font in installed_fonts:
            return font
    return "Arial"  # 默认 fallback


CONFIG_PATH = str(DWGLOT_CONFIG_PATH)
OUTPUT_PREFIXES = {
    "zh_to_fr": "fr",
    "fr_to_zh": "zh",
    "zh_to_en": "en",
    "en_to_zh": "zh",
}


def output_prefix(mode):
    if mode in OUTPUT_PREFIXES:
        return OUTPUT_PREFIXES[mode]
    from backend.languages import output_prefix as lang_prefix

    try:
        return lang_prefix(mode)
    except ValueError:
        return "out"


def wait_for_translation(resume_event=None, cancel_event=None):
    while resume_event and not resume_event.wait(0.1):
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("translation cancelled")
    if cancel_event and cancel_event.is_set():
        raise InterruptedError("translation cancelled")


def _is_layout_space_block(name: str) -> bool:
    """True for *Model_Space / *Paper_Space* — already extracted as layouts."""
    upper = (name or "").upper()
    return upper == "*MODEL_SPACE" or upper.startswith("*PAPER_SPACE")


class CADChineseTranslator:

    @staticmethod
    def contains_surrogates(text):
        """检测是否包含 Unicode surrogate（代理）字符"""
        return any(0xD800 <= ord(c) <= 0xDFFF for c in text)
    def fully_clean_for_write(self, text):
        try:
            cleaned = self.cleaner.full_clean(text)
            return cleaned.encode("utf-8", "ignore").decode("utf-8")
        except Exception as e:
            return f"[完全清洗失败: {e}]"

    def __init__(self, log_callback=None):
        self.translated_cache = {}
        self.language_assets = LanguageAssets()
        self.project_package_path = ""
        self.default_font = pick_available_font()
        self.log_callback = log_callback
        self.deepl_api_key = os.environ.get("DEEPL_API_KEY")
        self.deepl_translator = None
        self.translation_provider = "deepl"
        self.azure_translator = None
        self.mt_provider = None
        self.enable_v02_entities = False
        self.cleaner = TextCleaner()
        abbrev_data = load_yaml_data("glossaries/translation_abbreviations.yaml")
        self.abbrev_map_fr_to_zh = abbrev_data.get("abbrev_map", {})

        context_zh_to_fr = load_yaml_data("glossaries/translation_context.yaml").get("context_zh_to_fr", {})
        context_fr_to_zh = load_yaml_data("glossaries/translation_context_fr_to_zh.yaml").get("context_fr_to_zh", {})
        context_zh_to_en = load_yaml_data("glossaries/translation_context_zh_to_en.yaml").get("context_zh_to_en", {})
        context_en_to_zh = load_yaml_data("glossaries/translation_context_en_to_zh.yaml").get("context_en_to_zh", {})
        corrections_fr_to_zh = load_yaml_data("glossaries/translation_corrections.yaml").get("corrections_fr_to_zh", {})

        self.context_zh_to_fr = context_zh_to_fr
        self.context_fr_to_zh = context_fr_to_zh
        self.context_zh_to_en = context_zh_to_en
        self.context_en_to_zh = context_en_to_zh
        self.corrections_fr_to_zh = corrections_fr_to_zh

        self.language_configs = {
            'zh_to_fr': {
                'source': 'zh-cn',
                'target': 'fr',
                'name': '中文→法语',
                'context': self.context_zh_to_fr
            },
            'fr_to_zh': {
                'source': 'fr',
                'target': 'zh-cn',
                'name': '法语→中文',
                'context': self.context_fr_to_zh
            },
            'zh_to_en': {
                'source': 'zh-cn',
                'target': 'en-us',
                'name': '中文→英语',
                'context': self.context_zh_to_en
            },
            'en_to_zh': {
                'source': 'en',
                'target': 'zh-cn',
                'name': '英语→中文',
                'context': self.context_en_to_zh
            }
        }
        for config in self.language_configs.values():
            config['glossary'] = {
                term.casefold(): translation for term, translation in config['context'].items()
            }
        if self.deepl_api_key:
            try:
                self.deepl_translator = deepl.Translator(self.deepl_api_key)
                self.safe_log(" DeepL 引擎初始化成功")
            except Exception as e:
                self.safe_log(f" DeepL 初始化失败: {e}")
    @property
    def deepl_api_key(self):
        return self._deepl_api_key

    @deepl_api_key.setter
    def deepl_api_key(self, value):
        self._deepl_api_key = value
        if value:
            try:
                self.deepl_translator = deepl.Translator(value)
            except Exception as e:
                self.safe_log(f" DeepL 初始化失败: {e}")
    def safe_log(self, message, level="INFO"):
        if not self.log_callback:
            print(f"[{level}][无日志回调]:", message)
            return
        try:
            cleaned = self.cleaner.clean_for_log(message)
            self.log_callback(cleaned, level=level)
        except Exception as e:
            print("[日志记录失败]", e)
            print("原始日志内容:", repr(message))

    def configure_azure(self, key, region=""):
        self.translation_provider = "azure"
        self.azure_translator = AzureTranslator(key, region) if key else None
        self.mt_provider = self.azure_translator

    def configure_deepl(self, key):
        self.translation_provider = "deepl"
        self.deepl_api_key = (key or "").strip()
        self.mt_provider = DeepLProvider(self.deepl_api_key) if self.deepl_api_key else None

    def configure_openai(self, key, base_url="", model=""):
        self.translation_provider = "openai"
        self.mt_provider = OpenAICompatProvider(key, base_url, model)

    def configure_ollama(self, host="", model=""):
        self.translation_provider = "ollama"
        self.mt_provider = OllamaProvider(host, model)

    def configure_engine(
        self,
        provider="deepl",
        *,
        deepl_key="",
        azure_key="",
        azure_region="",
        openai_key="",
        openai_base="",
        openai_model="",
        ollama_host="",
        ollama_model="",
    ):
        provider = (provider or "deepl").strip() or "deepl"
        self.mt_provider = None
        self.azure_translator = None
        self.deepl_translator = None
        if provider == "azure":
            if azure_key.strip():
                self.configure_azure(azure_key, azure_region)
            else:
                self.translation_provider = "azure"
            return
        if provider == "openai":
            from backend.providers.openai_compat import openai_reachable

            if openai_key.strip() and openai_base.strip() and openai_reachable(openai_base):
                self.configure_openai(openai_key, openai_base, openai_model)
            else:
                self.translation_provider = "openai"
            return
        if provider == "ollama":
            from backend.providers.ollama import ollama_reachable

            self.configure_ollama(ollama_host, ollama_model)
            if not ollama_reachable(ollama_host):
                self.mt_provider = None
            return
        if deepl_key.strip():
            self.configure_deepl(deepl_key)
        else:
            self.translation_provider = "deepl"

    def configure_language_assets(self, project_package_path=""):
        self.project_package_path = project_package_path or ""

    def glossary_hit(self, text, lang_config_key, layer=""):
        cleaned = self.cleaner.full_clean(decode_oda_mbcs_escapes(str(text or "")))
        if not cleaned.strip():
            return None
        if not self.ensure_lang_config(lang_config_key):
            return None
        hit = self.language_assets.lookup_term(cleaned, lang_config_key, layer, self.project_package_path)
        hit = hit or self.get_layer_glossary_translation(cleaned, lang_config_key, layer)
        hit = hit or self.get_glossary_translation(cleaned, lang_config_key)
        return hit

    def has_mt(self) -> bool:
        if self.mt_provider is not None:
            return True
        if self.translation_provider == "azure":
            return self.azure_translator is not None
        return self.deepl_translator is not None

    def ensure_lang_config(self, lang_config_key):
        if lang_config_key in self.language_configs:
            return self.language_configs[lang_config_key]
        try:
            source, target = split_mode(lang_config_key)
        except ValueError:
            return None
        canonical = mode_key(source, target)
        if canonical in self.language_configs:
            self.language_configs[lang_config_key] = self.language_configs[canonical]
            return self.language_configs[canonical]
        config = {
            "source": source,
            "target": target,
            "name": f"{language_name(source)}→{language_name(target)}",
            "context": {},
            "glossary": {},
        }
        self.language_configs[lang_config_key] = config
        self.language_configs[canonical] = config
        return config

    def preprocess_abbreviations(self, text, lang_config_key):
        """在翻译前处理常见缩写，例如 W:800mm → 宽度:800mm，W400*H650 → 宽度400×高度650"""
        if not text or not isinstance(text, str):
            return text

        if lang_config_key == 'fr_to_zh':
            # 缩写映射
            abbrev_map = self.abbrev_map_fr_to_zh

            # 处理纯楼层标识 B2 → 负二楼
            if text.strip().upper() in abbrev_map:
                return abbrev_map[text.strip().upper()]

            # 处理类似 W:800mm 格式
            pattern = re.compile(r'\b([WHDL])\s*[:：]\s*(\d+\.?\d*\s*(?:mm|cm|m)?)', re.IGNORECASE)
            text = pattern.sub(lambda m: f"{abbrev_map.get(m.group(1).upper(), m.group(1))}:{m.group(2)}", text)

            # 处理类似 W400*H650 或 H650*W400 格式
            pattern_pair = re.compile(r'\b([WHDL])\s*(\d+)\s*[*×x]\s*([WHDL])\s*(\d+)', re.IGNORECASE)
            def replace_pair(match):
                key1 = match.group(1).upper()
                val1 = match.group(2)
                key2 = match.group(3).upper()
                val2 = match.group(4)
                name1 = abbrev_map.get(key1, key1)
                name2 = abbrev_map.get(key2, key2)
                return f"{name1}{val1}×{name2}{val2}"
            text = pattern_pair.sub(replace_pair, text)
        return text

    def log(self, message):
        """发送日志消息到GUI"""
        if self.log_callback:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.log_callback(f"[{timestamp}] {message}")

    def get_contextual_translation(self, text, lang_config_key):
        """根据语言配置获取上下文翻译提示"""
        if lang_config_key not in self.language_configs:
            return text
            
        context_dict = self.language_configs[lang_config_key]['context']
        hints = [f"{term}={trans}" for term, trans in context_dict.items() if term in text]
        if hints:
            return f"建筑术语: {'; '.join(hints[:3])}."
        return text

    def get_glossary_translation(self, text, lang_config_key):
        return self.language_configs[lang_config_key]['glossary'].get(text.strip().casefold())

    def get_layer_glossary_translation(self, text, lang_config_key, layer):
        text = str(text or "")
        if lang_config_key != 'fr_to_zh' or text.strip().casefold() != 'alimentation':
            return None
        layer = str(layer if layer is not None else "").casefold()
        if any(term in layer for term in ('eau', 'plomb', 'sanit', 'hydrau', 'aep')):
            return '供水'
        if any(term in layer for term in ('elec', 'élec', 'cfo', 'cfa', 'power', 'courant')):
            return '供电'
        return None
    
    def post_process_translation(self, text, original, lang_config_key):
        if '建筑术语:' in text and '原文:' in text:
            text = text.split('原文:')[-1].strip()
        text = re.sub(r'.*术语[：:][^.]*\.\s*', '', text)

        corrections = {}

        if lang_config_key == 'zh_to_fr':
            corrections = {
                'variole': 'plafond',
                'virus du plafond': 'plafond',
                'maladie du plafond': 'plafond',
                'plan de variole': 'plan de plafond',
                'fleur de plafond': 'plafond',
                'toilettes salle de bain': 'salle de bain',
                'cuisine cuisine': 'cuisine',
                'écran de contrôle': 'écran de contrôle',
                'contrôle': 'contrôle',
            }

        elif lang_config_key == 'fr_to_zh':
            corrections = self.corrections_fr_to_zh  # <-- 来自 YAML 文件

        # 替换所有定义的错误词
        for wrong, right in corrections.items():
            text = re.sub(rf'\b{re.escape(wrong)}\b', right, text)

        return self.cleaner.normalize_whitespace(text)
   
    def translate_text(self, text, lang_config_key, layer=''):
        if not text or not lang_config_key:
            return text

        cache_key = (text, lang_config_key, str(layer if layer is not None else "").casefold())

        # Step 1: 预清洗
        cleaned = self.cleaner.full_clean(text)

        if cache_key in self.translated_cache:
            return self.translated_cache[cache_key]

        if not cleaned.strip():
            self.safe_log(f"跳过空文本或无效文本: \"{text}\"")
            return self.cleaner.safe_utf8(text)

        try:
            cleaned.encode('utf-8')
        except UnicodeEncodeError as e:
            self.safe_log(f"跳过包含编码问题的文本: \"{text}\" - 错误: {e}")
            return self.cleaner.safe_utf8(text)

        # Step 2: 判定是否跳过翻译
        non_translatable = re.fullmatch(r'[\d\s.,:;*×x\-_/\\%°(){}\[\]]+', cleaned.strip())
        ascii_only = all(ord(c) < 128 for c in cleaned.strip())
        non_word_ratio = sum(1 for c in cleaned if not c.isalnum()) / (len(cleaned) or 1)

        if non_translatable or (ascii_only and non_word_ratio > 0.6):
            self.safe_log(f"跳过非翻译文本（符号/ASCII）: \"{cleaned}\"")
            self.translated_cache[cache_key] = cleaned
            return self.cleaner.safe_utf8(cleaned)

        # Step 3: 缩写处理 & 中文校验
        cleaned = self.preprocess_abbreviations(cleaned, lang_config_key)
        cleaned = self.cleaner.safe_utf8(cleaned)

        lang_config = self.ensure_lang_config(lang_config_key)
        source_code = (lang_config or {}).get("source", "")
        if str(source_code).lower().startswith("zh") and not re.search(r'[\u4e00-\u9fff]', cleaned):
            self.safe_log(f"跳过非中文内容（疑似编号）: \"{cleaned}\"")
            return self.cleaner.safe_utf8(text)

        if not lang_config:
            self.safe_log(f"无效的翻译配置: {lang_config_key}")
            return self.cleaner.safe_utf8(text)
        glossary_translation = self.language_assets.lookup_term(cleaned, lang_config_key, layer, self.project_package_path)
        glossary_translation = glossary_translation or self.get_layer_glossary_translation(cleaned, lang_config_key, layer)
        glossary_translation = glossary_translation or self.get_glossary_translation(cleaned, lang_config_key)
        if glossary_translation:
            final = self.cleaner.safe_utf8(self.cleaner.full_clean(glossary_translation)).strip()
            self.translated_cache[cache_key] = final
            self.safe_log(f"✔ 术语表命中 ({lang_config['name']}): \"{cleaned}\" → \"{final}\"")
            return final

        memory_translation = self.language_assets.lookup_memory(cleaned, lang_config_key, layer)
        if memory_translation:
            final = self.cleaner.safe_utf8(self.cleaner.full_clean(memory_translation)).strip()
            self.translated_cache[cache_key] = final
            self.safe_log(f"✔ 翻译记忆命中 ({lang_config['name']}): \"{cleaned}\" → \"{final}\"")
            return final

        # Step 4: 可读性检查
        printable_chars = sum(1 for char in cleaned if char.isprintable() or '\u4e00' <= char <= '\u9fff')
        if len(cleaned) > 0 and printable_chars / len(cleaned) < 0.5:
            self.safe_log(f"跳过损坏文本(可读字符比例过低): \"{cleaned}\"")
            return self.cleaner.safe_utf8(text)

        if not self.has_mt():
            self.safe_log(f"未译（无引擎）: \"{cleaned}\"")
            return self.cleaner.safe_utf8(text)

        try:
            context = self.get_contextual_translation(cleaned, lang_config_key)
            self.safe_log(f"翻译中 ({lang_config['name']}): {cleaned}")
            if context != cleaned:
                self.safe_log(f"提示术语: {context}")

            # Step 5: provider translation
            if self.mt_provider is not None:
                translated_result = self.mt_provider.translate_text(
                    cleaned, lang_config['source'], lang_config['target']
                )
            elif self.translation_provider == "azure":
                if not self.azure_translator:
                    raise RuntimeError("Azure Translator 未初始化，请配置 API Key")
                translated_result = self.azure_translator.translate_text(
                    cleaned, lang_config['source'], lang_config['target']
                )
            else:
                if not self.deepl_translator:
                    raise RuntimeError("DeepL 未初始化，请配置 API Key")
                deepl_result = self.deepl_translator.translate_text(
                    cleaned,
                    source_lang=lang_config['source'].split('-')[0].upper(),
                    target_lang=(
                        lang_config['target'].upper()
                        if lang_config['target'].startswith('en-')
                        else lang_config['target'].split('-')[0].upper()
                    ),
                )
                translated_result = deepl_result.text

            # Step 6: 翻译结果后处理
            if self.contains_surrogates(translated_result):
                self.safe_log(f"⚠ 翻译结果含代理字符，准备清理: {repr(translated_result)}")
                translated_result = self.cleaner.full_clean(translated_result)

            final = self.post_process_translation(translated_result, cleaned, lang_config_key)
            final = self.cleaner.safe_utf8(final)
            final = self.cleaner.full_clean(final)
            final = self.cleaner.safe_utf8(final).strip()  # ✨ 此处加入 strip

            if self.contains_surrogates(final):
                self.safe_log(f"⚠ 最终翻译仍包含代理字符，将用占位符替换: {repr(final)}")
                final = final.replace('\ufffd', '?')  # 防止乱码
                final = self.cleaner.safe_utf8(final)

            self.translated_cache[cache_key] = final
            self.language_assets.record_memory(cleaned, final, lang_config_key, layer, self.translation_provider)
            self.language_assets.record_usage(self.translation_provider, len(cleaned))
            self.safe_log(f"✔ 翻译完成 ({self.translation_provider}): \"{cleaned}\" → \"{final}\"")
            time.sleep(0.5)
            return final

        except AzureFreeQuotaExceededError as e:
            self.language_assets.record_usage("azure", 0, quota_exceeded=True)
            self.safe_log(str(e), level="error")
            raise
        except Exception as e:
            provider = self.translation_provider or "mt"
            label = "DeepL" if provider == "deepl" else provider
            self.safe_log(f"翻译失败 ({label}): {e} → 原文: \"{cleaned}\"")
            raise RuntimeError(f"{label} 翻译失败: {e}") from e

    def translate_mtext(self, raw, lang_config_key, layer=""):
        return map_translatable(raw, lambda run: self.translate_text(run, lang_config_key, layer))


    def extract_text_entities(
        self,
        doc,
        lang_config,
        include_blocks=False,
        include_attribs=True,
        include_model=True,
        include_paper=True,
        include_frozen=False,
        include_locked=False,
        include_off=False,
        skip_numbers=True,
        skip_dupes=True,
        skip_nonsource=True,
    ):
        """
        提取文本实体。
        支持从模型空间、布局以及块定义中提取文字。
        不再依赖“块已炸开”的假设。
        """
        items = []
        processed_layouts = set()

        # 1. 提取模型空间
        modelspace = doc.modelspace()
        items.extend(self._extract_from_layout(modelspace, include_blocks))
        processed_layouts.add(modelspace.name)

        # 2. 提取布局 (Paper Space)
        for layout in doc.layouts:
            if layout.name not in processed_layouts:
                items.extend(self._extract_from_layout(layout, include_blocks))
                processed_layouts.add(layout.name)

        # 3. 块定义扫描。ACAD_TABLE 和 DIMENSION 的可见图形分别存放在
        # *T / *D 匿名块中，它们没有普通 INSERT 引用；即使用户选择不扫描
        # 所有块，也必须扫描这两类可见内容，否则表格和标注会整片漏译。
        if include_blocks:
            self.safe_log("📦 正在扫描块定义 (Blocks)...")
            block_count = 0
            for block in doc.blocks:
                # 跳过空块
                if len(list(block)) == 0:
                    continue
                # 跳过已经处理过的特殊布局块
                if block.name in processed_layouts or _is_layout_space_block(block.name):
                    continue
                
                # ⚠️ 重要：不再跳过匿名块 (*Uxxx)，确保所有块都被扫描
                # 如果你确定某些匿名块不需要翻译，可以加回过滤，但默认建议全扫
                try:
                    block_items = self._extract_from_layout(block, include_blocks=True, in_block_def=True)
                    if block_items:
                        self.safe_log(f"  -> 块 '{block.name}' 中发现 {len(block_items)} 个文本对象")
                        items.extend(block_items)
                        block_count += 1
                except Exception as e:
                    self.safe_log(f"  ⚠️ 扫描块 '{block.name}' 失败: {e}", level="warning")
            
            self.safe_log(f"✅ 块扫描完成，共处理 {block_count} 个块。")
        else:
            special_block_count = 0
            for block in doc.blocks:
                block_name = block.name.upper()
                if not block_name.startswith(("*T", "*D")):
                    continue
                try:
                    block_items = self._extract_from_layout(block, include_blocks=False, in_block_def=True)
                    if block_items:
                        self.safe_log(f"  -> 可见匿名块 '{block.name}' 中发现 {len(block_items)} 个文本对象")
                        items.extend(block_items)
                        special_block_count += 1
                except Exception as e:
                    self.safe_log(f"  ⚠️ 扫描匿名块 '{block.name}' 失败: {e}", level="warning")
            if special_block_count:
                self.safe_log(f"✅ 已补扫 {special_block_count} 个表格/标注匿名块。")
            else:
                self.safe_log("ℹ️ 未勾选'包含块'，仅跳过普通块定义。")

        if not include_attribs:
            items = [
                item for item in items
                if str(item.get("type") or "").upper() not in {"ATTRIB", "ATTDEF"}
            ]
        from backend.drawings import item_passes_text_filters, keep_extracted_item

        items = [
            item
            for item in items
            if keep_extracted_item(
                doc,
                item,
                include_model=include_model,
                include_paper=include_paper,
                include_frozen=include_frozen,
                include_locked=include_locked,
                include_off=include_off,
            )
        ]
        seen = set()
        marked = []
        for item in items:
            source = item.get("original_text") or ""
            item["duplicate"] = source in seen
            if source not in seen:
                seen.add(source)
            marked.append(item)
        items = [
            item
            for item in marked
            if item_passes_text_filters(
                item,
                skip_numbers=skip_numbers,
                skip_dupes=skip_dupes,
                skip_nonsource=skip_nonsource,
                translation_mode=lang_config,
            )
        ]
        self.safe_log(f"📝 总共提取到 {len(items)} 个文本对象。")
        return items

    SUPPORTED_TEXT_TYPES = ['TEXT', 'MTEXT', 'ATTDEF', 'ATTRIB', 'MULTILEADER', 'DIMENSION', 'ACAD_TABLE']

    def _clean_entity_text(self, text):
        """清洗实体中的原始文本"""
        if not text:
            return ""
        return self.cleaner.full_clean(decode_oda_mbcs_escapes(str(text)))

    def _get_multileader_mtext(self, entity):
        if hasattr(entity, 'get_mtext_content'):
            try:
                return entity.get_mtext_content() or ""
            except Exception:
                pass
        context = getattr(entity, 'context', None)
        mtext = getattr(context, 'mtext', None) if context else None
        if mtext is not None:
            return getattr(mtext, 'default_content', '') or ""
        return ""

    def _get_multileader_block_content(self, entity):
        if hasattr(entity, 'get_block_content'):
            try:
                return dict(entity.get_block_content() or {})
            except Exception:
                pass
        return {}

    def _get_acad_table_text_slots(self, entity):
        """Return writable raw-text slots from a modern ``ACAD_TABLE`` entity.

        ODA/DXF stores the rendered table in an anonymous ``*T`` block, but
        retains the authoritative cell values in the ``AcDbTable`` subclass as
        group-code 302 tags.  Changing only the rendered block is not durable:
        AutoCAD can rebuild it from these source values when opening the file.
        """
        try:
            tags = entity.xtags.get_subclass("AcDbTable")
        except (AttributeError, KeyError, TypeError):
            return []
        return [
            (index, tag.value)
            for index, tag in enumerate(tags)
            if getattr(tag, "code", None) == 302 and isinstance(getattr(tag, "value", None), str)
        ]

    def _write_acad_table_text_slot(self, entity, slot, text):
        try:
            tags = entity.xtags.get_subclass("AcDbTable")
            index = int(slot)
            tag = tags[index]
        except (AttributeError, KeyError, TypeError, ValueError, IndexError) as exc:
            raise ValueError(f"无效 ACAD_TABLE 文字槽: {slot}") from exc
        if getattr(tag, "code", None) != 302:
            raise ValueError(f"ACAD_TABLE 文字槽 {slot} 不再指向文本")
        from ezdxf.lldxf.types import DXFTag
        old_text = tag.value
        tags[index] = DXFTag(302, text)
        self._sync_acad_table_preview_block(entity, old_text, text)

    def _sync_acad_table_preview_block(self, entity, old_text, new_text):
        """Keep the anonymous ``*T`` render block in step with group-code 302.

        AutoCAD may rebuild the table from 302, or keep showing the ``*T``
        preview. Writing both is what survives a regen; ezdxf cannot prove
        AutoCAD's own rebuild.
        """
        if not old_text or old_text == new_text:
            return
        doc = getattr(entity, "doc", None)
        if doc is None:
            return
        block_name = ""
        try:
            getter = getattr(entity, "get_block_name", None)
            if callable(getter):
                block_name = getter() or ""
            if not block_name:
                block_name = entity.dxf.get("geometry", "") or ""
        except Exception:
            return
        if not block_name:
            return
        try:
            block = doc.blocks.get(block_name)
        except Exception:
            return
        if block is None:
            return
        old_clean = self._clean_entity_text(old_text)
        replaced = 0
        for preview in block:
            kind = preview.dxftype()
            if kind == "TEXT":
                current = getattr(preview.dxf, "text", "") or ""
                if current == old_text or self._clean_entity_text(current) == old_clean:
                    preview.dxf.text = new_text
                    replaced += 1
            elif kind == "MTEXT":
                current = getattr(preview.dxf, "text", "") or ""
                try:
                    visible = preview.plain_text(fast=False)
                except Exception:
                    visible = current
                if current == old_text or self._clean_entity_text(visible) == old_clean:
                    self._write_mtext_entity(preview, new_text)
                    replaced += 1
        if replaced:
            self.safe_log(f"  已同步表格预览块 '{block_name}' 中 {replaced} 处文字")

    def _append_text_item(self, items, entity, layout, field, raw_text, raw_source=None):
        if not raw_text or not str(raw_text).strip():
            return
        decoded_text = decode_oda_mbcs_escapes(str(raw_text))
        if not self.is_valid_text_for_translation(decoded_text):
            return
        cleaned = self._clean_entity_text(decoded_text)
        if not cleaned:
            return
        coded = decode_oda_mbcs_escapes(str(raw_source)) if raw_source is not None else decoded_text
        handle = ""
        try:
            handle = str(getattr(entity.dxf, "handle", "") or "")
        except Exception:
            handle = ""
        items.append({
            'entity': entity,
            'field': field,
            'original_text': cleaned,
            'raw_source': str(coded).strip(),
            'layer': str(getattr(entity.dxf, 'layer', 'DEFAULT') or "0"),
            'location': layout.name if hasattr(layout, 'name') else 'Unknown',
            'type': entity.dxftype(),
            'handle': handle,
        })

    def _should_translate_attdef_tag(self, tag, default_text):
        """
        判断是否翻译属性标记(Tag)。
        短编码如 MJ01、P2 不翻译；含中文或长说明性文字则翻译。
        """
        tag = (tag or '').strip()
        text = (default_text or '').strip()
        if not tag:
            return False
        if re.fullmatch(r'[A-Za-z0-9_\-]{1,15}', tag):
            return False
        tag_norm = self._clean_entity_text(tag)
        text_norm = self._clean_entity_text(text)
        if text_norm and tag_norm == text_norm:
            return False
        if re.search(r'[\u4e00-\u9fff]', tag):
            return True
        if not text and len(tag) > 3:
            return True
        return False

    def _sync_attrib_tags(self, doc, old_tag, new_tag):
        """ATTDEF 标记变更后，同步更新图上所有 ATTRIB 实例的标记"""
        if not old_tag or old_tag == new_tag:
            return
        count = 0
        layouts = [doc.modelspace()]
        layout_names = {doc.modelspace().name}
        for layout in doc.layouts:
            if layout.name not in layout_names:
                layouts.append(layout)
                layout_names.add(layout.name)
        for layout in layouts:
            for insert in layout.query('INSERT'):
                for attrib in getattr(insert, 'attribs', []) or []:
                    if getattr(attrib.dxf, 'tag', '') == old_tag:
                        attrib.dxf.tag = new_tag[:255]
                        count += 1
        if count:
            self.safe_log(f"  已同步 {count} 个 ATTRIB 标记: {old_tag!r} → {new_tag!r}")

    def collect_entity_text_items(self, entity, layout):
        """收集实体上所有可翻译字段（含 ATTDEF 提示、多重引线）"""
        items = []
        dxftype = entity.dxftype()

        if dxftype in ('TEXT', 'ATTRIB'):
            text_val = ""
            if hasattr(entity.dxf, 'text'):
                text_val = entity.dxf.text or ''
            elif hasattr(entity, 'text'):
                text_val = entity.text or ''
            self._append_text_item(items, entity, layout, 'text', text_val)
            if dxftype == 'ATTRIB':
                tag_val = getattr(entity.dxf, 'tag', '') or ''
                if self._should_translate_attdef_tag(tag_val, text_val):
                    self._append_text_item(items, entity, layout, 'tag', tag_val)

        elif dxftype == 'MTEXT':
            coded = getattr(entity.dxf, 'text', '') or ''
            try:
                visible = entity.plain_text(fast=False)
            except Exception:
                visible = coded
            self._append_text_item(items, entity, layout, 'text', visible, raw_source=coded)

        elif dxftype == 'ATTDEF':
            text_val = getattr(entity.dxf, 'text', '') or ''
            prompt_val = getattr(entity.dxf, 'prompt', '') or ''
            tag_val = getattr(entity.dxf, 'tag', '') or ''
            self._append_text_item(items, entity, layout, 'text', text_val)
            self._append_text_item(items, entity, layout, 'prompt', prompt_val)
            if self._should_translate_attdef_tag(tag_val, text_val):
                self._append_text_item(items, entity, layout, 'tag', tag_val)

        elif dxftype == 'MULTILEADER':
            mtext_content = self._get_multileader_mtext(entity)
            if mtext_content.strip():
                self._append_text_item(items, entity, layout, 'mtext', mtext_content)
            else:
                for tag, value in self._get_multileader_block_content(entity).items():
                    self._append_text_item(items, entity, layout, f'block:{tag}', value)

        elif dxftype == 'DIMENSION':
            # A literal dimension override (e.g. a Chinese explanatory note)
            # lives on the DIMENSION entity. ``<>`` is AutoCAD's measurement
            # placeholder and must remain untouched.
            text_val = getattr(entity.dxf, 'text', '') or ''
            if text_val.strip() not in {'', '<>'}:
                self._append_text_item(items, entity, layout, 'text', text_val)

        elif dxftype == 'ACAD_TABLE':
            for slot, text_val in self._get_acad_table_text_slots(entity):
                self._append_text_item(items, entity, layout, f'table:{slot}', text_val)

        return items

    def _entity_key(self, entity, field='text'):
        handle = getattr(entity.dxf, 'handle', None)
        if handle:
            return (handle, field)
        return (id(entity), field)

    def _extract_from_block_layout(self, block_layout, layout, block_name, items, seen, depth=0):
        """直接扫描块定义内的文字（含嵌套块），用于图框/标题栏等块参照"""
        if depth > 15:
            return 0
        found = 0
        for entity in block_layout:
            dxftype = entity.dxftype()
            if dxftype == 'INSERT':
                try:
                    nested_layout = entity.block()
                    nested_name = getattr(entity.dxf, 'name', '?')
                    if nested_layout is not None:
                        found += self._extract_from_block_layout(
                            nested_layout,
                            layout,
                            f"{block_name}>{nested_name}",
                            items,
                            seen,
                            depth + 1,
                        )
                except Exception:
                    pass
                continue
            if dxftype not in self.SUPPORTED_TEXT_TYPES:
                continue
            if not self.enable_v02_entities and dxftype in {"DIMENSION", "ACAD_TABLE"}:
                continue
            for it in self.collect_entity_text_items(entity, layout):
                key = self._entity_key(it['entity'], it['field'])
                if key in seen:
                    continue
                seen.add(key)
                it['location'] = f"{layout.name}|块:{block_name}"
                items.append(it)
                found += 1
        return found

    def _extract_from_insert(self, insert, layout, include_blocks, items, seen):
        """从块引用提取属性文字，以及图框/标题栏等块内可见文字"""
        block_name = getattr(insert.dxf, 'name', '?')

        for attrib in getattr(insert, 'attribs', []) or []:
            for it in self.collect_entity_text_items(attrib, layout):
                key = self._entity_key(it['entity'], it['field'])
                if key in seen:
                    continue
                seen.add(key)
                items.append(it)

        if include_blocks:
            return

        try:
            block_layout = insert.block()
        except Exception as e:
            self.safe_log(f"  ⚠️ 无法打开块 '{block_name}': {e}", level="warning")
            return
        if block_layout is None:
            return

        found = self._extract_from_block_layout(block_layout, layout, block_name, items, seen)
        if found:
            self.safe_log(f"  -> 块参照 '{block_name}' 中发现 {found} 个块内文字")

    def _extract_from_layout(self, layout, include_blocks=False, in_block_def=False):
        """
        从布局提取 TEXT/MTEXT/ATTRIB 等，并扫描 INSERT 块引用（图框/标题栏）。
        未勾选「翻译块内文字」时，仍会通过 INSERT 提取当前图纸可见的块内文字。
        """
        items = []
        seen = set()
        for entity in layout:
            dxftype = entity.dxftype()
            if dxftype == 'INSERT' and not in_block_def:
                self._extract_from_insert(entity, layout, include_blocks, items, seen)
            elif dxftype in self.SUPPORTED_TEXT_TYPES:
                if not self.enable_v02_entities and dxftype in {"DIMENSION", "ACAD_TABLE"}:
                    continue
                for it in self.collect_entity_text_items(entity, layout):
                    key = self._entity_key(it['entity'], it['field'])
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append(it)
        return items

    def is_valid_text_for_translation(self, text):
        """检查文本是否适合翻译（增强编码检查）"""
        if not text or not text.strip():
            return False

        cleaned = self.cleaner.full_clean(decode_oda_mbcs_escapes(text))

        if not cleaned.strip():
            return False

        # 检查是否包含无效字符
        invalid_chars = sum(1 for char in cleaned if not self.cleaner.is_valid_char(char))
        if invalid_chars > 0:
            self.safe_log(f"发现 {invalid_chars} 个无效字符，跳过文本: \"{text[:20]}...\"")
            return False

        # 检查可读性
        printable_chars = sum(1 for char in cleaned if (
            char.isprintable() or 
            char.isspace() or 
            '\u4e00' <= char <= '\u9fff'
        ))
        if len(cleaned) > 0 and printable_chars / len(cleaned) < 0.8:
            return False

        return True

    def _write_mtext_entity(self, entity, cleaned_text):
        text = cleaned_text or ""
        if "\\" in text or "{" in text:
            formatted_text = text
        else:
            formatted_text = self.cleaner.escape_mtext_special_chars(text)
        self.safe_log(f"写入 MTEXT: {repr(formatted_text[:50])}...")
        entity.dxf.text = formatted_text
        if hasattr(entity, 'text'):
            entity.text = formatted_text

    def write_back_translation(self, entity, new_text, field='text'):
        try:
            cleaned_text = self.fully_clean_for_write(new_text)
            dxftype = entity.dxftype()
            field_label = {
                'text': '文本', 'prompt': '属性提示', 'tag': '属性标记', 'mtext': '多重引线',
            }.get(field, field.replace('block:', '块属性:').replace('table:', '表格单元:'))

            if len(cleaned_text) > 0:
                first_char = cleaned_text[0]
                self.safe_log(
                    f"准备写入 [{dxftype}/{field_label}] - 首字符: "
                    f"'{first_char}' (Unicode: U+{ord(first_char):04X})"
                )
            else:
                self.safe_log(f"准备写入 [{dxftype}/{field_label}] - 文本为空!")

            if field == 'text' and dxftype in ("TEXT", "ATTRIB", "ATTDEF", "DIMENSION"):
                entity.dxf.text = cleaned_text

            elif field == 'prompt' and dxftype == "ATTDEF":
                entity.dxf.prompt = cleaned_text

            elif field == 'tag' and dxftype in ("ATTDEF", "ATTRIB"):
                entity.dxf.tag = cleaned_text[:255]

            elif field == 'text' and dxftype == "MTEXT":
                self._write_mtext_entity(entity, cleaned_text)

            elif field == 'mtext' and dxftype == "MULTILEADER":
                payload = new_text if ("\\" in str(new_text or "") or "{" in str(new_text or "")) else cleaned_text
                if hasattr(entity, 'set_mtext_content'):
                    entity.set_mtext_content(payload)
                else:
                    context = getattr(entity, 'context', None)
                    mtext = getattr(context, 'mtext', None) if context else None
                    if mtext is not None:
                        mtext.default_content = payload
                    else:
                        raise Exception("无法写入 MULTILEADER 文字内容")

            elif field.startswith('block:') and dxftype == "MULTILEADER":
                tag = field[6:]
                content = self._get_multileader_block_content(entity)
                content[tag] = cleaned_text
                if hasattr(entity, 'set_block_content'):
                    entity.set_block_content(content)
                else:
                    raise Exception("无法写入 MULTILEADER 块属性")

            elif field.startswith('table:') and dxftype == "ACAD_TABLE":
                self._write_acad_table_text_slot(entity, field[6:], cleaned_text)

            else:
                raise ValueError(f"未知写入目标: {dxftype}/{field}")

        except Exception:
            self.safe_log("写回失败")
            raise

    def translate_cad_file(self, input_file, output_file, lang_config, include_blocks=False, output_format="source", output_version="", resume_event=None, cancel_event=None, style="纯译文", enable_v02=True, include_attribs=True, include_model=True, include_paper=True, include_frozen=False, include_locked=False, include_off=False, skip_numbers=True, skip_dupes=True, skip_nonsource=True):
        from backend.cad import CadConversionSession

        self.enable_v02_entities = bool(enable_v02)
        wait_for_translation(resume_event, cancel_event)
        with CadConversionSession(input_file, self.safe_log, output_format, output_version) as session:
            work_input = session.work_input
            work_output = session.work_output_path() or output_file
            wait_for_translation(resume_event, cancel_event)
            self._translate_cad_file_dxf(
                work_input, work_output, lang_config, include_blocks, input_file, "", resume_event, cancel_event, style=style, include_attribs=include_attribs, include_model=include_model, include_paper=include_paper, include_frozen=include_frozen, include_locked=include_locked, include_off=include_off, skip_numbers=skip_numbers, skip_dupes=skip_dupes, skip_nonsource=skip_nonsource
            )
            if session.meta.is_dwg or output_version or output_format == "dwg":
                wait_for_translation(resume_event, cancel_event)
                session.finalize(work_output, output_file)

    def _translate_cad_file_dxf(
        self, input_file, output_file, lang_config, include_blocks=False, source_label=None, output_version="", resume_event=None, cancel_event=None, style="纯译文", include_attribs=True, include_model=True, include_paper=True, include_frozen=False, include_locked=False, include_off=False, skip_numbers=True, skip_dupes=True, skip_nonsource=True
    ):
        display_name = source_label or input_file
        self.safe_log(f"正在读取: {display_name}")
        self.safe_log(f"当前写入字体: {self.default_font}")
        
        doc = None
        # 自动检测编码读取
        try:
            doc = ezdxf.readfile(input_file) 
            self.safe_log("✅ 成功读取文件 (自动检测编码)")
        except Exception as e:
            self.safe_log("❌ 无法读取DXF文件")
            raise ValueError("无法读取DXF文件") from e

        if doc is None:
            raise ValueError("无法读取DXF文件")

        # ============================================================
        # 🔥 核心逻辑：直接提取 (不再炸开块)
        # ============================================================
        # 注意：这里直接调用修改后的 extract_text_entities，它内部会处理块遍历
        items = self.extract_text_entities(
            doc,
            lang_config,
            include_blocks=include_blocks,
            include_attribs=include_attribs,
            include_model=include_model,
            include_paper=include_paper,
            include_frozen=include_frozen,
            include_locked=include_locked,
            include_off=include_off,
            skip_numbers=skip_numbers,
            skip_dupes=skip_dupes,
            skip_nonsource=skip_nonsource,
        )

        # ============================================================
        # 执行翻译循环
        # ============================================================
        total_items = len(items)
        if total_items == 0:
            self.safe_log("⚠️ 未找到任何可翻译的文本对象。")
        else:
            self.safe_log(f"🚀 开始翻译，共发现 {total_items} 个文本对象...")
            
            from backend.drawings import apply_pdf_style, normalize_pdf_style

            style = normalize_pdf_style(style)
            bilingual = []
            successful_translations = 0
            skipped_invalid = 0

            for i, item in enumerate(items, 1):
                wait_for_translation(resume_event, cancel_event)
                original_text = item['original_text']
                
                if not self.is_valid_text_for_translation(original_text):
                    skipped_invalid += 1
                    item['translated_text'] = original_text
                    continue

                entity = item['entity']
                field = item.get('field', 'text')
                layer = item.get('layer', '')
                raw = item.get('raw_source') or original_text
                kind = entity.dxftype() if hasattr(entity, 'dxftype') else str(item.get('type') or '')
                formatted = kind in {"MTEXT", "MULTILEADER"} and ("\\" in str(raw) or "{" in str(raw))
                if formatted:
                    translated_raw = map_translatable(
                        raw,
                        lambda run, layer=layer: self.translate_text(run, lang_config, layer),
                    )
                    translated = original_text if translated_raw == raw else self.translate_text(original_text, lang_config, layer)
                    item['translated_text'] = translated
                    payload = translated_raw
                    changed = translated_raw != raw
                else:
                    translated = self.translate_text(original_text, lang_config, layer)
                    item['translated_text'] = translated
                    payload = translated
                    changed = translated != original_text

                if changed:
                    bilingual.append({
                        "handle": item.get("handle") or "",
                        "source": original_text,
                        "target": translated,
                        "raw": raw,
                        "target_raw": payload if formatted else translated,
                        "field": field,
                        "type": kind,
                        "location": item.get("location") or "",
                    })
                    if style == "纯译文":
                        try:
                            if kind == "MTEXT" and formatted:
                                self._write_mtext_entity(entity, payload)
                            else:
                                self.write_back_translation(entity, payload, field)
                            if field == 'tag':
                                self._sync_attrib_tags(
                                    doc,
                                    item.get('raw_source', original_text),
                                    translated,
                                )
                            successful_translations += 1
                        except Exception as e:
                            self.safe_log("写回 CAD 实体失败", level="error")
                            raise RuntimeError("写回 CAD 实体失败") from e
                    else:
                        successful_translations += 1
                
                if i % 10 == 0 or i == total_items:
                    self.safe_log(f"   进度: {i}/{total_items} ({i/total_items*100:.1f}%)")

            unmatched = total_items - skipped_invalid - successful_translations
            if unmatched > 0:
                self.safe_log(f"未译 {unmatched} 条（术语表未命中，无引擎）。")
            if style != "纯译文" and bilingual:
                stamped = apply_pdf_style(doc, bilingual, style)
                self.safe_log(f"版式 {style}：已叠 {stamped} 条。")
            self.safe_log(f"翻译统计：成功 {successful_translations}, 跳过 {skipped_invalid}")
            if successful_translations == 0 and unmatched > 0:
                raise RuntimeError("术语表没有命中，请配置翻译引擎或手填译文")

        # ============================================================
        # 保存文件
        # ============================================================
        self.safe_log("💾 正在保存文件...")
        try:
            wait_for_translation(resume_event, cancel_event)
            rewrite_shx_styles(doc, self.safe_log)
            if output_version and output_file.lower().endswith(".dxf"):
                doc.dxfversion = output_version
            with atomic_output_path(output_file) as temporary_output:
                doc.saveas(temporary_output)
            self.safe_log(f"✅ 文件成功保存: {output_file}")
        except Exception as e:
            self.safe_log(f"❌ 文件保存失败: {e}")
            raise e

        self.safe_log("🎉 全部任务完成！")

    # 注意：你原来的 clean_all_entities 和 write_back_translation 保持不变即可
    # 只要它们能正确处理 entity 对象，无论这个 entity 来自模型空间还是块，操作都是一样的。

# GUI类保持不变，只需要更新版本号
class CADTranslatorGUI:
    def __init__(self):
        self.log_text = None
        self.root = tk.Tk()
        self.root.title(f"Honsen CAD中法英互译工具 v{APP_VERSION}")
        self.root.geometry("850x750")
        self.root.resizable(True, True)
        self.cleaner = TextCleaner()
        try:
            icon_path = resource_path("ico.ico")
            self.root.iconbitmap(icon_path)
        except:
            pass  # 如果图标文件不存在，忽略错误
        self.deepl_key = tk.StringVar()
        # 日志队列
        self.log_queue = queue.Queue()
        
        # 变量
        self.input_file = tk.StringVar()
        self.output_dir = tk.StringVar()
        now = datetime.now()
        default_filename = f"translated_cad_{now.strftime('%Hh%M_%d-%m-%y')}"
        self.output_name = tk.StringVar(value=default_filename)
        self.translate_blocks = tk.BooleanVar(value=False)  # 默认不翻译块内文字
        self.translation_mode = tk.StringVar(value='zh_to_fr')  # 默认中文→法语
        self._save_job = None
        self.setup_ui()
        self.load_api_keys()
        self.check_log_queue()
    def _create_translator(self):
        translator = CADChineseTranslator(log_callback=self.log_message)
        translator.deepl_api_key = self.deepl_key.get().strip()
        return translator
    def safe_text_for_tkinter(self, text):
        """
        过滤超出tkinter支持范围的Unicode字符
        tkinter在某些版本中不支持U+FFFF以上的字符（如emoji）
        """
        if not text:
            return ""
        
        safe_chars = []
        for char in text:
            # 过滤超出BMP（基本多文种平面）的字符
            if ord(char) <= 0xFFFF:
                safe_chars.append(char)
            else:
                # 将不支持的字符替换为方括号描述
                char_name = f"[U+{ord(char):04X}]"
                safe_chars.append(char_name)
        
        return ''.join(safe_chars)
        
    def setup_ui(self):
        # 主容器
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # 创建标签页控件
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=10)
        
        # 创建翻译功能页面
        self.translation_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.translation_frame, text='翻译功能')
        
        # 创建版本日志页面
        self.changelog_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.changelog_frame, text='版本更新日志')
        
        # 设置翻译功能页面
        self.setup_translation_tab()
        
        # 设置版本日志页面
        self.setup_changelog_tab()
        
    def setup_translation_tab(self):
        main_frame = ttk.Frame(self.translation_frame, padding="10")
        main_frame.pack(fill='both', expand=True)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(7, weight=1)

        title_label = tk.Label(main_frame, text=f"Honsen CAD 中法英互译工具 v{APP_VERSION}\n支持 DXF / DWG（DWG 需 ODA File Converter）",
                            font=('宋体', 16, 'bold'))
        title_label.grid(row=0, column=0, columnspan=3, pady=(0, 20))

        ttk.Label(main_frame, text="选择 CAD 文件:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(main_frame, textvariable=self.input_file, width=50).grid(
            row=1, column=1, sticky=(tk.W, tk.E), pady=5, padx=(5, 5))
        ttk.Button(main_frame, text="浏览", command=self.browse_input_file).grid(row=1, column=2, pady=5)

        ttk.Label(main_frame, text="输出目录:").grid(row=2, column=0, sticky=tk.W, pady=5)
        ttk.Entry(main_frame, textvariable=self.output_dir, width=50).grid(
            row=2, column=1, sticky=(tk.W, tk.E), pady=5, padx=(5, 5))
        ttk.Button(main_frame, text="浏览", command=self.browse_output_dir).grid(row=2, column=2, pady=5)

        ttk.Label(main_frame, text="输出文件名:").grid(row=3, column=0, sticky=tk.W, pady=5)
        name_frame = ttk.Frame(main_frame)
        name_frame.grid(row=3, column=1, sticky=(tk.W, tk.E), pady=5, padx=(5, 5))
        name_frame.columnconfigure(0, weight=1)
        ttk.Entry(name_frame, textvariable=self.output_name).grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 5))
        self.output_ext_label = ttk.Label(name_frame, text=".dxf")
        self.output_ext_label.grid(row=0, column=1)

        options_api_container = ttk.Frame(main_frame)
        options_api_container.grid(row=4, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=10)
        options_api_container.columnconfigure(0, weight=1)
        options_api_container.columnconfigure(1, weight=1)

        options_frame = ttk.LabelFrame(options_api_container, text="翻译选项", padding="10")
        options_frame.grid(row=0, column=0, sticky=(tk.N, tk.EW), padx=(0, 10))

        ttk.Label(options_frame, text="翻译模式:").grid(row=0, column=0, sticky=tk.W, pady=5)
        mode_frame = ttk.Frame(options_frame)
        mode_frame.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(10, 0))
        for column, (label, value) in enumerate((
            ("中文→法语", "zh_to_fr"), ("法语→中文", "fr_to_zh"),
            ("中文→英语", "zh_to_en"), ("英语→中文", "en_to_zh"),
        )):
            ttk.Radiobutton(
                mode_frame, text=label, variable=self.translation_mode, value=value,
                command=self._update_output_name_prefix,
            ).grid(row=column // 2, column=column % 2, sticky=tk.W, padx=(0, 15), pady=2)

        ttk.Checkbutton(options_frame, text="翻译CAD块(Block)内的文字", variable=self.translate_blocks).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(10, 0))
        note_label = tk.Label(options_frame, text="注意：勾选后将翻译所有块定义（含未使用的标准符号块）", font=('宋体', 9), fg='gray')
        note_label.grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(5, 0))
        mleader_note = tk.Label(
            options_frame,
            text="图框/标题栏文字会自动从块引用中提取，无需勾选",
            font=('宋体', 9),
            fg='gray',
        )
        mleader_note.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(2, 0))

        api_frame = ttk.LabelFrame(options_api_container, text="DeepL API Key", padding="10")
        api_frame.grid(row=0, column=1, sticky=(tk.N, tk.EW))
        ttk.Label(api_frame, text="API Key:").grid(row=0, column=0, sticky=tk.W, pady=3)
        ttk.Entry(api_frame, textvariable=self.deepl_key, width=40, show="*").grid(
            row=0, column=1, sticky=(tk.W, tk.E), padx=5
        )
        api_note = tk.Label(api_frame, text="翻译需联网并配置有效的 DeepL API Key", font=('宋体', 9), fg='gray')
        api_note.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(5, 0))
        self.deepl_key.trace_add("write", lambda *args: self.save_api_keys())

        # 添加按钮组到 api_frame 下方
        style = ttk.Style()
        style.configure("Big.TButton", font=("Microsoft YaHei", 12, "bold"))

        button_frame = ttk.Frame(api_frame)
        button_frame.grid(row=2, column=0, columnspan=2, pady=(12, 0), sticky=tk.W)

        self.start_button = ttk.Button(
            button_frame, text="开始翻译", command=self.start_translation, style="Big.TButton"
        )
        self.start_button.pack(side=tk.LEFT, padx=(0, 10), ipady=4)

        ttk.Button(button_frame, text="清除日志", command=self.clear_log).pack(side=tk.LEFT)


        self.progress = ttk.Progressbar(main_frame, mode='indeterminate')
        self.progress.grid(row=6, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=10)

        log_frame = ttk.LabelFrame(main_frame, text="实时日志", padding="5")
        log_frame.grid(row=7, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S), pady=10)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        main_frame.rowconfigure(7, weight=1)
        font = pick_available_font()
        self.log_text = tk.Text(log_frame, height=15, wrap=tk.WORD, font=(font, 11))
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))

        self.status_var = tk.StringVar(value="就绪")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN)
        status_bar.grid(row=8, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(10, 0))

        footer_frame = ttk.Frame(main_frame)
        footer_frame.grid(row=9, column=0, columnspan=3, pady=(10, 5), sticky=(tk.W, tk.E))
        footer_frame.columnconfigure((0, 1, 2), weight=1)
        ttk.Label(footer_frame, text="作者: Etienne").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(footer_frame, text="邮箱：etn@live.com").grid(row=0, column=1, sticky=tk.EW)
        ttk.Label(footer_frame, text="翻译完需要打开CAD调整文字位置").grid(row=0, column=2, sticky=tk.E)

    def setup_changelog_tab(self):
        """设置版本更新日志标签页，内容读取自 changelog.json 文件"""
        changelog_main_frame = ttk.Frame(self.changelog_frame, padding="15")
        changelog_main_frame.pack(fill='both', expand=True)

        # 标题
        title_frame = ttk.Frame(changelog_main_frame)
        title_frame.pack(fill='x', pady=(0, 20))

        title_label = tk.Label(title_frame, text="CAD中法互译工具", 
                            font=('Microsoft YaHei', 18, 'bold'))
        title_label.pack()

        subtitle_label = tk.Label(title_frame, text="版本更新历史", 
                                font=('Microsoft YaHei', 12), fg='gray')
        subtitle_label.pack()

        # 创建滚动文本区域
        text_frame = ttk.Frame(changelog_main_frame)
        text_frame.pack(fill='both', expand=True)
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)

        # 文本框和滚动条
        self.changelog_text = tk.Text(text_frame, wrap=tk.WORD, font=('Consolas', 10), 
                                    bg='#f8f9fa', fg='#333333', padx=15, pady=15)
        changelog_scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.changelog_text.yview)
        self.changelog_text.configure(yscrollcommand=changelog_scrollbar.set)

        self.changelog_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        changelog_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))

        # 从资源路径加载 changelog（兼容打包后的 exe）
        changelog_path = resource_path("changelog.json")
        try:
            with open(changelog_path, 'r', encoding='utf-8') as f:
                changelog_data = json.load(f)

            content_lines = []
            for entry in changelog_data.get("changelog", []):
                version = entry.get("version", "未知版本")
                date = entry.get("date", "")
                title = entry.get("title", "")
                content_lines.append(f"版本 {version} - {date} {title}".strip())
                content_lines.append("=" * 80)
                content_lines.extend(entry.get("content", []))
                content_lines.append("")  # 空行分隔

            final_text = '\n'.join(content_lines).strip()
            self.changelog_text.insert('1.0', self.safe_text_for_tkinter(final_text))
            self.changelog_text.config(state='disabled')
        except Exception as e:
            self.changelog_text.insert('1.0', f"无法加载更新日志文件: {e}")

        # 底部信息
        bottom_frame = ttk.Frame(changelog_main_frame)
        bottom_frame.pack(fill='x', pady=(15, 0))

        info_label = tk.Label(bottom_frame, 
                            text=f"© 2025 Honsen - CAD中法英互译工具 v{APP_VERSION}",
                            font=('Microsoft YaHei', 9), fg='gray')
        info_label.pack()
    def browse_input_file(self):
        filename = filedialog.askopenfilename(
            title="选择 CAD 文件",
            filetypes=[
                ("CAD files", "*.dxf;*.dwg"),
                ("DXF files", "*.dxf"),
                ("DWG files", "*.dwg"),
                ("All files", "*.*"),
            ],
        )
        if filename:
            self.input_file.set(filename)

            # 自动设置输出目录为输入文件所在目录
            if not self.output_dir.get():
                self.output_dir.set(os.path.dirname(filename))

            # 自动根据选择的文件名和翻译模式设置输出名
            base_name = os.path.splitext(os.path.basename(filename))[0]
            out_ext = os.path.splitext(filename)[1].lower() or ".dxf"
            if hasattr(self, "output_ext_label"):
                self.output_ext_label.config(text=out_ext)
            now = datetime.now()
            timestamp = now.strftime('%Hh%M_%d-%m-%y')
            
            self.output_name.set(f"{output_prefix(self.translation_mode.get())}_{base_name}_{timestamp}")

    def _update_output_name_prefix(self):
        name = self.output_name.get().strip()
        if not name:
            return
        prefix = output_prefix(self.translation_mode.get())
        if re.match(r"^(fr|zh|en)_", name):
            name = re.sub(r"^(fr|zh|en)_", f"{prefix}_", name)
        else:
            name = f"{prefix}_{name}"
        self.output_name.set(name)
    
    def browse_output_dir(self):
        directory = filedialog.askdirectory(title="选择输出目录")
        if directory:
            self.output_dir.set(directory)
    def log_message(self, message, level="INFO"):
        """将日志放入队列，由主线程 check_log_queue 写入 UI（线程安全）"""
        try:
            if hasattr(self, 'translator') and hasattr(self.translator, 'cleaner'):
                cleaned = self.translator.cleaner.clean_for_log(message)
            else:
                cleaned = self.cleaner.clean_for_log(str(message))
            safe_message = self.safe_text_for_tkinter(cleaned)
            self.log_queue.put(safe_message)
        except Exception as e:
            print("[日志处理异常]:", e)
            print("原始内容:", repr(message))
    def on_close(self):
        """窗口关闭时安全退出"""
        self.root.quit()
        self.root.destroy()

    def check_log_queue(self):
        """检查日志队列并更新UI（含异常处理）"""
        try:
            while True:
                message = self.log_queue.get_nowait()
                if isinstance(message, str):  # 防御式检查
                    # 使用安全文本处理
                    safe_message = self.safe_text_for_tkinter(message)
                    self.log_text.insert(tk.END, safe_message + "\n")
                    self.log_text.see(tk.END)
        except queue.Empty:
            pass
        except Exception as e:
            import traceback
            print("日志处理异常:")
            traceback.print_exc()
        finally:
            if hasattr(self, 'root') and self.root.winfo_exists():
                self.root.after(100, self.check_log_queue)

    def clear_log(self):
        """清除日志内容"""
        self.log_text.delete(1.0, tk.END)
    
    def validate_inputs(self):
        if not self.input_file.get():
            messagebox.showerror("错误", "请选择输入文件")
            return False
        
        if not os.path.exists(self.input_file.get()):
            messagebox.showerror("错误", "输入文件不存在")
            return False
        
        if not self.input_file.get().lower().endswith(('.dxf', '.dwg')):
            messagebox.showerror("错误", "请选择 DXF 或 DWG 文件")
            return False
        if self.input_file.get().lower().endswith('.dwg'):
            from backend.cad import dwg_unavailable_message, odafc_available
            if not odafc_available():
                messagebox.showerror("无法处理 DWG", dwg_unavailable_message())
                return False
        
        if not self.output_dir.get():
            messagebox.showerror("错误", "请选择输出目录")
            return False
        
        if not os.path.exists(self.output_dir.get()):
            messagebox.showerror("错误", "输出目录不存在")
            return False
        
        if not self.output_name.get().strip():
            messagebox.showerror("错误", "请输入输出文件名")
            return False

        if not self.deepl_key.get().strip():
            messagebox.showerror("错误", "请配置 DeepL API Key")
            return False
        
        return True

    # 加载和保存 API Key
    def load_api_keys(self):
        """从本地配置文件加载 API Key"""
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.deepl_key.set(config.get("deepl_key", ""))
                    self.log_message(" 已加载保存的 DeepL API Key")
            except Exception as e:
                self.log_message(f" 加载配置失败: {e}")

    def save_api_keys(self):
        """防抖保存 API Key，避免每次按键都写盘"""
        if not hasattr(self, 'root'):
            return
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(800, self._save_api_keys_impl)

    def _save_api_keys_impl(self):
        self._save_job = None
        try:
            config = {}
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            config["deepl_key"] = self.deepl_key.get().strip()
            atomic_write_json(CONFIG_PATH, config)
        except Exception as e:
            self.log_message(f" 保存配置失败: {e}")
    
    
    def start_translation(self):
        if not self.validate_inputs():
            return
        if not self.check_internet_connection():
            messagebox.showerror("网络错误", " 无法连接网络，请检查您的网络连接后重试。")
            self.log_message(" 网络中断，翻译终止")
            self.status_var.set("网络中断，已取消")
            self.progress.stop()
            self.start_button.config(state='normal')
            return
        self.translator = self._create_translator()
        if not self.translator.deepl_translator:
            messagebox.showerror("错误", "DeepL 初始化失败，请检查 API Key 是否有效")
            return
        # 禁用开始按钮
        self.start_button.config(state='disabled')
        self.progress.start()
        self.status_var.set("翻译中...")
        
        # 构建输出文件路径（扩展名与输入一致）
        from backend.cad import analyze_source, output_path_for

        try:
            meta = analyze_source(self.input_file.get())
            output_file = output_path_for(
                meta, self.output_dir.get(), self.output_name.get().strip()
            )
        except ValueError:
            output_file = os.path.join(
                self.output_dir.get(),
                self.output_name.get().strip() + ".dxf",
            )
        
        # 在新线程中执行翻译
        def translation_thread():
            try:
                self.translator.translate_cad_file(
                    self.input_file.get(),
                    output_file,
                    self.translation_mode.get(),
                    self.translate_blocks.get(),
                )
                self.root.after(0, self.translation_complete, True, "翻译完成！")
            except Exception:
                import traceback
                err = traceback.format_exc()
                error_msg = f"翻译失败: {self.translator.cleaner.safe_utf8(err)}"
                self.log_message(error_msg)
                self.root.after(0, self.translation_complete, False, error_msg)

        thread = threading.Thread(target=translation_thread, daemon=True)
        thread.start()
    
    def translation_complete(self, success, message):
        self.progress.stop()
        self.start_button.config(state='normal')
        
        if success:
            self.status_var.set("完成")
            messagebox.showinfo("成功", message)
            self.log_message("=" * 50)
        else:
            self.status_var.set("失败")
            safe = self.safe_text_for_tkinter(str(message))
            messagebox.showerror("错误", safe)
            self.log_message(f"ERROR: {safe}")

    def check_internet_connection(self, url='http://www.baidu.com', timeout=3):
        try:
            urllib.request.urlopen(url, timeout=timeout)
            return True
        except Exception:
            return False

    def run(self):
        self.root.mainloop()
