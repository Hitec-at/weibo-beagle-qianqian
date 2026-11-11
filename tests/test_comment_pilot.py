from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import comment_pilot
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


if __name__ == "__main__":
    unittest.main()
