from __future__ import annotations

import unittest

from starlette.requests import Request

from app.main import meme_detail


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


class MemeRouteTest(unittest.TestCase):
    def test_legacy_fanguoji_url_redirects_to_fanlaji(self) -> None:
        response = meme_detail(
            request_for("/meme/fanguoji", b"page=2&sort=hot"),
            "fanguoji",
            page=2,
        )

        self.assertEqual(response.status_code, 308)
        self.assertEqual(response.headers["location"], "/meme/fanlaji?page=2&sort=hot")


if __name__ == "__main__":
    unittest.main()
