from __future__ import annotations

import unittest

from app.main import (
    WECHAT_VERIFICATION_CONTENT,
    WECHAT_VERIFICATION_NAME,
    WECHAT_VERIFICATION_PATH,
    app,
    wechat_browser_verification,
)


class WechatBrowserVerificationTest(unittest.TestCase):
    def test_verification_file_contains_exact_token(self) -> None:
        self.assertEqual(
            WECHAT_VERIFICATION_PATH.read_text(encoding="ascii").strip(),
            WECHAT_VERIFICATION_CONTENT,
        )

    def test_verification_route_is_at_site_root(self) -> None:
        route = next(
            route for route in app.routes if route.path == f"/{WECHAT_VERIFICATION_NAME}"
        )
        self.assertIs(route.endpoint, wechat_browser_verification)

    def test_verification_response_is_plain_text_without_caching(self) -> None:
        response = wechat_browser_verification()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, WECHAT_VERIFICATION_CONTENT.encode("ascii"))
        self.assertEqual(response.media_type, "text/plain")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["content-length"], str(len(WECHAT_VERIFICATION_CONTENT)))


if __name__ == "__main__":
    unittest.main()
