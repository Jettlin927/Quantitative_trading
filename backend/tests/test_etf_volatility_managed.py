from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

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
from backend.app.quant_research.etf_volatility_managed import (
    build_etf_volatility_managed_targets,
    validate_etf_volatility_managed_config,
)
from backend.app.quant_research.runner import reproduce_quant_research, run_quant_research
from backend.app.quant_research.snapshot import SnapshotCapacityPolicy
from backend.app.quant_research.universe import build_explicit_universe
from backend.tests.research_test_support import golden_run_config


SOURCE = "backend/tests/fixtures/quant_research_golden/universe.txt"


class EtfVolatilityManagedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dates, self.closes = _synthetic_history()
        _write_plain_inputs(self.root, self.dates, self.closes)
        self.config = _config(self.dates)

    def tearDown(self):
        self.tmp.cleanup()

    def test_t0_uses_prior_month_information_and_caps_exposure(self):
        targets = build_etf_volatility_managed_targets(
            self.root,
            self.config,
            compressed=False,
        )
        self.assertFalse(targets.empty)
        self.assertEqual(targets.iloc[0]["signal_date"], pd.Timestamp("2017-12-29"))
        self.assertLess(targets.iloc[0]["signal_date"], pd.Timestamp(self.config["startDate"]))
        self.assertTrue(targets["signal_date"].eq(targets["available_date"]).all())
        self.assertTrue(targets["target_weight"].between(0, 1).all())
        self.assertFalse(targets["signal_date"].dt.to_period("M").duplicated().any())

    def test_appending_future_prices_does_not_change_targets(self):
        expected = build_etf_volatility_managed_targets(
            self.root,
            self.config,
            compressed=False,
        )
        bars = pd.read_csv(self.root / "fund_daily_bars.csv")
        future = bars.iloc[-1].copy()
        future["trade_date"] = "2027-01-04"
        future["close"] = 1
        pd.concat([bars, pd.DataFrame([future])], ignore_index=True).to_csv(
            self.root / "fund_daily_bars.csv",
            index=False,
        )
        actual = build_etf_volatility_managed_targets(
            self.root,
            self.config,
            compressed=False,
        )
        pd.testing.assert_frame_equal(expected, actual)

    def test_only_four_preregistered_trials_are_allowed(self):
        trials = (
            ("previous_month", "1", "0"),
            ("previous_month", "0.5", "0"),
            ("trailing_3_month_mean", "1", "0"),
            ("previous_month", "1", "0.1"),
        )
        for estimator, power, band in trials:
            with self.subTest(estimator=estimator, power=power, band=band):
                config = _trial_config(self.config, estimator, power, band)
                validate_etf_volatility_managed_config(config)

        invalid = _trial_config(self.config, "trailing_3_month_mean", "0.5", "0")
        with self.assertRaisesRegex(ValueError, "四个试验"):
            validate_etf_volatility_managed_config(invalid)

    def test_calibration_must_end_before_oos(self):
        config = dict(self.config)
        config["featureParameters"] = {
            **self.config["featureParameters"],
            "calibrationEndDate": self.config["startDate"],
        }
        with self.assertRaisesRegex(ValueError, "calibrationEnd < startDate"):
            validate_etf_volatility_managed_config(config)

    def test_formal_pipeline_reproduces_with_walk_forward_and_risk(self):
        engine = create_engine(f"sqlite+pysqlite:///{self.root / 'volatility.sqlite'}")
        Base.metadata.create_all(engine)
        quality_id = "volatility-quality-ready"
        contract = QualityCheckContract.create(
            scope="etf_time_series",
            start_date=self.dates[0].date(),
            end_date=self.dates[-1].date(),
            universe=["SYNETF.SZ"],
            universe_type="explicit_snapshot",
            universe_source=SOURCE,
            universe_as_of_date=self.dates[0].date(),
            benchmark="SYNIDX.SH",
        )
        with Session(engine) as db:
            _seed_database(db, self.dates, self.closes)
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
                    code_commit="volatility-test",
                    started_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
                    finished_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
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
                    checked_rows=len(self.dates),
                    failed_rows=0,
                    sample_issues=[],
                )
            )
            db.commit()

        config = dict(self.config)
        config["qualityRunId"] = quality_id
        output_root = self.root / "formal-runs"
        with Session(engine) as db:
            result = run_quant_research(
                db,
                config,
                output_root,
                code_commit="volatility-test",
                schema_revision="test-schema",
                test_mode=True,
                capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
            )
        engine.dispose()
        reproduction = reproduce_quant_research(result.path)
        self.assertTrue(reproduction["matches"])
        self.assertEqual(result.manifest["strategyId"], "etf_volatility_managed")
        self.assertIn("walk_forward_metrics.csv.gz", result.manifest["artifactHashes"])
        self.assertIn("risk_exposures.csv.gz", result.manifest["artifactHashes"])


def _synthetic_history() -> tuple[pd.DatetimeIndex, pd.Series]:
    dates = pd.bdate_range("2015-01-01", "2019-12-31")
    close = 100.0
    values: list[float] = []
    for offset, trade_date in enumerate(dates):
        month_index = (trade_date.year - 2015) * 12 + trade_date.month - 1
        amplitude = 0.004 if month_index % 4 < 2 else 0.018
        daily_return = 0.00035 + (amplitude if offset % 2 else -amplitude)
        close *= 1 + daily_return
        values.append(close)
    return dates, pd.Series(values, index=dates)


