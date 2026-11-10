from __future__ import annotations

import html
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import config
import filters
import store

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="比格钱钱 微博检索")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

store.init_db()

CST = timezone(timedelta(hours=8))
PAGE_SIZE = 20
PHOTO_PAGE_SIZE = 24


def pagination_context(
    request: Request, total: int, page: int, per_page: int, sort: str | None = None
) -> dict:
    pages = max(1, (total + per_page - 1) // per_page)
    current = max(1, min(page, pages))

    def page_url(number: int) -> str:
        params = dict(request.query_params)
        params.pop("page", None)
        if sort is not None:
            params["sort"] = sort
        params["page"] = str(number)
        return f"{request.url.path}?{urlencode(params)}"

    return {
        "page": current,
        "pages": pages,
        "total": total,
        "prev_url": page_url(current - 1) if current > 1 else "",
        "next_url": page_url(current + 1) if current < pages else "",
    }


def sort_control(
    request: Request, options: list[tuple[str, str]], default: str
) -> dict:
    allowed = {value for value, _ in options}
    requested = request.query_params.get("sort", default)
    current = requested if requested in allowed else default
    base = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key not in {"sort", "page"}
    ]
    links = []
    for value, label in options:
        params = list(base)
        if value != default:
            params.append(("sort", value))
        url = request.url.path + (("?" + urlencode(params)) if params else "")
        links.append(
            {
                "value": value,
                "label": label,
                "url": url,
                "active": value == current,
            }
        )
    return {"current": current, "options": links}


def original_url(p: dict) -> str:
    if p.get("mid"):
        return f"https://weibo.com/{p['uid']}/{p['mid']}"
    return f"https://m.weibo.cn/detail/{p['id']}"


def excerpt(text: str, n: int = 80) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    return t[:n] + ("…" if len(t) > n else "")


def pics_of(p: dict) -> list[dict]:
    try:
        pics = json.loads(p.get("pics_json") or "[]")
    except ValueError:
        return []
    return [pic for pic in pics if pic.get("thumb") or pic.get("url")]


def _node_pics(node: dict) -> list[dict]:
    pics: list[dict] = []
    seen: set[str] = set()
    pic_infos = node.get("pic_infos") or {}
    for pid in node.get("pic_ids") or []:
        info = pic_infos.get(pid) or {}
        thumb = (info.get("bmiddle") or info.get("wap180") or info.get("thumbnail") or {}).get("url")
        large = (info.get("large") or info.get("mw2000") or info.get("original") or {}).get("url")
        if thumb or large:
            pics.append({"pid": pid, "thumb": thumb, "url": large})
            seen.add(pid)
    for item in (node.get("mix_media_info") or {}).get("items") or []:
        if item.get("type") != "pic":
            continue
        data = item.get("data") or {}
        pid = str(data.get("pic_id") or item.get("id") or "")
        thumb = (data.get("bmiddle") or data.get("thumbnail") or {}).get("url")
        large = (
            data.get("large")
            or data.get("mw2000")
            or data.get("original")
            or data.get("largest")
            or {}
        ).get("url")
        if pid not in seen and (thumb or large):
            pics.append({"pid": pid, "thumb": thumb, "url": large})
            seen.add(pid)
    return pics


def source_of(p: dict) -> dict | None:
    """从 raw_json 解析被转发微博（源微博）的正文、图片与视频。"""
    try:
        raw = json.loads(p.get("raw_json") or "{}")
    except ValueError:
        return None
    rt = raw.get("retweeted_status") or {}
    if not rt:
        return None
    text = html.unescape(re.sub(r"<[^>]+>", "", rt.get("text_raw") or rt.get("text") or ""))
    text = re.sub(r"\s+", " ", text).strip()
    pics = _node_pics(rt)
    video = _video_meta(rt)
    if not text and not pics and not video:
        return None
    return {
        "user": (rt.get("user") or {}).get("screen_name") or "",
        "text": text,
        "pics": pics,
        "video": video,
    }


