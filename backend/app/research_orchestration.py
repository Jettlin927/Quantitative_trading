from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Iterable
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    FormalResearch,
    FrozenResearchPlan,
    ResearchEvent,
    ResearchOrchestration,
    ResearchPlanApproval,
    ResearchRun,
    ResearchWorkItem,
    StrategyDefinition,
)
from .quant_research.strategy_registry import resolve_strategy_definition
from .research_plan import PreparedResearchPlan


AUTHORIZED_RESEARCH_APPROVER = "Jettlin927"
STATE_LABELS = {
    "pending_approval": "研究:待批准",
    "approved": "研究:已批准",
    "queued": "研究:已批准",
    "running": "研究:运行中",
    "stopping": "研究:运行中",
    "publishing": "研究:运行中",
    "published": "研究:已发布",
    "stopped": "研究:受阻",
    "blocked": "研究:受阻",
}
RESEARCH_STATE_LABELS = frozenset(STATE_LABELS.values())
RESUME_PATTERN = re.compile(r"^恢复研究 ([0-9a-f]{64}) ([0-9a-f-]{36})$")

ALLOWED_TRANSITIONS = {
    "pending_approval": {"approved", "blocked", "stopped"},
    "approved": {"queued", "blocked", "stopped"},
    "queued": {"running", "stopping", "blocked", "stopped"},
    "running": {"queued", "stopping", "publishing", "blocked", "stopped"},
    "stopping": {"blocked", "stopped"},
    "publishing": {"published", "blocked", "stopped"},
    "blocked": {"pending_approval", "approved", "queued", "stopped"},
    "published": set(),
    "stopped": {"queued"},
}


class ResearchAuthorizationError(RuntimeError):
    pass


class ResearchStateTransitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class IssueSnapshot:
    number: int
    state: str
    body: str
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommentSnapshot:
    id: int
    author_login: str
    body: str


@dataclass(frozen=True)
class OrchestrationResult:
    issue_number: int
    plan_sha256: str
    state: str
    desired_label: str
    approval_found: bool
    queue_created: bool
    reason: str | None = None


def apply_issue_plan(
    db: Session,
    issue: IssueSnapshot,
    comments: Iterable[CommentSnapshot],
    prepared: PreparedResearchPlan,
    *,
    app_git_commit: str,
    app_git_ref: str,
    authorization_write_confirmed: bool,
    now: datetime | None = None,
) -> OrchestrationResult:
    current_time = now or datetime.now(timezone.utc)
    ordered_comments = list(comments)
    _invalidate_reused_plan_body_edit(db, issue, prepared, current_time)
    plan, orchestration, created = _freeze_plan(db, issue, prepared)
    _supersede_older_plans(db, issue.number, plan, current_time)

    approval = _bound_approval_comment(db, orchestration, ordered_comments, prepared)
    if issue.state.upper() != "OPEN":
        _prevent_closed_issue_start(db, orchestration, current_time)
        return _result(orchestration, prepared, bool(approval), False)
    if orchestration.approval_invalidated:
        return _result(
            orchestration,
            prepared,
            bool(approval),
            False,
            reason=(
                f"{orchestration.state_reason}；该计划的批准已永久失效，"
                "必须形成新计划哈希并重新批准"
                if orchestration.state_reason
                else "该计划的批准已永久失效；必须形成新计划哈希并重新批准"
            ),
        )
    if approval is None:
        _invalidate_missing_approval(db, orchestration, current_time)
        if orchestration.formal_research_id is None and orchestration.state == "blocked":
            transition_orchestration(orchestration, "pending_approval", reason=None)
        elif orchestration.formal_research_id is None:
            orchestration.state_reason = None
        return _result(orchestration, prepared, False, False)
    if not authorization_write_confirmed:
        return _result(
            orchestration,
            prepared,
            True,
            False,
            reason="GitHub 写权限尚未确认，未创建正式研究或队列",
        )

    identity_error = _validate_code_identity(
        db, plan, prepared, app_git_commit, app_git_ref
    )
    if identity_error:
        _set_state(orchestration, "blocked", identity_error)
        return _result(orchestration, prepared, True, False)

    queue_created = False
    if orchestration.formal_research_id is None:
        approval_record = ResearchPlanApproval(
            id=str(uuid4()),
            plan_id=plan.id,
            action="approved",
            actor_login=approval.author_login,
            comment_id=approval.id,
            comment_body=approval.body,
            plan_sha256=plan.plan_sha256,
        )
        formal = FormalResearch(
            id=str(uuid4()),
            plan_id=plan.id,
            approval_id=approval_record.id,
            phase="approved",
        )
        db.add(approval_record)
        db.flush()
        db.add(formal)
        db.flush()
        orchestration.formal_research_id = formal.id
        transition_orchestration(orchestration, "approved", reason=None)
        append_research_event(
            db,
            formal.id,
            "plan_approved",
            {
                "issueNumber": issue.number,
                "planSha256": plan.plan_sha256,
                "approvalCommentId": approval.id,
                "actorLogin": approval.author_login,
            },
        )
        transition_orchestration(orchestration, "queued", reason=None)
        formal.phase = "approved"
        budget = prepared.normalized["resourceBudget"]
        work_item = ResearchWorkItem(
            id=str(uuid4()),
            orchestration_id=orchestration.id,
            formal_research_id=formal.id,
            status="queued",
            attempt_count=0,
            max_attempts=int(budget["maxRetries"]) + 1,
            next_attempt_at=current_time,
        )
        db.add(work_item)
        append_research_event(
            db,
            formal.id,
            "research_queued",
            {
                "workItemId": work_item.id,
                "maxAttempts": work_item.max_attempts,
                "resourceBudget": budget,
            },
        )
        queue_created = True

    stop = _latest_exact_comment(
        ordered_comments,
        prepared.stop_comment,
        after_comment_id=approval.id,
    )
    resume = _latest_resume_comment(
        ordered_comments,
        prepared.plan_sha256,
        after_comment_id=approval.id,
    )
    if resume is not None and (stop is None or resume.id > stop.id):
        request_research_resume(db, orchestration, resume, current_time)
    elif stop is not None:
        request_research_stop(db, orchestration, stop, current_time)

    if created:
        orchestration.updated_at = current_time
    return _result(orchestration, prepared, True, queue_created)


