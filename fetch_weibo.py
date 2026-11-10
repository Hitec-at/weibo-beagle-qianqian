#!/usr/bin/env python3
"""抓取 @比格钱钱 公开微博到本地 SQLite（走 weibo.com PC 端接口）。

用法：
  python fetch_weibo.py --mode probe         # 校验 Cookie，打印首条样本
  python fetch_weibo.py --mode snapshot      # 全量快照
  python fetch_weibo.py --mode incremental   # 增量（自上次检查点）
  python fetch_weibo.py --mode snapshot --resume
  python fetch_weibo.py --mode snapshot --limit 5
"""
from __future__ import annotations

import argparse
import html
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone

import requests

import config
import store

MYBLOG_API = "https://weibo.com/ajax/statuses/mymblog"
SHOW_API = "https://weibo.com/ajax/statuses/show"
LONGTEXT_API = "https://weibo.com/ajax/statuses/longtext"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

BLOCK_CODES = {403, 418, 429, 432}
MAX_TRANSIENT_RETRY = 3


class WeiboAuthError(Exception):
    """Cookie 缺失或登录态失效。"""


class WeiboBlocked(Exception):
    """被风控拦截或接口异常，应立即停止。"""


class RateLimiter:
    def __init__(self) -> None:
        self.total = 0

    def wait(self) -> None:
        if self.total >= config.DAILY_MAX_REQUESTS:
            raise WeiboBlocked(f"已达单日请求上限 {config.DAILY_MAX_REQUESTS}，停止。明日再跑 incremental。")
        time.sleep(random.uniform(config.RATE_MIN_SEC, config.RATE_MAX_SEC))
        self.total += 1


def make_session() -> requests.Session:
    cookie = (os.environ.get("WEIBO_COOKIE") or "").strip().strip('"')
    if not cookie:
        raise WeiboAuthError("未找到 WEIBO_COOKIE，请按 docs/cookie-sop.md 写入 .env。")
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": UA,
            "Referer": f"https://weibo.com/u/{config.TARGET_UID}",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cookie": cookie,
        }
    )
    return s


def request_json(session: requests.Session, limiter: RateLimiter, url: str, params: dict) -> dict:
    for attempt in range(1, MAX_TRANSIENT_RETRY + 1):
        limiter.wait()
        try:
            resp = session.get(url, params=params, timeout=20)
        except requests.RequestException as exc:
            print(f"网络异常：{exc}", flush=True)
            if attempt == MAX_TRANSIENT_RETRY:
                raise WeiboBlocked("网络持续异常，已停止。")
            time.sleep(60)
            continue

        code = resp.status_code
        if code in BLOCK_CODES:
            if code == 403:
                try:
                    if resp.json().get("ok") in (0, -1, -100):
                        raise WeiboAuthError("登录态失效（HTTP 403, ok=0）。请重新登录 weibo.com 后复制 Cookie。")
                except ValueError:
                    pass
            raise WeiboBlocked(f"被风控/拒绝（HTTP {code}），立即停止，请勿短间隔重试。")
        if code == 400:
            raise WeiboBlocked("接口返回 400（参数错误），已停止；请先核对请求参数，不要重试。")
        if code != 200:
            print(f"HTTP {code}（第 {attempt} 次），等待 60 秒重试…", flush=True)
            time.sleep(60)
            continue
        try:
            data = resp.json()
        except ValueError:
            raise WeiboBlocked(f"返回非 JSON：{resp.text[:200]}")
        if data.get("ok") in (0, -1, -100):
            raise WeiboAuthError(f"登录态失效（ok={data.get('ok')}）。请重新登录 weibo.com 后复制 Cookie。")
        return data
    raise WeiboBlocked("请求失败，已停止。")


TAG_RE = re.compile(r"<[^>]+>")


