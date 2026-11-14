from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import baseline
import memes
import store


class MemeSeedTest(unittest.TestCase):
    def test_pangshou_erxiangxing_is_curated(self) -> None:
        seed = memes.load_seed()
        item = next(m for m in seed if m["slug"] == "pangshou-erxiangxing")

        self.assertEqual(item["match_mode"], "mixed")
        self.assertEqual(len(item["curated_post_ids"]), 25)
        self.assertEqual(len(set(item["curated_post_ids"])), 25)
        self.assertEqual(
            item["representative_url"],
            "https://weibo.com/3866429224/5333186169013964",
        )

    def test_first_review_batch_is_recorded(self) -> None:
        seed = {item["slug"]: item for item in memes.load_seed()}

        self.assertEqual(
            set(seed["chishi"]["curated_post_ids"]),
            {"5341336321656934", "5358220445221881", "5278559430249600"},
        )
        self.assertEqual(
            set(seed["touchi"]["curated_post_ids"]),
            {"5305779676776585", "5302886017010419"},
        )
        self.assertEqual(
            set(seed["shouli"]["curated_post_ids"]),
            {
                "5338393051399605",
                "5271618083358631",
                "5327445222758150",
                "5304365923435602",
            },
        )
        self.assertNotIn("5296319899895967", seed["shouli"]["curated_post_ids"])

    def test_fanlaji_is_curated_with_ten_posts(self) -> None:
        seed = {item["slug"]: item for item in memes.load_seed()}
        item = seed["fanlaji"]

        self.assertNotIn("fanguoji", seed)
        self.assertEqual(item["name"], "翻垃圾")
        self.assertEqual(item["match_mode"], "curated")
        self.assertEqual(len(item["curated_post_ids"]), 10)
        self.assertIn("单纯提到收垃圾、卖纸箱、垃圾桶镜头不算", item["boundary"])
        self.assertEqual(memes.canonical_slug("fanguoji"), "fanlaji")

    def test_audited_false_positive_exclusions_are_registered(self) -> None:
        seed = {item["slug"]: item for item in memes.load_seed()}

        self.assertIn("猫咪领养", seed["chishi"]["exclude"])
        self.assertIn("我偷偷吃", seed["touchi"]["exclude"])
        self.assertIn("发小的狗", seed["pangshou-erxiangxing"]["exclude"])
        self.assertIn("11岁的边牧", seed["pangshou-erxiangxing"]["exclude"])
        self.assertNotIn("一箱", seed["shouli"]["exclude"])

    def test_confirmed_boundaries_are_registered_in_query_packs(self) -> None:
        seed = {item["slug"]: item for item in memes.load_seed()}

        self.assertIn(
            "有屎的纸尿裤", seed["chishi"]["auto_publish_terms"]
        )
        self.assertIn("垃圾桶", seed["chishi"]["candidate_terms"])
        self.assertIn("小胖狗", seed["pangshou-erxiangxing"]["auto_publish_terms"])
        self.assertIn("胖狗", seed["pangshou-erxiangxing"]["auto_publish_terms"])
        self.assertIn("减肥失败", seed["pangshou-erxiangxing"]["auto_publish_terms"])
        self.assertIn("钱钱的快递", seed["shouli"]["auto_publish_terms"])
        self.assertIn("只吃垃圾桶内容属于翻垃圾", seed["chishi"]["boundary"])
        self.assertIn(
            "不再要求同时出现体重变化和饮食控制两个环节",
            seed["pangshou-erxiangxing"]["boundary"],
        )


class CuratedSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = store.DB_PATH
        self.old_ledger_path = store.LEDGER_DB_PATH
        store.DB_PATH = Path(self.temp_dir.name) / "test.db"
        store.LEDGER_DB_PATH = Path(self.temp_dir.name) / "ledger.db"
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
        store.LEDGER_DB_PATH = self.old_ledger_path
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

    def test_legacy_slug_migration_preserves_links_and_baseline(self) -> None:
        conn = store.connect()
        try:
            conn.execute(
                "INSERT INTO memes (slug, name) VALUES ('fanguoji', '翻垃圾')"
            )
            conn.execute(
                """
                INSERT INTO post_memes (
                    post_id, meme_slug, matched_by, matched_term, confidence
                ) VALUES ('1', 'fanguoji', 'curated', '人工审定', 1.0)
                """
            )
            conn.execute(
                """
                INSERT INTO baseline_candidates (
                    baseline_version, post_id, meme_slug, source, query,
                    matched_term, status, observed_at
                ) VALUES (
                    'baseline-test', '1', 'fanguoji', 'local', '翻垃圾',
                    '翻垃圾', 'candidate', '2026-11-12T00:00:00Z'
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

        self.assertTrue(store.rename_meme_slug("fanguoji", "fanlaji"))
        self.assertIsNone(store.get_meme("fanguoji"))
        self.assertEqual(store.get_meme("fanlaji")["name"], "翻垃圾")

        conn = store.connect()
        try:
            meme_slug = conn.execute(
                "SELECT meme_slug FROM post_memes WHERE post_id = '1'"
            ).fetchone()[0]
            baseline_slug = conn.execute(
                "SELECT meme_slug FROM baseline_candidates WHERE post_id = '1'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(meme_slug, "fanlaji")
        self.assertEqual(baseline_slug, "fanlaji")


class ConfirmedBoundaryReplayTest(unittest.TestCase):
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

    def _set_post(self, post_id: str, text: str) -> None:
        conn = store.connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO posts (id, text_plain) VALUES (?, ?)",
                (post_id, text),
            )
            conn.execute(
                "UPDATE posts SET text_plain = ? WHERE id = ?",
                (text, post_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _statuses(self, post_id: str) -> dict[str, set[str]]:
        conn = store.connect()
        try:
            rows = conn.execute(
                """
                SELECT meme_slug, status
                FROM baseline_candidates
                WHERE baseline_version = 'baseline-rule65'
                  AND post_id = ?
                ORDER BY observed_at DESC
                """,
                (post_id,),
            ).fetchall()
        finally:
            conn.close()
        statuses: dict[str, set[str]] = {}
        for row in rows:
            statuses.setdefault(row["meme_slug"], set()).add(row["status"])
        return statuses

    def test_confirmed_human_boundaries_replay_through_rules(self) -> None:
        seed = memes.load_seed()
        for item in seed:
            for post_id in item.get("curated_post_ids") or []:
                self._set_post(str(post_id), "")

        cases = {
            "5234028678220391": (
                "一到家，奶奶和弟弟就和我说今天钱钱哥又偷妹妹有屎的纸尿裤了",
                "chishi",
            ),
            "5223004414149306": ("早上好🌞小胖狗～", "pangshou-erxiangxing"),
            "5219598859240577": (
                "保安大叔问是不是那只胖狗脖子是白色的",
                "pangshou-erxiangxing",
            ),
            "5232897495401918": ("减肥失败☹️", "pangshou-erxiangxing"),
            "5246370361640174": (
                "胖的还在吃，瘦的已经睡觉了",
                "pangshou-erxiangxing",
            ),
            "5231732026510004": ("您的快递📦正在打包～", "shouli"),
            "5235115999366135": ("全是钱钱的快递📦", "shouli"),
        }
        for post_id, (text, _) in cases.items():
            self._set_post(post_id, text)
        self._set_post("5219209567277286", "昨天偷吃太多了，饿他两天！")
        self._set_post("5313804204247818", "垃圾桶放在地上没一分钟！")

        self._set_post("human-1", "没有自动命中词的微博")
        store.link_post_meme(
            "human-1",
            "pangshou-erxiangxing",
            "人工审定",
            matched_by="human",
        )

        baseline.build("baseline-rule65")

        for post_id, (_, meme_slug) in cases.items():
            self.assertIn(
                "published",
                self._statuses(post_id).get(meme_slug, set()),
                post_id,
            )

        self.assertIn(
            "published",
            self._statuses("5219209567277286").get("touchi", set()),
        )
        self.assertNotIn(
            "published",
            self._statuses("5219209567277286").get(
                "pangshou-erxiangxing", set()
            ),
        )
        self.assertNotIn(
            "published",
            self._statuses("5313804204247818").get("chishi", set()),
        )
        self.assertIn(
            "published",
            self._statuses("5313804204247818").get("fanlaji", set()),
        )

        conn = store.connect()
        try:
            human_links = {
                row["meme_slug"]: row["matched_by"]
                for row in conn.execute(
                    "SELECT meme_slug, matched_by FROM post_memes WHERE post_id = ?",
                    ("human-1",),
                ).fetchall()
            }
        finally:
            conn.close()
        self.assertEqual(human_links.get("pangshou-erxiangxing"), "human")


if __name__ == "__main__":
    unittest.main()