def engagement(p: dict) -> int:
    return int(p["attitudes_count"]) + 5 * int(p["reposts_count"]) + 3 * int(p["comments_count"])


def fmt_time(ts) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=CST).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


_IMG_CACHE: dict[str, tuple[bytes, str]] = {}
_IMG_CACHE_LOCK = threading.Lock()
_IMG_CACHE_BYTES = 0
_IMG_CACHE_MAX_BYTES = config.env_int("IMG_CACHE_MAX_BYTES", 24 * 1024 * 1024)
_IMG_ITEM_MAX_BYTES = config.env_int("IMG_ITEM_MAX_BYTES", 1536 * 1024)
_IMG_MAX_BYTES = config.env_int("IMG_MAX_BYTES", 6 * 1024 * 1024)


def is_sina_image_url(url: str) -> bool:
    """仅允许新浪图床域名，防止图片代理被用作 SSRF 或开放代理。"""
    try:
        parsed = urlsplit((url or "").strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme in {"http", "https"}
        and not parsed.username
        and not parsed.password
        and (host == "sinaimg.cn" or host.endswith(".sinaimg.cn"))
    )


def img_proxy(url: str) -> str:
    """微博图床防盗链：前端走 /img 代理，后端带 weibo.com Referer 转发，仅内存缓存不落盘。"""
    u = (url or "").strip()
    if is_sina_image_url(u):
        return f"/img?u={quote(u, safe='')}"
    return u


VIDEO_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_VIDEO_LOCK = threading.Lock()


def _video_data(node: dict) -> dict:
    pi = node.get("page_info") or {}
    mi = pi.get("media_info") or {}
    if pi.get("object_type") == "video" or mi.get("stream_url") or mi.get("stream_url_hd"):
        return pi
    for item in (node.get("mix_media_info") or {}).get("items") or []:
        data = item.get("data") or {}
        media = data.get("media_info") or {}
        if (
            item.get("type") == "video"
            or data.get("object_type") == "video"
            or media.get("stream_url")
            or media.get("stream_url_hd")
        ):
            return data
    return {}


def _video_meta(node: dict) -> dict | None:
    pi = _video_data(node)
    if not pi:
        return None
    mi = pi.get("media_info") or {}
    url = (
        mi.get("stream_url_hd")
        or mi.get("stream_url")
        or mi.get("mp4_hd_url")
        or mi.get("mp4_sd_url")
        or ""
    )
    if pi.get("object_type") != "video" and not url:
        return None
    big = mi.get("big_pic_info") or {}
    poster = (big.get("pic_big") or {}).get("url") if isinstance(big, dict) else ""
    if not poster:
        poster = pi.get("page_pic") or ""
    return {
        "poster": poster,
        "page_url": mi.get("h5_url") or pi.get("page_url") or "",
    }


def video_info(p: dict) -> dict | None:
    """列表卡片用：返回本人视频或转发源视频的播放信息。"""
    try:
        raw = json.loads(p.get("raw_json") or "{}")
    except ValueError:
        return None
    rt = raw.get("retweeted_status") or {}
    meta = _video_meta(rt)
    if meta:
        return {"source": 1, **meta}
    if int(p.get("is_video") or 0):
        meta = _video_meta(raw) or {}
        return {
            "source": 0,
            "poster": meta.get("poster") or "",
            "page_url": meta.get("page_url") or "",
        }
    meta = _video_meta(raw)
    if meta:
        return {"source": 0, **meta}
    return None


def _video_url_of(node: dict) -> str:
    mi = (_video_data(node).get("media_info") or {})
    return (
        mi.get("stream_url_hd")
        or mi.get("stream_url")
        or mi.get("mp4_hd_url")
        or mi.get("mp4_sd_url")
        or ""
    )