def transition_orchestration(
    orchestration: ResearchOrchestration,
    new_state: str,
    *,
    reason: str | None = None,
) -> None:
    if new_state == orchestration.state:
        orchestration.state_reason = reason
        return
    allowed = ALLOWED_TRANSITIONS.get(orchestration.state)
    if allowed is None or new_state not in allowed:
        raise ResearchStateTransitionError(
            f"非法研究编排状态迁移：{orchestration.state} -> {new_state}"
        )
    orchestration.state = new_state
    orchestration.state_reason = reason


def invalidate_issue_plan(
    db: Session,
    issue_number: int,
    issue_body: str,
    reason: str,
    *,
    now: datetime | None = None,
) -> bool:
    invalidated_at = now or datetime.now(timezone.utc)
    body_sha256 = sha256(issue_body.encode("utf-8")).hexdigest()
    orchestration = db.scalar(
        select(ResearchOrchestration)
        .join(FrozenResearchPlan, FrozenResearchPlan.id == ResearchOrchestration.plan_id)
        .where(FrozenResearchPlan.issue_number == issue_number)
        .order_by(FrozenResearchPlan.version.desc())
        .limit(1)
    )
    if orchestration is None or orchestration.last_issue_body_sha256 == body_sha256:
        return False
    orchestration.last_issue_body_sha256 = body_sha256
    orchestration.state_reason = reason
    if (
        orchestration.formal_research_id is None
        and orchestration.state == "pending_approval"
    ):
        return True
    orchestration.approval_invalidated = True
    if orchestration.state in {"published", "stopped"}:
        return True
    if orchestration.formal_research_id is None:
        _set_state(orchestration, "blocked", reason)
        return True
    work = db.scalar(
        select(ResearchWorkItem)
        .where(ResearchWorkItem.formal_research_id == orchestration.formal_research_id)
        .with_for_update()
    )
    formal = db.get(FormalResearch, orchestration.formal_research_id)
    if work is not None and work.status in {"leased", "running"}:
        work.stop_requested_at = invalidated_at
        if orchestration.state != "stopping":
            transition_orchestration(orchestration, "stopping", reason=reason)
        event_type = "invalid_plan_stop_requested"
    else:
        if work is not None and work.status == "queued":
            work.status = "interrupted"
            work.stop_requested_at = invalidated_at
        _set_state(orchestration, "blocked", reason)
        formal.phase = "stopped"
        formal.completed_at = invalidated_at
        event_type = "plan_approval_invalidated"
    append_research_event(
        db,
        orchestration.formal_research_id,
        event_type,
        {"issueNumber": issue_number, "reason": reason},
        run_id=work.current_run_id if work else None,
        occurred_at=invalidated_at,
    )
    return True