def _config(dates: pd.DatetimeIndex) -> dict[str, object]:
    config = golden_run_config("quality", "a" * 64)
    config.update(
        {
            "strategyId": "etf_volatility_managed",
            "strategyVersion": "1",
            "universe": build_explicit_universe(
                ["SYNETF.SZ"],
                as_of_date=dates[0].date(),
                source=SOURCE,
            ),
            "warmupStart": dates[0].date().isoformat(),
            "startDate": "2018-01-01",
            "endDate": dates[-1].date().isoformat(),
            "featureParameters": {
                "calibrationStartDate": "2015-02-01",
                "calibrationEndDate": "2017-12-29",
                "realizedVarianceEstimator": "previous_month",
                "exposurePower": "1",
            },
            "targetWeightParameters": {
                "rebalanceFrequency": "month_end",
                "maxWeight": "1",
                "rebalanceBand": "0",
            },
            "executionPolicy": {
                "calendarExchange": "SSE",
                "executionPrice": "next_trade_open",
                "signalPrice": "close",
            },
            "validationPolicy": {
                "mode": "anchored",
                "trainPeriods": 252,
                "testPeriods": 126,
                "stepPeriods": 126,
            },
            "riskPolicy": {
                "mode": "rolling_covariance",
                "lookbackPeriods": 60,
                "minPeriods": 20,
            },
        }
    )
    return config


def _trial_config(
    source: dict[str, object],
    estimator: str,
    power: str,
    band: str,
) -> dict[str, object]:
    config = dict(source)
    config["featureParameters"] = {
        **source["featureParameters"],
        "realizedVarianceEstimator": estimator,
        "exposurePower": power,
    }
    config["targetWeightParameters"] = {
        **source["targetWeightParameters"],
        "rebalanceBand": band,
    }
    return config


def _write_plain_inputs(
    root: Path,
    dates: pd.DatetimeIndex,
    closes: pd.Series,
) -> None:
    pd.DataFrame(
        {
            "exchange": "SSE",
            "cal_date": dates.strftime("%Y-%m-%d"),
            "is_open": 1,
        }
    ).to_csv(root / "trade_calendars.csv", index=False)
    previous = closes.shift(1).fillna(closes.iloc[0])
    bars = pd.DataFrame(
        {
            "ts_code": "SYNETF.SZ",
            "trade_date": dates.strftime("%Y-%m-%d"),
            "open": previous.values,
            "high": pd.concat([previous, closes], axis=1).max(axis=1).values,
            "low": pd.concat([previous, closes], axis=1).min(axis=1).values,
            "close": closes.values,
            "pre_close": previous.values,
            "change_amount": (closes - previous).values,
            "pct_chg": ((closes / previous - 1) * 100).values,
            "vol": 1000,
            "amount": 10000,
        }
    )
    bars.to_csv(root / "fund_daily_bars.csv", index=False)
    pd.DataFrame(
        {
            "ts_code": "SYNETF.SZ",
            "trade_date": dates.strftime("%Y-%m-%d"),
            "adj_factor": 1.0,
        }
    ).to_csv(root / "fund_adjust_factors.csv", index=False)


def _seed_database(
    db: Session,
    dates: pd.DatetimeIndex,
    closes: pd.Series,
) -> None:
    db.add(
        Fund(
            ts_code="SYNETF.SZ",
            name="Synthetic ETF",
            market="SZ",
            fund_type="ETF",
            list_date=date(2010, 1, 1),
        )
    )
    db.add(
        Index(
            ts_code="SYNIDX.SH",
            name="Synthetic Index",
            market="SH",
            publisher="synthetic",
            category="broad",
            base_date=date(2010, 1, 1),
            list_date=date(2010, 1, 1),
        )
    )
    previous = Decimal("100")
    benchmark_previous = Decimal("1000")
    for offset, trade_date in enumerate(dates):
        close = Decimal(str(round(float(closes.iloc[offset]), 4)))
        benchmark_close = Decimal(str(round(1000 + offset * 0.2 + (offset % 7), 4)))
        db.add(
            TradeCalendar(
                exchange="SSE",
                cal_date=trade_date.date(),
                is_open=True,
            )
        )
        db.add(
            FundDailyBar(
                ts_code="SYNETF.SZ",
                trade_date=trade_date.date(),
                open=previous,
                high=max(previous, close),
                low=min(previous, close),
                close=close,
                pre_close=previous,
                change_amount=close - previous,
                pct_chg=(close / previous - 1) * 100,
                vol=Decimal("1000"),
                amount=Decimal("10000"),
            )
        )
        db.add(
            FundAdjustFactor(
                ts_code="SYNETF.SZ",
                trade_date=trade_date.date(),
                adj_factor=Decimal("1"),
            )
        )
        db.add(
            IndexDailyBar(
                ts_code="SYNIDX.SH",
                trade_date=trade_date.date(),
                open=benchmark_previous,
                high=max(benchmark_previous, benchmark_close),
                low=min(benchmark_previous, benchmark_close),
                close=benchmark_close,
                pre_close=benchmark_previous,
                change_amount=benchmark_close - benchmark_previous,
                pct_chg=(benchmark_close / benchmark_previous - 1) * 100,
                vol=Decimal("1000"),
                amount=Decimal("10000"),
            )
        )
        previous = close
        benchmark_previous = benchmark_close
    db.commit()


if __name__ == "__main__":
    unittest.main()
