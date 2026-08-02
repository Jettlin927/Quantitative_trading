from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import os
from time import monotonic
import unittest

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from backend.app import main, market_data_ingestion, sync_worker
from backend.app.database import Base
from backend.app.models import DataSyncJob, DataSyncRun, SyncWorkerHeartbeat, TradeCalendar


@unittest.skipUnless(
    os.getenv("TEST_SYNC_WORKER_POSTGRES_URL"),
    "TEST_SYNC_WORKER_POSTGRES_URL 未配置，跳过 PostgreSQL worker 集成测试",
)
class SyncWorkerPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        database_url = os.environ["TEST_SYNC_WORKER_POSTGRES_URL"]
        parsed = make_url(database_url)
        if parsed.host not in {"127.0.0.1", "localhost"} or parsed.database != "quant_worker_test":
            raise AssertionError("worker 集成测试只允许本机 quant_worker_test 隔离库")
        cls.engine = create_engine(database_url, pool_pre_ping=True)
        with cls.engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS private_workbench CASCADE"))
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, autocommit=False)

    @classmethod
    def tearDownClass(cls) -> None:
        with cls.engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS private_workbench CASCADE"))
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        cls.engine.dispose()

    def setUp(self) -> None:
        with self.Session.begin() as db:
            for table in reversed(Base.metadata.sorted_tables):
                db.execute(table.delete())

    def _enqueue(self, job_id: str) -> None:
        now = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)
        with self.Session.begin() as db:
            db.add(
                DataSyncJob(
                    id=job_id,
                    action="trade_calendar",
                    status="queued",
                    payload={"start_date": "2026-07-11", "end_date": "2026-07-11", "exchange": ""},
                    payload_hash=job_id,
                    active_key=job_id,
                    next_attempt_at=now,
                )
            )

    def test_two_workers_claim_exactly_one_copy_with_skip_locked(self) -> None:
        self._enqueue("job-exclusive")
        now = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(
                executor.map(
                    lambda worker_id: sync_worker.claim_next_job(
                        worker_id,
                        session_factory=self.Session,
                        now=now,
                        lease_seconds=60,
                    ),
                    ["worker-a", "worker-b"],
                )
            )

        claimed = [item for item in claims if item is not None]
        self.assertEqual(len(claimed), 1)
        with self.Session() as db:
            job = db.get(DataSyncJob, "job-exclusive")
            heartbeat = db.get(SyncWorkerHeartbeat, claimed[0].worker_id)
            self.assertEqual(job.status, "running")
            self.assertEqual(job.attempt_count, 1)
            self.assertEqual(heartbeat.code_commit, os.environ.get("APP_GIT_COMMIT", "unknown"))

    def test_skip_locked_does_not_wait_for_another_claim_transaction(self) -> None:
        self._enqueue("job-locked")
        now = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)

        with self.Session.begin() as locker:
            locker.scalar(
                select(DataSyncJob)
                .where(DataSyncJob.id == "job-locked")
                .with_for_update()
            )
            started = monotonic()
            skipped = sync_worker.claim_next_job(
                "worker-skip",
                session_factory=self.Session,
                now=now,
            )
            elapsed = monotonic() - started

        self.assertIsNone(skipped)
        self.assertLess(elapsed, 1.0)
        claimed = sync_worker.claim_next_job(
            "worker-after-unlock",
            session_factory=self.Session,
            now=now,
        )
        self.assertEqual(claimed.job_id, "job-locked")

    def test_crash_replay_keeps_natural_key_duplicates_at_zero(self) -> None:
        self._enqueue("job-replay")
        now = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)
        first = sync_worker.claim_next_job(
            "worker-before-crash",
            session_factory=self.Session,
            now=now,
            lease_seconds=10,
        )

        row = {"exchange": "SSE", "cal_date": date(2026, 7, 11), "is_open": True, "pretrade_date": date(2026, 7, 10)}
        with self.Session() as db:
            main.upsert_rows(db, TradeCalendar, [row], ["exchange", "cal_date"])

        second = sync_worker.claim_next_job(
            "worker-after-restart",
            session_factory=self.Session,
            now=now + timedelta(seconds=11),
            lease_seconds=60,
        )
        self.assertEqual(second.job_id, first.job_id)
        with self.Session() as db:
            main.upsert_rows(db, TradeCalendar, [row], ["exchange", "cal_date"])
        sync_worker.complete_claimed_job(
            second,
            {"status": "ok", "rows_upserted": 1},
            session_factory=self.Session,
            now=now + timedelta(seconds=12),
        )

        with self.Session() as db:
            rows = db.scalar(select(func.count()).select_from(TradeCalendar))
            duplicate_groups = db.scalar(
                select(func.count()).select_from(
                    select(TradeCalendar.exchange, TradeCalendar.cal_date)
                    .group_by(TradeCalendar.exchange, TradeCalendar.cal_date)
                    .having(func.count() > 1)
                    .subquery()
                )
            )
        self.assertEqual(rows, 1)
        self.assertEqual(duplicate_groups, 0)

    def test_trade_calendar_canonical_execution_survives_crash_replay(self) -> None:
        class Frame:
            @staticmethod
            def to_dict(orient: str) -> list[dict]:
                if orient != "records":
                    raise AssertionError(orient)
                return [
                    {
                        "exchange": "SSE",
                        "cal_date": "20260711",
                        "is_open": 1,
                        "pretrade_date": "20260710",
                    }
                ]

        class Provider:
            @staticmethod
            def trade_cal(**_kwargs):
                return Frame()

        self._enqueue("job-canonical-replay")
        now = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)
        first = sync_worker.claim_next_job(
            "worker-before-action-crash",
            session_factory=self.Session,
            now=now,
            lease_seconds=10,
        )
        with self.Session() as db:
            first_result = market_data_ingestion.execute_command(
                first.action,
                first.payload,
                db,
                provider_factory=lambda _token: Provider(),
            )

        recovered = sync_worker.claim_next_job(
            "worker-after-action-crash",
            session_factory=self.Session,
            now=now + timedelta(seconds=11),
            lease_seconds=60,
        )
        with self.Session() as db:
            replay_result = market_data_ingestion.execute_command(
                recovered.action,
                recovered.payload,
                db,
                provider_factory=lambda _token: Provider(),
            )
        sync_worker.complete_claimed_job(
            recovered,
            replay_result,
            session_factory=self.Session,
            now=now + timedelta(seconds=12),
        )

        with self.Session() as db:
            job = db.get(DataSyncJob, "job-canonical-replay")
            rows = db.scalar(select(func.count()).select_from(TradeCalendar))
            runs = db.scalar(
                select(func.count()).select_from(DataSyncRun).where(
                    DataSyncRun.target == "trade_calendar"
                )
            )
        self.assertEqual(first_result["rows_upserted"], 1)
        self.assertEqual(replay_result["rows_upserted"], 1)
        self.assertEqual(job.status, "ok")
        self.assertEqual(job.attempt_count, 2)
        self.assertEqual(job.rows_upserted, 1)
        self.assertEqual(rows, 1)
        self.assertEqual(runs, 2)

    def test_financial_revision_insert_reports_exact_postgres_count(self) -> None:
        row = main.financial_indicator_record_to_row(
            {
                "ts_code": "688981.SH",
                "ann_date": "20260720",
                "end_date": "20260630",
                "eps": "0.25",
                "roe": "4.20",
                "update_flag": "1",
            },
            source_observed_at=datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc),
            available_from=date(2026, 7, 22),
        )

        with self.Session() as db:
            self.assertEqual(main.insert_financial_revision_rows(db, [row]), 1)
            self.assertEqual(main.insert_financial_revision_rows(db, [row]), 0)


if __name__ == "__main__":
    unittest.main()
