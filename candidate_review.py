"""内容候选审核队列：为子 agent 导出批次、保存决定并汇总人审项。"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import store

ROOT = Path(__file__).resolve().parent
RULES_VERSION = "candidate-review-v1"
AUTO_APPLY_CONFIDENCE = 0.85
DECISIONS = {"accept", "reject", "uncertain"}
RULES_PATH = ROOT / "docs" / "review" / "candidate-review-v1.md"
FEW_SHOTS_PATH = ROOT / "docs" / "review" / "candidate-few-shots-v1.json"


def candidate_key(source_type: str, post_id: str, meme_slug: str) -> str:
    value = "\0".join((source_type, post_id, meme_slug))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_list(value: str | None) -> list:
    try:
        parsed = json.loads(value or "[]")
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


def _meme_context(slug: str) -> dict:
    meme = store.get_meme(slug) or {}
    return {
        "slug": slug,
        "name": meme.get("name") or slug,
        "origin": meme.get("origin") or "",
        "explanation": meme.get("explanation") or "",
        "boundary": meme.get("boundary") or "",
        "aliases": _json_list(meme.get("aliases_json")),
        "exclude": _json_list(meme.get("exclude_json")),
        "behavior_tags": _json_list(meme.get("behavior_tags_json")),
    }


def _post_context(post: dict | None) -> dict:
    if not post:
        return {"missing_from_corpus": True}
    return {
        "id": post.get("id"),
        "screen_name": post.get("screen_name") or "",
        "created_at": post.get("created_at") or "",
        "text": post.get("text_plain") or "",
        "reposts_count": int(post.get("reposts_count") or 0),
        "comments_count": int(post.get("comments_count") or 0),
        "attitudes_count": int(post.get("attitudes_count") or 0),
        "retweeted_user": post.get("retweeted_user") or "",
        "has_pics": int(post.get("has_pics") or 0),
        "is_video": int(post.get("is_video") or 0),
    }


def _comment_context(post_id: str) -> dict:
    rows = store.comments_for_post(post_id)
    roots = [row for row in rows if not row.get("is_reply")]
    replies: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("is_reply"):
            replies[str(row.get("root_id") or "")].append(
                {
                    "comment_id": str(row.get("id") or ""),
                    "text": row.get("text_plain") or "",
                    "like_count": int(row.get("like_count") or 0),
                    "author_liked": int(row.get("author_liked") or 0),
                    "author_liked_known": int(
                        row.get("author_liked_known") or 0
                    ),
                }
            )
    thread = store.comment_thread(post_id)
    return {
        "thread": thread,
        "roots": [
            {
                "comment_id": str(root.get("id") or ""),
                "text": root.get("text_plain") or "",
                "like_count": int(root.get("like_count") or 0),
                "author_liked": int(root.get("author_liked") or 0),
                "author_liked_known": int(root.get("author_liked_known") or 0),
                "page_rank": root.get("page_rank"),
                "like_rank": root.get("like_rank"),
                "selected_by_default": int(root.get("selected_by_default") or 0),
                "selected_by_likes": int(root.get("selected_by_likes") or 0),
                "reply_complete": int(root.get("reply_complete") or 0),
                "replies": replies.get(str(root.get("id") or ""), []),
            }
            for root in roots
        ],
    }


def pending_items(limit: int | None = None) -> list[dict]:
    """返回尚未审核的候选，同一 post/meme 只取最新证据。"""
    store.init_db()
    conn = store.connect()
    try:
        rows = conn.execute(
            """
            SELECT post_id, meme_slug, source, query, search_rank,
                   matched_term, status, observed_at
            FROM baseline_candidates
            ORDER BY observed_at DESC
            """
        ).fetchall()
    finally:
        conn.close()

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        item = dict(row)
        key = candidate_key("baseline", item["post_id"], item["meme_slug"])
        grouped[key].append(item)

    reviewed = store.candidate_review_keys()
    pending: list[dict] = []
    for key, candidates in grouped.items():
        if key in reviewed:
            continue
        if any(item["status"] in {"published", "excluded"} for item in candidates):
            continue
        item = next(
            (candidate for candidate in candidates if candidate["status"] == "candidate"),
            None,
        )
        if not item:
            continue
        post_id = str(item["post_id"])
        meme_slug = str(item["meme_slug"])
        pending.append(
            {
                "candidate_key": key,
                "source_type": "baseline",
                "post_id": post_id,
                "meme_slug": meme_slug,
                "baseline": {
                    "source": item["source"],
                    "query": item["query"],
                    "search_rank": item["search_rank"],
                    "matched_term": item["matched_term"],
                    "observed_at": item["observed_at"],
                },
                "meme": _meme_context(meme_slug),
                "post": _post_context(store.get_post(post_id)),
                "comments": _comment_context(post_id),
            }
        )

    pending.sort(
        key=lambda item: (
            item["meme_slug"],
            item["baseline"]["observed_at"] or "",
            item["candidate_key"],
        ),
        reverse=True,
    )
    return pending[:limit] if limit is not None else pending


def export_batches(
    output_dir: str | Path,
    limit: int = 150,
    batch_size: int = 50,
) -> dict:
    if limit < 1:
        raise ValueError("limit 必须至少为 1")
    if not 1 <= batch_size <= 50:
        raise ValueError("batch_size 必须在 1–50 之间")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    items = pending_items(limit)
    batches = []
    for index, start in enumerate(range(0, len(items), batch_size), start=1):
        batch_id = f"batch-{index:02d}"
        path = output / f"{batch_id}.json"
        payload = {
            "rules_version": RULES_VERSION,
            "batch_id": batch_id,
            "reviewer": f"agent-{index}",
            "rules": str(RULES_PATH.relative_to(ROOT)),
            "few_shots": str(FEW_SHOTS_PATH.relative_to(ROOT)),
            "items": items[start : start + batch_size],
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        batches.append(
            {
                "batch_id": batch_id,
                "reviewer": f"agent-{index}",
                "path": str(path),
                "count": len(payload["items"]),
            }
        )

    manifest = {
        "rules_version": RULES_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_pending": len(items),
        "batches": batches,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _normalize_decision(decision: dict, batch_id: str, reviewer: str) -> dict:
    value = str(decision.get("decision") or "").strip().lower()
    if value not in DECISIONS:
        raise ValueError(f"无效决定：{value or '空'}")
    confidence = float(decision.get("confidence"))
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("confidence 必须在 0–1 之间")
    reason = str(decision.get("reason") or "").strip()
    if not reason:
        raise ValueError("reason 不能为空")
    evidence = decision.get("evidence")
    if not isinstance(evidence, (dict, list)):
        raise ValueError("evidence 必须是对象或数组")
    proposed = decision.get("proposed_changes") or {}
    if not isinstance(proposed, dict):
        raise ValueError("proposed_changes 必须是对象")
    return {
        "candidate_key": str(decision.get("candidate_key") or ""),
        "decision": value,
        "confidence": confidence,
        "reason": reason,
        "evidence": evidence,
        "proposed_changes": proposed,
        "batch_id": str(decision.get("batch_id") or batch_id),
        "reviewer": str(decision.get("reviewer") or reviewer),
    }


def record_decisions(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    batch_id = str(payload.get("batch_id") or "")
    reviewer = str(payload.get("reviewer") or "")
    decisions = payload.get("decisions")
    if not batch_id or not reviewer:
        raise ValueError("决定文件必须包含 batch_id 和 reviewer")
    if not isinstance(decisions, list):
        raise ValueError("decisions 必须是数组")

    valid = {item["candidate_key"]: item for item in pending_items(None)}
    accepted = rejected = human = skipped = invalid = 0
    for raw in decisions:
        try:
            decision = _normalize_decision(raw, batch_id, reviewer)
        except (TypeError, ValueError):
            invalid += 1
            continue
        candidate = valid.get(decision["candidate_key"])
        if not candidate:
            skipped += 1
            continue
        auto_apply = (
            decision["decision"] in {"accept", "reject"}
            and decision["confidence"] >= AUTO_APPLY_CONFIDENCE
            and bool(decision["evidence"])
        )
        review = {
            "candidate_key": decision["candidate_key"],
            "source_type": candidate["source_type"],
            "post_id": candidate["post_id"],
            "meme_slug": candidate["meme_slug"],
            "comment_id": "",
            "decision": decision["decision"],
            "confidence": decision["confidence"],
            "reason": decision["reason"],
            "evidence_json": json.dumps(
                decision["evidence"], ensure_ascii=False
            ),
            "proposed_changes_json": json.dumps(
                decision["proposed_changes"], ensure_ascii=False
            ),
            "reviewer": decision["reviewer"],
            "batch_id": decision["batch_id"],
            "rules_version": RULES_VERSION,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "human_required": int(not auto_apply),
            "applied": 0,
        }
        if not store.insert_candidate_review(review):
            skipped += 1
            continue
        if auto_apply and decision["decision"] == "accept":
            store.link_post_meme(
                candidate["post_id"],
                candidate["meme_slug"],
                matched_term=f"agent:{decision['reviewer']}",
                matched_by="agent",
                confidence=decision["confidence"],
            )
            review["applied"] = 1
            conn = store.connect()
            try:
                conn.execute(
                    "UPDATE candidate_reviews SET applied = 1 WHERE candidate_key = ?",
                    (decision["candidate_key"],),
                )
                conn.commit()
            finally:
                conn.close()
            accepted += 1
        elif auto_apply:
            rejected += 1
        else:
            human += 1

    return {
        "batch_id": batch_id,
        "reviewer": reviewer,
        "accepted": accepted,
        "rejected": rejected,
        "human_required": human,
        "skipped": skipped,
        "invalid": invalid,
    }


def export_human_review(output_dir: str | Path) -> dict:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rows = store.candidate_reviews_needing_human()
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        post = store.get_post(row["post_id"]) or {}
        grouped[row["meme_slug"]].append(
            {
                **row,
                "post_text": post.get("text_plain") or "",
                "post_created_at": post.get("created_at") or "",
            }
        )

    json_path = output / "human-review.json"
    json_path.write_text(
        json.dumps(grouped, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = ["# 候选审核人工待办", ""]
    for slug, items in grouped.items():
        meme = _meme_context(slug)
        lines.extend([f"## {meme['name']}（{slug}）", ""])
        for item in items:
            lines.extend(
                [
                    f"- `{item['candidate_key']}` · {item['post_id']} · "
                    f"{item['decision']} · 置信度 {item['confidence']:.2f}",
                    f"  - 原微博：{item['post_text'][:200]}",
                    f"  - 理由：{item['reason']}",
                    f"  - 证据：`{item['evidence_json']}`",
                ]
            )
        lines.append("")
    markdown_path = output / "human-review.md"
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "pending": len(rows),
        "json": str(json_path),
        "markdown": str(markdown_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="内容候选审核队列")
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="导出子 agent 批次")
    export.add_argument("--output", required=True)
    export.add_argument("--limit", type=int, default=150)
    export.add_argument("--batch-size", type=int, default=50)
    record = sub.add_parser("record", help="导入子 agent 决定")
    record.add_argument("--input", required=True)
    human = sub.add_parser("human", help="导出人工待办")
    human.add_argument("--output", required=True)
    sub.add_parser("stats", help="查看审核统计")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "export":
        print(
            json.dumps(
                export_batches(args.output, args.limit, args.batch_size),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "record":
        print(json.dumps(record_decisions(args.input), ensure_ascii=False, indent=2))
        return 0
    if args.command == "human":
        print(
            json.dumps(
                export_human_review(args.output),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(json.dumps(store.candidate_review_stats(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
