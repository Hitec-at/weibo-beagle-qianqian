from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import classify
import store


class ClassificationTest(unittest.TestCase):
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

    @staticmethod
    def _post(post_id: str, text: str, retweeted_user: str = "") -> dict:
        return {
            "id": post_id,
            "text_plain": text,
            "retweeted_user": retweeted_user,
            "has_pics": 0,
            "is_video": 0,
            "is_live": 0,
            "reposts_count": 0,
            "comments_count": 0,
        }

    def test_entity_alias_is_indexed_without_becoming_a_meme(self) -> None:
        categories = classify.classify_post(
            self._post("1", "村长今天又去巡视村田了")
        )

        self.assertIn("qianqian_aliases", categories)
        self.assertNotIn("memes", categories)

        categories = classify.classify_post(self._post("1", "今天天气不错"))
        self.assertNotIn("qianqian_aliases", categories)

    def test_mengqian_cp_requires_interaction_match(self) -> None:
        categories = classify.classify_post(
            self._post("2", "今天和 @比格犬受害者联盟 高调示爱")
        )

        self.assertIn("interactions", categories)
        self.assertIn("interaction_mengqian_cp", categories)

    def test_mengqian_cp_does_not_match_outside_interactions(self) -> None:
        categories = classify.classify_post(
            self._post("3", "今天看到一条普通官宣")
        )

        self.assertNotIn("interactions", categories)
        self.assertNotIn("interaction_mengqian_cp", categories)

    def test_curated_mengqian_cp_is_indexed_without_body_terms(self) -> None:
        categories = classify.classify_post(
            self._post("5284063264377508", "@比格犬受害者联盟 ​​​")
        )

        self.assertIn("interactions", categories)
        self.assertIn("interaction_mengqian_cp", categories)
        classify.classify_post(
            self._post("5284063264377508", "@比格犬受害者联盟 ​​​")
        )
        conn = store.connect()
        try:
            row = conn.execute(
                "SELECT matched_by FROM post_categories "
                "WHERE post_id = ? AND category = 'interaction_mengqian_cp'",
                ("5284063264377508",),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["matched_by"], "curated")

    def test_confirmed_high_precision_phrases_are_indexed_as_memes(self) -> None:
        cases = (
            "今天钱钱又偷有屎的纸尿裤了",
            "早上好，小胖狗",
            "保安说是不是那只胖狗",
            "减肥失败",
            "全是钱钱的快递",
            "您的快递📦正在打包",
        )
        for index, text in enumerate(cases, start=10):
            with self.subTest(text=text):
                categories = classify.classify_post(self._post(str(index), text))
                self.assertIn("memes", categories)

    def test_generic_diaper_and_bin_words_remain_reviewable(self) -> None:
        self.assertNotIn(
            "memes",
            classify.classify_post(self._post("20", "纸尿裤放在桌上")),
        )
        self.assertIn(
            "memes",
            classify.classify_post(self._post("21", "垃圾桶放在地上")),
        )


if __name__ == "__main__":
    unittest.main()
