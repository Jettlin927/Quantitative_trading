from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import main
from backend.app.database import Base
from backend.app.models import DataSyncJob
from backend.app.schemas import SyncJobCreate


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

    def enqueue(self, action: str, payload: dict) -> tuple[dict, BackgroundTasks]:
        background_tasks = BackgroundTasks()
        with self.Session() as db:
            response = main.create_sync_job(
                SyncJobCreate(action=action, payload=payload),
                background_tasks,
                db,
            )
        return response, background_tasks

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
        first, first_tasks = self.enqueue("trade_calendar", payload)
        second, second_tasks = self.enqueue("trade_calendar", payload)

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["status"], "queued")
        self.assertEqual(len(first_tasks.tasks), 1)
        self.assertEqual(len(second_tasks.tasks), 0)
        with self.Session() as db:
            job = db.get(DataSyncJob, first["id"])
            self.assertNotIn("token", job.payload)
            self.assertEqual(job.payload["start_date"], "2026-07-10")

    def test_worker_uses_its_own_session_and_persists_json_safe_result(self):
        queued, _ = self.enqueue(
            "stock_listings",
            {"statuses": ["L", "D"]},
        )
        worker_sessions = []

        def fake_execute(_action, _payload, db):
            worker_sessions.append(db)
            return {
                "status": "ok",
                "rows_upserted": 12,
                "as_of": date(2026, 7, 11),
                "unsafe": float("nan"),
            }

        with patch.object(main, "SessionLocal", self.Session), patch.object(main, "execute_sync_job_action", side_effect=fake_execute):
            main.run_sync_job(queued["id"])

        with self.Session() as db:
            result = main.get_sync_job(queued["id"], db)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["rowsUpserted"], 12)
            self.assertEqual(result["result"]["as_of"], "2026-07-11")
            self.assertIsNone(result["result"]["unsafe"])
            self.assertIsNotNone(result["startedAt"])
            self.assertIsNotNone(result["finishedAt"])
            self.assertEqual(len(main.list_sync_jobs(20, db)), 1)
        self.assertEqual(len(worker_sessions), 1)

    def test_worker_persists_exception_and_allows_retry(self):
        first, _ = self.enqueue("us_sample", {})
        with patch.object(main, "SessionLocal", self.Session), patch.object(main, "execute_sync_job_action", side_effect=RuntimeError("source unavailable")):
            main.run_sync_job(first["id"])

        with self.Session() as db:
            failed = db.get(DataSyncJob, first["id"])
            self.assertEqual(failed.status, "failed")
            self.assertIn("source unavailable", failed.message)
            self.assertIsNone(failed.active_key)

        retry, tasks = self.enqueue("us_sample", {})
        self.assertNotEqual(retry["id"], first["id"])
        self.assertEqual(len(tasks.tasks), 1)

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
        )
        with self.Session() as db, patch.object(main, "sync_stock_basic", side_effect=fake_result("stock_basic", 1)), patch.object(
            main, "sync_market_daily_basic", side_effect=fake_result("daily_basic", 2)
        ), patch.object(main, "sync_market_daily", side_effect=fake_result("daily", 3)), patch.object(
            main, "sync_market_limit_prices", side_effect=fake_result("limit_prices", 4)
        ), patch.object(main, "sync_market_suspend_events", side_effect=fake_result("suspend_events", 5)):
            result = main.execute_market_sync_bundle("daily_market", payload, db)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["rows_upserted"], 15)
        self.assertEqual(calls, ["stock_basic", "daily_basic", "daily", "limit_prices", "suspend_events"])

    def test_missing_job_and_invalid_payload_are_rejected(self):
        with self.Session() as db:
            with self.assertRaises(HTTPException) as missing:
                main.get_sync_job("missing", db)
            with self.assertRaises(HTTPException) as invalid:
                main.create_sync_job(
                    SyncJobCreate(action="trade_calendar", payload={}),
                    BackgroundTasks(),
                    db,
                )
        self.assertEqual(missing.exception.status_code, 404)
        self.assertEqual(invalid.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
