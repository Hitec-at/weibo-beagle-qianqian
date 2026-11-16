from __future__ import annotations

import unittest

from starlette.requests import Request

from app.main import pagination_context, post_sort_control


def request_for(path: str, query: bytes = b"") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": ("testserver", 443),
            "path": path,
            "query_string": query,
            "headers": [],
        }
    )


class ListSortingTest(unittest.TestCase):
    def test_post_lists_share_hot_latest_earliest_order(self) -> None:
        control = post_sort_control(request_for("/memes/all"))

        self.assertEqual(control["current"], "hot")
        self.assertEqual(
            [option["value"] for option in control["options"]],
            ["hot", "latest", "earliest"],
        )
        self.assertEqual(
            [option["label"] for option in control["options"]],
            ["热门", "最新", "最早"],
        )
        self.assertTrue(control["options"][0]["active"])

    def test_unknown_sort_falls_back_to_hot(self) -> None:
        control = post_sort_control(request_for("/photos", b"sort=likes"))

        self.assertEqual(control["current"], "hot")
        self.assertTrue(control["options"][0]["active"])

    def test_sort_links_keep_unrelated_query_parameters(self) -> None:
        control = post_sort_control(
            request_for("/interactions", b"category=mengqian-cp")
        )

        self.assertEqual(control["current"], "hot")
        self.assertEqual(
            [option["url"] for option in control["options"]],
            [
                "/interactions?category=mengqian-cp",
                "/interactions?category=mengqian-cp&sort=latest",
                "/interactions?category=mengqian-cp&sort=earliest",
            ],
        )

    def test_pagination_preserves_selected_sort(self) -> None:
        pagination = pagination_context(
            request_for("/meme/chishi", b"sort=earliest"),
            total=45,
            page=2,
            per_page=20,
            sort="earliest",
        )

        self.assertEqual(pagination["prev_url"], "/meme/chishi?sort=earliest&page=1")
        self.assertEqual(pagination["next_url"], "/meme/chishi?sort=earliest&page=3")


if __name__ == "__main__":
    unittest.main()
