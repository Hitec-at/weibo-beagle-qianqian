from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import baseline
import store


class BaselineBuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = store.DB_PATH
        self.old_ledger_path = store.LEDGER_DB_PATH
        store.DB_PATH = Path(self.temp_dir.name) / "test.db"
        store.LEDGER_DB_PATH = Path(self.temp_dir.name) / "ledger.db"
        store.init_db()
        conn = store.connect()
        try:
            for post_id, text in (
                ("1", "今日体重管理记录"),
                ("2", "刚吃了罐头又饿了"),
                ("3", "人工审定素材"),
                ("4", "完全无关内容"),
            ):
                conn.execute(
                    "INSERT INTO posts (id, text_plain) VALUES (?, ?)",
                    (post_id, text),
                )
            conn.commit()
        finally:
            conn.close()
        self.seed = Path(self.temp_dir.name) / "memes.toml"
        self.seed.write_text(
            """
[[memes]]
slug = "test"
name = "测试罪名"
match_mode = "mixed"
auto_publish_terms = ["体重管理"]
candidate_terms = ["饿"]
curated_post_ids = ["3"]
""",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        store.DB_PATH = self.old_db_path
        store.LEDGER_DB_PATH = self.old_ledger_path
        self.temp_dir.cleanup()

    def test_mixed_mode_public_and_candidate_are_separated(self) -> None:
        result = baseline.build("baseline-test", seed_path=self.seed)

        self.assertEqual(
            {row["id"] for row in store.query_meme_posts("test")},
            {"1", "3"},
        )
        rows = store.baseline_candidate_stats("baseline-test")
        by_status = {
            (row["status"], row["source"]): row["post_count"] for row in rows
        }
        self.assertEqual(by_status[("published", "local")], 1)
        self.assertEqual(by_status[("candidate", "local")], 1)
        self.assertEqual(by_status[("published", "curated")], 1)
        self.assertEqual(result["version"], "baseline-test")

    def test_baseline_version_is_immutable(self) -> None:
        baseline.build("baseline-test", seed_path=self.seed)
        with self.assertRaisesRegex(ValueError, "baseline 已存在"):
            baseline.build("baseline-test", seed_path=self.seed)


class SearchParserTest(unittest.TestCase):
    def test_nested_search_cards_are_flattened(self) -> None:
        from fetch_weibo import search_mblogs

        data = {
            "data": {
                "cards": [
                    {"mblog": {"id": "1"}},
                    {
                        "card_group": [
                            {"mblog": {"id": "2"}},
                            {"mblog": {"id": "1"}},
                        ]
                    },
                ]
            }
        }
        self.assertEqual(
            [mblog["id"] for mblog in search_mblogs(data)],
            ["1", "2"],
        )


if __name__ == "__main__":
    unittest.main()
