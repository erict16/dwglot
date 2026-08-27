import json
import unittest
import tempfile
import ezdxf
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from ezdxf.lldxf.types import DXFTag
from fastapi.testclient import TestClient

from backend.providers.azure import AzureFreeQuotaExceededError, AzureTranslator, AzureTranslatorError
from backend.providers.base import TranslationProviderError
from backend.providers.deepl_provider import DeepLProvider
from backend.providers.ollama import ollama_reachable
from backend.providers.openai_compat import OpenAICompatProvider
from backend.language_assets import LanguageAssets
from backend.drawings import translate_rows
from backend import translator
from backend.translator import CADChineseTranslator, decode_oda_mbcs_escapes, output_prefix
from backend.api import BatchStartBody, TranslateBody, app, builtin_terms, default_output_name, service, start_batch


class TranslationModeTests(unittest.TestCase):
    def setUp(self):
        self.assets_tmp = tempfile.TemporaryDirectory()
        self.assets = LanguageAssets(f"{self.assets_tmp.name}/assets.sqlite3")
        self.assets_patch = patch("backend.translator.LanguageAssets", return_value=self.assets)
        self.assets_patch.start()

    def tearDown(self):
        self.assets_patch.stop()
        self.assets_tmp.cleanup()

    def test_azure_uses_v3_request_and_language_codes(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'[{"translations":[{"text":"cement structure"}]}]'

        with patch("backend.providers.azure.urllib.request.urlopen", return_value=Response()) as open_url:
            self.assertEqual(AzureTranslator("key", "eastus").translate_text("水泥结构", "zh-cn", "en-us"), "cement structure")
        request = open_url.call_args.args[0]
        self.assertIn("from=zh-Hans", request.full_url)
        self.assertIn("to=en", request.full_url)
        self.assertEqual(request.headers["Ocp-apim-subscription-region"], "eastus")

    def test_azure_f0_quota_error_is_not_retryable(self):
        error = HTTPError("https://example.test", 403, "Forbidden", None, BytesIO(b'{"error":{"code":403001,"message":"quota exceeded"}}'))
        with patch("backend.providers.azure.urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(AzureFreeQuotaExceededError, "免费额度已用尽") as raised:
                AzureTranslator("key").translate_text("文本", "zh-cn", "fr")
        error.close()
        self.assertFalse(raised.exception.retryable)

    def test_azure_invalid_request_and_key_are_not_retryable(self):
        for status in (400, 401, 403):
            error = HTTPError("https://example.test", status, "Request failed", None, BytesIO(b'{"error":{"code":400000,"message":"invalid"}}'))
            with patch("backend.providers.azure.urllib.request.urlopen", side_effect=error):
                with self.assertRaises(AzureTranslatorError) as raised:
                    AzureTranslator("key").translate_text("文本", "zh-cn", "fr")
            error.close()
            self.assertFalse(raised.exception.retryable)

    def test_deepl_language_pairs_and_output_prefixes(self):
        translator = CADChineseTranslator()
        expected = {
            "zh_to_fr": ("zh-cn", "fr", "fr"),
            "fr_to_zh": ("fr", "zh-cn", "zh"),
            "zh_to_en": ("zh-cn", "en-us", "en"),
            "en_to_zh": ("en", "zh-cn", "zh"),
        }
        for mode, (source, target, prefix) in expected.items():
            self.assertEqual((translator.language_configs[mode]["source"], translator.language_configs[mode]["target"]), (source, target))
            self.assertEqual(output_prefix(mode), prefix)
            self.assertTrue(default_output_name(mode, "drawing")["name"].startswith(f"{prefix}_drawing_"))

    def test_chinese_to_english_keeps_deepl_english_variant(self):
        calls = []

        class Translator:
            def translate_text(self, text, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(text="cement structure")

        translator = CADChineseTranslator(log_callback=lambda *args, **kwargs: None)
        translator.deepl_translator = Translator()
        self.assertEqual(translator.translate_text("水泥结构", "zh_to_en"), "cement structure")
        self.assertEqual(calls, [{"source_lang": "ZH", "target_lang": "EN-US"}])

    def test_glossary_bypasses_deepl_for_exact_cad_labels(self):
        class Translator:
            def translate_text(self, *args, **kwargs):
                raise AssertionError("exact glossary entries must not call DeepL")

        translator = CADChineseTranslator(log_callback=lambda *args, **kwargs: None)
        translator.deepl_translator = Translator()
        self.assertEqual(translator.translate_text("天花", "zh_to_fr"), "plafond")
        self.assertEqual(translator.translate_text("PLAFOND", "fr_to_zh"), "天花")
        self.assertEqual(translator.translate_text("剪力墙", "zh_to_fr"), "voile de contreventement")
        self.assertEqual(translator.translate_text("VOILE DE CONTREVENTEMENT", "fr_to_zh"), "剪力墙")
        self.assertEqual(translator.translate_text("LOCAL INFORMATIQUE", "fr_to_zh"), "计算机房")
        self.assertEqual(translator.translate_text("天花图", "zh_to_en"), "reflected ceiling plan")
        self.assertEqual(translator.translate_text("CABLE TRAY", "en_to_zh"), "桥架")
        self.assertEqual(translator.translate_text("OUVERTURE", "fr_to_zh"), "开洞")
        self.assertEqual(translator.translate_text("ALIMENTATION", "fr_to_zh", "ELEC-CFO"), "供电")
        self.assertEqual(translator.translate_text("ALIMENTATION", "fr_to_zh", "PLOMB-EAU"), "供水")
        self.assertEqual(translator.translate_text("alimentation en eau", "fr_to_zh"), "供水")
        self.assertEqual(translator.translate_text("alimentation de secours", "fr_to_zh"), "应急电源")
        self.assertEqual(translator.translate_text("trémie d'escalier", "fr_to_zh"), "楼梯洞口")
        self.assertEqual(translator.translate_text("墙体开洞", "zh_to_fr"), "ouverture de mur")
        self.assertEqual(translator.translate_text("楼板开洞", "zh_to_en"), "floor opening")
        self.assertEqual(translator.translate_text("WALL OPENING", "en_to_zh"), "墙体开洞")
        self.assertEqual(translator.translate_text("POWER SUPPLY", "en_to_zh"), "供电")
        self.assertEqual(translator.translate_text("安装高度", "zh_to_en"), "installation height")
        self.assertEqual(translator.translate_text("墙体拆除图", "zh_to_en"), "wall demolition plan")
        self.assertEqual(translator.translate_text("材料表", "zh_to_en"), "bill of materials")
        self.assertEqual(translator.translate_text("INSTALLATION HEIGHT", "en_to_zh"), "安装高度")

    def test_visible_anonymous_table_block_is_scanned_without_full_block_option(self):
        doc = ezdxf.new()
        table_block = doc.blocks.new_anonymous_block("T")
        table_block.add_text("墙体拆除图")
        cad_translator = CADChineseTranslator(log_callback=lambda *args, **kwargs: None)

        items = cad_translator.extract_text_entities(doc, "zh_to_fr", include_blocks=False)

        self.assertTrue(any(item["original_text"] == "墙体拆除图" for item in items))

    def test_dimension_override_text_is_collected_but_placeholder_is_not(self):
        class Dimension:
            def __init__(self, text):
                self.dxf = SimpleNamespace(text=text, layer="0")

            def dxftype(self):
                return "DIMENSION"

        cad_translator = CADChineseTranslator(log_callback=lambda *args, **kwargs: None)
        layout = SimpleNamespace(name="Model")
        self.assertEqual(
            [item["original_text"] for item in cad_translator.collect_entity_text_items(Dimension("安装高度"), layout)],
            ["安装高度"],
        )
        self.assertEqual(cad_translator.collect_entity_text_items(Dimension("<>"), layout), [])

    def test_acad_table_source_text_is_collected_and_written(self):
        class XTags:
            def __init__(self):
                self.table_tags = [DXFTag(91, 1), DXFTag(302, "墙体拆除图"), DXFTag(302, "Layout 1")]

            def get_subclass(self, name):
                if name != "AcDbTable":
                    raise KeyError(name)
                return self.table_tags

        class Table:
            def __init__(self):
                self.dxf = SimpleNamespace(layer="0")
                self.xtags = XTags()

            def dxftype(self):
                return "ACAD_TABLE"

        cad_translator = CADChineseTranslator(log_callback=lambda *args, **kwargs: None)
        table = Table()
        layout = SimpleNamespace(name="Layout1")
        items = cad_translator.collect_entity_text_items(table, layout)
        self.assertEqual([item["original_text"] for item in items], ["墙体拆除图", "Layout 1"])
        cad_translator.write_back_translation(table, "plan de demolition des murs", items[0]["field"])
        self.assertEqual(table.xtags.table_tags[1].value, "plan de demolition des murs")

    def test_oda_legacy_mbcs_text_is_decoded_before_translation_filtering(self):
        self.assertEqual(decode_oda_mbcs_escapes(r"\M+5C6BD\M+5C3E6"), "平面")
        self.assertEqual(decode_oda_mbcs_escapes(r"r\M+5A8A6serv\M+5A8A6"), "réservé")

        cad_translator = CADChineseTranslator(log_callback=lambda *args, **kwargs: None)
        entity = SimpleNamespace(dxf=SimpleNamespace(text=r"\M+5C6BD\M+5C3E6", layer="0"), dxftype=lambda: "TEXT")
        items = cad_translator.collect_entity_text_items(entity, SimpleNamespace(name="Layout1"))
        self.assertEqual(items[0]["original_text"], "平面")

    def test_builtin_yaml_glossaries_are_exposed_read_only(self):
        terms = builtin_terms()
        self.assertTrue(any(term["mode"] == "zh_to_fr" and term["source"] == "天花" for term in terms))
        self.assertEqual({term["scope"] for term in terms}, {"builtin"})

    def test_provider_failure_is_not_reported_as_a_translation(self):
        class Translator:
            def translate_text(self, *args, **kwargs):
                raise OSError("network unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            translator = CADChineseTranslator(log_callback=lambda *args, **kwargs: None)
            translator.language_assets = LanguageAssets(f"{tmp}/assets.sqlite3")
            translator.deepl_translator = Translator()
            with self.assertRaisesRegex(RuntimeError, "DeepL 翻译失败"):
                translator.translate_text("水泥结构", "zh_to_en")

    def test_azure_f0_quota_error_reaches_the_queue(self):
        translator = CADChineseTranslator(log_callback=lambda *args, **kwargs: None)
        translator.configure_azure("key")
        with patch.object(translator.azure_translator, "translate_text", side_effect=AzureFreeQuotaExceededError("Azure Translator F0 免费额度已用尽")):
            with self.assertRaises(AzureFreeQuotaExceededError):
                translator.translate_text("水泥结构", "zh_to_en")

    def test_single_file_api_rejects_unknown_translation_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            drawing = f"{tmp}/drawing.dxf"
            open(drawing, "w", encoding="utf-8").close()
            body = TranslateBody(
                input_file=drawing, output_dir=tmp, output_name="output",
                translation_mode="unsupported", deepl_key="key",
            )
            self.assertEqual(service.validate(body), "不支持的翻译方向")

    def test_write_back_failure_is_not_silenced(self):
        class UnsupportedEntity:
            def dxftype(self):
                return "LINE"

        translator = CADChineseTranslator(log_callback=lambda *args, **kwargs: None)
        with self.assertRaises(ValueError):
            translator.write_back_translation(UnsupportedEntity(), "translated")

    def test_local_api_has_no_permissive_cors_middleware(self):
        self.assertFalse(any(middleware.cls.__name__ == "CORSMiddleware" for middleware in app.user_middleware))

    def test_batch_api_rejects_unknown_output_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(Exception, "不支持的输出版本"):
                start_batch(BatchStartBody(output_dir=tmp, output_version="ACAD9999", deepl_key="key"))

    def test_legacy_save_preserves_azure_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = f"{tmp}/config.json"
            with open(config_path, "w", encoding="utf-8") as stream:
                json.dump({"azure_key": "azure", "azure_region": "eastus", "provider": "azure"}, stream)
            legacy = object.__new__(translator.CADTranslatorGUI)
            legacy._save_job = None
            legacy.deepl_key = SimpleNamespace(get=lambda: "deepl")
            legacy.log_message = lambda *_: None
            with patch("backend.translator.CONFIG_PATH", config_path):
                legacy._save_api_keys_impl()
            with open(config_path, encoding="utf-8") as stream:
                config = json.load(stream)
            self.assertEqual(config["deepl_key"], "deepl")
            self.assertEqual(config["azure_key"], "azure")


EMPTY_CONFIG = {
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

GLOSSARY_ROWS = [
    {"source": "天花", "type": "TEXT", "layer": "0"},
    {"source": "这句不在术语表xyz", "type": "TEXT", "layer": "0"},
]


class EngineAndGlossaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = TestClient(app)
        self.config_patch = patch.object(service, "load_config", return_value=dict(EMPTY_CONFIG))
        self.config_patch.start()

    def tearDown(self):
        self.config_patch.stop()
        self.tmp.cleanup()

    def _assert_calm(self, response, status, needle):
        self.assertEqual(response.status_code, status, response.text)
        detail = response.json()["detail"]
        self.assertIn(needle, detail)
        self.assertNotIn("Traceback", response.text)
        self.assertNotIn("Errno", response.text)

    def test_glossary_only_translate_without_key(self):
        with patch("urllib.request.urlopen") as open_url, patch("deepl.Translator") as deepl_cls:
            open_url.side_effect = AssertionError("empty-key translate must not call the network")
            deepl_cls.side_effect = AssertionError("empty-key translate must not construct DeepL")
            result = translate_rows(GLOSSARY_ROWS, mode="zh_to_en", provider="deepl", engine={})
        by_source = {item["source"]: item for item in result["items"]}
        self.assertEqual(by_source["天花"]["target"], "ceiling")
        self.assertEqual(by_source["天花"]["via"], "glossary")
        self.assertEqual(by_source["这句不在术语表xyz"]["target"], "")
        self.assertEqual(by_source["这句不在术语表xyz"]["via"], "needs_engine")
        self.assertGreaterEqual(result["glossary"], 1)
        self.assertEqual(result["mt"], 0)
        self.assertGreaterEqual(result["skipped"], 1)
        self.assertFalse(result["has_engine"])

    def test_empty_cloud_keys_skip_mt_and_do_not_call_network(self):
        with patch("urllib.request.urlopen") as open_url, patch("deepl.Translator") as deepl_cls:
            open_url.side_effect = AssertionError("empty key must not call the network")
            deepl_cls.side_effect = AssertionError("empty DeepL key must not construct a client")
            deepl = self.client.post(
                "/api/drawings/translate",
                json={"items": GLOSSARY_ROWS, "translation_mode": "zh_to_en", "provider": "deepl", "deepl_key": ""},
            )
            azure = self.client.post(
                "/api/drawings/translate",
                json={"items": GLOSSARY_ROWS, "translation_mode": "zh_to_en", "provider": "azure", "azure_key": ""},
            )
        self.assertEqual(deepl.status_code, 200, deepl.text)
        self.assertEqual(azure.status_code, 200, azure.text)
        self.assertNotIn("Traceback", deepl.text)
        self.assertNotIn("Traceback", azure.text)
        for payload in (deepl.json(), azure.json()):
            self.assertFalse(payload["has_engine"])
            self.assertEqual(payload["mt"], 0)
            self.assertTrue(any(item["source"] == "天花" and item["target"] == "ceiling" for item in payload["items"]))
            self.assertTrue(any(item["via"] == "needs_engine" for item in payload["items"]))

    def test_custom_empty_url_is_not_ready_and_does_not_default_to_deepseek(self):
        with patch("backend.providers.openai_compat.urllib.request.urlopen") as open_url:
            with self.assertRaisesRegex(TranslationProviderError, "自定义接口地址") as raised:
                OpenAICompatProvider("sk-test", "")
            self.assertFalse(raised.exception.retryable)
            open_url.assert_not_called()
        engine = {"openai_key": "sk-test", "openai_base": ""}
        self.assertFalse(service._engine_ready("openai", engine))
        self.assertIn("接口地址", service._engine_missing_message("openai", engine))
        with patch("urllib.request.urlopen") as open_url:
            open_url.side_effect = AssertionError("empty custom URL must not call the network")
            result = translate_rows(GLOSSARY_ROWS, mode="zh_to_en", provider="openai", engine=engine)
        self.assertFalse(result["has_engine"])
        self.assertEqual(result["mt"], 0)
        self.assertTrue(any(item["target"] == "ceiling" for item in result["items"]))

    def test_ollama_down_is_fast_and_skips_mt(self):
        with patch("backend.providers.ollama.urllib.request.urlopen", side_effect=OSError("down")) as open_url:
            self.assertFalse(ollama_reachable("http://127.0.0.1:9"))
            self.assertLessEqual(open_url.call_args.kwargs.get("timeout", 99), 2.0)
        with patch("backend.providers.ollama.ollama_reachable", return_value=False):
            self.assertFalse(service._engine_ready("ollama", {}))
            self.assertIn("Ollama", service._engine_missing_message("ollama", {}))
            result = translate_rows(GLOSSARY_ROWS, mode="zh_to_en", provider="ollama", engine={})
        self.assertFalse(result["has_engine"])
        self.assertEqual(result["mt"], 0)
        self.assertTrue(any(item["target"] == "ceiling" for item in result["items"]))

    def test_empty_azure_and_deepl_providers_raise_without_network(self):
        with patch("urllib.request.urlopen") as open_url:
            with self.assertRaisesRegex(AzureTranslatorError, "Azure Translator Key") as azure:
                AzureTranslator("")
            with self.assertRaisesRegex(TranslationProviderError, "DeepL API Key") as deepl:
                DeepLProvider("")
            self.assertFalse(azure.exception.retryable)
            self.assertFalse(deepl.exception.retryable)
            open_url.assert_not_called()

    def test_batch_start_rejects_empty_engines(self):
        cases = [
            ({"provider": "deepl", "deepl_key": "", "output_dir": self.tmp.name}, "DeepL"),
            ({"provider": "azure", "azure_key": "", "output_dir": self.tmp.name}, "Azure"),
            ({"provider": "openai", "openai_key": "sk", "openai_base": "", "output_dir": self.tmp.name}, "接口地址"),
            ({"provider": "openai", "openai_key": "", "openai_base": "https://example.test/v1", "output_dir": self.tmp.name}, "API Key"),
        ]
        for body, needle in cases:
            response = self.client.post("/api/batch/start", json=body)
            self._assert_calm(response, 400, needle)
        with patch("backend.providers.ollama.ollama_reachable", return_value=False):
            ollama = self.client.post("/api/batch/start", json={"provider": "ollama", "output_dir": self.tmp.name})
        self._assert_calm(ollama, 400, "Ollama")

    def test_glossary_missing_empty_and_bad_encoding_are_calm(self):
        missing = self.client.post(
            "/api/language-assets/project",
            json={"path": str(Path(self.tmp.name) / "gone.hcterms.json")},
        )
        self._assert_calm(missing, 400, "术语表不存在")

        empty_file = Path(self.tmp.name) / "empty.hcterms.json"
        empty_file.write_bytes(b"   \n")
        empty = self.client.post("/api/language-assets/project", json={"path": str(empty_file)})
        self._assert_calm(empty, 400, "术语表是空的")

        blank_terms = Path(self.tmp.name) / "blank.hcterms.json"
        blank_terms.write_text('{"format":"honsen-cad-terms/v1","name":"blank","terms":[]}', encoding="utf-8")
        blank = self.client.post("/api/language-assets/project", json={"path": str(blank_terms)})
        self._assert_calm(blank, 400, "术语表是空的")

        gbk = Path(self.tmp.name) / "gbk.hcterms.json"
        gbk.write_bytes('{"name":"测","terms":[{"source":"天花","target":"ceiling"}]}'.encode("gbk"))
        encoding = self.client.post("/api/language-assets/project", json={"path": str(gbk)})
        self._assert_calm(encoding, 400, "UTF-8")

        broken = Path(self.tmp.name) / "broken.hcterms.json"
        broken.write_text("{not json", encoding="utf-8")
        invalid = self.client.post("/api/language-assets/project", json={"path": str(broken)})
        self._assert_calm(invalid, 400, "术语表")
        self.assertNotIn("JSONDecodeError", invalid.text)

        imported = self.client.post("/api/language-assets/import", json={"mode": "zh_to_en", "csv": "\n# comment\n"})
        self._assert_calm(imported, 400, "术语表是空的")

    def test_frontend_engine_and_glossary_hints(self):
        text = Path(__file__).resolve().parents[1].joinpath("frontend", "src", "App.jsx").read_text(encoding="utf-8")
        self.assertIn("请先启动 Ollama。", text)
        self.assertIn("请配置自定义接口地址和 Key。", text)
        self.assertIn("剩下的要填云引擎 Key，或手填译文。", text)
        self.assertIn('setStatus("术语表是空的")', text)
        self.assertIn("术语表读不出来。", text)


if __name__ == "__main__":
    unittest.main()
