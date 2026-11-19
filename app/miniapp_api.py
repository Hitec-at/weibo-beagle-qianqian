"""微信原生小程序使用的只读 JSON 接口。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response

from app.miniapp_repository import (
    FEED_CATEGORIES,
    FEED_LABELS,
    MINIAPP_SORTS,
    POST_SORTS,
    SEARCH_SORT_LABELS,
    SEARCH_SORTS,
    get_repository,
)
from filters import search_terms
from weibo_links import original_url

router = APIRouter(prefix="/api/v1", tags=["miniapp"])
CST = timezone(timedelta(hours=8))
MAX_PAGE_SIZE = 20
MAX_QUERY_LENGTH = 100
MAX_IMAGE_BYTES = 900 * 1024


def _ok(data: Any) -> dict:
    return {"ok": True, "data": data}


def _fmt_time(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=CST).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _pics(value: Any, limit: int) -> list[dict]:
    try:
        items = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    result: list[dict] = []
    for item in items:
        if len(result) >= limit:
            break
        if not isinstance(item, dict):
            continue
        thumb = str(item.get("thumb") or "")
        full = str(item.get("url") or thumb)
        if thumb or full:
            result.append(
                {
                    "thumb": _https(thumb or full),
                    "url": _https(full or thumb),
                }
            )
    return result


def _https(url: str) -> str:
    value = str(url or "").strip()
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("http://"):
        return "https://" + value.removeprefix("http://")
    return value


def _main_helpers():
    # main 在导入时注册本模块；延迟导入避免循环依赖。
    from app import main

    return main


def _serialise_post(post: dict, *, detail: bool = False) -> dict:
    helpers = _main_helpers()
    source = helpers.source_of(post) if detail else None
    video = helpers.video_info(post) if detail else None
    text = str(post.get("text_plain") or "").strip()
    data = {
        "id": str(post.get("id") or ""),
        "mid": str(post.get("mid") or ""),
        "uid": str(post.get("uid") or ""),
        "author": str(post.get("screen_name") or "比格钱钱"),
        "created_at": str(post.get("created_at") or ""),
        "time_text": _fmt_time(post.get("ts")),
        "ts": int(post.get("ts") or 0),
        "text": text[:500] if detail else text[:140],
        "truncated": len(text) > (500 if detail else 140),
        "counts": {
            "reposts": int(post.get("reposts_count") or 0),
            "comments": int(post.get("comments_count") or 0),
            "likes": int(post.get("attitudes_count") or 0),
        },
        "engagement": (
            int(post.get("attitudes_count") or 0)
            + 5 * int(post.get("reposts_count") or 0)
            + 3 * int(post.get("comments_count") or 0)
        ),
        "original_url": original_url(post),
        "pics": _pics(post.get("pics_json"), 9 if detail else 3),
        "has_pics": bool(int(post.get("has_pics") or 0)),
        "is_video": bool(int(post.get("is_video") or 0)),
        "retweeted_user": str(post.get("retweeted_user") or ""),
    }
    if detail:
        labels = get_repository().post_labels(str(post["id"]))
        data["categories"] = labels["categories"]
        data["memes"] = labels["memes"]
        data["video"] = (
            {
                "source": int(video.get("source") or 0),
                "page_url": str(video.get("page_url") or ""),
                "poster": str(video.get("poster") or ""),
            }
            if video
            else None
        )
        if source:
            source_pics = source.get("pics") or []
            source_video = source.get("video")
            data["source"] = {
                "user": str(source.get("user") or ""),
                "text": str(source.get("text") or "")[:500],
                "pics": [
                    {
                        "thumb": _https(pic.get("thumb") or pic.get("url") or ""),
                        "url": _https(pic.get("url") or pic.get("thumb") or ""),
                    }
                    for pic in source_pics[:6]
                    if isinstance(pic, dict)
                ],
                "video": (
                    {
                        "page_url": str(source_video.get("page_url") or ""),
                        "poster": str(source_video.get("poster") or ""),
                    }
                    if source_video
                    else None
                ),
            }
        else:
            data["source"] = None
    return data


def _validate_feed(feed: str) -> str:
    if feed not in FEED_LABELS:
        raise HTTPException(status_code=400, detail="未知内容分类")
    return feed


def _validate_page(page: int, page_size: int) -> tuple[int, int]:
    return max(1, int(page or 1)), max(1, min(MAX_PAGE_SIZE, int(page_size or 20)))


@router.get("/bootstrap")
def bootstrap() -> dict:
    repo = get_repository()
    counts = repo.category_counts()
    time_range = repo.time_range()
    total_posts = repo.count_posts()
    feeds = []
    for feed, title in FEED_LABELS.items():
        category = FEED_CATEGORIES.get(feed, "")
        feeds.append(
            {
                "key": feed,
                "title": title,
                "count": total_posts if not category else counts.get(category, 0),
            }
        )
    return _ok(
        {
            "api_version": "v1",
            "feeds": feeds,
            "sorts": [
                {"value": value, "label": label}
                for value, label in MINIAPP_SORTS.items()
            ],
            "default_sort": "hot",
            "time_range": {
                **time_range,
                "min_text": _fmt_time(time_range["min_ts"]),
                "max_text": _fmt_time(time_range["max_ts"]),
            },
            "notice": "仅索引公开微博，短引文与缩略图预览，原文版权归原作者。",
        }
    )


@router.get("/posts")
def list_posts(
    feed: str = Query("home"),
    meme: str = Query("", max_length=80),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
    sort: str = Query("hot"),
) -> dict:
    feed = _validate_feed(feed)
    if sort not in POST_SORTS:
        raise HTTPException(status_code=400, detail="未知排序")
    page, page_size = _validate_page(page, page_size)
    result = get_repository().list_posts(
        feed=feed,
        meme=meme.strip(),
        page=page,
        page_size=page_size,
        sort=sort,
    )
    result["items"] = [_serialise_post(post) for post in result["items"]]
    result["feed"] = feed
    result["sort"] = sort
    return _ok(result)


@router.get("/search")
def search_posts(
    q: str = Query("", max_length=MAX_QUERY_LENGTH),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
    sort: str = Query("relevance"),
) -> dict:
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="请输入搜索词")
    if sort not in SEARCH_SORTS:
        raise HTTPException(status_code=400, detail="未知排序")
    page, page_size = _validate_page(page, page_size)
    result = get_repository().search_posts(
        query=query, page=page, page_size=page_size, sort=sort
    )
    result["items"] = [_serialise_post(post) for post in result["items"]]
    result.update(
        {
            "query": query,
            "sort": sort,
            "sorts": [
                {"value": value, "label": label}
                for value, label in SEARCH_SORT_LABELS.items()
            ],
            "terms": search_terms(query),
        }
    )
    return _ok(result)


@router.get("/posts/{post_id}")
def get_post_detail(post_id: str) -> dict:
    post = get_repository().get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="微博不存在")
    return _ok(_serialise_post(post, detail=True))


@router.get("/posts/{post_id}/video")
def get_post_video(post_id: str, source: int = Query(0, ge=0, le=1)) -> dict:
    post = get_repository().get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="微博不存在")
    helpers = _main_helpers()
    info = helpers.resolve_video(post, bool(source))
    if not info:
        raise HTTPException(status_code=502, detail="视频暂不可用")
    return _ok(
        {
            "url": info.get("url") or "",
            "poster": info.get("poster") or "",
            "expires_at": int(info.get("expires_at") or 0),
        }
    )


@router.get("/media/image")
def get_proxied_image(u: str = Query("", max_length=2048)):
    if not u:
        raise HTTPException(status_code=400, detail="缺少图片地址")
    helpers = _main_helpers()
    image = helpers.img(u=u)
    if len(image.body) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="图片超过小程序单次返回上限")
    return Response(
        content=image.body,
        media_type=image.media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/memes")
def list_memes(kind: str = Query("behavior")) -> dict:
    if kind not in {"behavior", "fan_fiction"}:
        raise HTTPException(status_code=400, detail="未知条目类型")
    return _ok(
        {
            "kind": kind,
            "items": get_repository().list_memes(kind),
        }
    )


@router.get("/memes/{slug}")
def get_meme_detail(slug: str) -> dict:
    meme = get_repository().get_meme(slug)
    if not meme:
        raise HTTPException(status_code=404, detail="分类条目不存在")
    return _ok(meme)


@router.get("/healthz")
def miniapp_healthz() -> dict:
    repo = get_repository()
    return _ok(
        {
            "backend": repo.backend_name,
            "posts": repo.count_posts(),
        }
    )
