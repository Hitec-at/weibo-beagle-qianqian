from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import daily_ops


class DailyOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_capture_classification_counts(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE post_categories (post_id TEXT, category TEXT);
            CREATE TABLE post_memes (post_id TEXT, meme_slug TEXT);
            INSERT INTO post_categories VALUES
                ('100', 'hot'), ('200', 'hot'), ('200', 'interactions');
            INSERT INTO post_memes VALUES
                ('100', 'chishi'), ('100', 'touchi'), ('200', 'chishi');
            """
        )

        counts = daily_ops.capture_classification_counts(connection)
        connection.close()

        self.assertEqual(
            counts["categories"],
            {
                "hot": {"associations": 2, "unique_posts": 2},
                "interactions": {"associations": 1, "unique_posts": 1},
            },
        )
        self.assertEqual(
            counts["memes"],
            {
                "chishi": {"associations": 2, "unique_posts": 2},
                "touchi": {"associations": 1, "unique_posts": 1},
            },
        )

    def test_classification_deltas_keep_positive_zero_and_negative_changes(
        self,
    ) -> None:
        result = daily_ops.classification_deltas(
            {
                "categories": {
                    "gone": {"associations": 2, "unique_posts": 2},
                    "hot": {"associations": 3, "unique_posts": 2},
                    "same": {"associations": 1, "unique_posts": 1},
                },
                "memes": {
                    "chishi": {"associations": 2, "unique_posts": 2}
                },
            },
            {
                "categories": {
                    "hot": {"associations": 5, "unique_posts": 4},
                    "new": {"associations": 1, "unique_posts": 1},
                    "same": {"associations": 1, "unique_posts": 1},
                },
                "memes": {
                    "chishi": {"associations": 1, "unique_posts": 1},
                    "touchi": {"associations": 3, "unique_posts": 2},
                },
            },
        )

        categories = {
            row["category"]: row for row in result["category_deltas"]
        }
        self.assertEqual(categories["hot"]["association_delta"], 2)
        self.assertEqual(categories["hot"]["unique_posts_delta"], 2)
        self.assertEqual(categories["same"]["association_delta"], 0)
        self.assertEqual(categories["gone"]["association_delta"], -2)
        self.assertEqual(categories["new"]["association_delta"], 1)

        memes = {row["meme_slug"]: row for row in result["meme_deltas"]}
        self.assertEqual(memes["chishi"]["association_delta"], -1)
        self.assertEqual(memes["touchi"]["association_delta"], 3)
        self.assertEqual(memes["touchi"]["unique_posts_delta"], 2)

    def test_success_manifest_records_request_and_release(self) -> None:
        result = daily_ops.write_run_manifest(
            self.root,
            {
                "stamp": "20261114T010000Z",
                "status": "success",
                "request_ledger": {
                    "used": 447,
                    "limit": 500,
                    "by_category": {"timeline": 33, "comments": 20},
                },
                "data": {
                    "release": "data-20261114T020000Z",
                    "asset_name": "weibo.db.gz",
                    "sha256": "a" * 64,
                    "posts": 5005,
                    "comments": 791,
                },
                "baseline": {"version": "baseline-20261114T010000Z"},
            },
        )

        manifest = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["request_ledger"]["remaining"], 53)
        self.assertEqual(manifest["data"]["posts"], 5005)
        self.assertEqual(manifest["category_deltas"], [])
        self.assertEqual(manifest["meme_deltas"], [])

    def test_manifest_records_classification_deltas_idempotently(self) -> None:
        state = {
            "stamp": "20261114T010000Z",
            "status": "success",
            "request_ledger": {"used": 447, "limit": 500},
            "data": {
                "release": "data-20261114T020000Z",
                "asset_name": "weibo.db.gz",
                "sha256": "a" * 64,
            },
            "baseline": {"version": "baseline-20261114T010000Z"},
            "classification_before": {
                "categories": {
                    "hot": {"associations": 2, "unique_posts": 2}
                },
                "memes": {
                    "chishi": {"associations": 1, "unique_posts": 1}
                },
            },
            "classification_after": {
                "categories": {
                    "hot": {"associations": 4, "unique_posts": 3},
                    "interactions": {
                        "associations": 1,
                        "unique_posts": 1,
                    },
                },
                "memes": {
                    "chishi": {"associations": 2, "unique_posts": 2},
                    "touchi": {"associations": 1, "unique_posts": 1},
                },
            },
        }

        first = daily_ops.write_run_manifest(self.root, state)
        first_payload = json.loads(
            Path(first["path"]).read_text(encoding="utf-8")
        )
        second = daily_ops.write_run_manifest(self.root, state)
        second_payload = json.loads(
            Path(second["path"]).read_text(encoding="utf-8")
        )

        self.assertEqual(first_payload, second_payload)
        categories = {
            row["category"]: row for row in first_payload["category_deltas"]
        }
        self.assertEqual(categories["hot"]["association_delta"], 2)
        self.assertEqual(categories["interactions"]["association_delta"], 1)
        memes = {
            row["meme_slug"]: row for row in first_payload["meme_deltas"]
        }
        self.assertEqual(memes["chishi"]["association_delta"], 1)
        self.assertEqual(memes["touchi"]["association_delta"], 1)

    def test_success_manifest_requires_both_classification_snapshots(self) -> None:
        with self.assertRaises(ValueError):
            daily_ops.write_run_manifest(
                self.root,
                {
                    "stamp": "20261114T010000Z",
                    "status": "success",
                    "request_ledger": {"used": 10, "limit": 500},
                    "data": {
                        "release": "data-20261114T020000Z",
                        "asset_name": "weibo.db.gz",
                        "sha256": "a" * 64,
                    },
                    "baseline": {"version": "baseline-20261114T010000Z"},
                    "classification_before": {
                        "categories": {
                            "hot": {
                                "associations": 2,
                                "unique_posts": 2,
                            }
                        },
                        "memes": {},
                    },
                },
            )

    def test_failed_manifest_requires_failed_stage(self) -> None:
        with self.assertRaises(ValueError):
            daily_ops.write_run_manifest(
                self.root,
                {
                    "stamp": "20261114T010000Z",
                    "status": "failed",
                    "request_ledger": {"used": 10, "limit": 500},
                    "data": {},
                },
            )

    def test_semantic_proposal_validation_and_review_dossier(self) -> None:
        candidate_dir = self.root / "candidate"
        candidate_dir.mkdir()
        (candidate_dir / "human-review.json").write_text(
            json.dumps(
                {
                    "test-meme": [
                        {
                            "candidate_key": "candidate-1",
                            "post_id": "100",
                            "meme_slug": "test-meme",
                            "confidence": 0.6,
                            "reason": "需要确认主体",
                            "evidence": ["post.text"],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        proposals = self.root / "proposals.json"
        proposals.write_text(
            json.dumps(
                {
                    "schema_version": "semantic-proposals-v1",
                    "proposals": [
                        {
                            "proposal_key": "alias-1",
                            "kind": "entity_alias",
                            "title": "增加测试别名",
                            "target": "钱钱实体别名",
                            "proposed_change": {"add": ["测试称呼"]},
                            "reason": "两个帖子重复出现",
                            "confidence": 0.9,
                            "evidence": [
                                {
                                    "post_id": "100",
                                    "comment_id": "c1",
                                    "quote": "测试称呼",
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = daily_ops.build_review_dossier(
            candidate_dir,
            self.root / "review",
            "20261114T010000Z",
            proposals_path=proposals,
            post_lookup=lambda post_id: {
                "id": post_id,
                "uid": "42",
                "mid": f"m-{post_id}",
            },
        )

        self.assertEqual(result["human_candidates"], 1)
        self.assertEqual(result["semantic_proposals"], 1)
        markdown = Path(result["markdown"]).read_text(encoding="utf-8")
        self.assertIn("第一部分：待确认微博（1）", markdown)
        self.assertIn("第二部分：规则与内容建议（1）", markdown)
        self.assertIn(
            "[微博原文（100/c1）](https://weibo.com/42/m-100)",
            markdown,
        )
        self.assertIn(
            "[微博原文](https://weibo.com/42/m-100)（`100`）",
            markdown,
        )

    def test_invalid_semantic_proposal_kind_is_rejected(self) -> None:
        path = self.root / "bad.json"
        path.write_text(
            json.dumps(
                {
                    "proposals": [
                        {
                            "proposal_key": "bad-1",
                            "kind": "unknown",
                            "title": "无效",
                            "target": "测试",
                            "proposed_change": {"x": 1},
                            "confidence": 0.9,
                            "evidence": [{"post_id": "1"}],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with self.assertRaises(ValueError):
            daily_ops.load_semantic_proposals(path)


if __name__ == "__main__":
    unittest.main()
