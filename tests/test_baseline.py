from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import baseline
import config
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
                ("4", "体重管理 排除"),
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
search_queries = ["测试查询"]
exclude = ["排除"]
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

    def test_probe_evidence_builds_search_layer_and_review(self) -> None:
        evidence_path = Path(self.temp_dir.name) / "probe.json"

        def fetcher(query: str, page: int) -> list[dict]:
            self.assertEqual((query, page), ("测试查询", 1))
            return [
                {
                    "id": "88",
                    "user": {"id": "not-target"},
                    "text_raw": "无关账号",
                },
                {
                    "id": "1",
                    "mid": "9001",
                    "user": {
                        "id": config.TARGET_UID,
                        "screen_name": "比格钱钱",
                    },
                    "created_at": "Wed Nov 11 10:00:00 +0800 2026",
                    "text_raw": "今日体重管理记录",
                },
                {
                    "id": "999",
                    "mid": "9002",
                    "user": {
                        "id": config.TARGET_UID,
                        "screen_name": "比格钱钱",
                    },
                    "created_at": "Wed Nov 11 10:01:00 +0800 2026",
                    "text_raw": "尚未入库的搜索命中",
                },
                {
                    "id": "1",
                    "mid": "9001",
                    "user": {
                        "id": config.TARGET_UID,
                        "screen_name": "比格钱钱",
                    },
                    "text_raw": "重复结果",
                },
                {
                    "id": "4",
                    "mid": "9004",
                    "user": {
                        "id": config.TARGET_UID,
                        "screen_name": "比格钱钱",
                    },
                    "text_raw": "体重管理 排除",
                },
            ]

        evidence = baseline.probe_search(
            label="probe-test",
            output_path=evidence_path,
            seed_path=self.seed,
            fetcher=fetcher,
        )

        self.assertTrue(evidence["complete"])
        self.assertEqual(evidence["summary"]["request_count"], 1)
        self.assertEqual(evidence["summary"]["corpus_present_count"], 2)
        self.assertEqual(evidence["summary"]["corpus_missing_count"], 1)
        missing = evidence["summary"]["missing_posts"][0]
        self.assertEqual(missing["post_id"], "999")
        self.assertEqual(missing["search_ranks"], [3])
        self.assertIn("screen_name", missing["available_fields"])

        result = baseline.build(
            "baseline-search-test",
            seed_path=self.seed,
            search_evidence=evidence_path,
        )
        self.assertEqual(result["missing_search_posts"][0]["post_id"], "999")
        rows = store.baseline_candidate_stats("baseline-search-test")
        search_rows = {
            (row["status"], row["source"]): row["post_count"]
            for row in rows
        }
        self.assertEqual(search_rows[("published", "weibo_search")], 1)
        self.assertEqual(search_rows[("excluded", "weibo_search")], 1)
        query_rows = baseline.report("baseline-search-test")["queries"]
        query_row = next(
            row
            for row in query_rows
            if row["query"] == "测试查询" and row["status"] == "published"
        )
        self.assertEqual(query_row["best_rank"], 2)

        review = baseline.export_review(
            "baseline-search-test",
            Path(self.temp_dir.name) / "review",
        )
        self.assertGreater(review["review_row_count"], 0)
        self.assertTrue((Path(review["output"]) / "review.md").exists())

    def test_incomplete_probe_cannot_build_baseline(self) -> None:
        evidence_path = Path(self.temp_dir.name) / "incomplete.json"

        def fetcher(query: str, page: int) -> list[dict]:
            raise RuntimeError("stop")

        with self.assertRaisesRegex(RuntimeError, "stop"):
            baseline.probe_search(
                label="probe-test",
                output_path=evidence_path,
                seed_path=self.seed,
                fetcher=fetcher,
            )
        with self.assertRaisesRegex(ValueError, "证据不完整"):
            baseline.load_search_evidence(evidence_path)


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
