from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import gzip
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from hashlib import sha256
import json
import os
import socket
from threading import Barrier, Lock, Thread
import time
import unittest
from unittest.mock import patch

import pandas as pd
from alembic import command
from sqlalchemy import create_engine, func, select
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import uvicorn

from backend.app.database import Base, alembic_config
from backend.app import main
from backend.app.github_research import (
    GitHubIssueClient,
    GitHubResearchError,
    GitHubUnavailableError,
)
from backend.app.historical_publication_issues import (
    resolve_historical_publication_issue,
    validate_historical_publication_issue_snapshot,
)
from backend.app.models import (
    FormalResearch,
    FollowUpResearchProposal,
    FrozenResearchPlan,
    ResearchEvaluation,
    ResearchEvaluationRun,
    ResearchEvidenceRef,
    ResearchEvent,
    ResearchOrchestration,
    ResearchPlanApproval,
    ResearchPublication,
    ResearchPublicationIssueMapping,
    ResearchRun,
    ResearchWorkItem,
    StrategyDefinition,
)
from backend.app.research_publication import (
    EvaluationDraft,
    EvidenceDraft,
    MAX_GITHUB_ISSUE_COMMENT_BYTES,
    PublicationConflictError,
    PublicationError,
    RESEARCH_PASS_REQUIRED_GATES,
    RESEARCH_PASS_REQUIRED_EVIDENCE_KINDS,
    _comparison_chart,
    _mapping_table,
    _read_canonical_nav_series,
    parse_evaluation_contract,
    prepare_research_evaluation,
    publish_research_evaluation,
    publish_existing_research_evaluation,
    publish_next_pending_research_evaluation,
    render_evaluation_report,
    render_evaluation_report_page,
)
from backend.app.research_catalog import (
    get_publication_projection,
    get_strategy_profile,
)
from backend.app.research_analytics import get_publication_analytics
from backend.app.quant_research.manifest import build_result_fingerprint
from backend.app.quant_research.evaluation import build_capacity_evidence
from backend.app.quant_research.runner import (
    run_quant_research,
    validate_research_archive,
)
from backend.app.quant_research.run_config import (
    build_parameter_neighborhood_configs,
    canonical_run_config_sha256,
    canonical_sha256,
)
from backend.app.quant_research.snapshot import (
    SnapshotCapacityPolicy,
    SnapshotIntegrityError,
)
from backend.tests.research_test_support import golden_run_config, seed_golden_database
from scripts.research import register_historical_issue_mapping


class FakeGitHubClient:
    repository = "Jettlin927/Quantitative_trading"

    def __init__(self) -> None:
        self.issues: dict[int, dict] = {}
        self.comments: dict[int, list[dict]] = {}
        self.next_comment_id = 9000
        self.fail_close_once = False
        self.fail_get_once = False

    def add_issue(self, issue_number: int) -> None:
        self.issues[issue_number] = {
            "number": issue_number,
            "state": "open",
            "title": "研究计划：测试",
            "labels": [{"name": "类型:策略研究"}],
        }
        self.comments[issue_number] = []

    def get_issue(self, issue_number: int) -> dict:
        if self.fail_get_once:
            self.fail_get_once = False
            raise GitHubUnavailableError("模拟 Issue 读回暂时不可用")
        return dict(self.issues[issue_number])

    def list_comments(self, issue_number: int) -> list[dict]:
        return [dict(item) for item in self.comments[issue_number]]

    def ensure_comment(
        self,
        issue_number: int,
        body: str,
        existing_comments: list[dict],
        *,
        marker: str,
    ) -> dict:
        existing = next(
            (
                item
                for item in existing_comments
                if marker in str(item.get("body") or "")
            ),
            None,
        )
        if existing is not None:
            if existing["body"] != body:
                raise GitHubResearchError("同一评价标记已存在不同正文")
            return existing
        comment = {"id": self.next_comment_id, "body": body}
        self.next_comment_id += 1
        self.comments[issue_number].append(comment)
        return dict(comment)

    def finalize_issue(self, issue_number: int, issue: dict | None = None) -> dict:
        if self.fail_close_once:
            self.fail_close_once = False
            raise GitHubUnavailableError("模拟关闭 Issue 失败")
        current = issue or self.issues[issue_number]
        labels = {str(item.get("name") or "") for item in current.get("labels", [])}
        labels = {item for item in labels if not item.startswith("研究:")}
        labels.add("研究:已发布")
        self.issues[issue_number]["state"] = "closed"
        self.issues[issue_number]["labels"] = [
            {"name": item} for item in sorted(labels)
        ]
        return dict(self.issues[issue_number])


class LocalReadbackClient:
    def __init__(self, session_factory, artifact_root: Path) -> None:
        self.session_factory = session_factory
        self.artifact_root = artifact_root
        self.override_conclusion: str | None = None
        self.fail_once = False

    def read_publication(self, publication_id: str) -> dict:
        if self.fail_once:
            self.fail_once = False
            raise PublicationError("模拟前端入口暂时不可用")
        with self.session_factory() as db:
            projection = get_publication_projection(db, publication_id)
        payload = projection.model_dump(mode="json")
        if self.override_conclusion is not None:
            payload["conclusion"] = self.override_conclusion
        return payload

    def read_report(self, evaluation_id: str) -> str:
        with self.session_factory() as db:
            return render_evaluation_report_page(
                db, self.artifact_root, evaluation_id
            )

    def read_artifact(self, evaluation_id: str, filename: str) -> bytes:
        return (
            self.artifact_root / "publications" / evaluation_id / filename
        ).read_bytes()


class ResearchPublicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.tempdir = TemporaryDirectory()
        self.artifact_root = Path(self.tempdir.name)
        self.github = FakeGitHubClient()
        self.readback = LocalReadbackClient(self.Session, self.artifact_root)
        self._start_archive_validator()

    def _start_archive_validator(self) -> None:
        validator = patch(
            "backend.app.research_publication.validate_research_archive",
            side_effect=self._validate_test_archive,
        )
        self.validate_archive = validator.start()
        self.addCleanup(validator.stop)

    @staticmethod
    def _validate_test_archive(run_root: Path):
        manifest = json.loads(
            (Path(run_root) / "manifest.json").read_text(encoding="utf-8")
        )
        return manifest, {}

    def tearDown(self) -> None:
        self.engine.dispose()
        self.tempdir.cleanup()

    def run_historical_mapping_cli(self) -> tuple[int, dict]:
        expiring_session = sessionmaker(bind=self.engine)
        output = StringIO()
        with (
            patch.object(
                register_historical_issue_mapping,
                "assert_schema_revision_at_head",
            ),
            patch.object(
                register_historical_issue_mapping.GitHubIssueClient,
                "from_env",
                return_value=self.github,
            ),
            patch.object(
                register_historical_issue_mapping,
                "SessionLocal",
                expiring_session,
            ),
            redirect_stdout(output),
        ):
            status = register_historical_issue_mapping.main(
                [
                    "--strategy-id",
                    "etf_volatility_managed",
                    "--issue-number",
                    "37",
                ]
            )
        return status, json.loads(output.getvalue())

    def test_historical_mapping_cli_reports_created_after_commit(self) -> None:
        formal_id, _run_id, _issue_number = self.seed_research(
            149,
            origin="historical_import",
            include_historical_issue_mapping=False,
        )

        status, payload = self.run_historical_mapping_cli()

        self.assertEqual(status, 0)
        self.assertEqual(
            payload,
            {
                "formalResearchId": formal_id,
                "issueNumber": 37,
                "status": "created",
                "strategyId": "etf_volatility_managed",
            },
        )

    def test_historical_mapping_cli_reports_unchanged_after_commit(self) -> None:
        formal_id, _run_id, _issue_number = self.seed_research(
            149,
            origin="historical_import",
            include_historical_issue_mapping=False,
        )
        first_status, first_payload = self.run_historical_mapping_cli()

        status, payload = self.run_historical_mapping_cli()

        self.assertEqual((first_status, first_payload["status"]), (0, "created"))
        self.assertEqual(status, 0)
        self.assertEqual(
            payload,
            {
                "formalResearchId": formal_id,
                "issueNumber": 37,
                "status": "unchanged",
                "strategyId": "etf_volatility_managed",
            },
        )

    def seed_research(
        self,
        serial: int,
        *,
        run_status: str = "succeeded",
        origin: str = "native",
        plan_oos_start: str | None = None,
        max_trials: int = 1,
        complete_multiple_testing: bool = False,
        sparse_regime_cells: bool = False,
        invalid_walk_forward: bool = False,
        complete_parameter_neighborhood: bool = True,
        complete_capacity: bool = True,
        include_historical_issue_mapping: bool = True,
    ) -> tuple[str, str, int]:
        suffix = f"{serial:012d}"
        historical = origin == "historical_import"
        strategy_id = (
            "etf_volatility_managed"
            if historical
            else f"publication_contract_{serial}"
        )
        plan_id = f"10000000-0000-0000-0000-{suffix}"
        approval_id = f"20000000-0000-0000-0000-{suffix}"
        formal_id = f"30000000-0000-0000-0000-{suffix}"
        orchestration_id = f"40000000-0000-0000-0000-{suffix}"
        run_id = f"50000000-0000-0000-0000-{suffix}"
        issue_number = 37 if historical else 700 + serial
        snapshot_id = None
        run_root = self.artifact_root / "runs" / run_id
        run_root.mkdir(parents=True)
        config = {
            "strategyId": strategy_id,
            "strategyVersion": "1",
            "scope": "etf_time_series",
            "startDate": "2025-01-02",
            "endDate": "2025-12-31",
            "warmupStart": "2024-06-01",
            "benchmark": "000300.SH",
            "featureParameters": {"lookbackPeriod": 20},
            "targetWeightParameters": {},
            "executionPolicy": {
                "signalPrice": "close",
                "executionPrice": "next_trade_open",
            },
            "costModel": {
                "buyRate": "0.00035",
                "sellRate": "0.00085",
                "slippageRate": "0.001",
            },
            "validationPolicy": {
                "mode": "anchored",
                "trainPeriods": 60,
                "testPeriods": 20,
                "stepPeriods": 20,
            },
            "riskPolicy": {
                "mode": "rolling_covariance",
                "lookbackPeriods": 60,
                "minPeriods": 20,
            },
        }
        sample_splits = [
            {
                "role": "train",
                "startDate": "2025-01-02",
                "endDate": "2025-04-30",
            },
            {
                "role": "validation",
                "startDate": "2025-05-01",
                "endDate": "2025-06-30",
            },
            {
                "role": "test_oos",
                "startDate": "2025-07-01",
                "endDate": "2025-12-31",
            },
        ]
        evaluation_policy = {
            "marketRegime": {
                "directionLookbackPeriods": 20,
                "upThreshold": "0.03",
                "downThreshold": "-0.03",
                "volatilityLookbackPeriods": 20,
                "highVolatilityThreshold": "0.20",
            },
            "costStressMultiplier": "2",
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
                "minimumWindowTotalReturn": "0.00",
                "minimumPositiveWindowRate": "0.50",
            },
            "costStress": {
                "minimumStressedTotalReturn": "0.00",
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
                                "path": "featureParameters.lookbackPeriod",
                                "value": 15,
                            }
                        ],
                    },
                    {
                        "id": "upper",
                        "changes": [
                            {
                                "path": "featureParameters.lookbackPeriod",
                                "value": 25,
                            }
                        ],
                    },
                ],
                "maximumAbsoluteOosReturnDifference": "0.05",
                "minimumOosTotalReturn": "-0.05",
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
        config.update(
            {
                "evaluationSampleSplits": sample_splits,
                "evaluationPolicy": evaluation_policy,
                "researchPassPolicy": research_pass_policy,
            }
        )
        metrics = {
            "startDate": "2025-01-02",
            "endDate": "2025-12-31",
            "observations": 3,
            "openTradingDays": 3,
            "rebalanceCount": 1,
            "requestCount": 1,
            "executionCount": 1,
            "blockedCount": 0,
            "independentTradeCount": 1,
            "totalReturn": 0.03,
            "annualizedReturn": 0.03,
            "annualizedVolatility": 0.12,
            "sharpe": 0.25,
            "sortino": 0.31,
            "maxDrawdown": -0.02,
            "maxDrawdownDuration": 1,
            "benchmarkTotalReturn": 0.02,
            "excessTotalReturn": 0.01,
            "trackingError": 0.08,
            "informationRatio": 0.125,
            "averageOneWayTurnover": 0.1,
            "maxOneWayTurnover": 0.2,
            "cumulativeTransactionCostRate": 0.0015,
            "blockedRequestRate": 0.0,
            "partialRequestRate": 0.0,
            "cumulativeBlockedChange": 0.0,
            "maxSingleWeight": 0.6,
            "averageHhi": 0.42,
            "maxHhi": 0.5,
            "endingHhi": 0.5,
            "averageGrossExposure": 0.55,
            "endingGrossExposure": 0.6,
            "averageNetExposure": 0.55,
            "endingNetExposure": 0.6,
            "var95": 0.02,
            "es95": 0.025,
            "yearly": {"2025": {"totalReturn": 0.03}},
            "marketRegimes": {
                "上涨_低波": {"observations": 2, "totalReturn": 0.04},
                "下跌_高波": {"observations": 1, "totalReturn": -0.01},
            },
            "walkForward": {
                "mode": "anchored",
                "oosOnly": True,
                "testObservationCount": 2,
                "windowCount": 1,
                "minimumWindowTotalReturn": 0.02,
                "medianWindowTotalReturn": 0.02,
                "positiveWindowRate": 1.0,
            },
            "parameterNeighborhood": "单一冻结参数；not_applicable",
            "costStress": {"base": 0.03, "double": 0.02},
        }
        regime_cells = {
            name: {
                "status": "available",
                "startDate": "2025-07-01",
                "endDate": "2025-12-31",
                "observations": 2,
                "openTradingDays": 2,
                "rebalanceCount": 1,
                "requestCount": 1,
                "executionCount": 1,
                "blockedCount": 0,
                "independentTradeCount": 1,
                "totalReturn": 0.02,
                "benchmarkTotalReturn": 0.01,
                "activeTotalReturn": 0.01,
                "annualizedVolatility": 0.12,
                "maxDrawdown": -0.02,
                "averageOneWayTurnover": 0.1,
                "cumulativeTransactionCostRate": 0.001,
                "blockedRequestRate": 0.0,
                "averageGrossExposure": 0.55,
                "endingGrossExposure": 0.6,
                "averageNetExposure": 0.55,
                "endingNetExposure": 0.6,
                "averageHhi": 0.42,
                "endingHhi": 0.5,
            }
            for name in ("上涨_低波", "上涨_高波", "下跌_低波", "下跌_高波")
        }
        if sparse_regime_cells:
            regime_cells = {"上涨_低波": regime_cells["上涨_低波"]}
        parameter_returns = {"base": 0.03, "lower": 0.02, "upper": 0.04}
        parameter_configurations = [
            {
                "id": variant_id,
                "changes": next(
                    item["changes"]
                    for item in research_pass_policy["parameterNeighborhood"][
                        "variants"
                    ]
                    if item["id"] == variant_id
                ),
                "configSha256": canonical_run_config_sha256(candidate),
                "totalReturn": parameter_returns[variant_id],
                "maxDrawdown": -0.02,
            }
            for variant_id, candidate in build_parameter_neighborhood_configs(
                config
            )
        ]
        oos_metrics = {
            **metrics,
            "schemaVersion": "research-oos-metrics/v2",
            "status": "complete",
            "sampleRole": "test_oos",
            "sampleStartDate": "2025-07-01",
            "sampleEndDate": "2025-12-31",
            "warmupStartDate": "2024-06-01",
            "sampleSplitSha256": canonical_sha256(sample_splits),
            "evaluationPolicy": evaluation_policy,
            "evaluationPolicySha256": canonical_sha256(evaluation_policy),
            "startDate": "2025-12-31",
            "endDate": "2025-12-31",
            "observations": 8,
            "openTradingDays": 8,
            "rebalanceCount": 4,
            "requestCount": 4,
            "executionCount": 4,
            "blockedCount": 0,
            "independentTradeCount": 4,
            "averageGrossExposure": 0.55,
            "endingGrossExposure": 0.6,
            "averageNetExposure": 0.55,
            "endingNetExposure": 0.6,
            "averageHhi": 0.42,
            "endingHhi": 0.5,
            "yearly": {"2025": {"totalReturn": 0.03}},
            "marketRegimes": {
                "policy": evaluation_policy["marketRegime"],
                "coverage": {
                    "observations": sum(
                        item["observations"] for item in regime_cells.values()
                    ),
                    "directionStates": ["上涨", "下跌"],
                    "volatilityStates": ["高波", "低波"],
                },
                "cells": regime_cells,
            },
            "walkForward": (
                "not_available"
                if invalid_walk_forward
                else {
                    "mode": "anchored",
                    "oosOnly": True,
                    "testObservationCount": 2,
                    "windowCount": 1,
                    "minimumWindowTotalReturn": 0.02,
                    "medianWindowTotalReturn": 0.02,
                    "positiveWindowRate": 1.0,
                }
            ),
            "parameterNeighborhood": (
                {
                    "status": "complete",
                    "policySha256": canonical_sha256(
                        research_pass_policy["parameterNeighborhood"]
                    ),
                    "evaluatedConfigurations": 3,
                    "maximumAllowedAbsoluteOosReturnDifference": "0.05",
                    "minimumAllowedOosTotalReturn": "-0.05",
                    "maximumObservedAbsoluteOosReturnDifference": 0.02,
                    "minimumObservedOosTotalReturn": 0.02,
                    "passed": True,
                    "configurations": parameter_configurations,
                }
                if complete_parameter_neighborhood
                else {
                    "status": "not_available",
                    "policySha256": canonical_sha256(
                        research_pass_policy["parameterNeighborhood"]
                    ),
                    "reason": "未执行参数邻域",
                }
            ),
            "costStress": {
                "multiplier": "2",
                "baseTotalReturn": 0.03,
                "stressedTotalReturn": 0.02,
                "returnDifference": -0.01,
            },
            "capacity": (
                {
                    "status": "complete",
                    "policySha256": canonical_sha256(
                        research_pass_policy["capacity"]
                    ),
                    "expectedCapital": "1000000",
                    "advLookbackPeriods": 20,
                    "minimumAdvObservations": 5,
                    "marketAmountScale": "1000",
                    "maximumAllowedAdvParticipationRate": "0.10",
                    "impactModel": {
                        "type": "linear",
                        "coefficient": "0.10",
                    },
                    "maximumAllowedModeledImpactRate": "0.01",
                    "requestCount": 1,
                    "coveredRequestCount": 1,
                    "medianAdvParticipationRate": 0.001,
                    "p95AdvParticipationRate": 0.001,
                    "maxAdvParticipationRate": 0.001,
                    "maxModeledImpactRate": 0.0001,
                    "passed": True,
                    "observations": [
                        {
                            "executionDate": "2025-07-02",
                            "tsCode": "510300.SH",
                            "advObservations": 20,
                            "requestedChange": 0.5,
                            "advAmount": 500000000.0,
                            "participationRate": 0.001,
                            "modeledImpactRate": 0.0001,
                        }
                    ],
                }
                if complete_capacity
                else {
                    "status": "not_available",
                    "policySha256": canonical_sha256(
                        research_pass_policy["capacity"]
                    ),
                    "reason": "未绑定 ADV 与资金规模",
                }
            ),
            "riskSummary": {
                "status": "complete",
                "observations": 8,
                "averageGrossExposure": 0.55,
                "endingGrossExposure": 0.6,
                "averageNetExposure": 0.55,
                "endingNetExposure": 0.6,
                "averageHhi": 0.42,
                "endingHhi": 0.5,
                "averagePortfolioVolatility": 0.11,
                "endingPortfolioVolatility": 0.12,
                "riskContributionObservations": 6,
                "riskContributionEndDate": "2025-12-31",
                "endingRiskContributions": [
                    {
                        "tsCode": "510300.SH",
                        "closeWeight": 0.6,
                        "totalRiskContribution": 0.12,
                    }
                ],
                "unavailableReason": None,
            },
        }
        if max_trials > 1:
            oos_metrics["dsr"] = (
                {
                    "trialCount": max_trials,
                    "observations": 120,
                    "probability": 0.96,
                }
                if complete_multiple_testing
                else None
            )
            oos_metrics["pbo"] = (
                {
                    "monthlyObservations": 24,
                    "combinations": 10,
                    "probability": 0.2,
                    "trainingWinnerCounts": {"方案一": 6, "方案二": 4},
                }
                if complete_multiple_testing
                else "not_available"
            )
        nav_csv = (
            "trade_date,nav,cash_weight,gross_exposure,one_way_turnover,transaction_cost_rate\n"
            "2025-01-02,0.99,0.4,0.6,0.1,0.0005\n"
            "2025-06-30,0.98,0.5,0.5,0.2,0.0005\n"
            "2025-12-31,1.03,0.4,0.6,0.0,0.0005\n"
        ).encode()
        payloads = {
            "inputs/index_daily_bars.csv.gz": gzip.compress(
                (
                    "ts_code,trade_date,open,close\n"
                    "000300.SH,2025-01-02,100,100\n"
                    "000300.SH,2025-06-30,100,99\n"
                    "000300.SH,2025-12-31,99,102\n"
                ).encode(),
                mtime=0,
            ),
            "targets.csv.gz": gzip.compress(b"signal_date,target\n2025-01-01,1\n", mtime=0),
            "nav.csv.gz": gzip.compress(nav_csv, mtime=0),
            "benchmark_nav.csv.gz": gzip.compress(
                (
                    "trade_date,nav\n"
                    "2025-01-02,1.0\n"
                    "2025-06-30,0.99\n"
                    "2025-12-31,1.02\n"
                ).encode(),
                mtime=0,
            ),
            "metrics.json": (
                json.dumps(metrics, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode(),
            "oos_metrics.json": (
                json.dumps(
                    oos_metrics,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode(),
            "rebalance_requests.csv.gz": gzip.compress(b"request\n1\n", mtime=0),
            "rebalance_executions.csv.gz": gzip.compress(b"execution\n1\n", mtime=0),
            "positions.csv.gz": gzip.compress(b"position\n1\n", mtime=0),
            "risk_exposures.csv.gz": gzip.compress(b"risk\n1\n", mtime=0),
            "risk_contributions.csv.gz": gzip.compress(
                b"contribution\n1\n", mtime=0
            ),
            "walk_forward_windows.csv.gz": gzip.compress(
                (
                    "window_id,mode,train_start,train_end,test_start,test_end,train_periods,test_periods\n"
                    "wf-0001,anchored,2025-01-02,2025-06-30,2025-07-01,2025-12-31,60,20\n"
                ).encode(),
                mtime=0,
            ),
            "walk_forward_metrics.csv.gz": gzip.compress(
                (
                    "window_id,sample_role,start_date,end_date,observations,total_return,max_drawdown\n"
                    "wf-0001,test_oos,2025-07-01,2025-12-31,2,0.02,-0.01\n"
                ).encode(),
                mtime=0,
            ),
        }
        artifact_hashes = {}
        for filename, payload in payloads.items():
            path = run_root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            digest = sha256(payload).hexdigest()
            artifact_hashes[filename] = {
                "filename": filename,
                "contentSha256": digest,
                "fileSha256": digest,
            }
        result_fingerprint = build_result_fingerprint(artifact_hashes)
        (run_root / "manifest.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "runId": run_id,
                    "strategyId": strategy_id,
                    "codeCommit": "c" * 40,
                    "reproducibilityKey": "a" * 64,
                    "configSha256": "d" * 64,
                    "randomSeed": 7,
                    "config": config,
                    "dataSnapshot": {
                        "snapshotId": snapshot_id,
                        "startDate": "2025-01-02",
                        "endDate": "2025-12-31",
                        "warmupStart": "2024-06-01",
                        "scope": "etf_time_series",
                        "benchmark": "000300.SH",
                        "rowCounts": {"nav": 3},
                        "tableArtifacts": {
                            "index_daily_bars": {
                                "filename": "index_daily_bars.csv.gz",
                                "rowCount": 3,
                            }
                        },
                    },
                    "qualityRun": {"id": "quality-test", "status": "ready"},
                    "universe": {"mode": "explicit_snapshot", "members": ["510300.SH"]},
                    "environment": {"sha256": "e" * 64, "timezone": "Asia/Shanghai"},
                    "limitations": ["仅用于发布合同验证"],
                    "boundaries": {"researchOnly": True, "executionEnabled": False},
                    "artifactSchemaVersion": 5,
                    "artifactHashes": artifact_hashes,
                    "resultFingerprint": result_fingerprint,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        with self.Session.begin() as db:
            strategy = StrategyDefinition(
                strategy_id=strategy_id,
                display_name=f"一致发布合同 {serial}",
                lifecycle_status="活跃",
                economic_thesis="仅验证发布合同。",
                registry_version="1",
                code_commit="c" * 40,
                metadata_json={},
            )
            plan_json = {
                "strategyId": strategy_id,
                "strategy": {
                    "id": strategy_id,
                    "version": "1",
                    "displayName": f"一致发布合同 {serial}",
                    "codeCommit": "c" * 40,
                },
                "economicHypothesis": "测试假设仅用于验证发布合同。",
                "gates": ["净成本门禁", "OOS 门禁", "复现身份门禁"],
                "parameterSpace": {"singleRun": ["frozen"]},
                "trialBudget": {"maxTrials": max_trials},
                "runConfig": config,
                "sampleSplits": [dict(item) for item in sample_splits],
                "reportContract": {
                    "evaluationPolicy": evaluation_policy,
                    "researchPassPolicy": research_pass_policy,
                },
            }
            if plan_oos_start is not None:
                plan_json["sampleSplits"][2]["startDate"] = plan_oos_start
            if historical:
                plan_json["runIdentities"] = [
                    {
                        "runId": run_id,
                        "strategyId": strategy_id,
                        "codeCommit": "c" * 40,
                        "reproducibilityKey": "a" * 64,
                        "resultFingerprint": result_fingerprint,
                    }
                ]
            plan = FrozenResearchPlan(
                id=plan_id,
                strategy_id=strategy_id,
                issue_number=issue_number,
                version=1,
                schema_version="research-plan/v3",
                plan_sha256=f"{serial:064x}",
                code_commit="c" * 40,
                plan_json=plan_json,
            )
            approval = ResearchPlanApproval(
                id=approval_id,
                plan_id=plan_id,
                action="historical_import" if historical else "approved",
                actor_login="history-migration-v1" if historical else "Jettlin927",
                comment_id=None if historical else 8000 + serial,
                source_uri="repo://history/summary.json" if historical else None,
                comment_body=(
                    "历史导入：不构成研究批准。"
                    if historical
                    else f"批准研究 {plan.plan_sha256}"
                ),
                plan_sha256=plan.plan_sha256,
            )
            formal = FormalResearch(
                id=formal_id,
                plan_id=plan_id,
                approval_id=approval_id,
                origin=origin,
                phase=(
                    "stopped"
                    if historical
                    else ("evaluating" if run_status == "succeeded" else "stopped")
                ),
            )
            orchestration = ResearchOrchestration(
                id=orchestration_id,
                plan_id=plan_id,
                formal_research_id=None if historical else formal_id,
                issue_number=issue_number,
                state="running" if run_status == "succeeded" else "blocked",
                last_issue_body_sha256="b" * 64,
            )
            run = ResearchRun(
                run_id=run_id,
                formal_research_id=None if historical else formal_id,
                reproducibility_key="a" * 64,
                strategy_id=strategy_id,
                status=run_status,
                stage="finalized",
                config=config,
                config_sha256="d" * 64,
                data_snapshot_id=snapshot_id,
                code_commit="c" * 40,
                environment_sha256="e" * 64,
                random_seed=7,
                metrics=metrics,
                result_fingerprint=result_fingerprint
                if run_status == "succeeded"
                else None,
                artifact_root=str(run_root),
                error="模拟运行失败" if run_status == "failed" else None,
                started_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
                finished_at=datetime(2025, 12, 31, tzinfo=timezone.utc),
            )
            db.add(strategy)
            db.flush()
            db.add(plan)
            db.flush()
            db.add(approval)
            db.flush()
            if historical:
                db.add(run)
                db.flush()
                db.add(formal)
                db.flush()
                run.formal_research_id = formal_id
                db.flush()
                if include_historical_issue_mapping:
                    db.add(
                        ResearchPublicationIssueMapping(
                            formal_research_id=formal_id,
                            issue_number=issue_number,
                        )
                    )
            else:
                db.add(formal)
                db.flush()
                db.add(orchestration)
                db.flush()
                db.add(run)
                db.flush()
                db.add(
                    ResearchWorkItem(
                        id=f"45000000-0000-0000-0000-{suffix}",
                        orchestration_id=orchestration_id,
                        formal_research_id=formal_id,
                        status=run_status,
                        attempt_count=1,
                        max_attempts=3,
                    )
                )
        self.github.add_issue(issue_number)
        if historical:
            self.github.issues[issue_number]["title"] = (
                "历史研究：沪深300 ETF 波动率管理结构化评价发布"
            )
            self.github.issues[issue_number]["labels"].append(
                {"name": "来源:历史导入"}
            )
        return formal_id, run_id, issue_number

    def seed_historical_pending(
        self, serial: int
    ) -> tuple[str, str, str, int]:
        formal_id, run_id, issue_number = self.seed_research(
            serial, origin="historical_import"
        )
        suffix = f"{serial:012d}"
        evaluation_id = f"60000000-0000-0000-0000-{suffix}"
        publication_id = f"70000000-0000-0000-0000-{suffix}"
        with self.Session.begin() as db:
            db.add(
                ResearchEvaluation(
                    id=evaluation_id,
                    formal_research_id=formal_id,
                    version=1,
                    conclusion="不通过",
                    evaluation_sha256="6" * 64,
                    supporting_evidence=[{"statement": "历史报告已冻结"}],
                    opposing_evidence=[{"statement": "门禁未通过"}],
                    missing_evidence=[],
                    limitations=[{"statement": "历史迁移"}],
                    follow_up_recommendations=[],
                )
            )
            db.flush()
            db.add(
                ResearchEvaluationRun(
                    evaluation_id=evaluation_id,
                    run_id=run_id,
                )
            )
            db.add(
                ResearchPublication(
                    id=publication_id,
                    formal_research_id=formal_id,
                    evaluation_id=evaluation_id,
                    version=1,
                    status="pending",
                    publication_sha256="7" * 64,
                    artifact_manifest_uri="artifacts://history/manifest.json",
                    issue_number=issue_number,
                )
            )
        return formal_id, evaluation_id, publication_id, issue_number

    def draft(
        self,
        run_id: str,
        *,
        conclusion: str = "证据不足",
        version_note: str = "v1",
        supersedes_evaluation_id: str | None = None,
    ) -> EvaluationDraft:
        passed = conclusion == "研究通过"
        if passed:
            evidence_specs = [
                (kind, "manifest.json")
                for kind in ("input_snapshot", "code", "environment", "parameters")
            ] + [
                ("ledger", "rebalance_requests.csv.gz"),
                ("ledger", "rebalance_executions.csv.gz"),
                ("ledger", "positions.csv.gz"),
                ("statistics", "metrics.json"),
                ("statistics", "oos_metrics.json"),
                ("statistics", "benchmark_nav.csv.gz"),
                ("statistics", "walk_forward_windows.csv.gz"),
                ("statistics", "walk_forward_metrics.csv.gz"),
                ("statistics", "risk_exposures.csv.gz"),
                ("statistics", "risk_contributions.csv.gz"),
            ]
        else:
            evidence_specs = [("report", "manifest.json")]
        evidence_refs = tuple(
            EvidenceDraft(
                run_id=run_id,
                kind=kind,
                uri=f"artifacts://{run_id}/{filename}",
                sha256=sha256(
                    (self.artifact_root / "runs" / run_id / filename).read_bytes()
                ).hexdigest(),
                metadata={"mediaType": "application/octet-stream"},
            )
            for kind, filename in evidence_specs
        )
        canonical_uris = sorted({item.uri for item in evidence_refs})
        supporting_evidence = [{"statement": f"可复现运行 {version_note}"}]
        if passed:
            supporting_evidence.extend(
                {
                    "gate": gate,
                    "status": "passed",
                    "statement": f"{label}已按事前门槛通过",
                    "evidenceRefs": canonical_uris,
                }
                for gate, label in RESEARCH_PASS_REQUIRED_GATES.items()
            )
            supporting_evidence.extend(
                {
                    "planGate": gate,
                    "status": "passed",
                    "statement": f"冻结计划事前门禁已通过：{gate}",
                    "evidenceRefs": canonical_uris,
                }
                for gate in ("净成本门禁", "OOS 门禁", "复现身份门禁")
            )
        return EvaluationDraft(
            conclusion=conclusion,
            run_ids=(run_id,),
            supporting_evidence=tuple(supporting_evidence),
            opposing_evidence=(
                () if passed else ({"statement": "尚未形成超额收益证据"},)
            ),
            missing_evidence=(() if passed else ({"statement": "缺少更长 OOS"},)),
            limitations=({"statement": "仅用于发布合同验证"},),
            follow_up_recommendations=({"statement": "补充 OOS"},),
            evidence_refs=evidence_refs,
            supersedes_evaluation_id=supersedes_evaluation_id,
        )

    def publish(self, formal_id: str, draft: EvaluationDraft):
        return publish_research_evaluation(
            self.Session,
            self.github,
            formal_research_id=formal_id,
            draft=draft,
            artifact_root=self.artifact_root,
            public_base_url="https://research.example.com",
            readback_client=self.readback,
        )

    def test_native_publication_analytics_preserves_robustness_and_capacity(self) -> None:
        formal_id, run_id, _ = self.seed_research(
            151,
            max_trials=3,
            complete_multiple_testing=True,
        )
        projection = self.publish(formal_id, self.draft(run_id))

        with self.Session() as db:
            analytics = get_publication_analytics(db, str(projection.publication_id))

        self.assertIsNotNone(analytics)
        self.assertEqual(analytics.data_status, "complete")
        self.assertAlmostEqual(analytics.metrics["averageOneWayTurnover"], 0.1)
        self.assertAlmostEqual(analytics.metrics["cumulativeOneWayTurnover"], 0.0)
        self.assertEqual(analytics.robustness["walkForward"]["windowCount"], 1)
        self.assertEqual(
            analytics.robustness["parameterNeighborhood"]["status"], "complete"
        )
        self.assertEqual(analytics.robustness["costStress"]["status"], "complete")
        self.assertAlmostEqual(analytics.robustness["dsr"]["probability"], 0.96)
        self.assertAlmostEqual(analytics.robustness["pbo"]["probability"], 0.2)
        self.assertEqual(analytics.capacity["status"], "complete")
        self.assertAlmostEqual(analytics.capacity["p95AdvParticipationRate"], 0.001)
        self.assertAlmostEqual(analytics.metrics["advParticipationP95"], 0.001)

    def rewrite_oos_and_resign(self, run_id: str, mutate) -> None:
        self.rewrite_json_artifacts_and_resign(
            run_id, {"oos_metrics.json": mutate}
        )

    def rewrite_json_artifacts_and_resign(
        self, run_id: str, mutations
    ) -> None:
        run_root = self.artifact_root / "runs" / run_id
        manifest_path = run_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for filename, mutate in mutations.items():
            path = run_root / filename
            payload = json.loads(path.read_text(encoding="utf-8"))
            mutate(payload)
            path.write_text(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            digest = sha256(path.read_bytes()).hexdigest()
            manifest["artifactHashes"][filename] = {
                "filename": filename,
                "contentSha256": digest,
                "fileSha256": digest,
            }
        fingerprint = build_result_fingerprint(manifest["artifactHashes"])
        manifest["resultFingerprint"] = fingerprint
        manifest_path.write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        with self.Session.begin() as db:
            db.get(ResearchRun, run_id).result_fingerprint = fingerprint

    def test_capacity_allows_zero_turnover_without_accepting_zero_adv(self) -> None:
        _formal_id, run_id, _issue_number = self.seed_research(101)
        with self.Session() as db:
            config = dict(db.get(ResearchRun, run_id).config)
        execution_date = pd.Timestamp("2025-07-08")
        requests = pd.DataFrame(
            [
                {
                    "execution_date": execution_date,
                    "ts_code": "AAA.SH",
                    "requested_change": 0.5,
                }
            ]
        )
        history_dates = pd.bdate_range("2025-07-01", periods=5)
        market_bars = pd.DataFrame(
            [
                {
                    "trade_date": trade_date,
                    "ts_code": "AAA.SH",
                    "amount": amount,
                }
                for trade_date, amount in zip(
                    history_dates,
                    (1_000_000, 0, 1_000_000, 0, 1_000_000),
                    strict=True,
                )
            ]
            + [
                {
                    "trade_date": trade_date,
                    "ts_code": "UNRELATED.SH",
                    "amount": 0,
                }
                for trade_date in history_dates
            ]
        )

        evidence = build_capacity_evidence(
            config,
            requests,
            market_bars,
            dates=pd.DatetimeIndex([execution_date]),
        )
        self.assertEqual(evidence["status"], "complete")
        self.assertEqual(evidence["coveredRequestCount"], 1)

        zero_adv = market_bars.copy()
        zero_adv.loc[zero_adv["ts_code"].eq("AAA.SH"), "amount"] = 0
        unavailable = build_capacity_evidence(
            config,
            requests,
            zero_adv,
            dates=pd.DatetimeIndex([execution_date]),
        )
        self.assertEqual(unavailable["status"], "not_available")
        self.assertIn("ADV为零", unavailable["reason"])

    def test_all_five_conclusions_and_failed_run_audit_can_be_published(self) -> None:
        conclusions = ("研究通过", "有条件候选", "证据不足", "受阻", "不通过")
        for serial, conclusion in enumerate(conclusions, start=1):
            with self.subTest(conclusion=conclusion):
                formal_id, run_id, issue_number = self.seed_research(serial)
                projection = self.publish(
                    formal_id,
                    self.draft(run_id, conclusion=conclusion),
                )
                self.assertEqual(projection.conclusion, conclusion)
                self.assertEqual(projection.status, "published")
                self.assertEqual(self.github.issues[issue_number]["state"], "closed")

        formal_id, run_id, issue_number = self.seed_research(6, run_status="failed")
        projection = self.publish(formal_id, self.draft(run_id, conclusion="受阻"))
        self.assertEqual(projection.conclusion, "受阻")
        self.assertEqual(self.github.issues[issue_number]["state"], "closed")

    def test_real_runner_plan_bound_capacity_and_parameter_neighborhood_can_pass(
        self,
    ) -> None:
        with self.Session() as db:
            quality_run_id, universe_hash = seed_golden_database(db)
        config = golden_run_config(quality_run_id, universe_hash)
        config["targetWeightParameters"]["signalDate"] = "2026-01-16"
        sample_splits = [
            {
                "role": "train",
                "startDate": "2026-01-05",
                "endDate": "2026-01-09",
            },
            {
                "role": "validation",
                "startDate": "2026-01-12",
                "endDate": "2026-01-15",
            },
            {
                "role": "test_oos",
                "startDate": "2026-01-16",
                "endDate": "2026-01-23",
            },
        ]
        evaluation_policy = {
            "marketRegime": {
                "directionLookbackPeriods": 2,
                "upThreshold": "0.004",
                "downThreshold": "-0.004",
                "volatilityLookbackPeriods": 2,
                "highVolatilityThreshold": "0.01",
            },
            "costStressMultiplier": "2",
        }
        research_pass_policy = {
            "oosPerformance": {
                "minimumTotalReturn": "-0.50",
                "minimumExcessTotalReturn": "-0.50",
            },
            "risk": {
                "maximumAbsoluteMaxDrawdown": "1",
                "maximumEs95": "1",
                "maximumMaxSingleWeight": "1",
                "maximumHhi": "1",
            },
            "walkForward": {
                "minimumWindowTotalReturn": "-0.50",
                "minimumPositiveWindowRate": "0",
            },
            "costStress": {
                "minimumStressedTotalReturn": "-0.50",
                "maximumAbsoluteReturnDifference": "1",
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
                                "path": "targetWeightParameters.targetWeight",
                                "value": "0.6",
                            }
                        ],
                    },
                    {
                        "id": "upper",
                        "changes": [
                            {
                                "path": "targetWeightParameters.targetWeight",
                                "value": "0.8",
                            }
                        ],
                    },
                ],
                "maximumAbsoluteOosReturnDifference": "0.20",
                "minimumOosTotalReturn": "-0.20",
            },
            "capacity": {
                "expectedCapital": "10000",
                "advLookbackPeriods": 5,
                "minimumAdvObservations": 3,
                "marketAmountScale": "1000",
                "maximumAdvParticipationRate": "0.10",
                "impactModel": {"type": "linear", "coefficient": "0.10"},
                "maximumModeledImpactRate": "0.01",
            },
        }
        config.update(
            {
                "validationPolicy": {
                    "mode": "anchored",
                    "trainPeriods": 5,
                    "testPeriods": 2,
                    "stepPeriods": 2,
                },
                "riskPolicy": {
                    "mode": "rolling_covariance",
                    "lookbackPeriods": 3,
                    "minPeriods": 2,
                },
                "evaluationSampleSplits": sample_splits,
                "evaluationPolicy": evaluation_policy,
                "researchPassPolicy": research_pass_policy,
            }
        )
        plan_id = "61000000-0000-0000-0000-000000000001"
        approval_id = "62000000-0000-0000-0000-000000000001"
        formal_id = "63000000-0000-0000-0000-000000000001"
        orchestration_id = "64000000-0000-0000-0000-000000000001"
        work_id = "65000000-0000-0000-0000-000000000001"
        issue_number = 1999
        plan_json = {
            "strategy": {
                "id": "sentinel_etf_baseline",
                "version": "1",
                "displayName": "ETF 哨兵基线",
                "codeCommit": "c" * 40,
            },
            "economicHypothesis": "仅用合成数据验证真实 OOS 发布链路。",
            "runConfig": config,
            "sampleSplits": sample_splits,
            "parameterSpace": {"singleRun": ["frozen"]},
            "trialBudget": {"maxTrials": 1},
            "gates": ["净成本门禁", "OOS 门禁", "复现身份门禁"],
            "reportContract": {
                "evaluationPolicy": evaluation_policy,
                "researchPassPolicy": research_pass_policy,
            },
        }
        with self.Session.begin() as db:
            db.add(
                StrategyDefinition(
                    strategy_id="sentinel_etf_baseline",
                    display_name="ETF 哨兵基线",
                    lifecycle_status="活跃",
                    economic_thesis="仅用合成数据验证研究管线。",
                    registry_version="1",
                    code_commit="c" * 40,
                    metadata_json={},
                )
            )
            db.flush()
            db.add(
                FrozenResearchPlan(
                    id=plan_id,
                    strategy_id="sentinel_etf_baseline",
                    issue_number=issue_number,
                    version=1,
                    schema_version="research-plan/v3",
                    plan_sha256=canonical_sha256(plan_json),
                    code_commit="c" * 40,
                    plan_json=plan_json,
                )
            )
            db.flush()
            db.add(
                ResearchPlanApproval(
                    id=approval_id,
                    plan_id=plan_id,
                    action="approved",
                    actor_login="Jettlin927",
                    comment_id=99991,
                    comment_body="批准研究 " + canonical_sha256(plan_json),
                    plan_sha256=canonical_sha256(plan_json),
                )
            )
            db.flush()
            db.add(
                FormalResearch(
                    id=formal_id,
                    plan_id=plan_id,
                    approval_id=approval_id,
                    origin="native",
                    phase="active",
                )
            )
            db.flush()
            db.add(
                ResearchOrchestration(
                    id=orchestration_id,
                    plan_id=plan_id,
                    formal_research_id=formal_id,
                    issue_number=issue_number,
                    state="running",
                    last_issue_body_sha256="b" * 64,
                )
            )
            db.flush()
            db.add(
                ResearchWorkItem(
                    id=work_id,
                    orchestration_id=orchestration_id,
                    formal_research_id=formal_id,
                    status="queued",
                    attempt_count=0,
                    max_attempts=1,
                )
            )
        with self.Session() as db:
            run = run_quant_research(
                db,
                config,
                self.artifact_root,
                code_commit="c" * 40,
                schema_revision="test-schema",
                test_mode=True,
                capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
                formal_research_id=formal_id,
            )
        with self.Session.begin() as db:
            db.get(FormalResearch, formal_id).phase = "evaluating"
            db.get(ResearchWorkItem, work_id).status = "succeeded"
        self.github.add_issue(issue_number)
        evidence_specs = [
            ("input_snapshot", "manifest.json"),
            ("code", "manifest.json"),
            ("environment", "manifest.json"),
            ("parameters", "manifest.json"),
            ("ledger", "rebalance_requests.csv.gz"),
            ("ledger", "rebalance_executions.csv.gz"),
            ("ledger", "positions.csv.gz"),
            ("statistics", "metrics.json"),
            ("statistics", "oos_metrics.json"),
            ("statistics", "benchmark_nav.csv.gz"),
            ("statistics", "walk_forward_windows.csv.gz"),
            ("statistics", "walk_forward_metrics.csv.gz"),
            ("statistics", "risk_exposures.csv.gz"),
            ("statistics", "risk_contributions.csv.gz"),
        ]
        evidence_refs = tuple(
            EvidenceDraft(
                kind=kind,
                uri=f"artifacts://{run.run_id}/{filename}",
                run_id=run.run_id,
                sha256=sha256((run.path / filename).read_bytes()).hexdigest(),
            )
            for kind, filename in evidence_specs
        )
        uris = sorted({item.uri for item in evidence_refs})
        supporting = tuple(
            [
                {
                    "statement": "真实 Runner 已生成冻结 test/OOS 归档。",
                }
            ]
            + [
                {
                    "gate": gate,
                    "status": "passed",
                    "statement": f"{label}已通过合成合同夹具验证。",
                    "evidenceRefs": uris,
                }
                for gate, label in RESEARCH_PASS_REQUIRED_GATES.items()
            ]
            + [
                {
                    "planGate": gate,
                    "status": "passed",
                    "statement": f"冻结计划门禁已通过：{gate}",
                    "evidenceRefs": uris,
                }
                for gate in plan_json["gates"]
            ]
        )
        draft = EvaluationDraft(
            conclusion="研究通过",
            run_ids=(run.run_id,),
            supporting_evidence=supporting,
            limitations=({"statement": "仅验证发布合同，不代表任何真实策略结论。"},),
            follow_up_recommendations=(
                {"statement": "在真实正式研究中沿用同一冻结 OOS 合同。"},
            ),
            evidence_refs=evidence_refs,
        )

        oos_metrics = json.loads((run.path / "oos_metrics.json").read_text())
        self.assertEqual(oos_metrics["capacity"]["status"], "complete")
        self.assertTrue(oos_metrics["capacity"]["passed"])
        self.assertEqual(
            oos_metrics["parameterNeighborhood"]["status"], "complete"
        )
        self.assertTrue(oos_metrics["parameterNeighborhood"]["passed"])
        with patch(
            "backend.app.research_publication.validate_research_archive",
            side_effect=validate_research_archive,
        ):
            projection = self.publish(formal_id, draft)

        self.assertEqual(projection.conclusion, "研究通过")
        self.assertEqual(self.github.issues[issue_number]["state"], "closed")

    def test_each_conclusion_requires_meaningful_minimum_evidence(self) -> None:
        cases = (
            ("有条件候选", {"supporting_evidence": ()}, "支持证据"),
            ("证据不足", {"missing_evidence": ()}, "尚缺证据"),
            ("受阻", {"limitations": ()}, "阻塞或限制事实"),
            ("不通过", {"opposing_evidence": ()}, "反对证据"),
            ("研究通过", {"follow_up_recommendations": ()}, "后续建议"),
        )
        for serial, (conclusion, changes, expected) in enumerate(cases, start=71):
            with self.subTest(conclusion=conclusion):
                formal_id, run_id, _ = self.seed_research(serial)
                draft = replace(
                    self.draft(run_id, conclusion=conclusion),
                    **changes,
                )
                with self.assertRaisesRegex(PublicationConflictError, expected):
                    self.publish(formal_id, draft)

    def test_empty_object_is_not_meaningful_evidence(self) -> None:
        formal_id, run_id, _ = self.seed_research(76)
        draft = replace(
            self.draft(run_id, conclusion="不通过"),
            opposing_evidence=({},),
        )
        with self.assertRaisesRegex(PublicationConflictError, "非空文字事实"):
            self.publish(formal_id, draft)

    def test_succeeded_nonpass_requires_run_bound_canonical_evidence(self) -> None:
        formal_id, run_id, _ = self.seed_research(77)
        draft = replace(
            self.draft(run_id, conclusion="证据不足"),
            evidence_refs=(
                EvidenceDraft(
                    kind="report",
                    uri="https://example.invalid/report.html",
                    sha256="1" * 64,
                ),
            ),
        )
        with self.assertRaisesRegex(PublicationConflictError, "绑定该运行.*canonical"):
            self.publish(formal_id, draft)

    def test_report_charts_share_calendar_axis_and_keep_initial_drawdown(self) -> None:
        chart = _comparison_chart(
            [
                {"date": "2025-01-01", "value": 1.0},
                {"date": "2025-01-03", "value": 1.1},
            ],
            [
                {"date": "2025-01-02", "value": 1.0},
                {"date": "2025-01-03", "value": 1.05},
            ],
        )
        self.assertIn('class="benchmark" points="380.00,', chart)
        run_root = self.artifact_root / "chart-test"
        run_root.mkdir()
        path = run_root / "nav.csv.gz"
        path.write_bytes(
            gzip.compress(
                (
                    "trade_date,nav,cash_weight,gross_exposure,one_way_turnover,transaction_cost_rate\n"
                    "2025-01-02,0.99,0.4,0.6,0.1,0.0005\n"
                    "2025-06-30,0.98,0.5,0.5,0.2,0.0005\n"
                    "2025-12-31,1.03,0.4,0.6,0.0,0.0005\n"
                ).encode(),
                mtime=0,
            )
        )
        series = _read_canonical_nav_series(path)
        self.assertAlmostEqual(series["drawdown"][0]["value"], -0.01)
        oos_series = _read_canonical_nav_series(
            path,
            start_date="2025-06-30",
            end_date="2025-12-31",
        )
        self.assertAlmostEqual(oos_series["nav"][0]["value"], 0.98 / 0.99)
        self.assertAlmostEqual(
            oos_series["cumulativeCost"][0]["value"], 0.0005
        )

    def test_user_visible_metric_table_has_chinese_explanations(self) -> None:
        table = _mapping_table(
            {"annualizedVolatility": 0.12, "customMetric": 1}
        )
        self.assertIn("年化波动率（annualizedVolatility）", table)
        self.assertIn("原始指标（customMetric）", table)

    def test_report_run_audit_lists_complete_reproducibility_identity(self) -> None:
        formal_id, run_id, _ = self.seed_research(79)
        projection = self.publish(formal_id, self.draft(run_id))
        with self.Session() as db:
            report = render_evaluation_report(
                db,
                self.artifact_root,
                str(projection.evaluation_id),
            )
        audit = report.split("10. 复现身份与失败审计", 1)[1]
        for field in (
            "runId",
            "reproducibilityKey",
            "configSha256",
            "dataSnapshotId",
            "codeCommit",
            "environmentSha256",
            "randomSeed",
            "manifestSha256",
            "resultFingerprint",
        ):
            self.assertIn(field, audit)
        self.assertIn("wf-0001", report)
        self.assertIn("test_start", report)

    def test_failed_run_cannot_be_promoted_to_research_passed(self) -> None:
        formal_id, run_id, _ = self.seed_research(10, run_status="failed")
        with self.assertRaisesRegex(PublicationConflictError, "至少包含一个成功运行"):
            self.publish(formal_id, self.draft(run_id, conclusion="研究通过"))
        with self.Session() as db:
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchEvaluation)), 0
            )
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchPublication)), 0
            )

    def test_research_passed_rejects_declared_missing_evidence(self) -> None:
        formal_id, run_id, _ = self.seed_research(11)
        draft = replace(
            self.draft(run_id, conclusion="研究通过"),
            missing_evidence=({"statement": "缺少匹配基准"},),
        )

        with self.assertRaisesRegex(
            PublicationConflictError, "研究通过不得携带尚缺证据"
        ):
            self.publish(formal_id, draft)

    def test_research_passed_requires_all_explicit_hard_gates(self) -> None:
        formal_id, run_id, _ = self.seed_research(13)
        draft = self.draft(run_id, conclusion="研究通过")
        draft = replace(
            draft,
            supporting_evidence=tuple(
                item
                for item in draft.supporting_evidence
                if item.get("gate") != "matched_benchmark"
            ),
        )

        with self.assertRaisesRegex(PublicationConflictError, "缺少硬门禁.*匹配基准"):
            self.publish(formal_id, draft)

    def test_research_passed_rejects_oos_boundary_different_from_frozen_plan(
        self,
    ) -> None:
        formal_id, run_id, _ = self.seed_research(
            78, plan_oos_start="2025-08-01"
        )

        with self.assertRaisesRegex(
            PublicationConflictError, "OOS 边界、评价策略或研究通过策略与冻结计划不一致"
        ):
            self.publish(
                formal_id, self.draft(run_id, conclusion="研究通过")
            )

    def test_research_passed_derives_regime_coverage_from_actual_cells(self) -> None:
        formal_id, run_id, _ = self.seed_research(
            90, sparse_regime_cells=True
        )

        with self.assertRaisesRegex(
            PublicationConflictError, "实际市场环境单元未覆盖"
        ):
            self.publish(formal_id, self.draft(run_id, conclusion="研究通过"))

    def test_research_passed_rejects_unavailable_parameter_and_capacity_evidence(
        self,
    ) -> None:
        parameter_formal_id, parameter_run_id, _ = self.seed_research(
            93, complete_parameter_neighborhood=False
        )
        with self.assertRaisesRegex(
            PublicationConflictError, "canonical 参数邻域证据"
        ):
            self.publish(
                parameter_formal_id,
                self.draft(parameter_run_id, conclusion="研究通过"),
            )

        capacity_formal_id, capacity_run_id, _ = self.seed_research(
            94, complete_capacity=False
        )
        with self.assertRaisesRegex(
            PublicationConflictError, "预期资金规模、ADV 参与率与冲击模型"
        ):
            self.publish(
                capacity_formal_id,
                self.draft(capacity_run_id, conclusion="研究通过"),
            )

    def test_research_passed_rejects_plan_identity_and_risk_summary_tampering(
        self,
    ) -> None:
        parameter_formal_id, parameter_run_id, _ = self.seed_research(95)
        self.rewrite_oos_and_resign(
            parameter_run_id,
            lambda metrics: metrics["parameterNeighborhood"][
                "configurations"
            ][0].update({"configSha256": "0" * 64}),
        )
        with self.assertRaisesRegex(
            PublicationConflictError, "参数邻域配置身份与冻结计划不一致"
        ):
            self.publish(
                parameter_formal_id,
                self.draft(parameter_run_id, conclusion="研究通过"),
            )

        capacity_formal_id, capacity_run_id, _ = self.seed_research(96)
        self.rewrite_oos_and_resign(
            capacity_run_id,
            lambda metrics: metrics["capacity"].update(
                {"expectedCapital": "2000000"}
            ),
        )
        with self.assertRaisesRegex(
            PublicationConflictError, "容量合同与冻结计划不一致"
        ):
            self.publish(
                capacity_formal_id,
                self.draft(capacity_run_id, conclusion="研究通过"),
            )

        risk_formal_id, risk_run_id, _ = self.seed_research(97)
        self.rewrite_oos_and_resign(
            risk_run_id,
            lambda metrics: metrics["riskSummary"].update(
                {
                    "riskContributionObservations": 0,
                    "endingRiskContributions": [],
                }
            ),
        )
        with self.assertRaisesRegex(
            PublicationConflictError, "缺少可用总风险贡献"
        ):
            self.publish(
                risk_formal_id,
                self.draft(risk_run_id, conclusion="研究通过"),
            )

    def test_research_passed_enforces_preregistered_core_thresholds(self) -> None:
        cases = (
            (
                102,
                "冻结 OOS 收益门槛",
                {"oos_metrics.json": lambda metrics: metrics.update(
                    {
                        "totalReturn": -0.90,
                        "benchmarkTotalReturn": 0.20,
                        "excessTotalReturn": -1.10,
                    }
                )},
                1,
                False,
            ),
            (
                103,
                "冻结风险门槛",
                {"oos_metrics.json": lambda metrics: metrics.update(
                    {"maxDrawdown": -0.95}
                )},
                1,
                False,
            ),
            (
                104,
                "冻结成本压力门槛",
                {"oos_metrics.json": lambda metrics: metrics[
                    "costStress"
                ].update(
                    {
                        "stressedTotalReturn": -0.99,
                        "returnDifference": -1.02,
                    }
                )},
                1,
                False,
            ),
            (
                105,
                "冻结 walk-forward 门槛",
                {
                    filename: lambda metrics: metrics["walkForward"].update(
                        {
                            "minimumWindowTotalReturn": -0.99,
                            "positiveWindowRate": 0.0,
                        }
                    )
                    for filename in ("metrics.json", "oos_metrics.json")
                },
                1,
                False,
            ),
            (
                106,
                "冻结 DSR/PBO 门槛.*",
                {"oos_metrics.json": lambda metrics: metrics["dsr"].update(
                    {"probability": 0.10}
                )},
                2,
                True,
            ),
            (
                107,
                "冻结 DSR/PBO 门槛.*",
                {"oos_metrics.json": lambda metrics: metrics["pbo"].update(
                    {"probability": 0.90}
                )},
                2,
                True,
            ),
        )
        for serial, message, mutations, max_trials, complete_multiple in cases:
            with self.subTest(message=message):
                formal_id, run_id, _issue_number = self.seed_research(
                    serial,
                    max_trials=max_trials,
                    complete_multiple_testing=complete_multiple,
                )
                self.rewrite_json_artifacts_and_resign(
                    run_id, mutations
                )
                with self.assertRaisesRegex(PublicationConflictError, message):
                    self.publish(
                        formal_id,
                        self.draft(run_id, conclusion="研究通过"),
                    )

    def test_research_passed_rejects_missing_regime_execution_facts(self) -> None:
        formal_id, run_id, _ = self.seed_research(98)

        def remove_execution_count(metrics):
            del metrics["marketRegimes"]["cells"]["上涨_低波"][
                "executionCount"
            ]

        self.rewrite_oos_and_resign(run_id, remove_execution_count)
        with self.assertRaisesRegex(
            PublicationConflictError, "市场环境单元缺少指标.*executionCount"
        ):
            self.publish(formal_id, self.draft(run_id, conclusion="研究通过"))

    def test_report_natively_displays_execution_and_risk_summary(self) -> None:
        formal_id, run_id, _ = self.seed_research(99)
        projection = self.publish(
            formal_id, self.draft(run_id, conclusion="研究通过")
        )
        with self.Session() as db:
            report = render_evaluation_report(
                db,
                self.artifact_root,
                str(projection.evaluation_id),
            )
        self.assertIn("warmupStartDate", report)
        self.assertIn("executionCount", report)
        self.assertIn("endingRiskContributions", report)
        self.assertIn("成交请求数（executionCount）", report)
        self.assertIn("组合风险与总风险贡献（riskSummary）", report)

    def test_research_passed_requires_structured_walk_forward_and_csv_evidence(
        self,
    ) -> None:
        formal_id, run_id, _ = self.seed_research(
            91, invalid_walk_forward=True
        )
        with self.assertRaisesRegex(
            PublicationConflictError, "结构化 walk-forward 证据"
        ):
            self.publish(formal_id, self.draft(run_id, conclusion="研究通过"))

        evidence_formal_id, evidence_run_id, _ = self.seed_research(92)
        draft = self.draft(evidence_run_id, conclusion="研究通过")
        refs = tuple(
            item
            for item in draft.evidence_refs
            if not item.uri.endswith(
                ("walk_forward_windows.csv.gz", "walk_forward_metrics.csv.gz")
            )
        )
        declared_uris = sorted({item.uri for item in refs})
        draft = replace(
            draft,
            evidence_refs=refs,
            supporting_evidence=tuple(
                (
                    {**item, "evidenceRefs": declared_uris}
                    if item.get("gate") or item.get("planGate")
                    else item
                )
                for item in draft.supporting_evidence
            ),
        )
        with self.assertRaisesRegex(
            PublicationConflictError, "缺少 canonical 工件.*walk_forward"
        ):
            self.publish(evidence_formal_id, draft)

    def test_research_passed_requires_every_frozen_plan_gate(self) -> None:
        formal_id, run_id, _ = self.seed_research(68)
        draft = self.draft(run_id, conclusion="研究通过")
        draft = replace(
            draft,
            supporting_evidence=tuple(
                item
                for item in draft.supporting_evidence
                if item.get("planGate") != "OOS 门禁"
            ),
        )

        with self.assertRaisesRegex(
            PublicationConflictError, "缺少冻结计划事前门禁.*OOS 门禁"
        ):
            self.publish(formal_id, draft)

    def test_multiple_trial_pass_requires_structured_numeric_dsr_and_pbo(
        self,
    ) -> None:
        formal_id, run_id, _ = self.seed_research(86, max_trials=2)
        with self.assertRaisesRegex(
            PublicationConflictError, "结构化 DSR 与 PBO"
        ):
            self.publish(formal_id, self.draft(run_id, conclusion="研究通过"))

        valid_formal_id, valid_run_id, issue_number = self.seed_research(
            87,
            max_trials=2,
            complete_multiple_testing=True,
        )
        projection = self.publish(
            valid_formal_id,
            self.draft(valid_run_id, conclusion="研究通过"),
        )
        self.assertEqual(projection.conclusion, "研究通过")
        self.assertEqual(self.github.issues[issue_number]["state"], "closed")

    def test_research_passed_rejects_unverifiable_external_evidence(self) -> None:
        formal_id, run_id, _ = self.seed_research(57)
        draft = self.draft(run_id, conclusion="研究通过")
        external_refs = tuple(
            replace(
                item,
                uri=f"repo://claimed-evidence/{index}",
                run_id=None,
                sha256="1" * 64,
            )
            for index, item in enumerate(draft.evidence_refs)
        )
        external_uris = [item.uri for item in external_refs]
        draft = replace(
            draft,
            evidence_refs=external_refs,
            supporting_evidence=tuple(
                ({**item, "evidenceRefs": external_uris} if item.get("gate") else item)
                for item in draft.supporting_evidence
            ),
        )

        with self.assertRaisesRegex(
            PublicationConflictError, "只接受可校验的 canonical 工件证据"
        ):
            self.publish(formal_id, draft)

    def test_research_passed_rejects_kind_labels_without_required_artifacts(
        self,
    ) -> None:
        formal_id, run_id, _ = self.seed_research(58)
        draft = self.draft(run_id, conclusion="研究通过")
        manifest_uri = f"artifacts://{run_id}/manifest.json"
        manifest_sha256 = sha256(
            (self.artifact_root / "runs" / run_id / "manifest.json").read_bytes()
        ).hexdigest()
        draft = replace(
            draft,
            evidence_refs=tuple(
                EvidenceDraft(
                    kind=kind,
                    uri=manifest_uri,
                    run_id=run_id,
                    sha256=manifest_sha256,
                )
                for kind in sorted(RESEARCH_PASS_REQUIRED_EVIDENCE_KINDS)
            ),
            supporting_evidence=tuple(
                ({**item, "evidenceRefs": [manifest_uri]} if item.get("gate") else item)
                for item in draft.supporting_evidence
            ),
        )

        with self.assertRaisesRegex(PublicationConflictError, "缺少 canonical 工件"):
            self.publish(formal_id, draft)

    def test_research_passed_keeps_failed_attempt_audit_when_success_exists(
        self,
    ) -> None:
        formal_id, run_id, issue_number = self.seed_research(14)
        failed_run_id = "50000000-0000-0000-0000-000000000114"
        with self.Session.begin() as db:
            source = db.get(ResearchRun, run_id)
            db.add(
                ResearchRun(
                    run_id=failed_run_id,
                    formal_research_id=formal_id,
                    reproducibility_key="b" * 64,
                    strategy_id=source.strategy_id,
                    status="failed",
                    stage="finalized",
                    config={},
                    config_sha256="d" * 64,
                    data_snapshot_id=None,
                    code_commit="c" * 40,
                    environment_sha256="e" * 64,
                    random_seed=7,
                    metrics={},
                    artifact_root=str(self.artifact_root / "runs" / failed_run_id),
                    error="模拟前序失败尝试",
                )
            )

        draft = replace(
            self.draft(run_id, conclusion="研究通过"),
            run_ids=tuple(sorted((run_id, failed_run_id))),
        )
        projection = self.publish(formal_id, draft)

        self.assertEqual(projection.conclusion, "研究通过")
        self.assertEqual(self.github.issues[issue_number]["state"], "closed")
        self.assertEqual(
            [(item.run_id, item.status) for item in projection.runs],
            [(run_id, "succeeded"), (failed_run_id, "failed")],
        )

    def test_research_passed_gates_cannot_use_failed_run_artifacts(self) -> None:
        formal_id, succeeded_run_id, _ = self.seed_research(62)
        failed_run_id = "50000000-0000-0000-0000-000000000162"
        failed_root = self.artifact_root / "runs" / failed_run_id
        failed_root.mkdir(parents=True)
        artifact_hashes = {}
        for filename in (
            "metrics.json",
            "rebalance_requests.csv.gz",
            "rebalance_executions.csv.gz",
            "positions.csv.gz",
        ):
            payload = f"failed:{failed_run_id}:{filename}\n".encode()
            (failed_root / filename).write_bytes(payload)
            digest = sha256(payload).hexdigest()
            artifact_hashes[filename] = {
                "filename": filename,
                "contentSha256": digest,
                "fileSha256": digest,
            }
        (failed_root / "manifest.json").write_text(
            json.dumps(
                {"runId": failed_run_id, "artifactHashes": artifact_hashes},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        with self.Session.begin() as db:
            source = db.get(ResearchRun, succeeded_run_id)
            db.add(
                ResearchRun(
                    run_id=failed_run_id,
                    formal_research_id=formal_id,
                    reproducibility_key="b" * 64,
                    strategy_id=source.strategy_id,
                    status="failed",
                    stage="finalized",
                    config={},
                    config_sha256="d" * 64,
                    data_snapshot_id=None,
                    code_commit="c" * 40,
                    environment_sha256="e" * 64,
                    random_seed=7,
                    metrics={},
                    artifact_root=str(failed_root),
                    error="模拟前序失败尝试",
                )
            )

        evidence_specs = [
            (kind, "manifest.json")
            for kind in ("input_snapshot", "code", "environment", "parameters")
        ] + [
            ("ledger", "rebalance_requests.csv.gz"),
            ("ledger", "rebalance_executions.csv.gz"),
            ("ledger", "positions.csv.gz"),
            ("statistics", "metrics.json"),
        ]
        failed_refs = tuple(
            EvidenceDraft(
                kind=kind,
                uri=f"artifacts://{failed_run_id}/{filename}",
                run_id=failed_run_id,
                sha256=sha256((failed_root / filename).read_bytes()).hexdigest(),
            )
            for kind, filename in evidence_specs
        )
        failed_uris = sorted({item.uri for item in failed_refs})
        draft = self.draft(succeeded_run_id, conclusion="研究通过")
        draft = replace(
            draft,
            run_ids=tuple(sorted((succeeded_run_id, failed_run_id))),
            evidence_refs=failed_refs,
            supporting_evidence=tuple(
                (
                    {**item, "evidenceRefs": failed_uris}
                    if item.get("gate")
                    else item
                )
                for item in draft.supporting_evidence
            ),
        )

        with self.assertRaisesRegex(PublicationConflictError, "证据必须来自成功运行"):
            self.publish(formal_id, draft)

    def test_canonical_evidence_sha_must_match_actual_artifact(self) -> None:
        formal_id, run_id, _ = self.seed_research(15)
        draft = self.draft(run_id)
        draft = replace(
            draft,
            evidence_refs=(replace(draft.evidence_refs[0], sha256="1" * 64),),
        )

        with self.assertRaisesRegex(
            PublicationConflictError, "SHA-256 与 canonical 工件不一致"
        ):
            self.publish(formal_id, draft)

    def test_artifact_evidence_cannot_omit_or_forge_run_binding(self) -> None:
        formal_id, run_id, _ = self.seed_research(18)
        draft = replace(
            self.draft(run_id),
            evidence_refs=(
                EvidenceDraft(
                    kind="report",
                    uri="artifacts://unknown-run/manifest.json",
                    run_id=None,
                    sha256="1" * 64,
                ),
            ),
        )

        with self.assertRaisesRegex(
            PublicationConflictError, "canonical 证据 URI 与声明运行不一致"
        ):
            self.publish(formal_id, draft)

    def test_formal_research_and_work_item_must_be_terminal_for_evaluation(
        self,
    ) -> None:
        formal_id, run_id, _ = self.seed_research(16)
        with self.Session.begin() as db:
            db.get(FormalResearch, formal_id).phase = "active"
        with self.assertRaisesRegex(PublicationConflictError, "阶段 active"):
            self.publish(formal_id, self.draft(run_id))

        formal_id, run_id, _ = self.seed_research(17)
        with self.Session.begin() as db:
            work_item = db.scalar(
                select(ResearchWorkItem).where(
                    ResearchWorkItem.formal_research_id == formal_id
                )
            )
            work_item.status = "queued"
        with self.assertRaisesRegex(PublicationConflictError, "工作项状态 queued"):
            self.publish(formal_id, self.draft(run_id))

        formal_id, run_id, _ = self.seed_research(19)
        with self.Session.begin() as db:
            work_item = db.scalar(
                select(ResearchWorkItem).where(
                    ResearchWorkItem.formal_research_id == formal_id
                )
            )
            db.delete(work_item)
        with self.assertRaisesRegex(PublicationConflictError, "缺少研究工作项"):
            self.publish(formal_id, self.draft(run_id))

    def test_evaluation_must_include_every_terminal_run(self) -> None:
        formal_id, run_id, _ = self.seed_research(12)
        omitted_run_id = "50000000-0000-0000-0000-000000000112"
        with self.Session.begin() as db:
            source = db.get(ResearchRun, run_id)
            db.add(
                ResearchRun(
                    run_id=omitted_run_id,
                    formal_research_id=formal_id,
                    reproducibility_key="b" * 64,
                    strategy_id=source.strategy_id,
                    status="failed",
                    stage="finalized",
                    config={},
                    config_sha256="d" * 64,
                    data_snapshot_id=None,
                    code_commit="c" * 40,
                    environment_sha256="e" * 64,
                    random_seed=7,
                    metrics={},
                    artifact_root=str(self.artifact_root / "runs" / omitted_run_id),
                    error="模拟前序失败尝试",
                )
            )

        with self.assertRaisesRegex(PublicationConflictError, "完整包含.*全部运行"):
            self.publish(formal_id, self.draft(run_id))

    def test_partial_failure_retries_without_duplicate_evaluation_comment_or_artifact(
        self,
    ) -> None:
        formal_id, run_id, issue_number = self.seed_research(20)
        draft = self.draft(run_id)
        self.github.fail_close_once = True

        with self.assertRaisesRegex(PublicationError, "模拟关闭 Issue 失败"):
            self.publish(formal_id, draft)

        with self.Session() as db:
            first = db.scalar(select(ResearchPublication))
            self.assertEqual(first.status, "published")
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchEvaluation)), 1
            )
            self.assertEqual(db.get(FormalResearch, formal_id).phase, "evaluating")
            orchestration = db.scalar(
                select(ResearchOrchestration).where(
                    ResearchOrchestration.formal_research_id == formal_id
                )
            )
            self.assertEqual(orchestration.state, "blocked")
            failure = db.scalar(
                select(ResearchEvent).where(
                    ResearchEvent.event_type == "research_publication_failed"
                )
            )
            self.assertIs(failure.payload_json["retryable"], True)
        self.assertEqual(len(self.github.comments[issue_number]), 1)
        self.assertEqual(self.github.issues[issue_number]["state"], "open")
        self.assertNotIn(
            "研究:已发布",
            {item["name"] for item in self.github.issues[issue_number]["labels"]},
        )
        with self.Session() as db:
            publication = db.scalar(select(ResearchPublication))
            formal = db.get(FormalResearch, formal_id)
            orchestration = db.scalar(
                select(ResearchOrchestration).where(
                    ResearchOrchestration.formal_research_id == formal_id
                )
            )
            failure = db.scalar(
                select(ResearchEvent).where(
                    ResearchEvent.event_type == "research_publication_failed"
                )
            )
        self.assertEqual(publication.status, "published")
        self.assertEqual(formal.phase, "evaluating")
        self.assertEqual(orchestration.state, "blocked")
        self.assertIs(failure.payload_json["retryable"], True)
        frozen_report = next(
            self.artifact_root.glob("publications/*/report.html")
        ).read_bytes()
        frozen_comment = self.github.comments[issue_number][0]["body"]
        with self.Session.begin() as db:
            strategy = db.get(StrategyDefinition, db.get(ResearchRun, run_id).strategy_id)
            strategy.display_name = "发布失败后被修改的可变名称"
            strategy.economic_thesis = "发布失败后被修改的可变假设。"
            strategy.lifecycle_status = "已归档"

        projection = publish_next_pending_research_evaluation(
            self.Session,
            self.github,
            artifact_root=self.artifact_root,
            public_base_url="https://research.example.com",
            readback_client=self.readback,
            retry_failed_after_seconds=0,
        )

        with self.Session() as db:
            publications = db.scalars(
                select(ResearchPublication).order_by(ResearchPublication.version)
            ).all()
            self.assertEqual([item.status for item in publications], ["published"])
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchEvaluation)), 1
            )
            self.assertEqual(db.get(FormalResearch, formal_id).phase, "published")
            lifecycle_events = db.scalars(
                select(ResearchEvent)
                .where(
                    ResearchEvent.event_type.in_(
                        {
                            "research_published",
                            "research_publication_failed",
                            "research_publication_recovered",
                        }
                    )
                )
                .order_by(ResearchEvent.sequence_no)
            ).all()
            self.assertEqual(
                [item.event_type for item in lifecycle_events],
                [
                    "research_published",
                    "research_publication_failed",
                    "research_publication_recovered",
                ],
            )
        self.assertEqual(len(self.github.comments[issue_number]), 1)
        self.assertEqual(self.github.comments[issue_number][0]["body"], frozen_comment)
        self.assertEqual(
            next(self.artifact_root.glob("publications/*/report.html")).read_bytes(),
            frozen_report,
        )
        self.assertEqual(projection.status, "published")
        self.assertEqual(len(list(self.artifact_root.rglob("report.html"))), 1)
        self.assertEqual(len(list(self.artifact_root.rglob("summary.json"))), 1)
        self.assertEqual(len(list(self.artifact_root.rglob("manifest.json"))), 2)

        repeated = self.publish(formal_id, draft)
        self.assertEqual(repeated.publication_id, projection.publication_id)
        self.assertEqual(len(self.github.comments[issue_number]), 1)
        with self.Session() as db:
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchPublication)), 1
            )

    def test_core_state_failure_keeps_github_issue_open(self) -> None:
        formal_id, run_id, issue_number = self.seed_research(21)
        with patch(
            "backend.app.research_publication._finalize_research_state",
            side_effect=PublicationError("模拟核心状态提交失败"),
        ):
            with self.assertRaisesRegex(PublicationError, "模拟核心状态提交失败"):
                self.publish(formal_id, self.draft(run_id))

        self.assertEqual(self.github.issues[issue_number]["state"], "open")
        self.assertNotIn(
            "研究:已发布",
            {item["name"] for item in self.github.issues[issue_number]["labels"]},
        )

    def test_failed_run_without_manifest_keeps_reason_and_timeline_audit(self) -> None:
        formal_id, run_id, _ = self.seed_research(67, run_status="failed")
        draft = replace(self.draft(run_id, conclusion="受阻"), evidence_refs=())
        (self.artifact_root / "runs" / run_id / "manifest.json").unlink()

        projection = self.publish(formal_id, draft)

        raw_report = (
            self.artifact_root
            / "publications"
            / str(projection.evaluation_id)
            / "report.html"
        ).read_text(encoding="utf-8")
        self.assertIn("模拟运行失败", raw_report)
        self.assertIn("2025-01-02", raw_report)
        self.assertIn("2025-12-31", raw_report)
        self.assertIn("failed", raw_report)

    def test_failed_run_with_truncated_manifest_still_publishes_db_audit(self) -> None:
        formal_id, run_id, _ = self.seed_research(78, run_status="failed")
        draft = replace(
            self.draft(run_id, conclusion="受阻"),
            evidence_refs=(),
        )
        (self.artifact_root / "runs" / run_id / "manifest.json").write_text(
            "{\"runId\":",
            encoding="utf-8",
        )

        projection = self.publish(formal_id, draft)

        summary = json.loads(
            (
                self.artifact_root
                / "publications"
                / str(projection.evaluation_id)
                / "summary.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(summary["runs"][0]["manifestSha256"], None)
        self.assertIn("部分工件不作为可信证据", summary["runs"][0]["manifestAudit"])

    def test_terminal_run_audit_mutation_invalidates_frozen_evaluation(self) -> None:
        formal_id, run_id, _ = self.seed_research(79, run_status="failed")
        draft = replace(
            self.draft(run_id, conclusion="受阻"),
            evidence_refs=(),
        )
        pending = prepare_research_evaluation(
            self.Session,
            formal_research_id=formal_id,
            draft=draft,
        )
        with self.Session.begin() as db:
            db.get(ResearchRun, run_id).error = "冻结后被改写的失败原因"

        with self.assertRaisesRegex(PublicationConflictError, "既有评价指纹"):
            publish_existing_research_evaluation(
                self.Session,
                self.github,
                evaluation_id=str(pending.evaluation_id),
                artifact_root=self.artifact_root,
                public_base_url="https://research.example.com",
                readback_client=self.readback,
            )
        self.assertFalse(
            (self.artifact_root / "publications" / str(pending.evaluation_id)).exists()
        )

    def test_crash_tail_issue_probe_failure_is_compensated_on_explicit_replay(
        self,
    ) -> None:
        formal_id, run_id, issue_number = self.seed_research(64)
        draft = self.draft(run_id)
        self.publish(formal_id, draft)
        self.github.issues[issue_number]["state"] = "open"
        self.github.issues[issue_number]["labels"] = [
            {"name": "类型:策略研究"}
        ]
        self.github.fail_get_once = True

        with self.assertRaisesRegex(PublicationError, "Issue 读回暂时不可用"):
            self.publish(formal_id, draft)

        with self.Session() as db:
            formal = db.get(FormalResearch, formal_id)
            orchestration = db.scalar(
                select(ResearchOrchestration).where(
                    ResearchOrchestration.formal_research_id == formal_id
                )
            )
            failure = db.scalars(
                select(ResearchEvent)
                .where(ResearchEvent.event_type == "research_publication_failed")
                .order_by(ResearchEvent.sequence_no.desc())
            ).first()
        self.assertEqual(formal.phase, "evaluating")
        self.assertEqual(orchestration.state, "blocked")
        self.assertIs(failure.payload_json["retryable"], True)

    def test_worker_preflight_probe_failure_rechecks_inside_claim_without_downgrade(
        self,
    ) -> None:
        formal_id, run_id, issue_number = self.seed_research(65)
        self.publish(formal_id, self.draft(run_id))
        self.github.issues[issue_number]["state"] = "open"
        self.github.issues[issue_number]["labels"] = [
            {"name": "类型:策略研究"}
        ]
        self.github.fail_get_once = True

        projection = publish_next_pending_research_evaluation(
            self.Session,
            self.github,
            artifact_root=self.artifact_root,
            public_base_url="https://research.example.com",
            readback_client=self.readback,
            retry_failed_after_seconds=0,
        )

        with self.Session() as db:
            formal = db.get(FormalResearch, formal_id)
            orchestration = db.scalar(
                select(ResearchOrchestration).where(
                    ResearchOrchestration.formal_research_id == formal_id
                )
            )
            failure_count = db.scalar(
                select(func.count())
                .select_from(ResearchEvent)
                .where(ResearchEvent.event_type == "research_publication_failed")
            )
        self.assertEqual(projection.status, "published")
        self.assertEqual(formal.phase, "published")
        self.assertEqual(orchestration.state, "published")
        self.assertEqual(failure_count, 0)
        self.assertEqual(self.github.issues[issue_number]["state"], "closed")

    def test_worker_detects_edited_terminal_comment_without_overwriting_it(
        self,
    ) -> None:
        formal_id, run_id, issue_number = self.seed_research(84)
        draft = self.draft(run_id)
        self.publish(formal_id, draft)
        original = self.github.comments[issue_number][0]["body"]
        edited = original + "\n人工改写"
        self.github.comments[issue_number][0]["body"] = edited

        with self.assertRaisesRegex(PublicationError, "终态评论缺失或正文漂移"):
            publish_next_pending_research_evaluation(
                self.Session,
                self.github,
                artifact_root=self.artifact_root,
                public_base_url="https://research.example.com",
                readback_client=self.readback,
                retry_failed_after_seconds=0,
            )

        self.assertEqual(len(self.github.comments[issue_number]), 1)
        self.assertEqual(self.github.comments[issue_number][0]["body"], edited)
        with self.Session() as db:
            self.assertEqual(db.get(FormalResearch, formal_id).phase, "evaluating")
            failure = db.scalars(
                select(ResearchEvent)
                .where(ResearchEvent.event_type == "research_publication_failed")
                .order_by(ResearchEvent.sequence_no.desc())
            ).first()
        self.assertIs(failure.payload_json["retryable"], False)

        self.github.comments[issue_number][0]["body"] = original
        recovered = self.publish(formal_id, draft)
        self.assertEqual(recovered.status, "published")
        with self.Session() as db:
            self.assertEqual(db.get(FormalResearch, formal_id).phase, "published")

    def test_deleted_terminal_comment_can_be_replaced_by_forward_correction(
        self,
    ) -> None:
        formal_id, run_id, issue_number = self.seed_research(85)
        first = self.publish(formal_id, self.draft(run_id))
        first_comment_id = self.github.comments[issue_number][0]["id"]
        self.github.comments[issue_number].clear()

        with self.assertRaisesRegex(PublicationError, "终态评论缺失或正文漂移"):
            publish_next_pending_research_evaluation(
                self.Session,
                self.github,
                artifact_root=self.artifact_root,
                public_base_url="https://research.example.com",
                readback_client=self.readback,
                retry_failed_after_seconds=0,
            )
        self.assertEqual(self.github.comments[issue_number], [])

        correction = self.publish(
            formal_id,
            self.draft(
                run_id,
                conclusion="不通过",
                version_note="终态评论删除后的前向更正",
                supersedes_evaluation_id=str(first.evaluation_id),
            ),
        )

        self.assertEqual(correction.status, "published")
        self.assertEqual(len(self.github.comments[issue_number]), 1)
        self.assertNotEqual(
            self.github.comments[issue_number][0]["id"], first_comment_id
        )
        with self.Session() as db:
            publications = db.scalars(
                select(ResearchPublication).order_by(ResearchPublication.version)
            ).all()
        self.assertEqual([item.status for item in publications], ["published", "published"])
        self.assertEqual(publications[1].supersedes_publication_id, publications[0].id)

    def test_correction_creates_replacement_and_keeps_old_stable_report(self) -> None:
        formal_id, run_id, _ = self.seed_research(30)
        first = self.publish(formal_id, self.draft(run_id))
        old_report = next(self.artifact_root.glob("publications/*/report.html"))
        old_bytes = old_report.read_bytes()

        second = self.publish(
            formal_id,
            self.draft(
                run_id,
                conclusion="不通过",
                version_note="v2",
                supersedes_evaluation_id=str(first.evaluation_id),
            ),
        )

        self.assertEqual(second.evaluation_version, 2)
        self.assertEqual(second.supersedes_evaluation_id, first.evaluation_id)
        self.assertEqual(second.supersedes_publication_id, first.publication_id)
        self.assertEqual(old_report.read_bytes(), old_bytes)
        self.assertEqual(len(list(self.artifact_root.rglob("report.html"))), 2)
        with self.Session() as db:
            old_projection = get_publication_projection(db, str(first.publication_id))
            self.assertEqual(
                old_projection.superseded_by_evaluation_id, second.evaluation_id
            )
            rendered = render_evaluation_report(
                db, self.artifact_root, str(first.evaluation_id)
            )
            status_page = render_evaluation_report_page(
                db, self.artifact_root, str(first.evaluation_id)
            )
        self.assertEqual(rendered.encode("utf-8"), old_bytes)
        self.assertIn("此评价已被替代", status_page)
        self.assertIn(str(second.evaluation_id), status_page)

    def test_worker_recovers_only_latest_published_correction(self) -> None:
        formal_id, run_id, _ = self.seed_research(59)
        first = self.publish(formal_id, self.draft(run_id))
        correction = self.draft(
            run_id,
            conclusion="不通过",
            version_note="v2 核心状态失败",
            supersedes_evaluation_id=str(first.evaluation_id),
        )

        with patch(
            "backend.app.research_publication._finalize_research_state",
            side_effect=PublicationError("模拟更正核心状态提交失败"),
        ):
            with self.assertRaisesRegex(PublicationError, "更正核心状态提交失败"):
                self.publish(formal_id, correction)

        with self.Session() as db:
            latest = db.scalar(
                select(ResearchPublication)
                .where(ResearchPublication.formal_research_id == formal_id)
                .order_by(ResearchPublication.version.desc())
            )
            self.assertEqual(latest.version, 2)
            self.assertEqual(latest.status, "published")
            latest_publication_id = latest.id
            events_before = db.scalars(
                select(ResearchEvent)
                .where(ResearchEvent.event_type == "research_published")
                .order_by(ResearchEvent.sequence_no)
            ).all()
        self.assertEqual(
            [item.payload_json["publicationId"] for item in events_before],
            [str(first.publication_id)],
        )

        recovered = publish_next_pending_research_evaluation(
            self.Session,
            self.github,
            artifact_root=self.artifact_root,
            public_base_url="https://research.example.com",
            readback_client=self.readback,
            retry_failed_after_seconds=0,
        )

        self.assertEqual(str(recovered.publication_id), latest_publication_id)
        self.assertEqual(recovered.evaluation_version, 2)
        with self.Session() as db:
            events_after = db.scalars(
                select(ResearchEvent)
                .where(ResearchEvent.event_type == "research_published")
                .order_by(ResearchEvent.sequence_no)
            ).all()
        self.assertEqual(
            [item.payload_json["publicationId"] for item in events_after],
            [str(first.publication_id), latest_publication_id],
        )

    def test_existing_artifact_is_verified_and_never_overwritten(self) -> None:
        formal_id, run_id, _ = self.seed_research(40)
        draft = self.draft(run_id)
        self.readback.override_conclusion = "研究通过"
        with self.assertRaises(PublicationError):
            self.publish(formal_id, draft)
        self.readback.override_conclusion = None
        summary_path = next(self.artifact_root.rglob("summary.json"))
        summary_path.write_text("tampered\n", encoding="utf-8")

        with self.assertRaisesRegex(PublicationError, "工件.*不匹配"):
            self.publish(formal_id, draft)

        self.assertEqual(summary_path.read_text(encoding="utf-8"), "tampered\n")
        with self.Session() as db:
            statuses = db.scalars(
                select(ResearchPublication.status).order_by(
                    ResearchPublication.version
                )
            ).all()
            self.assertEqual(statuses, ["failed", "failed"])

    def test_worker_detects_tampered_published_report_without_overwriting(self) -> None:
        formal_id, run_id, _issue_number = self.seed_research(88)
        projection = self.publish(formal_id, self.draft(run_id))
        report_path = (
            self.artifact_root
            / "publications"
            / str(projection.evaluation_id)
            / "report.html"
        )
        tampered = b"<html><body>tampered</body></html>\n"
        report_path.write_bytes(tampered)

        with self.assertRaisesRegex(PublicationError, "发布工件内容不匹配"):
            publish_next_pending_research_evaluation(
                self.Session,
                self.github,
                artifact_root=self.artifact_root,
                public_base_url="https://research.example.com",
                readback_client=self.readback,
                retry_failed_after_seconds=0,
            )

        self.assertEqual(report_path.read_bytes(), tampered)
        with self.Session() as db:
            status_page = render_evaluation_report_page(
                db, self.artifact_root, str(projection.evaluation_id)
            )
            failed = db.scalars(
                select(ResearchEvent)
                .where(ResearchEvent.event_type == "research_publication_failed")
                .order_by(ResearchEvent.sequence_no.desc())
            ).first()
        self.assertIn("此评价尚未完成一致发布", status_page)
        self.assertIs(failed.payload_json["retryable"], False)

    def test_frontend_readback_mismatch_keeps_issue_open_and_attempt_failed(
        self,
    ) -> None:
        formal_id, run_id, issue_number = self.seed_research(45)
        self.readback.override_conclusion = "研究通过"

        with self.assertRaisesRegex(PublicationError, "前端入口.*不一致"):
            self.publish(formal_id, self.draft(run_id))

        self.assertEqual(self.github.issues[issue_number]["state"], "open")
        self.assertNotIn(
            "研究:已发布",
            {item["name"] for item in self.github.issues[issue_number]["labels"]},
        )
        with self.Session() as db:
            publication = db.scalar(select(ResearchPublication))
            self.assertEqual(publication.status, "failed")

    def test_succeeded_run_manifest_must_match_database_fingerprint(self) -> None:
        formal_id, run_id, issue_number = self.seed_research(46)
        run_manifest = self.artifact_root / "runs" / run_id / "manifest.json"
        wrong_fingerprint = "0" * 64
        run_manifest.write_text(
            f'{{"runId":"{run_id}","resultFingerprint":"{wrong_fingerprint}"}}\n',
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            PublicationError, "canonical manifest.*运行身份不一致"
        ):
            self.publish(formal_id, self.draft(run_id))

        self.assertEqual(self.github.comments[issue_number], [])
        with self.Session() as db:
            self.assertEqual(db.scalar(select(ResearchPublication.status)), "failed")

    def test_canonical_ledger_tampering_blocks_publication(self) -> None:
        formal_id, run_id, issue_number = self.seed_research(47)
        ledger = self.artifact_root / "runs" / run_id / "positions.csv.gz"
        ledger.write_bytes(b"tampered ledger\n")

        with self.assertRaisesRegex(PublicationError, "canonical 工件 SHA-256 不匹配"):
            self.publish(formal_id, self.draft(run_id))

        self.assertEqual(self.github.comments[issue_number], [])
        with self.Session() as db:
            self.assertEqual(db.scalar(select(ResearchPublication.status)), "failed")

    def test_succeeded_run_delegates_to_full_archive_validator(self) -> None:
        formal_id, run_id, issue_number = self.seed_research(54)
        self.validate_archive.side_effect = SnapshotIntegrityError(
            "冻结 config 已被篡改"
        )

        with self.assertRaisesRegex(PublicationError, "canonical 归档校验失败"):
            self.publish(formal_id, self.draft(run_id))

        self.validate_archive.assert_called_once_with(
            self.artifact_root / "runs" / run_id
        )
        self.assertEqual(self.github.issues[issue_number]["state"], "open")
        with self.Session() as db:
            publication = db.scalar(select(ResearchPublication))
            event = db.scalar(
                select(ResearchEvent).where(
                    ResearchEvent.event_type == "research_publication_failed"
                )
            )
        self.assertEqual(publication.status, "failed")
        self.assertIs(event.payload_json["retryable"], False)

    def test_failed_correction_does_not_hide_latest_published_conclusion(self) -> None:
        formal_id, run_id, issue_number = self.seed_research(48)
        first = self.publish(formal_id, self.draft(run_id, conclusion="证据不足"))
        self.readback.override_conclusion = "研究通过"

        with self.assertRaises(PublicationError):
            self.publish(
                formal_id,
                self.draft(
                    run_id,
                    conclusion="不通过",
                    version_note="失败更正",
                    supersedes_evaluation_id=str(first.evaluation_id),
                ),
            )

        with self.Session() as db:
            strategy_id = db.get(ResearchRun, run_id).strategy_id
            profile = get_strategy_profile(db, strategy_id)
        summary = profile.formal_researches[0]
        self.assertEqual(summary.latest_publication_id, first.publication_id)
        self.assertEqual(summary.latest_publication_conclusion, "证据不足")
        self.assertEqual(summary.latest_publication_status, "published")

        self.readback.override_conclusion = None
        self.github.comments[issue_number][0]["body"] += "\n人工改写旧生效评论"
        with self.assertRaisesRegex(PublicationError, "终态评论缺失或正文漂移"):
            publish_next_pending_research_evaluation(
                self.Session,
                self.github,
                artifact_root=self.artifact_root,
                public_base_url="https://research.example.com",
                readback_client=self.readback,
                retry_failed_after_seconds=0,
            )
        with self.Session() as db:
            latest_failure = db.scalars(
                select(ResearchEvent)
                .where(ResearchEvent.event_type == "research_publication_failed")
                .order_by(ResearchEvent.sequence_no.desc())
            ).first()
            status_page = render_evaluation_report_page(
                db, self.artifact_root, str(first.evaluation_id)
            )
        self.assertEqual(
            latest_failure.payload_json["publicationId"],
            str(first.publication_id),
        )
        self.assertIn("此评价尚未完成一致发布", status_page)

    def test_post_published_correction_failure_keeps_previous_current_and_blocks_v3(
        self,
    ) -> None:
        formal_id, run_id, _ = self.seed_research(63)
        first = self.publish(formal_id, self.draft(run_id, conclusion="证据不足"))

        class PublishedMismatchReadback(LocalReadbackClient):
            def __init__(self, session_factory, artifact_root):
                super().__init__(session_factory, artifact_root)
                self.publication_reads = 0

            def read_publication(self, publication_id: str) -> dict:
                payload = super().read_publication(publication_id)
                self.publication_reads += 1
                if self.publication_reads >= 2:
                    payload["conclusion"] = "研究通过"
                return payload

        correction = self.draft(
            run_id,
            conclusion="不通过",
            version_note="v2 发布后读回失败",
            supersedes_evaluation_id=str(first.evaluation_id),
        )
        with self.assertRaisesRegex(PublicationError, "前端入口.*不一致"):
            publish_research_evaluation(
                self.Session,
                self.github,
                formal_research_id=formal_id,
                draft=correction,
                artifact_root=self.artifact_root,
                public_base_url="https://research.example.com",
                readback_client=PublishedMismatchReadback(
                    self.Session, self.artifact_root
                ),
            )

        with self.Session() as db:
            evaluations = db.scalars(
                select(ResearchEvaluation).order_by(ResearchEvaluation.version)
            ).all()
            strategy_id = db.get(ResearchRun, run_id).strategy_id
            profile = get_strategy_profile(db, strategy_id)
            old_projection = get_publication_projection(
                db, str(first.publication_id)
            )
            old_status_page = render_evaluation_report_page(
                db, self.artifact_root, str(first.evaluation_id)
            )
            failed_status_page = render_evaluation_report_page(
                db, self.artifact_root, evaluations[1].id
            )
        self.assertEqual(len(evaluations), 2)
        failed_correction = evaluations[1]
        summary = profile.formal_researches[0]
        self.assertEqual(summary.latest_publication_id, first.publication_id)
        self.assertEqual(summary.latest_publication_conclusion, "证据不足")
        self.assertIsNone(old_projection.superseded_by_evaluation_id)
        self.assertIn("当前生效评价", old_status_page)
        self.assertIn(
            "此评价尚未完成一致发布，不代表当前研究结论",
            failed_status_page,
        )
        latest_comment = self.github.comments[700 + 63][-1]["body"]
        self.assertIn("若显示未完成或已替代，本评论不替代当前结论", latest_comment)

        with self.assertRaisesRegex(PublicationConflictError, "上一评价尚未发布"):
            self.publish(
                formal_id,
                self.draft(
                    run_id,
                    conclusion="有条件候选",
                    version_note="v3 不得越过失败更正",
                    supersedes_evaluation_id=failed_correction.id,
                ),
            )
        with self.Session() as db:
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchEvaluation)), 2
            )

    def test_worker_entrypoint_retries_frozen_evaluation_without_inferring_conclusion(
        self,
    ) -> None:
        formal_id, run_id, _ = self.seed_research(49)
        self.readback.fail_once = True
        with self.assertRaises(PublicationError):
            self.publish(formal_id, self.draft(run_id, conclusion="不通过"))

        projection = publish_next_pending_research_evaluation(
            self.Session,
            self.github,
            artifact_root=self.artifact_root,
            public_base_url="https://research.example.com",
            readback_client=self.readback,
            retry_failed_after_seconds=0,
        )

        self.assertIsNotNone(projection)
        self.assertEqual(projection.conclusion, "不通过")
        with self.Session() as db:
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchEvaluation)), 1
            )

    def test_cli_prepare_has_no_github_side_effect_and_proposal_link_is_stable(
        self,
    ) -> None:
        formal_id, run_id, issue_number = self.seed_research(56)
        prepared = prepare_research_evaluation(
            self.Session,
            formal_research_id=formal_id,
            draft=self.draft(run_id),
        )

        self.assertEqual(prepared.status, "pending")
        self.assertEqual(self.github.comments[issue_number], [])
        proposal_id = "90000000-0000-0000-0000-000000000056"
        with self.Session.begin() as db:
            strategy_id = db.get(ResearchRun, run_id).strategy_id
            db.add(
                FollowUpResearchProposal(
                    id=proposal_id,
                    strategy_id=strategy_id,
                    source_evaluation_id=str(prepared.evaluation_id),
                    title="补充独立样本外验证",
                    rationale="扩大独立样本并覆盖压力环境。",
                    status="proposed",
                    proposal_json={"scope": "OOS"},
                )
            )

        projection = publish_next_pending_research_evaluation(
            self.Session,
            self.github,
            artifact_root=self.artifact_root,
            public_base_url="http://127.0.0.1:15173",
            readback_client=self.readback,
            retry_failed_after_seconds=0,
        )

        self.assertEqual(projection.status, "published")
        comment = self.github.comments[issue_number][0]["body"]
        summary_path = next(self.artifact_root.rglob("summary.json"))
        frozen_summary = summary_path.read_bytes()
        self.assertIn("### 后续研究提案", comment)
        self.assertIn("查看该评价的结构化后续研究提案", comment)
        self.assertIn(
            f"http://127.0.0.1:15173/api/research/formal-researches/{formal_id}",
            comment,
        )

        with self.Session.begin() as db:
            proposal = db.get(FollowUpResearchProposal, proposal_id)
            proposal.title = "补充独立压力环境验证"
            proposal.status = "accepted"
            db.add(
                FollowUpResearchProposal(
                    id="90000000-0000-0000-0000-000000000057",
                    strategy_id=proposal.strategy_id,
                    source_evaluation_id=str(prepared.evaluation_id),
                    title="追加成本压力测试",
                    rationale="检查更高成本下的稳健性。",
                    status="proposed",
                    proposal_json={"scope": "cost-stress"},
                )
            )

        repeated = publish_research_evaluation(
            self.Session,
            self.github,
            formal_research_id=formal_id,
            draft=self.draft(run_id),
            artifact_root=self.artifact_root,
            public_base_url="http://127.0.0.1:15173",
            readback_client=self.readback,
        )

        self.assertEqual(repeated.publication_id, projection.publication_id)
        self.assertEqual(summary_path.read_bytes(), frozen_summary)
        self.assertEqual(self.github.comments[issue_number][0]["body"], comment)
        self.assertEqual(len(self.github.comments[issue_number]), 1)

    def test_issue_comment_is_bounded_and_full_evidence_stays_in_report(
        self,
    ) -> None:
        formal_id, run_id, issue_number = self.seed_research(84)
        large_statement = "超长反对证据" + "量" * 70_000
        draft = replace(
            self.draft(run_id, conclusion="不通过"),
            opposing_evidence=({"statement": large_statement},),
        )

        projection = self.publish(formal_id, draft)

        comment = self.github.comments[issue_number][0]["body"]
        with self.Session() as db:
            report = render_evaluation_report(
                db, self.artifact_root, str(projection.evaluation_id)
            )
        self.assertLessEqual(
            len(comment.encode("utf-8")), MAX_GITHUB_ISSUE_COMMENT_BYTES
        )
        self.assertIn("完整内容见机器摘要与冻结报告", comment)
        self.assertNotIn(large_statement, comment)
        self.assertIn(large_statement, report)

    def test_worker_does_not_retry_deterministic_publication_failure(self) -> None:
        formal_id, run_id, _ = self.seed_research(55)
        self.readback.override_conclusion = "研究通过"
        with self.assertRaises(PublicationError):
            self.publish(formal_id, self.draft(run_id, conclusion="不通过"))
        self.readback.override_conclusion = None

        projection = publish_next_pending_research_evaluation(
            self.Session,
            self.github,
            artifact_root=self.artifact_root,
            public_base_url="https://research.example.com",
            readback_client=self.readback,
            retry_failed_after_seconds=0,
        )

        self.assertIsNone(projection)
        with self.Session() as db:
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchPublication)),
                1,
            )

    def test_worker_retry_delay_starts_at_latest_failure_event(self) -> None:
        formal_id, run_id, _ = self.seed_research(60)
        entry_at = datetime(2026, 7, 20, 3, 0, tzinfo=timezone.utc)
        failed_at = entry_at + timedelta(hours=1)
        prepared = prepare_research_evaluation(
            self.Session,
            formal_research_id=formal_id,
            draft=self.draft(run_id),
            now=entry_at,
        )
        with self.Session.begin() as db:
            publication = db.get(ResearchPublication, str(prepared.publication_id))
            publication.created_at = entry_at - timedelta(hours=1)
        self.readback.fail_once = True

        with patch(
            "backend.app.research_publication._utc_now", return_value=failed_at
        ):
            with self.assertRaisesRegex(PublicationError, "前端入口暂时不可用"):
                publish_next_pending_research_evaluation(
                    self.Session,
                    self.github,
                    artifact_root=self.artifact_root,
                    public_base_url="https://research.example.com",
                    readback_client=self.readback,
                    retry_failed_after_seconds=0,
                    now=entry_at,
                )

        with self.Session() as db:
            failure = db.scalar(
                select(ResearchEvent).where(
                    ResearchEvent.event_type == "research_publication_failed"
                )
            )
        self.assertEqual(failure.occurred_at.replace(tzinfo=timezone.utc), failed_at)

        too_early = publish_next_pending_research_evaluation(
            self.Session,
            self.github,
            artifact_root=self.artifact_root,
            public_base_url="https://research.example.com",
            readback_client=self.readback,
            retry_failed_after_seconds=300,
            now=failed_at + timedelta(seconds=299),
        )
        self.assertIsNone(too_early)

        recovered = publish_next_pending_research_evaluation(
            self.Session,
            self.github,
            artifact_root=self.artifact_root,
            public_base_url="https://research.example.com",
            readback_client=self.readback,
            retry_failed_after_seconds=300,
            now=failed_at + timedelta(seconds=300),
        )
        self.assertEqual(recovered.status, "published")

    def test_worker_does_not_retry_deterministic_failure_after_published_readback(
        self,
    ) -> None:
        formal_id, run_id, issue_number = self.seed_research(61)

        class PublishedMismatchReadback(LocalReadbackClient):
            def __init__(self, session_factory, artifact_root):
                super().__init__(session_factory, artifact_root)
                self.publication_reads = 0

            def read_publication(self, publication_id: str) -> dict:
                payload = super().read_publication(publication_id)
                self.publication_reads += 1
                if self.publication_reads >= 2:
                    payload["conclusion"] = "研究通过"
                return payload

        mismatched = PublishedMismatchReadback(self.Session, self.artifact_root)
        with self.assertRaisesRegex(PublicationError, "前端入口.*不一致"):
            publish_research_evaluation(
                self.Session,
                self.github,
                formal_research_id=formal_id,
                draft=self.draft(run_id),
                artifact_root=self.artifact_root,
                public_base_url="https://research.example.com",
                readback_client=mismatched,
            )

        with self.Session() as db:
            publication = db.scalar(select(ResearchPublication))
            formal = db.get(FormalResearch, formal_id)
            orchestration = db.scalar(
                select(ResearchOrchestration).where(
                    ResearchOrchestration.formal_research_id == formal_id
                )
            )
            failure = db.scalar(
                select(ResearchEvent).where(
                    ResearchEvent.event_type == "research_publication_failed"
                )
            )
        self.assertEqual(publication.status, "published")
        self.assertEqual(formal.phase, "evaluating")
        self.assertEqual(orchestration.state, "blocked")
        self.assertIs(failure.payload_json["retryable"], False)
        self.assertEqual(failure.payload_json["publicationStatus"], "published")
        self.assertEqual(self.github.issues[issue_number]["state"], "open")

        automatic_retry = publish_next_pending_research_evaluation(
            self.Session,
            self.github,
            artifact_root=self.artifact_root,
            public_base_url="https://research.example.com",
            readback_client=self.readback,
            retry_failed_after_seconds=0,
        )
        self.assertIsNone(automatic_retry)

    def test_default_readback_uses_real_http_adapter_and_environment_url(self) -> None:
        formal_id, run_id, _ = self.seed_research(52)

        def override_db():
            with self.Session() as db:
                yield db

        main.app.dependency_overrides[main.get_db] = override_db
        listening_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listening_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listening_socket.bind(("127.0.0.1", 0))
        listening_socket.listen(128)
        port = int(listening_socket.getsockname()[1])
        server = uvicorn.Server(
            uvicorn.Config(
                main.app,
                log_level="critical",
                lifespan="off",
            )
        )
        thread = Thread(
            target=server.run,
            kwargs={"sockets": [listening_socket]},
            daemon=True,
        )
        thread.start()
        try:
            deadline = time.monotonic() + 2
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(server.started)
            with patch.dict(
                os.environ,
                {
                    "RESEARCH_READBACK_BASE_URL": (f"http://127.0.0.1:{port}"),
                    "RESEARCH_ARTIFACT_ROOT": str(self.artifact_root),
                },
            ):
                projection = publish_research_evaluation(
                    self.Session,
                    self.github,
                    formal_research_id=formal_id,
                    draft=self.draft(run_id),
                    artifact_root=self.artifact_root,
                    public_base_url="https://research.example.com",
                )
        finally:
            server.should_exit = True
            thread.join(timeout=2)
            listening_socket.close()
            main.app.dependency_overrides.pop(main.get_db, None)

        self.assertEqual(projection.status, "published")

    def test_public_comment_base_url_allows_loopback_http_only(self) -> None:
        formal_id, run_id, _ = self.seed_research(53)
        with self.assertRaisesRegex(PublicationConflictError, "loopback HTTP URL"):
            publish_research_evaluation(
                self.Session,
                self.github,
                formal_research_id=formal_id,
                draft=self.draft(run_id),
                artifact_root=self.artifact_root,
                public_base_url="http://research.example.com",
                readback_client=self.readback,
            )
        with self.Session() as db:
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchEvaluation)), 0
            )

        projection = publish_research_evaluation(
            self.Session,
            self.github,
            formal_research_id=formal_id,
            draft=self.draft(run_id),
            artifact_root=self.artifact_root,
            public_base_url="http://127.0.0.1:15173",
            readback_client=self.readback,
        )
        self.assertEqual(projection.status, "published")

    def test_invalid_public_url_marks_existing_pending_publication_failed(
        self,
    ) -> None:
        formal_id, run_id, _ = self.seed_research(89)
        pending = prepare_research_evaluation(
            self.Session,
            formal_research_id=formal_id,
            draft=self.draft(run_id),
        )

        with self.assertRaisesRegex(PublicationConflictError, "loopback HTTP URL"):
            publish_existing_research_evaluation(
                self.Session,
                self.github,
                evaluation_id=str(pending.evaluation_id),
                artifact_root=self.artifact_root,
                public_base_url="http://research.example.com",
                readback_client=self.readback,
            )

        with self.Session() as db:
            publication = db.get(ResearchPublication, str(pending.publication_id))
            failure = db.scalar(
                select(ResearchEvent).where(
                    ResearchEvent.event_type == "research_publication_failed"
                )
            )
        self.assertEqual(publication.status, "failed")
        self.assertIs(failure.payload_json["retryable"], False)

    def test_preexisting_pending_evaluation_is_published_without_duplication(
        self,
    ) -> None:
        formal_id, run_id, issue_number = self.seed_research(
            50,
            origin="historical_import",
        )
        with self.assertRaisesRegex(PublicationConflictError, "历史导入评价已由迁移合同冻结"):
            prepare_research_evaluation(
                self.Session,
                formal_research_id=formal_id,
                draft=self.draft(run_id),
            )
        evaluation_id = "60000000-0000-0000-0000-000000000050"
        publication_id = "70000000-0000-0000-0000-000000000050"
        with self.Session.begin() as db:
            run = db.get(ResearchRun, run_id)
            source_summary_sha256 = "6" * 64
            evaluation_sha256 = canonical_sha256(
                {
                    "formalResearchId": formal_id,
                    "version": 1,
                    "conclusion": "不通过",
                    "supportingEvidence": [{"statement": "历史报告已冻结"}],
                    "opposingEvidence": [{"statement": "门禁未通过"}],
                    "missingEvidence": [],
                    "limitations": [{"statement": "历史迁移"}],
                    "followUpRecommendations": [
                        {"statement": "保持为待证伪假设，不在同一 OOS 调参。"}
                    ],
                    "runIdentities": [
                        {
                            "runId": run_id,
                            "codeCommit": run.code_commit,
                            "reproducibilityKey": run.reproducibility_key,
                            "resultFingerprint": run.result_fingerprint,
                        }
                    ],
                    "sourceSummarySha256": source_summary_sha256,
                }
            )
            evaluation = ResearchEvaluation(
                id=evaluation_id,
                formal_research_id=formal_id,
                version=1,
                conclusion="不通过",
                evaluation_sha256=evaluation_sha256,
                supporting_evidence=[{"statement": "历史报告已冻结"}],
                opposing_evidence=[{"statement": "门禁未通过"}],
                missing_evidence=[],
                limitations=[{"statement": "历史迁移"}],
                follow_up_recommendations=[
                    {"statement": "保持为待证伪假设，不在同一 OOS 调参。"}
                ],
            )
            db.add(evaluation)
            db.flush()
            db.add_all(
                [
                    ResearchEvaluationRun(evaluation_id=evaluation_id, run_id=run_id),
                    ResearchEvidenceRef(
                        id="80000000-0000-0000-0000-000000000050",
                        evaluation_id=evaluation_id,
                        kind="report",
                        uri="repo://history/report.html",
                        sha256="8" * 64,
                        metadata_json={"origin": "historical_import"},
                    ),
                    ResearchEvidenceRef(
                        id="80000000-0000-0000-0000-000000000051",
                        evaluation_id=evaluation_id,
                        kind="statistics",
                        uri="repo://history/summary.json",
                        sha256=source_summary_sha256,
                        metadata_json={"origin": "historical_import"},
                    ),
                    ResearchPublication(
                        id=publication_id,
                        formal_research_id=formal_id,
                        evaluation_id=evaluation_id,
                        version=1,
                        status="pending",
                        publication_sha256="7" * 64,
                        artifact_manifest_uri="artifacts://history/manifest.json",
                        issue_number=issue_number,
                    ),
                ]
            )

        projection = publish_existing_research_evaluation(
            self.Session,
            self.github,
            evaluation_id=evaluation_id,
            artifact_root=self.artifact_root,
            public_base_url="https://research.example.com",
            readback_client=self.readback,
        )

        self.assertEqual(str(projection.publication_id), publication_id)
        self.assertEqual(projection.evaluation_sha256, evaluation_sha256)
        self.assertEqual(projection.status, "published")
        self.assertEqual(
            projection.manifest_url,
            f"/api/research/evaluations/{evaluation_id}/artifacts/manifest.json",
        )
        self.assertEqual(len(self.github.comments[issue_number]), 1)
        with self.Session() as db:
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchEvaluation)), 1
            )
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchPublication)), 1
            )
            publication = db.get(ResearchPublication, publication_id)
            self.assertEqual(publication.artifact_manifest_uri, projection.manifest_url)
            self.assertNotEqual(publication.publication_sha256, "7" * 64)

        self.github.issues[issue_number]["labels"] = [
            item
            for item in self.github.issues[issue_number]["labels"]
            if item["name"] != "来源:历史导入"
        ]
        with self.assertRaisesRegex(PublicationError, "缺少标签"):
            publish_next_pending_research_evaluation(
                self.Session,
                self.github,
                artifact_root=self.artifact_root,
                public_base_url="https://research.example.com",
                readback_client=self.readback,
                retry_failed_after_seconds=0,
            )
        with self.Session() as db:
            formal = db.get(FormalResearch, formal_id)
            failed = db.scalars(
                select(ResearchEvent)
                .where(ResearchEvent.event_type == "research_publication_failed")
                .order_by(ResearchEvent.sequence_no.desc())
            ).first()
            status_page = render_evaluation_report_page(
                db, self.artifact_root, evaluation_id
            )
        self.assertEqual(formal.phase, "stopped")
        self.assertIs(failed.payload_json["retryable"], False)
        self.assertIn("此评价尚未完成一致发布", status_page)
        self.assertIn('class="banner incomplete"', status_page)

    def test_historical_pending_requires_dedicated_labeled_issue(self) -> None:
        formal_id, evaluation_id, _publication_id, issue_number = (
            self.seed_historical_pending(66)
        )
        self.github.issues[issue_number]["labels"] = [{"name": "类型:工程任务"}]

        with self.assertRaisesRegex(PublicationConflictError, "独立的策略研究 Issue"):
            publish_next_pending_research_evaluation(
                self.Session,
                self.github,
                artifact_root=self.artifact_root,
                public_base_url="https://research.example.com",
                readback_client=self.readback,
                retry_failed_after_seconds=0,
            )
        with self.assertRaisesRegex(PublicationConflictError, "独立的策略研究 Issue"):
            publish_existing_research_evaluation(
                self.Session,
                self.github,
                evaluation_id=evaluation_id,
                artifact_root=self.artifact_root,
                public_base_url="https://research.example.com",
                readback_client=self.readback,
            )
        self.assertEqual(self.github.comments[issue_number], [])
        with self.Session() as db:
            publication = db.scalar(select(ResearchPublication))
            failure = db.scalar(
                select(ResearchEvent).where(
                    ResearchEvent.event_type == "research_publication_failed"
                )
            )
        self.assertEqual(publication.status, "failed")
        self.assertIs(failure.payload_json["retryable"], False)

    def test_historical_pending_requires_same_minimum_content_contract(self) -> None:
        _formal_id, evaluation_id, _publication_id, issue_number = (
            self.seed_historical_pending(81)
        )

        with self.assertRaisesRegex(PublicationConflictError, "后续建议"):
            publish_existing_research_evaluation(
                self.Session,
                self.github,
                evaluation_id=evaluation_id,
                artifact_root=self.artifact_root,
                public_base_url="https://research.example.com",
                readback_client=self.readback,
            )

        self.assertEqual(self.github.comments[issue_number], [])

    def test_historical_pending_rejects_closed_issue_before_publication(self) -> None:
        _formal_id, evaluation_id, _publication_id, issue_number = (
            self.seed_historical_pending(82)
        )
        self.github.issues[issue_number]["state"] = "closed"

        with self.assertRaisesRegex(PublicationConflictError, "OPEN Issue"):
            publish_existing_research_evaluation(
                self.Session,
                self.github,
                evaluation_id=evaluation_id,
                artifact_root=self.artifact_root,
                public_base_url="https://research.example.com",
                readback_client=self.readback,
            )

        self.assertEqual(self.github.comments[issue_number], [])

    def test_historical_pending_revalidates_frozen_strategy_issue_pair(self) -> None:
        if self.engine.dialect.name != "sqlite":
            self.skipTest("PostgreSQL 由 0010 不可变触发器直接拒绝映射 UPDATE")
        formal_id, evaluation_id, _publication_id, _issue_number = (
            self.seed_historical_pending(83)
        )
        with self.Session.begin() as db:
            db.execute(
                text(
                    "UPDATE research_publication_issue_mappings "
                    "SET issue_number = 38 WHERE formal_research_id = :formal_id"
                ),
                {"formal_id": formal_id.replace("-", "")},
            )
        self.github.add_issue(38)
        self.github.issues[38]["title"] = (
            "历史研究：沪深300 ETF 低波动准入结构化评价发布"
        )
        self.github.issues[38]["labels"].append({"name": "来源:历史导入"})

        with self.assertRaisesRegex(PublicationConflictError, "冻结映射"):
            publish_existing_research_evaluation(
                self.Session,
                self.github,
                evaluation_id=evaluation_id,
                artifact_root=self.artifact_root,
                public_base_url="https://research.example.com",
                readback_client=self.readback,
            )

        self.assertEqual(self.github.comments[38], [])

    def test_historical_issue_mapping_is_immutable_in_orm(self) -> None:
        formal_id, _run_id, _ = self.seed_research(
            80, origin="historical_import"
        )
        with self.Session() as db:
            mapping = db.get(ResearchPublicationIssueMapping, formal_id)
            mapping.issue_number += 1
            with self.assertRaisesRegex(RuntimeError, "不可变研究记录"):
                db.commit()
            db.rollback()
        with self.Session() as db:
            mapping = db.get(ResearchPublicationIssueMapping, formal_id)
            db.delete(mapping)
            with self.assertRaisesRegex(RuntimeError, "不可删除"):
                db.commit()
            db.rollback()

    def test_preexisting_evaluation_rejects_tampered_fingerprint(self) -> None:
        formal_id, run_id, issue_number = self.seed_research(51)
        evaluation_id = "60000000-0000-0000-0000-000000000051"
        publication_id = "70000000-0000-0000-0000-000000000051"
        with self.Session.begin() as db:
            draft = self.draft(run_id)
            db.add(
                ResearchEvaluation(
                    id=evaluation_id,
                    formal_research_id=formal_id,
                    version=1,
                    conclusion=draft.conclusion,
                    evaluation_sha256="9" * 64,
                    supporting_evidence=list(draft.supporting_evidence),
                    opposing_evidence=list(draft.opposing_evidence),
                    missing_evidence=list(draft.missing_evidence),
                    limitations=list(draft.limitations),
                    follow_up_recommendations=list(draft.follow_up_recommendations),
                )
            )
            db.flush()
            db.add(ResearchEvaluationRun(evaluation_id=evaluation_id, run_id=run_id))
            db.add(
                ResearchEvidenceRef(
                    id="80000000-0000-0000-0000-000000000052",
                    evaluation_id=evaluation_id,
                    run_id=run_id,
                    kind="report",
                    uri=f"artifacts://{run_id}/manifest.json",
                    sha256=draft.evidence_refs[0].sha256,
                    metadata_json={"mediaType": "application/json"},
                )
            )
            db.add(
                ResearchPublication(
                    id=publication_id,
                    formal_research_id=formal_id,
                    evaluation_id=evaluation_id,
                    version=1,
                    status="pending",
                    publication_sha256="7" * 64,
                    artifact_manifest_uri=(
                        f"/api/research/evaluations/{evaluation_id}/artifacts/manifest.json"
                    ),
                    issue_number=issue_number,
                )
            )

        with self.assertRaisesRegex(PublicationConflictError, "评价指纹.*不一致"):
            publish_existing_research_evaluation(
                self.Session,
                self.github,
                evaluation_id=evaluation_id,
                artifact_root=self.artifact_root,
                public_base_url="https://research.example.com",
                readback_client=self.readback,
            )
        with self.Session() as db:
            publication = db.get(ResearchPublication, publication_id)
            event = db.scalar(
                select(ResearchEvent).where(
                    ResearchEvent.event_type == "research_publication_failed"
                )
            )
        self.assertEqual(publication.status, "failed")
        self.assertIs(event.payload_json["retryable"], False)


