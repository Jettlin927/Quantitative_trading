from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.data_quality.contracts import QualityCheckContract
from backend.app.database import Base
from backend.app.models import (
    DataQualityResult,
    DataQualityRun,
    Fund,
    FundAdjustFactor,
    FundDailyBar,
    Index,
    IndexDailyBar,
    TradeCalendar,
)
from backend.app.quant_research.etf_trend_baseline import (
    build_etf_trend_targets,
    validate_etf_trend_config,
)
from backend.app.quant_research.run_config import validate_run_config
from backend.app.quant_research.runner import reproduce_quant_research, run_quant_research
from backend.app.quant_research.snapshot import SnapshotCapacityPolicy
from backend.app.quant_research.universe import build_explicit_universe
from backend.tests.research_test_support import golden_run_config


class EtfTrendBaselineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        dates = pd.bdate_range("2025-01-02", periods=150)
        pd.DataFrame(
            {
                "exchange": "SSE",
                "cal_date": dates.strftime("%Y-%m-%d"),
                "is_open": 1,
            }
        ).to_csv(self.root / "trade_calendars.csv", index=False)
        closes = pd.Series(range(100, 250), dtype=float)
        pd.DataFrame(
            {
                "ts_code": "SYNETF.SZ",
                "trade_date": dates.strftime("%Y-%m-%d"),
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "pre_close": closes.shift(1).fillna(closes.iloc[0]),
                "change_amount": closes.diff().fillna(0),
                "pct_chg": closes.pct_change().fillna(0) * 100,
                "vol": 1000,
                "amount": 10000,
            }
        ).to_csv(self.root / "fund_daily_bars.csv", index=False)
        pd.DataFrame(
            {
                "ts_code": "SYNETF.SZ",
                "trade_date": dates.strftime("%Y-%m-%d"),
                "adj_factor": 1.0,
            }
        ).to_csv(self.root / "fund_adjust_factors.csv", index=False)
        self.config = golden_run_config("quality", "a" * 64)
        self.config.update(
            {
                "strategyId": "etf_trend_120d",
                "strategyVersion": "1",
                "warmupStart": dates[0].date().isoformat(),
                "startDate": dates[119].date().isoformat(),
                "endDate": dates[-1].date().isoformat(),
                "featureParameters": {"movingAverageWindow": 120},
                "targetWeightParameters": {
                    "rebalanceFrequency": "month_end",
                    "riskOnWeight": "1",
                    "riskOffWeight": "0",
                },
            }
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_fixed_120_day_month_end_signal_is_causal(self):
        targets = build_etf_trend_targets(self.root, self.config, compressed=False)
        self.assertFalse(targets.empty)
        self.assertTrue(targets["target_weight"].eq(1.0).all())
        self.assertTrue(targets["signal_date"].eq(targets["available_date"]).all())
        month_keys = targets["signal_date"].dt.to_period("M")
        self.assertFalse(month_keys.duplicated().any())

        extended = pd.read_csv(self.root / "fund_daily_bars.csv")
        future = extended.iloc[-1].copy()
        future["trade_date"] = "2027-01-04"
        future["close"] = 1
        extended = pd.concat([extended, pd.DataFrame([future])], ignore_index=True)
        extended.to_csv(self.root / "fund_daily_bars.csv", index=False)
        actual = build_etf_trend_targets(self.root, self.config, compressed=False)
        pd.testing.assert_frame_equal(targets, actual)

    def test_rejects_window_search_or_non_fixed_contract(self):
        for parameters in (
            {"movingAverageWindow": 60},
            {"movingAverageWindow": 120, "windowGrid": [60, 120]},
        ):
            with self.subTest(parameters=parameters):
                config = {**self.config, "featureParameters": parameters}
                with self.assertRaisesRegex(ValueError, "120"):
                    build_etf_trend_targets(self.root, config, compressed=False)

    def test_long_history_configs_keep_one_fixed_signal_and_three_cost_scenarios(self):
        config_dir = Path(__file__).resolve().parents[2] / "configs" / "research"
        paths = {
            "base": config_dir / "etf_trend_120d_long_history.json",
            "zero": config_dir / "etf_trend_120d_long_history_zero_cost.json",
            "double": config_dir / "etf_trend_120d_long_history_double_cost.json",
        }
        configs = {
            label: json.loads(path.read_text(encoding="utf-8"))
            for label, path in paths.items()
        }
        expected_costs = {
            "base": (0.00035, 0.00085, 0.001),
            "zero": (0.0, 0.0, 0.0),
            "double": (0.0007, 0.0017, 0.002),
        }
        for label, config in configs.items():
            config["qualityRunId"] = "test-quality"
            validate_run_config(config)
            validate_etf_trend_config(config)
            self.assertEqual(config["warmupStart"], "2012-05-28")
            self.assertEqual(config["startDate"], "2012-11-19")
            self.assertEqual(config["endDate"], "2026-06-29")
            self.assertEqual(config["featureParameters"], {"movingAverageWindow": 120})
            self.assertEqual(
                tuple(
                    float(config["costModel"][field])
                    for field in ("buyRate", "sellRate", "slippageRate")
                ),
                expected_costs[label],
            )

    def test_formal_pipeline_reproduces_without_database(self):
        engine = create_engine(f"sqlite+pysqlite:///{self.root / 'trend.sqlite'}")
        Base.metadata.create_all(engine)
        dates = pd.bdate_range("2025-01-02", periods=150)
        quality_id = "trend-quality-ready"
        source = "backend/tests/fixtures/quant_research_golden/universe.txt"
        contract = QualityCheckContract.create(
            scope="etf_time_series",
            start_date=dates[0].date(),
            end_date=dates[-1].date(),
            universe=["SYNETF.SZ"],
            universe_type="explicit_snapshot",
            universe_source=source,
            universe_as_of_date=dates[0].date(),
            benchmark="SYNIDX.SH",
        )
        with Session(engine) as db:
            db.add(Fund(ts_code="SYNETF.SZ", name="Synthetic ETF", market="SZ", fund_type="ETF", list_date=date(2020, 1, 1)))
            db.add(Index(ts_code="SYNIDX.SH", name="Synthetic Index", market="SH", publisher="synthetic", category="broad", base_date=date(2020, 1, 1), list_date=date(2020, 1, 1)))
            previous = Decimal("100")
            for offset, trade_date in enumerate(dates):
                close = Decimal(100 + offset)
                db.add(TradeCalendar(exchange="SSE", cal_date=trade_date.date(), is_open=True))
                db.add(
                    FundDailyBar(
                        ts_code="SYNETF.SZ",
                        trade_date=trade_date.date(),
                        open=close,
                        high=close,
                        low=close,
                        close=close,
                        pre_close=previous,
                        change_amount=close - previous,
                        pct_chg=Decimal("0"),
                        vol=Decimal("1000"),
                        amount=Decimal("10000"),
                    )
                )
                db.add(FundAdjustFactor(ts_code="SYNETF.SZ", trade_date=trade_date.date(), adj_factor=Decimal("1")))
                db.add(
                    IndexDailyBar(
                        ts_code="SYNIDX.SH",
                        trade_date=trade_date.date(),
                        open=close,
                        high=close,
                        low=close,
                        close=close,
                        pre_close=previous,
                        change_amount=close - previous,
                        pct_chg=Decimal("0"),
                        vol=Decimal("1000"),
                        amount=Decimal("10000"),
                    )
                )
                previous = close
            db.add(
                DataQualityRun(
                    id=quality_id,
                    scope=contract.scope,
                    start_date=contract.start_date,
                    end_date=contract.end_date,
                    universe_hash=contract.universe_hash,
                    status="ready",
                    config=contract.to_config(),
                    summary={
                        "status": "ready",
                        "resultCount": 1,
                        "passedCount": 1,
                        "warningCount": 0,
                        "blockerCount": 0,
                        "failedCount": 0,
                        "blockers": [],
                        "warnings": [],
                        "failedRules": [],
                        "limitations": [],
                        "benchmark": "SYNIDX.SH",
                    },
                    code_commit="trend-test",
                    started_at=datetime(2025, 8, 1, tzinfo=timezone.utc),
                    finished_at=datetime(2025, 8, 1, tzinfo=timezone.utc),
                )
            )
            db.flush()
            db.add(
                DataQualityResult(
                    run_id=quality_id,
                    rule_id="fixture.complete",
                    table_name="fund_daily_bars",
                    severity="info",
                    status="passed",
                    checked_rows=len(dates),
                    failed_rows=0,
                    sample_issues=[],
                )
            )
            db.commit()

        config = dict(self.config)
        config["qualityRunId"] = quality_id
        config["universe"] = build_explicit_universe(
            ["SYNETF.SZ"],
            as_of_date=dates[0].date(),
            source=source,
        )
        output_root = self.root / "formal-runs"
        with Session(engine) as db:
            result = run_quant_research(
                db,
                config,
                output_root,
                code_commit="trend-test",
                schema_revision="test-schema",
                test_mode=True,
                capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
            )
        engine.dispose()
        reproduction = reproduce_quant_research(result.path)
        self.assertTrue(reproduction["matches"])
        self.assertEqual(result.manifest["strategyId"], "etf_trend_120d")


if __name__ == "__main__":
    unittest.main()