def append_research_event(
    db: Session,
    formal_research_id: str,
    event_type: str,
    payload: dict,
    *,
    run_id: str | None = None,
    occurred_at: datetime | None = None,
) -> ResearchEvent:
    db.scalar(
        select(FormalResearch)
        .where(FormalResearch.id == formal_research_id)
        .with_for_update()
    )
    sequence = db.scalar(
        select(func.coalesce(func.max(ResearchEvent.sequence_no), 0)).where(
            ResearchEvent.formal_research_id == formal_research_id
        )
    )
    event = ResearchEvent(
        id=str(uuid4()),
        formal_research_id=formal_research_id,
        run_id=run_id,
        sequence_no=int(sequence or 0) + 1,
        event_type=event_type,
        payload_json=payload,
        occurred_at=occurred_at or datetime.now(timezone.utc),
    )
    db.add(event)
    db.flush()
    return event


def request_research_stop(
    db: Session,
    orchestration: ResearchOrchestration,
    comment: CommentSnapshot,
    now: datetime,
) -> None:
    if comment.author_login != AUTHORIZED_RESEARCH_APPROVER:
        raise ResearchAuthorizationError("只有授权用户可以停止正式研究")
    if orchestration.formal_research_id is None or orchestration.state in {"published", "stopped"}:
        return
    work = db.scalar(
        select(ResearchWorkItem)
        .where(ResearchWorkItem.formal_research_id == orchestration.formal_research_id)
        .with_for_update()
    )
    if work is None:
        return
    work.stop_requested_at = now
    if work.status == "queued":
        work.status = "interrupted"
        transition_orchestration(orchestration, "stopped", reason="授权用户在启动前停止研究")
        formal = db.get(FormalResearch, orchestration.formal_research_id)
        formal.phase = "stopped"
        formal.completed_at = now
        event_type = "research_stopped_before_start"
    elif work.status in {"leased", "running"}:
        transition_orchestration(orchestration, "stopping", reason="等待当前安全点停止")
        event_type = "research_stop_requested"
    elif work.status == "succeeded" and orchestration.state in {"running", "publishing"}:
        transition_orchestration(orchestration, "stopped", reason="授权用户在评价或发布前停止研究")
        formal = db.get(FormalResearch, orchestration.formal_research_id)
        formal.phase = "stopped"
        formal.completed_at = now
        event_type = "research_stopped_after_run"
    else:
        return
    append_research_event(
        db,
        orchestration.formal_research_id,
        event_type,
        {"commentId": comment.id, "actorLogin": comment.author_login},
        run_id=work.current_run_id,
        occurred_at=now,
    )


def request_research_resume(
    db: Session,
    orchestration: ResearchOrchestration,
    comment: CommentSnapshot,
    now: datetime,
) -> None:
    match = RESUME_PATTERN.fullmatch(comment.body)
    if comment.author_login != AUTHORIZED_RESEARCH_APPROVER or match is None:
        raise ResearchAuthorizationError("显式恢复评论无效")
    if (
        orchestration.formal_research_id is None
        or orchestration.state not in {"blocked", "stopped"}
        or orchestration.superseded_by_plan_id is not None
    ):
        return
    work = db.scalar(
        select(ResearchWorkItem)
        .where(ResearchWorkItem.formal_research_id == orchestration.formal_research_id)
        .with_for_update()
    )
    run = db.get(ResearchRun, match.group(2))
    plan = db.get(FrozenResearchPlan, orchestration.plan_id)
    if (
        work is None
        or work.status not in {"failed", "interrupted"}
        or run is None
        or run.status != "interrupted"
        or run.formal_research_id != orchestration.formal_research_id
        or plan.plan_sha256 != match.group(1)
        or orchestration.approval_invalidated
        or work.attempt_count >= work.max_attempts
    ):
        return
    work.resume_run_id = run.run_id
    work.current_run_id = run.run_id
    work.status = "queued"
    work.next_attempt_at = now
    work.stop_requested_at = None
    work.last_error = None
    work.last_error_kind = None
    transition_orchestration(orchestration, "queued", reason=None)
    formal = db.get(FormalResearch, orchestration.formal_research_id)
    formal.phase = "approved"
    formal.completed_at = None
    append_research_event(
        db,
        orchestration.formal_research_id,
        "research_resume_requested",
        {"commentId": comment.id, "runId": run.run_id},
        run_id=run.run_id,
        occurred_at=now,
    )


