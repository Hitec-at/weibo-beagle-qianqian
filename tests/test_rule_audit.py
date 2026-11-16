from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import rule_audit
import store


class RuleAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.old_db_path = store.DB_PATH
        self.old_ledger_path = store.LEDGER_DB_PATH
        store.DB_PATH = self.root / "test.db"
        store.LEDGER_DB_PATH = self.root / "ledger.db"
        store.init_db()
        conn = store.connect()
        try:
            for post_id, text in (
                ("1", "胖爪爪握着手机打字"),
                ("2", "钱田村给村长上贡"),
                ("3", "无关内容"),
            ):
                conn.execute(
                    "INSERT INTO posts (id, text_plain) VALUES (?, ?)",
                    (post_id, text),
                )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        store.DB_PATH = self.old_db_path
        store.LEDGER_DB_PATH = self.old_ledger_path
        self.temp_dir.cleanup()

    def _seed(self, name: str, body: str) -> Path:
        path = self.root / name
        path.write_text(body, encoding="utf-8")
        return path

    def _complete_seed(self) -> Path:
        return self._seed(
            "complete.toml",
            """
[[memes]]
slug = "pangzhuazhua"
name = "胖爪爪"
kind = "fan_fiction"
match_mode = "mixed"
auto_publish_terms = ["胖爪爪"]
candidate_terms = ["胖爪爪文学"]
search_queries = ["胖爪爪", "胖爪爪文学"]
curated_post_ids = ["1"]
exclude = ["普通爪子"]
origin = "粉丝创作"
explanation = "粉丝围绕胖爪子创作故事"
boundary = "必须明确使用胖爪爪二创叙事"
representative_url = "https://weibo.com/1/1"
""",
        )

    def test_complete_rule_is_approved(self) -> None:
        report = rule_audit.build_report(
            self._complete_seed(),
            db_path=store.DB_PATH,
        )

        self.assertEqual(report["status"], "approve")
        self.assertEqual(report["rules"][0]["sample_size"], 1)
        self.assertEqual(report["rules"][0]["status"], "approve")

    def test_missing_examples_is_warning_not_block(self) -> None:
        seed = self._seed(
            "warn.toml",
            """
[[memes]]
slug = "new"
name = "新规则"
match_mode = "mixed"
auto_publish_terms = ["新规则"]
search_queries = ["新规则"]
origin = "来源"
explanation = "解释"
boundary = "边界"
""",
        )

        report = rule_audit.build_report(seed, db_path=store.DB_PATH)
        item = report["rules"][0]
        codes = {finding["code"] for finding in item["findings"]}

        self.assertEqual(report["status"], "warn")
        self.assertIn("single_search_query", codes)
        self.assertIn("missing_positive_examples", codes)
        self.assertIn("missing_negative_examples", codes)
        self.assertIn("insufficient_historical_sample", codes)

    def test_removed_rule_with_links_blocks(self) -> None:
        previous = self._complete_seed()
        current = self._seed(
            "empty.toml",
            "[[memes]]\nslug='other'\nname='其他'\n",
        )
        store.link_post_meme("1", "pangzhuazhua", "胖爪爪")

        report = rule_audit.build_report(
            current,
            previous,
            db_path=store.DB_PATH,
        )
        removed = next(
            item for item in report["rules"] if item["slug"] == "pangzhuazhua"
        )

        self.assertEqual(removed["action"], "removed")
        self.assertEqual(removed["status"], "block")
        self.assertEqual(
            removed["findings"][0]["code"],
            "removed_rule_has_links",
        )

    def test_modified_rule_reports_possible_regression(self) -> None:
        previous = self._complete_seed()
        current = self._seed(
            "changed.toml",
            previous.read_text(encoding="utf-8").replace(
                'auto_publish_terms = ["胖爪爪"]',
                'auto_publish_terms = ["钱田村"]',
            ).replace('curated_post_ids = ["1"]', "curated_post_ids = []"),
        )

        report = rule_audit.build_report(
            current,
            previous,
            db_path=store.DB_PATH,
        )
        item = report["rules"][0]
        codes = {finding["code"] for finding in item["findings"]}

        self.assertIn("possible_historical_regression", codes)
        regression = next(
            finding
            for finding in item["findings"]
            if finding["code"] == "possible_historical_regression"
        )
        self.assertEqual(regression["details"]["lost_count"], 1)

    def test_audit_does_not_write_database(self) -> None:
        before = hashlib.sha256(store.DB_PATH.read_bytes()).digest()
        report = rule_audit.build_report(
            self._complete_seed(),
            db_path=store.DB_PATH,
        )
        after = hashlib.sha256(store.DB_PATH.read_bytes()).digest()

        self.assertEqual(before, after)
        self.assertFalse(report["summary"]["historical_data_complete"])

    def test_report_files_are_stable_json_and_markdown(self) -> None:
        result = rule_audit.write_report(
            self._complete_seed(),
            self.root / "report",
            db_path=store.DB_PATH,
        )
        payload = json.loads(Path(result["json"]).read_text(encoding="utf-8"))

        self.assertEqual(payload["schema_version"], "rule-change-audit-v1")
        self.assertEqual(payload["status"], "approve")
        self.assertTrue(Path(result["markdown"]).is_file())
        self.assertIn("历史微博尚未完整抓取", Path(result["markdown"]).read_text())


if __name__ == "__main__":
    unittest.main()
