from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import comment_pilot
import fetch_weibo
import store


class CommentPilotTest(unittest.TestCase):
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

    def test_root_selection_is_union_of_default_and_top_likes(self) -> None:
        comments = [
            {"idstr": str(i), "like_counts": i if i == 12 else 0}
            for i in range(1, 13)
        ]
        roots = comment_pilot.choose_root_comments(comments)
        self.assertEqual([root["idstr"] for root in roots], [*map(str, range(1, 11)), "12"])

    def test_root_selection_records_page_and_like_ranks(self) -> None:
        comments = [
            {"idstr": str(i), "like_counts": i if i == 12 else 0}
            for i in range(1, 13)
        ]
        selected = comment_pilot._select_root_comments(comments)
        extra = selected[-1]

        self.assertEqual(extra["comment"]["idstr"], "12")
        self.assertEqual(extra["page_rank"], 12)
        self.assertEqual(extra["like_rank"], 1)
        self.assertEqual(extra["selected_by_default"], 0)
        self.assertEqual(extra["selected_by_likes"], 1)

    def test_embedded_replies_are_saved_but_marked_incomplete(self) -> None:
        items = [
            {
                "idstr": "root-1",
                "text_raw": "高赞评论",
                "like_counts": 10,
                "isLikedByMblogAuthor": True,
                "user": {"screen_name": "观众"},
                "total_number": 3,
                "comments": [
                    {
                        "idstr": "reply-1",
                        "text_raw": "内嵌回复",
                        "like_counts": 2,
                        "reply_id": "root-1",
                        "user": {"screen_name": "回复者"},
                    }
                ],
            }
        ]
        thread = comment_pilot.save_page("100", items, "next", "pilot")
        rows = store.comments_for_post("100")

        self.assertEqual(len(rows), 2)
        self.assertEqual(thread["roots_seen"], 1)
        self.assertEqual(thread["replies_saved"], 1)
        self.assertEqual(thread["complete"], 0)
        root = next(row for row in rows if not row["is_reply"])
        self.assertEqual(root["reply_total"], 3)
        self.assertEqual(root["reply_saved"], 1)
        self.assertEqual(root["reply_complete"], 0)
        self.assertEqual(root["author_liked"], 1)
        self.assertEqual(root["author_liked_known"], 1)

    def test_all_roots_from_page_are_saved(self) -> None:
        items = [
            {
                "idstr": str(i),
                "text_raw": f"评论 {i}",
                "like_counts": i if i == 20 else 0,
                "total_number": 0,
            }
            for i in range(1, 21)
        ]
        thread = comment_pilot.save_page("100", items, "next", "pilot")
        rows = store.comments_for_post("100")

        self.assertEqual(thread["roots_selected"], 20)
        self.assertEqual(len(rows), 20)
        self.assertEqual(
            {row["id"] for row in rows},
            {str(i) for i in range(1, 21)},
        )

    def test_embedded_reply_counts_ignore_duplicates_and_invalid_ids(self) -> None:
        items = [
            {
                "idstr": "root-1",
                "text_raw": "根评论",
                "total_number": 2,
                "comments": [
                    {"idstr": "reply-1", "text_raw": "回复一"},
                    {"idstr": "reply-1", "text_raw": "重复回复"},
                    {"text_raw": "缺少 id"},
                ],
            }
        ]
        thread = comment_pilot.save_page("100", items, "next", "pilot")
        rows = store.comments_for_post("100")
        root = next(row for row in rows if not row["is_reply"])

        self.assertEqual(thread["replies_saved"], 1)
        self.assertEqual(root["reply_saved"], 1)
        self.assertEqual(root["reply_complete"], 0)

    def test_resaving_page_replaces_stale_comments(self) -> None:
        old_items = [
            {
                "idstr": "old-root",
                "text_raw": "旧评论",
                "total_number": 0,
            }
        ]
        new_items = [
            {
                "idstr": "new-root",
                "text_raw": "新评论",
                "total_number": 0,
            }
        ]
        comment_pilot.save_page("100", old_items, "next", "pilot")
        comment_pilot.save_page("100", new_items, "next", "pilot")

        rows = store.comments_for_post("100")
        self.assertEqual([row["id"] for row in rows], ["new-root"])

    def test_pilot_is_capped_and_skips_completed_posts(self) -> None:
        with self.assertRaises(ValueError):
            comment_pilot.select_posts(limit=21)

        done = {
            "idstr": "done-root",
            "text_raw": "已完成",
            "total_number": 0,
        }
        comment_pilot.save_page("done", [done], "next", "pilot")
        posts = [
            {"id": post_id}
            for post_id in ("done", "pending-1", "pending-2")
        ]
        with (
            patch("fetch_weibo.make_session", return_value=object()),
            patch("fetch_weibo.RateLimiter", return_value=object()),
            patch("fetch_weibo.fetch_comments", return_value=([], "next")) as fetch_comments,
        ):
            result = comment_pilot.fetch(posts)

        self.assertEqual(fetch_comments.call_count, 2)
        self.assertEqual(result["fetched_posts"], 2)
        self.assertEqual(result["skipped_posts"], 1)
        for call in fetch_comments.call_args_list:
            self.assertEqual(call.kwargs["max_attempts"], 1)

    def test_single_attempt_request_does_not_retry(self) -> None:
        class Response:
            status_code = 500
            text = "server error"

        class Session:
            calls = 0

            def get(self, *args, **kwargs):
                self.calls += 1
                return Response()

        class Limiter:
            calls = 0

            def wait(self, url):
                self.calls += 1

        session = Session()
        limiter = Limiter()
        with self.assertRaises(fetch_weibo.WeiboBlocked):
            fetch_weibo.request_json(
                session,
                limiter,
                fetch_weibo.COMMENTS_API,
                {},
                max_attempts=1,
            )
        self.assertEqual(session.calls, 1)
        self.assertEqual(limiter.calls, 1)

    def test_selection_and_export_are_deterministic_offline(self) -> None:
        conn = store.connect()
        try:
            for number in range(1, 21):
                post_id = str(number)
                conn.execute(
                    "INSERT INTO posts (id, text_plain, comments_count) VALUES (?, ?, ?)",
                    (post_id, f"post {post_id}", 20 - number),
                )
            conn.commit()
        finally:
            conn.close()
        seed = Path(self.temp_dir.name) / "memes.toml"
        seed.write_text(
            "\n".join(
                f"""
[[memes]]
slug = "meme-{index}"
name = "罪{index}"
match_mode = "curated"
curated_post_ids = ["{index}", "{index + 4}", "{index + 8}", "{index + 12}", "{index + 16}"]
"""
                for index in range(1, 5)
            ),
            encoding="utf-8",
        )
        import memes

        memes.sync(seed)
        conn = store.connect()
        try:
            for post_id in ("17", "18", "19", "20"):
                conn.execute(
                    "INSERT INTO post_categories (post_id, category) VALUES (?, 'interactions')",
                    (post_id,),
                )
            conn.commit()
        finally:
            conn.close()

        posts = comment_pilot.select_posts(limit=20)
        output = Path(self.temp_dir.name) / "out"
        exported = comment_pilot.export(posts, output)

        self.assertEqual(len(posts), 20)
        self.assertEqual(len({post["id"] for post in posts}), 20)
        self.assertTrue((output / "comment-pilot.json").exists())
        self.assertTrue((output / "comment-pilot.md").exists())
        self.assertEqual(exported["posts"], 20)

    def test_selection_skips_completed_and_prefers_newer_posts(self) -> None:
        conn = store.connect()
        try:
            for number in range(1, 5):
                conn.execute(
                    "INSERT INTO posts (id, text_plain, fetched_at, ts, comments_count) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        str(number),
                        f"post {number} 比格犬受害者联盟",
                        f"2026-11-{number:02d}T00:00:00+00:00",
                        number,
                        number,
                    ),
                )
            conn.execute(
                "INSERT INTO post_categories (post_id, category) "
                "VALUES ('1', 'interactions'), ('2', 'interactions'), "
                "('3', 'interactions'), ('4', 'interactions')"
            )
            conn.commit()
        finally:
            conn.close()
        comment_pilot.save_page(
            "4",
            [{"idstr": "done", "text_raw": "done", "total_number": 0}],
            "next",
            "pilot",
        )

        posts = comment_pilot.select_posts(limit=3)

        self.assertEqual([post["id"] for post in posts], ["3", "2", "1"])

    def test_export_contains_selection_audit_fields(self) -> None:
        comments = [
            {
                "idstr": str(i),
                "text_raw": f"评论 {i}",
                "like_counts": i if i == 12 else 0,
                "total_number": 0,
            }
            for i in range(1, 13)
        ]
        comment_pilot.save_page("100", comments, "next", "pilot")
        post = {
            "id": "100",
            "text_plain": "原微博",
            "comments_count": 12,
            "selection_reason": "测试",
            "selection_rank": 1,
        }
        output = Path(self.temp_dir.name) / "audit"
        comment_pilot.export([post], output)
        package = json.loads((output / "comment-pilot.json").read_text(encoding="utf-8"))
        extra = next(
            row for row in package[0]["comments"] if row["id"] == "12"
        )

        self.assertEqual(extra["page_rank"], 12)
        self.assertEqual(extra["like_rank"], 1)
        self.assertEqual(extra["selected_by_default"], 0)
        self.assertEqual(extra["selected_by_likes"], 1)
        markdown = (output / "comment-pilot.md").read_text(encoding="utf-8")
        self.assertIn("首页位次", markdown)
        self.assertIn("| 12 | 1 | 高赞 |", markdown)
        self.assertIn("| 未知 |", markdown)


if __name__ == "__main__":
    unittest.main()