def _freeze_plan(
    db: Session,
    issue: IssueSnapshot,
    prepared: PreparedResearchPlan,
) -> tuple[FrozenResearchPlan, ResearchOrchestration, bool]:
    normalized = prepared.normalized
    strategy_payload = normalized["strategy"]
    strategy = db.get(StrategyDefinition, strategy_payload["id"])
    if strategy is None:
        strategy = StrategyDefinition(
            strategy_id=strategy_payload["id"],
            display_name=strategy_payload["displayName"],
            lifecycle_status="活跃",
            economic_thesis=normalized["economicHypothesis"],
            registry_version=strategy_payload["version"],
            code_commit=strategy_payload["codeCommit"],
            metadata_json={"source": "github_research_plan"},
        )
        db.add(strategy)
        db.flush()

    plan = db.scalar(
        select(FrozenResearchPlan).where(
            FrozenResearchPlan.plan_sha256 == prepared.plan_sha256
        )
    )
    created = False
    if plan is not None and plan.issue_number != issue.number:
        raise ResearchAuthorizationError("同一冻结计划哈希已绑定其他 Issue")
    if plan is None:
        version = db.scalar(
            select(func.coalesce(func.max(FrozenResearchPlan.version), 0)).where(
                FrozenResearchPlan.issue_number == issue.number
            )
        )
        plan = FrozenResearchPlan(
            id=str(uuid4()),
            strategy_id=strategy.strategy_id,
            issue_number=issue.number,
            version=int(version or 0) + 1,
            schema_version=normalized["schemaVersion"],
            plan_sha256=prepared.plan_sha256,
            code_commit=strategy_payload["codeCommit"],
            plan_json=normalized,
        )
        db.add(plan)
        db.flush()
        created = True
    orchestration = db.scalar(
        select(ResearchOrchestration).where(ResearchOrchestration.plan_id == plan.id)
    )
    if orchestration is None:
        orchestration = ResearchOrchestration(
            id=str(uuid4()),
            plan_id=plan.id,
            issue_number=issue.number,
            state="pending_approval",
            last_issue_body_sha256=sha256(issue.body.encode("utf-8")).hexdigest(),
        )
        db.add(orchestration)
        db.flush()
        created = True
    else:
        orchestration.last_issue_body_sha256 = sha256(issue.body.encode("utf-8")).hexdigest()
    return plan, orchestration, created


def _invalidate_reused_plan_body_edit(
    db: Session,
    issue: IssueSnapshot,
    prepared: PreparedResearchPlan,
    now: datetime,
) -> None:
    body_sha256 = sha256(issue.body.encode("utf-8")).hexdigest()
    orchestration = db.scalar(
        select(ResearchOrchestration)
        .join(FrozenResearchPlan, FrozenResearchPlan.id == ResearchOrchestration.plan_id)
        .where(
            FrozenResearchPlan.issue_number == issue.number,
            FrozenResearchPlan.plan_sha256 == prepared.plan_sha256,
        )
        .limit(1)
    )
    if orchestration is None or orchestration.last_issue_body_sha256 == body_sha256:
        return
    invalidate_issue_plan(
        db,
        issue.number,
        issue.body,
        "Issue 正文已编辑，原批准立即失效",
        now=now,
    )


def _bound_approval_comment(
    db: Session,
    orchestration: ResearchOrchestration,
    comments: list[CommentSnapshot],
    prepared: PreparedResearchPlan,
) -> CommentSnapshot | None:
    matches = [
        comment
        for comment in comments
        if comment.author_login == AUTHORIZED_RESEARCH_APPROVER
        and comment.body == prepared.approval_comment
    ]
    if orchestration.formal_research_id is None:
        return min(matches, key=lambda comment: comment.id, default=None)
    formal = db.get(FormalResearch, orchestration.formal_research_id)
    approval = db.get(ResearchPlanApproval, formal.approval_id) if formal else None
    if approval is None:
        return None
    return next((comment for comment in matches if comment.id == approval.comment_id), None)


