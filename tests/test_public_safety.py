from __future__ import annotations

import json
import time
import unittest

from app.main import img_proxy, is_sina_image_url, video_info


class ImageProxySafetyTest(unittest.TestCase):
    def test_allows_sina_hosts(self) -> None:
        self.assertTrue(is_sina_image_url("https://wx1.sinaimg.cn/large/demo.jpg"))
        self.assertTrue(is_sina_image_url("http://sinaimg.cn/demo.jpg"))

    def test_rejects_lookalike_or_embedded_hosts(self) -> None:
        rejected = [
            "https://example.com/a.sinaimg.cn/demo.jpg",
            "https://sinaimg.cn.evil.test/demo.jpg",
            "https://evilsinaimg.cn/demo.jpg",
            "https://evil.test/?u=.sinaimg.cn",
            "https://sinaimg.cn@evil.test/demo.jpg",
            "file:///etc/passwd",
        ]
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(is_sina_image_url(url))

    def test_img_proxy_only_rewrites_allowed_urls(self) -> None:
        allowed = "https://wx1.sinaimg.cn/large/demo.jpg"
        self.assertTrue(img_proxy(allowed).startswith("/img?u="))
        self.assertEqual(img_proxy("https://example.com/demo.jpg"), "https://example.com/demo.jpg")


class VideoFallbackTest(unittest.TestCase):
    def _post(self, stream_url: str) -> dict:
        raw = {
            "page_info": {
                "object_type": "video",
                "page_url": "https://video.weibo.com/show?fid=demo",
                "media_info": {
                    "stream_url_hd": stream_url,
                    "h5_url": "https://video.weibo.com/show?fid=demo",
                },
            }
        }
        return {"is_video": 1, "raw_json": json.dumps(raw)}

    def test_expired_direct_url_uses_fallback(self) -> None:
        info = video_info(self._post("https://video.example/demo.mp4?Expires=1"))
        self.assertIsNotNone(info)
        self.assertFalse(info["playable"])
        self.assertEqual(info["page_url"], "https://video.weibo.com/show?fid=demo")

    def test_future_direct_url_is_playable(self) -> None:
        expires = int(time.time()) + 3600
        info = video_info(self._post(f"https://video.example/demo.mp4?Expires={expires}"))
        self.assertIsNotNone(info)
        self.assertTrue(info["playable"])


if __name__ == "__main__":
    unittest.main()
