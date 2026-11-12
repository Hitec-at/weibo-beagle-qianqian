from __future__ import annotations

import unittest
from pathlib import Path


DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"
APP_MODULES = {"config.py", "filters.py", "memes.py", "store.py"}


class DockerRuntimeDependencyTest(unittest.TestCase):
    def test_app_modules_are_copied_into_image(self) -> None:
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        for module in APP_MODULES:
            with self.subTest(module=module):
                self.assertIn(module, dockerfile)


if __name__ == "__main__":
    unittest.main()
