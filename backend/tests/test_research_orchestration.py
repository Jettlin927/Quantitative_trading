from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import os
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from alembic import command
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base, alembic_config
from backend.app.github_research import (
    GitHubIssueClient,
    GitHubPermissionError,
    GitHubUnavailableError,
    poll_research_issues_once,
)
from backend.app.models import (
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
    OrchestrationResult,
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
from backend.app.research_publication import PublicationConflictError
from backend.app import research_worker


REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
APP_COMMIT = "c" * 40


def valid_plan_payload() -> dict:
    config = json.loads(
        (
            REPO_ROOT
            / "configs/research/etf_volatility_managed_baseline.json"
        ).read_text(
            encoding="utf-8"
        )
    )
    config["qualityRunId"] = "quality-ready"
    config["startDate"] = "2025-12-01"
    config["validationPolicy"] = {
        "mode": "anchored",
        "trainPeriods": 60,
        "testPeriods": 20,
        "stepPeriods": 20,
    }
    config["riskPolicy"] = {
        "mode": "rolling_covariance",
        "lookbackPeriods": 60,
        "minPeriods": 20,
    }
    research_pass_policy = {
        "oosPerformance": {
            "minimumTotalReturn": "0.00",
            "minimumExcessTotalReturn": "0.00",
        },
        "risk": {
            "maximumAbsoluteMaxDrawdown": "0.20",
            "maximumEs95": "0.10",
            "maximumMaxSingleWeight": "0.80",
            "maximumHhi": "0.70",
        },
        "walkForward": {
            "minimumWindowTotalReturn": "-0.10",
            "minimumPositiveWindowRate": "0.50",
        },
        "costStress": {
            "minimumStressedTotalReturn": "-0.05",
            "maximumAbsoluteReturnDifference": "0.05",
        },
        "multipleTesting": {
            "minimumDsrProbability": "0.95",
            "maximumPboProbability": "0.20",
        },
        "parameterNeighborhood": {
            "variants": [
                {"id": "base", "changes": []},
                {
                    "id": "lower",
                    "changes": [
                        {
                            "path": "featureParameters.exposurePower",
                            "value": "0.5",
                        }
                    ],
                },
                {
                    "id": "upper",
                    "changes": [
                        {
                            "path": "featureParameters.realizedVarianceEstimator",
                            "value": "trailing_3_month_mean",
                        }
                    ],
                },
            ],
            "maximumAbsoluteOosReturnDifference": "0.20",
            "minimumOosTotalReturn": "-0.20",
        },
        "capacity": {
            "expectedCapital": "1000000",
            "advLookbackPeriods": 20,
            "minimumAdvObservations": 5,
            "marketAmountScale": "1000",
            "maximumAdvParticipationRate": "0.10",
            "impactModel": {"type": "linear", "coefficient": "0.10"},
            "maximumModeledImpactRate": "0.01",
        },
    }
    return {
        "schemaVersion": "research-plan/v3",
        "strategy": {
            "id": config["strategyId"],
            "version": config["strategyVersion"],
            "displayName": "ETF 波动率管理基线",
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
            "requiredArtifacts": [
                "benchmark_nav.csv.gz",
                "manifest.json",
                "metrics.json",
                "oos_metrics.json",
                "positions.csv.gz",
                "rebalance_executions.csv.gz",
                "rebalance_requests.csv.gz",
                "report.html",
                "risk_contributions.csv.gz",
                "risk_exposures.csv.gz",
                "walk_forward_metrics.csv.gz",
                "walk_forward_windows.csv.gz",
            ],
            "conclusionValues": [
                "研究通过",
                "有条件候选",
                "证据不足",
                "受阻",
                "不通过",
            ],
            "evaluationPolicy": {
                "marketRegime": {
                    "directionLookbackPeriods": 20,
                    "upThreshold": "0.03",
                    "downThreshold": "-0.03",
                    "volatilityLookbackPeriods": 20,
                    "highVolatilityThreshold": "0.20",
                },
                "costStressMultiplier": "2",
            },
            "researchPassPolicy": research_pass_policy,
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
    issue = IssueSnapshot(
        number=issue_number, state="OPEN", body="", labels=("类型:策略研究",)
    )
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
    def test_canonical_issue_template_is_accepted_by_active_plan_parser(self) -> None:
        template_path = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "正式研究计划.md"
        template = template_path.read_text(encoding="utf-8")
        machine_json = template.split(PLAN_START_MARKER, 1)[1].split(
            PLAN_END_MARKER, 1
        )[0]
        payload = json.loads(machine_json.strip().removeprefix("```json").removesuffix("```").strip())
        valid = valid_plan_payload()
        for field in (
            "strategy",
            "economicHypothesis",
            "runConfig",
            "sampleSplits",
            "reportContract",
        ):
            payload[field] = deepcopy(valid[field])

        prepared = prepare_research_plan(
            issue_body(payload), verify_universe_source=False
        )

        self.assertEqual(prepared.normalized["schemaVersion"], "research-plan/v3")
        self.assertIn(
            "oos_metrics.json",
            prepared.normalized["reportContract"]["requiredArtifacts"],
        )
        self.assertIn("evaluationPolicy", prepared.normalized["reportContract"])
        self.assertIn("researchPassPolicy", prepared.normalized["reportContract"])

    def test_canonical_hash_is_stable_for_key_and_set_order(self) -> None:
        payload = valid_plan_payload()
        first = prepare(payload)
        reordered = {key: payload[key] for key in reversed(list(payload))}
        reordered["gates"] = list(reversed(reordered["gates"]))
        reordered["reportContract"] = dict(
            reversed(list(reordered["reportContract"].items()))
        )
        second = prepare(reordered)

        self.assertEqual(first.plan_sha256, second.plan_sha256)
        self.assertEqual(first.normalized, second.normalized)
        changed = deepcopy(payload)
        changed["economicHypothesis"] += " 新证据会推翻该假设。"
        self.assertNotEqual(first.plan_sha256, prepare(changed).plan_sha256)
        changed_capacity = deepcopy(payload)
        changed_capacity["reportContract"]["researchPassPolicy"]["capacity"][
            "expectedCapital"
        ] = "2000000"
        self.assertNotEqual(
            first.plan_sha256, prepare(changed_capacity).plan_sha256
        )
        changed_oos_gate = deepcopy(payload)
        changed_oos_gate["reportContract"]["researchPassPolicy"][
            "oosPerformance"
        ]["minimumTotalReturn"] = "0.01"
        self.assertNotEqual(
            first.plan_sha256, prepare(changed_oos_gate).plan_sha256
        )

    def test_float_and_over_budget_plan_are_rejected(self) -> None:
        floating = valid_plan_payload()
        floating["resourceBudget"]["cpuCores"] = 0.75
        with self.assertRaisesRegex(ResearchPlanError, "禁止 JSON 浮点数"):
            prepare(floating)

        integer_cpu = valid_plan_payload()
        integer_cpu["resourceBudget"]["cpuCores"] = 1
        with self.assertRaisesRegex(ResearchPlanError, "字符串化十进制定点"):
            prepare(integer_cpu)

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
        with self.assertRaisesRegex(ResearchPlanError, "下一.*日"):
            prepare(payload)

        payload = valid_plan_payload()
        payload["runConfig"]["validationPolicy"] = {"mode": "none"}
        with self.assertRaisesRegex(ResearchPlanError, "walk-forward"):
            prepare(payload)

        payload = valid_plan_payload()
        payload["reportContract"]["evaluationPolicy"]["marketRegime"][
            "directionLookbackPeriods"
        ] = 1
        with self.assertRaises(ResearchPlanError):
            prepare(payload)

        payload = valid_plan_payload()
        payload["parameterSpace"] = {}
        with self.assertRaisesRegex(ResearchPlanError, "单一冻结批次"):
            prepare(payload)

        payload = valid_plan_payload()
        payload["reportContract"]["researchPassPolicy"][
            "parameterNeighborhood"
        ]["variants"][1]["changes"][0]["path"] = "costModel.buyRate"
        with self.assertRaisesRegex(ResearchPlanError, "参数邻域"):
            prepare(payload)

        payload = valid_plan_payload()
        payload["reportContract"]["researchPassPolicy"][
            "parameterNeighborhood"
        ]["variants"][1]["changes"][0]["value"] = "1"
        with self.assertRaisesRegex(ResearchPlanError, "不同的实际参数"):
            prepare(payload)

        payload = valid_plan_payload()
        payload["reportContract"]["researchPassPolicy"][
            "parameterNeighborhood"
        ]["variants"][2]["changes"] = [
            {
                "path": "featureParameters.exposurePower",
                "value": "0.50",
            }
        ]
        with self.assertRaisesRegex(ResearchPlanError, "不同的实际参数"):
            prepare(payload)

        payload = valid_plan_payload()
        payload["sampleSplits"][0]["startDate"] = "20251201"
        payload["runConfig"]["startDate"] = "20251201"
        with self.assertRaisesRegex(ResearchPlanError, "YYYY-MM-DD"):
            prepare(payload)

    def test_duplicate_json_keys_are_rejected_instead_of_silently_overwritten(
        self,
    ) -> None:
        body = (
            f"{PLAN_START_MARKER}\n"
            '{"schemaVersion":"research-plan/v3","schemaVersion":"other"}'
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

    def test_published_label_is_reserved_for_atomic_publication_finalizer(self) -> None:
        prepared = prepare()

        class PublishedClient:
            def __init__(self):
                self.labels = []

            def list_research_issues(self):
                return [
                    {
                        "number": 908,
                        "state": "open",
                        "body": issue_body(valid_plan_payload()),
                        "labels": [
                            {"name": "类型:策略研究"},
                            {"name": "研究:运行中"},
                        ],
                    }
                ]

            def list_comments(self, _number):
                return [
                    {
                        "id": 90800,
                        "user": {"login": "Jettlin927"},
                        "body": prepared.approval_comment,
                    }
                ]

            def confirm_comment(self, *_args, **_kwargs):
                return {"id": 1}

            def set_state_label(self, number, _current, desired):
                self.labels.append((number, desired))

        result = OrchestrationResult(
            issue_number=908,
            plan_sha256=prepared.plan_sha256,
            state="published",
            desired_label="研究:已发布",
            approval_found=True,
            queue_created=False,
        )
        client = PublishedClient()
        with patch("backend.app.github_research.apply_issue_plan", return_value=result):
            poll = poll_research_issues_once(
                client,
                self.Session,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                limits=ResearchServerLimits(),
            )

        self.assertTrue(poll.github_available)
        self.assertEqual(poll.processed, (result,))
        self.assertEqual(client.labels, [])

    def test_historical_issue_is_read_only_and_never_enters_approval_queue(self) -> None:
        class HistoricalClient:
            def __init__(self):
                self.comments_read = False

            def list_research_issues(self):
                return [
                    {
                        "number": 9210,
                        "state": "open",
                        "body": "历史评价发布入口，不是研究计划。",
                        "labels": [
                            {"name": "类型:策略研究"},
                            {"name": "来源:历史导入"},
                        ],
                    }
                ]

            def list_comments(self, _number):
                self.comments_read = True
                raise AssertionError("历史 Issue 不应进入批准解析")

        client = HistoricalClient()
        poll = poll_research_issues_once(
            client,
            self.Session,
            app_git_commit=APP_COMMIT,
            app_git_ref="refs/heads/main",
            limits=ResearchServerLimits(),
        )

        self.assertTrue(poll.github_available)
        self.assertEqual(poll.processed, ())
        self.assertEqual(poll.errors, ())
        self.assertFalse(client.comments_read)
        with self.Session() as db:
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchWorkItem)), 0
            )

    def test_exact_approval_is_idempotent_and_creates_one_queue(self) -> None:
        prepared = prepare()
        issue = IssueSnapshot(number=902, state="OPEN", body="")
        wrong = [
            CommentSnapshot(
                id=1, author_login="Jettlin927", body=prepared.approval_comment + " "
            )
        ]
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

        exact = [
            CommentSnapshot(
                id=2, author_login="Jettlin927", body=prepared.approval_comment
            )
        ]
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
            self.assertEqual(
                db.scalar(select(func.count()).select_from(FormalResearch)), 1
            )
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchWorkItem)), 1
            )
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchEvent)), 2
            )

    def test_write_permission_must_be_confirmed_before_queue(self) -> None:
        prepared = prepare()
        issue = IssueSnapshot(number=903, state="OPEN", body="")
        comments = [
            CommentSnapshot(
                id=3, author_login="Jettlin927", body=prepared.approval_comment
            )
        ]
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
            self.assertEqual(
                db.scalar(select(func.count()).select_from(FormalResearch)), 0
            )
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchWorkItem)), 0
            )

    def test_deleted_or_edited_approval_comment_invalidates_existing_queue(
        self,
    ) -> None:
        prepared, issue, _comments, _ = approved_issue_graph(
            self.Session, issue_number=9031
        )
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
                select(func.count())
                .select_from(ResearchEvent)
                .where(ResearchEvent.event_type == "approval_comment_invalidated")
            )
        self.assertEqual(invalidations, 1)

    def test_duplicate_approval_cannot_override_an_earlier_stop(self) -> None:
        prepared = prepare()
        issue = IssueSnapshot(
            number=90311, state="OPEN", body=issue_body(valid_plan_payload())
        )
        approval = CommentSnapshot(
            id=9031100,
            author_login="Jettlin927",
            body=prepared.approval_comment,
        )
        stop = CommentSnapshot(
            id=9031101,
            author_login="Jettlin927",
            body=prepared.stop_comment,
        )
        duplicate = CommentSnapshot(
            id=9031102,
            author_login="Jettlin927",
            body=prepared.approval_comment,
        )
        with self.Session.begin() as db:
            result = apply_issue_plan(
                db,
                issue,
                [approval, stop, duplicate],
                prepared,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=True,
                now=NOW,
            )
        self.assertEqual(result.state, "stopped")
        with self.Session() as db:
            formal = db.scalar(select(FormalResearch))
            persisted_approval = formal.approval_id
            self.assertEqual(
                db.scalar(select(ResearchWorkItem.status)),
                "interrupted",
            )
            self.assertEqual(
                db.scalar(
                    select(ResearchEvent.payload_json).where(
                        ResearchEvent.event_type == "plan_approved"
                    )
                )["approvalCommentId"],
                approval.id,
            )
            self.assertIsNotNone(persisted_approval)

    def test_duplicate_approval_cannot_replace_deleted_bound_approval(self) -> None:
        prepared = prepare()
        issue = IssueSnapshot(
            number=90313, state="OPEN", body=issue_body(valid_plan_payload())
        )
        original = CommentSnapshot(
            id=9031300,
            author_login="Jettlin927",
            body=prepared.approval_comment,
        )
        with self.Session.begin() as db:
            apply_issue_plan(
                db,
                issue,
                [original],
                prepared,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=True,
                now=NOW,
            )
        replacement = CommentSnapshot(
            id=9031301,
            author_login="Jettlin927",
            body=prepared.approval_comment,
        )
        with self.Session.begin() as db:
            result = apply_issue_plan(
                db,
                issue,
                [replacement],
                prepared,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=True,
                now=NOW + timedelta(minutes=1),
            )
        self.assertEqual(result.state, "blocked")
        self.assertFalse(result.approval_found)

    def test_summary_edit_before_approval_invalidates_reused_plan(self) -> None:
        payload = valid_plan_payload()
        prepared = prepare(payload)
        original = IssueSnapshot(number=90314, state="OPEN", body=issue_body(payload))
        with self.Session.begin() as db:
            first = apply_issue_plan(
                db,
                original,
                [],
                prepared,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=False,
                now=NOW,
            )
        self.assertEqual(first.state, "pending_approval")
        edited = IssueSnapshot(
            number=original.number,
            state="OPEN",
            body=original.body.replace("固定研究计划。", "批准前补充的中文摘要。"),
        )
        with self.Session.begin() as db:
            edited_result = apply_issue_plan(
                db,
                edited,
                [],
                prepared,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=False,
                now=NOW + timedelta(minutes=1),
            )
        self.assertEqual(edited_result.state, "blocked")
        approval = CommentSnapshot(
            id=9031400,
            author_login="Jettlin927",
            body=prepared.approval_comment,
        )
        with self.Session.begin() as db:
            reused_approval = apply_issue_plan(
                db,
                edited,
                [approval],
                prepared,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=True,
                now=NOW + timedelta(minutes=2),
            )
        self.assertEqual(reused_approval.state, "blocked")
        self.assertFalse(reused_approval.queue_created)
        self.assertIn("永久失效", reused_approval.reason)
        with self.Session() as db:
            self.assertTrue(
                db.scalar(select(ResearchOrchestration.approval_invalidated))
            )
            self.assertIsNone(db.scalar(select(ResearchWorkItem.id)))

    def test_editing_chinese_summary_invalidates_same_machine_plan(self) -> None:
        payload = valid_plan_payload()
        prepared = prepare(payload)
        original = IssueSnapshot(number=90312, state="OPEN", body=issue_body(payload))
        comments = [
            CommentSnapshot(
                id=9031200,
                author_login="Jettlin927",
                body=prepared.approval_comment,
            )
        ]
        with self.Session.begin() as db:
            apply_issue_plan(
                db,
                original,
                comments,
                prepared,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=True,
                now=NOW,
            )

        edited = IssueSnapshot(
            number=original.number,
            state="OPEN",
            body=original.body.replace("固定研究计划。", "已编辑的中文摘要。"),
        )
        with self.Session.begin() as db:
            result = apply_issue_plan(
                db,
                edited,
                comments,
                prepared,
                app_git_commit=APP_COMMIT,
                app_git_ref="refs/heads/main",
                authorization_write_confirmed=True,
                now=NOW + timedelta(minutes=1),
            )
        self.assertEqual(result.state, "blocked")
        self.assertIn("永久失效", result.reason)
        with self.Session() as db:
            self.assertTrue(
                db.scalar(select(ResearchOrchestration.approval_invalidated))
            )
            self.assertEqual(db.scalar(select(ResearchWorkItem.status)), "interrupted")

    def test_invalid_edit_before_formal_creation_cannot_revive_old_approval(
        self,
    ) -> None:
        prepared = prepare()
        issue = IssueSnapshot(
            number=9032, state="OPEN", body=issue_body(valid_plan_payload())
        )
        comments = [
            CommentSnapshot(
                id=903200, author_login="Jettlin927", body=prepared.approval_comment
            )
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
            self.assertEqual(
                db.scalar(select(func.count()).select_from(FormalResearch)), 0
            )

    def test_wrong_author_closed_issue_and_code_mismatch_never_start(self) -> None:
        prepared = prepare()
        wrong_author = [
            CommentSnapshot(
                id=31, author_login="someone-else", body=prepared.approval_comment
            )
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
            self.assertEqual(
                db.scalar(select(func.count()).select_from(FormalResearch)), 0
            )
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchWorkItem)), 0
            )

    def test_closing_an_already_queued_issue_cancels_the_queue_idempotently(
        self,
    ) -> None:
        prepared, _issue, comments, _ = approved_issue_graph(
            self.Session, issue_number=934
        )
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

    def test_edit_invalidates_old_approval_and_old_hash_cannot_start_new_plan(
        self,
    ) -> None:
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
                select(FrozenResearchPlan)
                .where(FrozenResearchPlan.issue_number == issue.number)
                .order_by(FrozenResearchPlan.version)
            ).all()
            old_state = db.scalar(
                select(ResearchOrchestration.state).where(
                    ResearchOrchestration.plan_id == plans[0].id
                )
            )
            old_work = db.scalar(select(ResearchWorkItem))
            old_formal = db.scalar(select(FormalResearch))
            self.assertEqual(
                [plan.plan_sha256 for plan in plans],
                [old.plan_sha256, changed.plan_sha256],
            )
            self.assertEqual(old_state, "stopped")
            self.assertEqual(old_work.status, "interrupted")
            self.assertEqual(old_formal.phase, "stopped")

    def test_edit_after_run_success_stops_before_evaluation_without_waiting_for_worker(
        self,
    ) -> None:
        prepared, issue, comments, _ = approved_issue_graph(
            self.Session, issue_number=909
        )
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
                .join(
                    FrozenResearchPlan,
                    FrozenResearchPlan.id == ResearchOrchestration.plan_id,
                )
                .where(FrozenResearchPlan.plan_sha256 == prepared.plan_sha256)
            )
            formal = db.get(FormalResearch, old_orchestration.formal_research_id)
            self.assertEqual(old_orchestration.state, "stopped")
            self.assertEqual(formal.phase, "stopped")
            self.assertIsNotNone(formal.completed_at)

    def test_stop_comment_preserves_records_and_stops_before_start(self) -> None:
        prepared, issue, comments, _ = approved_issue_graph(
            self.Session, issue_number=905
        )
        comments.append(
            CommentSnapshot(
                id=90501, author_login="Jettlin927", body=prepared.stop_comment
            )
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
            self.assertGreaterEqual(
                db.scalar(select(func.count()).select_from(ResearchEvent)), 3
            )

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
        prepared, issue, comments, _ = approved_issue_graph(
            self.Session, issue_number=908
        )
        run_id = "20000000-0000-0000-0000-000000000008"
        attempt_id = "30000000-0000-0000-0000-000000000008"
        with self.Session.begin() as db:
            formal = db.scalar(select(FormalResearch))
            db.add(
                ResearchRun(
                    run_id=run_id,
                    formal_research_id=formal.id,
                    orchestration_attempt_id=attempt_id,
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
            work.current_attempt_id = attempt_id
            work.current_run_id = run_id
            work.resume_run_id = run_id
            work.stop_requested_at = NOW + timedelta(minutes=1)
            orchestration = db.scalar(select(ResearchOrchestration))
            transition_orchestration(orchestration, "stopped", reason="测试停止")
            formal.phase = "stopped"
            formal.completed_at = NOW + timedelta(minutes=1)

        comments.extend(
            [
                CommentSnapshot(
                    id=90801, author_login="Jettlin927", body=prepared.stop_comment
                ),
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
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchWorkItem)), 0
            )

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

    def test_malformed_edit_invalidates_an_existing_queue_before_comment_write(
        self,
    ) -> None:
        prepared, _issue, _comments, _ = approved_issue_graph(
            self.Session, issue_number=907
        )

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

    def test_deterministic_publication_failure_does_not_block_work_claim(self) -> None:
        approved_issue_graph(self.Session, issue_number=930)
        github_available = Event()
        github_available.set()

        with patch(
            "backend.app.research_worker.publish_next_pending_research_evaluation",
            side_effect=PublicationConflictError("模拟确定性发布合同错误"),
        ):
            research_worker._publish_pending_evaluation_once(
                session_factory=self.Session,
                github=object(),
                github_available=github_available,
                artifact_root=Path("unused"),
                public_base_url="http://127.0.0.1:15173",
                readback_base_url=None,
                retry_failed_after_seconds=300,
            )

        self.assertTrue(github_available.is_set())
        claim = research_worker.claim_next_research_work(
            "worker-after-publication-error",
            github_available=github_available.is_set(),
            session_factory=self.Session,
            now=NOW,
            lease_seconds=30,
        )
        self.assertIsNotNone(claim)

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
        self.assertEqual(
            research_worker._mark_work_running(claim, self.Session, NOW), "running"
        )
        outcome = research_worker.fail_research_work(
            claim,
            TimeoutError("临时网络错误"),
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
                TimeoutError("临时网络错误"),
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
        prepared, _issue, _comments, _ = approved_issue_graph(
            self.Session, issue_number=911
        )
        first = research_worker.claim_next_research_work(
            "worker-reused-after-crash",
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
                    orchestration_attempt_id=first.attempt_id,
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
            "worker-reused-after-crash",
            github_available=True,
            session_factory=self.Session,
            now=NOW + timedelta(seconds=11),
            lease_seconds=30,
        )
        self.assertEqual(
            recovered.resume_run_id, "20000000-0000-0000-0000-000000000011"
        )
        self.assertEqual(recovered.attempt_count, 2)
        self.assertEqual(recovered.attempt_id, first.attempt_id)
        self.assertNotEqual(recovered.lease_token, first.lease_token)
        self.assertEqual(
            research_worker.heartbeat_research_work(
                first,
                session_factory=self.Session,
                now=NOW + timedelta(seconds=12),
                lease_seconds=30,
            ),
            "lease_lost",
        )
        with self.Session() as db:
            run = db.get(ResearchRun, recovered.resume_run_id)
            self.assertEqual(run.status, "interrupted")
            self.assertIn("租约过期", run.error)

    def test_expired_lease_reconciles_terminal_run_without_duplicate_execution(
        self,
    ) -> None:
        prepared, _issue, _comments, _ = approved_issue_graph(
            self.Session, issue_number=9111
        )
        claim = research_worker.claim_next_research_work(
            "worker-before-terminal-crash",
            github_available=True,
            session_factory=self.Session,
            now=NOW,
            lease_seconds=10,
        )
        self.assertEqual(
            research_worker._mark_work_running(claim, self.Session, NOW), "running"
        )
        run_id = "20000000-0000-0000-0000-000000000111"
        with self.Session.begin() as db:
            db.add(
                ResearchRun(
                    run_id=run_id,
                    formal_research_id=claim.formal_research_id,
                    orchestration_attempt_id=claim.attempt_id,
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
                    artifact_root="outputs/research-runs/runs/20000000-0000-0000-0000-000000000111",
                    started_at=NOW,
                    heartbeat_at=NOW,
                    finished_at=NOW + timedelta(seconds=1),
                )
            )

        duplicate = research_worker.claim_next_research_work(
            "worker-after-terminal-crash",
            github_available=True,
            session_factory=self.Session,
            now=NOW + timedelta(seconds=11),
            lease_seconds=30,
        )
        self.assertIsNone(duplicate)
        with self.Session() as db:
            work = db.get(ResearchWorkItem, claim.work_item_id)
            formal = db.get(FormalResearch, claim.formal_research_id)
            self.assertEqual(work.status, "succeeded")
            self.assertEqual(work.current_run_id, run_id)
            self.assertEqual(formal.phase, "evaluating")
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchRun)), 1
            )

    def test_expired_lease_blocks_terminal_failure_without_blind_retry(self) -> None:
        prepared, _issue, _comments, _ = approved_issue_graph(
            self.Session, issue_number=9112
        )
        claim = research_worker.claim_next_research_work(
            "worker-before-failure-crash",
            github_available=True,
            session_factory=self.Session,
            now=NOW,
            lease_seconds=10,
        )
        self.assertEqual(
            research_worker._mark_work_running(claim, self.Session, NOW), "running"
        )
        run_id = "20000000-0000-0000-0000-000000000112"
        with self.Session.begin() as db:
            db.add(
                ResearchRun(
                    run_id=run_id,
                    formal_research_id=claim.formal_research_id,
                    orchestration_attempt_id=claim.attempt_id,
                    strategy_id=prepared.normalized["strategy"]["id"],
                    status="failed",
                    stage="simulation",
                    config=prepared.normalized["runConfig"],
                    config_sha256="d" * 64,
                    code_commit=APP_COMMIT,
                    environment_sha256="e" * 64,
                    random_seed=7,
                    metrics={},
                    error="ValueError: 确定性错误",
                    artifact_root="outputs/research-runs/runs/20000000-0000-0000-0000-000000000112",
                    started_at=NOW,
                    heartbeat_at=NOW,
                    finished_at=NOW + timedelta(seconds=1),
                )
            )

        retry = research_worker.claim_next_research_work(
            "worker-after-failure-crash",
            github_available=True,
            session_factory=self.Session,
            now=NOW + timedelta(seconds=11),
            lease_seconds=30,
        )
        self.assertIsNone(retry)
        with self.Session() as db:
            work = db.get(ResearchWorkItem, claim.work_item_id)
            orchestration = db.get(ResearchOrchestration, claim.orchestration_id)
            self.assertEqual(work.status, "failed")
            self.assertEqual(orchestration.state, "blocked")
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchRun)), 1
            )

    def test_heartbeat_failure_retries_instead_of_becoming_user_stop(self) -> None:
        prepared, _issue, _comments, _ = approved_issue_graph(
            self.Session,
            issue_number=9113,
        )
        claim = research_worker.claim_next_research_work(
            "worker-heartbeat-failure",
            github_available=True,
            session_factory=self.Session,
            now=NOW,
            lease_seconds=30,
        )
        run_id = "20000000-0000-0000-0000-000000001113"

        def wait_for_heartbeat(_claim, stop_event):
            self.assertTrue(stop_event.wait(timeout=1))
            with self.Session.begin() as db:
                db.add(
                    ResearchRun(
                        run_id=run_id,
                        formal_research_id=claim.formal_research_id,
                        orchestration_attempt_id=claim.attempt_id,
                        strategy_id=prepared.normalized["strategy"]["id"],
                        status="interrupted",
                        stage="simulation",
                        config=prepared.normalized["runConfig"],
                        config_sha256="d" * 64,
                        code_commit=APP_COMMIT,
                        environment_sha256="e" * 64,
                        random_seed=7,
                        metrics={},
                        artifact_root=f"outputs/research-runs/runs/.{run_id}.tmp",
                        started_at=NOW,
                        heartbeat_at=NOW,
                        finished_at=NOW + timedelta(seconds=1),
                    )
                )
            raise research_worker.ResearchStopRequested("在安全点停止")

        with patch.object(
            research_worker,
            "heartbeat_research_work",
            side_effect=TimeoutError("临时数据库断连"),
        ):
            outcome = research_worker.execute_claimed_research_work(
                claim,
                executor=wait_for_heartbeat,
                session_factory=self.Session,
                heartbeat_interval_seconds=0.01,
                lease_seconds=30,
            )
        self.assertEqual(outcome, "retrying")
        with self.Session() as db:
            work = db.get(ResearchWorkItem, claim.work_item_id)
            self.assertEqual(work.status, "queued")
            self.assertEqual(work.current_run_id, run_id)
            self.assertEqual(work.resume_run_id, run_id)
            self.assertEqual(
                db.get(ResearchOrchestration, claim.orchestration_id).state,
                "queued",
            )

    def test_failed_resume_clears_pointer_before_transient_retry(self) -> None:
        prepared, _issue, _comments, _ = approved_issue_graph(
            self.Session,
            issue_number=9117,
        )
        first = research_worker.claim_next_research_work(
            "worker-first-interruption",
            github_available=True,
            session_factory=self.Session,
            now=NOW,
            lease_seconds=30,
        )
        run_id = "20000000-0000-0000-0000-000000001117"
        with self.Session.begin() as db:
            db.add(
                ResearchRun(
                    run_id=run_id,
                    formal_research_id=first.formal_research_id,
                    orchestration_attempt_id=first.attempt_id,
                    strategy_id=prepared.normalized["strategy"]["id"],
                    status="interrupted",
                    stage="simulation",
                    config=prepared.normalized["runConfig"],
                    config_sha256="d" * 64,
                    code_commit=APP_COMMIT,
                    environment_sha256="e" * 64,
                    random_seed=7,
                    metrics={},
                    artifact_root=f"outputs/research-runs/runs/.{run_id}.tmp",
                    started_at=NOW,
                    heartbeat_at=NOW,
                    finished_at=NOW + timedelta(seconds=1),
                )
            )
        self.assertEqual(
            research_worker.fail_research_work(
                first,
                TimeoutError("首次心跳超时"),
                transient=True,
                session_factory=self.Session,
                now=NOW + timedelta(seconds=1),
            ),
            "retrying",
        )
        resumed = research_worker.claim_next_research_work(
            "worker-resume-failed-run",
            github_available=True,
            session_factory=self.Session,
            now=NOW + timedelta(seconds=5),
            lease_seconds=30,
        )
        self.assertEqual(resumed.resume_run_id, run_id)
        self.assertEqual(resumed.attempt_id, first.attempt_id)
        with self.Session.begin() as db:
            run = db.get(ResearchRun, run_id)
            run.status = "failed"
            run.error = "TimeoutError: 恢复期间基础设施超时"
            run.finished_at = NOW + timedelta(seconds=6)

        self.assertEqual(
            research_worker.fail_research_work(
                resumed,
                TimeoutError("恢复期间基础设施超时"),
                transient=True,
                session_factory=self.Session,
                now=NOW + timedelta(seconds=6),
            ),
            "retrying",
        )
        with self.Session() as db:
            work = db.get(ResearchWorkItem, first.work_item_id)
            self.assertIsNone(work.resume_run_id)

        fresh = research_worker.claim_next_research_work(
            "worker-fresh-after-failed-resume",
            github_available=True,
            session_factory=self.Session,
            now=NOW + timedelta(seconds=12),
            lease_seconds=30,
        )
        self.assertIsNone(fresh.resume_run_id)
        self.assertNotEqual(fresh.attempt_id, resumed.attempt_id)

    def test_unexpected_heartbeat_error_blocks_without_retry(self) -> None:
        approved_issue_graph(self.Session, issue_number=9114)
        claim = research_worker.claim_next_research_work(
            "worker-heartbeat-programming-error",
            github_available=True,
            session_factory=self.Session,
            now=NOW,
            lease_seconds=30,
        )

        def wait_for_heartbeat(_claim, stop_event):
            self.assertTrue(stop_event.wait(timeout=1))
            raise research_worker.ResearchStopRequested("在安全点停止")

        with patch.object(
            research_worker,
            "heartbeat_research_work",
            side_effect=ValueError("确定性程序错误"),
        ):
            outcome = research_worker.execute_claimed_research_work(
                claim,
                executor=wait_for_heartbeat,
                session_factory=self.Session,
                heartbeat_interval_seconds=0.01,
                lease_seconds=30,
            )
        self.assertEqual(outcome, "blocked")
        with self.Session() as db:
            work = db.get(ResearchWorkItem, claim.work_item_id)
            orchestration = db.get(ResearchOrchestration, claim.orchestration_id)
            self.assertEqual(work.status, "failed")
            self.assertEqual(work.last_error_kind, "ResearchHeartbeatError")
            self.assertEqual(orchestration.state, "blocked")

    def test_executor_permanent_failure_blocks_work_item(self) -> None:
        approved_issue_graph(self.Session, issue_number=9118)
        claim = research_worker.claim_next_research_work(
            "worker-permanent-executor-failure",
            github_available=True,
            session_factory=self.Session,
            now=NOW,
            lease_seconds=30,
        )

        def fail_permanently(_claim, _stop_event):
            raise ValueError("确定性执行错误")

        outcome = research_worker.execute_claimed_research_work(
            claim,
            executor=fail_permanently,
            session_factory=self.Session,
            heartbeat_interval_seconds=1,
            lease_seconds=30,
        )

        self.assertEqual(outcome, "blocked")
        with self.Session() as db:
            work = db.get(ResearchWorkItem, claim.work_item_id)
            orchestration = db.get(ResearchOrchestration, claim.orchestration_id)
            self.assertEqual(work.status, "failed")
            self.assertEqual(work.last_error_kind, "ValueError")
            self.assertEqual(orchestration.state, "blocked")

    def test_executor_transient_failure_schedules_retry(self) -> None:
        approved_issue_graph(self.Session, issue_number=9119)
        claim = research_worker.claim_next_research_work(
            "worker-transient-executor-failure",
            github_available=True,
            session_factory=self.Session,
            now=NOW,
            lease_seconds=30,
        )

        def fail_transiently(_claim, _stop_event):
            raise TimeoutError("临时执行超时")

        outcome = research_worker.execute_claimed_research_work(
            claim,
            executor=fail_transiently,
            session_factory=self.Session,
            heartbeat_interval_seconds=1,
            lease_seconds=30,
        )

        self.assertEqual(outcome, "retrying")
        with self.Session() as db:
            work = db.get(ResearchWorkItem, claim.work_item_id)
            orchestration = db.get(ResearchOrchestration, claim.orchestration_id)
            self.assertEqual(work.status, "queued")
            self.assertEqual(work.last_error_kind, "TimeoutError")
            self.assertEqual(orchestration.state, "queued")

    def test_terminal_success_waits_for_lease_reconciliation_after_heartbeat_failure(
        self,
    ) -> None:
        prepared, _issue, _comments, _ = approved_issue_graph(
            self.Session,
            issue_number=9115,
        )
        claim = research_worker.claim_next_research_work(
            "worker-terminal-heartbeat-race",
            github_available=True,
            session_factory=self.Session,
            now=NOW,
            lease_seconds=30,
        )
        run_id = "20000000-0000-0000-0000-000000001115"

        with TemporaryDirectory() as artifact_root:

            def finish_after_heartbeat_failure(_claim, stop_event):
                self.assertTrue(stop_event.wait(timeout=1))
                with self.Session.begin() as db:
                    db.add(
                        ResearchRun(
                            run_id=run_id,
                            formal_research_id=claim.formal_research_id,
                            orchestration_attempt_id=claim.attempt_id,
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
                            artifact_root=artifact_root,
                            started_at=NOW,
                            heartbeat_at=NOW,
                            finished_at=NOW + timedelta(seconds=1),
                        )
                    )
                return research_worker.ResearchRunResult(
                    run_id=run_id,
                    path=Path(artifact_root),
                    manifest={},
                )

            with patch.object(
                research_worker,
                "heartbeat_research_work",
                side_effect=TimeoutError("收尾心跳失败"),
            ):
                outcome = research_worker.execute_claimed_research_work(
                    claim,
                    executor=finish_after_heartbeat_failure,
                    session_factory=self.Session,
                    heartbeat_interval_seconds=0.01,
                    lease_seconds=30,
                )

        self.assertEqual(outcome, "awaiting_lease_reconciliation")
        with self.Session() as db:
            work = db.get(ResearchWorkItem, claim.work_item_id)
            self.assertEqual(work.status, "running")
            reconcile_at = work.lease_expires_at + timedelta(seconds=1)

        duplicate = research_worker.claim_next_research_work(
            "worker-after-terminal-heartbeat-race",
            github_available=True,
            session_factory=self.Session,
            now=reconcile_at,
            lease_seconds=30,
        )
        self.assertIsNone(duplicate)
        with self.Session() as db:
            work = db.get(ResearchWorkItem, claim.work_item_id)
            formal = db.get(FormalResearch, claim.formal_research_id)
            self.assertEqual(work.status, "succeeded")
            self.assertEqual(work.current_run_id, run_id)
            self.assertEqual(formal.phase, "evaluating")
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchRun)), 1
            )

    def test_expired_new_attempt_does_not_reconcile_previous_terminal_run(self) -> None:
        prepared, _issue, _comments, _ = approved_issue_graph(
            self.Session,
            issue_number=9116,
        )
        first = research_worker.claim_next_research_work(
            "worker-reused-identity",
            github_available=True,
            session_factory=self.Session,
            now=NOW,
            lease_seconds=10,
        )
        old_run_id = "20000000-0000-0000-0000-000000001116"
        with self.Session.begin() as db:
            db.add(
                ResearchRun(
                    run_id=old_run_id,
                    formal_research_id=first.formal_research_id,
                    orchestration_attempt_id=first.attempt_id,
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
                    artifact_root=f"outputs/research-runs/runs/{old_run_id}",
                    started_at=NOW,
                    heartbeat_at=NOW,
                    finished_at=NOW + timedelta(seconds=1),
                )
            )
            work = db.get(ResearchWorkItem, first.work_item_id)
            work.status = "queued"
            work.current_run_id = old_run_id
            work.lease_owner = None
            work.lease_token = None
            work.lease_expires_at = None
            work.next_attempt_at = NOW + timedelta(seconds=1)

        second = research_worker.claim_next_research_work(
            "worker-reused-identity",
            github_available=True,
            session_factory=self.Session,
            now=NOW + timedelta(seconds=2),
            lease_seconds=10,
        )
        self.assertNotEqual(second.attempt_id, first.attempt_id)
        self.assertEqual(
            research_worker.heartbeat_research_work(
                first,
                session_factory=self.Session,
                now=NOW + timedelta(seconds=3),
                lease_seconds=30,
            ),
            "lease_lost",
        )
        recovered = research_worker.claim_next_research_work(
            "worker-reused-identity",
            github_available=True,
            session_factory=self.Session,
            now=NOW + timedelta(seconds=13),
            lease_seconds=30,
        )
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.attempt_count, 3)
        self.assertNotEqual(recovered.attempt_id, first.attempt_id)
        with self.Session() as db:
            work = db.get(ResearchWorkItem, first.work_item_id)
            formal = db.get(FormalResearch, first.formal_research_id)
            self.assertEqual(work.status, "leased")
            self.assertEqual(formal.phase, "approved")

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

    def test_stop_racing_with_completion_blocks_evaluation_but_keeps_run_success(
        self,
    ) -> None:
        prepared, _issue, _comments, _ = approved_issue_graph(
            self.Session, issue_number=914
        )
        claim = research_worker.claim_next_research_work(
            "worker-completion-race",
            github_available=True,
            session_factory=self.Session,
            now=NOW,
            lease_seconds=30,
        )
        self.assertEqual(
            research_worker._mark_work_running(claim, self.Session, NOW), "running"
        )
        run_id = "20000000-0000-0000-0000-000000000014"
        with self.Session.begin() as db:
            db.add(
                ResearchRun(
                    run_id=run_id,
                    formal_research_id=claim.formal_research_id,
                    orchestration_attempt_id=claim.attempt_id,
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
            research_worker.ResearchRunResult(
                run_id=run_id, path=Path("."), manifest={}
            ),
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

@unittest.skipUnless(
    os.getenv("TEST_RESEARCH_WORKER_POSTGRES_URL"),
    "TEST_RESEARCH_WORKER_POSTGRES_URL 未配置，跳过 PostgreSQL 研究 Worker 集成测试",
)
class ResearchWorkerPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        database_url = os.environ["TEST_RESEARCH_WORKER_POSTGRES_URL"]
        parsed = make_url(database_url)
        if (
            parsed.host not in {"127.0.0.1", "localhost"}
            or parsed.database != "quant_worker_test"
        ):
            raise AssertionError(
                "研究 Worker 集成测试只允许本机 quant_worker_test 隔离库"
            )
        cls.engine = create_engine(database_url, pool_pre_ping=True)
        with cls.engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS private_workbench CASCADE"))
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        with cls.engine.connect() as connection:
            command.upgrade(alembic_config(connection), "head")
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls) -> None:
        with cls.engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS private_workbench CASCADE"))
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        cls.engine.dispose()

    def setUp(self) -> None:
        with self.Session.begin() as db:
            table_names = ", ".join(
                f'"{table.name}"' for table in Base.metadata.sorted_tables
            )
            db.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))

    def test_postgres_advisory_lock_keeps_global_concurrency_at_one(self) -> None:
        approved_issue_graph(self.Session, issue_number=920)
        second_payload = valid_plan_payload()
        second_payload["economicHypothesis"] += " 第二个独立研究计划。"
        approved_issue_graph(
            self.Session, issue_number=921, prepared=prepare(second_payload)
        )

        with self.Session() as db:
            work_items = db.scalars(
                select(ResearchWorkItem).order_by(
                    ResearchWorkItem.created_at, ResearchWorkItem.id
                )
            ).all()
            orchestration_id = work_items[0].orchestration_id
        with self.assertRaisesRegex(
            DBAPIError, "research orchestration relation mismatch"
        ):
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE research_orchestrations SET issue_number = -1 WHERE id = :id"
                    ),
                    {"id": orchestration_id},
                )
        with self.assertRaisesRegex(
            DBAPIError, "research orchestration relation mismatch"
        ):
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
                select(func.count())
                .select_from(ResearchWorkItem)
                .where(ResearchWorkItem.status == "leased")
            )
            queued = db.scalar(
                select(func.count())
                .select_from(ResearchWorkItem)
                .where(ResearchWorkItem.status == "queued")
            )
        self.assertEqual(active, 1)
        self.assertEqual(queued, 1)

    def test_expiry_recovery_skips_row_locked_by_live_heartbeat(self) -> None:
        approved_issue_graph(self.Session, issue_number=922)
        claim = research_worker.claim_next_research_work(
            "worker-live-heartbeat",
            github_available=True,
            session_factory=self.Session,
            now=NOW,
            lease_seconds=10,
        )

        with ThreadPoolExecutor(max_workers=1) as executor:
            with self.Session.begin() as heartbeat_db:
                work = heartbeat_db.scalar(
                    select(ResearchWorkItem)
                    .where(ResearchWorkItem.id == claim.work_item_id)
                    .with_for_update()
                )
                work.heartbeat_at = NOW + timedelta(seconds=11)
                work.lease_expires_at = NOW + timedelta(seconds=41)
                contender = executor.submit(
                    research_worker.claim_next_research_work,
                    "worker-expiry-contender",
                    github_available=True,
                    session_factory=self.Session,
                    now=NOW + timedelta(seconds=11),
                    lease_seconds=30,
                )
                self.assertIsNone(contender.result(timeout=2))

        with self.Session() as db:
            work = db.get(ResearchWorkItem, claim.work_item_id)
            self.assertEqual(work.status, "leased")
            self.assertEqual(work.lease_owner, claim.worker_id)
            self.assertEqual(work.lease_expires_at, NOW + timedelta(seconds=41))


if __name__ == "__main__":
    unittest.main()