def clean_text(text: str) -> str:
    text = TAG_RE.sub("", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_time(created_at: str) -> int:
    if not created_at:
        return 0
    try:
        return int(datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y").timestamp())
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        return int(parsedate_to_datetime(created_at).timestamp())
    except Exception:
        return 0


def is_video(mblog: dict) -> bool:
    pi = mblog.get("page_info") or {}
    if pi.get("object_type") == "video":
        return True
    if pi.get("media_info"):
        return True
    if pi.get("type") in ("video", "video_play"):
        return True
    for item in (mblog.get("mix_media_info") or {}).get("items") or []:
        data = item.get("data") or {}
        if (
            item.get("type") == "video"
            or data.get("object_type") == "video"
            or (data.get("media_info") or {}).get("stream_url")
        ):
            return True
    return False


def is_live(mblog: dict) -> bool:
    """live 图字段因接口版本而异，多候选判断；以 probe 输出核对为准。"""
    if mblog.get("is_live") or mblog.get("pic_is_live"):
        return True
    for info in (mblog.get("pic_infos") or {}).values():
        if info.get("type") == "livephoto" or info.get("live_photo"):
            return True
        for size in ("thumbnail", "bmiddle", "large", "largest", "original"):
            sub = info.get(size) or {}
            if sub.get("type") == "livephoto":
                return True
    for item in (mblog.get("mix_media_info") or {}).get("items") or []:
        data = item.get("data") or {}
        if item.get("type") != "pic":
            continue
        if data.get("type") == "livephoto":
            return True
        for size in ("thumbnail", "bmiddle", "large", "largest", "original"):
            if (data.get(size) or {}).get("type") == "livephoto":
                return True
    return False


def pics_from_pic_infos(mblog: dict) -> list[dict]:
    """解析常规 pic_infos，以及混合媒体中的图片项。"""
    pic_infos = mblog.get("pic_infos") or {}
    pics = []
    for pid in mblog.get("pic_ids") or []:
        info = pic_infos.get(pid) or {}

        def url_of(size: str):
            return (info.get(size) or {}).get("url")

        thumb = url_of("bmiddle") or url_of("wap180") or url_of("thumbnail")
        large = url_of("large") or url_of("mw2000") or url_of("original")
        if thumb or large:
            pics.append({"pid": pid, "thumb": thumb, "url": large})
    seen = {pic["pid"] for pic in pics}
    for item in (mblog.get("mix_media_info") or {}).get("items") or []:
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


def post_from_mblog(mblog: dict, pics: list[dict]) -> dict:
    mid = str(mblog.get("mid") or mblog.get("idstr") or mblog.get("id") or "")
    pid = str(mblog.get("idstr") or mblog.get("id") or mid)
    user = mblog.get("user") or {}
    text = mblog.get("text_raw") or mblog.get("text") or ""
    text_plain = clean_text(text)
    retweeted = mblog.get("retweeted_status") or {}
    retweeted_user = (retweeted.get("user") or {}).get("screen_name") or ""
    return {
        "id": pid,
        "mid": mid,
        "uid": str(user.get("id") or config.TARGET_UID),
        "screen_name": user.get("screen_name") or "",
        "created_at": mblog.get("created_at") or "",
        "ts": parse_time(mblog.get("created_at") or ""),
        "text": text,
        "text_plain": text_plain,
        "reposts_count": int(mblog.get("reposts_count") or 0),
        "comments_count": int(mblog.get("comments_count") or 0),
        "attitudes_count": int(mblog.get("attitudes_count") or 0),
        "source": mblog.get("source") or "",
        "is_longtext": 1 if mblog.get("isLongText") else 0,
        "is_video": 1 if is_video(mblog) else 0,
        "is_live": 1 if is_live(mblog) else 0,
        "has_pics": 1 if pics else 0,
        "pics_json": json.dumps(pics, ensure_ascii=False),
        "retweeted_user": retweeted_user,
        "raw_json": json.dumps(mblog, ensure_ascii=False) if config.SAVE_RAW else "",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_long_text(session: requests.Session, limiter: RateLimiter, mid: str) -> str:
    data = request_json(session, limiter, LONGTEXT_API, {"id": mid})
    return html.unescape((data.get("data") or {}).get("longTextContent") or "")


def fetch_mymblog(session: requests.Session, limiter: RateLimiter, page: int) -> tuple[list[dict], str | None]:
    data = request_json(
        session,
        limiter,
        MYBLOG_API,
        {"uid": config.TARGET_UID, "page": page, "feature": 0},
    )
    d = data.get("data") or {}
    return d.get("list") or [], d.get("since_id")


def fetch_show(session: requests.Session, limiter: RateLimiter, pid: str) -> dict:
    return request_json(session, limiter, SHOW_API, {"id": pid})


COMMENTS_API = "https://weibo.com/ajax/statuses/buildComments"


def fetch_comments(session: requests.Session, limiter: RateLimiter, post_id: str, max_id: int = 0, count: int = 20):
    """抓取单条微博的一级评论（一页）。

    已实测字段：like_counts（点赞数）、isLikedByMblogAuthor（博主是否点赞）、
    comments（内嵌部分二级评论）、total_number（二级评论总数）。
    """
    data = request_json(
        session, limiter, COMMENTS_API,
        {
            "is_reload": 1,
            "id": post_id,
            "is_show_bulletin": 2,
            "is_mix": 0,
            "count": count,
            "uid": config.TARGET_UID,
            "flow": 0,
            "max_id": max_id,
        },
    )
    d = data.get("data") or []
    return d, data.get("max_id")


def pics_from_comment(c: dict) -> list[dict]:
    """从单条评论里提取图片，多候选字段兼容，避免因接口版本差异漏图。"""
    pics = []
    pic = c.get("pic")
    if isinstance(pic, dict):
        u = pic.get("url") or pic.get("large") or pic.get("bmiddle")
        if u:
            pics.append({"pid": str(c.get("id") or ""), "thumb": u, "url": u})
    for pi in (c.get("pic_infos") or {}).values():
        for size in ("bmiddle", "large", "original", "thumbnail"):
            u = (pi.get(size) or {}).get("url")
            if u:
                pics.append({"pid": str(c.get("id") or ""), "thumb": u, "url": u})
                break
    return pics


def post_id(mblog: dict) -> str:
    return str(mblog.get("idstr") or mblog.get("id") or mblog.get("mid") or "")


def run(mode: str, resume: bool, limit: int | None) -> None:
    store.init_db()
    session = make_session()
    limiter = RateLimiter()
    print(f"目标：抓取截至 {config.SNAPSHOT_CUTOFF} 的存量微博（含更早历史）。", flush=True)

    if mode == "probe":
        posts, since_id = fetch_mymblog(session, limiter, 1)
        print(f"Cookie 有效。首页微博数：{len(posts)}，since_id={since_id}", flush=True)
        if posts:
            p = posts[0]
            print("首条顶层键：", sorted(p.keys()), flush=True)
            print("text_raw 前 80：", (p.get("text_raw") or "")[:80], flush=True)
            print("page_info：", json.dumps(p.get("page_info"), ensure_ascii=False)[:300], flush=True)
            print("pic_ids：", p.get("pic_ids"), flush=True)
            pic_posts = [x for x in posts if (x.get("pic_num") or 0) > 0]
            if pic_posts and config.FETCH_PIC_INFO:
                shown = fetch_show(session, limiter, post_id(pic_posts[0]))
                pi = shown.get("pic_infos") or {}
                k0 = list(pi.keys())[0] if pi else None
                print("pic_infos 示例键：", sorted(pi[k0].keys()) if k0 else "无", flush=True)
        return

    start_page = 1
    if resume:
        saved = store.get_meta("last_page")
        if saved and saved.isdigit():
            start_page = int(saved)

    last_id: str | None = None
    if mode == "incremental":
        last_id = store.get_meta("last_id")

    newest_overall: str | None = None
    page = start_page
    while True:
        if limit is not None and page - start_page + 1 > limit:
            break
        posts, _ = fetch_mymblog(session, limiter, page)
        if not posts:
            print(
                f"本页无内容，抓取结束。已覆盖截至 {config.SNAPSHOT_CUTOFF} 的存量微博。",
                flush=True,
            )
            break

        stopped = False
        own_count = 0
        page_newest: str | None = None
        for mblog in posts:
            if str((mblog.get("user") or {}).get("id") or "") != config.TARGET_UID:
                continue
            pid = post_id(mblog)
            if page_newest is None:
                page_newest = pid
            if last_id and pid and int(pid) <= int(last_id):
                print(f"已到上次位置 {last_id}，增量结束。", flush=True)
                stopped = True
                break

            pics: list[dict] = []
            is_live_val = 0
            if (mblog.get("pic_num") or 0) > 0 and config.FETCH_PIC_INFO and pid:
                try:
                    shown = fetch_show(session, limiter, pid)
                    pics = pics_from_pic_infos(shown)
                    is_live_val = 1 if is_live(shown) else 0
                except (WeiboBlocked, WeiboAuthError):
                    raise
                except Exception as exc:
                    print(f"取图失败 {pid}：{exc}", flush=True)

            p = post_from_mblog(mblog, pics)
            if is_live_val:
                p["is_live"] = 1
            if p["is_longtext"] and config.FETCH_LONGTEXT and p["mid"]:
                try:
                    lt = fetch_long_text(session, limiter, p["mid"])
                    if lt:
                        p["text"] = lt
                        p["text_plain"] = clean_text(lt)
                except (WeiboBlocked, WeiboAuthError):
                    raise
                except Exception as exc:
                    print(f"长文失败 {p['mid']}：{exc}", flush=True)
            store.upsert_post(p)
            own_count += 1

        if stopped:
            break

        if newest_overall is None and page_newest:
            newest_overall = page_newest
        store.set_meta("last_page", str(page))
        if newest_overall:
            store.set_meta("last_id", newest_overall)
        print(f"第 {page} 页完成（本页她的帖子 {own_count} 条）。", flush=True)
        page += 1
        pages_done = page - start_page
        if config.PAGES_PER_BREAK > 0 and pages_done % config.PAGES_PER_BREAK == 0:
            secs = random.uniform(config.BREAK_MIN_SEC, config.BREAK_MAX_SEC)
            print(f"已连续抓取 {pages_done} 页，休眠 {int(secs)} 秒…", flush=True)
            time.sleep(secs)

    print(f"完成。当前库中共 {store.count_posts()} 条微博。", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取 @比格钱钱 公开微博")
    parser.add_argument("--mode", choices=["probe", "snapshot", "incremental"], default="snapshot")
    parser.add_argument("--resume", action="store_true", help="从检查点继续")
    parser.add_argument("--limit", type=int, default=None, help="本次最多抓取的页数")
    args = parser.parse_args()
    try:
        run(args.mode, args.resume, args.limit)
        return 0
    except WeiboAuthError as exc:
        print(f"[Cookie 问题] {exc}", file=sys.stderr)
        return 2
    except WeiboBlocked as exc:
        print(f"[风控/停止] {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\n已手动中断，检查点已保存，下次可用 --resume 继续。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
