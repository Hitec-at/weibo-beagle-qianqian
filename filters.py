"""三个检索视图 + 搜索的筛选 WHERE 子句，供 Web 与 CLI 共用。"""
from __future__ import annotations

import re

import config


def like_pattern(term: str) -> str:
    esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def _like(field: str) -> str:
    return f"{field} LIKE ? ESCAPE '\\'"


def meme_where() -> tuple[str, tuple]:
    clauses, params = [], []
    for kw in config.MEME_KEYWORDS:
        clauses.append(_like("text_plain"))
        params.append(like_pattern(kw))
    where = "(" + " OR ".join(clauses) + ")" if clauses else "1=0"
    return where, tuple(params)


def interact_where() -> tuple[str, tuple]:
    clauses, params = [], []
    for kw in config.INTERACT_KEYWORDS:
        clauses.append(_like("text_plain"))
        params.append(like_pattern(kw))
        clauses.append(_like("retweeted_user"))
        params.append(like_pattern(kw))
    where = "(" + " OR ".join(clauses) + ")" if clauses else "1=0"
    return where, tuple(params)


def photo_where(threshold: int | None = None) -> tuple[str, tuple]:
    t = config.PHOTO_MIN_ENGAGEMENT if threshold is None else threshold
    where = (
        "has_pics = 1 AND is_video = 0 AND is_live = 0 "
        "AND retweeted_user = '' AND (reposts_count + comments_count) > ?"
    )
    return where, (t,)


def search_terms(q: str) -> list[str]:
    return [t for t in re.split(r"\s+", (q or "").strip()) if t]


def search_where(q: str) -> tuple[str, tuple]:
    terms = search_terms(q)
    clauses, params = [], []
    for t in terms:
        clauses.append(_like("text_plain"))
        params.append(like_pattern(t))
    where = " AND ".join(clauses) if clauses else "1=0"
    return f"({where})", tuple(params)


def category_where(category: str) -> tuple[str, tuple]:
    """分类整理结果驱动的 tab 查询。"""
    return (
        "EXISTS (SELECT 1 FROM post_categories pc "
        "WHERE pc.post_id = posts.id AND pc.category = ?)",
        (category,),
    )


def meme_hits(text: str) -> list[str]:
    """分类器用：返回正文命中的迷因关键词（去重，保留配置顺序）。"""
    lowered = (text or "").lower()
    out: list[str] = []
    for kw in config.MEME_KEYWORDS:
        if kw.lower() in lowered and kw not in out:
            out.append(kw)
    return out


def interact_hits(text: str, retweeted_user: str) -> list[str]:
    """分类器用：正文或转发源账号命中的互动关键词。"""
    haystack = f"{text or ''}\n{retweeted_user or ''}"
    return [kw for kw in config.INTERACT_KEYWORDS if kw in haystack]


def interaction_subcategory_hits(
    text: str, retweeted_user: str
) -> list[tuple[str, list[str]]]:
    """分类器用：返回命中的互动子分类及其关键词。"""
    haystack = f"{text or ''}\n{retweeted_user or ''}"
    result: list[tuple[str, list[str]]] = []
    for slug, terms in config.INTERACTION_SUBCATEGORIES.items():
        hits = [term for term in terms if term in haystack]
        if hits:
            result.append((slug, hits))
    return result


def curated_interaction_subcategories(post_id: str) -> list[str]:
    """返回人工审定属于某条互动关系线的子分类。"""
    return [
        slug
        for slug, post_ids in config.INTERACTION_CURATED_POSTS.items()
        if post_id in post_ids
    ]


def entity_alias_hits(text: str) -> list[str]:
    """返回正文中出现的钱钱实体别名。"""
    haystack = text or ""
    return [alias for alias in config.ENTITY_ALIASES if alias in haystack]


def photo_match(post: dict, threshold: int | None = None) -> bool:
    """分类器用：与 photo_where 保持同一口径。"""
    t = config.PHOTO_MIN_ENGAGEMENT if threshold is None else threshold
    return (
        int(post.get("has_pics") or 0) == 1
        and int(post.get("is_video") or 0) == 0
        and int(post.get("is_live") or 0) == 0
        and not (post.get("retweeted_user") or "")
        and int(post.get("reposts_count") or 0) + int(post.get("comments_count") or 0) > t
    )
