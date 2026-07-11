from __future__ import annotations

from contextlib import ExitStack
import unittest
from datetime import date
import inspect
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import main
from backend.app.database import Base
from backend.app.models import DataSyncJob, IndexDailyBar, Stock, StockAdjustFactor
from backend.app.schemas import SyncIndexDailyRequest, SyncJobCreate, SyncMarketFundamentalsRequest


class SyncJobTest(unittest.TestCase):
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

    def enqueue(self, action: str, payload: dict) -> dict:
        with self.Session() as db:
            return main.create_sync_job(SyncJobCreate(action=action, payload=payload), db)

    def test_routes_table_and_duplicate_queue_contract(self):
        self.assertIn("data_sync_jobs", Base.metadata.tables)
        paths = [route.path for route in main.app.routes]
        self.assertEqual(paths.count("/api/sync-jobs"), 2)
        self.assertIn("/api/sync-jobs/{job_id}", paths)

        payload = {
            "start_date": "2026-07-10",
            "end_date": "2026-07-11",
            "exchange": "",
            "token": "must-not-be-persisted",
        }
        first = self.enqueue("trade_calendar", payload)
        second = self.enqueue("trade_calendar", payload)

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["status"], "queued")
        self.assertNotIn("background_tasks", inspect.signature(main.create_sync_job).parameters)
        main_source = Path(main.__file__).read_text(encoding="utf-8")
        self.assertNotIn("BackgroundTasks", main_source)
        self.assertNotIn("background_tasks.add_task", main_source)
        with self.Session() as db:
            job = db.get(DataSyncJob, first["id"])
            self.assertNotIn("token", job.payload)
            self.assertEqual(job.payload["start_date"], "2026-07-10")

    def test_api_restart_leaves_queued_job_durable_for_worker(self):
        with patch.object(main, "execute_sync_job_action") as execute:
            queued = self.enqueue("stock_listings", {"statuses": ["L", "D"]})
        execute.assert_not_called()
        with self.Session() as db:
            persisted = db.get(DataSyncJob, queued["id"])
            self.assertEqual(persisted.status, "queued")
            self.assertEqual(persisted.attempt_count, 0)
            self.assertIsNone(persisted.lease_owner)
            self.assertEqual(len(main.list_sync_jobs(20, db)), 1)

    def test_daily_market_runs_all_required_components(self):
        calls = []

        def fake_result(name, rows):
            def operation(_payload=None, _db=None):
                calls.append(name)
                return {"status": "ok", "rows_upserted": rows}

            return operation

        payload = main.SyncMarketDataRequest(
            start_date=date(2026, 7, 11),
            end_date=date(2026, 7, 11),
            skip_existing=True,
            min_existing_rows=4000,
            max_trade_dates=1,
            benchmark="000905.SH",
        )
        with self.Session() as db, patch.object(main, "sync_stock_basic", side_effect=fake_result("stock_basic", 1)), patch.object(
            main, "sync_stock_listings", side_effect=fake_result("stock_listings", 2)
        ), patch.object(main, "sync_market_daily_basic", side_effect=fake_result("daily_basic", 3)
        ), patch.object(main, "sync_market_daily", side_effect=fake_result("daily", 3)), patch.object(
            main, "sync_market_limit_prices", side_effect=fake_result("limit_prices", 5)
        ), patch.object(main, "sync_market_suspend_events", side_effect=fake_result("suspend_events", 6)), patch.object(
            main, "sync_market_adjust_factors", side_effect=fake_result("adjust_factors", 7)
        ), patch.object(main, "sync_index_daily", side_effect=fake_result("benchmark_index_daily", 8)) as sync_index:
            result = main.execute_market_sync_bundle("daily_market", payload, db)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["rows_upserted"], 35)
        self.assertEqual(
            calls,
            [
                "stock_basic",
                "stock_listings",
                "daily_basic",
                "daily",
                "limit_prices",
                "suspend_events",
                "adjust_factors",
                "benchmark_index_daily",
            ],
        )
        self.assertEqual(sync_index.call_args.args[0].ts_codes, ["000905.SH"])

    def test_daily_market_propagates_partial_component_status(self):
        payload = main.SyncMarketDataRequest(
            start_date=date(2026, 7, 11),
            end_date=date(2026, 7, 11),
            skip_existing=False,
        )
        component_names = [
            "sync_stock_basic",
            "sync_stock_listings",
            "sync_market_daily_basic",
            "sync_market_daily",
            "sync_market_limit_prices",
            "sync_market_suspend_events",
            "sync_index_daily",
        ]
        with self.Session() as db, ExitStack() as stack:
            for component_name in component_names:
                stack.enter_context(
                    patch.object(main, component_name, return_value={"status": "ok", "rows_upserted": 1})
                )
            stack.enter_context(
                patch.object(
                    main,
                    "sync_market_adjust_factors",
                    return_value={"status": "partial", "rows_upserted": 2, "failed_dates": ["2026-07-11:temporary"]},
                )
            )
            result = main.execute_market_sync_bundle("daily_market", payload, db)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["components"]["adjust_factors"]["status"], "partial")
        self.assertIn("partial=1", result["message"])

    def test_market_adjust_and_benchmark_fill_latest_quality_inputs_idempotently(self):
        class Frame:
            def __init__(self, rows):
                self.rows = rows

            def to_dict(self, orient):
                if orient != "records":
                    raise AssertionError(f"unexpected orient: {orient}")
                return self.rows

        pro = Mock()
        pro.trade_cal.return_value = Frame([{"cal_date": "20260711", "is_open": 1}])
        pro.adj_factor.return_value = Frame(
            [
                {"ts_code": "000001.SZ", "trade_date": "20260711", "adj_factor": 2.5},
                {"ts_code": "600000.SH", "trade_date": "20260711", "adj_factor": 3.5},
            ]
        )
        pro.index_daily.return_value = Frame(
            [
                {
                    "ts_code": "000300.SH",
                    "trade_date": "20260711",
                    "open": 4000,
                    "high": 4010,
                    "low": 3990,
                    "close": 4005,
                }
            ]
        )
        payload = main.SyncMarketDataRequest(
            start_date=date(2026, 7, 11),
            end_date=date(2026, 7, 11),
            skip_existing=False,
            benchmark="000300.SH",
        )
        with self.Session() as db, patch.object(main, "get_pro_api", return_value=pro):
            first = main.sync_market_adjust_factors(payload, db)
            second = main.sync_market_adjust_factors(payload, db)
            benchmark = main.sync_index_daily(
                SyncIndexDailyRequest(
                    ts_codes=[payload.benchmark],
                    start_date=payload.start_date,
                    end_date=payload.end_date,
                ),
                db,
            )
            adjust_rows = db.scalar(select(func.count(StockAdjustFactor.id)))
            benchmark_rows = db.scalar(
                select(func.count(IndexDailyBar.id)).where(
                    IndexDailyBar.ts_code == payload.benchmark,
                    IndexDailyBar.trade_date == payload.end_date,
                )
            )

        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "ok")
        self.assertEqual(benchmark["status"], "ok")
        self.assertEqual(adjust_rows, 2)
        self.assertEqual(benchmark_rows, 1)
        self.assertEqual(pro.adj_factor.call_args.kwargs["trade_date"], "20260711")

    def test_market_fundamentals_is_a_durable_action_and_not_run_by_api(self):
        payload = {
            "start_date": "2026-07-11",
            "end_date": "2026-07-11",
            "max_stocks": 25,
            "rate_per_minute": 120,
            "skip_existing": False,
        }
        with patch.object(main, "sync_market_fundamentals") as sync:
            queued = self.enqueue("market_fundamentals", payload)
        sync.assert_not_called()
        self.assertEqual(queued["status"], "queued")

        with self.Session() as db, patch.object(
            main,
            "sync_market_fundamentals",
            return_value={"status": "ok", "rows_upserted": 7},
        ) as sync:
            job = db.get(DataSyncJob, queued["id"])
            result = main.execute_sync_job_action(job.action, job.payload, db)
        self.assertEqual(result["rows_upserted"], 7)
        request = sync.call_args.args[0]
        self.assertEqual(request.max_stocks, 25)
        self.assertEqual(request.rate_per_minute, 120)
        self.assertFalse(request.skip_existing)
        with self.Session() as db, self.assertRaises(HTTPException) as invalid_rate:
            main.create_sync_job(
                SyncJobCreate(action="market_fundamentals", payload={**payload, "rate_per_minute": 151}),
                db,
            )
        self.assertEqual(invalid_rate.exception.status_code, 422)

    def test_market_fundamentals_preserves_single_stock_rate_limit(self):
        class EmptyFrame:
            @staticmethod
            def to_dict(_kind):
                return []

        pro = Mock()
        pro.fina_indicator.return_value = EmptyFrame()
        with self.Session.begin() as db:
            db.add_all(
                [
                    Stock(ts_code="000001.SZ", name="A"),
                    Stock(ts_code="600000.SH", name="B"),
                ]
            )
        payload = SyncMarketFundamentalsRequest(
            start_date=date(2026, 7, 11),
            end_date=date(2026, 7, 11),
            max_stocks=2,
            rate_per_minute=120,
            skip_existing=False,
        )
        with self.Session() as db, patch.object(main, "get_pro_api", return_value=pro), patch.object(
            main.time,
            "monotonic",
            return_value=0.0,
        ), patch.object(main.time, "sleep") as sleep:
            result = main.sync_market_fundamentals(payload, db)

        self.assertEqual(pro.fina_indicator.call_count, 2)
        sleep.assert_called_once_with(0.5)
        self.assertEqual(result["rate_per_minute"], 120)
        self.assertEqual(result["status"], "ok")

    def test_missing_job_and_invalid_payload_are_rejected(self):
        with self.Session() as db:
            with self.assertRaises(HTTPException) as missing:
                main.get_sync_job("missing", db)
            with self.assertRaises(HTTPException) as invalid:
                main.create_sync_job(
                    SyncJobCreate(action="trade_calendar", payload={}),
                    db,
                )
        self.assertEqual(missing.exception.status_code, 404)
        self.assertEqual(invalid.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
