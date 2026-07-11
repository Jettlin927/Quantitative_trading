from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import math
import os
import signal
import socket
from threading import Event, Thread
from typing import Any, Callable
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from .database import SessionLocal, assert_schema_revision_at_head, engine
from .models import DataSyncJob, SyncWorkerHeartbeat


UTC = timezone.utc
SUPPORTED_SYNC_ACTIONS = {
    "stock_listings",
    "trade_calendar",
    "market_bundle",
    "daily_market",
    "market_fundamentals",
    "us_sample",
}


class PermanentSyncError(RuntimeError):
    """An invalid durable job that must not be retried."""


class RetryableSyncResultError(RuntimeError):
    """A returned failure without an explicit non-retryable business decision."""


@dataclass(frozen=True)
class ClaimedJob:
    job_id: str
    action: str
    payload: dict[str, Any]
    worker_id: str
    attempt_count: int


SessionFactory = Callable[[], Session]
Executor = Callable[[str, dict[str, Any], Session], dict[str, Any]]


def claim_next_job(
    worker_id: str,
    *,
    session_factory: SessionFactory | None = None,
    now: datetime | None = None,
    lease_seconds: int = 60,
) -> ClaimedJob | None:
    """Atomically claim one eligible job, including an expired running lease."""
    if lease_seconds <= 0:
        raise ValueError("lease_seconds 必须大于 0")
    claimed_at = now or utc_now()
    factory = session_factory or SessionLocal
    with factory.begin() as db:
        _fail_exhausted_jobs(db, claimed_at)
        eligible = or_(
            and_(
                DataSyncJob.status == "queued",
                DataSyncJob.attempt_count < DataSyncJob.max_attempts,
                DataSyncJob.next_attempt_at <= claimed_at,
            ),
            and_(
                DataSyncJob.status == "running",
                DataSyncJob.attempt_count < DataSyncJob.max_attempts,
                or_(DataSyncJob.lease_expires_at.is_(None), DataSyncJob.lease_expires_at < claimed_at),
            ),
        )
        job = db.scalar(
            select(DataSyncJob)
            .where(eligible)
            .order_by(DataSyncJob.next_attempt_at, DataSyncJob.created_at, DataSyncJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None

        recovered = job.status == "running"
        job.status = "running"
        job.attempt_count = int(job.attempt_count or 0) + 1
        job.started_at = job.started_at or claimed_at
        job.last_attempt_at = claimed_at
        job.lease_owner = worker_id
        job.lease_expires_at = claimed_at + timedelta(seconds=lease_seconds)
        job.heartbeat_at = claimed_at
        job.message = "过期租约已恢复，任务重新执行" if recovered else "独立 worker 正在执行"
        job.updated_at = claimed_at
        _set_worker_heartbeat(db, worker_id, "running", claimed_at, current_job_id=job.id)
        return ClaimedJob(
            job_id=job.id,
            action=job.action,
            payload=dict(job.payload or {}),
            worker_id=worker_id,
            attempt_count=job.attempt_count,
        )


def refresh_heartbeat(
    worker_id: str,
    job_id: str,
    *,
    session_factory: SessionFactory | None = None,
    now: datetime | None = None,
    lease_seconds: int = 60,
) -> bool:
    """Extend a lease in its own short transaction."""
    if lease_seconds <= 0:
        raise ValueError("lease_seconds 必须大于 0")
    heartbeat_at = now or utc_now()
    factory = session_factory or SessionLocal
    with factory.begin() as db:
        result = db.execute(
            update(DataSyncJob)
            .where(
                DataSyncJob.id == job_id,
                DataSyncJob.status == "running",
                DataSyncJob.lease_owner == worker_id,
            )
            .values(
                heartbeat_at=heartbeat_at,
                lease_expires_at=heartbeat_at + timedelta(seconds=lease_seconds),
                updated_at=heartbeat_at,
            )
        )
        if result.rowcount != 1:
            return False
        _set_worker_heartbeat(db, worker_id, "running", heartbeat_at, current_job_id=job_id)
        return True


def complete_claimed_job(
    claim: ClaimedJob,
    raw_result: Any,
    *,
    session_factory: SessionFactory | None = None,
    now: datetime | None = None,
) -> bool:
    finished_at = now or utc_now()
    result = json_safe_value(raw_result)
    if not isinstance(result, dict):
        raise RetryableSyncResultError("同步执行结果必须是 JSON 对象")
    status = normalize_sync_job_status(result.get("status") if isinstance(result, dict) else None)
    if status == "failed" and not is_explicit_terminal_result(result):
        raise RetryableSyncResultError(
            "status=failed 默认视为暂时失败；只有 retryable=false 才是明确业务终态"
        )
    rows_upserted = sync_result_rows(result)
    message = sync_result_message(claim.action, status, result)
    factory = session_factory or SessionLocal
    with factory.begin() as db:
        job = db.scalar(select(DataSyncJob).where(DataSyncJob.id == claim.job_id).with_for_update())
        if job is None or job.status != "running" or job.lease_owner != claim.worker_id:
            return False
        job.status = status
        job.rows_upserted = rows_upserted
        job.message = message
        job.result = result
        job.finished_at = finished_at
        job.active_key = None
        job.lease_owner = None
        job.lease_expires_at = None
        job.heartbeat_at = finished_at
        job.last_error = None if status != "failed" else message
        job.updated_at = finished_at
        _set_worker_heartbeat(db, claim.worker_id, "idle", finished_at)
        return True


def fail_claimed_job(
    claim: ClaimedJob,
    error: BaseException,
    *,
    permanent: bool | None = None,
    session_factory: SessionFactory | None = None,
    now: datetime | None = None,
    retry_base_seconds: int = 30,
    retry_max_seconds: int = 1800,
) -> str:
    if retry_base_seconds <= 0 or retry_max_seconds < retry_base_seconds:
        raise ValueError("重试退避参数无效")
    failed_at = now or utc_now()
    error_text = f"{type(error).__name__}: {error}"[:1000]
    factory = session_factory or SessionLocal
    with factory.begin() as db:
        job = db.scalar(select(DataSyncJob).where(DataSyncJob.id == claim.job_id).with_for_update())
        if job is None or job.status != "running" or job.lease_owner != claim.worker_id:
            return "lease_lost"
        is_permanent = is_permanent_error(error) if permanent is None else permanent
        terminal = is_permanent or int(job.attempt_count or 0) >= int(job.max_attempts or 0)
        job.last_error = error_text
        job.result = {
            "error": error_text,
            "attempt": int(job.attempt_count or 0),
            "permanent": bool(is_permanent),
        }
        job.lease_owner = None
        job.lease_expires_at = None
        job.heartbeat_at = failed_at
        job.updated_at = failed_at
        if terminal:
            job.status = "failed"
            job.message = error_text
            job.finished_at = failed_at
            job.active_key = None
        else:
            delay = min(retry_base_seconds * (2 ** max(int(job.attempt_count or 1) - 1, 0)), retry_max_seconds)
            job.status = "queued"
            job.next_attempt_at = failed_at + timedelta(seconds=delay)
            job.message = f"暂时失败，{delay} 秒后重试：{error_text}"[:1000]
            job.finished_at = None
        _set_worker_heartbeat(db, claim.worker_id, "idle", failed_at, last_error=error_text)
        return job.status


def run_claimed_job(
    claim: ClaimedJob,
    *,
    executor: Executor | None = None,
    session_factory: SessionFactory | None = None,
    heartbeat_interval_seconds: float = 15,
    lease_seconds: int = 60,
    now: datetime | None = None,
) -> str:
    factory = session_factory or SessionLocal
    operation = executor or execute_sync_job_action
    stop_heartbeat = Event()
    heartbeat_thread: Thread | None = None
    if heartbeat_interval_seconds > 0:
        heartbeat_thread = Thread(
            target=_heartbeat_loop,
            args=(stop_heartbeat, claim, factory, heartbeat_interval_seconds, lease_seconds),
            daemon=True,
        )
        heartbeat_thread.start()
    try:
        if claim.action not in SUPPORTED_SYNC_ACTIONS:
            raise PermanentSyncError(f"不支持的同步动作: {claim.action}")
        with factory() as action_db:
            raw_result = operation(claim.action, claim.payload, action_db)
        safe_result = json_safe_value(raw_result)
        if not isinstance(safe_result, dict):
            raise RetryableSyncResultError("同步执行结果必须是 JSON 对象")
        result_status = normalize_sync_job_status(safe_result.get("status"))
        if result_status == "failed" and not is_explicit_terminal_result(safe_result):
            raise RetryableSyncResultError(sync_result_message(claim.action, result_status, safe_result))
        completed = complete_claimed_job(claim, raw_result, session_factory=factory, now=now)
        if not completed:
            return "lease_lost"
        return result_status
    except Exception as exc:  # noqa: BLE001
        return fail_claimed_job(claim, exc, session_factory=factory, now=now)
    finally:
        stop_heartbeat.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=max(heartbeat_interval_seconds * 2, 1))


def execute_sync_job_action(action: str, payload: dict[str, Any], db: Session) -> dict[str, Any]:
    from .main import execute_sync_job_action as execute

    return execute(action, payload, db)


def run_forever() -> None:
    assert_schema_revision_at_head(engine)
    poll_seconds = positive_float_env("SYNC_WORKER_POLL_SECONDS", 2.0)
    heartbeat_seconds = positive_float_env("SYNC_WORKER_HEARTBEAT_SECONDS", 15.0)
    lease_seconds = positive_int_env("SYNC_WORKER_LEASE_SECONDS", 60)
    if heartbeat_seconds >= lease_seconds:
        raise ValueError("SYNC_WORKER_HEARTBEAT_SECONDS 必须小于租约时长")
    worker_id = os.getenv("SYNC_WORKER_ID") or f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
    stop = Event()

    def request_stop(_signum: int, _frame: Any) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    set_worker_heartbeat(worker_id, "starting")
    log_event("worker_started", worker_id=worker_id)
    try:
        while not stop.is_set():
            try:
                claim = claim_next_job(worker_id, lease_seconds=lease_seconds)
                if claim is None:
                    set_worker_heartbeat(worker_id, "idle")
                    stop.wait(poll_seconds)
                    continue
                log_event("job_claimed", worker_id=worker_id, job_id=claim.job_id, attempt=claim.attempt_count)
                status = run_claimed_job(
                    claim,
                    heartbeat_interval_seconds=heartbeat_seconds,
                    lease_seconds=lease_seconds,
                )
                log_event("job_finished", worker_id=worker_id, job_id=claim.job_id, status=status)
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"[:1000]
                set_worker_heartbeat(worker_id, "error", last_error=error)
                log_event("worker_error", worker_id=worker_id, error=error)
                stop.wait(poll_seconds)
    finally:
        set_worker_heartbeat(worker_id, "stopped")
        log_event("worker_stopped", worker_id=worker_id)


def set_worker_heartbeat(
    worker_id: str,
    status: str,
    *,
    current_job_id: str | None = None,
    last_error: str | None = None,
    session_factory: SessionFactory | None = None,
    now: datetime | None = None,
) -> None:
    heartbeat_at = now or utc_now()
    factory = session_factory or SessionLocal
    with factory.begin() as db:
        _set_worker_heartbeat(
            db,
            worker_id,
            status,
            heartbeat_at,
            current_job_id=current_job_id,
            last_error=last_error,
        )


def _heartbeat_loop(
    stop: Event,
    claim: ClaimedJob,
    session_factory: SessionFactory,
    interval_seconds: float,
    lease_seconds: int,
) -> None:
    while not stop.wait(interval_seconds):
        try:
            if not refresh_heartbeat(
                claim.worker_id,
                claim.job_id,
                session_factory=session_factory,
                lease_seconds=lease_seconds,
            ):
                return
        except Exception as exc:  # noqa: BLE001
            log_event(
                "heartbeat_error",
                worker_id=claim.worker_id,
                job_id=claim.job_id,
                error=f"{type(exc).__name__}: {exc}"[:1000],
            )


def _fail_exhausted_jobs(db: Session, now: datetime) -> None:
    exhausted_running = and_(
        DataSyncJob.status == "running",
        or_(DataSyncJob.lease_expires_at.is_(None), DataSyncJob.lease_expires_at < now),
        DataSyncJob.attempt_count >= DataSyncJob.max_attempts,
    )
    exhausted_queued = and_(
        DataSyncJob.status == "queued",
        DataSyncJob.attempt_count >= DataSyncJob.max_attempts,
    )
    message = "任务已达到最大尝试次数"
    db.execute(
        update(DataSyncJob)
        .where(or_(exhausted_running, exhausted_queued))
        .values(
            status="failed",
            message=message,
            last_error=message,
            result={"error": message},
            finished_at=now,
            active_key=None,
            lease_owner=None,
            lease_expires_at=None,
            heartbeat_at=now,
            updated_at=now,
        )
    )


def _set_worker_heartbeat(
    db: Session,
    worker_id: str,
    status: str,
    now: datetime,
    *,
    current_job_id: str | None = None,
    last_error: str | None = None,
) -> None:
    heartbeat = db.get(SyncWorkerHeartbeat, worker_id)
    if heartbeat is None:
        heartbeat = SyncWorkerHeartbeat(worker_id=worker_id, process_started_at=now)
        db.add(heartbeat)
    if status == "starting":
        heartbeat.process_started_at = now
    heartbeat.status = status
    heartbeat.current_job_id = current_job_id
    heartbeat.heartbeat_at = now
    heartbeat.code_commit = current_code_commit()
    heartbeat.last_error = last_error


def is_permanent_error(error: BaseException) -> bool:
    return isinstance(error, (PermanentSyncError, ValidationError))


def is_explicit_terminal_result(result: Any) -> bool:
    return isinstance(result, dict) and result.get("retryable") is False


def normalize_sync_job_status(status: Any) -> str:
    if status is None:
        return "ok"
    if status in {"ok", "partial", "failed"}:
        return str(status)
    raise ValueError(f"未知同步任务状态：{status}")


def sync_result_rows(result: Any) -> int:
    if not isinstance(result, dict):
        return 0
    if "rows_upserted" in result:
        return int(result.get("rows_upserted") or 0)
    summary = result.get("summary")
    if isinstance(summary, dict):
        return sum(int(value or 0) for value in summary.values())
    return 0


def sync_result_message(action: str, status: str, result: Any) -> str:
    if isinstance(result, dict) and result.get("message"):
        return str(result["message"])[:1000]
    return f"{action} {status}"[:1000]


def json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe_value(item) for item in value]
    return str(value)


def positive_int_env(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} 必须大于 0")
    return value


def positive_float_env(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} 必须大于 0")
    return value


def utc_now() -> datetime:
    return datetime.now(UTC)


def current_code_commit() -> str:
    return (os.getenv("APP_GIT_COMMIT") or "unknown").strip()[:64] or "unknown"


def log_event(event: str, **payload: Any) -> None:
    print(json.dumps({"event": event, "at": utc_now().isoformat(), **payload}, ensure_ascii=False), flush=True)


def main() -> None:
    run_forever()


if __name__ == "__main__":
    main()
