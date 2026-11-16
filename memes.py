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
ENTRY_KINDS = {"behavior", "fan_fiction"}
ENTRY_STATUSES = {"active", "paused", "deprecated"}


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


def _entry_status(item: dict) -> str:
    return str(item.get("status") or "active").strip()


def _duplicates(items) -> list[str]:
    seen: set[str] = set()
    duplicated: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if not value:
            continue
        if value in seen:
            duplicated.add(value)
        seen.add(value)
    return sorted(duplicated)


def preflight(seed_path: Path | str = SEED_PATH) -> dict:
    """Read-only rule package checks for lifecycle and evidence quality."""
    seed = load_seed(seed_path)
    errors: list[dict] = []
    warnings: list[dict] = []

    def add(level: str, code: str, slug: str, message: str) -> None:
        target = errors if level == "error" else warnings
        target.append({"code": code, "slug": slug, "message": message})

    active_terms: dict[str, set[str]] = {}
    seen_slugs: set[str] = set()
    db_available = store.DB_PATH.exists()

    for order, item in enumerate(seed, start=1):
        name = str(item.get("name") or "").strip()
        slug = str(item.get("slug") or name).strip()
        label = slug or f"entry-{order}"
        if not name:
            add("error", "missing_name", label, "缺少 name")
        if not slug:
            add("error", "missing_slug", label, "缺少 slug")
        if slug in seen_slugs:
            add("error", "duplicate_slug", label, f"slug 重复：{slug}")
        seen_slugs.add(slug)

        status = _entry_status(item)
        if status not in ENTRY_STATUSES:
            add(
                "error",
                "invalid_status",
                label,
                f"status 必须是 {'/'.join(sorted(ENTRY_STATUSES))}",
            )
        if status != "active":
            continue

        kind = str(item.get("kind") or "behavior").strip()
        if kind not in ENTRY_KINDS:
            add("error", "invalid_kind", label, f"无效 kind：{kind}")
        match_mode = str(item.get("match_mode") or "rule").strip()
        if match_mode not in {"rule", "curated", "mixed"}:
            add("error", "invalid_match_mode", label, f"无效 match_mode：{match_mode}")

        aliases = list(item.get("aliases") or [])
        excludes = list(item.get("exclude") or [])
        queries = list(item.get("search_queries") or [])
        auto_terms = list(item.get("auto_publish_terms") or [])
        candidate_terms = list(item.get("candidate_terms") or [])
        curated_ids = [
            str(post_id).strip()
            for post_id in item.get("curated_post_ids") or []
            if str(post_id).strip()
        ]

        for field, values in (
            ("aliases", aliases),
            ("exclude", excludes),
            ("search_queries", queries),
            ("auto_publish_terms", auto_terms),
            ("candidate_terms", candidate_terms),
            ("curated_post_ids", curated_ids),
        ):
            duplicates = _duplicates(values)
            if duplicates:
                add(
                    "warning",
                    "duplicate_value",
                    label,
                    f"{field} 包含重复值：{', '.join(duplicates)}",
                )

        if not str(item.get("origin") or "").strip():
            add("warning", "missing_origin", label, "缺少来源说明 origin")
        if not str(item.get("explanation") or "").strip():
            add("warning", "missing_explanation", label, "缺少解释 explanation")
        if not str(item.get("boundary") or "").strip():
            add("warning", "missing_boundary", label, "缺少判断边界 boundary")
        if not queries:
            add("warning", "missing_search_queries", label, "没有站内搜索词")
        if not excludes:
            add("warning", "missing_negative_examples", label, "没有排除词或反例")
        if not curated_ids and not auto_terms and match_mode != "mixed":
            add("warning", "missing_positive_examples", label, "没有正例或自动公开词")

        overlap = sorted(
            set(str(value).strip() for value in auto_terms)
            & set(str(value).strip() for value in candidate_terms)
        )
        if overlap:
            add(
                "error",
                "auto_candidate_overlap",
                label,
                f"自动公开词与候选词重叠：{', '.join(overlap)}",
            )
        if len(auto_terms) == 1:
            add(
                "warning",
                "single_auto_publish_term",
                label,
                "只有一个小范围自动公开词；样本不足时应优先作为候选规则",
            )

        if match_mode == "curated" and not curated_ids:
            add("error", "missing_curated_posts", label, "curated 模式缺少正例帖子")
        if match_mode == "rule" and curated_ids:
            add("error", "rule_with_curated_posts", label, "rule 模式不能配置正例帖子")
        if match_mode == "mixed" and not (
            auto_terms or candidate_terms or curated_ids
        ):
            add(
                "error",
                "mixed_without_rule_terms",
                label,
                "mixed 模式至少需要正例、自动公开词或候选词之一",
            )
        if match_mode == "mixed" and not auto_terms:
            add(
                "warning",
                "candidate_only_rule",
                label,
                "没有自动公开词；规则只能产生候选",
            )
        if match_mode != "mixed" and (auto_terms or candidate_terms):
            add(
                "error",
                "terms_require_mixed_mode",
                label,
                "自动公开词和候选词只允许在 mixed 模式使用",
            )

        if db_available:
            for post_id in curated_ids:
                if not store.get_post(post_id):
                    add(
                        "error",
                        "missing_curated_post",
                        label,
                        f"正例微博尚未入库：{post_id}",
                    )
        else:
            add("warning", "database_missing", label, "数据库不存在，无法核对正例帖子")

        terms = {
            str(value).strip().lower()
            for value in [name, *aliases]
            if str(value).strip()
        }
        for term in terms:
            active_terms.setdefault(term, set()).add(label)

    for term, slugs in sorted(active_terms.items()):
        if len(slugs) > 1:
            add(
                "warning",
                "term_conflict",
                ",".join(sorted(slugs)),
                f"active 条目共享名称或别名：{term}",
            )

    return {
        "ok": not errors,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "entries": len(seed),
    }


