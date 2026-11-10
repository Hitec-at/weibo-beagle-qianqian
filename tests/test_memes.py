from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import memes
import store


class MemeSeedTest(unittest.TestCase):
    def test_pangshou_erxiangxing_is_curated(self) -> None:
        seed = memes.load_seed()
        item = next(m for m in seed if m["slug"] == "pangshou-erxiangxing")

        self.assertEqual(item["match_mode"], "curated")
        self.assertEqual(len(item["curated_post_ids"]), 22)
        self.assertEqual(len(set(item["curated_post_ids"])), 22)
        self.assertEqual(
            item["representative_url"],
            "https://weibo.com/3866429224/5333186169013964",
        )


class CuratedSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = store.DB_PATH
        store.DB_PATH = Path(self.temp_dir.name) / "test.db"
        store.init_db()
        conn = store.connect()
        try:
            for post_id, text in (
                ("1", "今日体重管理记录46斤"),
                ("2", "刚吃了罐头又饿了"),
                ("3", "无关的胖瘦关键词"),
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
        self.temp_dir.cleanup()

    def _seed(self, post_ids: list[str]) -> Path:
        path = Path(self.temp_dir.name) / "memes.toml"
        ids = ", ".join(f'"{post_id}"' for post_id in post_ids)
        path.write_text(
            f"""
[[memes]]
slug = "curated"
name = "人工条目"
match_mode = "curated"
curated_post_ids = [{ids}]
""",
            encoding="utf-8",
        )
        return path

    def test_curated_mode_links_only_approved_posts(self) -> None:
        summary = memes.sync(self._seed(["1", "2"]))
        rows = store.query_meme_posts("curated")

        self.assertEqual(summary[0]["matched_posts"], 2)
        self.assertEqual({row["id"] for row in rows}, {"1", "2"})
        self.assertEqual({row["matched_by"] for row in rows}, {"curated"})

    def test_curated_mode_rejects_missing_post(self) -> None:
        with self.assertRaisesRegex(ValueError, "尚未入库：999"):
            memes.sync(self._seed(["1", "999"]))


if __name__ == "__main__":
    unittest.main()
