from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import fetch_weibo
import store


def _post(post_id: str) -> dict:
    return {
        "id": post_id,
        "idstr": post_id,
        "mid": post_id,
        "user": {"id": config.TARGET_UID, "screen_name": "target"},
        "created_at": "Thu Nov 12 10:00:00 +0800 2026",
        "text_raw": f"post {post_id}",
    }


class _NoopLimiter:
    def __init__(self, max_requests=None) -> None:
        self.max_requests = max_requests


class FetchTransitionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = store.DB_PATH
        self.old_ledger_path = store.LEDGER_DB_PATH
        store.DB_PATH = Path(self.temp_dir.name) / "test.db"
        store.LEDGER_DB_PATH = store.DB_PATH
        store.init_db()

    def tearDown(self) -> None:
        store.DB_PATH = self.old_db_path
        store.LEDGER_DB_PATH = self.old_ledger_path
        self.temp_dir.cleanup()

    def _insert_post(self, post_id: str) -> None:
        conn = store.connect()
        try:
            conn.execute("INSERT INTO posts (id, ts) VALUES (?, 1)", (post_id,))
            conn.commit()
        finally:
            conn.close()

    def test_resuming_history_does_not_roll_back_last_id(self) -> None:
        store.set_meta("last_page", "5")
        store.set_meta("last_id", "200")
        self._insert_post("200")
        calls: list[int] = []

        def fetch_page(_session, _limiter, page: int):
            calls.append(page)
            return [_post("150")], None

        with (
            patch("fetch_weibo.make_session", return_value=object()),
            patch("fetch_weibo.RateLimiter", _NoopLimiter),
            patch("fetch_weibo.fetch_mymblog", side_effect=fetch_page),
        ):
            fetch_weibo.run("snapshot", True, 1)

        self.assertEqual(calls, [5])
        self.assertEqual(store.get_meta("last_page"), "5")
        self.assertEqual(store.get_meta("last_id"), "200")

    def test_empty_snapshot_marks_history_complete(self) -> None:
        store.set_meta("last_page", "5")

        with (
            patch("fetch_weibo.make_session", return_value=object()),
            patch("fetch_weibo.RateLimiter", _NoopLimiter),
            patch("fetch_weibo.fetch_mymblog", return_value=([], None)),
        ):
            fetch_weibo.run("snapshot", True, None)

        self.assertEqual(store.get_meta("snapshot_complete"), "1")
        self.assertEqual(store.get_meta("last_page"), "0")

    def test_auto_uses_history_until_complete(self) -> None:
        store.set_meta("last_page", "5")
        calls: list[int] = []

        def fetch_page(_session, _limiter, page: int):
            calls.append(page)
            return [], None

        with (
            patch("fetch_weibo.make_session", return_value=object()),
            patch("fetch_weibo.RateLimiter", _NoopLimiter),
            patch("fetch_weibo.fetch_mymblog", side_effect=fetch_page),
        ):
            fetch_weibo.run("auto", False, None)

        self.assertEqual(calls, [5])
        self.assertEqual(store.get_meta("snapshot_complete"), "1")

    def test_auto_uses_incremental_after_completion_and_preserves_page(self) -> None:
        store.set_meta("snapshot_complete", "1")
        store.set_meta("last_page", "0")
        store.set_meta("last_id", "200")
        self._insert_post("200")
        calls: list[int] = []

        def fetch_page(_session, _limiter, page: int):
            calls.append(page)
            if page == 1:
                return [_post("300"), _post("200")], None
            return [], None

        with (
            patch("fetch_weibo.make_session", return_value=object()),
            patch("fetch_weibo.RateLimiter", _NoopLimiter),
            patch("fetch_weibo.fetch_mymblog", side_effect=fetch_page),
        ):
            fetch_weibo.run("auto", False, None)

        self.assertEqual(calls, [1])
        self.assertEqual(store.get_meta("last_id"), "300")
        self.assertEqual(store.get_meta("last_page"), "0")


if __name__ == "__main__":
    unittest.main()
