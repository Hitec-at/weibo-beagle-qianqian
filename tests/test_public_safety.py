from __future__ import annotations

import json
import time
import threading
import unittest
from unittest.mock import Mock, patch

import app.main as main_module
from app.main import img_proxy, is_sina_image_url, video_info
from starlette.requests import Request


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


class VideoInfoTest(unittest.TestCase):
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

    def test_expired_direct_url_still_returns_refreshable_video_info(self) -> None:
        info = video_info(self._post("https://video.example/demo.mp4?Expires=1"))
        self.assertIsNotNone(info)
        self.assertEqual(info["source"], 0)
        self.assertEqual(info["page_url"], "https://video.weibo.com/show?fid=demo")

    def test_future_direct_url_is_playable(self) -> None:
        expires = int(time.time()) + 3600
        info = video_info(self._post(f"https://video.example/demo.mp4?Expires={expires}"))
        self.assertIsNotNone(info)
        self.assertEqual(info["source"], 0)

    def test_mix_media_video_is_detected(self) -> None:
        raw = {
            "mix_media_info": {
                "items": [
                    {
                        "type": "video",
                        "data": {
                            "object_type": "video",
                            "page_pic": "https://wx1.sinaimg.cn/demo.jpg",
                            "media_info": {
                                "stream_url": "https://video.example/demo.mp4?Expires=1",
                                "h5_url": "https://video.weibo.com/show?fid=demo",
                            },
                        },
                    }
                ]
            }
        }
        info = video_info({"is_video": 1, "raw_json": json.dumps(raw)})
        self.assertIsNotNone(info)
        self.assertEqual(info["source"], 0)
        self.assertEqual(info["poster"], "https://wx1.sinaimg.cn/demo.jpg")


