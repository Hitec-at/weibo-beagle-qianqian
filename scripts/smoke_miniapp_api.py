"""本地或云托管公网测试入口的小程序只读 API 验收。"""
from __future__ import annotations

import json
import sys
from urllib.parse import quote
from urllib.request import urlopen

MAX_RESPONSE_BYTES = 1000 * 1024


def get(base_url: str, path: str) -> dict:
    with urlopen(base_url.rstrip("/") + path, timeout=30) as response:
        body = response.read()
        if response.status != 200:
            raise RuntimeError(f"{path} 返回 {response.status}")
    if len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError(f"{path} 响应为 {len(body)} 字节，超过小程序上限")
    payload = json.loads(body)
    if payload.get("ok") is not True or "data" not in payload:
        raise RuntimeError(f"{path} 响应格式错误")
    if "raw_json" in body.decode("utf-8"):
        raise RuntimeError(f"{path} 泄漏 raw_json")
    return payload["data"]


def main(argv: list[str]) -> int:
    base_url = argv[1] if len(argv) > 1 else "http://127.0.0.1:8000"
    bootstrap = get(base_url, "/api/v1/bootstrap")
    if not bootstrap.get("feeds"):
        raise RuntimeError("bootstrap 没有内容入口")

    posts = get(base_url, "/api/v1/posts?feed=home&page=1&page_size=20&sort=hot")
    if not posts.get("items"):
        raise RuntimeError("首页没有微博")
    first = posts["items"][0]
    for field in ("id", "text", "counts", "original_url", "pics"):
        if field not in first:
            raise RuntimeError(f"列表项缺少字段：{field}")

    detail = get(base_url, "/api/v1/posts/" + quote(first["id"], safe=""))
    if detail["id"] != first["id"]:
        raise RuntimeError("详情返回了错误的微博")

    search = get(
        base_url,
        "/api/v1/search?q=" + quote("钱钱", safe="") + "&sort=relevance",
    )
    if "total" not in search:
        raise RuntimeError("搜索没有返回总数")

    print(
        "miniapp_api=ok "
        f"posts={bootstrap['feeds'][0]['count']} "
        f"search_total={search['total']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except Exception as exc:
        print(f"小程序 API 验收失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
