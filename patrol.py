from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


BASE_URL = "https://wb-bgqq.fly.dev"
TRACKING_TITLE = "每日巡检与待办梳理"
TRACKING_LABEL = "daily-patrol-tracking"
INCIDENT_LABEL = "patrol-incident"
STATE_MARKER = "<!-- daily-patrol-state:v1 -->"
TRACKING_MARKER = "<!-- daily-patrol-tracking:v1 -->"
DAILY_REPORT_MARKER = "<!-- daily-patrol-report:"
FAILURE_THRESHOLD = 2
RECOVERY_THRESHOLD = 3
PERFORMANCE_THRESHOLD = 3
INCIDENT_UPDATE_INTERVAL = timedelta(hours=1)
P95_LIMIT_MS = 2000
CST = timezone(timedelta(hours=8))
STATE_VERSION = 2
STATE_RETENTION_DAYS = 3
LEGACY_TRANSPORT_RUN_TIMESTAMP = "2026-11-17T09:02:58.281478+00:00"
# 该微博是页面不暴露站内详情链接时使用的稳定真实样本。
# 微博 ID 本身不可变；巡检不依赖 data-latest 或本地数据库。
DEFAULT_POST_PATH = "/post/5231732026510004"

PRIORITY_LABELS = {
    "priority:P0": ("b60205", "全站不可用、数据损坏或同等严重事故"),
    "priority:P1": ("d93f0b", "核心检索不可用"),
    "priority:P2": ("fbca04", "局部功能或数据质量下降"),
    "priority:P3": ("0e8a16", "体验优化和远期需求"),
}
PATROL_LABELS = {
    TRACKING_LABEL: ("5319e7", "固定巡检日报载体"),
    INCIDENT_LABEL: ("b60205", "由生产巡检自动创建的故障单"),
}

PAGE_ENDPOINTS = (
    ("healthz", "/healthz"),
    ("home", "/"),
    ("memes_index", "/memes"),
    ("memes_all", "/memes/all"),
    ("fan_memes_index", "/fan-memes"),
    ("fan_memes_all", "/fan-memes/all"),
    ("interactions", "/interactions"),
    ("photos", "/photos"),
    ("search", "/search?q=%E5%9E%83%E5%9C%BE%E6%A1%B6"),
)


@dataclass(frozen=True)
class Endpoint:
    name: str
    path: str
    expected_statuses: tuple[int, ...] = (200,)
    samples: int = 3
    content_type_prefix: str = ""
    display_name: str = ""
    capture_body: bool = True

    @property
    def label(self) -> str:
        return self.display_name or self.name


@dataclass(frozen=True)
class Event:
    key: str
    endpoint: str
    kind: str
    action: str


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def cst_day(value: datetime) -> str:
    return value.astimezone(CST).date().isoformat()


def nearest_rank_percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return ordered[rank - 1]


def _sample(
    session: requests.Session,
    url: str,
    endpoint: Endpoint,
    *,
    timeout: float,
    capture_body: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    sample: dict[str, Any] = {
        "cold": False,
        "ok": False,
        "outcome": "transport_failure",
        "failure_layer": "transport",
        "transport_error_type": "",
    }
    try:
        response = session.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": "wbq-production-patrol/1.0",
                "Accept": "*/*",
            },
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip()
        sample.update(
            {
                "http_code": response.status_code,
                "elapsed_ms": elapsed_ms,
                "content_type": content_type,
            }
        )
        if response.status_code not in endpoint.expected_statuses:
            sample["error"] = f"期望状态码 {endpoint.expected_statuses}，实际 {response.status_code}"
            sample["outcome"] = "http_failure"
            sample["failure_layer"] = "http"
        elif endpoint.content_type_prefix and not content_type.startswith(
            endpoint.content_type_prefix
        ):
            sample["error"] = (
                f"期望 Content-Type {endpoint.content_type_prefix}*，实际 {content_type}"
            )
            sample["outcome"] = "http_failure"
            sample["failure_layer"] = "http"
        else:
            sample["ok"] = True
            sample["outcome"] = "ok"
            sample["failure_layer"] = ""
        if capture_body:
            sample["_body"] = response.text
        return sample
    except requests.exceptions.RequestException as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        sample.update(
            {
                "http_code": None,
                "elapsed_ms": elapsed_ms,
                "content_type": "",
                "error": f"{type(exc).__name__}: {exc}",
                "outcome": "transport_failure",
                "failure_layer": "transport",
                "transport_error_type": _transport_error_type(exc),
            }
        )
        return sample


def _transport_error_type(exc: requests.exceptions.RequestException) -> str:
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.SSLError):
        return "tls"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection"
    return "request"


