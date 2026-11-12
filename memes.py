"""梗条目：种子加载、入库与关联。

梗条目由人工维护（memes.seed.toml）。条目可使用关键词规则匹配，也可使用人工
审定的 post id 列表，关联结果写入 post_memes，供专题页和 CLI 使用。
"""
from __future__ import annotations

import json
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import store

BASE_DIR = Path(__file__).resolve().parent
SEED_PATH = BASE_DIR / "memes.seed.toml"
LEGACY_SLUGS = {"fanguoji": "fanlaji"}


def canonical_slug(slug: str) -> str:
    return LEGACY_SLUGS.get(slug, slug)


def load_seed(path: Path | str = SEED_PATH) -> list[dict]:
    with Path(path).open("rb") as f:
        data = tomllib.load(f)
    items = data.get("memes") or []
    if not isinstance(items, list):
        raise ValueError("memes.seed.toml 必须是 [[memes]] 数组")
    return items


def _mid_from_url(url: str) -> str:
    path = urlparse(url or "").path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else ""


def _unique(items) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def sync(seed_path: Path | str = SEED_PATH) -> list[dict]:
    """把种子文件同步到库，并重建规则或人工审定关联。"""
    seed = load_seed(seed_path)
    store.init_db()
    for legacy_slug, canonical in LEGACY_SLUGS.items():
        store.rename_meme_slug(legacy_slug, canonical)
    now = datetime.now(timezone.utc).isoformat()
    summary: list[dict] = []

    for order, item in enumerate(seed):
        name = str(item.get("name") or "").strip()
        if not name:
            raise ValueError(f"第 {order + 1} 条梗条目缺少 name")
        slug = str(item.get("slug") or name).strip()
        user_aliases = _unique(item.get("aliases") or [])
        excludes = _unique(item.get("exclude") or [])
        search_queries = _unique(item.get("search_queries") or [])
        match_mode = str(item.get("match_mode") or "rule").strip()
        if match_mode not in {"rule", "curated", "mixed"}:
            raise ValueError(
                f"「{name}」的 match_mode 必须是 rule、curated 或 mixed"
            )
        curated_ids = _unique(item.get("curated_post_ids") or [])
        if match_mode == "curated" and not curated_ids:
            raise ValueError(f"「{name}」使用 curated 模式时必须提供 curated_post_ids")
        if match_mode == "rule" and curated_ids:
            raise ValueError(f"「{name}」使用 rule 模式时不能提供 curated_post_ids")
        auto_publish_terms = _unique(item.get("auto_publish_terms") or [])
        candidate_terms = _unique(item.get("candidate_terms") or [])
        if match_mode == "mixed" and not auto_publish_terms:
            raise ValueError(f"「{name}」使用 mixed 模式时必须提供 auto_publish_terms")
        if match_mode != "mixed" and (auto_publish_terms or candidate_terms):
            raise ValueError(
                f"「{name}」只有 mixed 模式可以提供 auto_publish_terms/candidate_terms"
            )
        terms = _unique([name, *user_aliases])
        rep_url = str(item.get("representative_url") or "").strip()
        rep_mid = _mid_from_url(rep_url)
        rep_post = store.find_post_by_mid(rep_mid) if rep_mid else None
        curated_posts: list[dict] = []
        if match_mode in {"curated", "mixed"}:
            curated_posts = [store.get_post(post_id) or {} for post_id in curated_ids]
            missing = [
                post_id
                for post_id, post in zip(curated_ids, curated_posts)
                if not post
            ]
            if missing:
                raise ValueError(
                    f"「{name}」审定的微博尚未入库：{', '.join(missing)}"
                )

        store.upsert_meme(
            {
                "slug": slug,
                "name": name,
                "aliases_json": json.dumps(user_aliases, ensure_ascii=False),
                "origin": str(item.get("origin") or "").strip(),
                "explanation": str(item.get("explanation") or "").strip(),
                "behavior_tags_json": json.dumps(
                    _unique(item.get("behavior_tags") or []), ensure_ascii=False
                ),
                "exclude_json": json.dumps(excludes, ensure_ascii=False),
                "search_queries_json": json.dumps(search_queries, ensure_ascii=False),
                "boundary": str(item.get("boundary") or "").strip(),
                "representative_url": rep_url,
                "representative_post_id": rep_post["id"] if rep_post else None,
                "sort_order": order,
                "updated_at": now,
            }
        )

        store.clear_generated_links(slug)
        matched_ids: set[str] = set()
        term_counts: dict[str, int] = {}
        if match_mode in {"curated", "mixed"}:
            for post in curated_posts:
                store.link_post_meme(
                    post["id"],
                    slug,
                    "人工审定",
                    matched_by="curated",
                )
                matched_ids.add(post["id"])
        if match_mode in {"mixed", "rule"}:
            rule_terms = auto_publish_terms if match_mode == "mixed" else terms
            for term in rule_terms:
                rows = store.find_posts_by_text(term, tuple(excludes))
                if rows:
                    term_counts[term] = len(rows)
                for row in rows:
                    store.link_post_meme(row["id"], slug, term)
                    matched_ids.add(row["id"])

        summary.append(
            {
                "slug": slug,
                "name": name,
                "match_mode": match_mode,
                "matched_posts": len(matched_ids),
                "term_counts": term_counts,
                "representative_found": bool(rep_post),
            }
        )

    return summary
