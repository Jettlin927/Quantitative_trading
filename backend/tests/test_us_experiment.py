from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

import pandas as pd
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import main, sync_worker
from backend.app.database import Base
from backend.app.models import (
    DataOverviewSnapshot,
    DataSyncJob,
    UsExperimentDailyBar,
    UsExperimentDailyCheck,
    UsExperimentInstrument,
)
from backend.app.schemas import SyncUsExperimentPricesRequest
from backend.app.us_experiment import (
    build_overview,
    list_daily_checks,
    list_instruments,
    register_targeted_universe,
    refresh_universe,
    sync_daily_prices,
    yfinance_frame_to_rows,
)


NOW = datetime(2026, 7, 21, 14, 30, tzinfo=timezone.utc)


class FakeUniverse:
    def __init__(self, records):
        self.records = records

    def fetch_universe(self):
        return self.records


class FakeYFinance:
    def fetch(self, symbols, start_date, end_date):
        del start_date, end_date
        rows = {
            "AAPL": [
                {
                    "trade_date": "2026-07-20",
                    "open": "210",
                    "high": "215",
                    "low": "209",
                    "close": "214",
                    "adj_close": "213.5",
                    "volume": 1000,
                    "cash_dividend": "0.25",
                    "split_ratio": "0",
                },
                {
                    "trade_date": "2026-07-21",
                    "open": "214",
                    "high": "216",
                    "low": "212",
                    "close": "215",
                    "adj_close": "215",
                    "volume": 1100,
                },
            ],
            "BABA": [],
        }
        return {symbol: rows.get(symbol, []) for symbol in symbols}


class FakeAkshare:
    def fetch_history(self, source_code, start_date, end_date):
        del start_date, end_date
        if source_code == "105.AAPL":
            return [
                {
                    "trade_date": "2026-07-21",
                    "open": "214",
                    "high": "216",
                    "low": "212",
                    "close": "215",
                    "volume": 1100,
                }
            ]
        return [
            {
                "trade_date": "2026-07-21",
                "open": "84",
                "high": "86",
                "low": "83",
                "close": "85",
                "volume": 900,
            }
        ]


class UsExperimentTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def tearDown(self):
        self.engine.dispose()

    def seed_universe(self):
        with self.Session() as db:
            return refresh_universe(
                db,
                provider=FakeUniverse(
                    [
                        {"代码": "105.AAPL", "名称": "苹果"},
                        {"代码": "106.BABA", "名称": "阿里巴巴"},
                        {"代码": "116.00700", "名称": "应忽略港股"},
                    ]
                ),
                observed_at=NOW,
                minimum_rows=2,
            )

    def test_universe_refresh_is_current_snapshot_and_preserves_first_seen(self):
        first = self.seed_universe()
        self.assertEqual(first["current_instruments"], 2)
        self.assertEqual(first["by_market"], {"105": 1, "106": 1})

        later = datetime(2026, 7, 22, 14, 30, tzinfo=timezone.utc)
        with self.Session() as db:
            original_first_seen = db.get(UsExperimentInstrument, "105.AAPL").first_seen_at
            refresh_universe(
                db,
                provider=FakeUniverse([{"代码": "105.AAPL", "名称": "Apple Inc."}]),
                observed_at=later,
                minimum_rows=1,
            )
            apple = db.get(UsExperimentInstrument, "105.AAPL")
            baba = db.get(UsExperimentInstrument, "106.BABA")

        self.assertEqual(apple.yahoo_symbol, "AAPL")
        self.assertEqual(apple.name, "Apple Inc.")
        self.assertEqual(apple.first_seen_at, original_first_seen)
        self.assertTrue(apple.is_current)
        self.assertFalse(baba.is_current)

    def test_targeted_universe_uses_isolated_namespace_without_disclosing_account_source(self):
        with self.Session() as db:
            result = register_targeted_universe(
                db,
                symbols=["aapl", "META", "aapl"],
                observed_at=NOW,
            )
            overview = build_overview(db)
            instruments = list(db.scalars(select(UsExperimentInstrument).order_by(UsExperimentInstrument.source_code)))

        self.assertEqual(result["source_codes"], ["TGT.AAPL", "TGT.META"])
        self.assertEqual([item.source_code for item in instruments], ["TGT.AAPL", "TGT.META"])
        self.assertEqual([item.market_name for item in instruments], ["TARGETED", "TARGETED"])
        self.assertEqual(overview["universe"]["mode"], "targeted_explicit")
        self.assertIn("显式目标名单", overview["universe"]["selection"])
        self.assertNotIn("持仓", str(overview))
        self.assertNotIn("成交", str(overview))

    def test_daily_sync_keeps_primary_rows_and_validation_separate(self):
        self.seed_universe()
        request = SyncUsExperimentPricesRequest(
            start_date=date(2026, 7, 20),
            end_date=date(2026, 7, 21),
            source_codes=["105.AAPL", "106.BABA"],
            validation_source_codes=["105.AAPL"],
        )
        with self.Session() as db:
            result = sync_daily_prices(
                db,
                request,
                price_provider=FakeYFinance(),
                validation_provider=FakeAkshare(),
                observed_at=NOW,
            )
            bars = list(db.scalars(select(UsExperimentDailyBar).order_by(UsExperimentDailyBar.trade_date)))
            checks = list(db.scalars(select(UsExperimentDailyCheck).order_by(UsExperimentDailyCheck.source_code)))
            apple = db.get(UsExperimentInstrument, "105.AAPL")
            baba = db.get(UsExperimentInstrument, "106.BABA")

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["successfulSourceCodes"], ["105.AAPL"])
        self.assertEqual(result["failed"][0]["sourceCode"], "106.BABA")
        self.assertEqual(len(bars), 2)
        self.assertTrue(all(row.source == "yfinance" for row in bars))
        self.assertEqual(bars[-1].close, Decimal("215.00000000"))
        self.assertEqual([(row.source_code, row.status) for row in checks], [("105.AAPL", "match"), ("106.BABA", "source_missing")])
        self.assertEqual(apple.history_start_date, date(2026, 7, 20))
        self.assertEqual(apple.history_end_date, date(2026, 7, 21))
        self.assertEqual(baba.last_sync_status, "failed")

        with self.Session() as db:
            sync_daily_prices(
                db,
                SyncUsExperimentPricesRequest(
                    start_date=date(2026, 7, 20),
                    end_date=date(2026, 7, 21),
                    source_codes=["105.AAPL"],
                    validation_source_codes=["105.AAPL"],
                ),
                price_provider=FakeYFinance(),
                validation_provider=FakeAkshare(),
                observed_at=NOW,
            )
            self.assertEqual(db.scalar(select(func.count(UsExperimentDailyBar.id))), 2)
            self.assertEqual(db.scalar(select(func.count(UsExperimentDailyCheck.id))), 2)
            page = list_daily_checks(
                db,
                source_code="105.AAPL",
                status="match",
                start_date=None,
                end_date=None,
                limit=10,
                offset=0,
            )

        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(page["items"][0]["yfinance"]["close"], 215.0)
        self.assertEqual(page["items"][0]["akshare"]["close"], 215.0)
        self.assertFalse(page["hasMore"])

    def test_batch_provider_error_falls_back_to_single_symbol_isolation(self):
        class BatchFragileYFinance:
            def fetch(self, symbols, start_date, end_date):
                if len(symbols) > 1:
                    raise RuntimeError("batch failed")
                if symbols == ["BABA"]:
                    raise RuntimeError("ticker failed")
                return FakeYFinance().fetch(symbols, start_date, end_date)

        self.seed_universe()
        request = SyncUsExperimentPricesRequest(
            start_date=date(2026, 7, 20),
            end_date=date(2026, 7, 21),
            source_codes=["105.AAPL", "106.BABA"],
        )
        with self.Session() as db:
            result = sync_daily_prices(
                db,
                request,
                price_provider=BatchFragileYFinance(),
                validation_provider=FakeAkshare(),
                observed_at=NOW,
            )
            bars = list(db.scalars(select(UsExperimentDailyBar)))
            checks = list(db.scalars(select(UsExperimentDailyCheck)))

        self.assertEqual(result["successfulSourceCodes"], ["105.AAPL"])
        self.assertEqual(result["failed"], [{"sourceCode": "106.BABA", "error": "RuntimeError: ticker failed"}])
        self.assertEqual(len(bars), 2)
        self.assertEqual([(row.source_code, row.status) for row in checks], [("106.BABA", "source_missing")])

    def test_overview_uses_explicit_persisted_coverage_snapshot(self):
        self.seed_universe()
        with self.Session() as db:
            sync_daily_prices(
                db,
                SyncUsExperimentPricesRequest(
                    start_date=date(2026, 7, 20),
                    end_date=date(2026, 7, 21),
                    source_codes=["105.AAPL"],
                    validation_source_codes=["105.AAPL"],
                ),
                price_provider=FakeYFinance(),
                validation_provider=FakeAkshare(),
                observed_at=NOW,
            )
            before_refresh = build_overview(db)
            refresh_result = main.execute_sync_job_action("us_experiment_overview_refresh", {}, db)
            refreshed = build_overview(db)
            cached = build_overview(db)

        self.assertEqual(before_refresh["snapshotStatus"], "pending_refresh")
        self.assertEqual(refresh_result["status"], "ok")
        self.assertEqual(before_refresh["coverage"]["dailyBars"], 0)
        self.assertEqual(refreshed["snapshotStatus"], "ready")
        self.assertEqual(refreshed["coverage"]["dailyBars"], 2)
        self.assertEqual(refreshed["validation"]["startDate"], "2026-07-21")
        self.assertEqual(refreshed["validation"]["endDate"], "2026-07-21")
        self.assertEqual(cached["coverage"], refreshed["coverage"])
        self.assertIsNotNone(cached["snapshotAt"])

    def test_overview_get_is_readonly_and_experiment_jobs_are_flagged_and_paginated(self):
        self.seed_universe()
        with self.Session.begin() as db:
            db.add_all(
                [
                    DataSyncJob(id="experiment-1", action="us_experiment_prices", status="ok", payload={}, payload_hash="a"),
                    DataSyncJob(id="experiment-2", action="us_experiment_universe", status="failed", payload={}, payload_hash="b"),
                    DataSyncJob(id="experiment-3", action="us_experiment_targeted_universe", status="ok", payload={}, payload_hash="d"),
                    DataSyncJob(id="other", action="daily_market", status="ok", payload={}, payload_hash="c"),
                ]
            )

        with self.Session() as db:
            overview = main.get_us_experiment_overview(db=db)
            self.assertIsNone(db.get(DataOverviewSnapshot, "us_experiment"))
            page = main.list_us_experiment_sync_jobs(limit=1, offset=0, db=db)

        self.assertEqual(overview["snapshotStatus"], "pending_refresh")
        self.assertTrue(page["isExperimental"])
        self.assertFalse(page["researchEligible"])
        self.assertEqual(page["total"], 3)
        self.assertEqual(len(page["items"]), 1)
        self.assertTrue(page["items"][0]["isExperimental"])
        self.assertFalse(page["items"][0]["researchEligible"])
        self.assertTrue(page["hasMore"])

    def test_overview_and_listing_keep_experimental_research_gate(self):
        self.seed_universe()
        with self.Session() as db:
            overview = build_overview(db)
            listing = list_instruments(db, q="AAPL", current_only=True, limit=100, offset=0)

        self.assertTrue(overview["isExperimental"])
        self.assertFalse(overview["researchEligible"])
        self.assertFalse(overview["executionEnabled"])
        self.assertEqual(overview["targetStartDate"], "2010-01-01")
        self.assertEqual(overview["schedule"], {"timezone": "Asia/Shanghai", "dailyAt": "10:00"})
        self.assertFalse(overview["universe"]["historicalUniverse"])
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["items"][0]["sourceCode"], "105.AAPL")

    def test_readonly_routes_and_worker_actions_preserve_experimental_flags(self):
        self.seed_universe()
        normalized = main.validate_sync_job_payload(
            "us_experiment_prices",
            {
                "start_date": "2026-07-20",
                "end_date": "2026-07-21",
                "source_codes": ["105.aapl"],
                "validation_source_codes": ["105.aapl"],
            },
        )
        self.assertEqual(normalized["source_codes"], ["105.AAPL"])
        self.assertIn("us_experiment_universe", sync_worker.SUPPORTED_SYNC_ACTIONS)
        self.assertIn("us_experiment_targeted_universe", sync_worker.SUPPORTED_SYNC_ACTIONS)
        self.assertIn("us_experiment_prices", sync_worker.SUPPORTED_SYNC_ACTIONS)
        self.assertIn("us_experiment_overview_refresh", sync_worker.SUPPORTED_SYNC_ACTIONS)
        paths = {route.path for route in main.app.routes}
        self.assertIn("/api/us-experiment/overview", paths)
        self.assertIn("/api/us-experiment/sync-jobs", paths)
        self.assertIn("/api/us-experiment/instruments", paths)
        self.assertIn("/api/us-experiment/daily-checks", paths)
        self.assertIn("/api/us-experiment/instruments/{source_code}/daily-bars", paths)

        with self.Session() as db:
            bars = main.get_us_experiment_daily_bars("105.AAPL", db=db)
        self.assertTrue(bars["isExperimental"])
        self.assertFalse(bars["researchEligible"])
        self.assertFalse(bars["executionEnabled"])
        self.assertEqual(bars["bars"], [])

    def test_yfinance_multi_index_frame_preserves_raw_and_adjusted_fields(self):
        columns = pd.MultiIndex.from_product(
            [["AAPL"], ["Open", "High", "Low", "Close", "Adj Close", "Volume", "Dividends", "Stock Splits"]]
        )
        frame = pd.DataFrame(
            [[210, 215, 209, 214, 213.5, 1000, 0.25, 0]],
            index=pd.to_datetime(["2026-07-20"]),
            columns=columns,
        )
        result = yfinance_frame_to_rows(frame, ["AAPL"])

        self.assertEqual(result["AAPL"][0]["trade_date"], date(2026, 7, 20))
        self.assertEqual(result["AAPL"][0]["close"], Decimal("214"))
        self.assertEqual(result["AAPL"][0]["adj_close"], Decimal("213.5"))
        self.assertEqual(result["AAPL"][0]["cash_dividend"], Decimal("0.25"))

    def test_price_request_rejects_invalid_codes_and_cross_batch_validation(self):
        with self.assertRaises(ValueError):
            SyncUsExperimentPricesRequest(
                start_date=date(2026, 7, 21),
                end_date=date(2026, 7, 20),
                source_codes=["AAPL"],
            )
        with self.assertRaises(ValueError):
            SyncUsExperimentPricesRequest(
                start_date=date(2026, 7, 20),
                end_date=date(2026, 7, 21),
                source_codes=["105.AAPL"],
                validation_source_codes=["106.BABA"],
            )

        targeted = SyncUsExperimentPricesRequest(
            start_date=date(2026, 7, 20),
            end_date=date(2026, 7, 21),
            source_codes=["tgt.aapl"],
        )
        self.assertEqual(targeted.source_codes, ["TGT.AAPL"])
        with self.assertRaises(ValueError):
            SyncUsExperimentPricesRequest(
                start_date=date(2026, 7, 20),
                end_date=date(2026, 7, 21),
                source_codes=["TGT.AAPL"],
                validation_source_codes=["TGT.AAPL"],
            )


if __name__ == "__main__":
    unittest.main()
