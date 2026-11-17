from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import patrol


ROOT = Path(__file__).resolve().parents[1]


def endpoint_result(
    name: str,
    *,
    status: str = "ok",
    p95_ms: float | None = 500,
    http_codes: list[int] | None = None,
) -> dict:
    return {
        "name": name,
        "display_name": name,
        "url": f"https://example.test/{name}",
        "status": status,
        "http_codes": http_codes or ([200] if status == "ok" else [502]),
        "p95_ms": p95_ms,
        "max_ms": p95_ms,
        "samples": [
            {
                "cold": True,
                "ok": status == "ok",
                "http_code": 200 if status == "ok" else 502,
                "elapsed_ms": p95_ms or 100,
                "content_type": "text/html",
                "error": "上游失败" if status != "ok" else "",
            }
        ],
    }


def report(
    *,
    finished_at: str = "2026-11-17T00:00:00+00:00",
    day: str = "2026-11-17",
    status: str = "ok",
    p95_ms: float = 500,
) -> dict:
    return {
        "version": 1,
        "run_id": "test-run",
        "started_at": finished_at,
        "finished_at": finished_at,
        "cst_date": day,
        "base_url": "https://example.test",
        "status": status,
        "endpoints": [
            endpoint_result(
                "healthz",
                status=status,
                p95_ms=p95_ms,
            )
        ],
    }


class PercentileAndDiscoveryTest(unittest.TestCase):
    def test_nearest_rank_percentile(self) -> None:
        self.assertEqual(patrol.nearest_rank_percentile([100, 200, 300], 95), 300)
        self.assertEqual(patrol.nearest_rank_percentile([300, 100, 200], 50), 200)
        self.assertIsNone(patrol.nearest_rank_percentile([], 95))

    def test_discovers_real_links_from_rendered_pages(self) -> None:
        page = (
            '<a href="/post/5231732026510004">post</a>'
            '<img src="/img?u=https%3A%2F%2Fwx1.sinaimg.cn%2Fdemo.jpg">'
        )
        self.assertEqual(
            patrol._discover_post_path(page),
            "/post/5231732026510004",
        )
        self.assertEqual(
            patrol._discover_image_path(page),
            "/img?u=https%3A%2F%2Fwx1.sinaimg.cn%2Fdemo.jpg",
        )


class StateTransitionTest(unittest.TestCase):
    def test_availability_requires_two_failures_and_three_recoveries(self) -> None:
        state = patrol.empty_state()
        failed = report(status="fail", p95_ms=None)
        self.assertEqual(patrol.apply_checkpoint(state, failed), [])
        events = patrol.apply_checkpoint(state, failed)
        self.assertEqual(
            [(event.key, event.action) for event in events],
            [("healthz:availability", "open")],
        )
        check = state["checks"]["healthz:availability"]
        check["issue"] = {"number": 101, "state": "open"}

        healthy = report()
        self.assertEqual(patrol.apply_checkpoint(state, healthy), [])
        self.assertEqual(patrol.apply_checkpoint(state, healthy), [])
        events = patrol.apply_checkpoint(state, healthy)
        self.assertEqual(
            [(event.key, event.action) for event in events],
            [("healthz:availability", "recover")],
        )

    def test_closed_fingerprint_reopens_after_two_new_failures(self) -> None:
        state = patrol.empty_state()
        check = state["checks"].setdefault(
            "healthz:availability",
            patrol._empty_check("healthz", "availability"),
        )
        check["issue"] = {"number": 101, "state": "closed"}
        patrol.apply_checkpoint(state, report(status="fail", p95_ms=None))
        events = patrol.apply_checkpoint(
            state, report(status="fail", p95_ms=None)
        )
        self.assertEqual(events[0].action, "reopen")

    def test_performance_requires_three_rounds(self) -> None:
        state = patrol.empty_state()
        slow = report(p95_ms=2100)
        self.assertEqual(patrol.apply_checkpoint(state, slow), [])
        self.assertEqual(patrol.apply_checkpoint(state, slow), [])
        events = patrol.apply_checkpoint(state, slow)
        self.assertEqual(
            [(event.key, event.action) for event in events],
            [("healthz:performance", "open")],
        )

    def test_daily_aggregate_counts_runs_failures_and_codes(self) -> None:
        state = patrol.empty_state()
        patrol.apply_checkpoint(state, report())
        patrol.apply_checkpoint(state, report(status="fail", p95_ms=None))
        daily = state["days"]["2026-11-17"]
        self.assertEqual(daily["runs"], 2)
        self.assertEqual(daily["abnormal_runs"], 1)
        health = daily["endpoints"]["healthz"]
        self.assertEqual(health["checks"], 2)
        self.assertEqual(health["failures"], 1)
        self.assertEqual(health["http_codes"], {"200": 1, "502": 1})

    def test_daily_aggregate_keeps_current_and_previous_two_days(self) -> None:
        state = patrol.empty_state()
        for day in ("2026-11-14", "2026-11-15", "2026-11-16", "2026-11-17"):
            state["days"][day] = {
                "runs": 1,
                "abnormal_runs": 0,
                "endpoints": {},
            }
        patrol.apply_checkpoint(
            state,
            report(day="2026-11-17"),
        )
        self.assertEqual(
            sorted(state["days"]),
            ["2026-11-15", "2026-11-16", "2026-11-17"],
        )


