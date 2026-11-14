"""评论质量试跑：分层选样、首页评论捕获和审核包导出。"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import filters
import store

COMMENT_MAX_POSTS = 20
PILOT_MAX_POSTS = COMMENT_MAX_POSTS


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


def _select_exploration_posts(
    limit: int, excluded_ids: set[str] | None = None
) -> list[dict]:
    """按新入库优先、N宗罪/互动分层轮转选择探索微博。"""
    excluded_ids = excluded_ids or set()

    def pending(rows: list[dict]) -> list[dict]:
        return [
            row
            for row in rows
            if row["id"] not in excluded_ids
            and (store.comment_thread(row["id"]) or {}).get("source") != "pilot"
        ]

    def priority(row: dict) -> tuple[str, int, int]:
        return (
            str(row.get("fetched_at") or ""),
            int(row.get("ts") or 0),
            int(row.get("comments_count") or 0),
        )

    buckets: list[tuple[str, list[dict]]] = []
    for meme in store.list_memes():
        rows = pending(
            store.query_meme_posts(
                meme["slug"],
                limit=max(limit * 10, 200),
                order="hot",
            )
        )
        if rows:
            buckets.append(
                (f"N宗罪:{meme['name']}", sorted(rows, key=priority, reverse=True))
            )

    where, params = filters.interact_where()
    interactions = pending(
        store.query_posts(
            where,
            params,
            limit=max(limit * 10, 200),
            order="hot",
        )
    )
    if interactions:
        buckets.append(("互动", sorted(interactions, key=priority, reverse=True)))

    buckets.sort(key=lambda bucket: priority(bucket[1][0]), reverse=True)

    selected: list[dict] = []
    seen: set[str] = set(excluded_ids)
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
                        "selection_source": "exploration",
                    }
                )
                progressed = True
                break
            if len(selected) >= limit:
                break
        if not progressed:
            break
    return selected


def _candidate_priority_posts(limit: int) -> list[dict]:
    """返回最需要评论证据的候选微博，同一微博只保留一次。"""
    from candidate_review import pending_items

    human_rows = store.candidate_reviews_needing_human()
    human_ids = {str(row["post_id"]) for row in human_rows}
    signals: dict[str, dict] = {}

    def add_signal(
        post_id: str,
        meme_slug: str,
        source: str,
        observed_at: str,
        reason: str,
    ) -> None:
        post = store.get_post(post_id)
        if not post:
            return
        if (store.comment_thread(post_id) or {}).get("source") == "pilot":
            return
        item = signals.setdefault(
            post_id,
            {
                "post": post,
                "meme_slugs": set(),
                "sources": set(),
                "observed_at": "",
                "human_review": post_id in human_ids,
                "reasons": set(),
            },
        )
        item["meme_slugs"].add(meme_slug)
        item["sources"].add(source)
        item["observed_at"] = max(item["observed_at"], observed_at or "")
        item["reasons"].add(reason)

    for row in human_rows:
        add_signal(
            str(row["post_id"]),
            str(row["meme_slug"]),
            str(row["source_type"]),
            str(row["reviewed_at"] or ""),
            "人工待审",
        )

    for item in pending_items():
        baseline = item.get("baseline") or {}
        add_signal(
            str(item["post_id"]),
            str(item["meme_slug"]),
            str(baseline.get("source") or ""),
            str(baseline.get("observed_at") or ""),
            "待审核候选",
        )

    def priority(item: dict) -> tuple[int, int, int, str]:
        post = item["post"]
        return (
            0 if item["human_review"] else 1,
            0 if not store.comment_thread(str(post["id"])) else 1,
            0 if "weibo_search" in item["sources"] else 1,
            str(item["observed_at"]),
        )

    priority_by_id = {
        post_id: priority(item) for post_id, item in signals.items()
    }
    buckets: dict[str, list[dict]] = defaultdict(list)
    for item in signals.values():
        bucket = min(item["meme_slugs"])
        post = item["post"]
        reasons = "、".join(sorted(item["reasons"]))
        buckets[bucket].append(
            {
                **post,
                "selection_reason": f"候选优先:{reasons}:{bucket}",
                "selection_source": "candidate",
            }
        )
    for rows in buckets.values():
        rows.sort(key=lambda row: priority_by_id[str(row["id"])], reverse=True)

    ordered_buckets = sorted(
        buckets.values(),
        key=lambda rows: priority_by_id[str(rows[0]["id"])],
        reverse=True,
    )
    selected: list[dict] = []
    offsets = [0 for _ in ordered_buckets]
    while len(selected) < limit and any(
        offset < len(rows) for offset, rows in zip(offsets, ordered_buckets)
    ):
        progressed = False
        for index, rows in enumerate(ordered_buckets):
            if offsets[index] >= len(rows):
                continue
            selected.append(rows[offsets[index]])
            offsets[index] += 1
            progressed = True
            if len(selected) >= limit:
                break
        if not progressed:
            break
    return selected


def select_posts(
    limit: int = 20,
    strategy: str = "legacy",
    candidate_count: int = 12,
) -> list[dict]:
    """选择评论微博；候选优先模式仅显式开启，不影响现有默认行为。"""
    if not 1 <= limit <= COMMENT_MAX_POSTS:
        raise ValueError(f"评论挖掘最多选择 {COMMENT_MAX_POSTS} 条微博")
    if strategy not in {"legacy", "candidate-first"}:
        raise ValueError(f"未知评论选帖模式：{strategy}")
    if strategy == "candidate-first" and not 0 <= candidate_count <= limit:
        raise ValueError("候选评论数必须介于 0 和样本数之间")

    selected: list[dict] = []
    if strategy == "candidate-first":
        selected = _candidate_priority_posts(candidate_count)

    seen = {str(post["id"]) for post in selected}
    remaining = limit - len(selected)
    if remaining:
        selected.extend(
            _select_exploration_posts(remaining, excluded_ids=seen)
        )
    for index, post in enumerate(selected, start=1):
        post["selection_rank"] = index
    return selected


def _root_metadata(
    comments: list[dict],
    default_count: int = 10,
    top_count: int = 5,
) -> dict[str, dict]:
    """返回单页全部根评论及其首页位次、点赞位次和入选依据。"""
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
    default_ids = {
        _comment_id(comment)
        for comment in comments[:default_count]
        if _comment_id(comment) in by_id
    }
    top_like_ids = set(like_order[:top_count])
    return {
        cid: {
            "comment": comment,
            "page_rank": page_rank[cid],
            "like_rank": like_rank[cid],
            "selected_by_default": int(cid in default_ids),
            "selected_by_likes": int(cid in top_like_ids),
        }
        for cid, comment in by_id.items()
    }


def _select_root_comments(
    comments: list[dict],
    default_count: int = 10,
    top_count: int = 5,
) -> list[dict]:
    """返回根评论及入选依据，保留首页位置和点赞名次。"""
    metadata = _root_metadata(comments, default_count, top_count)
    like_order = sorted(
        metadata,
        key=lambda cid: (
            metadata[cid]["like_rank"],
            metadata[cid]["page_rank"],
        ),
    )
    selected: list[dict] = []
    seen: set[str] = set()
    candidates = [
        *(comments[:default_count]),
        *(metadata[cid]["comment"] for cid in like_order[:top_count]),
    ]
    for comment in candidates:
        cid = _comment_id(comment)
        if cid and cid in metadata and cid not in seen:
            item = metadata[cid]
            selected.append(
                {
                    **item,
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
    roots = sorted(
        _root_metadata(items).values(),
        key=lambda item: item["page_rank"],
    )
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
                "selection_order": selected["page_rank"],
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
            "roots_selected": len(roots),
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


def export(
    posts: list[dict],
    output_dir: Path,
    strategy: str = "legacy",
) -> dict:
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
        "# 评论挖掘审核包",
        "",
        f"选帖模式：{strategy}",
        "",
        "说明：每条微博只取一级评论首页；保存接口返回的全部根评论，"
        "并标记默认前 10 与点赞前 5。"
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