def _probe_endpoint(
    session: requests.Session,
    base_url: str,
    endpoint: Endpoint,
    *,
    timeout: float,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{endpoint.path}"
    samples = []
    for index in range(endpoint.samples):
        sample = _sample(
            session,
            url,
            endpoint,
            timeout=timeout,
            capture_body=index == 0 and endpoint.capture_body,
        )
        sample["cold"] = index == 0
        samples.append(sample)
    elapsed = [
        float(sample["elapsed_ms"])
        for sample in samples
        if sample.get("elapsed_ms") is not None
    ]
    failed = [sample for sample in samples if not sample.get("ok")]
    received_http_response = any(
        sample.get("http_code") is not None for sample in samples
    )
    if not failed:
        status = "ok"
    elif received_http_response:
        status = "fail"
    else:
        status = "inconclusive"
    return {
        "name": endpoint.name,
        "display_name": endpoint.label,
        "url": url,
        "status": status,
        "received_http_response": received_http_response,
        "http_codes": sorted(
            {
                int(sample["http_code"])
                for sample in samples
                if sample.get("http_code") is not None
            }
        ),
        "p95_ms": nearest_rank_percentile(elapsed, 95),
        "max_ms": round(max(elapsed), 1) if elapsed else None,
        "samples": [
            {key: value for key, value in sample.items() if not key.startswith("_")}
            for sample in samples
        ],
        "_body": samples[0].get("_body", "") if samples else "",
    }


def _discover_post_path(page_body: str) -> str:
    match = re.search(r'href=["\'](/post/\d+)', html.unescape(page_body))
    return match.group(1) if match else ""


def _discover_image_path(page_body: str) -> str:
    match = re.search(r'["\'](/img\?u=[^"\']+)', html.unescape(page_body))
    if not match:
        return ""
    value = match.group(1)
    return value.replace("&amp;", "&")


def probe_site(
    base_url: str,
    *,
    timeout: float = 12,
    run_id: str = "",
    session: requests.Session | None = None,
) -> dict[str, Any]:
    started = now_utc()
    results = []
    for name, path in PAGE_ENDPOINTS:
        result = _probe_endpoint(
            session or requests.Session(),
            base_url,
            Endpoint(name, path, samples=3, display_name=name),
            timeout=timeout,
        )
        results.append(result)

    body_by_name = {result["name"]: result.get("_body", "") for result in results}
    post_path = _discover_post_path(
        "\n".join(
            body_by_name.get(name, "")
            for name in ("memes_all", "home", "photos")
        )
    ) or DEFAULT_POST_PATH
    if post_path:
        results.append(
            _probe_endpoint(
                session or requests.Session(),
                base_url,
                Endpoint(
                    "post_detail",
                    post_path,
                    samples=3,
                    display_name="真实微博详情",
                ),
                timeout=timeout,
            )
        )
    else:
        results.append(_discovery_failure("post_detail", "真实微博详情", "首页中未找到微博详情链接"))

    image_path = _discover_image_path(
        "\n".join(
            body_by_name.get(name, "")
            for name in ("photos", "home", "memes_all")
        )
    )
    if image_path:
        results.append(
            _probe_endpoint(
                session or requests.Session(),
                base_url,
                Endpoint(
                    "image_proxy",
                    image_path,
                    samples=1,
                    content_type_prefix="image/",
                    display_name="图片代理",
                    capture_body=False,
                ),
                timeout=timeout,
            )
        )
    else:
        results.append(_discovery_failure("image_proxy", "图片代理", "高清图页面中未找到图片代理链接"))

    reject_url = "/img?u=" + quote("https://example.com/not-sina.jpg", safe="")
    results.append(
        _probe_endpoint(
            session or requests.Session(),
            base_url,
            Endpoint(
                "image_proxy_reject",
                reject_url,
                expected_statuses=(400,),
                samples=1,
                display_name="图片代理域名拦截",
                capture_body=False,
            ),
            timeout=timeout,
        )
    )

    cleaned_results = []
    for result in results:
        clean = dict(result)
        clean.pop("_body", None)
        cleaned_results.append(clean)
    classification, diagnostics = _classify_run(cleaned_results)
    if classification == "inconclusive":
        for result in cleaned_results:
            result["status"] = "inconclusive"
    finished = now_utc()
    return {
        "version": 2,
        "run_id": run_id,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "cst_date": cst_day(finished),
        "base_url": base_url.rstrip("/"),
        "status": classification,
        "classification": classification,
        "diagnostics": diagnostics,
        "endpoints": cleaned_results,
    }


def _discovery_failure(name: str, display_name: str, error: str) -> dict[str, Any]:
    return {
        "name": name,
        "display_name": display_name,
        "url": "",
        "status": "fail",
        "received_http_response": False,
        "http_codes": [],
        "p95_ms": None,
        "max_ms": None,
        "samples": [
            {
                "cold": True,
                "ok": False,
                "http_code": None,
                "elapsed_ms": 0.0,
                "content_type": "",
                "error": error,
                "outcome": "discovery_failure",
                "failure_layer": "discovery",
                "transport_error_type": "",
            }
        ],
    }


def _classify_run(endpoints: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    network_endpoints = [item for item in endpoints if item.get("url")]
    http_response_endpoints = [
        item for item in network_endpoints if item.get("received_http_response")
    ]
    transport_failed_endpoints = [
        item for item in network_endpoints if item.get("status") == "inconclusive"
    ]
    error_types = sorted(
        {
            sample.get("transport_error_type", "")
            for item in transport_failed_endpoints
            for sample in item.get("samples", [])
            if sample.get("transport_error_type")
        }
    )
    if network_endpoints and not http_response_endpoints:
        classification = "inconclusive"
        note = "所有目标端点都在收到 HTTP 响应前失败，无法判断站点页面状态。"
    elif any(item.get("status") != "ok" for item in endpoints):
        classification = "fail"
        note = "至少一个端点收到 HTTP 响应或出现确定性页面失败。"
    else:
        classification = "ok"
        note = "所有端点检查正常。"
    diagnostics = {
        "network_endpoints": len(network_endpoints),
        "http_response_endpoints": len(http_response_endpoints),
        "transport_failed_endpoints": len(transport_failed_endpoints),
        "transport_error_types": error_types,
        "note": note,
    }
    return classification, diagnostics


def empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "updated_at": "",
        "checks": {},
        "days": {},
        "daily_report_comments": {},
    }


def load_state(comment_body: str | None) -> dict[str, Any]:
    if not comment_body or STATE_MARKER not in comment_body:
        return empty_state()
    match = re.search(r"```json\s*(\{.*\})\s*```", comment_body, re.DOTALL)
    if not match:
        return empty_state()
    try:
        state = json.loads(match.group(1))
    except json.JSONDecodeError:
        return empty_state()
    if state.get("version") == 1:
        state = _migrate_state_v1(state)
    elif state.get("version") != STATE_VERSION:
        return empty_state()
    state.setdefault("checks", {})
    state.setdefault("days", {})
    state.setdefault("daily_report_comments", {})
    return state


def _migrate_state_v1(state: dict[str, Any]) -> dict[str, Any]:
    """清除旧版本把统一传输失败误记为端点故障的状态。"""
    if state.get("updated_at") != LEGACY_TRANSPORT_RUN_TIMESTAMP:
        state["version"] = STATE_VERSION
        return state
    for check in state.get("checks", {}).values():
        if check.get("issue") is None:
            check["failure_streak"] = 0
            check["success_streak"] = 0
    for day in state.get("days", {}).values():
        if day.get("runs") != 1:
            continue
        day["abnormal_runs"] = 0
        day["inconclusive_runs"] = 1
        for endpoint in day.get("endpoints", {}).values():
            if endpoint.get("failures") == 1:
                endpoint["failures"] = 0
                endpoint["inconclusive_checks"] = 1
                endpoint["p95_ms"] = []
                endpoint["perf_alerts"] = 0
    state["version"] = STATE_VERSION
    return state


def state_comment_body(state: dict[str, Any]) -> str:
    encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded) > 58_000:
        raise RuntimeError("巡检状态过大，拒绝写入 Issue 评论")
    return f"{STATE_MARKER}\n```json\n{encoded}\n```"