class IssueLifecycleTest(unittest.TestCase):
    def test_same_fingerprint_updates_existing_issue(self) -> None:
        state = patrol.empty_state()
        failed = report(status="fail", p95_ms=None)
        patrol.apply_checkpoint(state, failed)
        events = patrol.apply_checkpoint(state, failed)

        class FakeGh:
            def __init__(self) -> None:
                self.created = []
                self.updated = []
                self.incident = None

            def find_incident(self, fingerprint):
                return self.incident

            def create_incident(self, *, title, body, priority):
                self.created.append((title, body, priority))
                self.incident = {
                    "number": 102,
                    "state": "OPEN",
                    "body": body,
                    "url": "https://example.test/issues/102",
                }
                return self.incident

            def update_issue_body(self, number, body):
                self.updated.append((number, body))

            def issue(self, number):
                return self.incident

        gh = FakeGh()
        messages = patrol.process_events(state, failed, events, gh)
        self.assertIn("创建故障 Issue #102", messages[0])
        self.assertEqual(len(gh.created), 1)

        next_events = patrol.apply_checkpoint(state, failed)
        later = report(
            finished_at="2026-11-17T01:01:00+00:00",
            status="fail",
            p95_ms=None,
        )
        messages = patrol.process_events(state, later, next_events, gh)
        self.assertEqual(gh.created.__len__(), 1)
        self.assertEqual(gh.updated[-1][0], 102)
        self.assertIn("更新故障 Issue #102", messages[0])

    def test_recovery_closes_issue(self) -> None:
        state = patrol.empty_state()
        check = state["checks"].setdefault(
            "healthz:availability",
            patrol._empty_check("healthz", "availability"),
        )
        check["issue"] = {"number": 103, "state": "open"}
        healthy = report()
        for _ in range(3):
            events = patrol.apply_checkpoint(state, healthy)

        class FakeGh:
            def __init__(self) -> None:
                self.comment = None
                self.closed = None

            def find_incident(self, fingerprint):
                return {"number": 103, "state": "OPEN"}

            def close_issue(self, number, body):
                self.closed = (number, body)

        gh = FakeGh()
        messages = patrol.process_events(state, healthy, events, gh)
        self.assertEqual(gh.closed[0], 103)
        self.assertIn("关闭已恢复故障 Issue #103", messages[0])
        self.assertEqual(
            state["checks"]["healthz:availability"]["issue"]["state"],
            "closed",
        )

    def test_recovery_closes_state_issue_when_search_does_not_find_it(self) -> None:
        state = patrol.empty_state()
        check = state["checks"].setdefault(
            "healthz:availability",
            patrol._empty_check("healthz", "availability"),
        )
        check["issue"] = {"number": 104, "state": "open"}
        healthy = report()
        for _ in range(3):
            events = patrol.apply_checkpoint(state, healthy)

        class FakeGh:
            def __init__(self) -> None:
                self.closed = None

            def find_incident(self, fingerprint):
                return None

            def close_issue(self, number, body):
                self.closed = (number, body)

        gh = FakeGh()
        messages = patrol.process_events(state, healthy, events, gh)
        self.assertEqual(gh.closed[0], 104)
        self.assertIn("关闭已恢复故障 Issue #104", messages[0])
        self.assertEqual(check["issue"]["state"], "closed")

    def test_recovery_close_failure_does_not_mark_issue_closed(self) -> None:
        state = patrol.empty_state()
        check = state["checks"].setdefault(
            "healthz:availability",
            patrol._empty_check("healthz", "availability"),
        )
        check["issue"] = {"number": 105, "state": "open"}
        healthy = report()
        for _ in range(3):
            events = patrol.apply_checkpoint(state, healthy)

        class FakeGh:
            def find_incident(self, fingerprint):
                return {"number": 105, "state": "CLOSED"}

            def close_issue(self, number, body):
                raise RuntimeError("close failed")

        with self.assertRaises(RuntimeError):
            patrol.process_events(state, healthy, events, FakeGh())
        self.assertEqual(check["issue"]["state"], "open")


