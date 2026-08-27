"""No-network checks for the local language asset precedence and storage."""

import tempfile
import unittest
from pathlib import Path

from backend.language_assets import LanguageAssets
from backend.translator import CADChineseTranslator


class LanguageAssetsTests(unittest.TestCase):
    def test_project_terms_outrank_global_and_protect_manual_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = LanguageAssets(Path(tmp) / "assets.sqlite3")
            project_path = Path(tmp) / "project.hcterms.json"
            assets.create_project(project_path, "Test project")

            assets.upsert_term("global", "fr_to_zh", "service label", "全局译文")
            assets.upsert_term("project", "fr_to_zh", "service label", "项目译文", project_path=str(project_path))
            self.assertEqual(
                assets.lookup_term("SERVICE LABEL", "fr_to_zh", project_path=str(project_path)),
                "项目译文",
            )
            self.assertEqual(assets.lookup_term("SERVICE LABEL", "fr_to_zh"), "全局译文")

            assets.record_memory("memory label", "接口译文", "fr_to_zh", "ELEC", "deepl")
            self.assertEqual(assets.lookup_memory("MEMORY LABEL", "fr_to_zh", "ELEC"), "接口译文")
            assets.upsert_memory("fr_to_zh", "memory label", "人工译文", "ELEC")
            assets.record_memory("memory label", "接口新译文", "fr_to_zh", "ELEC", "azure")
            self.assertEqual(assets.lookup_memory("memory label", "fr_to_zh", "ELEC"), "人工译文")

            translator = CADChineseTranslator(log_callback=lambda *_args, **_kwargs: None)
            translator.language_assets = assets
            translator.configure_language_assets(str(project_path))
            self.assertEqual(translator.translate_text("service label", "fr_to_zh"), "项目译文")
            self.assertEqual(translator.translate_text("memory label", "fr_to_zh", "ELEC"), "人工译文")

            assets.record_usage("azure", 123)
            usage = assets.usage()
            self.assertEqual(usage["azure"]["characters"], 123)
            self.assertEqual(usage["azure"]["remaining"], 2_000_000 - 123)
            self.assertEqual(len(assets.list_terms(str(project_path))), 2)
            self.assertTrue(project_path.is_file())


if __name__ == "__main__":
    unittest.main()
