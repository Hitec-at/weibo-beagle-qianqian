from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import candidate_review
import store


class CandidateReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = store.DB_PATH
        self.old_ledger_path = store.LEDGER_DB_PATH
        store.DB_PATH = Path(self.temp_dir.name) / "test.db"
        store.LEDGER_DB_PATH = Path(self.temp_dir.name) / "ledger.db"
        store.init_db()
        conn = store.connect()
        try:
            conn.execute(
                "INSERT INTO memes (slug, name, explanation, boundary) "
                "VALUES ('test-meme', '测试条目', '测试定义', '只接受直接证据')"
            )
            conn.execute(
                "INSERT INTO posts "
                "(id, screen_name, created_at, text_plain, comments_count) "
                "VALUES ('100', 'target', '2026-11-12', '直接行为证据', 2)"
            )
            conn.execute(
                "INSERT INTO posts "
                "(id, screen_name, created_at, text_plain, comments_count) "
                "VALUES ('200', 'target', '2026-11-12', '边界不明确', 0)"
            )
            conn.execute(
                "INSERT INTO comments "
                "(id, post_id, root_id, user_name, text_plain, like_count, fetched_at) "
                "VALUES ('c1', '100', 'c1', '观众', '这就是测试行为', 5, 'now')"
            )
            conn.execute(
                "INSERT INTO baseline_candidates "
                "(baseline_version, post_id, meme_slug, source, query, "
                "matched_term, status, observed_at) "
                "VALUES ('v1', '100', 'test-meme', 'local', '直接', "
                "'直接', 'candidate', '2026-11-12T01:00:00Z')"
            )
            conn.execute(
                "INSERT INTO baseline_candidates "
                "(baseline_version, post_id, meme_slug, source, query, "
                "matched_term, status, observed_at) "
                "VALUES ('v2', '100', 'test-meme', 'local', '直接', "
                "'直接', 'candidate', '2026-11-12T02:00:00Z')"
            )
            conn.execute(
                "INSERT INTO baseline_candidates "
                "(baseline_version, post_id, meme_slug, source, query, "
                "matched_term, status, observed_at) "
                "VALUES ('v2', '200', 'test-meme', 'local', '边界', "
                "'边界', 'candidate', '2026-11-12T02:00:00Z')"
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        store.DB_PATH = self.old_db_path
        store.LEDGER_DB_PATH = self.old_ledger_path
        self.temp_dir.cleanup()

    def _queue_all_for_human(self) -> dict[str, dict]:
        items = candidate_review.pending_items()
        by_post = {item["post_id"]: item for item in items}
        payload = {
            "batch_id": "batch-01",
            "reviewer": "agent-1",
            "decisions": [
                {
                    "candidate_key": item["candidate_key"],
                    "decision": "uncertain",
                    "confidence": 0.7,
                    "reason": "需要人工确认",
                    "evidence": ["post.text"],
                    "proposed_changes": {},
                }
                for item in items
            ],
        }
        path = Path(self.temp_dir.name) / "queue-human.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        candidate_review.record_decisions(path)
        return by_post

    def _human_file(self, decisions: list[dict]) -> Path:
        path = Path(self.temp_dir.name) / "human-decisions.json"
        path.write_text(
            json.dumps(
                {
                    "batch_id": "human-review-test",
                    "reviewer": "human:test",
                    "decisions": decisions,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def test_export_deduplicates_and_includes_context(self) -> None:
        output = Path(self.temp_dir.name) / "batches"
        manifest = candidate_review.export_batches(output, limit=10, batch_size=1)

        self.assertEqual(manifest["total_pending"], 2)
        self.assertEqual(len(manifest["batches"]), 2)
        first = json.loads(
            Path(manifest["batches"][0]["path"]).read_text(encoding="utf-8")
        )
        second = json.loads(
            Path(manifest["batches"][1]["path"]).read_text(encoding="utf-8")
        )
        items = first["items"] + second["items"]
        self.assertEqual({item["post_id"] for item in items}, {"100", "200"})
        item = next(item for item in items if item["post_id"] == "100")
        self.assertEqual(item["meme"]["name"], "测试条目")
        self.assertEqual(item["comments"]["roots"][0]["comment_id"], "c1")

    def test_record_applies_confident_accept_and_queues_uncertain(self) -> None:
        items = candidate_review.pending_items()
        by_post = {item["post_id"]: item for item in items}
        decisions = {
            "batch_id": "batch-01",
            "reviewer": "agent-1",
            "decisions": [
                {
                    "candidate_key": by_post["100"]["candidate_key"],
                    "decision": "accept",
                    "confidence": 0.95,
                    "reason": "正文直接描述行为",
                    "evidence": ["post.text"],
                    "proposed_changes": {},
                },
                {
                    "candidate_key": by_post["200"]["candidate_key"],
                    "decision": "uncertain",
                    "confidence": 0.9,
                    "reason": "需要上下文",
                    "evidence": ["post.text"],
                    "proposed_changes": {},
                },
            ],
        }
        decision_path = Path(self.temp_dir.name) / "decision.json"
        decision_path.write_text(
            json.dumps(decisions, ensure_ascii=False),
            encoding="utf-8",
        )

        result = candidate_review.record_decisions(decision_path)

        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["human_required"], 1)
        links = store.get_post_meme_links("100")
        self.assertEqual(links[0]["meme_slug"], "test-meme")
        self.assertEqual(candidate_review.pending_items(), [])

        human = candidate_review.export_human_review(
            Path(self.temp_dir.name) / "human"
        )
        self.assertEqual(human["pending"], 1)
        markdown = Path(human["markdown"]).read_text(encoding="utf-8")
        self.assertIn("需要上下文", markdown)

    def test_published_candidate_suppresses_older_candidate(self) -> None:
        conn = store.connect()
        try:
            conn.execute(
                "INSERT INTO baseline_candidates "
                "(baseline_version, post_id, meme_slug, source, query, "
                "matched_term, status, observed_at) "
                "VALUES ('v3', '100', 'test-meme', 'curated', '', "
                "'人工', 'published', '2026-11-12T03:00:00Z')"
            )
            conn.commit()
        finally:
            conn.close()

        items = candidate_review.pending_items()

        self.assertEqual([item["post_id"] for item in items], ["200"])

    def test_resolve_human_dry_run_does_not_write(self) -> None:
        by_post = self._queue_all_for_human()
        path = self._human_file(
            [
                {
                    "candidate_key": by_post["100"]["candidate_key"],
                    "decision": "accept",
                    "reason": "人工确认行为成立",
                    "evidence": ["user.review"],
                },
                {
                    "candidate_key": by_post["200"]["candidate_key"],
                    "decision": "reject",
                    "reason": "人工确认证据不足",
                    "evidence": ["user.review"],
                },
            ]
        )

        result = candidate_review.resolve_human_decisions(path, dry_run=True)

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(store.get_post_meme_links("100"), [])
        self.assertEqual(len(store.candidate_reviews_needing_human()), 2)
        self.assertTrue(
            all(
                item["status"] == "would_apply"
                for item in result["results"]
            )
        )

    def test_resolve_human_applies_final_decisions_and_is_idempotent(self) -> None:
        by_post = self._queue_all_for_human()
        path = self._human_file(
            [
                {
                    "candidate_key": by_post["100"]["candidate_key"],
                    "decision": "accept",
                    "reason": "人工确认行为成立",
                    "evidence": ["user.review"],
                },
                {
                    "candidate_key": by_post["200"]["candidate_key"],
                    "decision": "reject",
                    "reason": "人工确认证据不足",
                    "evidence": ["user.review"],
                },
            ]
        )

        result = candidate_review.resolve_human_decisions(path)
        repeated = candidate_review.resolve_human_decisions(path)

        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(repeated["accepted"], 0)
        self.assertEqual(repeated["rejected"], 0)
        self.assertEqual(repeated["unchanged"], 2)
        links = store.get_post_meme_links("100")
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["matched_term"], "human:test")
        conn = store.connect()
        try:
            link = conn.execute(
                "SELECT matched_by FROM post_memes "
                "WHERE post_id = '100' AND meme_slug = 'test-meme'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(link["matched_by"], "human")
        self.assertEqual(store.get_post_meme_links("200"), [])
        self.assertEqual(store.candidate_reviews_needing_human(), [])

    def test_resolve_human_defers_and_keeps_item_for_later(self) -> None:
        by_post = self._queue_all_for_human()
        deferred = self._human_file(
            [
                {
                    "candidate_key": by_post["100"]["candidate_key"],
                    "decision": "defer",
                    "reason": "等待补充视频证据",
                    "evidence": [],
                }
            ]
        )

        result = candidate_review.resolve_human_decisions(deferred)

        self.assertEqual(result["deferred"], 1)
        pending = store.candidate_reviews_needing_human()
        pending_keys = [item["candidate_key"] for item in pending]
        self.assertIn(by_post["100"]["candidate_key"], pending_keys)
        self.assertEqual(
            set(pending_keys),
            {
                by_post["100"]["candidate_key"],
                by_post["200"]["candidate_key"],
            },
        )
        export = candidate_review.export_human_review(
            Path(self.temp_dir.name) / "deferred-human"
        )
        markdown = Path(export["markdown"]).read_text(encoding="utf-8")
        self.assertIn("人工延后", markdown)
        self.assertIn("等待补充视频证据", markdown)
        accepted = self._human_file(
            [
                {
                    "candidate_key": by_post["100"]["candidate_key"],
                    "decision": "accept",
                    "reason": "补充证据后确认",
                    "evidence": ["video.frame"],
                }
            ]
        )
        self.assertEqual(
            candidate_review.resolve_human_decisions(accepted)["accepted"],
            1,
        )

    def test_resolve_human_rejects_conflicts_without_partial_writes(self) -> None:
        by_post = self._queue_all_for_human()
        accepted = self._human_file(
            [
                {
                    "candidate_key": by_post["100"]["candidate_key"],
                    "decision": "accept",
                    "reason": "人工确认行为成立",
                    "evidence": ["user.review"],
                }
            ]
        )
        candidate_review.resolve_human_decisions(accepted)
        conflicting = self._human_file(
            [
                {
                    "candidate_key": by_post["100"]["candidate_key"],
                    "decision": "reject",
                    "reason": "尝试覆盖最终决定",
                    "evidence": ["user.review"],
                },
                {
                    "candidate_key": by_post["200"]["candidate_key"],
                    "decision": "accept",
                    "reason": "本应与其他决定同批提交",
                    "evidence": ["user.review"],
                },
            ]
        )

        with self.assertRaisesRegex(ValueError, "已有人工最终决定"):
            candidate_review.resolve_human_decisions(conflicting)

        self.assertEqual(store.get_post_meme_links("200"), [])
        pending = store.candidate_reviews_needing_human()
        self.assertEqual(
            [item["candidate_key"] for item in pending],
            [by_post["200"]["candidate_key"]],
        )

    def test_resolve_human_rejects_duplicate_or_missing_keys(self) -> None:
        by_post = self._queue_all_for_human()
        duplicate = self._human_file(
            [
                {
                    "candidate_key": by_post["100"]["candidate_key"],
                    "decision": "accept",
                    "reason": "重复一次",
                    "evidence": ["user.review"],
                },
                {
                    "candidate_key": by_post["100"]["candidate_key"],
                    "decision": "reject",
                    "reason": "重复二次",
                    "evidence": ["user.review"],
                },
            ]
        )
        with self.assertRaisesRegex(ValueError, "重复 candidate_key"):
            candidate_review.resolve_human_decisions(duplicate)

        missing = self._human_file(
            [
                {
                    "candidate_key": "missing",
                    "decision": "accept",
                    "reason": "不存在的候选",
                    "evidence": ["user.review"],
                }
            ]
        )
        with self.assertRaisesRegex(ValueError, "候选不存在"):
            candidate_review.resolve_human_decisions(missing)
        self.assertEqual(len(store.candidate_reviews_needing_human()), 2)


if __name__ == "__main__":
    unittest.main()
