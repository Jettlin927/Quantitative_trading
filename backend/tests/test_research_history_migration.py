from __future__ import annotations

from alembic import command
from contextlib import redirect_stdout
from datetime import datetime
import gzip
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from backend.app import research_history_migration as history_migration
from backend.app.database import Base, alembic_config
from backend.app.models import (
    FormalResearch,
    FrozenResearchPlan,
    ResearchEvaluation,
    ResearchEvaluationRun,
    ResearchPlanApproval,
    ResearchPublication,
    ResearchRun,
    StrategyDefinition,
)
from backend.app.research_history_migration import (
    HistorySource,
    ResearchHistoryMigrationConflict,
    apply_history_migration,
    build_history_migration_plan,
    load_history_source,
    migration_report,
    render_migration_report_markdown,
)
from backend.app.research_analytics import get_publication_analytics
from backend.app.quant_research.manifest import build_result_fingerprint
from scripts.research import migrate_research_history as migration_cli


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "configs" / "research-history-migration-v1.json"


class ResearchHistoryMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.source = load_history_source(REPO_ROOT, CONTRACT_PATH)

    def tearDown(self) -> None:
        self.engine.dispose()

    def seed_runs(self, *, corrupt_first_fingerprint: bool = False) -> list[str]:
        declared = [
            (research.strategy_id, research.code_commit, identity)
            for research in self.source.current_research
            for identity in research.run_identities
        ]
        self.assertEqual(len(declared), 16)
        unpublished_ids: list[str] = []
        with Session(self.engine) as db:
            for index, (strategy_id, code_commit, identity) in enumerate(declared):
                fingerprint = identity.result_fingerprint
                if index == 0 and corrupt_first_fingerprint:
                    fingerprint = "0" * 64
                db.add(
                    self.make_run(
                        identity.run_id,
                        strategy_id,
                        fingerprint,
                        "succeeded",
                        reproducibility_key=identity.reproducibility_key,
                        code_commit=code_commit,
                    )
                )

            for index in range(34):
                run_id = str(uuid5(NAMESPACE_URL, f"history-fixture-unpublished-{index}"))
                unpublished_ids.append(run_id)
                fingerprint = f"{index + 100:064x}"
                if index == 0:
                    fingerprint = declared[0][2].result_fingerprint
                status = "failed" if index % 5 == 0 else "succeeded"
                db.add(
                    self.make_run(
                        run_id,
                        "unpublished_fixture_strategy",
                        fingerprint if status == "succeeded" else None,
                        status,
                    )
                )
            db.commit()
        return unpublished_ids

    @staticmethod
    def make_run(
        run_id: str,
        strategy_id: str,
        result_fingerprint: str | None,
        status: str,
        *,
        reproducibility_key: str | None = None,
        code_commit: str = "d" * 40,
    ) -> ResearchRun:
        return ResearchRun(
            run_id=run_id,
            formal_research_id=None,
            reproducibility_key=reproducibility_key,
            strategy_id=strategy_id,
            status=status,
            stage="finalized" if status == "succeeded" else "failed",
            config={},
            config_sha256="c" * 64,
            data_snapshot_id=None,
            code_commit=code_commit,
            environment_sha256="e" * 64,
            random_seed=7,
            metrics={},
            result_fingerprint=result_fingerprint,
            artifact_root=f"outputs/research-runs/{run_id}",
        )

    def test_source_contract_freezes_four_researches_and_three_legacy_archives(self) -> None:
        self.assertEqual(self.source.current_report_count, 3)
        self.assertEqual(len(self.source.current_research), 4)
        self.assertEqual(len(self.source.legacy_archives), 3)
        self.assertEqual(
            {item.conclusion for item in self.source.current_research}, {"不通过"}
        )
        self.assertEqual(
            sum(len(item.run_identities) for item in self.source.current_research), 16
        )
        self.assertTrue(all(item.archive_class == "legacy" for item in self.source.legacy_archives))
        self.assertTrue(all(item.structured_conclusion is None for item in self.source.legacy_archives))
        artifacts_by_id = {
            item.result_set_id: item.artifact_refs for item in self.source.legacy_archives
        }
        self.assertEqual(len(artifacts_by_id["b1-standard-phased-backtest-20260627"]), 2)
        self.assertEqual(len(artifacts_by_id["ma-trend-reversal-20260629"]), 6)
        self.assertEqual(len(artifacts_by_id["value-sector-stopfall-20260629"]), 7)
        self.assertTrue(
            any(
                ref["uri"].endswith("/signals.csv")
                for refs in artifacts_by_id.values()
                for ref in refs
            )
        )

        by_strategy = {item.strategy_id: item for item in self.source.current_research}
        low_volatility = by_strategy["etf_low_volatility_gate"]
        self.assertIn("月末 ETF 实现方差", low_volatility.economic_thesis)
        self.assertNotIn("预期收益不会随方差一比一上升", low_volatility.economic_thesis)
        self.assertTrue(low_volatility.follow_up_recommendations)
        self.assertIn(
            "待证伪的新研究假设",
            low_volatility.follow_up_recommendations[0]["statement"],
        )
        trend = by_strategy["etf_trend_120d"]
        self.assertTrue(trend.supporting_evidence)
        self.assertTrue(trend.opposing_evidence)
        self.assertTrue(trend.missing_evidence)
        b1 = by_strategy["a_share_b1_trend_pullback"]
        limitation_statements = [item.get("statement", "") for item in b1.limitations]
        self.assertTrue(any("房地产" in statement for statement in limitation_statements))
        self.assertTrue(any("不在当前" in statement for statement in limitation_statements))

    def test_source_fingerprint_includes_current_html_reports(self) -> None:
        original_file_sha256 = history_migration._file_sha256
        target = (
            REPO_ROOT
            / self.source.current_research[0].report_uri.removeprefix("repo://")
        ).resolve()

        def changed_report_sha256(path: Path) -> str:
            if path.resolve() == target:
                return "f" * 64
            return original_file_sha256(path)

        with patch.object(
            history_migration,
            "_file_sha256",
            side_effect=changed_report_sha256,
        ):
            changed = load_history_source(REPO_ROOT, CONTRACT_PATH)

        self.assertNotEqual(changed.source_fingerprint, self.source.source_fingerprint)

    def test_plan_matches_only_complete_reproducible_run_identity(self) -> None:
        self.seed_runs(corrupt_first_fingerprint=True)
        with Session(self.engine) as db:
            plan = build_history_migration_plan(db, self.source)

        self.assertEqual(plan.source_run_count, 50)
        self.assertEqual(plan.declared_run_count, 16)
        self.assertEqual(plan.matched_run_count, 15)
        self.assertEqual(plan.mismatched_run_count, 1)
        self.assertEqual(plan.missing_run_count, 0)
        self.assertEqual(plan.unpublished_run_count, 35)
        self.assertEqual(len(plan.source_inventory_sha256), 64)
        self.assertEqual(len(plan.migration_fingerprint), 64)

    def test_code_commit_and_reproducibility_drift_stay_unpublished(self) -> None:
        self.seed_runs()
        first, second = list(
            identity
            for research in self.source.current_research
            for identity in research.run_identities
        )[:2]
        with Session(self.engine) as db:
            db.get(ResearchRun, first.run_id).code_commit = "f" * 40
            db.get(ResearchRun, second.run_id).reproducibility_key = "f" * 64
            db.commit()
            plan = build_history_migration_plan(db, self.source)

        self.assertEqual(plan.matched_run_count, 14)
        self.assertEqual(plan.mismatched_run_count, 2)
        self.assertEqual(plan.unpublished_run_count, 36)
        reasons = {item.run_id: item.reason for item in plan.mismatched_runs}
        self.assertIn("代码提交不一致", reasons[first.run_id])
        self.assertIn("复现键不一致", reasons[second.run_id])

    def test_apply_is_idempotent_and_never_promotes_legacy_status_ok(self) -> None:
        unpublished_ids = self.seed_runs()
        with Session(self.engine) as db:
            plan = build_history_migration_plan(db, self.source)
            first = apply_history_migration(db, plan)
            db.commit()

        self.assertEqual(plan.source_run_count, 50)
        self.assertEqual(plan.matched_run_count, 16)
        self.assertEqual(plan.unpublished_run_count, 34)
        self.assertGreater(first.created_total, 0)

        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(StrategyDefinition)), 7)
            self.assertEqual(db.scalar(select(func.count()).select_from(FrozenResearchPlan)), 4)
            self.assertEqual(db.scalar(select(func.count()).select_from(ResearchPlanApproval)), 4)
            self.assertEqual(db.scalar(select(func.count()).select_from(FormalResearch)), 4)
            self.assertEqual(db.scalar(select(func.count()).select_from(ResearchEvaluation)), 4)
            self.assertEqual(db.scalar(select(func.count()).select_from(ResearchEvaluationRun)), 16)
            self.assertEqual(db.scalar(select(func.count()).select_from(ResearchPublication)), 4)

            approvals = db.scalars(select(ResearchPlanApproval)).all()
            self.assertTrue(all(item.action == "historical_import" for item in approvals))
            self.assertTrue(all(item.comment_id is None for item in approvals))
            self.assertTrue(all(item.source_uri for item in approvals))
            researches = db.scalars(select(FormalResearch)).all()
            self.assertTrue(all(item.origin == "historical_import" for item in researches))
            self.assertTrue(all(item.phase == "stopped" for item in researches))
            evaluations = db.scalars(select(ResearchEvaluation)).all()
            self.assertEqual({item.conclusion for item in evaluations}, {"不通过"})
            publications = db.scalars(select(ResearchPublication)).all()
            self.assertTrue(all(item.status == "pending" for item in publications))
            self.assertTrue(all(item.published_at is None for item in publications))

            legacy = db.scalars(
                select(StrategyDefinition).where(
                    StrategyDefinition.strategy_id.like("legacy_%")
                )
            ).all()
            self.assertEqual(len(legacy), 3)
            self.assertTrue(all(item.lifecycle_status == "已归档" for item in legacy))
            self.assertTrue(
                all(item.metadata_json["structuredConclusion"] is None for item in legacy)
            )
            self.assertTrue(
                all(db.get(ResearchRun, run_id).formal_research_id is None for run_id in unpublished_ids)
            )

            second_plan = build_history_migration_plan(db, self.source)
            second = apply_history_migration(db, second_plan)
            db.commit()

        self.assertEqual(second.created_total, 0)
        self.assertGreater(second.unchanged_total, 0)
        report = migration_report(second_plan, second, mode="apply", committed=True)
        markdown = render_migration_report_markdown(report)
        self.assertEqual(report["迁移指纹"], plan.migration_fingerprint)
        self.assertIn("来源运行：50", markdown)
        self.assertIn("可靠关联：16", markdown)
        self.assertIn("未发布运行：34", markdown)
        self.assertIn("legacy 不推断结论", markdown)
        self.assertIn("统一发布记录保持 pending", markdown)

    def test_publication_analytics_reads_the_same_historical_evaluation(self) -> None:
        self.seed_runs()
        with TemporaryDirectory() as temporary_name:
            with Session(self.engine) as db:
                plan = build_history_migration_plan(db, self.source)
                apply_history_migration(db, plan)
                db.commit()

                publication_id = db.scalar(
                    select(ResearchPublication.id)
                    .join(
                        FormalResearch,
                        FormalResearch.id == ResearchPublication.formal_research_id,
                    )
                    .join(
                        FrozenResearchPlan,
                        FrozenResearchPlan.id == FormalResearch.plan_id,
                    )
                    .where(FrozenResearchPlan.strategy_id == "etf_trend_120d")
                )
                run = db.get(
                    ResearchRun,
                    "73c82e27-754f-4f6a-bc85-4fc43c4b5be3",
                )
                root = Path(temporary_name)
                nav_path = root / "nav.csv.gz"
                with gzip.open(nav_path, "wt", encoding="utf-8", newline="") as handle:
                    handle.write(
                        "trade_date,nav,one_way_turnover,transaction_cost_rate,"
                        "gross_exposure,cash_weight\n"
                        "2025-01-02,1.0,0.0,0.0,0.0,1.0\n"
                        "2025-01-03,0.9,0.5,0.01,0.5,0.5\n"
                    )
                with gzip.open(nav_path, "rb") as handle:
                    content_sha256 = sha256(handle.read()).hexdigest()
                targets_path = root / "targets.csv.gz"
                targets_path.write_bytes(gzip.compress(b"targets"))
                metrics_path = root / "metrics.json"
                metrics_path.write_text("{}", encoding="utf-8")
                artifact_hashes = {
                    "nav.csv.gz": {
                        "fileSha256": sha256(nav_path.read_bytes()).hexdigest(),
                        "contentSha256": content_sha256,
                    },
                    "targets.csv.gz": {
                        "fileSha256": sha256(targets_path.read_bytes()).hexdigest(),
                        "contentSha256": sha256(b"targets").hexdigest(),
                    },
                    "metrics.json": {
                        "fileSha256": sha256(metrics_path.read_bytes()).hexdigest(),
                        "contentSha256": sha256(metrics_path.read_bytes()).hexdigest(),
                    },
                }
                run.artifact_root = str(root)
                run.result_fingerprint = build_result_fingerprint(artifact_hashes)
                (root / "manifest.json").write_text(
                    json.dumps(
                        {
                            "artifactSchemaVersion": 2,
                            "runId": run.run_id,
                            "strategyId": run.strategy_id,
                            "codeCommit": run.code_commit,
                            "configSha256": run.config_sha256,
                            "randomSeed": run.random_seed,
                            "reproducibilityKey": run.reproducibility_key,
                            "resultFingerprint": run.result_fingerprint,
                            "environment": {"sha256": run.environment_sha256},
                            "dataSnapshot": {"snapshotId": run.data_snapshot_id},
                            "artifactHashes": artifact_hashes,
                        }
                    ),
                    encoding="utf-8",
                )
                db.commit()
                analytics = get_publication_analytics(db, str(publication_id))

        self.assertIsNotNone(analytics)
        self.assertEqual(analytics.data_status, "complete")
        self.assertEqual(
            analytics.primary_run_id,
            "73c82e27-754f-4f6a-bc85-4fc43c4b5be3",
        )
        self.assertAlmostEqual(
            analytics.metrics["totalReturn"],
            0.006829920869796613,
        )
        self.assertAlmostEqual(
            analytics.metrics["benchmarkTotalReturn"],
            1.8509749999999991,
        )
        self.assertTrue(analytics.yearly)
        self.assertTrue(analytics.regimes)
        self.assertEqual(analytics.availability["metrics"]["status"], "complete")
        self.assertEqual(analytics.availability["nav"]["status"], "complete")
        self.assertEqual(analytics.chart_series["nav"][-1]["value"], 0.9)
        self.assertEqual(
            analytics.chart_series["cumulativeTurnover"][-1]["value"], 0.5
        )

    def test_apply_keeps_mismatched_canonical_run_unpublished_and_records_gap(self) -> None:
        self.seed_runs(corrupt_first_fingerprint=True)
        mismatched_run_id = self.source.current_research[0].run_identities[0].run_id
        with Session(self.engine) as db:
            plan = build_history_migration_plan(db, self.source)
            apply_history_migration(db, plan)
            db.commit()
        with Session(self.engine) as db:
            self.assertIsNone(db.get(ResearchRun, mismatched_run_id).formal_research_id)
            evaluations = db.scalars(select(ResearchEvaluation)).all()
            recorded_gaps = [
                item["statement"]
                for evaluation in evaluations
                for item in evaluation.missing_evidence
                if item.get("origin") == "historical_import"
            ]
        self.assertTrue(any(mismatched_run_id in statement for statement in recorded_gaps))

    def test_transaction_can_be_rehearsed_and_rolled_back_without_partial_import(self) -> None:
        self.seed_runs()
        with Session(self.engine) as db:
            plan = build_history_migration_plan(db, self.source)
            apply_history_migration(db, plan)
            db.flush()
            self.assertEqual(db.scalar(select(func.count()).select_from(FormalResearch)), 4)
            db.rollback()

        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(StrategyDefinition)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(FormalResearch)), 0)
            self.assertEqual(
                db.scalar(
                    select(func.count())
                    .select_from(ResearchRun)
                    .where(ResearchRun.formal_research_id.is_not(None))
                ),
                0,
            )

    def test_cli_preview_is_read_only_and_apply_requires_exact_confirmation(self) -> None:
        self.seed_runs()
        with patch.object(migration_cli, "engine", self.engine), redirect_stdout(StringIO()):
            self.assertEqual(
                migration_cli.main(
                    ["--mode", "preview", "--contract", str(CONTRACT_PATH)]
                ),
                0,
            )
            with self.assertRaisesRegex(SystemExit, "拒绝 apply"):
                migration_cli.main(
                    ["--mode", "apply", "--contract", str(CONTRACT_PATH)]
                )
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(FormalResearch)), 0)

    def test_apply_rejects_inventory_drift_after_preview(self) -> None:
        self.seed_runs()
        with Session(self.engine) as db:
            plan = build_history_migration_plan(db, self.source)
            db.add(
                self.make_run(
                    "ffffffff-ffff-4fff-8fff-ffffffffffff",
                    "late_fixture_strategy",
                    "f" * 64,
                    "succeeded",
                )
            )
            db.commit()
            with self.assertRaisesRegex(
                ResearchHistoryMigrationConflict,
                "来源运行清单在 preview 后发生变化",
            ):
                apply_history_migration(db, plan)

    def test_apply_rejects_unexpected_existing_formal_research_links(self) -> None:
        unpublished_ids = self.seed_runs()
        with Session(self.engine) as db:
            db.get(ResearchRun, unpublished_ids[0]).formal_research_id = (
                "ffffffff-ffff-4fff-8fff-ffffffffffff"
            )
            db.commit()
            plan = build_history_migration_plan(db, self.source)
            self.assertEqual(plan.unexpected_linked_run_count, 1)
            self.assertEqual(plan.unpublished_run_count, 33)
            with self.assertRaisesRegex(
                ResearchHistoryMigrationConflict,
                "既有正式研究关联",
            ):
                apply_history_migration(db, plan)

    def test_pending_publication_timestamp_drift_is_not_idempotent(self) -> None:
        self.seed_runs()
        with Session(self.engine) as db:
            plan = build_history_migration_plan(db, self.source)
            apply_history_migration(db, plan)
            db.commit()
            publication_id = db.scalar(select(ResearchPublication.id).limit(1))
            db.execute(
                ResearchPublication.__table__.update()
                .where(ResearchPublication.id == publication_id)
                .values(published_at=datetime(2026, 7, 19)),
            )
            db.commit()
            repeated_plan = build_history_migration_plan(db, self.source)
            with self.assertRaisesRegex(
                ResearchHistoryMigrationConflict,
                "published_at",
            ):
                apply_history_migration(db, repeated_plan)


