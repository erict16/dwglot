import json
import unittest
import tempfile
import ezdxf
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from ezdxf.lldxf.types import DXFTag

from backend.providers.azure import AzureFreeQuotaExceededError, AzureTranslator, AzureTranslatorError
from backend.language_assets import LanguageAssets
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


if __name__ == "__main__":
    unittest.main()
