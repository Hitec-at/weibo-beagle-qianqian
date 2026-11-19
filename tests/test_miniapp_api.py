from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

import mysql_import
import store
from app import miniapp_api
from app.miniapp_repository import MySQLRepository, SQLiteRepository, get_repository


class MiniAppApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = store.DB_PATH
        self.old_ledger_path = store.LEDGER_DB_PATH
        self.old_backend = os.environ.get("MINIAPP_DB_BACKEND")
        store.DB_PATH = Path(self.temp_dir.name) / "miniapp.db"
        store.LEDGER_DB_PATH = Path(self.temp_dir.name) / "ledger.db"
        os.environ["MINIAPP_DB_BACKEND"] = "sqlite"
        store.init_db()
        self._seed()

    def tearDown(self) -> None:
        store.DB_PATH = self.old_db_path
        store.LEDGER_DB_PATH = self.old_ledger_path
        if self.old_backend is None:
            os.environ.pop("MINIAPP_DB_BACKEND", None)
        else:
            os.environ["MINIAPP_DB_BACKEND"] = self.old_backend
        self.temp_dir.cleanup()

    def _seed(self) -> None:
        conn = store.connect()
        try:
            posts = [
                (
                    "101",
                    "5001",
                    "42",
                    "比格钱钱",
                    "2026-11-18 10:00",
                    1_794_967_200,
                    "胖爪爪今天在钱田村值班",
                    "胖爪爪今天在钱田村值班",
                    2,
                    3,
                    10,
                    "微博网页版",
                    0,
                    0,
                    0,
                    1,
                    json.dumps(
                        [
                            {
                                "thumb": "https://wx1.sinaimg.cn/thumb.jpg",
                                "url": "https://wx1.sinaimg.cn/large.jpg",
                            }
                        ],
                        ensure_ascii=False,
                    ),
                    "",
                    "{}",
                    "2026-11-18T10:00:00Z",
                ),
                (
                    "102",
                    "5002",
                    "42",
                    "比格钱钱",
                    "2026-11-17 09:00",
                    1_794_877_200,
                    "普通散步记录",
                    "普通散步记录",
                    0,
                    1,
                    50,
                    "微博网页版",
                    0,
                    0,
                    0,
                    0,
                    "[]",
                    "",
                    "{}",
                    "2026-11-17T09:00:00Z",
                ),
                (
                    "103",
                    "5003",
                    "42",
                    "比格钱钱",
                    "2026-11-16 08:00",
                    1_794_787_200,
                    "翻垃圾现场",
                    "翻垃圾现场",
                    8,
                    5,
                    15,
                    "微博网页版",
                    0,
                    0,
                    0,
                    0,
                    "[]",
                    "",
                    "{}",
                    "2026-11-16T08:00:00Z",
                ),
            ]
            conn.executemany(
                """
                INSERT INTO posts (
                    id, mid, uid, screen_name, created_at, ts, text, text_plain,
                    reposts_count, comments_count, attitudes_count, source,
                    is_longtext, is_video, is_live, has_pics, pics_json,
                    retweeted_user, raw_json, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                posts,
            )
            conn.execute(
                "INSERT INTO post_categories (post_id, category, matched_by) "
                "VALUES (?, ?, ?)",
                ("101", "fan_memes", "curated"),
            )
            conn.execute(
                "INSERT INTO post_categories (post_id, category, matched_by) "
                "VALUES (?, ?, ?)",
                ("103", "memes", "rule"),
            )
            conn.execute(
                "INSERT INTO memes (slug, name, kind, aliases_json, origin, "
                "explanation, behavior_tags_json, exclude_json, "
                "search_queries_json, boundary, representative_url) "
                "VALUES (?, ?, ?, '[]', ?, ?, '[]', '[]', '[]', ?, '')",
                (
                    "pangzhuazhua",
                    "胖爪爪",
                    "fan_fiction",
                    "粉丝二创",
                    "粉丝创作的胖爪爪形象",
                    "只表示二创叙事",
                ),
            )
            conn.execute(
                "INSERT INTO post_memes (post_id, meme_slug, matched_by) "
                "VALUES (?, ?, ?)",
                ("101", "pangzhuazhua", "curated"),
            )
            conn.commit()
        finally:
            conn.close()

    def test_bootstrap_exposes_categories_and_range(self) -> None:
        result = miniapp_api.bootstrap()["data"]

        self.assertEqual(result["api_version"], "v1")
        self.assertEqual(result["default_sort"], "hot")
        self.assertEqual(result["time_range"]["min_text"], "2026-11-16 08:00")
        self.assertEqual(result["time_range"]["max_text"], "2026-11-18 10:00")
        counts = {item["key"]: item["count"] for item in result["feeds"]}
        self.assertEqual(counts["home"], 3)
        self.assertEqual(counts["fan-memes"], 1)
        self.assertEqual(counts["memes"], 1)

    def test_list_posts_defaults_to_hot_and_returns_safe_summary(self) -> None:
        result = miniapp_api.list_posts(
            feed="home", meme="", page=1, page_size=20, sort="hot"
        )["data"]

        self.assertEqual(result["total"], 3)
        self.assertEqual([item["id"] for item in result["items"]], ["103", "102", "101"])
        self.assertNotIn("raw_json", result["items"][0])
        self.assertEqual(result["items"][2]["original_url"], "https://weibo.com/42/5001")
        self.assertEqual(result["items"][2]["pics"][0]["thumb"], "https://wx1.sinaimg.cn/thumb.jpg")

    def test_category_and_meme_filters_are_independent(self) -> None:
        category = miniapp_api.list_posts(
            feed="fan-memes", meme="", page=1, page_size=20, sort="latest"
        )["data"]
        meme = miniapp_api.list_posts(
            feed="home", meme="pangzhuazhua", page=1, page_size=20, sort="latest"
        )["data"]

        self.assertEqual([item["id"] for item in category["items"]], ["101"])
        self.assertEqual([item["id"] for item in meme["items"]], ["101"])

    def test_search_supports_relevance_and_escaped_literal(self) -> None:
        result = miniapp_api.search_posts(
            q="胖爪爪", page=1, page_size=20, sort="relevance"
        )["data"]
        empty = miniapp_api.search_posts(
            q="100%_不存在", page=1, page_size=20, sort="relevance"
        )["data"]

        self.assertEqual([item["id"] for item in result["items"]], ["101"])
        self.assertEqual(empty["total"], 0)

    def test_detail_contains_labels_source_shape_and_no_raw_json(self) -> None:
        item = miniapp_api.get_post_detail("101")["data"]

        self.assertEqual(item["categories"], ["fan_memes"])
        self.assertEqual(item["memes"][0]["slug"], "pangzhuazhua")
        self.assertIsNone(item["video"])
        self.assertNotIn("raw_json", item)
        serialised = json.dumps(item, ensure_ascii=False).encode("utf-8")
        self.assertLess(len(serialised), 1000 * 1024)

    def test_unknown_feed_and_empty_search_are_rejected(self) -> None:
        with self.assertRaises(HTTPException):
            miniapp_api.list_posts(
                feed="not-a-feed", meme="", page=1, page_size=20, sort="hot"
            )
        with self.assertRaises(HTTPException):
            miniapp_api.search_posts(
                q="   ", page=1, page_size=20, sort="relevance"
            )

    def test_page_size_is_capped_for_response_budget(self) -> None:
        result = miniapp_api.list_posts(
            feed="home", meme="", page=1, page_size=20, sort="latest"
        )["data"]

        self.assertEqual(result["page_size"], 20)
        self.assertLessEqual(len(result["items"]), 20)

    def test_mysql_importer_dry_run_reads_the_sqlite_snapshot(self) -> None:
        result = mysql_import.import_sqlite(
            store.DB_PATH,
            apply=False,
            batch_size=250,
            snapshot_label="test",
        )

        self.assertEqual(result["mode"], "dry-run")
        self.assertEqual(result["counts"]["posts"], 3)
        self.assertEqual(result["counts"]["post_categories"], 2)

    def test_mysql_repository_uses_mysql_placeholders_without_connecting(self) -> None:
        sqlite_repo = get_repository()
        self.assertIsInstance(sqlite_repo, SQLiteRepository)

        os.environ["MINIAPP_DB_BACKEND"] = "mysql"
        mysql_repo = get_repository()
        sql, _ = mysql_repo._relevance_order(["胖爪爪"])

        self.assertIsInstance(mysql_repo, MySQLRepository)
        self.assertEqual(mysql_repo.backend_name, "mysql")
        self.assertIn("%s", sql)
        self.assertNotIn("?", sql)


if __name__ == "__main__":
    unittest.main()