def _empty_check(endpoint: str, kind: str) -> dict[str, Any]:
    return {
        "endpoint": endpoint,
        "kind": kind,
        "failure_streak": 0,
        "success_streak": 0,
        "issue": None,
        "last_evaluated_at": "",
        "last_issue_update_at": "",
    }


def _transition(
    check: dict[str, Any],
    failed: bool | None,
    *,
    failure_threshold: int,
    recovery_threshold: int,
) -> str:
    if failed is None:
        return "none"
    issue = check.get("issue") or {}
    issue_state = issue.get("state")
    if failed:
        check["failure_streak"] += 1
        check["success_streak"] = 0
        if issue_state == "open":
            return "update"
        if check["failure_streak"] >= failure_threshold:
            return "reopen" if issue_state == "closed" else "open"
        return "none"

    check["failure_streak"] = 0
    check["success_streak"] += 1
    if issue_state == "open" and check["success_streak"] >= recovery_threshold:
        return "recover"
    return "none"


def apply_checkpoint(
    state: dict[str, Any],
    report: dict[str, Any],
    *,
    p95_limit_ms: float = P95_LIMIT_MS,
) -> list[Event]:
    events: list[Event] = []
    evaluated_at = report["finished_at"]
    run_inconclusive = report.get("classification", report["status"]) == "inconclusive"
    endpoint_results = {
        result["name"]: result for result in report.get("endpoints", [])
    }
    for name, result in endpoint_results.items():
        availability_key = f"{name}:availability"
        availability = state["checks"].setdefault(
            availability_key, _empty_check(name, "availability")
        )
        availability["last_evaluated_at"] = evaluated_at
        action = _transition(
            availability,
            None if run_inconclusive else result["status"] != "ok",
            failure_threshold=FAILURE_THRESHOLD,
            recovery_threshold=RECOVERY_THRESHOLD,
        )
        if action != "none":
            events.append(Event(availability_key, name, "availability", action))

        performance_key = f"{name}:performance"
        performance = state["checks"].setdefault(
            performance_key, _empty_check(name, "performance")
        )
        performance["last_evaluated_at"] = evaluated_at
        p95_ms = result.get("p95_ms")
        if (
            run_inconclusive
            or result["status"] == "inconclusive"
            or result["status"] != "ok"
            or p95_ms is None
        ):
            perf_failed = None
        else:
            perf_failed = float(p95_ms) > p95_limit_ms
        action = _transition(
            performance,
            perf_failed,
            failure_threshold=PERFORMANCE_THRESHOLD,
            recovery_threshold=RECOVERY_THRESHOLD,
        )
        if action != "none":
            events.append(Event(performance_key, name, "performance", action))

    _aggregate_day(state, report)
    state["updated_at"] = evaluated_at
    return events


def _aggregate_day(state: dict[str, Any], report: dict[str, Any]) -> None:
    day = report["cst_date"]
    daily = state["days"].setdefault(
        day,
        {
            "runs": 0,
            "abnormal_runs": 0,
            "inconclusive_runs": 0,
            "endpoints": {},
        },
    )
    daily.setdefault("abnormal_runs", 0)
    daily.setdefault("inconclusive_runs", 0)
    daily["runs"] += 1
    classification = report.get("classification", report["status"])
    if classification == "fail":
        daily["abnormal_runs"] += 1
    elif classification == "inconclusive":
        daily["inconclusive_runs"] += 1
    for result in report.get("endpoints", []):
        endpoint = daily["endpoints"].setdefault(
            result["name"],
            {
                "display_name": result.get("display_name") or result["name"],
                "checks": 0,
                "failures": 0,
                "inconclusive_checks": 0,
                "perf_alerts": 0,
                "p95_ms": [],
                "http_codes": {},
            },
        )
        endpoint["checks"] += 1
        endpoint.setdefault("inconclusive_checks", 0)
        endpoint["display_name"] = result.get("display_name") or result["name"]
        if classification == "inconclusive":
            endpoint["inconclusive_checks"] += 1
        elif result["status"] != "ok":
            endpoint["failures"] += 1
        if classification != "inconclusive" and result.get("p95_ms") is not None:
            endpoint["p95_ms"].append(round(float(result["p95_ms"]), 1))
            if float(result["p95_ms"]) > P95_LIMIT_MS:
                endpoint["perf_alerts"] += 1
        for code in result.get("http_codes", []):
            key = str(code)
            endpoint["http_codes"][key] = endpoint["http_codes"].get(key, 0) + 1

    current_day = date.fromisoformat(day)
    cutoff = current_day - timedelta(days=STATE_RETENTION_DAYS - 1)
    state["days"] = {
        key: value
        for key, value in state["days"].items()
        if cutoff <= date.fromisoformat(key) <= current_day
    }


