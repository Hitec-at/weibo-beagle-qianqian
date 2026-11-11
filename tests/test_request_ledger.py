from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import store


class RequestLedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = store.DB_PATH
        self.old_ledger_path = store.LEDGER_DB_PATH
        store.DB_PATH = Path(self.temp_dir.name) / "test.db"
        store.LEDGER_DB_PATH = Path(self.temp_dir.name) / "ledger.db"
        store.init_db()

    def tearDown(self) -> None:
        store.DB_PATH = self.old_db_path
        store.LEDGER_DB_PATH = self.old_ledger_path
        self.temp_dir.cleanup()

    def test_shared_daily_limit_and_categories(self) -> None:
        self.assertEqual(
            store.reserve_weibo_request("2026-11-11", "timeline", "/mymblog", 2),
            1,
        )
        self.assertEqual(
            store.reserve_weibo_request("2026-11-11", "comments", "/comments", 2),
            2,
        )
        self.assertIsNone(
            store.reserve_weibo_request("2026-11-11", "detail", "/show", 2)
        )

        stats = store.weibo_request_stats("2026-11-11")
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["categories"], {"comments": 1, "timeline": 1})

    def test_new_day_has_separate_budget(self) -> None:
        store.reserve_weibo_request("2026-11-11", "timeline", "/mymblog", 1)
        self.assertEqual(
            store.reserve_weibo_request("2026-11-12", "timeline", "/mymblog", 1),
            1,
        )

    def test_two_corpus_databases_share_one_ledger(self) -> None:
        first_db = Path(self.temp_dir.name) / "first.db"
        second_db = Path(self.temp_dir.name) / "second.db"
        shared_ledger = Path(self.temp_dir.name) / "shared-ledger.db"

        store.DB_PATH = first_db
        store.LEDGER_DB_PATH = shared_ledger
        store.init_db()
        self.assertEqual(
            store.reserve_weibo_request("2026-11-11", "timeline", "/mymblog", 2),
            1,
        )

        store.DB_PATH = second_db
        store.init_db()
        self.assertEqual(
            store.reserve_weibo_request("2026-11-11", "comments", "/comments", 2),
            2,
        )
        self.assertIsNone(
            store.reserve_weibo_request("2026-11-11", "search", "/search", 2)
        )

    def test_empty_shared_ledger_imports_existing_corpus_usage(self) -> None:
        source_db = store.DB_PATH
        store.LEDGER_DB_PATH = source_db
        store.reserve_weibo_request("2026-11-11", "timeline", "/mymblog", 10)

        store.DB_PATH = source_db
        store.LEDGER_DB_PATH = Path(self.temp_dir.name) / "shared.db"
        store.init_db()

        stats = store.weibo_request_stats("2026-11-11")
        self.assertEqual(stats["total"], 1)


if __name__ == "__main__":
    unittest.main()