def sync_with_report(seed_path: Path | str = SEED_PATH) -> dict:
    """把种子文件同步到库，并返回条目与停用结果。"""
    seed = load_seed(seed_path)
    check = preflight(seed_path)
    if not check["ok"]:
        details = "；".join(
            f"{item['slug']}: {item['message']}"
            for item in check["errors"][:5]
        )
        suffix = "（另有更多错误）" if len(check["errors"]) > 5 else ""
        raise ValueError(f"规则预检失败：{details}{suffix}")
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
        kind = str(item.get("kind") or "behavior").strip()
        if kind not in ENTRY_KINDS:
            raise ValueError(
                f"「{name}」的 kind 必须是 behavior 或 fan_fiction"
            )
        status = _entry_status(item)
        if status not in ENTRY_STATUSES:
            raise ValueError(
                f"「{name}」的 status 必须是 {'/'.join(sorted(ENTRY_STATUSES))}"
            )
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
        if match_mode == "mixed" and not (
            auto_publish_terms or candidate_terms or curated_ids
        ):
            raise ValueError(
                f"「{name}」使用 mixed 模式时必须提供正例、自动公开词或候选词"
            )
        if match_mode != "mixed" and (auto_publish_terms or candidate_terms):
            raise ValueError(
                f"「{name}」只有 mixed 模式可以提供 auto_publish_terms/candidate_terms"
            )
        terms = _unique([name, *user_aliases])
        rep_url = str(item.get("representative_url") or "").strip()
        rep_mid = _mid_from_url(rep_url)
        rep_post = store.find_post_by_mid(rep_mid) if rep_mid else None
        curated_posts: list[dict] = []
        if status == "active" and match_mode in {"curated", "mixed"}:
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
                "kind": kind,
                "status": status,
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

        matched_ids: set[str] = set()
        term_counts: dict[str, int] = {}
        if status == "active":
            store.clear_generated_links(slug)
        if status == "active" and match_mode in {"curated", "mixed"}:
            for post in curated_posts:
                store.link_post_meme(
                    post["id"],
                    slug,
                    "人工审定",
                    matched_by="curated",
                )
                matched_ids.add(post["id"])
        if status == "active" and match_mode in {"mixed", "rule"}:
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
                "kind": kind,
                "status": status,
                "match_mode": match_mode,
                "matched_posts": len(matched_ids),
                "term_counts": term_counts,
                "representative_found": bool(rep_post),
            }
        )

    deprecated = store.deprecate_missing_memes(
        {
            str(item.get("slug") or item.get("name") or "").strip()
            for item in seed
            if str(item.get("slug") or item.get("name") or "").strip()
        },
        now,
    )
    return {
        "items": summary,
        "deprecated_missing": deprecated,
        "preflight": check,
    }


def sync(seed_path: Path | str = SEED_PATH) -> list[dict]:
    """Compatibility wrapper returning the synced seed entries."""
    return sync_with_report(seed_path)["items"]
