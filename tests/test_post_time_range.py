from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import store
from app.main import fmt_time


class PostTimeRangeTest(unittest.TestCase):
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

    def _insert_post(self, post_id: str, ts: int | None) -> None:
        conn = store.connect()
        try:
            conn.execute(
                "INSERT INTO posts (id, ts, text_plain) VALUES (?, ?, ?)",
                (post_id, ts, post_id),
            )
            conn.commit()
        finally:
            conn.close()

    def test_empty_database_has_no_range(self) -> None:
        self.assertEqual(
            store.post_time_range(),
            {"min_ts": None, "max_ts": None},
        )

    def test_single_post_has_same_start_and_end(self) -> None:
        self._insert_post("1", 1_700_000_000)

        self.assertEqual(
            store.post_time_range(),
            {"min_ts": 1_700_000_000, "max_ts": 1_700_000_000},
        )

    def test_multiple_posts_use_earliest_and_latest_valid_time(self) -> None:
        self._insert_post("1", 1_700_000_000)
        self._insert_post("2", 1_600_000_000)
        self._insert_post("3", 1_800_000_000)
        self._insert_post("4", None)
        self._insert_post("5", 0)

        self.assertEqual(
            store.post_time_range(),
            {"min_ts": 1_600_000_000, "max_ts": 1_800_000_000},
        )

    def test_display_uses_shanghai_time_across_year_boundary(self) -> None:
        timestamp = int(
            datetime(2025, 12, 31, 16, 30, tzinfo=timezone.utc).timestamp()
        )

        self.assertEqual(fmt_time(timestamp), "2026-01-01 00:30")


if __name__ == "__main__":
    unittest.main()