class StateCommentTest(unittest.TestCase):
    def test_state_round_trip(self) -> None:
        state = patrol.empty_state()
        state["days"]["2026-11-17"] = {
            "runs": 1,
            "abnormal_runs": 0,
            "endpoints": {},
        }
        body = patrol.state_comment_body(state)
        restored = patrol.load_state(body)
        self.assertEqual(restored["days"]["2026-11-17"]["runs"], 1)
        self.assertIn(patrol.STATE_MARKER, body)

    def test_invalid_state_is_ignored(self) -> None:
        self.assertEqual(patrol.load_state("not json"), patrol.empty_state())

    def test_three_days_of_full_endpoint_samples_stay_below_comment_limit(self) -> None:
        state = patrol.empty_state()
        endpoint_names = [name for name, _ in patrol.PAGE_ENDPOINTS]
        endpoint_names.extend(
            [
                "post_detail",
                "image_proxy",
                "image_proxy_reject",
            ]
        )
        for day in ("2026-11-15", "2026-11-16", "2026-11-17"):
            state["days"][day] = {
                "runs": 96,
                "abnormal_runs": 0,
                "endpoints": {
                    name: {
                        "display_name": name,
                        "checks": 96,
                        "failures": 0,
                        "perf_alerts": 0,
                        "p95_ms": [500.0] * 96,
                        "http_codes": {"200": 96},
                    }
                    for name in endpoint_names
                },
            }
        body = patrol.state_comment_body(state)
        self.assertLess(len(body), 58_000)

    def test_issue_comments_flattens_paginated_pages(self) -> None:
        client = patrol.GhClient(repository="owner/repo", token="test")
        pages = [
            [{"id": 1, "body": "first"}],
            [{"id": 2, "body": patrol.STATE_MARKER}],
        ]
        with mock.patch.object(client, "run_json", return_value=pages) as run_json:
            comments = client.issue_comments(42)
        self.assertEqual(comments, pages[0] + pages[1])
        run_json.assert_called_once_with(
            [
                "api",
                "repos/owner/repo/issues/42/comments",
                "--paginate",
                "--slurp",
            ]
        )

    def test_state_comment_can_be_found_after_many_historical_comments(self) -> None:
        comments = [
            {"id": index, "body": f"daily report {index}"}
            for index in range(1, 149)
        ]
        comments.append({"id": 149, "body": patrol.STATE_MARKER})

        class FakeGh:
            def issue_comments(self, number):
                return comments

        found = patrol._state_comment(
            FakeGh(),
            {"number": 1},
        )
        self.assertEqual(found["id"], 149)