def _expires_at(url: str) -> int:
    match = re.search(r"[?&]Expires=(\d+)", url or "")
    return int(match.group(1)) if match else 0


def _video_node(p: dict, source: bool) -> dict:
    try:
        raw = json.loads(p.get("raw_json") or "{}")
    except ValueError:
        return {}
    return (raw.get("retweeted_status") or {}) if source else raw


def _cache_video(key: str, url: str, poster: str) -> dict:
    expires_at = _expires_at(url) or int(time.time()) + 3600
    store.set_video_cache(key, url, poster, expires_at)
    return {"url": url, "poster": poster, "expires_at": expires_at}


def _refresh_video(p: dict, source: bool, key: str) -> dict | None:
    if not config.ENABLE_VIDEO_REFRESH:
        return None
    node = _video_node(p, source)
    rid = str(node.get("idstr") or node.get("id") or "")
    if not rid:
        return None
    from fetch_weibo import SHOW_API, RateLimiter, make_session, request_json

    try:
        session = make_session()
        limiter = RateLimiter()
        data = request_json(session, limiter, SHOW_API, {"id": rid})
    except Exception:
        return None
    fresh_node = data if isinstance(data, dict) else {}
    url = _video_url_of(fresh_node)
    if not url:
        return None
    meta = _video_meta(fresh_node) or {}
    return _cache_video(key, url, meta.get("poster") or "")


def resolve_video(p: dict, source: bool) -> dict | None:
    key = f"{p['id']}:{'rt' if source else 'self'}"
    now = int(time.time())
    cached = store.get_video_cache(key)
    if cached and int(cached["expires_at"]) > now + 60:
        return cached
    node = _video_node(p, source)
    url = _video_url_of(node)
    if url and _expires_at(url) > now + 60:
        meta = _video_meta(node) or {}
        return _cache_video(key, url, meta.get("poster") or "")
    with _VIDEO_LOCK:
        cached = store.get_video_cache(key)
        if cached and int(cached["expires_at"]) > now + 60:
            return cached
        return _refresh_video(p, source, key)


