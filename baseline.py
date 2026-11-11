"""N宗罪检索词的版本化 baseline 构建与审计。"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import config
import memes
import store


EVIDENCE_SCHEMA_VERSION = 1
EVIDENCE_FIELDS = (
    "post_id",
    "mid",
    "uid",
    "screen_name",
    "created_at",
    "text",
    "reposts_count",
    "comments_count",
    "attitudes_count",
    "retweeted_user",
)


def _contains_any(text: str, terms: list[str]) -> str:
    lowered = (text or "").lower()
    for term in terms:
        if term.lower() in lowered:
            return term
    return ""


def _first_excluded(text: str, excludes: tuple[str, ...]) -> str:
    lowered = (text or "").lower()
    for term in excludes:
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


def _query_pack(seed: list[dict]) -> dict[str, list[str]]:
    """按查询词合并罪名，保证同一查询只消耗一次请求。"""
    queries: dict[str, list[str]] = {}
    for item in seed:
        slug = str(item.get("slug") or item.get("name") or "").strip()
        for raw_query in item.get("search_queries") or []:
            query = str(raw_query).strip()
            if query and slug not in queries.setdefault(query, []):
                queries[query].append(slug)
    return queries


def _search_snapshot(mblog: dict, rank: int) -> dict:
    from fetch_weibo import clean_text, post_id

    user = mblog.get("user") or {}
    retweeted = mblog.get("retweeted_status") or {}
    text = mblog.get("text_raw") or mblog.get("text") or ""
    values = {
        "post_id": post_id(mblog),
        "mid": str(mblog.get("mid") or mblog.get("idstr") or mblog.get("id") or ""),
        "uid": str(user.get("id") or ""),
        "screen_name": user.get("screen_name") or "",
        "created_at": mblog.get("created_at") or "",
        "text": clean_text(text),
        "reposts_count": int(mblog.get("reposts_count") or 0),
        "comments_count": int(mblog.get("comments_count") or 0),
        "attitudes_count": int(mblog.get("attitudes_count") or 0),
        "retweeted_user": (retweeted.get("user") or {}).get("screen_name") or "",
    }
    available = [
        key for key in EVIDENCE_FIELDS if values[key] not in ("", None)
    ]
    missing = [key for key in EVIDENCE_FIELDS if key not in available]
    return {
        **values,
        "search_rank": rank,
        "available_fields": available,
        "missing_api_fields": missing,
    }


def _digest(payload: dict) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _finalize_evidence(evidence: dict) -> dict:
    missing: dict[str, dict] = {}
    for query_result in evidence["queries"]:
        for hit in query_result["hits"]:
            if hit["corpus_present"]:
                continue
            item = missing.setdefault(
                hit["post_id"],
                {
                    **hit,
                    "queries": [],
                    "search_ranks": [],
                },
            )
            if query_result["query"] not in item["queries"]:
                item["queries"].append(query_result["query"])
            item["search_ranks"].append(hit["search_rank"])
    evidence["summary"] = {
        "query_count": len(evidence["queries"]),
        "request_count": len(evidence["queries"]),
        "result_count": sum(
            row["unique_target_results"] for row in evidence["queries"]
        ),
        "corpus_present_count": sum(
            row["corpus_present_count"] for row in evidence["queries"]
        ),
        "corpus_missing_count": sum(
            row["corpus_missing_count"] for row in evidence["queries"]
        ),
        "missing_post_ids": sorted(missing),
        "missing_posts": [missing[key] for key in sorted(missing)],
    }
    without_digest = {
        key: value for key, value in evidence.items() if key != "evidence_sha256"
    }
    evidence["evidence_sha256"] = _digest(without_digest)
    return evidence


def _write_probe_result(output: Path, evidence: dict) -> dict:
    _write_json(output, evidence)
    return evidence


def probe_search(
    label: str,
    output_path: str | Path,
    seed_path=None,
    session=None,
    limiter=None,
    fetcher=None,
) -> dict:
    """每个唯一搜索词只取第 1 页，并逐查询保存证据。"""
    output = Path(output_path)
    if output.exists():
        raise FileExistsError(f"probe 证据已存在：{output}")

    seed = memes.load_seed(seed_path) if seed_path else memes.load_seed()
    query_pack = _query_pack(seed)
    if fetcher is None:
        from fetch_weibo import RateLimiter, fetch_search, make_session

        session = session or make_session()
        limiter = limiter or RateLimiter()
        fetcher = lambda query, page: fetch_search(session, limiter, query, page)

    evidence = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "label": label,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_cutoff": config.SNAPSHOT_CUTOFF,
        "target_uid": config.TARGET_UID,
        "page": 1,
        "complete": False,
        "stop_reason": "",
        "queries": [],
    }
    _write_json(output, _finalize_evidence(evidence))

    for query, slugs in query_pack.items():
        try:
            results = fetcher(query, 1)
        except Exception as exc:
            evidence["stop_reason"] = f"{type(exc).__name__}: {exc}"
            _write_json(output, _finalize_evidence(evidence))
            raise

        seen: set[str] = set()
        hits: list[dict] = []
        for rank, mblog in enumerate(results, 1):
            hit = _search_snapshot(mblog, rank)
            if hit["uid"] != config.TARGET_UID or not hit["post_id"]:
                continue
            if hit["post_id"] in seen:
                continue
            seen.add(hit["post_id"])
            hit["corpus_present"] = store.get_post(hit["post_id"]) is not None
            hits.append(hit)

        evidence["queries"].append(
            {
                "query": query,
                "page": 1,
                "meme_slugs": slugs,
                "results_seen": len(results),
                "unique_target_results": len(hits),
                "corpus_present_count": sum(
                    1 for hit in hits if hit["corpus_present"]
                ),
                "corpus_missing_count": sum(
                    1 for hit in hits if not hit["corpus_present"]
                ),
                "hits": hits,
            }
        )
        _write_json(output, _finalize_evidence(evidence))

    evidence["complete"] = True
    return _write_probe_result(output, _finalize_evidence(evidence))


def load_search_evidence(path: str | Path) -> dict:
    evidence = json.loads(Path(path).read_text(encoding="utf-8"))
    if evidence.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise ValueError("probe 证据版本不受支持")
    if not evidence.get("complete"):
        raise ValueError("probe 证据不完整，不能构建 baseline")
    if evidence.get("target_uid") != config.TARGET_UID:
        raise ValueError("probe 目标 UID 与当前配置不一致")
    if evidence.get("page") != 1:
        raise ValueError("probe 只允许第 1 页证据")
    expected = evidence.get("evidence_sha256")
    payload = {key: value for key, value in evidence.items() if key != "evidence_sha256"}
    if not expected or expected != _digest(payload):
        raise ValueError("probe 证据校验失败")
    return evidence


def _search_hits_by_slug(
    evidence: dict, seed: list[dict]
) -> dict[str, list[tuple[str, dict]]]:
    allowed: dict[str, set[str]] = {}
    for item in seed:
        slug = str(item.get("slug") or item.get("name") or "").strip()
        allowed[slug] = {
            str(query).strip()
            for query in item.get("search_queries") or []
            if str(query).strip()
        }

    grouped: dict[str, list[tuple[str, dict]]] = {}
    covered: dict[str, set[str]] = {}
    for query_result in evidence.get("queries") or []:
        query = str(query_result.get("query") or "").strip()
        if query_result.get("page") != 1:
            raise ValueError("probe 只允许第 1 页证据")
        for slug in query_result.get("meme_slugs") or []:
            if query not in allowed.get(slug, set()):
                raise ValueError(f"probe 查询不属于「{slug}」：{query}")
            covered.setdefault(slug, set()).add(query)
            for hit in query_result.get("hits") or []:
                if hit.get("uid") != config.TARGET_UID:
                    raise ValueError("probe 命中包含非目标 UID")
                grouped.setdefault(slug, []).append((query, hit))
    for slug, queries in allowed.items():
        if queries - covered.get(slug, set()):
            raise ValueError(f"probe 证据未覆盖「{slug}」的全部搜索词")
    return grouped


def build(
    version: str,
    search_pages: int = 0,
    seed_path=None,
    search_evidence=None,
) -> dict:
    """构建并冻结一个 baseline；可消费已保存的一页搜索证据。"""
    if search_pages and search_pages != 1:
        raise ValueError("受控 probe 只允许 --search-pages 1")
    if search_pages and search_evidence:
        raise ValueError("search_pages 与 search_evidence 不能同时使用")

    store.init_db()
    if store.get_baseline_run(version):
        raise ValueError(f"baseline 已存在：{version}")

    memes.sync(seed_path) if seed_path else memes.sync()
    seed = memes.load_seed(seed_path) if seed_path else memes.load_seed()
    evidence = None
    if search_pages:
        evidence = probe_search(
            label=f"{version}-search-probe",
            output_path=Path("data") / f"{version}-search-probe.json",
            seed_path=seed_path,
        )
    elif search_evidence:
        evidence = load_search_evidence(search_evidence)
    search_hits = _search_hits_by_slug(evidence, seed) if evidence else {}

    store.create_baseline_run(version, config.SNAPSHOT_CUTOFF)
    report_data = {
        "version": version,
        "snapshot_cutoff": config.SNAPSHOT_CUTOFF,
        "weibo_search_pages": 1 if evidence else 0,
        "search_evidence": (
            {
                "label": evidence.get("label"),
                "generated_at": evidence.get("generated_at"),
                "evidence_sha256": evidence.get("evidence_sha256"),
                "request_count": evidence.get("summary", {}).get("request_count", 0),
            }
            if evidence
            else None
        ),
        "memes": [],
        "missing_search_posts": (evidence or {}).get("summary", {}).get(
            "missing_posts", []
        ),
    }

    for item in seed:
        slug = str(item.get("slug") or item.get("name") or "").strip()
        name = str(item.get("name") or slug).strip()
        mode = str(item.get("match_mode") or "rule").strip()
        excludes = tuple(str(x) for x in (item.get("exclude") or []))
        curated_ids = [str(x) for x in (item.get("curated_post_ids") or [])]
        auto_terms = [str(x) for x in (item.get("auto_publish_terms") or [])]
        candidate_terms = [str(x) for x in (item.get("candidate_terms") or [])]
        if mode == "rule":
            auto_terms = [name, *[str(x) for x in (item.get("aliases") or [])]]

        item_report = {
            "slug": slug,
            "name": name,
            "curated": 0,
            "published_auto_local": 0,
            "candidate_local": 0,
            "published_search": 0,
            "candidate_search": 0,
            "excluded_search": 0,
            "unique_published": 0,
            "unique_candidates": 0,
        }
        published: set[str] = set()
        candidates: set[str] = set()
        curated: set[str] = set()
        local_published: set[str] = set()
        local_candidates: set[str] = set()
        search_published: set[str] = set()
        search_candidates: set[str] = set()
        search_excluded: set[str] = set()

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
                curated.add(post_id)
                published.add(post_id)

        for term in auto_terms:
            for row in store.find_posts_by_text(term, excludes):
                _record(version, row["id"], slug, "local", term, term, "published")
                local_published.add(row["id"])
                published.add(row["id"])

        for term in candidate_terms:
            for row in store.find_posts_by_text(term, excludes):
                _record(version, row["id"], slug, "local", term, term, "candidate")
                local_candidates.add(row["id"])
                candidates.add(row["id"])

        for query, hit in search_hits.get(slug, []):
            pid = str(hit.get("post_id") or "")
            post = store.get_post(pid)
            if not post:
                continue
            text = post.get("text_plain") or hit.get("text") or ""
            matched = _contains_any(text, auto_terms)
            excluded = _first_excluded(text, excludes)
            if excluded:
                status = "excluded"
                matched_term = f"excluded:{excluded}"
            elif matched:
                status = "published"
                matched_term = matched
            else:
                status = "candidate"
                matched_term = ""
            _record(
                version,
                pid,
                slug,
                "weibo_search",
                query,
                matched_term,
                status,
                int(hit["search_rank"]),
            )
            if status == "published":
                store.link_post_meme(pid, slug, matched)
                search_published.add(pid)
                published.add(pid)
            elif status == "candidate":
                search_candidates.add(pid)
                candidates.add(pid)
            else:
                search_excluded.add(pid)

        item_report["curated"] = len(curated)
        item_report["published_auto_local"] = len(local_published - curated)
        item_report["candidate_local"] = len(local_candidates - published)
        item_report["published_search"] = len(search_published - curated)
        item_report["candidate_search"] = len(search_candidates - published)
        item_report["excluded_search"] = len(search_excluded - published)
        item_report["unique_published"] = len(published)
        item_report["unique_candidates"] = len(candidates - published)
        report_data["memes"].append(item_report)

    store.finish_baseline_run(version, report_data)
    return report_data


def report(version: str) -> dict:
    run = store.get_baseline_run(version)
    if not run:
        raise ValueError(f"未找到 baseline：{version}")
    return {
        "run": run,
        "candidates": store.baseline_candidate_stats(version),
        "queries": store.baseline_query_stats(version),
    }


def _md(value) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _csv_safe(value) -> str:
    text = str(value or "")
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def export_review(version: str, output_path: str | Path) -> dict:
    """导出 JSON、CSV 和 Markdown 人工审核清单。"""
    result = report(version)
    rows = store.baseline_review_rows(version)
    output = Path(output_path)
    output.mkdir(parents=True, exist_ok=True)

    summary = {
        "version": version,
        "snapshot_cutoff": result["run"]["snapshot_cutoff"],
        "search_evidence": result["run"]["summary"].get("search_evidence"),
        "candidate_stats": result["candidates"],
        "query_stats": result["queries"],
        "review_row_count": len(rows),
    }
    _write_json(output / "summary.json", summary)
    _write_json(output / "review.json", rows)

    columns = [
        "meme_slug",
        "status",
        "source",
        "query",
        "search_rank",
        "matched_term",
        "post_id",
        "created_at",
        "text_plain",
        "review_decision",
        "review_note",
    ]
    with (output / "review.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **row,
                    "text_plain": _csv_safe(row.get("text_plain")),
                    "review_decision": "",
                    "review_note": "",
                }
            )

    lines = [
        f"# N宗罪 baseline 人工审核：{version}",
        "",
        f"- 数据截止：{result['run']['snapshot_cutoff']}",
        f"- 审核行数：{len(rows)}",
        (
            "- 搜索证据 SHA256："
            f"`{summary['search_evidence']['evidence_sha256']}`"
            if summary["search_evidence"]
            else "- 搜索证据：无（仅本地规则）"
        ),
        "",
        "审核决定填写 `keep`、`candidate` 或 `exclude`；本条清单不自动修改人工审定集合。",
        "",
        "| 罪名 | 状态 | 来源 | 查询 | 排名 | 命中词 | post id | 正文摘要 | 决定 | 备注 |",
        "|---|---|---|---|---:|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {meme_slug} | {status} | {source} | {query} | {rank} | "
            "{matched_term} | {post_id} | {text} |  |  |".format(
                meme_slug=_md(row.get("meme_slug")),
                status=_md(row.get("status")),
                source=_md(row.get("source")),
                query=_md(row.get("query")),
                rank=_md(row.get("search_rank")),
                matched_term=_md(row.get("matched_term")),
                post_id=_md(row.get("post_id")),
                text=_md((row.get("text_plain") or "")[:180]),
            )
        )
    (output / "review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "version": version,
        "output": str(output),
        "review_row_count": len(rows),
        "files": ["summary.json", "review.json", "review.csv", "review.md"],
    }