class TriageTest(unittest.TestCase):
    def test_triage_preserves_existing_priority_and_sorts(self) -> None:
        now = datetime(2026, 11, 17, tzinfo=timezone.utc)
        issues = [
            {
                "number": 1,
                "title": "高清图优化",
                "body": "图片体验",
                "url": "https://example.test/1",
                "labels": [{"name": "priority:P3"}, {"name": "enhancement"}],
                "createdAt": "2026-11-10T00:00:00Z",
                "updatedAt": "2026-11-10T00:00:00Z",
            },
            {
                "number": 2,
                "title": "500 页面不可用",
                "body": "核心详情页故障",
                "url": "https://example.test/2",
                "labels": [{"name": "bug"}],
                "createdAt": "2026-11-16T00:00:00Z",
                "updatedAt": "2026-11-16T00:00:00Z",
            },
            {
                "number": 3,
                "title": patrol.TRACKING_TITLE,
                "body": "",
                "url": "https://example.test/3",
                "labels": [{"name": patrol.TRACKING_LABEL}],
                "createdAt": "2026-11-16T00:00:00Z",
                "updatedAt": "2026-11-16T00:00:00Z",
            },
        ]
        items = patrol.triage_issues(issues, now=now)
        self.assertEqual([item["number"] for item in items], [2, 1])
        self.assertEqual(items[0]["suggested_priority"], "priority:P1")
        self.assertEqual(items[1]["suggested_priority"], "priority:P3")

    def test_priority_definition_text_does_not_raise_every_issue_to_p0(self) -> None:
        issue = {
            "number": 95,
            "title": "建立每日生产巡检与待办梳理流程",
            "body": (
                "目标：建立巡检。\n\n"
                "## 优先级口径\n\n"
                "- P0：全站不可用、数据损坏或同等严重事故。\n\n"
                "## 验收\n\n- 生成日报。"
            ),
            "url": "https://example.test/95",
            "labels": [],
            "createdAt": "2026-11-17T00:00:00Z",
            "updatedAt": "2026-11-17T00:00:00Z",
        }
        self.assertEqual(patrol.infer_priority(issue), "priority:P2")

    def test_title_wins_over_comment_mentions_for_photo_issue(self) -> None:
        issue = {
            "number": 45,
            "title": "建立高清图分类与人工精选流程",
            "body": "评论图片目前为 0，需要用户提供样本。",
            "url": "https://example.test/45",
            "labels": [],
            "createdAt": "2026-11-12T00:00:00Z",
            "updatedAt": "2026-11-12T00:00:00Z",
        }
        self.assertEqual(patrol.infer_priority(issue), "priority:P3")
        self.assertEqual(patrol.infer_scope(issue), "图片数据与展示")

    def test_daily_report_marks_healthy_day(self) -> None:
        state = patrol.empty_state()
        state["days"]["2026-11-16"] = {
            "runs": 96,
            "abnormal_runs": 0,
            "endpoints": {},
        }
        markdown, healthy = patrol.daily_report_markdown(
            state=state,
            day="2026-11-16",
            triage_items=[],
        )
        self.assertTrue(healthy)
        self.assertIn("96", markdown)
        self.assertIn("只提供建议", markdown)

    def test_daily_report_handles_endpoint_without_latency(self) -> None:
        state = patrol.empty_state()
        state["days"]["2026-11-16"] = {
            "runs": 2,
            "abnormal_runs": 1,
            "endpoints": {
                "image_proxy": {
                    "display_name": "图片代理",
                    "checks": 2,
                    "failures": 1,
                    "perf_alerts": 0,
                    "p95_ms": [],
                    "http_codes": {},
                }
            },
        }
        markdown, healthy = patrol.daily_report_markdown(
            state=state,
            day="2026-11-16",
            triage_items=[],
        )
        self.assertFalse(healthy)
        self.assertIn("图片代理", markdown)

    def test_daily_report_upserts_one_comment_per_day(self) -> None:
        class FakeGh:
            def __init__(self) -> None:
                self.next_id = 1
                self.issue_data = {
                    "number": 1,
                    "state": "OPEN",
                    "body": patrol.TRACKING_MARKER,
                    "url": "https://example.test/issues/1",
                    "comments": [],
                }

            def ensure_labels(self):
                return None

            def tracking_issue(self):
                return {
                    "number": 1,
                    "title": patrol.TRACKING_TITLE,
                    "state": "OPEN",
                    "url": "https://example.test/issues/1",
                }

            def issue(self, number):
                return json.loads(json.dumps(self.issue_data))

            def issue_comments(self, number):
                return json.loads(json.dumps(self.issue_data["comments"]))

            def create_comment(self, number, body):
                comment_id = self.next_id
                self.next_id += 1
                self.issue_data["comments"].append(
                    {"id": comment_id, "body": body}
                )
                return comment_id

            def update_comment(self, comment_id, body):
                for comment in self.issue_data["comments"]:
                    if comment["id"] == comment_id:
                        comment["body"] = body
                        return
                self.fail(f"unknown comment {comment_id}")

            def fail(self, message):
                raise AssertionError(message)

        state = patrol.empty_state()
        state["days"]["2026-11-16"] = {
            "runs": 96,
            "abnormal_runs": 0,
            "endpoints": {},
        }
        gh = FakeGh()
        first = patrol.post_daily_report(
            gh=gh,
            day="2026-11-16",
            triage_items=[],
        )
        created_after_first = gh.next_id
        second = patrol.post_daily_report(
            gh=gh,
            day="2026-11-16",
            triage_items=[],
        )
        self.assertEqual(first["comment_id"], second["comment_id"])
        self.assertEqual(gh.next_id, created_after_first)
        matching = [
            comment
            for comment in gh.issue_data["comments"]
            if f"{patrol.DAILY_REPORT_MARKER}2026-11-16 -->"
            in comment["body"]
        ]
        self.assertEqual(len(matching), 1)


class WorkflowContractTest(unittest.TestCase):
    def test_schedules_permissions_and_timezone_comment(self) -> None:
        probe = (ROOT / ".github/workflows/patrol.yml").read_text(encoding="utf-8")
        daily = (ROOT / ".github/workflows/patrol-daily.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "*/15 * * * *"', probe)
        self.assertIn('cron: "0 1 * * *"', daily)
        self.assertIn("北京时间 09:00", daily)
        for workflow in (probe, daily):
            self.assertIn("issues: write", workflow)
            self.assertIn("production-patrol-state", workflow)
            self.assertNotIn("contents: write", workflow)


if __name__ == "__main__":
    unittest.main()
