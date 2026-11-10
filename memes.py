"""梗条目：种子加载、入库与规则关联。

梗条目由人工维护（data/memes.seed.toml），系统按「梗名 + 别名」对微博正文做
启发式匹配，匹配结果写入 post_memes，供专题页和 CLI 使用。
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
    """把种子文件同步到库，并按触发词重建规则关联。"""
    seed = load_seed(seed_path)
    store.init_db()
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
        terms = _unique([name, *user_aliases])
        rep_url = str(item.get("representative_url") or "").strip()
        rep_mid = _mid_from_url(rep_url)
        rep_post = store.find_post_by_mid(rep_mid) if rep_mid else None

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

        store.clear_rule_links(slug)
        matched_ids: set[str] = set()
        term_counts: dict[str, int] = {}
        for term in terms:
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
                "matched_posts": len(matched_ids),
                "term_counts": term_counts,
                "representative_found": bool(rep_post),
            }
        )

    return summary
