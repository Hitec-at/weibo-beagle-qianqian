"""评论质量试跑：分层选样、首页评论捕获和审核包导出。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import filters
import store

PILOT_MAX_POSTS = 20


def _comment_id(comment: dict) -> str:
    return str(
        comment.get("idstr")
        or comment.get("id")
        or comment.get("rootidstr")
        or ""
    )


def _like_count(comment: dict) -> int:
    try:
        return int(comment.get("like_counts") or 0)
    except (TypeError, ValueError):
        return 0


def select_posts(limit: int = 20) -> list[dict]:
    """按现有 N宗罪和互动分层，以热度轮转选择，按 post id 去重。"""
    if not 1 <= limit <= PILOT_MAX_POSTS:
        raise ValueError(f"评论试跑最多选择 {PILOT_MAX_POSTS} 条微博")

    buckets: list[tuple[str, list[dict]]] = []
    for meme in store.list_memes():
        rows = store.query_meme_posts(
            meme["slug"],
            limit=max(limit, 40),
            order="hot",
        )
        buckets.append((f"N宗罪:{meme['name']}", rows))

    where, params = filters.interact_where()
    interactions = store.query_posts(
        where,
        params,
        limit=max(limit, 40),
        order="hot",
    )
    buckets.append(("互动", interactions))

    selected: list[dict] = []
    seen: set[str] = set()
    offsets = [0 for _ in buckets]
    while len(selected) < limit and any(
        offset < len(bucket[1]) for offset, bucket in zip(offsets, buckets)
    ):
        progressed = False
        for index, (reason, rows) in enumerate(buckets):
            while offsets[index] < len(rows):
                post = rows[offsets[index]]
                offsets[index] += 1
                if post["id"] in seen:
                    continue
                seen.add(post["id"])
                selected.append(
                    {
                        **post,
                        "selection_reason": reason,
                        "selection_rank": len(selected) + 1,
                    }
                )
                progressed = True
                break
            if len(selected) >= limit:
                break
        if not progressed:
            break
    return selected


def _select_root_comments(
    comments: list[dict],
    default_count: int = 10,
    top_count: int = 5,
) -> list[dict]:
    """返回根评论及入选依据，保留首页位置和点赞名次。"""
    by_id: dict[str, dict] = {}
    page_rank: dict[str, int] = {}
    for index, comment in enumerate(comments, start=1):
        cid = _comment_id(comment)
        if cid and cid not in by_id:
            by_id[cid] = comment
            page_rank[cid] = index

    like_order = sorted(
        by_id,
        key=lambda cid: (-_like_count(by_id[cid]), page_rank[cid]),
    )
    like_rank = {
        cid: rank for rank, cid in enumerate(like_order, start=1)
    }

    selected: list[dict] = []
    seen: set[str] = set()
    default_ids = {
        _comment_id(comment)
        for comment in comments[:default_count]
        if _comment_id(comment) in by_id
    }
    candidates = [
        *(comments[:default_count]),
        *(by_id[cid] for cid in like_order[:top_count]),
    ]
    for comment in candidates:
        cid = _comment_id(comment)
        if cid and cid not in seen:
            selected.append(
                {
                    "comment": by_id[cid],
                    "page_rank": page_rank[cid],
                    "like_rank": like_rank[cid],
                    "selected_by_default": int(cid in default_ids),
                    "selected_by_likes": int(like_rank[cid] <= top_count),
                    "selection_order": len(selected) + 1,
                }
            )
            seen.add(cid)
    return selected


def choose_root_comments(
    comments: list[dict],
    default_count: int = 10,
    top_count: int = 5,
) -> list[dict]:
    """返回默认顺序前 N 与点赞前 M 的并集，保持首次出现顺序。"""
    return [
        item["comment"]
        for item in _select_root_comments(comments, default_count, top_count)
    ]


def save_page(post_id: str, items: list[dict], max_id, source: str) -> dict:
    from fetch_weibo import clean_text

    fetched_at = datetime.now(timezone.utc).isoformat()
    roots = _select_root_comments(items)
    comment_rows: list[dict] = []
    selection_rows: list[dict] = []
    replies_saved = 0
    seen_reply_ids: set[str] = set()
    for selected in roots:
        root = selected["comment"]
        root_id = _comment_id(root)
        if not root_id:
            continue
        embedded = root.get("comments") or []
        valid_replies: list[dict] = []
        for reply in embedded:
            reply_id = _comment_id(reply)
            if reply_id and reply_id not in seen_reply_ids:
                valid_replies.append(reply)
                seen_reply_ids.add(reply_id)
        reply_total_raw = root.get("total_number")
        reply_total_known = reply_total_raw is not None
        try:
            reply_total = int(reply_total_raw)
        except (TypeError, ValueError):
            reply_total_known = False
            reply_total = len(valid_replies)
        user = root.get("user") or {}
        comment_rows.append(
            {
                "id": root_id,
                "post_id": post_id,
                "root_id": root_id,
                "parent_id": root_id,
                "user_name": str(user.get("screen_name") or ""),
                "text_plain": clean_text(root.get("text_raw") or root.get("text") or ""),
                "like_count": _like_count(root),
                "author_liked": int(bool(root.get("isLikedByMblogAuthor"))),
                "author_liked_known": int(
                    "isLikedByMblogAuthor" in root
                    and root.get("isLikedByMblogAuthor") is not None
                ),
                "created_at": str(root.get("created_at") or ""),
                "is_reply": 0,
                "reply_total": reply_total,
                "reply_saved": len(valid_replies),
                "reply_complete": int(
                    reply_total_known and reply_total == len(valid_replies)
                ),
                "fetched_at": fetched_at,
            }
        )
        selection_rows.append(
            {
                "post_id": post_id,
                "comment_id": root_id,
                "page_rank": selected["page_rank"],
                "like_rank": selected["like_rank"],
                "selected_by_default": selected["selected_by_default"],
                "selected_by_likes": selected["selected_by_likes"],
                "selection_order": selected["selection_order"],
                "fetched_at": fetched_at,
            }
        )
        for reply in valid_replies:
            reply_id = _comment_id(reply)
            reply_user = reply.get("user") or {}
            comment_rows.append(
                {
                    "id": reply_id,
                    "post_id": post_id,
                    "root_id": root_id,
                    "parent_id": str(reply.get("reply_id") or root_id),
                    "user_name": str(reply_user.get("screen_name") or ""),
                    "text_plain": clean_text(
                        reply.get("text_raw") or reply.get("text") or ""
                    ),
                    "like_count": _like_count(reply),
                    "author_liked": int(bool(reply.get("isLikedByMblogAuthor"))),
                    "author_liked_known": int(
                        "isLikedByMblogAuthor" in reply
                        and reply.get("isLikedByMblogAuthor") is not None
                    ),
                    "created_at": str(reply.get("created_at") or ""),
                    "is_reply": 1,
                    "reply_total": 0,
                    "reply_saved": 0,
                    "reply_complete": 0,
                    "fetched_at": fetched_at,
                }
            )
            replies_saved += 1
    thread = {
        "post_id": post_id,
        "fetched_at": fetched_at,
        "source": source,
        "roots_seen": len(items),
        "roots_selected": len(comment_rows) - replies_saved,
        "replies_saved": replies_saved,
        "max_id": str(max_id or ""),
        "complete": 0,
    }
    store.replace_comment_page(post_id, comment_rows, selection_rows, thread)
    return thread


def fetch(posts: list[dict], source: str = "pilot") -> dict:
    from fetch_weibo import RateLimiter, WeiboAuthError, WeiboBlocked
    from fetch_weibo import fetch_comments, make_session

    posts = list(posts)
    if len(posts) > PILOT_MAX_POSTS:
        raise ValueError(f"评论试跑最多请求 {PILOT_MAX_POSTS} 条微博")
    post_ids = [post["id"] for post in posts]
    if len(set(post_ids)) != len(post_ids):
        raise ValueError("评论试跑样本包含重复微博")

    pending = [
        post
        for post in posts
        if (store.comment_thread(post["id"]) or {}).get("source") != source
    ]
    skipped = len(posts) - len(pending)
    if not pending:
        return {
            "fetched_posts": 0,
            "skipped_posts": skipped,
            "selected_posts": len(posts),
        }

    session = make_session()
    limiter = RateLimiter()
    fetched = 0
    for post in pending:
        try:
            items, max_id = fetch_comments(
                session,
                limiter,
                post["id"],
                count=20,
                max_attempts=1,
            )
        except (WeiboAuthError, WeiboBlocked):
            raise
        save_page(post["id"], items, max_id, source)
        fetched += 1
        print(f"{post['id']} 评论首页完成：{len(items)} 条一级评论", flush=True)
    return {
        "fetched_posts": fetched,
        "skipped_posts": skipped,
        "selected_posts": len(posts),
    }


def _markdown_escape(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def _selection_label(row: dict) -> str:
    labels = []
    if row.get("selected_by_default"):
        labels.append("默认")
    if row.get("selected_by_likes"):
        labels.append("高赞")
    return "+".join(labels)


def _author_liked_label(row: dict) -> str:
    if not row.get("author_liked_known"):
        return "未知"
    return "是" if row["author_liked"] else "否"


def export(posts: list[dict], output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    package = []
    for post in posts:
        rows = store.comments_for_post(post["id"])
        package.append(
            {
                "post_id": post["id"],
                "selection_reason": post["selection_reason"],
                "selection_rank": post["selection_rank"],
                "text": post.get("text_plain") or "",
                "comments_count": int(post.get("comments_count") or 0),
                "thread": store.comment_thread(post["id"]),
                "comments": rows,
            }
        )

    json_path = output_dir / "comment-pilot.json"
    json_path.write_text(
        json.dumps(package, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        "# 评论质量试跑",
        "",
        "说明：每条微博只取一级评论首页；点赞前 5 与默认前 10 取并集。"
        "二级回复仅包含接口内嵌部分，不能视为完整回复树。",
        "",
    ]
    for item in package:
        lines.extend(
            [
                f"## {item['selection_rank']}. {item['selection_reason']} · {item['post_id']}",
                "",
                f"原微博：{item['text']}",
                "",
            ]
        )
        roots = [row for row in item["comments"] if not row["is_reply"]]
        replies = [row for row in item["comments"] if row["is_reply"]]
        if not roots:
            lines.extend(["尚未抓取评论。", ""])
            continue
        lines.extend(
            [
                "| 一级评论 | 首页位次 | 点赞位次 | 入选 | 点赞 | 博主点赞 | 内嵌回复 |",
                "| --- | ---: | ---: | :---: | ---: | :---: | ---: |",
            ]
        )
        for root in roots:
            lines.append(
                f"| {_markdown_escape(root['text_plain'])} "
                f"| {root['page_rank']} | {root['like_rank']} "
                f"| {_selection_label(root)} | {root['like_count']} "
                f"| {_author_liked_label(root)} "
                f"| {root['reply_saved']}/{root['reply_total']}"
                f"{'（完整）' if root['reply_complete'] else '（不完整）'} |"
            )
            for reply in [r for r in replies if r["root_id"] == root["id"]]:
                lines.append(
                    f"| ↳ {_markdown_escape(reply['text_plain'])} "
                    f"| | | | {reply['like_count']} "
                    f"| {_author_liked_label(reply)} | |"
                )
        lines.append("")

    markdown_path = output_dir / "comment-pilot.md"
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "posts": len(package),
        "json": str(json_path),
        "markdown": str(markdown_path),
    }