class GuestVideoPlaybackTest(unittest.TestCase):
    def setUp(self) -> None:
        main_module._VIDEO_FAILURES.clear()
        with main_module._VIDEO_LOCKS_GUARD:
            main_module._VIDEO_LOCKS.clear()

    @staticmethod
    def _ordinary_node(media_id: str = "5361924561371184") -> dict:
        return {
            "page_info": {
                "object_type": "video",
                "page_url": f"https://h5.video.weibo.com/tv/show/1034:{media_id}",
                "media_info": {
                    "media_id": media_id,
                    "stream_url": "https://f.video.weibocdn.com/old.mp4?Expires=1",
                },
            }
        }

    @staticmethod
    def _mixed_node(media_id: str = "5235472251944992") -> dict:
        return {
            "mix_media_info": {
                "items": [
                    {
                        "type": "video",
                        "data": {
                            "object_type": "video",
                            "object_id": f"1034:{media_id}",
                            "media_info": {"media_id": media_id},
                        },
                    }
                ]
            }
        }

    def test_extracts_video_oid_from_rare_and_mixed_media(self) -> None:
        self.assertEqual(
            main_module._video_oid(self._ordinary_node()),
            "1034:5361924561371184",
        )
        self.assertEqual(
            main_module._video_oid(self._mixed_node()),
            "1034:5235472251944992",
        )

    def test_guest_endpoint_uses_raw_data_body_and_returns_https_url(self) -> None:
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "code": "100000",
            "data": {
                "Component_Play_Playinfo": {
                    "urls": {
                        "高清 720P": "//f.video.weibocdn.com/720.mp4?Expires=2000000000",
                        "高清 1080P": "http://f.video.weibocdn.com/1080.mp4?Expires=2000000000",
                    },
                    "cover_image": "https://wx1.sinaimg.cn/demo.jpg",
                }
            },
        }
        with patch.object(main_module.requests, "post", return_value=response) as post:
            result = main_module._fetch_guest_video(self._ordinary_node())

        self.assertIsNotNone(result)
        self.assertEqual(
            result["url"],
            "https://f.video.weibocdn.com/1080.mp4?Expires=2000000000",
        )
        self.assertEqual(result["poster"], "https://wx1.sinaimg.cn/demo.jpg")
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["params"], {"page": "/tv/show/1034:5361924561371184"})
        self.assertEqual(
            kwargs["data"],
            'data={"Component_Play_Playinfo":{"oid":"1034:5361924561371184"}}',
        )
        self.assertNotIn("Cookie", kwargs["headers"])

    def test_guest_endpoint_rejects_wrong_code_or_non_weibo_url(self) -> None:
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "code": "100001",
            "data": {"Component_Play_Playinfo": {}},
        }
        with patch.object(main_module.requests, "post", return_value=response):
            self.assertIsNone(
                main_module._fetch_guest_video(self._ordinary_node())
            )

        response.json.return_value = {
            "code": "100000",
            "data": {
                "Component_Play_Playinfo": {
                    "urls": {"高清 1080P": "https://evil.example/video.mp4"}
                }
            },
        }
        with patch.object(main_module.requests, "post", return_value=response):
            self.assertIsNone(
                main_module._fetch_guest_video(self._ordinary_node())
            )

    def test_concurrent_requests_refresh_only_once(self) -> None:
        post = {
            "id": "100",
            "is_video": 1,
            "raw_json": json.dumps(self._ordinary_node()),
        }
        cache: dict[str, dict] = {}
        calls = 0
        calls_lock = threading.Lock()

        def get_cache(key):
            return cache.get(key)

        def set_cache(key, url, poster, expires_at):
            cache[key] = {
                "key": key,
                "url": url,
                "poster": poster,
                "expires_at": expires_at,
            }

        def fetch_guest(node):
            nonlocal calls
            time.sleep(0.05)
            with calls_lock:
                calls += 1
            return {
                "url": "https://f.video.weibocdn.com/fresh.mp4?Expires=2000000000",
                "poster": "",
                "expires_at": 2000000000,
            }

        results = []
        with (
            patch.object(main_module.store, "get_video_cache", side_effect=get_cache),
            patch.object(main_module.store, "set_video_cache", side_effect=set_cache),
            patch.object(main_module, "_fetch_guest_video", side_effect=fetch_guest),
        ):
            threads = [
                threading.Thread(
                    target=lambda: results.append(
                        main_module.resolve_video(post, False)
                    )
                )
                for _ in range(4)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(calls, 1)
        self.assertEqual(len(results), 4)
        self.assertTrue(all(result and result["url"].endswith("fresh.mp4?Expires=2000000000") for result in results))

    def test_failed_guest_refresh_is_cooled_down_and_direct_url_is_stale_fallback(self) -> None:
        post = {
            "id": "101",
            "is_video": 1,
            "raw_json": json.dumps(self._ordinary_node()),
        }
        with (
            patch.object(main_module.store, "get_video_cache", return_value=None),
            patch.object(main_module.store, "set_video_cache"),
            patch.object(main_module, "_fetch_guest_video", return_value=None) as fetch_guest,
            patch.object(main_module.config, "ENABLE_VIDEO_REFRESH", False),
        ):
            first = main_module.resolve_video(post, False)
            second = main_module.resolve_video(post, False)

        self.assertEqual(fetch_guest.call_count, 1)
        self.assertEqual(first["url"], "https://f.video.weibocdn.com/old.mp4?Expires=1")
        self.assertEqual(second["url"], first["url"])

    def test_forced_guest_refresh_recovers_from_transient_failure(self) -> None:
        post = {
            "id": "102",
            "is_video": 1,
            "raw_json": json.dumps(self._ordinary_node()),
        }
        fresh = {
            "url": "https://f.video.weibocdn.com/recovered.mp4?Expires=2000000000",
            "poster": "",
            "expires_at": 2000000000,
        }
        with (
            patch.object(main_module.store, "get_video_cache", return_value=None),
            patch.object(main_module.store, "set_video_cache"),
            patch.object(
                main_module,
                "_fetch_guest_video",
                side_effect=[None, fresh],
            ) as fetch_guest,
            patch.object(main_module.config, "ENABLE_VIDEO_REFRESH", False),
        ):
            first = main_module.resolve_video(post, False)
            cooled_down = main_module.resolve_video(post, False)
            recovered = main_module.resolve_video(
                post, False, force_guest_refresh=True
            )

        self.assertEqual(fetch_guest.call_count, 2)
        self.assertEqual(
            first["url"],
            "https://f.video.weibocdn.com/old.mp4?Expires=1",
        )
        self.assertEqual(cooled_down["url"], first["url"])
        self.assertEqual(
            recovered["url"],
            "https://f.video.weibocdn.com/recovered.mp4?Expires=2000000000",
        )
        self.assertNotIn("102:self", main_module._VIDEO_FAILURES)

    def test_video_route_forces_one_guest_retry_after_upstream_failure(self) -> None:
        post = {
            "id": "103",
            "is_video": 1,
            "raw_json": json.dumps(self._ordinary_node()),
        }
        stale = {
            "url": "https://f.video.weibocdn.com/old.mp4?Expires=1",
            "poster": "",
            "expires_at": 1,
        }
        fresh = {
            "url": "https://f.video.weibocdn.com/fresh.mp4?Expires=2000000000",
            "poster": "",
            "expires_at": 2000000000,
        }
        failed = Mock(status_code=403)
        recovered = Mock(
            status_code=206,
            headers={
                "Content-Type": "video/mp4",
                "Content-Range": "bytes 0-0/1",
            },
        )
        recovered.iter_content.return_value = [b"x"]
        request = Request({"type": "http", "method": "GET", "headers": []})

        with (
            patch.object(main_module.store, "get_post", return_value=post),
            patch.object(
                main_module,
                "resolve_video",
                side_effect=[stale, fresh],
            ) as resolve_video,
            patch.object(
                main_module.requests,
                "get",
                side_effect=[failed, recovered],
            ),
            patch.object(main_module, "_ip_fallback_url", return_value=""),
            patch.object(main_module.store, "invalidate_video_cache"),
            patch.object(main_module.config, "ENABLE_GUEST_VIDEO_REFRESH", True),
            patch.object(main_module.time, "sleep"),
        ):
            response = main_module.video("103", request, source=0)

        self.assertEqual(response.status_code, 206)
        self.assertEqual(resolve_video.call_count, 2)
        self.assertFalse(
            resolve_video.call_args_list[0].kwargs["force_guest_refresh"]
        )
        self.assertTrue(
            resolve_video.call_args_list[1].kwargs["force_guest_refresh"]
        )

    def test_template_exposes_fallback_links(self) -> None:
        macro = main_module.templates.env.get_template(
            "_video_player.html"
        ).module.video_player
        rendered = macro(
            {"id": "100", "uid": "1", "mid": "100"},
            {
                "source": 0,
                "poster": "",
                "page_url": "https://h5.video.weibo.com/tv/show/1034:100",
            },
        )
        self.assertIn('class="video-error" hidden', rendered)
        self.assertIn("https://weibo.com/1/100", rendered)
        self.assertIn("https://h5.video.weibo.com/tv/show/1034:100", rendered)


if __name__ == "__main__":
    unittest.main()
