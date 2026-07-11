from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import main, sync_worker
from backend.app.database import Base
from backend.app.models import DataSyncJob, SyncWorkerHeartbeat
from backend.app.schemas import SyncJobCreate


UTC = timezone.utc
REPO_ROOT = Path(__file__).resolve().parents[2]


class SyncWorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.now = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.engine.dispose()

    def enqueue(self, action: str = "us_sample") -> str:
        with self.Session() as db:
            result = main.create_sync_job(SyncJobCreate(action=action, payload={}), db)
            job = db.get(DataSyncJob, result["id"])
            job.next_attempt_at = self.now
            db.commit()
        return result["id"]

    def test_claim_is_exclusive_and_records_lease_attempt_and_heartbeat(self) -> None:
        job_id = self.enqueue()

        first = sync_worker.claim_next_job(
            "worker-a",
            session_factory=self.Session,
            now=self.now,
            lease_seconds=60,
        )
        second = sync_worker.claim_next_job(
            "worker-b",
            session_factory=self.Session,
            now=self.now,
            lease_seconds=60,
        )

        self.assertEqual(first.job_id, job_id)
        self.assertIsNone(second)
        with self.Session() as db:
            job = db.get(DataSyncJob, job_id)
            heartbeat = db.get(SyncWorkerHeartbeat, "worker-a")
            self.assertEqual(job.status, "running")
            self.assertEqual(job.attempt_count, 1)
            self.assertEqual(job.lease_owner, "worker-a")
            self.assertEqual(job.updated_at.replace(tzinfo=UTC), self.now)
            self.assertEqual(heartbeat.current_job_id, job_id)
            self.assertEqual(heartbeat.status, "running")

    def test_worker_restart_reclaims_expired_lease_but_stale_owner_cannot_finish(self) -> None:
        job_id = self.enqueue()
        crashed = sync_worker.claim_next_job(
            "worker-before-crash",
            session_factory=self.Session,
            now=self.now,
            lease_seconds=10,
        )
        recovered = sync_worker.claim_next_job(
            "worker-after-restart",
            session_factory=self.Session,
            now=self.now + timedelta(seconds=11),
            lease_seconds=60,
        )

        self.assertEqual(recovered.job_id, job_id)
        self.assertEqual(recovered.attempt_count, 2)
        self.assertFalse(
            sync_worker.complete_claimed_job(
                crashed,
                {"status": "ok", "rows_upserted": 1},
                session_factory=self.Session,
                now=self.now + timedelta(seconds=12),
            )
        )
        self.assertTrue(
            sync_worker.complete_claimed_job(
                recovered,
                {"status": "ok", "rows_upserted": 1},
                session_factory=self.Session,
                now=self.now + timedelta(seconds=12),
            )
        )
        with self.Session() as db:
            job = db.get(DataSyncJob, job_id)
            self.assertEqual(job.status, "ok")
            self.assertEqual(job.attempt_count, 2)
            self.assertIsNone(job.active_key)

    def test_legacy_running_job_without_lease_is_recovered_after_api_restart(self) -> None:
        job_id = self.enqueue()
        with self.Session.begin() as db:
            job = db.get(DataSyncJob, job_id)
            job.status = "running"
            job.lease_owner = None
            job.lease_expires_at = None

        recovered = sync_worker.claim_next_job(
            "worker-after-deploy",
            session_factory=self.Session,
            now=self.now,
        )

        self.assertEqual(recovered.job_id, job_id)
        self.assertEqual(recovered.attempt_count, 1)
        with self.Session() as db:
            job = db.get(DataSyncJob, job_id)
            self.assertEqual(job.lease_owner, "worker-after-deploy")
            self.assertIsNotNone(job.lease_expires_at)

    def test_transient_failure_backs_off_and_stops_at_max_attempts(self) -> None:
        job_id = self.enqueue()
        with self.Session.begin() as db:
            db.get(DataSyncJob, job_id).max_attempts = 2

        first = sync_worker.claim_next_job("worker-a", session_factory=self.Session, now=self.now)
        status = sync_worker.fail_claimed_job(
            first,
            RuntimeError("temporary"),
            session_factory=self.Session,
            now=self.now,
            retry_base_seconds=5,
        )
        self.assertEqual(status, "queued")
        self.assertIsNone(
            sync_worker.claim_next_job(
                "worker-too-early",
                session_factory=self.Session,
                now=self.now + timedelta(seconds=4),
            )
        )

        second = sync_worker.claim_next_job(
            "worker-b",
            session_factory=self.Session,
            now=self.now + timedelta(seconds=5),
        )
        status = sync_worker.fail_claimed_job(
            second,
            RuntimeError("still temporary"),
            session_factory=self.Session,
            now=self.now + timedelta(seconds=5),
            retry_base_seconds=5,
        )
        self.assertEqual(status, "failed")
        with self.Session() as db:
            job = db.get(DataSyncJob, job_id)
            self.assertEqual(job.attempt_count, 2)
            self.assertEqual(job.status, "failed")
            self.assertIsNone(job.active_key)

    def test_permanent_error_never_retries(self) -> None:
        job_id = self.enqueue()
        claim = sync_worker.claim_next_job("worker-a", session_factory=self.Session, now=self.now)

        status = sync_worker.fail_claimed_job(
            claim,
            sync_worker.PermanentSyncError("invalid persisted payload"),
            session_factory=self.Session,
            now=self.now,
        )

        self.assertEqual(status, "failed")
        with self.Session() as db:
            job = db.get(DataSyncJob, job_id)
            self.assertEqual(job.attempt_count, 1)
            self.assertEqual(job.status, "failed")
            self.assertIsNone(job.active_key)

    def test_worker_result_is_json_safe_and_heartbeat_is_short_transaction(self) -> None:
        job_id = self.enqueue()
        claim = sync_worker.claim_next_job("worker-a", session_factory=self.Session, now=self.now)
        refreshed = sync_worker.refresh_heartbeat(
            "worker-a",
            job_id,
            session_factory=self.Session,
            now=self.now + timedelta(seconds=10),
            lease_seconds=60,
        )
        self.assertTrue(refreshed)

        status = sync_worker.run_claimed_job(
            claim,
            executor=lambda _action, _payload, _db: {
                "status": "ok",
                "rows_upserted": 12,
                "as_of": date(2026, 7, 11),
                "unsafe": float("nan"),
            },
            session_factory=self.Session,
            heartbeat_interval_seconds=0,
            now=self.now + timedelta(seconds=11),
        )

        self.assertEqual(status, "ok")
        with self.Session() as db:
            job = db.get(DataSyncJob, job_id)
            heartbeat = db.get(SyncWorkerHeartbeat, "worker-a")
            self.assertEqual(job.result["as_of"], "2026-07-11")
            self.assertIsNone(job.result["unsafe"])
            self.assertEqual(heartbeat.status, "idle")
            self.assertIsNone(heartbeat.current_job_id)
            runtime = main.build_sync_runtime_status(db, now=self.now + timedelta(seconds=11))
            self.assertEqual(runtime["queue"]["latestCompletedAt"], job.finished_at.isoformat())

    def test_failed_result_retries_unless_business_marks_it_non_retryable(self) -> None:
        retry_job_id = self.enqueue()
        with self.Session.begin() as db:
            db.get(DataSyncJob, retry_job_id).max_attempts = 2
        retry_claim = sync_worker.claim_next_job("worker-retry", session_factory=self.Session, now=self.now)
        retry_status = sync_worker.run_claimed_job(
            retry_claim,
            executor=lambda _action, _payload, _db: {"status": "failed", "message": "upstream unavailable"},
            session_factory=self.Session,
            heartbeat_interval_seconds=0,
            now=self.now,
        )
        self.assertEqual(retry_status, "queued")
        with self.Session() as db:
            retry_job = db.get(DataSyncJob, retry_job_id)
            self.assertEqual(retry_job.status, "queued")
            self.assertIsNotNone(retry_job.active_key)
            self.assertIn("RetryableSyncResultError", retry_job.last_error)

        final_claim = sync_worker.claim_next_job(
            "worker-retry-final",
            session_factory=self.Session,
            now=self.now + timedelta(seconds=30),
        )
        final_status = sync_worker.run_claimed_job(
            final_claim,
            executor=lambda _action, _payload, _db: {"status": "failed", "message": "still unavailable"},
            session_factory=self.Session,
            heartbeat_interval_seconds=0,
            now=self.now + timedelta(seconds=30),
        )
        self.assertEqual(final_status, "failed")
        with self.Session() as db:
            retry_job = db.get(DataSyncJob, retry_job_id)
            self.assertEqual(retry_job.attempt_count, 2)
            self.assertEqual(retry_job.status, "failed")
            self.assertIsNone(retry_job.active_key)

        terminal_job_id = self.enqueue("stock_listings")
        terminal_claim = sync_worker.claim_next_job(
            "worker-terminal",
            session_factory=self.Session,
            now=self.now,
        )
        terminal_status = sync_worker.run_claimed_job(
            terminal_claim,
            executor=lambda _action, _payload, _db: {
                "status": "failed",
                "retryable": False,
                "message": "明确业务终态",
            },
            session_factory=self.Session,
            heartbeat_interval_seconds=0,
            now=self.now,
        )
        self.assertEqual(terminal_status, "failed")
        with self.Session() as db:
            terminal_job = db.get(DataSyncJob, terminal_job_id)
            self.assertEqual(terminal_job.status, "failed")
            self.assertIsNone(terminal_job.active_key)

    def test_fixed_worker_id_restart_refreshes_process_started_at(self) -> None:
        with patch.dict(os.environ, {"APP_GIT_COMMIT": "abc123"}):
            sync_worker.set_worker_heartbeat(
                "fixed-worker",
                "starting",
                session_factory=self.Session,
                now=self.now,
            )
            sync_worker.set_worker_heartbeat(
                "fixed-worker",
                "stopped",
                session_factory=self.Session,
                now=self.now + timedelta(seconds=5),
            )
        restarted_at = self.now + timedelta(seconds=10)
        with patch.dict(os.environ, {"APP_GIT_COMMIT": "def456"}):
            sync_worker.set_worker_heartbeat(
                "fixed-worker",
                "starting",
                session_factory=self.Session,
                now=restarted_at,
            )
        with self.Session() as db:
            heartbeat = db.get(SyncWorkerHeartbeat, "fixed-worker")
            self.assertEqual(heartbeat.process_started_at.replace(tzinfo=UTC), restarted_at)
            self.assertEqual(heartbeat.heartbeat_at.replace(tzinfo=UTC), restarted_at)
            self.assertEqual(heartbeat.code_commit, "def456")

    def test_unknown_or_non_object_results_never_become_success(self) -> None:
        unknown_job_id = self.enqueue()
        unknown_claim = sync_worker.claim_next_job("worker-unknown", session_factory=self.Session, now=self.now)
        unknown_status = sync_worker.run_claimed_job(
            unknown_claim,
            executor=lambda _action, _payload, _db: {"status": "mystery"},
            session_factory=self.Session,
            heartbeat_interval_seconds=0,
            now=self.now,
        )
        self.assertEqual(unknown_status, "queued")

        list_job_id = self.enqueue("stock_listings")
        list_claim = sync_worker.claim_next_job("worker-list", session_factory=self.Session, now=self.now)
        list_status = sync_worker.run_claimed_job(
            list_claim,
            executor=lambda _action, _payload, _db: ["not", "a", "result", "object"],
            session_factory=self.Session,
            heartbeat_interval_seconds=0,
            now=self.now,
        )
        self.assertEqual(list_status, "queued")
        with self.Session() as db:
            self.assertNotEqual(db.get(DataSyncJob, unknown_job_id).status, "ok")
            self.assertNotEqual(db.get(DataSyncJob, list_job_id).status, "ok")
        self.assertEqual(sync_worker.normalize_sync_job_status(None), "ok")
        with self.assertRaisesRegex(ValueError, "未知同步任务状态"):
            sync_worker.normalize_sync_job_status("mystery")
        with self.assertRaisesRegex(ValueError, "未知同步任务状态"):
            main.normalize_sync_job_status("mystery")

    def test_health_reports_worker_and_queue_from_durable_database_state(self) -> None:
        self.enqueue()
        with self.Session() as db:
            before_worker = main.build_sync_runtime_status(db, now=self.now)
        self.assertEqual(before_worker["worker"]["status"], "unavailable")
        self.assertTrue(before_worker["worker"]["stale"])
        self.assertIsNone(before_worker["worker"]["ageSeconds"])
        self.assertEqual(before_worker["queue"]["status"], "stalled")
        self.assertEqual(before_worker["queue"]["queued"], 1)

        with patch.dict(os.environ, {"APP_GIT_COMMIT": "unknown"}):
            sync_worker.set_worker_heartbeat(
                "worker-a",
                "idle",
                session_factory=self.Session,
                now=self.now,
            )
        with self.Session() as db:
            after_worker = main.build_sync_runtime_status(db, now=self.now)
        self.assertEqual(after_worker["worker"]["status"], "ok")
        self.assertFalse(after_worker["worker"]["stale"])
        self.assertEqual(after_worker["worker"]["ageSeconds"], 0)
        self.assertEqual(after_worker["worker"]["codeCommit"], "unknown")
        self.assertEqual(after_worker["queue"]["status"], "pending")
        self.assertEqual(after_worker["worker"]["active"], 1)

    def test_migration_chain_is_reserved_after_phase3_and_revision_ids_are_short(self) -> None:
        job_migration = REPO_ROOT / "backend/migrations/versions/0005_job_leases.py"
        heartbeat_migration = REPO_ROOT / "backend/migrations/versions/0006_worker_heartbeats.py"
        job_source = job_migration.read_text(encoding="utf-8")
        heartbeat_source = heartbeat_migration.read_text(encoding="utf-8")

        self.assertIn('revision = "0005_job_leases"', job_source)
        self.assertIn('down_revision = "0004_research_runs"', job_source)
        self.assertIn('revision = "0006_worker_heartbeats"', heartbeat_source)
        self.assertIn('down_revision = "0005_job_leases"', heartbeat_source)
        self.assertIn('sa.Column("updated_at"', job_source)
        self.assertIn('sa.Column("code_commit"', heartbeat_source)
        self.assertLessEqual(len("0005_job_leases"), 32)
        self.assertLessEqual(len("0006_worker_heartbeats"), 32)


if __name__ == "__main__":
    unittest.main()
