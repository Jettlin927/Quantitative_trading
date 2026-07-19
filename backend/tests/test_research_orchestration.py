from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import os
import unittest

from alembic import command
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.database import Base, alembic_config
from backend.app.github_research import (
    GitHubIssueClient,
    GitHubPermissionError,
    GitHubUnavailableError,
    poll_research_issues_once,
)
from backend.app.models import (
    DataSyncJob,
    FormalResearch,
    FrozenResearchPlan,
    ResearchEvent,
    ResearchOrchestration,
    ResearchRun,
    ResearchWorkItem,
)
from backend.app.research_orchestration import (
    CommentSnapshot,
    IssueSnapshot,
    ResearchStateTransitionError,
    apply_issue_plan,
    append_research_event,
    invalidate_issue_plan,
    transition_orchestration,
)
from backend.app.research_plan import (
    PLAN_END_MARKER,
    PLAN_START_MARKER,
    ResearchPlanBudgetError,
    ResearchPlanError,
    ResearchServerLimits,
    prepare_research_plan,
)
from backend.app import research_worker
from backend.app import sync_worker


REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
APP_COMMIT = "c" * 40


def valid_plan_payload() -> dict:
    config = json.loads(
        (REPO_ROOT / "configs/research/etf_trend_baseline.json").read_text(encoding="utf-8")
    )
    config["qualityRunId"] = "quality-ready"
    config["validationPolicy"] = {
        "mode": "anchored",
        "trainPeriods": 60,
        "testPeriods": 20,
        "stepPeriods": 20,
    }
    return {
        "schemaVersion": "research-plan/v1",
        "strategy": {
            "id": config["strategyId"],
            "version": config["strategyVersion"],
            "displayName": "ETF 趋势基线",
            "codeCommit": APP_COMMIT,
        },
        "economicHypothesis": "中期趋势在承担显式成本后可能保持可复现的风险调整收益。",
        "runConfig": config,
        "dataPolicy": {"pointInTime": True, "freezeSnapshot": True},
        "sampleSplits": [
            {"role": "train", "startDate": "2025-12-01", "endDate": "2026-02-27"},
            {"role": "validation", "startDate": "2026-03-02", "endDate": "2026-04-30"},
            {"role": "test_oos", "startDate": "2026-05-04", "endDate": "2026-06-29"},
        ],
        "parameterSpace": {"singleRun": ["frozen"]},
        "trialBudget": {"maxTrials": 1},
        "gates": ["净成本门禁", "OOS 门禁", "复现身份门禁"],
        "stopRules": ["身份变化立即停止", "质量门禁失败立即停止"],
        "resourceBudget": {
            "wallClockSeconds": 3600,
            "cpuCores": "0.75",
            "memoryMiB": 768,
            "artifactMiB": 512,
            "maxRetries": 2,
        },
        "reportContract": {
            "language": "zh-CN",
            "requiredArtifacts": ["report.html", "metrics.json", "manifest.json"],
            "conclusionValues": ["研究通过", "有条件候选", "证据不足", "受阻", "不通过"],
        },
    }


