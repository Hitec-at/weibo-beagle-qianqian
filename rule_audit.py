"""只读的规则变更审计。

本模块只检查规则包结构、历史样本和删除残留，不执行语义裁决，也不修改
seed、数据库或分类结果。语义盲审由独立子智能体按审计文档执行。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from config import DB_PATH

SCHEMA_VERSION = "rule-change-audit-v1"
ACTIVE_STATUSES = {"active", ""}
INACTIVE_STATUSES = {"paused", "deprecated"}


def _unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _rule_status(rule: dict) -> str:
    return str(rule.get("status") or "active").strip().lower()


def _rule_terms(rule: dict) -> list[str]:
    mode = str(rule.get("match_mode") or "rule")
    if mode == "curated":
        return []
    if mode == "rule":
        return _unique(
            [rule.get("name") or "", *(rule.get("aliases") or [])]
        )
    return _unique(
        [
            *(rule.get("auto_publish_terms") or []),
            *(rule.get("candidate_terms") or []),
        ]
    )


def _public_terms(rule: dict) -> list[str]:
    mode = str(rule.get("match_mode") or "rule")
    if mode == "rule":
        return _unique(
            [rule.get("name") or "", *(rule.get("aliases") or [])]
        )
    if mode == "mixed":
        return _unique(rule.get("auto_publish_terms") or [])
    return []


def _load_seed(path: str | Path) -> list[dict]:
    import memes

    return memes.load_seed(path)


def _open_readonly(path: str | Path) -> sqlite3.Connection | None:
    db_path = Path(path)
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(
            f"file:{quote(str(db_path.resolve()))}?mode=ro",
            uri=True,
        )
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _escaped_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _matched_post_ids(
    conn: sqlite3.Connection | None,
    rule: dict,
) -> list[str]:
    if conn is None:
        return []
    terms = _rule_terms(rule)
    excludes = _unique(rule.get("exclude") or [])
    ids: set[str] = set()
    for term in terms:
        clauses = ["lower(text_plain) LIKE lower(?) ESCAPE '\\'"]
        params: list[str] = [f"%{_escaped_like(term)}%"]
        for excluded in excludes:
            clauses.append("lower(text_plain) NOT LIKE lower(?) ESCAPE '\\'")
            params.append(f"%{_escaped_like(excluded)}%")
        rows = conn.execute(
            "SELECT id FROM posts WHERE " + " AND ".join(clauses),
            params,
        ).fetchall()
        ids.update(str(row["id"]) for row in rows)
    for post_id in _unique(rule.get("curated_post_ids") or []):
        row = conn.execute(
            "SELECT id FROM posts WHERE id = ?",
            (post_id,),
        ).fetchone()
        if row:
            ids.add(str(row["id"]))
    return sorted(ids)


def _db_rule_info(
    conn: sqlite3.Connection | None,
    slug: str,
) -> dict:
    if conn is None:
        return {"exists": False, "status": "", "links": 0}
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(memes)").fetchall()
    }
    meme = conn.execute(
        "SELECT * FROM memes WHERE slug = ?",
        (slug,),
    ).fetchone()
    links = conn.execute(
        "SELECT COUNT(*) AS count FROM post_memes WHERE meme_slug = ?",
        (slug,),
    ).fetchone()
    return {
        "exists": meme is not None,
        "status": (
            str(meme["status"] or "") if meme is not None and "status" in columns else ""
        ),
        "links": int(links["count"] if links else 0),
    }


def _finding(code: str, severity: str, message: str, **details) -> dict:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "details": details,
    }


def _completeness_findings(rule: dict, action: str) -> list[dict]:
    findings: list[dict] = []
    mode = str(rule.get("match_mode") or "rule")
    search_queries = _unique(rule.get("search_queries") or [])
    excludes = _unique(rule.get("exclude") or [])
    curated = _unique(rule.get("curated_post_ids") or [])
    auto_terms = _unique(rule.get("auto_publish_terms") or [])
    candidate_terms = _unique(rule.get("candidate_terms") or [])

    for field, label in (
        ("origin", "来源说明"),
        ("explanation", "解释"),
        ("boundary", "判断边界"),
    ):
        if not str(rule.get(field) or "").strip():
            findings.append(
                _finding(
                    f"missing_{field}",
                    "warn",
                    f"规则缺少{label}",
                )
            )

    if not search_queries:
        findings.append(
            _finding(
                "missing_search_queries",
                "warn",
                "规则没有搜索词，无法进行站内搜索复核",
            )
        )
    elif len(search_queries) == 1:
        findings.append(
            _finding(
                "single_search_query",
                "warn",
                "规则只有一个搜索词，存量未完整时不据此自动公开",
                query=search_queries[0],
            )
        )

    if mode == "mixed" and not auto_terms:
        findings.append(
            _finding(
                "missing_auto_publish_terms",
                "warn",
                "混合规则没有高精度公开词，只能作为候选规则",
            )
        )
    if mode == "curated" and not curated:
        findings.append(
            _finding(
                "missing_curated_posts",
                "warn",
                "人工审定规则没有引用任何帖子",
            )
        )
    if mode in {"rule", "mixed"} and not _rule_terms(rule):
        findings.append(
            _finding(
                "missing_match_terms",
                "warn",
                "规则没有任何可用于匹配正文的词",
            )
        )
    if not curated and not str(rule.get("representative_url") or "").strip():
        findings.append(
            _finding(
                "missing_positive_examples",
                "warn",
                "规则没有代表微博或人工审定帖子作为正例",
            )
        )
    if not excludes and action != "removed":
        findings.append(
            _finding(
                "missing_negative_examples",
                "warn",
                "规则没有排除词等反例约束；这不单独否决规则，但需人工确认",
            )
        )

    for field, values in (
        ("aliases", rule.get("aliases") or []),
        ("exclude", rule.get("exclude") or []),
        ("search_queries", rule.get("search_queries") or []),
        ("auto_publish_terms", rule.get("auto_publish_terms") or []),
        ("candidate_terms", rule.get("candidate_terms") or []),
    ):
        normalized = [str(value or "").strip().casefold() for value in values]
        if len(normalized) != len(set(normalized)):
            findings.append(
                _finding(
                    "duplicate_rule_value",
                    "warn",
                    f"{field} 中存在重复值",
                    field=field,
                )
            )
    return findings


def _overall_status(findings: list[dict]) -> str:
    severities = {item["severity"] for item in findings}
    if "block" in severities:
        return "block"
    if "warn" in severities:
        return "warn"
    return "approve"


def build_report(
    seed_path: str | Path,
    previous_seed_path: str | Path | None = None,
    db_path: str | Path | None = None,
) -> dict:
    current_items = _load_seed(seed_path)
    previous_items = (
        _load_seed(previous_seed_path) if previous_seed_path is not None else []
    )
    current: dict[str, dict] = {}
    previous: dict[str, dict] = {}
    duplicate_slugs: list[str] = []

    for item in previous_items:
        slug = str(item.get("slug") or item.get("name") or "").strip()
        if slug:
            previous[slug] = item

    for item in current_items:
        slug = str(item.get("slug") or item.get("name") or "").strip()
        if slug in current:
            duplicate_slugs.append(slug)
        if slug:
            current[slug] = item

    conn = _open_readonly(db_path or DB_PATH)
    report_rules: list[dict] = []
    all_findings: list[dict] = []
    name_owner: dict[str, str] = {}
    alias_owner: dict[str, str] = defaultdict(str)

    for slug in duplicate_slugs:
        finding = _finding(
            "duplicate_slug",
            "block",
            "seed 中存在重复 slug，规则身份不唯一",
            slug=slug,
        )
        all_findings.append(finding)
        report_rules.append(
            {
                "slug": slug,
                "action": "invalid",
                "status": "block",
                "sample_size": 0,
                "sample_post_ids": [],
                "findings": [finding],
            }
        )

    for slug in sorted(current):
        rule = current[slug]
        action = (
            "removed"
            if slug not in current and slug in previous
            else "modified"
            if slug in previous
            else "added"
            if previous
            else "current"
        )
        if _rule_status(rule) not in ACTIVE_STATUSES:
            report_rules.append(
                {
                    "slug": slug,
                    "action": action,
                    "status": "approve",
                    "sample_size": 0,
                    "sample_post_ids": [],
                    "findings": [
                        _finding(
                            "inactive_rule",
                            "info",
                            "规则未启用，跳过公开匹配检查",
                            rule_status=_rule_status(rule),
                        )
                    ],
                }
            )
            continue

        findings = _completeness_findings(rule, action)
        normalized_name = str(rule.get("name") or "").strip().casefold()
        if normalized_name:
            owner = name_owner.get(normalized_name)
            if owner and owner != slug:
                findings.append(
                    _finding(
                        "duplicate_rule_name",
                        "block",
                        "不同规则使用了相同名称",
                        other_slug=owner,
                    )
                )
            else:
                name_owner[normalized_name] = slug
        for alias in _unique(rule.get("aliases") or []):
            key = alias.casefold()
            owner = alias_owner[key]
            if owner and owner != slug:
                findings.append(
                    _finding(
                        "overlapping_alias",
                        "warn",
                        "两个启用规则共享同一别名，需要确认边界",
                        alias=alias,
                        other_slug=owner,
                    )
                )
            else:
                alias_owner[key] = slug

        sample_ids = _matched_post_ids(conn, rule)
        if not sample_ids:
            findings.append(
                _finding(
                    "insufficient_historical_sample",
                    "warn",
                    "当前数据库没有该规则的历史样本，不能据此评估效果",
                )
            )

        if previous and action == "modified" and conn is not None:
            old_ids = _matched_post_ids(conn, previous[slug])
            lost = sorted(set(old_ids) - set(sample_ids))
            if lost and _public_terms(previous[slug]):
                findings.append(
                    _finding(
                        "possible_historical_regression",
                        "warn",
                        "修改后不再命中部分原有公开样本，需要盲审确认是否属于边界收紧",
                        lost_count=len(lost),
                        sample_lost_post_ids=lost[:10],
                    )
                )

        report_rules.append(
            {
                "slug": slug,
                "action": action,
                "status": _overall_status(findings),
                "sample_size": len(sample_ids),
                "sample_post_ids": sample_ids[:10],
                "findings": findings,
            }
        )
        all_findings.extend(findings)

    for slug in sorted(set(previous) - set(current)):
        db_info = _db_rule_info(conn, slug)
        findings = []
        if db_info["links"]:
            severity = (
                "warn"
                if db_info["status"] in INACTIVE_STATUSES
                else "block"
            )
            findings.append(
                _finding(
                    "removed_rule_has_links",
                    severity,
                    "seed 已移除规则，但数据库仍保留历史关联",
                    link_count=db_info["links"],
                    db_status=db_info["status"],
                )
            )
        elif db_info["exists"]:
            findings.append(
                _finding(
                    "removed_rule_still_in_database",
                    "warn",
                    "seed 已移除规则，数据库仍有规则记录但没有公开关联",
                    db_status=db_info["status"],
                )
            )
        report_rules.append(
            {
                "slug": slug,
                "action": "removed",
                "status": _overall_status(findings),
                "sample_size": db_info["links"],
                "sample_post_ids": [],
                "findings": findings,
            }
        )
        all_findings.extend(findings)

    if conn is not None:
        conn.close()
    else:
        all_findings.append(
            _finding(
                "database_unavailable",
                "warn",
                "数据库不存在或不可只读打开，无法检查历史样本和删除残留",
                database=str(db_path or DB_PATH),
            )
        )

    counts = {
        severity: sum(1 for item in all_findings if item["severity"] == severity)
        for severity in ("block", "warn", "info")
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": _overall_status(all_findings),
        "seed": str(seed_path),
        "previous_seed": (
            str(previous_seed_path) if previous_seed_path is not None else ""
        ),
        "database": str(db_path or DB_PATH),
        "summary": {
            "rules": len(report_rules),
            "block": counts["block"],
            "warn": counts["warn"],
            "info": counts["info"],
            "historical_data_complete": False,
        },
        "rules": sorted(report_rules, key=lambda item: item["slug"]),
        "limitations": [
            "历史微博尚未完整抓取，样本数量和回归结果不代表最终覆盖率。",
            "本工具只做确定性检查，不替代独立语义盲审。",
            "单个或极少样本不足以支持自动调阈值或宣称规则已完成。",
        ],
    }


def _markdown(report: dict) -> str:
    lines = [
        "# 规则变更审计",
        "",
        f"- 结论：`{report['status']}`",
        f"- 规则数：{report['summary']['rules']}",
        f"- 阻断项：{report['summary']['block']}",
        f"- 警告项：{report['summary']['warn']}",
        f"- 数据库历史完整：否",
        "",
        "## 规则结果",
        "",
    ]
    for item in report["rules"]:
        lines.extend(
            [
                f"### {item['slug']} · {item['action']} · {item['status']}",
                "",
                f"- 历史样本：{item['sample_size']}",
            ]
        )
        if item["sample_post_ids"]:
            lines.append(
                f"- 样本帖子：{', '.join(item['sample_post_ids'])}"
            )
        if not item["findings"]:
            lines.append("- 未发现确定性问题")
        for finding in item["findings"]:
            lines.append(
                f"- `{finding['severity']}` `{finding['code']}`："
                f"{finding['message']}"
            )
        lines.append("")
    lines.extend(["## 限制", ""])
    lines.extend(f"- {text}" for text in report["limitations"])
    lines.append("")
    return "\n".join(lines)


def write_report(
    seed_path: str | Path,
    output_dir: str | Path,
    previous_seed_path: str | Path | None = None,
    db_path: str | Path | None = None,
) -> dict:
    report = build_report(seed_path, previous_seed_path, db_path)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "rule-audit.json"
    markdown_path = output / "rule-audit.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    return {
        "status": report["status"],
        "summary": report["summary"],
        "json": str(json_path),
        "markdown": str(markdown_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="只读检查规则包结构、历史样本和删除残留"
    )
    parser.add_argument("--seed", default="memes.seed.toml")
    parser.add_argument("--previous-seed", default=None)
    parser.add_argument("--database", default=str(DB_PATH))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = write_report(
        seed_path=args.seed,
        previous_seed_path=args.previous_seed,
        db_path=args.database,
        output_dir=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] != "block" else 1


if __name__ == "__main__":
    raise SystemExit(main())