def _priority_for_check(endpoint: str, kind: str) -> str:
    if kind == "performance":
        return "priority:P2"
    if endpoint == "healthz":
        return "priority:P0"
    if endpoint in {"home", "post_detail", "search"}:
        return "priority:P1"
    return "priority:P2"


class GhClient:
    def __init__(self, repository: str = "", token: str = "") -> None:
        self.repository = repository or os.environ.get("GITHUB_REPOSITORY", "")
        self.token = token or os.environ.get("GH_TOKEN", "")

    def run(self, args: list[str], *, input_text: str | None = None) -> str:
        env = os.environ.copy()
        if self.token:
            env["GH_TOKEN"] = self.token
        completed = subprocess.run(
            ["gh", *args],
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            raise RuntimeError(f"gh {' '.join(args[:3])} 失败：{stderr}")
        return completed.stdout.strip()

    def run_json(self, args: list[str]) -> Any:
        return json.loads(self.run(args) or "null")

    def ensure_labels(self) -> None:
        for name, (color, description) in {
            **PRIORITY_LABELS,
            **PATROL_LABELS,
        }.items():
            self.run(
                [
                    "label",
                    "create",
                    name,
                    "--color",
                    color,
                    "--description",
                    description,
                    "--force",
                ]
            )

    def tracking_issue(self) -> dict[str, Any]:
        issues = self.run_json(
            [
                "issue",
                "list",
                "--state",
                "all",
                "--label",
                TRACKING_LABEL,
                "--limit",
                "100",
                "--json",
                "number,title,state,url",
            ]
        )
        for issue in issues:
            if issue["title"] == TRACKING_TITLE:
                if issue["state"] != "OPEN":
                    self.run(["issue", "reopen", str(issue["number"])])
                    issue["state"] = "OPEN"
                return issue
        body = (
            f"{TRACKING_MARKER}\n"
            "本 Issue 是生产巡检日报的固定载体，每个巡检周期使用状态评论保存机器可读数据，"
            "每日北京时间约 09:00 追加一条汇总。\n\n"
            "自动巡检不会修改既有 Issue 的业务优先级、路线图或负责人。"
        )
        url = self.run(
            [
                "issue",
                "create",
                "--title",
                TRACKING_TITLE,
                "--body",
                body,
                "--label",
                TRACKING_LABEL,
            ]
        )
        match = re.search(r"/issues/(\d+)$", url)
        if not match:
            raise RuntimeError(f"无法从 Issue URL 解析编号：{url}")
        return {
            "number": int(match.group(1)),
            "title": TRACKING_TITLE,
            "state": "OPEN",
            "url": url,
        }

    def issue(self, number: int) -> dict[str, Any]:
        return self.run_json(
            [
                "issue",
                "view",
                str(number),
                "--json",
                "number,title,state,body,url",
            ]
        )

    def issue_comments(self, number: int) -> list[dict[str, Any]]:
        pages = self.run_json(
            [
                "api",
                f"repos/{self.repository}/issues/{number}/comments",
                "--paginate",
                "--slurp",
            ]
        )
        if not isinstance(pages, list):
            return []
        if pages and all(isinstance(page, list) for page in pages):
            return [comment for page in pages for comment in page]
        return pages

    def create_comment(self, number: int, body: str) -> int:
        payload = json.dumps({"body": body})
        output = self.run(
            [
                "api",
                f"repos/{self.repository}/issues/{number}/comments",
                "--method",
                "POST",
                "--input",
                "-",
            ],
            input_text=payload,
        )
        return int(json.loads(output)["id"])

    def update_comment(self, comment_id: int, body: str) -> None:
        payload = json.dumps({"body": body})
        self.run(
            [
                "api",
                f"repos/{self.repository}/issues/comments/{comment_id}",
                "--method",
                "PATCH",
                "--input",
                "-",
            ],
            input_text=payload,
        )

    def find_incident(self, fingerprint: str) -> dict[str, Any] | None:
        issues = self.run_json(
            [
                "issue",
                "list",
                "--state",
                "all",
                "--label",
                INCIDENT_LABEL,
                "--limit",
                "100",
                "--json",
                "number,title,state,body,url",
            ]
        )
        marker = f"<!-- patrol-fingerprint:{fingerprint} -->"
        for issue in issues:
            if marker in issue.get("body", ""):
                return issue
        return None

    def create_incident(
        self,
        *,
        title: str,
        body: str,
        priority: str,
    ) -> dict[str, Any]:
        url = self.run(
            [
                "issue",
                "create",
                "--title",
                title,
                "--body",
                body,
                "--label",
                INCIDENT_LABEL,
                "--label",
                "bug",
                "--label",
                priority,
            ]
        )
        match = re.search(r"/issues/(\d+)$", url)
        if not match:
            raise RuntimeError(f"无法从故障 Issue URL 解析编号：{url}")
        return {"number": int(match.group(1)), "state": "OPEN", "url": url}

    def reopen_issue(self, number: int) -> None:
        self.run(["issue", "reopen", str(number)])

    def update_issue_body(self, number: int, body: str) -> None:
        self.run(["issue", "edit", str(number), "--body", body])

    def comment_issue(self, number: int, body: str) -> None:
        self.run(["issue", "comment", str(number), "--body", body])

    def close_issue(self, number: int, body: str) -> None:
        self.run(["issue", "close", str(number), "--comment", body])

    def open_issues(self) -> list[dict[str, Any]]:
        return self.run_json(
            [
                "issue",
                "list",
                "--state",
                "open",
                "--limit",
                "100",
                "--json",
                "number,title,state,body,url,labels,createdAt,updatedAt",
            ]
        )


def _report_endpoint(report: dict[str, Any], name: str) -> dict[str, Any]:
    return next(
        (item for item in report.get("endpoints", []) if item["name"] == name),
        _discovery_failure(name, name, "本次巡检无结果"),
    )


def incident_body(
    fingerprint: str,
    endpoint: str,
    kind: str,
    report: dict[str, Any],
    check: dict[str, Any],
) -> str:
    result = _report_endpoint(report, endpoint)
    failures = [
        sample for sample in result.get("samples", []) if not sample.get("ok")
    ]
    error_lines = [
        f"- `{sample.get('http_code') or '无响应'}` "
        f"{sample.get('elapsed_ms')} ms：{sample.get('error', '成功')}"
        for sample in (failures or result.get("samples", []))
    ]
    if kind == "performance":
        title = "连续性能超阈值"
        evidence = f"- 本次轮次 p95：`{result.get('p95_ms')}` ms；上限：`{P95_LIMIT_MS}` ms"
    else:
        title = "连续不可用"
        evidence = f"- 连续失败轮次：`{check['failure_streak']}`"
    return "\n".join(
        [
            f"<!-- patrol-fingerprint:{fingerprint} -->",
            f"## {result.get('display_name') or endpoint} {title}",
            "",
            "此 Issue 由生产巡检自动创建，同一故障指纹不会重复建单。",
            "",
            "<!-- patrol-live-start -->",
            f"- 端点：`{endpoint}`",
            f"- 最近 URL：{result.get('url') or '未发现可用 URL'}",
            f"- 最近检查：`{report.get('finished_at')}`",
            f"- 本次状态码：`{result.get('http_codes')}`",
            evidence,
            *error_lines,
            "<!-- patrol-live-end -->",
            "",
            "## 处理边界",
            "",
            f"- 连续 {RECOVERY_THRESHOLD} 次恢复正常后，巡检会自动追加恢复记录并关闭本 Issue。",
            "- 恢复依据是真实公开端点请求，不以部署成功代替。",
            "- 本 Issue 的最终修复方案仍由人工或后续梳理流程确认。",
        ]
    )


def _replace_live_section(body: str, new_body: str) -> str:
    pattern = re.compile(
        r"<!-- patrol-live-start -->.*?<!-- patrol-live-end -->",
        re.DOTALL,
    )
    replacement_match = re.search(
        r"<!-- patrol-live-start -->.*?<!-- patrol-live-end -->",
        new_body,
        re.DOTALL,
    )
    if not replacement_match:
        return new_body
    if pattern.search(body):
        return pattern.sub(replacement_match.group(0), body)
    return new_body


def process_events(
    state: dict[str, Any],
    report: dict[str, Any],
    events: list[Event],
    gh: GhClient,
) -> list[str]:
    messages = []
    for event in events:
        check = state["checks"][event.key]
        fingerprint = event.key
        current_issue = check.get("issue") or {}
        existing = gh.find_incident(fingerprint)
        issue_number = (
            existing["number"] if existing else current_issue.get("number")
        )
        priority = _priority_for_check(event.endpoint, event.kind)
        display_name = _report_endpoint(report, event.endpoint).get(
            "display_name", event.endpoint
        )
        body = incident_body(
            fingerprint, event.endpoint, event.kind, report, check
        )

        if event.action in {"open", "reopen", "update"}:
            if event.action == "update" and current_issue.get("last_issue_update_at"):
                last_update = parse_timestamp(
                    current_issue["last_issue_update_at"]
                )
                if parse_timestamp(report["finished_at"]) - last_update < INCIDENT_UPDATE_INTERVAL:
                    continue
            if not issue_number:
                suffix = "性能超阈值" if event.kind == "performance" else "连续不可用"
                created = gh.create_incident(
                    title=f"[巡检] {display_name} {suffix}",
                    body=body,
                    priority=priority,
                )
                issue_number = created["number"]
                messages.append(f"创建故障 Issue #{issue_number}：{fingerprint}")
            else:
                if event.action == "reopen" or (
                    existing and existing.get("state") == "CLOSED"
                ):
                    gh.reopen_issue(issue_number)
                    messages.append(f"重新打开故障 Issue #{issue_number}：{fingerprint}")
                existing_body = (
                    existing.get("body")
                    if existing and existing["number"] == issue_number
                    else gh.issue(issue_number).get("body", "")
                )
                gh.update_issue_body(
                    issue_number, _replace_live_section(existing_body or "", body)
                )
                messages.append(f"更新故障 Issue #{issue_number}：{fingerprint}")
            check["issue"] = {
                "number": issue_number,
                "state": "open",
                "last_issue_update_at": report["finished_at"],
            }
        elif event.action == "recover" and issue_number:
            gh.close_issue(
                issue_number,
                "巡检已连续 3 次恢复成功，按要求关闭本故障 Issue。"
                "同一指纹再次连续失败时会重新打开。"
                f"\n\n生产巡检于 `{report['finished_at']}` 确认恢复。",
            )
            messages.append(f"关闭已恢复故障 Issue #{issue_number}：{fingerprint}")
            check["issue"] = {
                "number": issue_number,
                "state": "closed",
                "last_issue_update_at": check["issue"].get("last_issue_update_at", ""),
            }
    return messages


def summarize_run(report: dict[str, Any]) -> str:
    classification = report.get("classification", report["status"])
    status_label = {
        "ok": "正常",
        "fail": "确定故障",
        "inconclusive": "无法判断",
    }.get(classification, classification)
    lines = [
        f"巡检状态：`{status_label}`（`{classification}`）",
        f"运行编号：`{report.get('run_id') or 'local'}`",
        f"时间：`{report['finished_at']}`",
    ]
    diagnostics = report.get("diagnostics") or {}
    if diagnostics:
        lines.extend(
            [
                f"诊断：{diagnostics.get('note', '')}",
                "错误类型："
                + (", ".join(diagnostics.get("transport_error_types", [])) or "无"),
            ]
        )
    lines.extend(
        [
            "",
            "| 端点 | 状态 | HTTP | p95 ms | 最大 ms |",
            "| --- | --- | --- | ---: | ---: |",
        ]
    )
    for endpoint in report.get("endpoints", []):
        lines.append(
            "| {name} | {status} | {codes} | {p95} | {max_ms} |".format(
                name=endpoint.get("display_name") or endpoint["name"],
                status=endpoint["status"],
                codes=",".join(str(code) for code in endpoint.get("http_codes", [])),
                p95=endpoint.get("p95_ms"),
                max_ms=endpoint.get("max_ms"),
            )
        )
    return "\n".join(lines)


def _comment_with_marker(
    comments: list[dict[str, Any]], marker: str
) -> dict[str, Any] | None:
    for comment in comments:
        if marker in comment.get("body", ""):
            return comment
    return None


def _state_comment(gh: GhClient, issue: dict[str, Any]) -> dict[str, Any] | None:
    comments = gh.issue_comments(int(issue["number"]))
    return _comment_with_marker(comments, STATE_MARKER)


def run_probe(
    *,
    base_url: str,
    gh: GhClient | None = None,
    run_id: str = "",
    output_path: Path | None = None,
    maintain_issues: bool = True,
) -> dict[str, Any]:
    report = probe_site(base_url, run_id=run_id)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if not maintain_issues:
        return {"report": report, "events": [], "messages": []}

    gh = gh or GhClient()
    gh.ensure_labels()
    tracking = gh.tracking_issue()
    state_comment = _state_comment(gh, tracking)
    state = load_state(state_comment.get("body") if state_comment else None)
    events = apply_checkpoint(state, report)
    messages = process_events(state, report, events, gh)
    body = state_comment_body(state)
    if state_comment:
        gh.update_comment(int(state_comment["id"]), body)
    else:
        gh.create_comment(int(tracking["number"]), body)
    return {
        "report": report,
        "events": [event.__dict__ for event in events],
        "messages": messages,
        "tracking_issue": tracking,
    }


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 1) if values else None


