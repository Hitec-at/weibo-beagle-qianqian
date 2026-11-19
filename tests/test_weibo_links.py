from __future__ import annotations

import unittest

from weibo_links import markdown_link, original_url


class WeiboLinksTest(unittest.TestCase):
    def test_prefers_canonical_uid_and_mid_url(self) -> None:
        self.assertEqual(
            original_url({"id": "100", "uid": "42", "mid": "AbC123"}),
            "https://weibo.com/42/AbC123",
        )

    def test_falls_back_to_numeric_post_detail(self) -> None:
        self.assertEqual(
            original_url({"id": "100"}),
            "https://m.weibo.cn/detail/100",
        )

    def test_missing_post_returns_empty_url(self) -> None:
        self.assertEqual(original_url(None), "")
        self.assertEqual(original_url({}), "")

    def test_markdown_link_keeps_post_id_for_audit(self) -> None:
        self.assertEqual(
            markdown_link("100", "https://m.weibo.cn/detail/100"),
            "[微博原文](https://m.weibo.cn/detail/100)（`100`）",
        )
        self.assertEqual(markdown_link("100", ""), "`100`")


if __name__ == "__main__":
    unittest.main()
