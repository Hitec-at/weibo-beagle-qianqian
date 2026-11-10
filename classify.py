"""按 Web tab 整理微博：热门 / 热梗 / 互动 / 高清图。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import filters
import store

CST = timezone(timedelta(hours=8))
CATEGORIES = ("hot", "memes", "interactions", "photos")


def _day_range(day: str | None) -> tuple[str, str] | None:
    if not day:
        return None
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=CST)
    end = start + timedelta(days=1)
    return (
        start.astimezone(timezone.utc).isoformat(),
        end.astimezone(timezone.utc).isoformat(),
    )


def _candidates(day: str | None, all_posts: bool) -> list[dict]:
    conn = store.connect()
    try:
        clauses: list[str] = []
        params: list[str] = []
        if not all_posts:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM post_categories pc WHERE pc.post_id = p.id)"
            )
        day_range = _day_range(day)
        if day_range:
            clauses.append("p.fetched_at >= ? AND p.fetched_at < ?")
            params.extend(day_range)
        sql = "SELECT p.* FROM posts p"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY p.ts DESC, p.id DESC"
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def classify_post(post: dict) -> list[str]:
    store.clear_rule_categories(post["id"])
    text = post.get("text_plain") or ""
    categories = ["hot"]

    meme_terms = filters.meme_hits(text)
    for link in store.get_post_meme_links(post["id"]):
        term = link.get("matched_term") or ""
        if term and term not in meme_terms:
            meme_terms.append(term)
    if meme_terms:
        categories.append("memes")

    interact_terms = filters.interact_hits(text, post.get("retweeted_user") or "")
    if interact_terms:
        categories.append("interactions")

    if filters.photo_match(post):
        categories.append("photos")

    terms_by_category = {
        "hot": "",
        "memes": "、".join(meme_terms),
        "interactions": "、".join(interact_terms),
        "photos": "",
    }
    for category in categories:
        store.add_post_category(
            post["id"], category, matched_by="rule", matched_term=terms_by_category[category]
        )
    return categories


def run(day: str | None = None, all_posts: bool = False) -> dict:
    store.init_db()
    posts = _candidates(day, all_posts)
    counts = {category: 0 for category in CATEGORIES}
    for post in posts:
        for category in classify_post(post):
            counts[category] += 1
    return {"processed": len(posts), **counts}
