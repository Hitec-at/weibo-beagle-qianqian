from __future__ import annotations

import json
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
        )

        self.assertEqual(result["human_candidates"], 1)
        self.assertEqual(result["semantic_proposals"], 1)
        markdown = Path(result["markdown"]).read_text(encoding="utf-8")
        self.assertIn("第一部分：待确认微博（1）", markdown)
        self.assertIn("第二部分：规则与内容建议（1）", markdown)
        self.assertIn("100/c1", markdown)

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
