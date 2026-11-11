"""N宗罪检索词的版本化 baseline 构建。"""
from __future__ import annotations

import config
import memes
import store


def _contains_any(text: str, terms: list[str]) -> str:
    lowered = (text or "").lower()
    for term in terms:
        if term.lower() in lowered:
            return term
    return ""


def _record(
    version: str,
    post_id: str,
    slug: str,
    source: str,
    query: str,
    matched_term: str,
    status: str,
    search_rank: int | None = None,
) -> None:
    store.add_baseline_candidate(
        version,
        post_id,
        slug,
        source,
        query,
        matched_term,
        status,
        search_rank,
    )


def build(
    version: str,
    search_pages: int = 0,
    seed_path=None,
) -> dict:
    """构建并冻结一个 baseline；search_pages=0 时不访问微博。"""
    store.init_db()
    if store.get_baseline_run(version):
        raise ValueError(f"baseline 已存在：{version}")

    memes.sync(seed_path) if seed_path else memes.sync()
    seed = memes.load_seed(seed_path) if seed_path else memes.load_seed()
    store.create_baseline_run(version, config.SNAPSHOT_CUTOFF)
    report = {
        "version": version,
        "snapshot_cutoff": config.SNAPSHOT_CUTOFF,
        "weibo_search_pages": search_pages,
        "memes": [],
        "missing_search_posts": [],
    }

    session = None
    limiter = None
    if search_pages:
        from fetch_weibo import RateLimiter, make_session

        session = make_session()
        limiter = RateLimiter()

    for item in seed:
        slug = str(item.get("slug") or item.get("name") or "").strip()
        name = str(item.get("name") or slug).strip()
        mode = str(item.get("match_mode") or "rule").strip()
        excludes = tuple(str(x) for x in (item.get("exclude") or []))
        curated_ids = [str(x) for x in (item.get("curated_post_ids") or [])]
        auto_terms = [
            str(x) for x in (item.get("auto_publish_terms") or [])
        ]
        candidate_terms = [
            str(x) for x in (item.get("candidate_terms") or [])
        ]
        if mode == "rule":
            auto_terms = [name, *[str(x) for x in (item.get("aliases") or [])]]

        item_report = {
            "slug": slug,
            "name": name,
            "curated": len(set(curated_ids)),
            "auto_publish_local": 0,
            "candidate_local": 0,
            "search_matches": 0,
            "search_candidates": 0,
        }
        published: set[str] = set()
        candidates: set[str] = set()

        for post_id in curated_ids:
            if store.get_post(post_id):
                _record(
                    version,
                    post_id,
                    slug,
                    "curated",
                    "human_approved",
                    "人工审定",
                    "published",
                )
                published.add(post_id)

        for term in auto_terms:
            for row in store.find_posts_by_text(term, excludes):
                _record(version, row["id"], slug, "local", term, term, "published")
                published.add(row["id"])

        for term in candidate_terms:
            for row in store.find_posts_by_text(term, excludes):
                _record(version, row["id"], slug, "local", term, term, "candidate")
                candidates.add(row["id"])

        if search_pages and session is not None and limiter is not None:
            from fetch_weibo import fetch_search, post_id as mblog_id

            for query in item.get("search_queries") or []:
                query = str(query).strip()
                if not query:
                    continue
                for page in range(1, search_pages + 1):
                    results = fetch_search(session, limiter, query, page)
                    if not results:
                        break
                    for index, mblog in enumerate(results, 1):
                        if str((mblog.get("user") or {}).get("id") or "") != config.TARGET_UID:
                            continue
                        pid = mblog_id(mblog)
                        post = store.get_post(pid)
                        if not post:
                            report["missing_search_posts"].append(pid)
                            continue
                        text = post.get("text_plain") or ""
                        matched = _contains_any(text, auto_terms)
                        status = "published" if matched else "candidate"
                        rank = (page - 1) * 20 + index
                        _record(
                            version,
                            pid,
                            slug,
                            "weibo_search",
                            query,
                            matched or query,
                            status,
                            rank,
                        )
                        item_report["search_matches"] += 1
                        if status == "published":
                            published.add(pid)
                            store.link_post_meme(pid, slug, matched)
                        else:
                            candidates.add(pid)
                            item_report["search_candidates"] += 1

        item_report["auto_publish_local"] = len(published - set(curated_ids))
        item_report["candidate_local"] = len(candidates - published)
        report["memes"].append(item_report)

    report["missing_search_posts"] = sorted(set(report["missing_search_posts"]))
    store.finish_baseline_run(version, report)
    return report


def report(version: str) -> dict:
    run = store.get_baseline_run(version)
    if not run:
        raise ValueError(f"未找到 baseline：{version}")
    return {
        "run": run,
        "candidates": store.baseline_candidate_stats(version),
    }
