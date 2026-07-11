from __future__ import annotations

import unittest
from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.database import Base
from backend.app.data_quality.contracts import QualityCheckContract, QualityRuleResult, summarize_quality_status
from backend.app.data_quality.rules import evaluate_quality_rules
from backend.app.data_quality.runner import run_data_quality_check
from backend.app import main
from backend.app.models import (
    DataQualityResult,
    DataQualityRun,
    DataSnapshot,
    Fund,
    FundAdjustFactor,
    FundDailyBar,
    Index,
    IndexDailyBar,
    StockAdjustFactor,
    StockDailyBar,
    StockFinancialIndicator,
    StockLimitPrice,
    StockListing,
    StockSuspendEvent,
    TradeCalendar,
)
from backend.app.quant_research.readiness import evaluate_quality_run_readiness, evaluate_research_readiness
from backend.app.schemas import DataQualityRunRequest
from scripts.research.check_data_quality import main as quality_cli_main


class DataQualityRulesTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self._seed_complete_slice()

    def tearDown(self):
        self.engine.dispose()

    def _seed_complete_slice(self) -> None:
        with Session(self.engine) as db:
            db.add_all(
                [
                    TradeCalendar(exchange="SSE", cal_date=date(2026, 1, 2), is_open=True),
                    TradeCalendar(exchange="SSE", cal_date=date(2026, 1, 3), is_open=False),
                    TradeCalendar(exchange="SSE", cal_date=date(2026, 1, 5), is_open=True),
                    StockListing(
                        ts_code="000001.SZ",
                        symbol="000001",
                        name="测试股票",
                        exchange="SZSE",
                        list_status="L",
                        list_date=date(1991, 4, 3),
                    ),
                    Index(ts_code="000300.SH", name="测试基准", market="CSI"),
                ]
            )
            for trade_date, close in [(date(2026, 1, 2), 10), (date(2026, 1, 5), 10.5)]:
                db.add(
                    StockDailyBar(
                        ts_code="000001.SZ",
                        trade_date=trade_date,
                        open=close,
                        high=close + 0.5,
                        low=close - 0.5,
                        close=close,
                        vol=100,
                        amount=1000,
                    )
                )
                db.add(StockAdjustFactor(ts_code="000001.SZ", trade_date=trade_date, adj_factor=1))
                db.add(
                    StockLimitPrice(
                        ts_code="000001.SZ",
                        trade_date=trade_date,
                        pre_close=close,
                        up_limit=close * 1.1,
                        down_limit=close * 0.9,
                    )
                )
                db.add(
                    IndexDailyBar(
                        ts_code="000300.SH",
                        trade_date=trade_date,
                        open=4000,
                        high=4010,
                        low=3990,
                        close=4005,
                        vol=1000,
                        amount=10000,
                    )
                )
            db.commit()

    @staticmethod
    def contract(**overrides) -> QualityCheckContract:
        values = {
            "scope": "a_share_cross_section",
            "start_date": date(2026, 1, 2),
            "end_date": date(2026, 1, 5),
            "universe": ["000001.SZ"],
            "required_datasets": [],
            "benchmark": "000300.SH",
            "universe_type": "explicit_snapshot",
            "universe_source": "backend/tests/fixtures/quality-universe.txt",
            "universe_as_of_date": date(2026, 1, 2),
            "statement_timeout_ms": 5000,
        }
        values.update(overrides)
        return QualityCheckContract.create(**values)

    def evaluate(self, contract: QualityCheckContract | None = None) -> list[QualityRuleResult]:
        with Session(self.engine) as db:
            return evaluate_quality_rules(db, contract or self.contract())

    def test_complete_research_slice_is_ready(self):
        results = self.evaluate()

        self.assertEqual(summarize_quality_status(results), "ready")
        self.assertTrue(results)
        self.assertTrue(all(result.status == "passed" for result in results))
        self.assertTrue(
            {
                "schema",
                "uniqueness",
                "domain",
                "referential",
                "calendar",
                "value",
                "adjustment",
                "freshness",
                "benchmark",
            }.issubset({result.rule_id.split(".", 1)[0] for result in results})
        )

    def test_etf_time_series_scope_is_evaluated_independently(self):
        with Session(self.engine) as db:
            db.add(Fund(ts_code="510300.SH", name="测试ETF", market="E", fund_type="ETF", list_date=date(2012, 5, 28)))
            for trade_date, close in [(date(2026, 1, 2), 4), (date(2026, 1, 5), 4.1)]:
                db.add(
                    FundDailyBar(
                        ts_code="510300.SH",
                        trade_date=trade_date,
                        open=close,
                        high=close + 0.1,
                        low=close - 0.1,
                        close=close,
                        vol=100,
                        amount=1000,
                    )
                )
                db.add(FundAdjustFactor(ts_code="510300.SH", trade_date=trade_date, adj_factor=1))
            db.commit()

        contract = self.contract(
            scope="etf_time_series",
            universe=["510300.SH"],
            universe_source="backend/tests/fixtures/etf-universe.txt",
        )
        results = self.evaluate(contract)

        self.assertEqual(summarize_quality_status(results), "ready")
        self.assertTrue(all(result.status == "passed" for result in results))

    def test_unlisted_limit_prices_are_warning_and_samples_are_capped(self):
        with Session(self.engine) as db:
            for index in range(25):
                db.add(
                    StockLimitPrice(
                        ts_code=f"5{index:05d}.SH",
                        trade_date=date(2026, 1, 2),
                        pre_close=1,
                        up_limit=1.1,
                        down_limit=0.9,
                    )
                )
            db.commit()

        results = self.evaluate()
        domain = next(
            result
            for result in results
            if result.rule_id == "domain.unlisted_codes" and result.table_name == "stock_limit_prices"
        )

        self.assertEqual(summarize_quality_status(results), "ready_with_warnings")
        self.assertEqual(domain.status, "warning")
        self.assertEqual(domain.failed_rows, 25)
        self.assertLessEqual(len(domain.sample_issues), 20)

    def test_missing_adjust_factor_blocks_research_slice(self):
        with Session(self.engine) as db:
            row = db.scalar(
                select(StockAdjustFactor).where(
                    StockAdjustFactor.ts_code == "000001.SZ",
                    StockAdjustFactor.trade_date == date(2026, 1, 5),
                )
            )
            db.delete(row)
            db.commit()

        results = self.evaluate()
        coverage = next(
            result
            for result in results
            if result.rule_id == "calendar.adjust_factor_coverage" and result.table_name == "stock_adjust_factors"
        )

        self.assertEqual(summarize_quality_status(results), "blocked")
        self.assertEqual(coverage.status, "blocked")
        self.assertEqual(coverage.failed_rows, 1)
        self.assertEqual(coverage.sample_issues[0]["tsCode"], "000001.SZ")
        self.assertEqual(coverage.sample_issues[0]["tradeDate"], "2026-01-05")

    def test_missing_limit_price_blocks_research_slice(self):
        with Session(self.engine) as db:
            row = db.scalar(
                select(StockLimitPrice).where(
                    StockLimitPrice.ts_code == "000001.SZ",
                    StockLimitPrice.trade_date == date(2026, 1, 5),
                )
            )
            db.delete(row)
            db.commit()

        results = self.evaluate()
        coverage = next(
            result
            for result in results
            if result.rule_id == "calendar.limit_price_coverage" and result.table_name == "stock_limit_prices"
        )
        self.assertEqual(coverage.status, "blocked")
        self.assertEqual(coverage.failed_rows, 1)

    def test_missing_benchmark_and_universe_provenance_are_explicit(self):
        missing_benchmark = self.evaluate(self.contract(benchmark="000905.SH"))
        benchmark = next(result for result in missing_benchmark if result.rule_id == "benchmark.overlap")
        self.assertEqual(benchmark.status, "blocked")

        static_universe = self.evaluate(self.contract(universe_type="static_current"))
        survivorship = next(result for result in static_universe if result.rule_id == "universe.survivorship_risk")
        self.assertEqual(survivorship.status, "blocked")
        self.assertEqual(summarize_quality_status(static_universe), "blocked")

        missing_provenance = self.evaluate(self.contract(universe_source=None, universe_as_of_date=None))
        provenance = next(result for result in missing_provenance if result.rule_id == "universe.provenance")
        self.assertEqual(provenance.status, "blocked")
        self.assertEqual(provenance.failed_rows, 2)

        future_snapshot = self.evaluate(self.contract(universe_as_of_date=date(2026, 1, 5)))
        future = next(result for result in future_snapshot if result.rule_id == "universe.provenance")
        self.assertEqual(future.status, "blocked")

    def test_invalid_prices_are_blocked_and_repeated_counts_are_deterministic(self):
        with Session(self.engine) as db:
            row = db.scalar(
                select(StockDailyBar).where(
                    StockDailyBar.ts_code == "000001.SZ",
                    StockDailyBar.trade_date == date(2026, 1, 5),
                )
            )
            row.high = 9
            db.commit()

        first = self.evaluate()
        second = self.evaluate()
        first_value = next(result for result in first if result.rule_id == "value.ohlcv_sanity" and result.table_name == "stock_daily_bars")
        second_value = next(result for result in second if result.rule_id == "value.ohlcv_sanity" and result.table_name == "stock_daily_bars")

        self.assertEqual(first_value.status, "blocked")
        self.assertEqual(first_value.failed_rows, second_value.failed_rows)
        self.assertEqual(first_value.sample_issues, second_value.sample_issues)

    def test_intraday_suspend_does_not_hide_missing_daily_bar(self):
        with Session(self.engine) as db:
            row = db.scalar(
                select(StockDailyBar).where(
                    StockDailyBar.ts_code == "000001.SZ",
                    StockDailyBar.trade_date == date(2026, 1, 5),
                )
            )
            db.delete(row)
            db.add(
                StockSuspendEvent(
                    ts_code="000001.SZ",
                    trade_date=date(2026, 1, 5),
                    suspend_type="S",
                    suspend_timing="13:00-14:00",
                )
            )
            db.commit()

        intraday = self.evaluate()
        coverage = next(result for result in intraday if result.rule_id == "calendar.daily_bar_coverage")
        self.assertEqual(coverage.status, "blocked")

        with Session(self.engine) as db:
            event = db.scalar(select(StockSuspendEvent))
            event.suspend_timing = "全天"
            db.commit()
        full_day = self.evaluate()
        coverage = next(result for result in full_day if result.rule_id == "calendar.daily_bar_coverage")
        self.assertEqual(coverage.status, "passed")

    def test_financial_coverage_uses_next_open_date_after_announcement(self):
        with Session(self.engine) as db:
            db.add(
                StockFinancialIndicator(
                    ts_code="000001.SZ",
                    ann_date=date(2026, 1, 2),
                    end_date=date(2025, 12, 31),
                )
            )
            db.commit()

        available = self.evaluate(self.contract(required_datasets=["stock_financial_indicators"]))
        coverage = next(result for result in available if result.rule_id == "calendar.financial_coverage")
        revision = next(result for result in available if result.rule_id == "point_in_time.financial_revision_history")
        self.assertEqual(coverage.status, "passed")
        self.assertEqual(revision.status, "blocked")
        self.assertEqual(summarize_quality_status(available), "blocked")
        with Session(self.engine) as db:
            report = run_data_quality_check(
                db,
                self.contract(required_datasets=["stock_financial_indicators"]),
            )
        self.assertIn("financial_revision_history_unavailable", report["summary"]["limitations"])

        same_day = self.evaluate(
            self.contract(
                end_date=date(2026, 1, 2),
                required_datasets=["stock_financial_indicators"],
            )
        )
        coverage = next(result for result in same_day if result.rule_id == "calendar.financial_coverage")
        self.assertEqual(coverage.status, "blocked")

        without_financials = self.evaluate()
        self.assertFalse(any(result.rule_id.startswith("point_in_time.") for result in without_financials))

    def test_null_volume_or_amount_is_reported_as_warning_not_hidden(self):
        with Session(self.engine) as db:
            row = db.scalar(
                select(StockDailyBar).where(
                    StockDailyBar.ts_code == "000001.SZ",
                    StockDailyBar.trade_date == date(2026, 1, 5),
                )
            )
            row.vol = None
            row.amount = None
            db.commit()

        results = self.evaluate()
        missing = next(
            result
            for result in results
            if result.rule_id == "value.volume_amount_missing" and result.table_name == "stock_daily_bars"
        )
        self.assertEqual(missing.status, "warning")
        self.assertEqual(missing.failed_rows, 1)
        self.assertEqual(summarize_quality_status(results), "ready_with_warnings")

    def test_failed_is_not_collapsed_into_blocked(self):
        blocked = QualityRuleResult.blocked("calendar.coverage", "stock_daily_bars", failed_rows=1)
        failed = QualityRuleResult.failed("engine.execution", "data_quality_runs", "statement timeout")

        self.assertEqual(summarize_quality_status([blocked]), "blocked")
        self.assertEqual(summarize_quality_status([blocked, failed]), "failed")

    def test_runner_persists_results_and_execution_failure_separately(self):
        with Session(self.engine) as db:
            report = run_data_quality_check(db, self.contract(), code_commit="abc123")
            persisted = db.get(DataQualityRun, report["qualityRunId"])
            result_rows = list(db.scalars(select(DataQualityResult).where(DataQualityResult.run_id == persisted.id)).all())

            self.assertEqual(report["status"], "ready")
            json.dumps(report, ensure_ascii=False, allow_nan=False)
            self.assertEqual(persisted.status, "ready")
            self.assertEqual(persisted.code_commit, "abc123")
            self.assertEqual(len(result_rows), report["summary"]["resultCount"])

            def fail_evaluation(_db, _contract):
                raise TimeoutError("statement timeout")

            failed = run_data_quality_check(db, self.contract(), evaluator=fail_evaluation)
            failed_row = db.scalar(
                select(DataQualityResult).where(
                    DataQualityResult.run_id == failed["qualityRunId"],
                    DataQualityResult.rule_id == "engine.execution",
                )
            )
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed_row.status, "failed")

    def test_api_runs_quality_and_research_readiness_requires_quality_run(self):
        payload = DataQualityRunRequest(
            scope="a_share_cross_section",
            start_date=date(2026, 1, 2),
            end_date=date(2026, 1, 5),
            universe=["000001.SZ"],
            universe_source="backend/tests/fixtures/quality-universe.txt",
            universe_as_of_date=date(2026, 1, 2),
            benchmark="000300.SH",
        )
        with Session(self.engine) as db:
            report = main.create_data_quality_run(payload, db)
            fetched = main.get_data_quality_run(report["qualityRunId"], db)
            readiness = main.get_research_readiness_by_quality_run(report["qualityRunId"], db)

        paths = {route.path for route in main.app.routes}
        self.assertIn("/api/data-quality/runs", paths)
        self.assertIn("/api/data-quality/runs/{quality_run_id}", paths)
        self.assertIn("/api/research/readiness/{quality_run_id}", paths)
        self.assertEqual(fetched["qualityRunId"], report["qualityRunId"])
        self.assertEqual(readiness["level"], "research")
        self.assertEqual(readiness["status"], "ready")
        self.assertTrue(readiness["researchReady"])

    def test_cli_exit_codes_distinguish_ready_blocked_and_failed(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "quality.sqlite3"
            engine = create_engine(f"sqlite+pysqlite:///{db_path}")
            Base.metadata.create_all(engine)
            try:
                with Session(engine) as target, Session(self.engine) as source:
                    for row in source.scalars(select(TradeCalendar)).all():
                        target.add(
                            TradeCalendar(
                                exchange=row.exchange,
                                cal_date=row.cal_date,
                                is_open=row.is_open,
                                pretrade_date=row.pretrade_date,
                            )
                        )
                    for model in [StockListing, Index, StockDailyBar, StockAdjustFactor, StockLimitPrice, IndexDailyBar]:
                        for row in source.scalars(select(model)).all():
                            values = {
                                column.name: getattr(row, column.name)
                                for column in model.__table__.columns
                                if column.name != "id"
                            }
                            target.add(model(**values))
                    target.commit()
            finally:
                engine.dispose()

            args = [
                "--scope",
                "a_share_cross_section",
                "--start-date",
                "2026-01-02",
                "--end-date",
                "2026-01-05",
                "--universe",
                "000001.SZ",
                "--universe-source",
                "backend/tests/fixtures/quality-universe.txt",
                "--universe-as-of-date",
                "2026-01-02",
                "--benchmark",
                "000300.SH",
                "--database-url",
                f"sqlite+pysqlite:///{db_path}",
            ]
            with patch("builtins.print"):
                self.assertEqual(quality_cli_main(args), 0)

            engine = create_engine(f"sqlite+pysqlite:///{db_path}")
            try:
                with Session(engine) as db:
                    db.delete(
                        db.scalar(
                            select(StockAdjustFactor).where(
                                StockAdjustFactor.ts_code == "000001.SZ",
                                StockAdjustFactor.trade_date == date(2026, 1, 5),
                            )
                        )
                    )
                    db.commit()
            finally:
                engine.dispose()
            with patch("builtins.print"):
                self.assertEqual(quality_cli_main(args), 2)
                self.assertEqual(quality_cli_main([*args[:-1], "not-a-database-url"]), 3)


class DataQualityRegistryAndReadinessTest(unittest.TestCase):
    def test_registry_constraints_and_migration_parent_are_explicit(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "versions"
            / "0002_quality_and_snapshot_registry.py"
        ).read_text(encoding="utf-8")
        self.assertIn('down_revision = "0001_existing_schema_baseline"', migration)
        self.assertNotIn("op.drop_table", migration.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0])

        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        try:
            with Session(engine) as db:
                run = DataQualityRun(
                    id="constraint-run",
                    scope="a_share_cross_section",
                    start_date=date(2026, 1, 2),
                    end_date=date(2026, 1, 5),
                    universe_hash="c" * 64,
                    status="ready",
                    config={},
                    summary={},
                )
                db.add(run)
                db.commit()
                for _ in range(2):
                    db.add(
                        DataQualityResult(
                            run_id=run.id,
                            rule_id="schema.contract",
                            table_name="stock_daily_bars",
                            severity="info",
                            status="passed",
                            checked_rows=1,
                            failed_rows=0,
                            sample_issues=[],
                        )
                    )
                with self.assertRaises(IntegrityError):
                    db.commit()
                db.rollback()

                db.add(
                    DataQualityRun(
                        id="invalid-status",
                        scope="a_share_cross_section",
                        start_date=date(2026, 1, 2),
                        end_date=date(2026, 1, 5),
                        universe_hash="d" * 64,
                        status="pretend_ready",
                        config={},
                        summary={},
                    )
                )
                with self.assertRaises(IntegrityError):
                    db.commit()
        finally:
            engine.dispose()

    def test_registry_schema_and_inventory_research_contracts(self):
        self.assertIn("data_quality_runs", Base.metadata.tables)
        self.assertIn("data_quality_results", Base.metadata.tables)
        self.assertIn("data_snapshots", Base.metadata.tables)

        result_table = Base.metadata.tables["data_quality_results"]
        self.assertEqual(
            next(constraint for constraint in result_table.constraints if constraint.name == "uq_data_quality_result_run_rule_table").columns.keys(),
            ["run_id", "rule_id", "table_name"],
        )
        self.assertEqual(next(iter(result_table.c.run_id.foreign_keys)).target_fullname, "data_quality_runs.id")
        self.assertEqual(
            next(iter(Base.metadata.tables["data_snapshots"].c.quality_run_id.foreign_keys)).target_fullname,
            "data_quality_runs.id",
        )

        inventory = evaluate_research_readiness(
            "etf_time_series",
            {
                "trade_calendars",
                "funds",
                "fund_daily_bars",
                "fund_adjust_factors",
                "indices",
                "index_daily_bars",
            },
            {
                "trade_calendars": 1,
                "funds": 1,
                "fund_daily_bars": 1,
                "fund_adjust_factors": 1,
                "indices": 1,
                "index_daily_bars": 1,
            },
        )
        self.assertEqual(inventory["level"], "inventory")
        self.assertEqual(inventory["status"], "inventory_available")
        self.assertFalse(inventory["researchReady"])

        run = DataQualityRun(
            id="run-warning",
            scope="a_share_cross_section",
            start_date=date(2026, 1, 2),
            end_date=date(2026, 1, 5),
            universe_hash="a" * 64,
            status="ready_with_warnings",
            config={
                "requiredDatasets": [],
                "benchmark": "000300.SH",
                "universeType": "explicit_snapshot",
                "universeSource": "fixture",
                "universeAsOfDate": "2026-01-02",
            },
            summary={"limitations": ["static_current_universe"]},
        )
        warning = DataQualityResult(
            run_id=run.id,
            rule_id="domain.unlisted_codes",
            table_name="stock_limit_prices",
            severity="warning",
            status="warning",
            checked_rows=100,
            failed_rows=25,
            sample_issues=[{"tsCode": f"5{index:05d}.SH"} for index in range(25)],
        )
        self.assertEqual(len(warning.sample_issues), 20)
        research = evaluate_quality_run_readiness(run, [warning])
        self.assertEqual(research["level"], "research")
        self.assertEqual(research["status"], "ready_with_warnings")
        self.assertEqual(research["qualityRunId"], "run-warning")
        self.assertEqual(research["warnings"], ["domain.unlisted_codes:stock_limit_prices"])
        self.assertEqual(research["limitations"], ["static_current_universe"])

        snapshot = DataSnapshot(
            snapshot_id="b" * 64,
            quality_run_id=run.id,
            scope=run.scope,
            start_date=run.start_date,
            end_date=run.end_date,
            universe_hash=run.universe_hash,
            artifact_root="outputs/research-runs/snapshots/example",
            table_artifacts={},
            row_counts={},
            status="building",
        )
        self.assertEqual(snapshot.quality_run_id, run.id)


if __name__ == "__main__":
    unittest.main()