def issue_body(payload: dict) -> str:
    return (
        "## 中文摘要\n\n固定研究计划。\n\n"
        f"{PLAN_START_MARKER}\n```json\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + f"\n```\n{PLAN_END_MARKER}"
    )


def prepare(payload: dict | None = None):
    return prepare_research_plan(issue_body(payload or valid_plan_payload()))


def approved_issue_graph(
    session_factory: sessionmaker,
    *,
    issue_number: int = 901,
    prepared=None,
):
    prepared = prepared or prepare()
    issue = IssueSnapshot(number=issue_number, state="OPEN", body="", labels=("类型:策略研究",))
    comments = [
        CommentSnapshot(
            id=issue_number * 100,
            author_login="Jettlin927",
            body=prepared.approval_comment,
        )
    ]
    with session_factory.begin() as db:
        result = apply_issue_plan(
            db,
            issue,
            comments,
            prepared,
            app_git_commit=APP_COMMIT,
            app_git_ref="refs/heads/main",
            authorization_write_confirmed=True,
            now=NOW,
        )
    return prepared, issue, comments, result


class ResearchPlanContractTest(unittest.TestCase):
    def test_canonical_hash_is_stable_for_key_and_set_order(self) -> None:
        payload = valid_plan_payload()
        first = prepare(payload)
        reordered = {key: payload[key] for key in reversed(list(payload))}
        reordered["gates"] = list(reversed(reordered["gates"]))
        reordered["reportContract"] = dict(reversed(list(reordered["reportContract"].items())))
        second = prepare(reordered)

        self.assertEqual(first.plan_sha256, second.plan_sha256)
        self.assertEqual(first.normalized, second.normalized)
        changed = deepcopy(payload)
        changed["economicHypothesis"] += " 新证据会推翻该假设。"
        self.assertNotEqual(first.plan_sha256, prepare(changed).plan_sha256)

    def test_float_and_over_budget_plan_are_rejected(self) -> None:
        floating = valid_plan_payload()
        floating["resourceBudget"]["cpuCores"] = 0.75
        with self.assertRaisesRegex(ResearchPlanError, "禁止 JSON 浮点数"):
            prepare(floating)

        over_budget = valid_plan_payload()
        over_budget["resourceBudget"]["wallClockSeconds"] = 7201
        with self.assertRaisesRegex(ResearchPlanBudgetError, "超过服务器上限"):
            prepare_research_plan(
                issue_body(over_budget),
                limits=ResearchServerLimits(max_trials=1),
            )

    def test_exact_three_way_split_and_next_day_execution_are_required(self) -> None:
        payload = valid_plan_payload()
        payload["sampleSplits"] = payload["sampleSplits"][:2]
        with self.assertRaisesRegex(ResearchPlanError, "train、validation、test_oos"):
            prepare(payload)
        payload = valid_plan_payload()
        payload["runConfig"]["executionPolicy"]["executionPrice"] = "close"
        with self.assertRaisesRegex(ResearchPlanError, "下一交易日"):
            prepare(payload)

        payload = valid_plan_payload()
        payload["parameterSpace"] = {}
        with self.assertRaisesRegex(ResearchPlanError, "单一冻结批次"):
            prepare(payload)

        payload = valid_plan_payload()
        payload["sampleSplits"][0]["startDate"] = "20251201"
        payload["runConfig"]["startDate"] = "20251201"
        with self.assertRaisesRegex(ResearchPlanError, "YYYY-MM-DD"):
            prepare(payload)

    def test_duplicate_json_keys_are_rejected_instead_of_silently_overwritten(self) -> None:
        body = (
            f"{PLAN_START_MARKER}\n"
            '{"schemaVersion":"research-plan/v1","schemaVersion":"other"}'
            f"\n{PLAN_END_MARKER}"
        )
        with self.assertRaisesRegex(ResearchPlanError, "不允许重复键：schemaVersion"):
            prepare_research_plan(body)


class ResearchGitHubClientTest(unittest.TestCase):
    def test_issue_listing_paginates_without_returning_pull_requests(self) -> None:
        class Client(GitHubIssueClient):
            def __init__(self) -> None:
                super().__init__("owner/repo", "test-token")
                self.paths = []

            def _request(self, method, path, payload=None):
                self.paths.append((method, path, payload))
                if path.endswith("page=1"):
                    return [{"number": number} for number in range(1, 100)] + [
                        {"number": 100, "pull_request": {}}
                    ]
                return [{"number": 101}]

        client = Client()
        issues = client.list_research_issues()

        self.assertEqual([item["number"] for item in issues], [*range(1, 100), 101])
        self.assertEqual(len(client.paths), 2)
        self.assertIn("page=2", client.paths[1][1])


class ResearchOrchestrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_exact_approval_is_idempotent_and_creates_one_queue(self) -> None:
        prepared = prepare()
        issue = IssueSnapshot(number=902, state="OPEN", body="")
        wrong = [CommentSnapshot(id=1, author_login="Jettlin927", body=prepared.approval_comment + " ")]
        with self.Session.begin() as db:
            pending = apply_issue_plan(
                db,
                issue,
                wrong,
                prepared,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=True,
                now=NOW,
            )
        self.assertEqual(pending.state, "pending_approval")

        exact = [CommentSnapshot(id=2, author_login="Jettlin927", body=prepared.approval_comment)]
        for _ in range(2):
            with self.Session.begin() as db:
                result = apply_issue_plan(
                    db,
                    issue,
                    exact,
                    prepared,
                    app_git_commit=APP_COMMIT,
                    app_git_ref="refs/heads/main",
                    authorization_write_confirmed=True,
                    now=NOW,
                )
        self.assertEqual(result.state, "queued")
        with self.Session() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(FormalResearch)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(ResearchWorkItem)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(ResearchEvent)), 2)

    def test_write_permission_must_be_confirmed_before_queue(self) -> None:
        prepared = prepare()
        issue = IssueSnapshot(number=903, state="OPEN", body="")
        comments = [CommentSnapshot(id=3, author_login="Jettlin927", body=prepared.approval_comment)]
        with self.Session.begin() as db:
            result = apply_issue_plan(
                db,
                issue,
                comments,
                prepared,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=False,
                now=NOW,
            )
        self.assertFalse(result.queue_created)
        with self.Session() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(FormalResearch)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(ResearchWorkItem)), 0)

    def test_deleted_or_edited_approval_comment_invalidates_existing_queue(self) -> None:
        prepared, issue, _comments, _ = approved_issue_graph(self.Session, issue_number=9031)
        for _ in range(2):
            with self.Session.begin() as db:
                result = apply_issue_plan(
                    db,
                    issue,
                    [],
                    prepared,
                    app_git_commit=APP_COMMIT,
                    app_git_ref="refs/heads/main",
                    authorization_write_confirmed=False,
                    now=NOW + timedelta(minutes=1),
                )
        self.assertEqual(result.state, "blocked")
        self.assertIn("原批准评论", result.reason)
        with self.Session() as db:
            self.assertEqual(db.scalar(select(ResearchWorkItem.status)), "interrupted")
            invalidations = db.scalar(
                select(func.count()).select_from(ResearchEvent).where(
                    ResearchEvent.event_type == "approval_comment_invalidated"
                )
            )
        self.assertEqual(invalidations, 1)

    def test_invalid_edit_before_formal_creation_cannot_revive_old_approval(self) -> None:
        prepared = prepare()
        issue = IssueSnapshot(number=9032, state="OPEN", body=issue_body(valid_plan_payload()))
        comments = [
            CommentSnapshot(id=903200, author_login="Jettlin927", body=prepared.approval_comment)
        ]
        with self.Session.begin() as db:
            blocked = apply_issue_plan(
                db,
                issue,
                comments,
                prepared,
                app_git_commit="d" * 40,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=True,
                now=NOW,
            )
        self.assertEqual(blocked.state, "blocked")
        with self.Session.begin() as db:
            invalidate_issue_plan(
                db,
                issue.number,
                "当前机器计划 JSON 无效",
                "当前 Issue 机器计划无效",
                now=NOW + timedelta(minutes=1),
            )
        with self.Session.begin() as db:
            restored = apply_issue_plan(
                db,
                issue,
                comments,
                prepared,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=True,
                now=NOW + timedelta(minutes=2),
            )
        self.assertEqual(restored.state, "blocked")
        self.assertIn("永久失效", restored.reason)
        with self.Session() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(FormalResearch)), 0)

    def test_wrong_author_closed_issue_and_code_mismatch_never_start(self) -> None:
        prepared = prepare()
        wrong_author = [
            CommentSnapshot(id=31, author_login="someone-else", body=prepared.approval_comment)
        ]
        with self.Session.begin() as db:
            result = apply_issue_plan(
                db,
                IssueSnapshot(number=931, state="OPEN", body=""),
                wrong_author,
                prepared,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=True,
                now=NOW,
            )
        self.assertEqual(result.state, "pending_approval")

        closed_payload = valid_plan_payload()
        closed_payload["economicHypothesis"] += " 关闭票测试。"
        closed_prepared = prepare(closed_payload)
        mismatch_payload = valid_plan_payload()
        mismatch_payload["economicHypothesis"] += " 代码身份测试。"
        mismatch_prepared = prepare(mismatch_payload)
        feature_payload = valid_plan_payload()
        feature_payload["economicHypothesis"] += " 未合并分支测试。"
        feature_prepared = prepare(feature_payload)
        with self.Session.begin() as db:
            closed = apply_issue_plan(
                db,
                IssueSnapshot(number=932, state="CLOSED", body=""),
                [
                    CommentSnapshot(
                        id=32,
                        author_login="Jettlin927",
                        body=closed_prepared.approval_comment,
                    )
                ],
                closed_prepared,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=True,
                now=NOW,
            )
            mismatch = apply_issue_plan(
                db,
                IssueSnapshot(number=933, state="OPEN", body=""),
                [
                    CommentSnapshot(
                        id=33,
                        author_login="Jettlin927",
                        body=mismatch_prepared.approval_comment,
                    )
                ],
                mismatch_prepared,
                app_git_commit="d" * 40,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=True,
                now=NOW,
            )
            feature_ref = apply_issue_plan(
                db,
                IssueSnapshot(number=935, state="OPEN", body=""),
                [
                    CommentSnapshot(
                        id=35,
                        author_login="Jettlin927",
                        body=feature_prepared.approval_comment,
                    )
                ],
                feature_prepared,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/codex/feature",
                authorization_write_confirmed=True,
                now=NOW,
            )
        self.assertEqual(closed.state, "blocked")
        self.assertEqual(mismatch.state, "blocked")
        self.assertEqual(feature_ref.state, "blocked")
        self.assertIn("refs/heads/main", feature_ref.reason)
        with self.Session() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(FormalResearch)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(ResearchWorkItem)), 0)

    def test_closing_an_already_queued_issue_cancels_the_queue_idempotently(self) -> None:
        prepared, _issue, comments, _ = approved_issue_graph(self.Session, issue_number=934)
        closed = IssueSnapshot(number=934, state="CLOSED", body="")
        for _ in range(2):
            with self.Session.begin() as db:
                result = apply_issue_plan(
                    db,
                    closed,
                    comments,
                    prepared,
                    app_git_commit=APP_COMMIT,
                    app_git_ref="refs/heads/main",
                    authorization_write_confirmed=True,
                    now=NOW + timedelta(minutes=1),
                )
        self.assertEqual(result.state, "blocked")
        self.assertIsNone(
            research_worker.claim_next_research_work(
                "worker-closed",
                github_available=True,
                session_factory=self.Session,
                now=NOW + timedelta(minutes=2),
            )
        )
        with self.Session() as db:
            self.assertEqual(db.scalar(select(ResearchWorkItem.status)), "interrupted")
            event_count = db.scalar(select(func.count()).select_from(ResearchEvent))
        self.assertEqual(event_count, 3)

    def test_edit_invalidates_old_approval_and_old_hash_cannot_start_new_plan(self) -> None:
        old, issue, comments, _ = approved_issue_graph(self.Session, issue_number=904)
        changed_payload = valid_plan_payload()
        changed_payload["economicHypothesis"] += " 该版本缩小了假设。"
        changed = prepare(changed_payload)
        with self.Session.begin() as db:
            current = apply_issue_plan(
                db,
                issue,
                comments,
                changed,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=True,
                now=NOW + timedelta(minutes=1),
            )
        self.assertEqual(current.state, "pending_approval")
        with self.Session() as db:
            plans = db.scalars(
                select(FrozenResearchPlan).where(FrozenResearchPlan.issue_number == issue.number)
                .order_by(FrozenResearchPlan.version)
            ).all()
            old_state = db.scalar(
                select(ResearchOrchestration.state).where(
                    ResearchOrchestration.plan_id == plans[0].id
                )
            )
            old_work = db.scalar(select(ResearchWorkItem))
            old_formal = db.scalar(select(FormalResearch))
            self.assertEqual([plan.plan_sha256 for plan in plans], [old.plan_sha256, changed.plan_sha256])
            self.assertEqual(old_state, "stopped")
            self.assertEqual(old_work.status, "interrupted")
            self.assertEqual(old_formal.phase, "stopped")

    def test_edit_after_run_success_stops_before_evaluation_without_waiting_for_worker(self) -> None:
        prepared, issue, comments, _ = approved_issue_graph(self.Session, issue_number=909)
        with self.Session.begin() as db:
            work = db.scalar(select(ResearchWorkItem))
            work.status = "succeeded"
            orchestration = db.scalar(select(ResearchOrchestration))
            transition_orchestration(orchestration, "running", reason="等待评价")
            formal = db.scalar(select(FormalResearch))
            formal.phase = "evaluating"

        changed_payload = valid_plan_payload()
        changed_payload["economicHypothesis"] += " 运行完成后编辑计划。"
        with self.Session.begin() as db:
            apply_issue_plan(
                db,
                issue,
                comments,
                prepare(changed_payload),
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=True,
                now=NOW + timedelta(minutes=1),
            )

        with self.Session() as db:
            old_orchestration = db.scalar(
                select(ResearchOrchestration)
                .join(FrozenResearchPlan, FrozenResearchPlan.id == ResearchOrchestration.plan_id)
                .where(FrozenResearchPlan.plan_sha256 == prepared.plan_sha256)
            )
            formal = db.get(FormalResearch, old_orchestration.formal_research_id)
            self.assertEqual(old_orchestration.state, "stopped")
            self.assertEqual(formal.phase, "stopped")
            self.assertIsNotNone(formal.completed_at)

    def test_stop_comment_preserves_records_and_stops_before_start(self) -> None:
        prepared, issue, comments, _ = approved_issue_graph(self.Session, issue_number=905)
        comments.append(
            CommentSnapshot(id=90501, author_login="Jettlin927", body=prepared.stop_comment)
        )
        with self.Session.begin() as db:
            result = apply_issue_plan(
                db,
                issue,
                comments,
                prepared,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=True,
                now=NOW + timedelta(minutes=1),
            )
        self.assertEqual(result.state, "stopped")
        with self.Session() as db:
            self.assertEqual(db.scalar(select(ResearchWorkItem.status)), "interrupted")
            self.assertGreaterEqual(db.scalar(select(func.count()).select_from(ResearchEvent)), 3)

        with self.Session.begin() as db:
            closed = apply_issue_plan(
                db,
                IssueSnapshot(number=905, state="CLOSED", body=""),
                comments,
                prepared,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=True,
                now=NOW + timedelta(minutes=2),
            )
        self.assertEqual(closed.state, "stopped")

    def test_latest_resume_comment_requeues_same_interrupted_run(self) -> None:
        prepared, issue, comments, _ = approved_issue_graph(self.Session, issue_number=908)
        run_id = "20000000-0000-0000-0000-000000000008"
        with self.Session.begin() as db:
            formal = db.scalar(select(FormalResearch))
            db.add(
                ResearchRun(
                    run_id=run_id,
                    formal_research_id=formal.id,
                    strategy_id=prepared.normalized["strategy"]["id"],
                    status="interrupted",
                    stage="simulation",
                    config=prepared.normalized["runConfig"],
                    config_sha256="d" * 64,
                    code_commit=APP_COMMIT,
                    environment_sha256="e" * 64,
                    random_seed=7,
                    metrics={},
                    artifact_root="outputs/research-runs/resume-test.tmp",
                )
            )
            work = db.scalar(select(ResearchWorkItem))
            work.status = "interrupted"
            work.current_run_id = run_id
            work.resume_run_id = run_id
            work.stop_requested_at = NOW + timedelta(minutes=1)
            orchestration = db.scalar(select(ResearchOrchestration))
            transition_orchestration(orchestration, "stopped", reason="测试停止")
            formal.phase = "stopped"
            formal.completed_at = NOW + timedelta(minutes=1)

        comments.extend(
            [
                CommentSnapshot(id=90801, author_login="Jettlin927", body=prepared.stop_comment),
                CommentSnapshot(
                    id=90802,
                    author_login="Jettlin927",
                    body=f"恢复研究 {prepared.plan_sha256} {run_id}",
                ),
            ]
        )
        with self.Session.begin() as db:
            resumed = apply_issue_plan(
                db,
                issue,
                comments,
                prepared,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=True,
                now=NOW + timedelta(minutes=2),
            )

        self.assertEqual(resumed.state, "queued")
        with self.Session() as db:
            work = db.scalar(select(ResearchWorkItem))
            formal = db.scalar(select(FormalResearch))
            self.assertEqual(work.status, "queued")
            self.assertEqual(work.resume_run_id, run_id)
            self.assertIsNone(work.stop_requested_at)
            self.assertEqual(formal.phase, "approved")
            self.assertIsNone(formal.completed_at)

        with self.Session.begin() as db:
            work = db.scalar(select(ResearchWorkItem))
            orchestration = db.scalar(select(ResearchOrchestration))
            formal = db.scalar(select(FormalResearch))
            work.status = "interrupted"
            work.stop_requested_at = NOW + timedelta(minutes=3)
            transition_orchestration(orchestration, "stopped", reason="计划编辑后停止")
            orchestration.approval_invalidated = True
            formal.phase = "stopped"
            formal.completed_at = NOW + timedelta(minutes=3)
            append_research_event(
                db,
                formal.id,
                "invalid_plan_stop_requested",
                {"reason": "机器计划编辑后失效"},
                run_id=run_id,
                occurred_at=NOW + timedelta(minutes=3),
            )
        comments.append(
            CommentSnapshot(
                id=90803,
                author_login="Jettlin927",
                body=f"恢复研究 {prepared.plan_sha256} {run_id}",
            )
        )
        with self.Session.begin() as db:
            invalidated = apply_issue_plan(
                db,
                issue,
                comments,
                prepared,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=True,
                now=NOW + timedelta(minutes=4),
            )
        self.assertEqual(invalidated.state, "stopped")
        with self.Session() as db:
            self.assertEqual(db.scalar(select(ResearchWorkItem.status)), "interrupted")

    def test_state_machine_rejects_shortcuts(self) -> None:
        orchestration = ResearchOrchestration(
            id="10000000-0000-0000-0000-000000000001",
            plan_id="10000000-0000-0000-0000-000000000002",
            issue_number=1,
            state="pending_approval",
            last_issue_body_sha256="a" * 64,
        )
        with self.assertRaisesRegex(ResearchStateTransitionError, "非法"):
            transition_orchestration(orchestration, "published")

    def test_github_permission_denial_or_outage_never_queues_new_research(self) -> None:
        prepared = prepare()
        raw_issue = {
            "number": 906,
            "state": "open",
            "body": issue_body(valid_plan_payload()),
            "labels": [{"name": "类型:策略研究"}],
        }
        raw_comment = {
            "id": 90600,
            "user": {"login": "Jettlin927"},
            "body": prepared.approval_comment,
        }

        class PermissionDeniedClient:
            def list_research_issues(self):
                return [raw_issue]

            def list_comments(self, _number):
                return [raw_comment]

            def confirm_comment(self, *_args, **_kwargs):
                raise GitHubPermissionError("HTTP 403")

        denied = poll_research_issues_once(
            PermissionDeniedClient(),
            self.Session,
            app_git_commit=APP_COMMIT,
            app_git_ref="refs/heads/main",
            limits=ResearchServerLimits(),
        )
        self.assertFalse(denied.github_available)
        with self.Session() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(ResearchWorkItem)), 0)

        class UnavailableClient:
            def list_research_issues(self):
                raise GitHubUnavailableError("断网")

        unavailable = poll_research_issues_once(
            UnavailableClient(),
            self.Session,
            app_git_commit=APP_COMMIT,
            app_git_ref="refs/heads/main",
            limits=ResearchServerLimits(),
        )
        self.assertFalse(unavailable.github_available)
        self.assertIsNone(
            research_worker.claim_next_research_work(
                "worker-offline",
                github_available=False,
                session_factory=self.Session,
                now=NOW,
            )
        )

    def test_duplicate_plan_on_another_issue_blocks_only_that_issue(self) -> None:
        prepared = prepare()

        class DuplicatePlanClient:
            def __init__(self):
                self.labels = []

            def list_research_issues(self):
                return [
                    {
                        "number": number,
                        "state": "open",
                        "body": issue_body(valid_plan_payload()),
                        "labels": [{"name": "类型:策略研究"}],
                    }
                    for number in (9061, 9062)
                ]

            def list_comments(self, number):
                return [
                    {
                        "id": number * 100,
                        "user": {"login": "Jettlin927"},
                        "body": prepared.approval_comment,
                    }
                ]

            def confirm_comment(self, *_args, **_kwargs):
                return {"id": 1}

            def set_state_label(self, number, _current, desired):
                self.labels.append((number, desired))

        client = DuplicatePlanClient()
        poll = poll_research_issues_once(
            client,
            self.Session,
            app_git_commit=APP_COMMIT,
            app_git_ref="refs/heads/main",
            limits=ResearchServerLimits(),
        )
        self.assertTrue(poll.github_available)
        self.assertEqual(len(poll.processed), 1)
        self.assertEqual(len(poll.errors), 1)
        self.assertIn("已绑定其他 Issue", poll.errors[0])
        self.assertIn((9062, "研究:受阻"), client.labels)

    def test_malformed_edit_invalidates_an_existing_queue_before_comment_write(self) -> None:
        prepared, _issue, _comments, _ = approved_issue_graph(self.Session, issue_number=907)

        class InvalidEditClient:
            def __init__(self):
                self.labels = []

            def list_research_issues(self):
                return [
                    {
                        "number": 907,
                        "state": "open",
                        "body": "机器计划正在编辑，当前 JSON 不完整",
                        "labels": [{"name": "类型:策略研究"}, {"name": "研究:已批准"}],
                    }
                ]

            def list_comments(self, _number):
                return [
                    {
                        "id": 90700,
                        "user": {"login": "Jettlin927"},
                        "body": prepared.approval_comment,
                    }
                ]

            def confirm_comment(self, *_args, **_kwargs):
                return {"id": 1}

            def set_state_label(self, _number, _current, desired):
                self.labels.append(desired)

        client = InvalidEditClient()
        poll = poll_research_issues_once(
            client,
            self.Session,
            app_git_commit=APP_COMMIT,
            app_git_ref="refs/heads/main",
            limits=ResearchServerLimits(),
        )
        self.assertTrue(poll.github_available)
        self.assertTrue(poll.errors)
        self.assertEqual(client.labels, ["研究:受阻"])
        with self.Session() as db:
            self.assertEqual(db.scalar(select(ResearchWorkItem.status)), "interrupted")
            self.assertEqual(db.scalar(select(ResearchOrchestration.state)), "blocked")
        self.assertIsNone(
            research_worker.claim_next_research_work(
                "worker-invalid-edit",
                github_available=True,
                session_factory=self.Session,
                now=NOW,
            )
        )


class ResearchWorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_single_concurrency_duplicate_claim_and_retry_budget(self) -> None:
        approved_issue_graph(self.Session, issue_number=910)
        claim = research_worker.claim_next_research_work(
            "worker-a",
            github_available=True,
            session_factory=self.Session,
            now=NOW,
            lease_seconds=30,
        )
        self.assertIsNotNone(claim)
        duplicate = research_worker.claim_next_research_work(
            "worker-b",
            github_available=True,
            session_factory=self.Session,
            now=NOW,
            lease_seconds=30,
        )
        self.assertIsNone(duplicate)
        self.assertEqual(research_worker._mark_work_running(claim, self.Session, NOW), "running")
        outcome = research_worker.fail_research_work(
            claim,
            research_worker.TransientResearchError("临时网络错误"),
            transient=True,
            session_factory=self.Session,
            now=NOW,
        )
        self.assertEqual(outcome, "retrying")

        for attempt in (2, 3):
            next_claim = research_worker.claim_next_research_work(
                f"worker-{attempt}",
                github_available=True,
                session_factory=self.Session,
                now=NOW + timedelta(seconds=10 * attempt),
                lease_seconds=30,
            )
            self.assertEqual(next_claim.attempt_count, attempt)
            self.assertEqual(
                research_worker._mark_work_running(
                    next_claim, self.Session, NOW + timedelta(seconds=10 * attempt)
                ),
                "running",
            )
            outcome = research_worker.fail_research_work(
                next_claim,
                research_worker.TransientResearchError("临时网络错误"),
                transient=True,
                session_factory=self.Session,
                now=NOW + timedelta(seconds=10 * attempt),
            )
        self.assertEqual(outcome, "blocked")
        with self.Session() as db:
            work = db.scalar(select(ResearchWorkItem))
            orchestration = db.scalar(select(ResearchOrchestration))
            self.assertEqual(work.attempt_count, 3)
            self.assertEqual(work.status, "failed")
            self.assertEqual(orchestration.state, "blocked")

    def test_expired_lease_recovers_same_interrupted_run(self) -> None:
        prepared, _issue, _comments, _ = approved_issue_graph(self.Session, issue_number=911)
        first = research_worker.claim_next_research_work(
            "worker-before-crash",
            github_available=True,
            session_factory=self.Session,
            now=NOW,
            lease_seconds=10,
        )
        with self.Session.begin() as db:
            db.add(
                ResearchRun(
                    run_id="20000000-0000-0000-0000-000000000011",
                    formal_research_id=first.formal_research_id,
                    strategy_id=prepared.normalized["strategy"]["id"],
                    status="running",
                    stage="simulation",
                    config=prepared.normalized["runConfig"],
                    config_sha256="d" * 64,
                    code_commit=APP_COMMIT,
                    environment_sha256="e" * 64,
                    random_seed=7,
                    metrics={},
                    artifact_root="outputs/research-runs/runs/20000000-0000-0000-0000-000000000011",
                    started_at=NOW,
                    heartbeat_at=NOW,
                )
            )
        recovered = research_worker.claim_next_research_work(
            "worker-after-crash",
            github_available=True,
            session_factory=self.Session,
            now=NOW + timedelta(seconds=11),
            lease_seconds=30,
        )
        self.assertEqual(recovered.resume_run_id, "20000000-0000-0000-0000-000000000011")
        self.assertEqual(recovered.attempt_count, 2)
        with self.Session() as db:
            run = db.get(ResearchRun, recovered.resume_run_id)
            self.assertEqual(run.status, "interrupted")
            self.assertIn("租约过期", run.error)

    def test_expired_final_attempt_becomes_blocked_instead_of_sticking(self) -> None:
        payload = valid_plan_payload()
        payload["economicHypothesis"] += " 零自动重试预算。"
        payload["resourceBudget"]["maxRetries"] = 0
        approved_issue_graph(self.Session, issue_number=913, prepared=prepare(payload))
        first = research_worker.claim_next_research_work(
            "worker-final-attempt",
            github_available=True,
            session_factory=self.Session,
            now=NOW,
            lease_seconds=10,
        )
        self.assertEqual(first.max_attempts, 1)

        self.assertIsNone(
            research_worker.claim_next_research_work(
                "worker-after-final-expiry",
                github_available=True,
                session_factory=self.Session,
                now=NOW + timedelta(seconds=11),
                lease_seconds=30,
            )
        )
        with self.Session() as db:
            work = db.scalar(select(ResearchWorkItem))
            orchestration = db.scalar(select(ResearchOrchestration))
            self.assertEqual(work.status, "failed")
            self.assertIsNone(work.lease_owner)
            self.assertEqual(orchestration.state, "blocked")

    def test_stop_racing_with_completion_blocks_evaluation_but_keeps_run_success(self) -> None:
        prepared, _issue, _comments, _ = approved_issue_graph(self.Session, issue_number=914)
        claim = research_worker.claim_next_research_work(
            "worker-completion-race",
            github_available=True,
            session_factory=self.Session,
            now=NOW,
            lease_seconds=30,
        )
        self.assertEqual(research_worker._mark_work_running(claim, self.Session, NOW), "running")
        run_id = "20000000-0000-0000-0000-000000000014"
        with self.Session.begin() as db:
            db.add(
                ResearchRun(
                    run_id=run_id,
                    formal_research_id=claim.formal_research_id,
                    strategy_id=prepared.normalized["strategy"]["id"],
                    status="succeeded",
                    stage="finalize",
                    config=prepared.normalized["runConfig"],
                    config_sha256="d" * 64,
                    code_commit=APP_COMMIT,
                    environment_sha256="e" * 64,
                    random_seed=7,
                    metrics={},
                    result_fingerprint="f" * 64,
                    artifact_root="outputs/research-runs/runs/20000000-0000-0000-0000-000000000014",
                    started_at=NOW,
                    heartbeat_at=NOW,
                    finished_at=NOW + timedelta(seconds=1),
                )
            )
            work = db.get(ResearchWorkItem, claim.work_item_id)
            work.stop_requested_at = NOW + timedelta(seconds=1)
            orchestration = db.get(ResearchOrchestration, claim.orchestration_id)
            transition_orchestration(orchestration, "stopping", reason="测试完成竞态")

        outcome = research_worker.complete_research_work(
            claim,
            research_worker.ResearchRunResult(run_id=run_id, path=Path("."), manifest={}),
            session_factory=self.Session,
            now=NOW + timedelta(seconds=2),
        )
        self.assertEqual(outcome, "stopped")
        with self.Session() as db:
            work = db.get(ResearchWorkItem, claim.work_item_id)
            orchestration = db.get(ResearchOrchestration, claim.orchestration_id)
            formal = db.get(FormalResearch, claim.formal_research_id)
            self.assertEqual(work.status, "succeeded")
            self.assertEqual(orchestration.state, "stopped")
            self.assertEqual(formal.phase, "stopped")

    def test_data_sync_and_formal_research_do_not_claim_concurrently(self) -> None:
        approved_issue_graph(self.Session, issue_number=912)
        research_claim = research_worker.claim_next_research_work(
            "research-worker",
            github_available=True,
            session_factory=self.Session,
            now=NOW,
            lease_seconds=30,
        )
        self.assertIsNotNone(research_claim)
        with self.Session.begin() as db:
            db.add(
                DataSyncJob(
                    id="sync-after-research",
                    action="trade_calendar",
                    status="queued",
                    payload={},
                    payload_hash="s" * 64,
                    active_key="sync-after-research",
                    next_attempt_at=NOW,
                )
            )
        self.assertIsNone(
            sync_worker.claim_next_job(
                "data-worker",
                session_factory=self.Session,
                now=NOW,
                lease_seconds=30,
            )
        )


@unittest.skipUnless(
    os.getenv("TEST_RESEARCH_WORKER_POSTGRES_URL"),
    "TEST_RESEARCH_WORKER_POSTGRES_URL 未配置，跳过 PostgreSQL 研究 Worker 集成测试",
)
class ResearchWorkerPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        database_url = os.environ["TEST_RESEARCH_WORKER_POSTGRES_URL"]
        parsed = make_url(database_url)
        if parsed.host not in {"127.0.0.1", "localhost"} or parsed.database != "quant_worker_test":
            raise AssertionError("研究 Worker 集成测试只允许本机 quant_worker_test 隔离库")
        cls.engine = create_engine(database_url, pool_pre_ping=True)
        with cls.engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        with cls.engine.connect() as connection:
            command.upgrade(alembic_config(connection), "head")
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls) -> None:
        with cls.engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        cls.engine.dispose()

    def setUp(self) -> None:
        with self.Session.begin() as db:
            for table in reversed(Base.metadata.sorted_tables):
                db.execute(table.delete())

    def test_postgres_advisory_lock_keeps_global_concurrency_at_one(self) -> None:
        approved_issue_graph(self.Session, issue_number=920)
        second_payload = valid_plan_payload()
        second_payload["economicHypothesis"] += " 第二个独立研究计划。"
        approved_issue_graph(self.Session, issue_number=921, prepared=prepare(second_payload))

        with self.Session() as db:
            work_items = db.scalars(
                select(ResearchWorkItem).order_by(ResearchWorkItem.created_at, ResearchWorkItem.id)
            ).all()
            orchestration_id = work_items[0].orchestration_id
        with self.assertRaisesRegex(DBAPIError, "research orchestration relation mismatch"):
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE research_orchestrations SET issue_number = -1 WHERE id = :id"
                    ),
                    {"id": orchestration_id},
                )
        with self.assertRaisesRegex(DBAPIError, "research orchestration relation mismatch"):
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE research_work_items SET orchestration_id = :wrong_orchestration "
                        "WHERE id = :work_item_id"
                    ),
                    {
                        "wrong_orchestration": work_items[0].orchestration_id,
                        "work_item_id": work_items[1].id,
                    },
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(
                executor.map(
                    lambda worker: research_worker.claim_next_research_work(
                        worker,
                        github_available=True,
                        session_factory=self.Session,
                        now=NOW,
                        lease_seconds=30,
                    ),
                    ("worker-pg-a", "worker-pg-b"),
                )
            )
        self.assertEqual(len([claim for claim in claims if claim is not None]), 1)
        with self.Session() as db:
            active = db.scalar(
                select(func.count()).select_from(ResearchWorkItem).where(
                    ResearchWorkItem.status == "leased"
                )
            )
            queued = db.scalar(
                select(func.count()).select_from(ResearchWorkItem).where(
                    ResearchWorkItem.status == "queued"
                )
            )
        self.assertEqual(active, 1)
        self.assertEqual(queued, 1)


if __name__ == "__main__":
    unittest.main()
