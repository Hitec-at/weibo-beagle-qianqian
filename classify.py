"""按 Web tab 整理微博：热门 / 热梗 / 互动 / 高清图。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import filters
import store

CST = timezone(timedelta(hours=8))
CATEGORIES = (
    "hot",
    "memes",
    "interactions",
    "photos",
    "qianqian_aliases",
    "interaction_mengqian_cp",
)


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
    store.clear_post_categories(post["id"])
    text = post.get("text_plain") or ""
    categories = ["hot"]
    terms_by_category = {"hot": ""}
    matched_by_category = {"hot": "rule"}

    def add_category(category: str, term: str = "", matched_by: str = "rule") -> None:
        if category not in categories:
            categories.append(category)
        if term:
            existing = terms_by_category.get(category, "")
            terms_by_category[category] = "、".join(
                value for value in (existing, term) if value
            )
        if matched_by == "curated" or category not in matched_by_category:
            matched_by_category[category] = matched_by

    meme_terms = filters.meme_hits(text)
    for link in store.get_post_meme_links(post["id"]):
        term = link.get("matched_term") or ""
        if term and term not in meme_terms:
            meme_terms.append(term)
    if meme_terms:
        add_category("memes", "、".join(meme_terms))

    interact_terms = filters.interact_hits(text, post.get("retweeted_user") or "")
    if interact_terms:
        add_category("interactions", "、".join(interact_terms))
        for slug, hits in filters.interaction_subcategory_hits(
            text, post.get("retweeted_user") or ""
        ):
            category = f"interaction_{slug}"
            add_category(category, "、".join(hits))

    if filters.photo_match(post):
        add_category("photos")

    alias_terms = filters.entity_alias_hits(text)
    if alias_terms:
        add_category("qianqian_aliases", "、".join(alias_terms))

    for slug in filters.curated_interaction_subcategories(post["id"]):
        category = f"interaction_{slug}"
        add_category(category, "人工审定", matched_by="curated")
        add_category("interactions", "人工审定", matched_by="curated")

    for category in categories:
        store.add_post_category(
            post["id"],
            category,
            matched_by=matched_by_category.get(category, "rule"),
            matched_term=terms_by_category.get(category, ""),
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