class ResearchPublicationGitHubClientTest(unittest.TestCase):
    def test_published_historical_issue_allows_state_label_drift_for_repair(
        self,
    ) -> None:
        expected = resolve_historical_publication_issue(
            "etf_volatility_managed", 37
        )
        issue = {
            "number": 37,
            "state": "closed",
            "title": expected.title,
            "labels": [
                {"name": "类型:策略研究"},
                {"name": "来源:历史导入"},
            ],
        }

        validate_historical_publication_issue_snapshot(
            issue, expected, allow_published=True
        )
        with self.assertRaisesRegex(ValueError, "OPEN Issue"):
            validate_historical_publication_issue_snapshot(issue, expected)

    def test_historical_mapping_rejects_invalid_issue_before_database_write(
        self,
    ) -> None:
        class InvalidIssueClient:
            repository = "Jettlin927/Quantitative_trading"

            def get_issue(self, issue_number: int) -> dict:
                return {
                    "number": issue_number,
                    "state": "open",
                    "title": "历史研究：沪深300 ETF 波动率管理结构化评价发布",
                    "labels": [{"name": "类型:策略研究"}],
                }

        class DatabaseMustNotOpen:
            opened = False

            def begin(self):
                self.opened = True
                raise AssertionError("GitHub Issue 校验失败后不得打开数据库事务")

        database = DatabaseMustNotOpen()
        with (
            patch.object(
                register_historical_issue_mapping,
                "assert_schema_revision_at_head",
            ),
            patch.object(
                register_historical_issue_mapping.GitHubIssueClient,
                "from_env",
                return_value=InvalidIssueClient(),
            ),
            patch.object(
                register_historical_issue_mapping,
                "SessionLocal",
                database,
            ),
        ):
            status = register_historical_issue_mapping.main(
                [
                    "--strategy-id",
                    "etf_volatility_managed",
                    "--issue-number",
                    "37",
                ]
            )

        self.assertEqual(status, 3)
        self.assertFalse(database.opened)

    def test_historical_mapping_rejects_cross_binding_before_external_access(
        self,
    ) -> None:
        with (
            patch.object(
                register_historical_issue_mapping,
                "assert_schema_revision_at_head",
            ) as schema_check,
            patch.object(
                register_historical_issue_mapping.GitHubIssueClient,
                "from_env",
            ) as github_factory,
        ):
            status = register_historical_issue_mapping.main(
                [
                    "--strategy-id",
                    "etf_volatility_managed",
                    "--issue-number",
                    "38",
                ]
            )

        self.assertEqual(status, 3)
        schema_check.assert_not_called()
        github_factory.assert_not_called()

    def test_explicit_evaluation_contract_is_strictly_parsed(self) -> None:
        formal_id, draft = parse_evaluation_contract(
            {
                "schemaVersion": "research-evaluation-request/v1",
                "formalResearchId": "formal-1",
                "conclusion": "证据不足",
                "runIds": ["run-1"],
                "supportingEvidence": [{"statement": "已冻结运行"}],
                "missingEvidence": [{"statement": "缺少更长 OOS"}],
                "evidenceRefs": [
                    {
                        "kind": "report",
                        "uri": "artifacts://run-1/manifest.json",
                        "runId": "run-1",
                        "sha256": "1" * 64,
                    }
                ],
            }
        )

        self.assertEqual(formal_id, "formal-1")
        self.assertEqual(draft.conclusion, "证据不足")
        self.assertEqual(draft.run_ids, ("run-1",))
        with self.assertRaisesRegex(PublicationConflictError, "未知字段"):
            parse_evaluation_contract(
                {
                    "schemaVersion": "research-evaluation-request/v1",
                    "formalResearchId": "formal-1",
                    "conclusion": "不通过",
                    "runIds": [],
                    "自动推断结论": True,
                }
            )

    def test_publication_comment_is_create_only_and_marker_collision_is_rejected(
        self,
    ) -> None:
        class Client(GitHubIssueClient):
            def __init__(self) -> None:
                super().__init__("owner/repo", "test-token")
                self.requests = []

            def _request(self, method, path, payload=None):
                self.requests.append((method, path, payload))
                return {"id": 22, "body": payload["body"]}

        client = Client()
        marker = "<!-- research-publication:evaluation:abc -->"
        body = f"{marker}\n中文结论"
        existing = [{"id": 11, "body": body}]

        self.assertEqual(
            client.ensure_comment(7, body, existing, marker=marker)["id"], 11
        )
        self.assertEqual(client.requests, [])
        with self.assertRaisesRegex(GitHubResearchError, "不同正文"):
            client.ensure_comment(
                7,
                body,
                [{"id": 11, "body": f"{marker}\n被篡改"}],
                marker=marker,
            )
        created = client.ensure_comment(7, body, [], marker=marker)
        self.assertEqual(created["id"], 22)
        self.assertEqual(client.requests[0][0], "POST")

    def test_issue_close_and_published_label_use_one_atomic_update(self) -> None:
        class Client(GitHubIssueClient):
            def __init__(self) -> None:
                super().__init__("owner/repo", "test-token")
                self.requests = []

            def _request(self, method, path, payload=None):
                self.requests.append((method, path, payload))
                return {
                    "state": payload["state"],
                    "labels": [{"name": item} for item in payload["labels"]],
                }

        client = Client()
        client.finalize_issue(
            7,
            {
                "state": "open",
                "labels": [
                    {"name": "类型:策略研究"},
                    {"name": "研究:运行中"},
                ],
            },
        )

        self.assertEqual(len(client.requests), 1)
        method, path, payload = client.requests[0]
        self.assertEqual((method, path), ("PATCH", "/repos/owner/repo/issues/7"))
        self.assertEqual(payload["state"], "closed")
        self.assertEqual(payload["state_reason"], "completed")
        self.assertEqual(payload["labels"], ["研究:已发布", "类型:策略研究"])

    def test_compose_readback_is_wired_through_frontend_proxy(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        compose = (repo_root / "docker-compose.yml").read_text(encoding="utf-8")
        dockerfile = (repo_root / "frontend" / "Dockerfile").read_text(
            encoding="utf-8"
        )
        nginx = (repo_root / "frontend" / "nginx.conf").read_text(
            encoding="utf-8"
        )
        frontend_compose = compose.split("\n  frontend:\n", 1)[1].split(
            "\nvolumes:\n", 1
        )[0]
        self.assertIn(
            "RESEARCH_READBACK_BASE_URL: ${RESEARCH_READBACK_BASE_URL:-http://frontend:5173}",
            compose,
        )
        self.assertIn(
            "RESEARCH_PUBLIC_BASE_URL: ${RESEARCH_PUBLIC_BASE_URL:-http://127.0.0.1:15173}",
            compose,
        )
        self.assertIn("npm run build", dockerfile)
        self.assertIn("FROM nginx:stable-alpine", dockerfile)
        self.assertNotIn('CMD ["npm", "run", "dev"', dockerfile)
        self.assertIn("condition: service_healthy", frontend_compose)
        self.assertNotIn("VITE_API_", frontend_compose)
        self.assertNotIn("volumes:", frontend_compose)
        self.assertIn("listen 5173;", nginx)
        self.assertIn("location /api/", nginx)
        self.assertIn("proxy_pass http://api:8000;", nginx)


@unittest.skipUnless(
    os.getenv("TEST_RESEARCH_WORKER_POSTGRES_URL"),
    "TEST_RESEARCH_WORKER_POSTGRES_URL 未配置，跳过 PostgreSQL 一致发布集成测试",
)
class ResearchPublicationPostgresTest(ResearchPublicationTest):
    @classmethod
    def setUpClass(cls) -> None:
        database_url = os.environ["TEST_RESEARCH_WORKER_POSTGRES_URL"]
        parsed = make_url(database_url)
        if (
            parsed.host not in {"127.0.0.1", "localhost"}
            or parsed.database != "quant_worker_test"
        ):
            raise AssertionError("一致发布集成测试只允许本机 quant_worker_test 隔离库")
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
        self.tempdir = TemporaryDirectory()
        self.artifact_root = Path(self.tempdir.name)
        self.github = FakeGitHubClient()
        self.readback = LocalReadbackClient(self.Session, self.artifact_root)
        self._start_archive_validator()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_postgres_advisory_lock_serializes_concurrent_publishers(self) -> None:
        class SlowGitHubClient(FakeGitHubClient):
            def __init__(self) -> None:
                super().__init__()
                self.guard = Lock()
                self.active_lists = 0
                self.max_active_lists = 0

            def list_comments(self, issue_number: int) -> list[dict]:
                with self.guard:
                    self.active_lists += 1
                    self.max_active_lists = max(
                        self.max_active_lists, self.active_lists
                    )
                try:
                    time.sleep(0.15)
                    return super().list_comments(issue_number)
                finally:
                    with self.guard:
                        self.active_lists -= 1

        self.github = SlowGitHubClient()
        formal_id, run_id, issue_number = self.seed_research(60)
        draft = self.draft(run_id)
        barrier = Barrier(3)
        projections = []
        errors = []

        def publish() -> None:
            barrier.wait()
            try:
                projections.append(self.publish(formal_id, draft))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [Thread(target=publish), Thread(target=publish)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(projections), 2)
        self.assertEqual(projections[0].publication_id, projections[1].publication_id)
        self.assertEqual(self.github.max_active_lists, 1)
        self.assertEqual(len(self.github.comments[issue_number]), 1)
        with self.Session() as db:
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchEvaluation)), 1
            )
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ResearchPublication)), 1
            )


if __name__ == "__main__":
    unittest.main()
