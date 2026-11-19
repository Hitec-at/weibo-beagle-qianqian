"""每日数据任务清单与统一人审材料。"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import store
from weibo_links import markdown_link, original_url

MANIFEST_SCHEMA_VERSION = 1
PROPOSAL_SCHEMA_VERSION = "semantic-proposals-v1"
PROPOSAL_KINDS = {
    "entity_alias",
    "search_term",
    "category_boundary",
    "subcategory",
    "new_category",
    "semantic_document",
}


def _write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def capture_classification_counts(connection=None) -> dict:
    """Capture category and N宗罪 association counts for delta reporting."""
    own_connection = connection is None
    if connection is None:
        store.init_db()
        connection = store.connect()
    try:
        category_rows = connection.execute(
            """
            SELECT category, COUNT(*) AS associations,
                   COUNT(DISTINCT post_id) AS unique_posts
            FROM post_categories
            GROUP BY category
            ORDER BY category
            """
        ).fetchall()
        meme_rows = connection.execute(
            """
            SELECT meme_slug, COUNT(*) AS associations,
                   COUNT(DISTINCT post_id) AS unique_posts
            FROM post_memes
            GROUP BY meme_slug
            ORDER BY meme_slug
            """
        ).fetchall()
    finally:
        if own_connection:
            connection.close()

    return {
        "categories": {
            str(row["category"]): {
                "associations": int(row["associations"]),
                "unique_posts": int(row["unique_posts"]),
            }
            for row in category_rows
        },
        "memes": {
            str(row["meme_slug"]): {
                "associations": int(row["associations"]),
                "unique_posts": int(row["unique_posts"]),
            }
            for row in meme_rows
        },
    }


def _validated_count_map(value, label: str) -> dict[str, dict[str, int]]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是对象")
    normalized = {}
    for key in sorted(value):
        counts = value[key]
        if not isinstance(counts, dict):
            raise ValueError(f"{label}.{key} 必须是对象")
        associations = int(counts.get("associations") or 0)
        unique_posts = int(counts.get("unique_posts") or 0)
        if associations < 0 or unique_posts < 0:
            raise ValueError(f"{label}.{key} 不能为负数")
        normalized[str(key)] = {
            "associations": associations,
            "unique_posts": unique_posts,
        }
    return normalized


def _validated_classification_counts(
    value,
) -> dict[str, dict[str, dict[str, int]]]:
    if value in (None, {}):
        return {"categories": {}, "memes": {}}
    if not isinstance(value, dict):
        raise ValueError("classification_counts 必须是对象")
    return {
        "categories": _validated_count_map(
            value.get("categories"), "classification_counts.categories"
        ),
        "memes": _validated_count_map(
            value.get("memes"), "classification_counts.memes"
        ),
    }


def _delta_rows(
    before: dict[str, dict[str, int]],
    after: dict[str, dict[str, int]],
    key_name: str,
) -> list[dict]:
    rows = []
    for key in sorted(set(before) | set(after)):
        previous = before.get(key, {})
        current = after.get(key, {})
        before_associations = int(previous.get("associations") or 0)
        after_associations = int(current.get("associations") or 0)
        before_posts = int(previous.get("unique_posts") or 0)
        after_posts = int(current.get("unique_posts") or 0)
        rows.append(
            {
                key_name: key,
                "before_associations": before_associations,
                "after_associations": after_associations,
                "association_delta": after_associations - before_associations,
                "before_unique_posts": before_posts,
                "after_unique_posts": after_posts,
                "unique_posts_delta": after_posts - before_posts,
            }
        )
    return rows


def classification_deltas(before: dict, after: dict) -> dict:
    """Return signed deltas for categories and N宗罪 meme associations."""
    previous = _validated_classification_counts(before)
    current = _validated_classification_counts(after)
    return {
        "category_deltas": _delta_rows(
            previous["categories"], current["categories"], "category"
        ),
        "meme_deltas": _delta_rows(
            previous["memes"], current["memes"], "meme_slug"
        ),
    }


def write_run_manifest(output_dir: str | Path, state: dict) -> dict:
    """校验并写入每日任务完成清单。"""
    if state.get("status") not in {"success", "failed"}:
        raise ValueError("status 必须是 success 或 failed")
    if not str(state.get("stamp") or "").strip():
        raise ValueError("stamp 不能为空")
    if not isinstance(state.get("request_ledger"), dict):
        raise ValueError("request_ledger 必须是对象")
    if not isinstance(state.get("data"), dict):
        raise ValueError("data 必须是对象")

    before_counts = state.get("classification_before") or {}
    after_counts = state.get("classification_after") or {}
    has_before = before_counts not in ({}, None)
    has_after = after_counts not in ({}, None)
    if state["status"] == "success" and bool(has_before) != bool(has_after):
        raise ValueError(
            "成功清单必须同时提供 classification_before 和 classification_after"
        )
    checked_before = _validated_classification_counts(before_counts)
    checked_after = _validated_classification_counts(after_counts)
    deltas = (
        classification_deltas(checked_before, checked_after)
        if has_before and has_after
        else {"category_deltas": [], "meme_deltas": []}
    )

    request_ledger = state["request_ledger"]
    used = int(request_ledger.get("used") or 0)
    limit = int(request_ledger.get("limit") or 500)
    if used < 0 or limit <= 0 or used > limit:
        raise ValueError("request_ledger 必须满足 0 <= used <= limit")

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "stamp": str(state["stamp"]),
        "status": state["status"],
        "started_at": str(state.get("started_at") or ""),
        "finished_at": str(state.get("finished_at") or ""),
        "request_ledger": {
            "used": used,
            "limit": limit,
            "remaining": limit - used,
            "by_category": request_ledger.get("by_category") or {},
        },
        "data": state["data"],
        "baseline": state.get("baseline") or {},
        "artifacts": state.get("artifacts") or {},
        "classification_counts": {
            "before": checked_before if has_before else {},
            "after": checked_after if has_after else {},
        },
        **deltas,
        "failed_stage": state.get("failed_stage") or "",
        "error": state.get("error") or "",
    }
    if manifest["status"] == "success":
        data = manifest["data"]
        for key in ("release", "asset_name", "sha256"):
            if not str(data.get(key) or "").strip():
                raise ValueError(f"成功清单缺少 data.{key}")
        if not str((manifest["baseline"] or {}).get("version") or "").strip():
            raise ValueError("成功清单缺少 baseline.version")
    else:
        if not manifest["failed_stage"]:
            raise ValueError("失败清单必须提供 failed_stage")

    path = Path(output_dir) / "run-manifest.json"
    _write_json(path, manifest)
    return {"path": str(path), "status": manifest["status"]}


def _validate_evidence(evidence: list, proposal_key: str) -> list[dict]:
    if not evidence:
        raise ValueError(f"{proposal_key} 至少需要一条证据")
    normalized = []
    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError(f"{proposal_key} 的证据必须是对象")
        post_id = str(item.get("post_id") or "").strip()
        if not post_id:
            raise ValueError(f"{proposal_key} 的证据缺少 post_id")
        normalized.append(
            {
                "post_id": post_id,
                "comment_id": str(item.get("comment_id") or ""),
                "quote": str(item.get("quote") or ""),
                "url": original_url({"id": post_id}),
            }
        )
    return normalized


def load_semantic_proposals(path: str | Path) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    proposals = payload.get("proposals") if isinstance(payload, dict) else payload
    if not isinstance(proposals, list):
        raise ValueError("语义建议文件必须包含 proposals 数组")

    seen: set[str] = set()
    normalized = []
    for raw in proposals:
        if not isinstance(raw, dict):
            raise ValueError("每条语义建议必须是对象")
        key = str(raw.get("proposal_key") or "").strip()
        kind = str(raw.get("kind") or "").strip()
        title = str(raw.get("title") or "").strip()
        target = str(raw.get("target") or "").strip()
        change = raw.get("proposed_change")
        confidence = float(raw.get("confidence"))
        if not key or key in seen:
            raise ValueError("proposal_key 不能为空或重复")
        if kind not in PROPOSAL_KINDS:
            raise ValueError(f"{key} 的建议类型无效：{kind}")
        if not title or not target or change in ("", None, {}, []):
            raise ValueError(f"{key} 缺少 title、target 或 proposed_change")
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError(f"{key} 的 confidence 必须在 0–1 之间")
        seen.add(key)
        normalized.append(
            {
                "proposal_key": key,
                "kind": kind,
                "title": title,
                "target": target,
                "proposed_change": change,
                "reason": str(raw.get("reason") or ""),
                "confidence": confidence,
                "evidence": _validate_evidence(raw.get("evidence") or [], key),
                "status": "pending",
            }
        )
    return normalized


def _review_markdown(
    stamp: str,
    human_items: list[dict],
    proposals: list[dict],
) -> str:
    lines = [
        f"# 每日统一人审清单（{stamp}）",
        "",
        "说明：规则、分类和文档建议只供审核，不会自动应用。",
        "",
        f"## 第一部分：待确认微博（{len(human_items)}）",
        "",
    ]
    for item in human_items:
        evidence = item.get("evidence")
        if evidence is None:
            try:
                evidence = json.loads(item.get("evidence_json") or "[]")
            except ValueError:
                evidence = item.get("evidence_json") or ""
        post_link = markdown_link(
            item["post_id"], item.get("url") or ""
        )
        lines.extend(
            [
                f"- {post_link} · `{item['candidate_key']}` · "
                f"{item.get('meme_slug') or ''} · 置信度 "
                f"{float(item.get('confidence') or 0):.2f}",
                f"  - 理由：{item.get('reason') or ''}",
                f"  - 证据：`{json.dumps(evidence, ensure_ascii=False)}`",
            ]
        )
    if not human_items:
        lines.append("无。")

    lines.extend(["", f"## 第二部分：规则与内容建议（{len(proposals)}）", ""])
    for proposal in proposals:
        evidence = "、".join(
            (
                f"[微博原文（{item['post_id']}"
                + (f"/{item['comment_id']}" if item["comment_id"] else "")
                + f"）]({item['url']})"
                if item.get("url")
                else (
                    item["post_id"]
                    + (
                        f"/{item['comment_id']}"
                        if item["comment_id"]
                        else ""
                    )
                )
            )
            for item in proposal["evidence"]
        )
        lines.extend(
            [
                f"### {proposal['title']}",
                "",
                f"- 编号：`{proposal['proposal_key']}`",
                f"- 类型：`{proposal['kind']}`",
                f"- 对象：`{proposal['target']}`",
                f"- 建议：{json.dumps(proposal['proposed_change'], ensure_ascii=False)}",
                f"- 理由：{proposal['reason']}",
                f"- 证据：{evidence}",
                f"- 置信度：{proposal['confidence']:.2f}",
                "",
            ]
        )
    if not proposals:
        lines.append("无。")
    lines.append("")
    return "\n".join(lines)


def build_review_dossier(
    candidate_dir: str | Path,
    output_dir: str | Path,
    stamp: str,
    proposals_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    post_lookup=None,
) -> dict:
    """合并待确认微博和语义建议，生成统一人审清单。"""
    post_lookup = post_lookup or store.get_post
    human_path = Path(candidate_dir) / "human-review.json"
    grouped = (
        json.loads(human_path.read_text(encoding="utf-8"))
        if human_path.exists()
        else {}
    )
    human_items = [
        item
        for items in grouped.values()
        for item in (items if isinstance(items, list) else [])
    ]
    for item in human_items:
        post = post_lookup(str(item.get("post_id") or "")) or {}
        item["url"] = (
            original_url(post)
            or item.get("url")
            or original_url({"id": item.get("post_id")})
        )
    proposals = (
        load_semantic_proposals(proposals_path) if proposals_path else []
    )
    for proposal in proposals:
        for evidence in proposal["evidence"]:
            post = post_lookup(str(evidence["post_id"])) or {}
            evidence["url"] = (
                original_url(post)
                or evidence.get("url")
                or original_url({"id": evidence["post_id"]})
            )
    manifest = (
        json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        if manifest_path
        else {}
    )
    payload = {
        "schema_version": "daily-review-v1",
        "stamp": stamp,
        "run_manifest": manifest,
        "human_candidates": human_items,
        "semantic_proposals": proposals,
    }
    output = Path(output_dir)
    json_path = output / "daily-review.json"
    markdown_path = output / "daily-review.md"
    _write_json(json_path, payload)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(
        _review_markdown(stamp, human_items, proposals),
        encoding="utf-8",
    )
    return {
        "human_candidates": len(human_items),
        "semantic_proposals": len(proposals),
        "json": str(json_path),
        "markdown": str(markdown_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="每日任务清单与人审材料")
    sub = parser.add_subparsers(dest="command", required=True)

    manifest = sub.add_parser("manifest", help="生成每日任务完成清单")
    manifest.add_argument("--output", required=True)
    manifest.add_argument("--state", required=True, help="任务状态 JSON")

    counts = sub.add_parser("counts", help="采集当前分类和 N宗罪关联数量")
    counts.add_argument("--output", required=True)

    review = sub.add_parser("review", help="生成统一人审清单")
    review.add_argument("--candidate-dir", required=True)
    review.add_argument("--output", required=True)
    review.add_argument("--stamp", required=True)
    review.add_argument("--proposals", default=None)
    review.add_argument("--manifest", default=None)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "manifest":
        state = json.loads(Path(args.state).read_text(encoding="utf-8"))
        result = write_run_manifest(args.output, state)
    elif args.command == "counts":
        counts = capture_classification_counts()
        output = Path(args.output)
        _write_json(output, counts)
        result = {
            "path": str(output),
            "categories": len(counts["categories"]),
            "memes": len(counts["memes"]),
        }
    else:
        result = build_review_dossier(
            args.candidate_dir,
            args.output,
            args.stamp,
            proposals_path=args.proposals,
            manifest_path=args.manifest,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