@unittest.skipUnless(
    os.getenv("TEST_POSTGRES_URL"),
    "TEST_POSTGRES_URL 未配置，跳过研究历史迁移 PostgreSQL 演练",
)
class ResearchHistoryMigrationPostgresTest(unittest.TestCase):
    def test_apply_rollback_and_idempotency_on_isolated_postgres(self) -> None:
        database_url = os.environ["TEST_POSTGRES_URL"]
        parsed = make_url(database_url)
        self.assertIn(parsed.host, {"127.0.0.1", "localhost"})
        self.assertEqual(parsed.database, "quant_migration_test")
        engine = create_engine(database_url, pool_pre_ping=True)
        source = load_history_source(REPO_ROOT, CONTRACT_PATH)
        try:
            self._reset(engine)
            with engine.connect() as connection:
                command.upgrade(alembic_config(connection), "head")
            self._seed_runs(engine, source)

            with Session(engine) as db:
                plan = build_history_migration_plan(db, source)
                rehearsal = apply_history_migration(db, plan)
                db.flush()
                self.assertGreater(rehearsal.created_total, 0)
                self.assertEqual(
                    db.scalar(select(func.count()).select_from(FormalResearch)), 4
                )
                db.rollback()
            with Session(engine) as db:
                self.assertEqual(
                    db.scalar(select(func.count()).select_from(FormalResearch)), 0
                )
                self.assertEqual(
                    db.scalar(
                        select(func.count())
                        .select_from(ResearchRun)
                        .where(ResearchRun.formal_research_id.is_not(None))
                    ),
                    0,
                )

            with Session(engine) as db:
                plan = build_history_migration_plan(db, source)
                applied = apply_history_migration(db, plan)
                db.commit()
            self.assertGreater(applied.created_total, 0)
            with Session(engine) as db:
                repeated_plan = build_history_migration_plan(db, source)
                repeated = apply_history_migration(db, repeated_plan)
                db.commit()
                self.assertEqual(repeated.created_total, 0)
                self.assertEqual(
                    db.scalar(select(func.count()).select_from(ResearchEvaluationRun)), 16
                )
                self.assertEqual(
                    db.scalar(select(func.count()).select_from(ResearchPublication)), 4
                )
                self.assertEqual(
                    set(db.scalars(select(FormalResearch.phase)).all()),
                    {"stopped"},
                )
                self.assertEqual(
                    set(db.scalars(select(ResearchPublication.status)).all()),
                    {"pending"},
                )
                self.assertTrue(
                    all(
                        value is None
                        for value in db.scalars(
                            select(ResearchPublication.published_at)
                        ).all()
                    )
                )
        finally:
            self._reset(engine)
            engine.dispose()

    @staticmethod
    def _seed_runs(engine, source: HistorySource) -> None:
        declared = [
            (research.strategy_id, research.code_commit, identity)
            for research in source.current_research
            for identity in research.run_identities
        ]
        with Session(engine) as db:
            for strategy_id, code_commit, identity in declared:
                db.add(
                    ResearchHistoryMigrationTest.make_run(
                        identity.run_id,
                        strategy_id,
                        identity.result_fingerprint,
                        "succeeded",
                        reproducibility_key=identity.reproducibility_key,
                        code_commit=code_commit,
                    )
                )
            for index in range(34):
                run_id = str(uuid5(NAMESPACE_URL, f"history-fixture-unpublished-{index}"))
                status = "failed" if index % 5 == 0 else "succeeded"
                db.add(
                    ResearchHistoryMigrationTest.make_run(
                        run_id,
                        "unpublished_fixture_strategy",
                        f"{index + 100:064x}" if status == "succeeded" else None,
                        status,
                    )
                )
            db.commit()

    @staticmethod
    def _reset(engine) -> None:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS private_workbench CASCADE"))
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))


if __name__ == "__main__":
    unittest.main()