def build_daily_health(
    state: dict[str, Any], day: str
) -> tuple[str, bool]:
    daily = state.get("days", {}).get(day)
    if not daily:
        return f"## 每日巡检 {day}（北京时间）\n\n当天没有可用的拨测记录。", False
    inconclusive_runs = daily.get("inconclusive_runs", 0)
    healthy = daily["abnormal_runs"] == 0 and inconclusive_runs == 0
    if healthy:
        report_lines = [
            f"## 每日巡检 {day}（北京时间）\n\n"
            f"- 健康摘要：当天共 `{daily['runs']}` 轮拨测，未发现页面失败或性能超阈值。"
        ]
        open_incidents = _current_open_incidents(state)
        if open_incidents:
            report_lines.extend(["", "当前仍未关闭的巡检故障：", *open_incidents])
        return "\n".join(report_lines), True
    if daily["abnormal_runs"] == 0:
        return (
            f"## 每日巡检 {day}（北京时间）\n\n"
            f"- 判定：无法判断。当天共 `{daily['runs']}` 轮拨测，"
            f"其中 `{inconclusive_runs}` 轮在收到 HTTP 响应前失败。\n"
            "- 这些请求不能证明页面可用，也不能按单个页面故障处理。\n"
            "- 请检查生产入口、DNS、TLS 证书和巡检运行节点的网络。",
            False,
        )
    lines = [
        f"## 每日巡检 {day}（北京时间）",
        "",
        f"- 拨测轮次：`{daily['runs']}`",
        f"- 异常轮次：`{daily['abnormal_runs']}`",
        f"- 无法判断轮次：`{inconclusive_runs}`",
        "",
        "| 端点 | 检查轮次 | 失败轮次 | 无法判断 | 轮次 p95 中位数 | "
        "最大轮次 p95 | 性能告警 | HTTP 状态码 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for endpoint in daily["endpoints"].values():
        p95_values = endpoint.get("p95_ms") or [0]
        lines.append(
            "| {name} | {checks} | {failures} | {inconclusive} | {median} | "
            "{maximum} | {perf} | {codes} |".format(
                name=endpoint.get("display_name") or "未知",
                checks=endpoint["checks"],
                failures=endpoint["failures"],
                inconclusive=endpoint.get("inconclusive_checks", 0),
                median=_mean(endpoint.get("p95_ms", [])),
                maximum=max(p95_values),
                perf=endpoint.get("perf_alerts", 0),
                codes=", ".join(
                    f"{code}:{count}"
                    for code, count in sorted(
                        endpoint.get("http_codes", {}).items()
                    )
                )
                or "-",
            )
        )
    open_incidents = _current_open_incidents(state)
    if open_incidents:
        lines.extend(["", "当前仍未关闭的巡检故障：", *open_incidents])
    return "\n".join(lines), False


def _current_open_incidents(state: dict[str, Any]) -> list[str]:
    items = []
    for check in state.get("checks", {}).values():
        issue = check.get("issue") or {}
        if issue.get("state") != "open":
            continue
        status = "性能" if check.get("kind") == "performance" else "可用性"
        items.append(
            f"- #{issue.get('number')} `{check.get('endpoint')}` {status}故障"
        )
    return sorted(set(items))


def _labels(issue: dict[str, Any]) -> set[str]:
    return {
        label.get("name", "")
        for label in issue.get("labels", [])
        if isinstance(label, dict)
    }


def _issue_text_without_priority_section(issue: dict[str, Any]) -> str:
    body = issue.get("body", "")
    body = re.sub(
        r"^## 优先级口径\b.*?(?=^## |\Z)",
        "",
        body,
        flags=re.MULTILINE | re.DOTALL,
    )
    return f"{issue.get('title', '')}\n{body}".lower()


def infer_priority(issue: dict[str, Any]) -> str:
    existing = sorted(
        label for label in _labels(issue) if label.startswith("priority:P")
    )
    if existing:
        return existing[0]
    title = issue.get("title", "").lower()
    text = _issue_text_without_priority_section(issue)
    if "low priority" in text or "低优先级" in text:
        return "priority:P3"
    if any(term in title for term in ("巡检", "生产监控")):
        return "priority:P2"
    if "高清图" in title:
        return "priority:P3"
    if any(term in title for term in ("数据损坏", "泄露", "安全漏洞")):
        return "priority:P0"
    if any(
        term in title
        for term in (
            "每日数据",
            "每日流程",
            "存量",
            "搜索",
            "详情",
            "热门",
            "分类",
            "500",
            "502",
            "不可用",
        )
    ):
        return "priority:P1"
    if any(
        term in title
        for term in (
            "评论",
            "图片",
            "视频",
            "局部",
            "数据质量",
            "bug",
            "缺陷",
        )
    ):
        return "priority:P2"
    return "priority:P3"


def infer_scope(issue: dict[str, Any]) -> str:
    title = issue.get("title", "").lower()
    text = f"{title}\n{issue.get('body', '')}".lower()
    if INCIDENT_LABEL in _labels(issue):
        return "生产站点可用性或性能"
    if any(term in title for term in ("巡检", "拨测", "生产监控")):
        return "生产巡检与故障跟踪"
    if any(term in title for term in ("每日数据", "每日流程", "数据运营")):
        return "每日数据运营"
    if any(term in title for term in ("高清图", "壁纸", "明信片")):
        return "图片数据与展示"
    if any(term in title for term in ("评论", "comment")):
        return "评论抓取与语义审核"
    if any(term in title for term in ("视频", "播放")):
        return "视频播放"
    if any(term in title for term in ("搜索", "详情", "热门", "分类")):
        return "核心浏览与检索"
    if any(term in text for term in ("评论", "comment")):
        return "评论抓取与语义审核"
    if any(term in text for term in ("高清图", "图片", "壁纸", "明信片")):
        return "图片数据与展示"
    if any(term in text for term in ("视频", "播放")):
        return "视频播放"
    if any(term in text for term in ("每日数据", "每日流程", "存量", "抓取")):
        return "每日数据运营"
    if any(term in text for term in ("搜索", "详情", "热门", "分类")):
        return "核心浏览与检索"
    return "待确认"


def solution_hint(issue: dict[str, Any]) -> str:
    scope = infer_scope(issue)
    return {
        "生产巡检与故障跟踪": "先完成只读拨测、故障去重和日报闭环，再接模型建议；不要自动修改既有优先级。",
        "生产站点可用性或性能": "核对故障 Issue 的连续失败证据，先恢复核心路径，再处理冷启动或上游依赖。",
        "评论抓取与语义审核": "把评论候选拆成独立适配层，保留原微博、评论和审核证据，再接入现有候选队列。",
        "图片数据与展示": "先完成存量和样本校准，避免在覆盖不足时用模型分数直接决定精选。",
        "视频播放": "先建立冷启动和热缓存基线，再按延迟与成功率评估预连接、缓存或直连。",
        "每日数据运营": "按路线图分阶段实现请求预算、检查点、审核包和不可变快照，避免一个脚本承担整条链路。",
        "核心浏览与检索": "优先确认失败页面、数据快照和部署绑定，再定位查询、模板或数据问题。",
    }.get(scope, "补充复现步骤、影响页面和当前数据快照后再排期。")


def dependency_hint(issue: dict[str, Any]) -> str:
    title = issue.get("title", "").lower()
    text = f"{title}\n{issue.get('body', '')}".lower()
    if "高清图" in title:
        return "依赖人工样本和存量抓取完成"
    if "存量" in text:
        return "依赖存量抓取进度"
    if any(term in text for term in ("样本", "人工审核", "用户提供")):
        return "依赖人工样本或确认"
    if INCIDENT_LABEL in _labels(issue):
        return "依赖生产端恢复与连续 3 次验收"
    return "未识别到硬阻塞"


def triage_issues(
    issues: list[dict[str, Any]], *, now: datetime | None = None
) -> list[dict[str, Any]]:
    now = now or now_utc()
    priority_order = {
        "priority:P0": 0,
        "priority:P1": 1,
        "priority:P2": 2,
        "priority:P3": 3,
    }
    output = []
    for issue in issues:
        if TRACKING_LABEL in _labels(issue):
            continue
        created = parse_timestamp(issue["createdAt"])
        priority = infer_priority(issue)
        output.append(
            {
                "number": issue["number"],
                "title": issue["title"],
                "url": issue.get("url", ""),
                "age_days": max(0, (now - created).days),
                "suggested_priority": priority,
                "affected_scope": infer_scope(issue),
                "solution_hint": solution_hint(issue),
                "dependency_hint": dependency_hint(issue),
                "existing_labels": sorted(_labels(issue)),
            }
        )
    output.sort(
        key=lambda item: (
            priority_order[item["suggested_priority"]],
            -item["age_days"],
            item["number"],
        )
    )
    return output


def triage_markdown(items: list[dict[str, Any]]) -> str:
    lines = [
        "### 未关闭 Issue 梳理建议",
        "",
        "> 本节只提供建议，不修改既有 Issue 的优先级、路线图或负责人。",
        "",
    ]
    if not items:
        lines.append("当前没有需要梳理的未关闭 Issue。")
        return "\n".join(lines)
    lines.extend(
        [
            "| Issue | 建议优先级 | 影响范围 | 依赖或阻塞 | 建议方案 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for item in items:
        lines.append(
            "| [#{number} {title}]({url}) | `{priority}` | {scope} | "
            "{dependency} | {solution} |".format(
                number=item["number"],
                title=item["title"].replace("|", "\\|"),
                url=item["url"],
                priority=item["suggested_priority"],
                scope=item["affected_scope"],
                dependency=item["dependency_hint"],
                solution=item["solution_hint"],
            )
        )
    return "\n".join(lines)


def daily_report_markdown(
    *,
    state: dict[str, Any],
    day: str,
    triage_items: list[dict[str, Any]],
) -> tuple[str, bool]:
    health, healthy = build_daily_health(state, day)
    return f"{health}\n\n{triage_markdown(triage_items)}", healthy


def _select_state_comment(
    gh: GhClient, tracking: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    issue = gh.issue(int(tracking["number"]))
    comments = gh.issue_comments(int(tracking["number"]))
    issue["comments"] = comments
    return issue, _comment_with_marker(comments, STATE_MARKER)


def post_daily_report(
    *,
    gh: GhClient,
    day: str,
    triage_items: list[dict[str, Any]],
) -> dict[str, Any]:
    gh.ensure_labels()
    tracking = gh.tracking_issue()
    issue, state_comment = _select_state_comment(gh, tracking)
    state = load_state(state_comment.get("body") if state_comment else None)
    report, healthy = daily_report_markdown(
        state=state, day=day, triage_items=triage_items
    )
    marker = f"{DAILY_REPORT_MARKER}{day} -->"
    report = f"{marker}\n{report}"
    existing_comment = _comment_with_marker(issue.get("comments", []), marker)
    existing_id = (
        existing_comment.get("id")
        if existing_comment
        else state.get("daily_report_comments", {}).get(day)
    )
    if existing_id:
        gh.update_comment(int(existing_id), report)
        comment_id = int(existing_id)
    else:
        comment_id = gh.create_comment(int(tracking["number"]), report)
    state.setdefault("daily_report_comments", {})[day] = comment_id
    if state_comment:
        gh.update_comment(int(state_comment["id"]), state_comment_body(state))
    else:
        gh.create_comment(int(tracking["number"]), state_comment_body(state))
    return {
        "tracking_issue": tracking,
        "tracking_issue_url": tracking.get("url", issue.get("url", "")),
        "comment_id": comment_id,
        "healthy": healthy,
        "markdown": report,
    }


def _write_step_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    Path(path).write_text(markdown + "\n", encoding="utf-8")


def command_run(args: argparse.Namespace) -> int:
    result = run_probe(
        base_url=args.base_url,
        run_id=args.run_id or os.environ.get("GITHUB_RUN_ID", ""),
        output_path=Path(args.output) if args.output else None,
        maintain_issues=not args.no_github,
    )
    report = result["report"]
    lines = [summarize_run(report)]
    if result["messages"]:
        lines.extend(["", "### 故障单操作", *[f"- {item}" for item in result["messages"]]])
    elif report.get("classification") == "inconclusive":
        lines.extend(
            [
                "",
                "本轮在收到 HTTP 响应前失败，只记录探测层异常，不更新端点故障计数。",
            ]
        )
    elif not args.no_github:
        lines.extend(["", "本次没有需要创建、重开或关闭的故障 Issue。"])
    _write_step_summary("\n".join(lines))
    print("\n".join(lines))
    return 0


def command_daily(args: argparse.Namespace) -> int:
    gh = GhClient()
    items = triage_issues(gh.open_issues())
    day = args.day or (now_utc().astimezone(CST).date() - timedelta(days=1)).isoformat()
    result = post_daily_report(gh=gh, day=day, triage_items=items)
    markdown = result["markdown"]
    _write_step_summary(markdown)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown + "\n", encoding="utf-8")
    print(markdown)
    return 0


def command_labels(_: argparse.Namespace) -> int:
    GhClient().ensure_labels()
    print("巡检与优先级标签已就绪。")
    return 0


def command_triage(args: argparse.Namespace) -> int:
    gh = GhClient()
    items = triage_issues(gh.open_issues())
    payload = {
        "generated_at": now_utc().isoformat(),
        "mode": "recommendation-only",
        "items": items,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生产巡检与待办梳理")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="执行一次拨测并维护故障 Issue")
    run_parser.add_argument("--base-url", default=BASE_URL)
    run_parser.add_argument("--run-id", default="")
    run_parser.add_argument("--output", default="")
    run_parser.add_argument(
        "--no-github",
        action="store_true",
        help="只拨测并输出 JSON，不读写 GitHub Issue",
    )
    run_parser.set_defaults(func=command_run)

    daily_parser = subparsers.add_parser("daily", help="追加每日巡检与待办梳理报告")
    daily_parser.add_argument("--day", default="")
    daily_parser.add_argument("--output", default="")
    daily_parser.set_defaults(func=command_daily)

    labels_parser = subparsers.add_parser("labels", help="创建或更新巡检标签")
    labels_parser.set_defaults(func=command_labels)

    triage_parser = subparsers.add_parser(
        "triage", help="输出未关闭 Issue 的建议排序 JSON"
    )
    triage_parser.add_argument("--output", default="")
    triage_parser.set_defaults(func=command_triage)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