@app.get("/img")
def img(u: str = ""):
    u = (u or "").strip()
    if not is_sina_image_url(u):
        raise HTTPException(status_code=400, detail="仅允许代理新浪图床")
    global _IMG_CACHE_BYTES
    with _IMG_CACHE_LOCK:
        cached = _IMG_CACHE.get(u)
    if cached:
        body, ctype = cached
        return Response(
            content=body,
            media_type=ctype,
            headers={"Cache-Control": "public, max-age=86400"},
        )
    try:
        resp = requests.get(
            u,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://weibo.com/",
            },
            timeout=15,
            allow_redirects=False,
            stream=True,
        )
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="图片拉取失败")
    if resp.status_code != 200:
        resp.close()
        raise HTTPException(status_code=502, detail="图片不可用")
    ctype = resp.headers.get("Content-Type", "")
    if not ctype.lower().startswith("image/"):
        resp.close()
        raise HTTPException(status_code=502, detail="上游返回内容不是图片")
    try:
        declared_size = int(resp.headers.get("Content-Length") or 0)
    except ValueError:
        declared_size = 0
    if declared_size > _IMG_MAX_BYTES:
        resp.close()
        raise HTTPException(status_code=502, detail="图片体积超过上限")
    chunks: list[bytes] = []
    size = 0
    try:
        for chunk in resp.iter_content(64 * 1024):
            if not chunk:
                continue
            size += len(chunk)
            if size > _IMG_MAX_BYTES:
                raise HTTPException(status_code=502, detail="图片体积超过上限")
            chunks.append(chunk)
    finally:
        resp.close()
    body = b"".join(chunks)
    if 0 < len(body) <= _IMG_ITEM_MAX_BYTES:
        with _IMG_CACHE_LOCK:
            old = _IMG_CACHE.pop(u, None)
            if old:
                _IMG_CACHE_BYTES -= len(old[0])
            while _IMG_CACHE and _IMG_CACHE_BYTES + len(body) > _IMG_CACHE_MAX_BYTES:
                _, evicted = _IMG_CACHE.popitem()
                _IMG_CACHE_BYTES -= len(evicted[0])
            _IMG_CACHE[u] = (body, ctype)
            _IMG_CACHE_BYTES += len(body)
    return Response(
        content=body,
        media_type=ctype,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok", "posts": store.count_posts()}


@app.get("/video/{pid}")
def video(pid: str, request: Request, source: int = 0):
    p = store.get_post(pid)
    if not p:
        raise HTTPException(status_code=404, detail="微博不存在")
    is_source = bool(source)
    key = f"{pid}:{'rt' if is_source else 'self'}"
    headers = {"User-Agent": VIDEO_UA, "Referer": "https://weibo.com/"}
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header

    def open_upstream():
        info = resolve_video(p, is_source)
        if not info:
            return None
        try:
            return requests.get(info["url"], headers=headers, stream=True, timeout=30)
        except requests.RequestException:
            return None

    upstream = open_upstream()
    if upstream is None:
        store.invalidate_video_cache(key)
        upstream = open_upstream()
        if upstream is None:
            raise HTTPException(status_code=502, detail="视频暂不可用")
    if upstream.status_code not in (200, 206):
        upstream.close()
        store.invalidate_video_cache(key)
        upstream = open_upstream()
        if upstream is None:
            raise HTTPException(status_code=502, detail="视频暂不可用")
    if upstream.status_code not in (200, 206):
        upstream.close()
        raise HTTPException(status_code=502, detail="视频源不可用")
    out_headers = {}
    for name in ("Content-Length", "Content-Range", "Accept-Ranges"):
        if upstream.headers.get(name):
            out_headers[name] = upstream.headers[name]

    def body():
        try:
            for chunk in upstream.iter_content(64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("Content-Type", "video/mp4"),
        headers=out_headers,
    )


templates.env.globals.update(
    excerpt=excerpt,
    engagement=engagement,
    pics_of=pics_of,
    source_of=source_of,
    video_info=video_info,
    original_url=original_url,
    fmt_time=fmt_time,
    img_proxy=img_proxy,
)


@app.get("/")
def home(request: Request, page: int = 1):
    total = store.count_posts()
    pagination = pagination_context(request, total, page, PAGE_SIZE)
    rows = store.query_posts(
        "1=1", (), limit=PAGE_SIZE, order="hot", offset=(pagination["page"] - 1) * PAGE_SIZE
    )
    return templates.TemplateResponse(
        request,
        "list.html",
        {
            "title": "热门推荐",
            "posts": rows,
            "grid": False,
            "pagination": pagination,
        },
    )


@app.get("/memes")
def meme_index(request: Request):
    rows = store.list_memes()
    return templates.TemplateResponse(
        request,
        "meme_index.html",
        {
            "title": "N宗罪",
            "memes": rows,
        },
    )


@app.get("/memes/all")
def memes_all(request: Request, page: int = 1):
    where, params = filters.category_where("memes")
    total = store.count_posts(where, params)
    options = [("hot", "热门"), ("latest", "最新"), ("earliest", "最早")]
    sort_control_data = sort_control(request, options, "hot")
    sort_key = sort_control_data["current"]
    pagination = pagination_context(request, total, page, PAGE_SIZE, sort_key)
    rows = store.query_posts(
        where, params, limit=PAGE_SIZE, order=sort_key, offset=(pagination["page"] - 1) * PAGE_SIZE
    )
    return templates.TemplateResponse(
        request,
        "list.html",
        {
            "title": "全部罪证",
            "posts": rows,
            "grid": False,
            "pagination": pagination,
            "sort_control": sort_control_data,
        },
    )


@app.get("/meme/{slug}")
def meme_detail(request: Request, slug: str, page: int = 1):
    meme = store.get_meme(slug)
    if not meme:
        raise HTTPException(status_code=404, detail="罪名专题不存在")
    total = store.count_meme_posts(slug)
    options = [("earliest", "时间线"), ("latest", "最新"), ("hot", "热门")]
    sort_control_data = sort_control(request, options, "earliest")
    sort_key = sort_control_data["current"]
    pagination = pagination_context(request, total, page, PAGE_SIZE, sort_key)
    posts = store.query_meme_posts(
        slug, limit=PAGE_SIZE, order=sort_key, offset=(pagination["page"] - 1) * PAGE_SIZE
    )
    return templates.TemplateResponse(
        request,
        "meme_detail.html",
        {
            "title": meme["name"],
            "meme": meme,
            "posts": posts,
            "pagination": pagination,
            "sort_control": sort_control_data,
        },
    )


@app.get("/interactions")
def interactions(request: Request, page: int = 1):
    where, params = filters.category_where("interactions")
    total = store.count_posts(where, params)
    options = [("latest", "最新"), ("hot", "热门")]
    sort_control_data = sort_control(request, options, "latest")
    sort_key = sort_control_data["current"]
    pagination = pagination_context(request, total, page, PAGE_SIZE, sort_key)
    rows = store.query_posts(
        where, params, limit=PAGE_SIZE, order=sort_key, offset=(pagination["page"] - 1) * PAGE_SIZE
    )
    return templates.TemplateResponse(
        request,
        "list.html",
        {
            "title": "与 @比格犬受害者联盟 的互动",
            "posts": rows,
            "grid": False,
            "show_source": True,
            "pagination": pagination,
            "sort_control": sort_control_data,
        },
    )


@app.get("/photos")
def photos(request: Request, page: int = 1):
    where, params = filters.category_where("photos")
    total = store.count_posts(where, params)
    options = [("hot", "热门"), ("latest", "最新"), ("likes", "最多点赞")]
    sort_control_data = sort_control(request, options, "hot")
    sort_key = sort_control_data["current"]
    pagination = pagination_context(request, total, page, PHOTO_PAGE_SIZE, sort_key)
    rows = store.query_posts(
        where,
        params,
        limit=PHOTO_PAGE_SIZE,
        order=sort_key,
        offset=(pagination["page"] - 1) * PHOTO_PAGE_SIZE,
    )
    return templates.TemplateResponse(
        request,
        "list.html",
        {
            "title": "高清图",
            "posts": rows,
            "grid": True,
            "pagination": pagination,
            "sort_control": sort_control_data,
        },
    )


@app.get("/search")
def search(request: Request, q: str = "", page: int = 1):
    q = q.strip()
    if not q:
        return RedirectResponse("/")
    where, params = filters.search_where(q)
    total = store.count_posts(where, params)
    options = [("relevance", "相关度"), ("latest", "最新"), ("hot", "热门")]
    sort_control_data = sort_control(request, options, "relevance")
    sort_key = sort_control_data["current"]
    pagination = pagination_context(request, total, page, PAGE_SIZE, sort_key)
    offset = (pagination["page"] - 1) * PAGE_SIZE
    if sort_key == "relevance":
        rows = store.query_posts_relevance(
            where, params, filters.search_terms(q), limit=PAGE_SIZE, offset=offset
        )
    else:
        rows = store.query_posts(
            where, params, limit=PAGE_SIZE, order=sort_key, offset=offset
        )
    return templates.TemplateResponse(
        request,
        "list.html",
        {
            "title": f"搜索：{q}",
            "posts": rows,
            "grid": False,
            "pagination": pagination,
            "sort_control": sort_control_data,
        },
    )


@app.get("/post/{pid}")
def post_detail(request: Request, pid: str):
    p = store.get_post(pid)
    if not p:
        raise HTTPException(status_code=404, detail="微博不存在")
    return templates.TemplateResponse(request, "post.html", {"p": p})
