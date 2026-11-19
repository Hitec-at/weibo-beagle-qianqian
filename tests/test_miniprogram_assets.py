from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MINI = ROOT / "miniprogram"


class MiniProgramAssetTest(unittest.TestCase):
    def test_all_declared_pages_have_native_files(self) -> None:
        app = json.loads((MINI / "app.json").read_text(encoding="utf-8"))
        for page in app["pages"]:
            for suffix in (".js", ".json", ".wxml", ".wxss"):
                path = MINI / (page + suffix)
                if suffix == ".wxss":
                    continue
                self.assertTrue(path.is_file(), str(path))

    def test_api_client_uses_cloud_container_only(self) -> None:
        source = (MINI / "utils" / "api.js").read_text(encoding="utf-8")

        self.assertIn("callContainer", source)
        self.assertIn("X-WX-SERVICE", source)
        self.assertNotIn("wx.request", source)
        self.assertNotIn("XMLHttpRequest", source)

    def test_local_project_uses_native_components(self) -> None:
        app = json.loads((MINI / "app.json").read_text(encoding="utf-8"))
        project = json.loads(
            (MINI / "project.config.json").read_text(encoding="utf-8")
        )

        self.assertEqual(project["compileType"], "miniprogram")
        self.assertEqual(project["appid"], "touristappid")
        self.assertNotIn("usingComponents", app)

    def test_pages_do_not_reference_web_dom(self) -> None:
        forbidden = ("document.", "window.", "localStorage")
        for path in (MINI / "pages").rglob("*.js"):
            source = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, source, f"{path}: {token}")


if __name__ == "__main__":
    unittest.main()
