from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import signal
import socket
from threading import Event, Thread
from time import monotonic
from typing import Callable

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .database import SessionLocal, assert_schema_revision_at_head, engine
from .github_research import GitHubIssueClient, poll_research_issues_once
from .models import (
    FormalResearch,
    DataSyncJob,
    FrozenResearchPlan,
    ResearchOrchestration,
    ResearchRun,
    ResearchWorkItem,
)
from .quant_research.runner import (
    ResearchRunResult,
    ResearchStopRequested,
    resume_quant_research,
    run_quant_research,
)
from .research_orchestration import append_research_event, transition_orchestration
from .research_plan import ResearchServerLimits
from .work_coordination import try_acquire_heavy_work_claim_lock


UTC = timezone.utc


class TransientResearchError(RuntimeError):
    pass


class ResearchBudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class ClaimedResearchWork:
    work_item_id: str
    orchestration_id: str
    formal_research_id: str
    plan_id: str
    worker_id: str
    attempt_count: int
    max_attempts: int
    resume_run_id: str | None
    resource_budget: dict


SessionFactory = Callable[[], Session]
Executor = Callable[[ClaimedResearchWork, Event], ResearchRunResult]


def claim_next_research_work(
    worker_id: str,
    *,
    github_available: bool,
    session_factory: SessionFactory | None = None,
    now: datetime | None = None,
    lease_seconds: int = 120,
) -> ClaimedResearchWork | None:
    if not github_available:
        return None
    if lease_seconds <= 0:
        raise ValueError("lease_seconds 必须大于 0")
    claimed_at = now or datetime.now(UTC)
    factory = session_factory or SessionLocal
    with factory.begin() as db:
        if not try_acquire_heavy_work_claim_lock(db):
            return None
        _finalize_expired_stops(db, claimed_at)
        live = db.scalar(
            select(ResearchWorkItem.id)
            .where(
                ResearchWorkItem.status.in_(("leased", "running")),
                ResearchWorkItem.lease_expires_at >= claimed_at,
            )
            .limit(1)
        )
        if live is not None:
            return None
        active_sync = db.scalar(
            select(DataSyncJob.id).where(DataSyncJob.status == "running").limit(1)
        )
        if active_sync is not None:
            return None
        _block_exhausted_work(db, claimed_at)
        eligible = and_(
            ResearchWorkItem.orchestration_id.in_(
                select(ResearchOrchestration.id).where(
                    ResearchOrchestration.state.in_(("queued", "running"))
                )
            ),
            or_(
                and_(
                    ResearchWorkItem.status == "queued",
                    ResearchWorkItem.attempt_count < ResearchWorkItem.max_attempts,
                    ResearchWorkItem.next_attempt_at <= claimed_at,
                    ResearchWorkItem.stop_requested_at.is_(None),
                ),
                and_(
                    ResearchWorkItem.status.in_(("leased", "running")),
                    ResearchWorkItem.attempt_count < ResearchWorkItem.max_attempts,
                    ResearchWorkItem.lease_expires_at < claimed_at,
                    ResearchWorkItem.stop_requested_at.is_(None),
                ),
            ),
        )
        work = db.scalar(
            select(ResearchWorkItem)
            .where(eligible)
            .order_by(ResearchWorkItem.next_attempt_at, ResearchWorkItem.created_at, ResearchWorkItem.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if work is None:
            return None
        orchestration = db.get(ResearchOrchestration, work.orchestration_id)
        plan = db.get(FrozenResearchPlan, orchestration.plan_id)
        recovered = work.status in {"leased", "running"}
        if recovered:
            stale_run = _latest_active_run(db, work.formal_research_id)
            if stale_run is not None:
                stale_run.status = "interrupted"
                stale_run.error = "ResearchWorkerLeaseExpired: Worker 租约过期，保留 checkpoint 并恢复"
                stale_run.finished_at = claimed_at
                stale_run.heartbeat_at = claimed_at
                work.current_run_id = stale_run.run_id
                work.resume_run_id = stale_run.run_id
            append_research_event(
                db,
                work.formal_research_id,
                "research_lease_recovered",
                {
                    "previousWorker": work.lease_owner,
                    "resumeRunId": work.resume_run_id,
                },
                run_id=work.resume_run_id,
                occurred_at=claimed_at,
            )
        work.status = "leased"
        work.attempt_count += 1
        work.lease_owner = worker_id
        work.lease_expires_at = claimed_at + timedelta(seconds=lease_seconds)
        work.heartbeat_at = claimed_at
        work.last_error_kind = None
        work.last_error = None
        work.updated_at = claimed_at
        return ClaimedResearchWork(
            work_item_id=work.id,
            orchestration_id=orchestration.id,
            formal_research_id=work.formal_research_id,
            plan_id=plan.id,
            worker_id=worker_id,
            attempt_count=work.attempt_count,
            max_attempts=work.max_attempts,
            resume_run_id=work.resume_run_id,
            resource_budget=dict(plan.plan_json["resourceBudget"]),
        )


def heartbeat_research_work(
    claim: ClaimedResearchWork,
    *,
    session_factory: SessionFactory | None = None,
    now: datetime | None = None,
    lease_seconds: int = 120,
) -> str:
    heartbeat_at = now or datetime.now(UTC)
    factory = session_factory or SessionLocal
    with factory.begin() as db:
        work = db.scalar(
            select(ResearchWorkItem)
            .where(ResearchWorkItem.id == claim.work_item_id)
            .with_for_update()
        )
        orchestration = db.get(ResearchOrchestration, claim.orchestration_id)
        if (
            work is None
            or work.status not in {"leased", "running"}
            or work.lease_owner != claim.worker_id
        ):
            return "lease_lost"
        work.heartbeat_at = heartbeat_at
        work.lease_expires_at = heartbeat_at + timedelta(seconds=lease_seconds)
        work.updated_at = heartbeat_at
        if work.stop_requested_at is not None or orchestration.state in {
            "stopping",
            "blocked",
            "stopped",
        }:
            return "stop_requested"
        return "ok"


def execute_claimed_research_work(
    claim: ClaimedResearchWork,
    *,
    executor: Executor | None = None,
    session_factory: SessionFactory | None = None,
    heartbeat_interval_seconds: int = 20,
    lease_seconds: int = 120,
) -> str:
    factory = session_factory or SessionLocal
    started_at = datetime.now(UTC)
    start_status = _mark_work_running(claim, factory, started_at)
    if start_status != "running":
        return start_status
    stop_event = Event()
    heartbeat_done = Event()
    heartbeat = Thread(
        target=_heartbeat_loop,
        args=(heartbeat_done, stop_event, claim, factory, heartbeat_interval_seconds, lease_seconds),
        daemon=True,
    )
    heartbeat.start()
    deadline = monotonic() + int(claim.resource_budget["wallClockSeconds"])
    budget_expired = Event()

    def should_stop() -> bool:
        if stop_event.is_set():
            return True
        if monotonic() >= deadline:
            budget_expired.set()
            return True
        return False

    try:
        if executor is None:
            result = _execute_with_runner(claim, factory, should_stop)
        else:
            result = executor(claim, stop_event)
        if monotonic() >= deadline:
            raise ResearchBudgetExceeded("研究运行超过冻结 wallClockSeconds 预算")
        post_run_status = heartbeat_research_work(
            claim, session_factory=factory, lease_seconds=lease_seconds
        )
        if post_run_status == "lease_lost":
            return "lease_lost"
        if post_run_status == "stop_requested":
            stop_research_work(claim, "运行完成后、评价开始前收到停止请求", session_factory=factory)
            return "stopped"
        artifact_bytes = _directory_size(result.path)
        if artifact_bytes > int(claim.resource_budget["artifactMiB"]) * 1024 * 1024:
            raise ResearchBudgetExceeded("研究工件超过冻结 artifactMiB 预算")
        return complete_research_work(
            claim,
            result,
            session_factory=factory,
            now=datetime.now(UTC),
        )
    except ResearchStopRequested as exc:
        if budget_expired.is_set():
            fail_research_work(
                claim,
                ResearchBudgetExceeded(str(exc)),
                transient=False,
                session_factory=factory,
            )
            return "blocked"
        stop_research_work(claim, str(exc), session_factory=factory)
        return "stopped"
    except ResearchBudgetExceeded as exc:
        fail_research_work(claim, exc, transient=False, session_factory=factory)
        return "blocked"
    except Exception as exc:
        transient = isinstance(exc, (TransientResearchError, OperationalError, TimeoutError, ConnectionError))
        return fail_research_work(
            claim,
            exc,
            transient=transient,
            session_factory=factory,
        )
    finally:
        heartbeat_done.set()
        heartbeat.join(timeout=max(heartbeat_interval_seconds * 2, 1))


def complete_research_work(
    claim: ClaimedResearchWork,
    result: ResearchRunResult,
    *,
    session_factory: SessionFactory | None = None,
    now: datetime | None = None,
) -> str:
    completed_at = now or datetime.now(UTC)
    factory = session_factory or SessionLocal
    with factory.begin() as db:
        work = _owned_work(db, claim)
        orchestration = db.get(ResearchOrchestration, claim.orchestration_id)
        formal = db.get(FormalResearch, claim.formal_research_id)
        work.status = "succeeded"
        work.current_run_id = result.run_id
        work.resume_run_id = None
        _release_lease(work, completed_at)
        if work.stop_requested_at is not None or orchestration.state == "stopping":
            transition_orchestration(
                orchestration,
                "stopped",
                reason="运行完成后、评价开始前收到停止请求",
            )
            formal.phase = "stopped"
            formal.completed_at = completed_at
            append_research_event(
                db,
                formal.id,
                "research_stopped_after_run",
                {"runId": result.run_id},
                run_id=result.run_id,
                occurred_at=completed_at,
            )
            return "stopped"
        orchestration.state_reason = "研究运行完成，等待结构化评价"
        formal.phase = "evaluating"
        append_research_event(
            db,
            formal.id,
            "research_run_succeeded",
            {"runId": result.run_id, "artifactRoot": str(result.path)},
            run_id=result.run_id,
            occurred_at=completed_at,
        )
        return "succeeded"


def fail_research_work(
    claim: ClaimedResearchWork,
    exc: Exception,
    *,
    transient: bool,
    session_factory: SessionFactory | None = None,
    now: datetime | None = None,
) -> str:
    failed_at = now or datetime.now(UTC)
    factory = session_factory or SessionLocal
    with factory.begin() as db:
        work = _owned_work(db, claim)
        orchestration = db.get(ResearchOrchestration, claim.orchestration_id)
        formal = db.get(FormalResearch, claim.formal_research_id)
        run = _latest_run(db, claim.formal_research_id)
        work.current_run_id = run.run_id if run else work.current_run_id
        work.last_error_kind = type(exc).__name__
        work.last_error = str(exc)[:2000]
        _release_lease(work, failed_at)
        if work.stop_requested_at is not None or orchestration.state == "stopping":
            work.status = "interrupted"
            transition_orchestration(orchestration, "stopped", reason="停止请求优先于失败重试")
            formal.phase = "stopped"
            formal.completed_at = failed_at
            append_research_event(
                db,
                formal.id,
                "research_stopped",
                {"reason": "停止请求生效", "errorKind": type(exc).__name__},
                run_id=work.current_run_id,
                occurred_at=failed_at,
            )
            return "stopped"
        if transient and work.attempt_count < work.max_attempts:
            work.status = "queued"
            work.next_attempt_at = failed_at + timedelta(seconds=2 ** work.attempt_count)
            transition_orchestration(orchestration, "queued", reason="瞬时基础设施故障，按预算重试")
            formal.phase = "approved"
            append_research_event(
                db,
                formal.id,
                "research_retry_scheduled",
                {
                    "attemptCount": work.attempt_count,
                    "maxAttempts": work.max_attempts,
                    "errorKind": type(exc).__name__,
                },
                run_id=work.current_run_id,
                occurred_at=failed_at,
            )
            return "retrying"
        work.status = "failed"
        transition_orchestration(
            orchestration,
            "blocked",
            reason=("瞬时故障重试预算已耗尽" if transient else "不可重试的研究执行错误"),
        )
        formal.phase = "stopped"
        formal.completed_at = failed_at
        append_research_event(
            db,
            formal.id,
            "research_blocked",
            {
                "attemptCount": work.attempt_count,
                "maxAttempts": work.max_attempts,
                "errorKind": type(exc).__name__,
                "retryable": transient,
            },
            run_id=work.current_run_id,
            occurred_at=failed_at,
        )
        return "blocked"


def stop_research_work(
    claim: ClaimedResearchWork,
    reason: str,
    *,
    session_factory: SessionFactory | None = None,
    now: datetime | None = None,
) -> None:
    stopped_at = now or datetime.now(UTC)
    factory = session_factory or SessionLocal
    with factory.begin() as db:
        work = _owned_work(db, claim)
        orchestration = db.get(ResearchOrchestration, claim.orchestration_id)
        formal = db.get(FormalResearch, claim.formal_research_id)
        run = _latest_run(db, claim.formal_research_id)
        work.current_run_id = run.run_id if run else work.current_run_id
        work.resume_run_id = work.current_run_id
        work.status = "interrupted"
        work.last_error_kind = "ResearchStopRequested"
        work.last_error = reason[:2000]
        _release_lease(work, stopped_at)
        transition_orchestration(orchestration, "stopped", reason="授权停止已在阶段安全点生效")
        formal.phase = "stopped"
        formal.completed_at = stopped_at
        append_research_event(
            db,
            formal.id,
            "research_stopped",
            {"reason": reason},
            run_id=work.current_run_id,
            occurred_at=stopped_at,
        )


def _mark_work_running(
    claim: ClaimedResearchWork,
    factory: SessionFactory,
    started_at: datetime,
) -> str:
    with factory.begin() as db:
        try:
            work = _owned_work(db, claim, expected=("leased",))
        except RuntimeError:
            return "lease_lost"
        orchestration = db.get(ResearchOrchestration, claim.orchestration_id)
        formal = db.get(FormalResearch, claim.formal_research_id)
        if orchestration.state in {"stopping", "blocked", "stopped"} or work.stop_requested_at is not None:
            work.status = "interrupted"
            _release_lease(work, started_at)
            if orchestration.state != "stopped":
                transition_orchestration(orchestration, "stopped", reason="启动前收到停止或阻塞状态")
            formal.phase = "stopped"
            formal.completed_at = started_at
            append_research_event(
                db,
                formal.id,
                "research_stopped_before_attempt",
                {"workerId": claim.worker_id},
                occurred_at=started_at,
            )
            return "stopped"
        work.status = "running"
        if orchestration.state == "queued":
            transition_orchestration(orchestration, "running", reason=None)
        formal.phase = "active"
        append_research_event(
            db,
            formal.id,
            "research_attempt_started",
            {"attemptCount": work.attempt_count, "workerId": claim.worker_id},
            run_id=work.resume_run_id,
            occurred_at=started_at,
        )
        return "running"


def _execute_with_runner(
    claim: ClaimedResearchWork,
    factory: SessionFactory,
    should_stop: Callable[[], bool],
) -> ResearchRunResult:
    with factory() as db:
        plan = db.get(FrozenResearchPlan, claim.plan_id)
        output_root = Path(os.getenv("RESEARCH_ARTIFACT_ROOT", "outputs/research-runs"))
        if claim.resume_run_id:
            return resume_quant_research(
                db,
                claim.resume_run_id,
                output_root,
                should_stop=should_stop,
            )
        return run_quant_research(
            db,
            dict(plan.plan_json["runConfig"]),
            output_root,
            formal_research_id=claim.formal_research_id,
            should_stop=should_stop,
        )


def _heartbeat_loop(
    done: Event,
    stop_event: Event,
    claim: ClaimedResearchWork,
    factory: SessionFactory,
    interval_seconds: int,
    lease_seconds: int,
) -> None:
    while not done.wait(interval_seconds):
        try:
            status = heartbeat_research_work(
                claim,
                session_factory=factory,
                lease_seconds=lease_seconds,
            )
        except Exception:
            stop_event.set()
            return
        if status != "ok":
            stop_event.set()
            return


def _owned_work(
    db: Session,
    claim: ClaimedResearchWork,
    *,
    expected: tuple[str, ...] = ("leased", "running"),
) -> ResearchWorkItem:
    work = db.scalar(
        select(ResearchWorkItem)
        .where(ResearchWorkItem.id == claim.work_item_id)
        .with_for_update()
    )
    if work is None or work.status not in expected or work.lease_owner != claim.worker_id:
        raise RuntimeError("研究 work item 租约已丢失")
    return work


def _latest_active_run(db: Session, formal_research_id: str) -> ResearchRun | None:
    return db.scalar(
        select(ResearchRun)
        .where(
            ResearchRun.formal_research_id == formal_research_id,
            ResearchRun.status == "running",
        )
        .order_by(ResearchRun.started_at.desc(), ResearchRun.run_id.desc())
        .limit(1)
    )


def _latest_run(db: Session, formal_research_id: str) -> ResearchRun | None:
    return db.scalar(
        select(ResearchRun)
        .where(ResearchRun.formal_research_id == formal_research_id)
        .order_by(ResearchRun.started_at.desc(), ResearchRun.run_id.desc())
        .limit(1)
    )


def _release_lease(work: ResearchWorkItem, at: datetime) -> None:
    work.lease_owner = None
    work.lease_expires_at = None
    work.heartbeat_at = at
    work.updated_at = at


def _block_exhausted_work(db: Session, now: datetime) -> None:
    exhausted = db.scalars(
        select(ResearchWorkItem).where(
            ResearchWorkItem.attempt_count >= ResearchWorkItem.max_attempts,
            or_(
                ResearchWorkItem.status == "queued",
                and_(
                    ResearchWorkItem.status.in_(("leased", "running")),
                    ResearchWorkItem.lease_expires_at < now,
                ),
            ),
        )
    ).all()
    for work in exhausted:
        run = _latest_active_run(db, work.formal_research_id)
        if run is not None:
            run.status = "interrupted"
            run.error = "RetryBudgetExhausted: 最后一次尝试的 Worker 租约已过期"
            run.finished_at = now
            run.heartbeat_at = now
            work.current_run_id = run.run_id
            work.resume_run_id = run.run_id
        work.status = "failed"
        work.last_error_kind = "RetryBudgetExhausted"
        work.last_error = "研究重试预算已耗尽"
        _release_lease(work, now)
        orchestration = db.get(ResearchOrchestration, work.orchestration_id)
        transition_orchestration(orchestration, "blocked", reason="研究重试预算已耗尽")
        formal = db.get(FormalResearch, work.formal_research_id)
        formal.phase = "stopped"
        formal.completed_at = now
        append_research_event(
            db,
            formal.id,
            "research_blocked",
            {"errorKind": "RetryBudgetExhausted", "runId": work.current_run_id},
            run_id=work.current_run_id,
            occurred_at=now,
        )


def _finalize_expired_stops(db: Session, now: datetime) -> None:
    stopped = db.scalars(
        select(ResearchWorkItem).where(
            ResearchWorkItem.status.in_(("leased", "running")),
            ResearchWorkItem.stop_requested_at.is_not(None),
            ResearchWorkItem.lease_expires_at < now,
        )
    ).all()
    for work in stopped:
        run = _latest_active_run(db, work.formal_research_id)
        if run is not None:
            run.status = "interrupted"
            run.error = "ResearchStopRequested: Worker 失联后由过期租约落实停止"
            run.finished_at = now
            run.heartbeat_at = now
            work.current_run_id = run.run_id
            work.resume_run_id = run.run_id
        work.status = "interrupted"
        work.last_error_kind = "ResearchStopRequested"
        work.last_error = "Worker 失联后由过期租约落实停止"
        _release_lease(work, now)
        orchestration = db.get(ResearchOrchestration, work.orchestration_id)
        if orchestration.state != "stopped":
            transition_orchestration(orchestration, "stopped", reason="过期租约已落实停止请求")
        formal = db.get(FormalResearch, work.formal_research_id)
        formal.phase = "stopped"
        formal.completed_at = now
        append_research_event(
            db,
            formal.id,
            "research_stopped_after_lease_expiry",
            {"runId": work.current_run_id},
            run_id=work.current_run_id,
            occurred_at=now,
        )


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in Path(path).rglob("*") if item.is_file())


def _positive_int_env(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} 必须大于 0")
    return value


def _main() -> None:
    assert_schema_revision_at_head(engine)
    client = GitHubIssueClient.from_env()
    limits = ResearchServerLimits.from_env()
    app_git_commit = os.getenv("APP_GIT_COMMIT", "")
    app_git_ref = os.getenv("APP_GIT_REF", "")
    poll_seconds = _positive_int_env("RESEARCH_WORKER_POLL_SECONDS", 30)
    heartbeat_seconds = _positive_int_env("RESEARCH_WORKER_HEARTBEAT_SECONDS", 20)
    lease_seconds = _positive_int_env("RESEARCH_WORKER_LEASE_SECONDS", 120)
    if heartbeat_seconds >= lease_seconds:
        raise ValueError("RESEARCH_WORKER_HEARTBEAT_SECONDS 必须小于租约秒数")
    worker_id = os.getenv("RESEARCH_WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}"
    stopped = Event()

    def stop_signal(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, stop_signal)
    signal.signal(signal.SIGTERM, stop_signal)
    github_available = Event()
    first_poll_done = Event()

    def poll_loop() -> None:
        while not stopped.is_set():
            try:
                poll = poll_research_issues_once(
                    client,
                    SessionLocal,
                    app_git_commit=app_git_commit,
                    app_git_ref=app_git_ref,
                    limits=limits,
                )
                if poll.github_available:
                    github_available.set()
                else:
                    github_available.clear()
                for error in poll.errors:
                    print(error, flush=True)
            except Exception as exc:
                github_available.clear()
                print(f"GitHub 研究轮询异常：{type(exc).__name__}: {exc}", flush=True)
            finally:
                first_poll_done.set()
            stopped.wait(poll_seconds)

    poller = Thread(target=poll_loop, daemon=True)
    poller.start()
    first_poll_done.wait(timeout=30)
    try:
        while not stopped.is_set():
            claim = claim_next_research_work(
                worker_id,
                github_available=github_available.is_set(),
                lease_seconds=lease_seconds,
            )
            if claim is not None:
                outcome = execute_claimed_research_work(
                    claim,
                    heartbeat_interval_seconds=heartbeat_seconds,
                    lease_seconds=lease_seconds,
                )
                print(
                    f"研究 work item {claim.work_item_id} 本轮结果：{outcome}",
                    flush=True,
                )
                continue
            stopped.wait(min(poll_seconds, 5))
    finally:
        stopped.set()
        poller.join(timeout=5)


if __name__ == "__main__":
    _main()