def _supersede_older_plans(
    db: Session,
    issue_number: int,
    current: FrozenResearchPlan,
    now: datetime,
) -> None:
    older = db.scalars(
        select(ResearchOrchestration)
        .join(FrozenResearchPlan, FrozenResearchPlan.id == ResearchOrchestration.plan_id)
        .where(
            FrozenResearchPlan.issue_number == issue_number,
            FrozenResearchPlan.id != current.id,
            FrozenResearchPlan.version < current.version,
        )
        .order_by(FrozenResearchPlan.version)
    ).all()
    for orchestration in older:
        if orchestration.superseded_by_plan_id is not None:
            continue
        orchestration.superseded_by_plan_id = current.id
        orchestration.approval_invalidated = True
        reason = f"Issue 机器计划已编辑，新计划哈希为 {current.plan_sha256}"
        if orchestration.state in {"queued", "approved", "pending_approval", "blocked"}:
            transition_orchestration(orchestration, "stopped", reason=reason)
            if orchestration.formal_research_id:
                work = db.scalar(
                    select(ResearchWorkItem)
                    .where(
                        ResearchWorkItem.formal_research_id
                        == orchestration.formal_research_id
                    )
                    .with_for_update()
                )
                if work and work.status == "queued":
                    work.status = "interrupted"
                    work.stop_requested_at = now
                formal = db.get(FormalResearch, orchestration.formal_research_id)
                formal.phase = "stopped"
                formal.completed_at = now
                append_research_event(
                    db,
                    orchestration.formal_research_id,
                    "plan_approval_invalidated",
                    {"supersededByPlanId": current.id, "newPlanSha256": current.plan_sha256},
                    run_id=work.current_run_id if work else None,
                    occurred_at=now,
                )
        elif orchestration.state in {"running", "stopping"}:
            work = db.scalar(
                select(ResearchWorkItem)
                .where(ResearchWorkItem.formal_research_id == orchestration.formal_research_id)
                .with_for_update()
            )
            if work and work.status in {"leased", "running"}:
                if orchestration.state == "running":
                    transition_orchestration(orchestration, "stopping", reason=reason)
                else:
                    orchestration.state_reason = reason
                work.stop_requested_at = now
            else:
                transition_orchestration(orchestration, "stopped", reason=reason)
                formal = db.get(FormalResearch, orchestration.formal_research_id)
                formal.phase = "stopped"
                formal.completed_at = now
            append_research_event(
                db,
                orchestration.formal_research_id,
                "plan_approval_invalidated",
                {"supersededByPlanId": current.id, "newPlanSha256": current.plan_sha256},
                run_id=work.current_run_id if work else None,
                occurred_at=now,
            )
        elif orchestration.state == "publishing":
            transition_orchestration(orchestration, "stopped", reason=reason)
            formal = db.get(FormalResearch, orchestration.formal_research_id)
            formal.phase = "stopped"
            formal.completed_at = now
            append_research_event(
                db,
                orchestration.formal_research_id,
                "plan_approval_invalidated",
                {"supersededByPlanId": current.id, "newPlanSha256": current.plan_sha256},
                occurred_at=now,
            )


def _validate_code_identity(
    db: Session,
    plan: FrozenResearchPlan,
    prepared: PreparedResearchPlan,
    app_git_commit: str,
    app_git_ref: str,
) -> str | None:
    if not re.fullmatch(r"[0-9a-f]{40,64}", app_git_commit or ""):
        return "运行环境未注入真实 APP_GIT_COMMIT，拒绝启动正式研究"
    if plan.code_commit != app_git_commit:
        return "冻结计划代码身份与已部署 main 提交不一致，拒绝启动未合并或未部署代码"
    if app_git_ref != "refs/heads/main":
        return "运行镜像不是从 refs/heads/main 构建，拒绝启动未合并策略"
    try:
        resolve_strategy_definition(prepared.normalized["runConfig"])
    except ValueError as exc:
        return f"策略未静态登记或身份不匹配：{exc}"
    strategy = db.get(StrategyDefinition, plan.strategy_id)
    if strategy is None or strategy.lifecycle_status != "活跃":
        return "策略档案不存在或生命周期不是活跃"
    if strategy.registry_version != prepared.normalized["strategy"]["version"]:
        return "策略档案版本与冻结计划不一致"
    strategy.code_commit = app_git_commit
    return None


def _latest_exact_comment(
    comments: list[CommentSnapshot], body: str, *, after_comment_id: int
) -> CommentSnapshot | None:
    matches = [
        item
        for item in comments
        if item.id > after_comment_id
        and item.author_login == AUTHORIZED_RESEARCH_APPROVER
        and item.body == body
    ]
    return max(matches, key=lambda item: item.id, default=None)


