"""评论质量试跑：分层选样、首页评论捕获和审核包导出。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import filters
import store


def _comment_id(comment: dict) -> str:
    return str(
        comment.get("idstr")
        or comment.get("id")
        or comment.get("rootidstr")
        or ""
    )


def select_posts(limit: int = 20) -> list[dict]:
    """按现有 N宗罪和互动分层，以热度轮转选择，按 post id 去重。"""
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


def choose_root_comments(comments: list[dict], default_count: int = 10, top_count: int = 5) -> list[dict]:
    """返回默认顺序前 N 与点赞前 M 的并集，保持首次出现顺序。"""
    selected: list[dict] = []
    seen: set[str] = set()
    ranked = sorted(
        enumerate(comments),
        key=lambda pair: (-int(pair[1].get("like_counts") or 0), pair[0]),
    )
    for _, comment in [*enumerate(comments[:default_count]), *ranked[:top_count]]:
        cid = _comment_id(comment)
        if cid and cid not in seen:
            selected.append(comment)
            seen.add(cid)
    return selected


def save_page(post_id: str, items: list[dict], max_id, source: str) -> dict:
    from fetch_weibo import clean_text

    fetched_at = datetime.now(timezone.utc).isoformat()
    roots = choose_root_comments(items)
    replies_saved = 0
    for root in roots:
        root_id = _comment_id(root)
        if not root_id:
            continue
        embedded = root.get("comments") or []
        reply_total = int(root.get("total_number") or len(embedded))
        user = root.get("user") or {}
        store.upsert_comment(
            {
                "id": root_id,
                "post_id": post_id,
                "root_id": root_id,
                "parent_id": root_id,
                "user_name": str(user.get("screen_name") or ""),
                "text_plain": clean_text(root.get("text_raw") or root.get("text") or ""),
                "like_count": int(root.get("like_counts") or 0),
                "author_liked": int(bool(root.get("isLikedByMblogAuthor"))),
                "created_at": str(root.get("created_at") or ""),
                "is_reply": 0,
                "reply_total": reply_total,
                "reply_saved": len(embedded),
                "reply_complete": 0,
                "fetched_at": fetched_at,
            }
        )
        for reply in embedded:
            reply_id = _comment_id(reply)
            if not reply_id:
                continue
            reply_user = reply.get("user") or {}
            store.upsert_comment(
                {
                    "id": reply_id,
                    "post_id": post_id,
                    "root_id": root_id,
                    "parent_id": str(reply.get("reply_id") or root_id),
                    "user_name": str(reply_user.get("screen_name") or ""),
                    "text_plain": clean_text(
                        reply.get("text_raw") or reply.get("text") or ""
                    ),
                    "like_count": int(reply.get("like_counts") or 0),
                    "author_liked": int(bool(reply.get("isLikedByMblogAuthor"))),
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
        "roots_selected": len(roots),
        "replies_saved": replies_saved,
        "max_id": str(max_id or ""),
        "complete": 0,
    }
    store.upsert_comment_thread(thread)
    return thread


def fetch(posts: list[dict], source: str = "pilot") -> dict:
    from fetch_weibo import RateLimiter, WeiboAuthError, WeiboBlocked
    from fetch_weibo import fetch_comments, make_session

    session = make_session()
    limiter = RateLimiter()
    fetched = 0
    for post in posts:
        try:
            items, max_id = fetch_comments(session, limiter, post["id"], count=20)
        except (WeiboAuthError, WeiboBlocked):
            raise
        save_page(post["id"], items, max_id, source)
        fetched += 1
        print(f"{post['id']} 评论首页完成：{len(items)} 条一级评论", flush=True)
    return {"fetched_posts": fetched, "selected_posts": len(posts)}


def _markdown_escape(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


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
                "| 一级评论 | 点赞 | 博主点赞 | 内嵌回复 |",
                "| --- | ---: | :---: | ---: |",
            ]
        )
        for root in roots:
            lines.append(
                f"| {_markdown_escape(root['text_plain'])} "
                f"| {root['like_count']} | {'是' if root['author_liked'] else '否'} "
                f"| {root['reply_saved']}/{root['reply_total']} |"
            )
            for reply in [r for r in replies if r["root_id"] == root["id"]]:
                lines.append(
                    f"| ↳ {_markdown_escape(reply['text_plain'])} "
                    f"| {reply['like_count']} | {'是' if reply['author_liked'] else '否'} | |"
                )
        lines.append("")

    markdown_path = output_dir / "comment-pilot.md"
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "posts": len(package),
        "json": str(json_path),
        "markdown": str(markdown_path),
    }
