from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import baseline
import classify
import memes
import store
from app.main import meme_detail
from fastapi import HTTPException
from starlette.requests import Request


class RuleLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = store.DB_PATH
        self.old_ledger_path = store.LEDGER_DB_PATH
        store.DB_PATH = Path(self.temp_dir.name) / "test.db"
        store.LEDGER_DB_PATH = Path(self.temp_dir.name) / "ledger.db"
        store.init_db()
        conn = store.connect()
        try:
            conn.executemany(
                "INSERT INTO posts (id, text_plain) VALUES (?, ?)",
                [
                    ("1", "高精度词一"),
                    ("2", "高精度词二"),
                    ("3", "候选词"),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        store.DB_PATH = self.old_db_path
        store.LEDGER_DB_PATH = self.old_ledger_path
        self.temp_dir.cleanup()

    def _seed(self, body: str, name: str = "memes.toml") -> Path:
        path = Path(self.temp_dir.name) / name
        path.write_text(body, encoding="utf-8")
        return path

    @staticmethod
    def _rule(
        *,
        slug: str = "test",
        name: str = "测试规则",
        status: str = "active",
        auto_terms: str = '"高精度词一", "高精度词二"',
        candidate_terms: str = '"候选词"',
        exclude: str = '"排除词"',
        queries: str = '"测试查询1", "测试查询2"',
    ) -> str:
        return f"""
[[memes]]
slug = "{slug}"
name = "{name}"
status = "{status}"
kind = "behavior"
match_mode = "mixed"
auto_publish_terms = [{auto_terms}]
candidate_terms = [{candidate_terms}]
exclude = [{exclude}]
search_queries = [{queries}]
origin = "测试来源"
explanation = "测试解释"
boundary = "测试边界"
"""

    def test_preflight_is_read_only_and_reports_package_gaps(self) -> None:
        path = self._seed(
            """
[[memes]]
slug = "single"
name = "单词语义"
match_mode = "mixed"
auto_publish_terms = ["宽泛"]
candidate_terms = ["宽泛"]
search_queries = ["宽泛", "宽泛"]
curated_post_ids = ["999"]
"""
        )

        result = memes.preflight(path)
        codes = {item["code"] for item in result["errors"]}
        warning_codes = {item["code"] for item in result["warnings"]}

        self.assertFalse(result["ok"])
        self.assertIn("auto_candidate_overlap", codes)
        self.assertIn("missing_curated_post", codes)
        self.assertIn("missing_boundary", warning_codes)
        self.assertIn("missing_negative_examples", warning_codes)
        self.assertIn("single_auto_publish_term", warning_codes)
        self.assertIn("duplicate_value", warning_codes)
        self.assertEqual(store.list_memes(status=None), [])

    def test_paused_rule_preserves_links_but_leaves_public_flow(self) -> None:
        active = self._seed(self._rule())
        memes.sync(active)
        before = {
            row["id"] for row in store.query_meme_posts("test")
        }
        self.assertEqual(before, {"1", "2"})

        paused = self._seed(
            self._rule(status="paused"),
            name="paused.toml",
        )
        memes.sync(paused)

        self.assertEqual(store.get_meme("test")["status"], "paused")
        self.assertEqual(
            {row["id"] for row in store.query_meme_posts("test")},
            {"1", "2"},
        )
        self.assertEqual(store.list_memes(), [])
        categories = classify.classify_post(store.get_post("1"))
        self.assertNotIn("memes", categories)
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "scheme": "https",
                "server": ("testserver", 443),
                "path": "/meme/test",
                "query_string": b"",
                "headers": [],
            }
        )
        with self.assertRaises(HTTPException) as raised:
            meme_detail(request, "test", page=1)
        self.assertEqual(raised.exception.status_code, 404)

    def test_missing_seed_entry_is_deprecated_without_deleting_history(self) -> None:
        two_rules = self._seed(
            self._rule()
            + self._rule(slug="old", name="旧规则", auto_terms='"高精度词一"')
        )
        memes.sync(two_rules)
        self.assertEqual(
            {row["id"] for row in store.query_meme_posts("old")},
            {"1"},
        )

        one_rule = self._seed(self._rule(), name="one.toml")
        report = memes.sync_with_report(one_rule)

        self.assertEqual(
            {item["slug"] for item in report["deprecated_missing"]},
            {"old"},
        )
        self.assertEqual(store.get_meme("old")["status"], "deprecated")
        self.assertEqual(
            {row["id"] for row in store.query_meme_posts("old")},
            {"1"},
        )
        self.assertNotIn("old", {row["slug"] for row in store.list_memes()})

    def test_baseline_ignores_paused_rules(self) -> None:
        path = self._seed(self._rule(status="paused"))

        result = baseline.build("paused-baseline", seed_path=path)

        self.assertEqual(result["memes"], [])
        self.assertEqual(
            store.baseline_candidate_stats("paused-baseline"),
            [],
        )

    def test_mixed_mode_can_remain_candidate_only(self) -> None:
        path = self._seed(
            """
[[memes]]
slug = "candidate-only"
name = "候选规则"
match_mode = "mixed"
auto_publish_terms = []
candidate_terms = ["候选词"]
search_queries = ["候选词"]
exclude = ["排除"]
origin = "测试"
explanation = "测试"
boundary = "测试"
"""
        )

        result = memes.preflight(path)
        memes.sync(path)
        baseline.build("candidate-only-baseline", seed_path=path)
        stats = store.baseline_candidate_stats("candidate-only-baseline")

        self.assertTrue(result["ok"])
        self.assertIn(
            "candidate_only_rule",
            {item["code"] for item in result["warnings"]},
        )
        self.assertEqual(
            {(row["status"], row["post_count"]) for row in stats},
            {("candidate", 1)},
        )


if __name__ == "__main__":
    unittest.main()