def _latest_resume_comment(
    comments: list[CommentSnapshot], plan_sha256: str, *, after_comment_id: int
) -> CommentSnapshot | None:
    matches = []
    for item in comments:
        if item.id <= after_comment_id or item.author_login != AUTHORIZED_RESEARCH_APPROVER:
            continue
        match = RESUME_PATTERN.fullmatch(item.body)
        if match and match.group(1) == plan_sha256:
            matches.append(item)
    return max(matches, key=lambda item: item.id, default=None)


def _set_state(orchestration: ResearchOrchestration, state: str, reason: str) -> None:
    if orchestration.state == state:
        orchestration.state_reason = reason
    else:
        transition_orchestration(orchestration, state, reason=reason)


def _invalidate_missing_approval(
    db: Session,
    orchestration: ResearchOrchestration,
    now: datetime,
) -> None:
    if orchestration.formal_research_id is None or orchestration.state in {"published", "stopped"}:
        return
    reason = "原批准评论已删除、编辑或不再精确匹配当前计划哈希"
    orchestration.approval_invalidated = True
    orchestration.state_reason = reason
    already_recorded = db.scalar(
        select(ResearchEvent.id)
        .where(
            ResearchEvent.formal_research_id == orchestration.formal_research_id,
            ResearchEvent.event_type == "approval_comment_invalidated",
        )
        .limit(1)
    )
    if already_recorded is not None:
        return
    work = db.scalar(
        select(ResearchWorkItem)
        .where(ResearchWorkItem.formal_research_id == orchestration.formal_research_id)
        .with_for_update()
    )
    formal = db.get(FormalResearch, orchestration.formal_research_id)
    if work is not None and work.status in {"leased", "running"}:
        work.stop_requested_at = now
        if orchestration.state != "stopping":
            transition_orchestration(orchestration, "stopping", reason=reason)
        else:
            orchestration.state_reason = reason
    else:
        if work is not None and work.status == "queued":
            work.status = "interrupted"
            work.stop_requested_at = now
        _set_state(orchestration, "blocked", reason)
        formal.phase = "stopped"
        formal.completed_at = now
    append_research_event(
        db,
        orchestration.formal_research_id,
        "approval_comment_invalidated",
        {"reason": reason},
        run_id=work.current_run_id if work else None,
        occurred_at=now,
    )


def _prevent_closed_issue_start(
    db: Session,
    orchestration: ResearchOrchestration,
    now: datetime,
) -> None:
    reason = "研究 Issue 已关闭，拒绝启动或继续新的研究阶段"
    if orchestration.state in {"published", "stopped"}:
        return
    if orchestration.formal_research_id is None:
        if orchestration.state in {"blocked", "stopped"} and orchestration.state_reason == reason:
            return
        _set_state(orchestration, "blocked", reason)
        return
    work = db.scalar(
        select(ResearchWorkItem)
        .where(ResearchWorkItem.formal_research_id == orchestration.formal_research_id)
        .with_for_update()
    )
    if work is None:
        if orchestration.state in {"blocked", "stopped"} and orchestration.state_reason == reason:
            return
        _set_state(orchestration, "blocked", reason)
        return
    if work.status in {"leased", "running"}:
        if orchestration.state == "stopping" and work.stop_requested_at is not None:
            return
        work.stop_requested_at = now
        if orchestration.state != "stopping":
            transition_orchestration(orchestration, "stopping", reason=reason)
        event_type = "closed_issue_stop_requested"
    else:
        if orchestration.state in {"blocked", "stopped"} and orchestration.state_reason == reason:
            return
        if work.status == "queued":
            work.status = "interrupted"
            work.stop_requested_at = now
        _set_state(orchestration, "blocked", reason)
        formal = db.get(FormalResearch, orchestration.formal_research_id)
        formal.phase = "stopped"
        formal.completed_at = now
        event_type = "closed_issue_blocked"
    append_research_event(
        db,
        orchestration.formal_research_id,
        event_type,
        {"issueNumber": orchestration.issue_number},
        run_id=work.current_run_id,
        occurred_at=now,
    )


def _result(
    orchestration: ResearchOrchestration,
    prepared: PreparedResearchPlan,
    approval_found: bool,
    queue_created: bool,
    reason: str | None = None,
) -> OrchestrationResult:
    return OrchestrationResult(
        issue_number=orchestration.issue_number,
        plan_sha256=prepared.plan_sha256,
        state=orchestration.state,
        desired_label=STATE_LABELS[orchestration.state],
        approval_found=approval_found,
        queue_created=queue_created,
        reason=reason or orchestration.state_reason,
    )
