from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .json_safety import json_safe_value
from .models import (
    FollowUpResearchProposal,
    FormalResearch,
    FrozenResearchPlan,
    ResearchEvaluation,
    ResearchEvaluationRun,
    ResearchEvidenceRef,
    ResearchEvent,
    ResearchPlanApproval,
    ResearchPublication,
    ResearchRun,
    StrategyDefinition,
)
from .schemas import (
    FollowUpResearchProposalOut,
    FormalResearchDetailOut,
    FormalResearchSummaryOut,
    FrozenResearchPlanOut,
    ResearchEvaluationOut,
    ResearchEvidenceRefOut,
    ResearchEventOut,
    ResearchPlanApprovalOut,
    ResearchPublicationOut,
    ResearchPublicationProjectionOut,
    ResearchPublicationRunOut,
    ResearchRunSummaryOut,
    StrategyProfileOut,
    StrategyProfileSummaryOut,
)


PUBLICATION_SUCCESS_EVENT_TYPES = frozenset(
    {"research_published", "research_publication_recovered"}
)
PUBLICATION_LIFECYCLE_EVENT_TYPES = frozenset(
    {*PUBLICATION_SUCCESS_EVENT_TYPES, "research_publication_failed"}
)


def list_strategy_profiles(db: Session) -> list[StrategyProfileSummaryOut]:
    strategies = db.scalars(select(StrategyDefinition).order_by(StrategyDefinition.strategy_id)).all()
    return [_strategy_summary(db, strategy) for strategy in strategies]


def get_strategy_profile(db: Session, strategy_id: str) -> StrategyProfileOut | None:
    strategy = db.get(StrategyDefinition, strategy_id)
    if strategy is None:
        return None

    formal_researches = db.scalars(
        select(FormalResearch)
        .join(FrozenResearchPlan, FrozenResearchPlan.id == FormalResearch.plan_id)
        .where(FrozenResearchPlan.strategy_id == strategy_id)
        .order_by(FormalResearch.created_at, FormalResearch.id)
    ).all()
    proposals = db.scalars(
        select(FollowUpResearchProposal)
        .where(FollowUpResearchProposal.strategy_id == strategy_id)
        .order_by(FollowUpResearchProposal.created_at, FollowUpResearchProposal.id)
    ).all()
    return StrategyProfileOut(
        strategy_id=strategy.strategy_id,
        display_name=strategy.display_name,
        lifecycle_status=strategy.lifecycle_status,
        economic_thesis=strategy.economic_thesis,
        registry_version=strategy.registry_version,
        code_commit=strategy.code_commit,
        metadata_json=json_safe_value(strategy.metadata_json or {}),
        formal_researches=[_formal_research_summary(db, item) for item in formal_researches],
        follow_up_proposals=[_proposal_out(item) for item in proposals],
        created_at=strategy.created_at,
        updated_at=strategy.updated_at,
    )


def get_formal_research_detail(db: Session, research_id: str) -> FormalResearchDetailOut | None:
    research = db.get(FormalResearch, research_id)
    if research is None:
        return None
    plan = db.get(FrozenResearchPlan, research.plan_id)
    approval = db.get(ResearchPlanApproval, research.approval_id)
    if plan is None or approval is None:
        raise RuntimeError("正式研究缺少冻结计划或批准记录")

    runs = db.scalars(
        select(ResearchRun)
        .where(ResearchRun.formal_research_id == research_id)
        .order_by(ResearchRun.started_at, ResearchRun.run_id)
    ).all()
    events = db.scalars(
        select(ResearchEvent)
        .where(ResearchEvent.formal_research_id == research_id)
        .order_by(ResearchEvent.sequence_no)
    ).all()
    evaluations = db.scalars(
        select(ResearchEvaluation)
        .where(ResearchEvaluation.formal_research_id == research_id)
        .order_by(ResearchEvaluation.version)
    ).all()
    publications = db.scalars(
        select(ResearchPublication)
        .where(ResearchPublication.formal_research_id == research_id)
        .order_by(ResearchPublication.version)
    ).all()
    evaluation_ids = [item.id for item in evaluations]
    proposals = (
        db.scalars(
            select(FollowUpResearchProposal)
            .where(FollowUpResearchProposal.source_evaluation_id.in_(evaluation_ids))
            .order_by(FollowUpResearchProposal.created_at, FollowUpResearchProposal.id)
        ).all()
        if evaluation_ids
        else []
    )
    return FormalResearchDetailOut(
        id=research.id,
        origin=research.origin,
        phase=research.phase,
        plan=_plan_out(plan),
        approval=_approval_out(approval),
        runs=[_run_out(item) for item in runs],
        events=[_event_out(item) for item in events],
        evaluations=[_evaluation_out(db, item) for item in evaluations],
        publications=[_publication_out(item) for item in publications],
        follow_up_proposals=[_proposal_out(item) for item in proposals],
        created_at=research.created_at,
        completed_at=research.completed_at,
    )


def get_publication_projection(
    db: Session,
    publication_id: str,
) -> ResearchPublicationProjectionOut | None:
    publication = db.get(ResearchPublication, publication_id)
    if publication is None:
        return None
    evaluation = db.get(ResearchEvaluation, publication.evaluation_id)
    if evaluation is None:
        raise RuntimeError("研究发布缺少对应评价记录")
    run_ids = list(
        db.scalars(
            select(ResearchEvaluationRun.run_id)
            .where(ResearchEvaluationRun.evaluation_id == evaluation.id)
            .order_by(ResearchEvaluationRun.run_id)
        ).all()
    )
    runs = (
        db.scalars(
            select(ResearchRun)
            .where(ResearchRun.run_id.in_(run_ids))
            .order_by(ResearchRun.run_id)
        ).all()
        if run_ids
        else []
    )
    effective_publication_ids = _effective_publication_ids(
        db, [publication.formal_research_id]
    )
    successor = (
        db.scalar(
            select(ResearchEvaluation)
            .join(
                ResearchPublication,
                ResearchPublication.evaluation_id == ResearchEvaluation.id,
            )
            .where(
                ResearchEvaluation.supersedes_evaluation_id == evaluation.id,
                ResearchPublication.status == "published",
                ResearchPublication.id.in_(effective_publication_ids),
            )
            .order_by(ResearchEvaluation.version.desc())
            .limit(1)
        )
        if effective_publication_ids
        else None
    )
    artifact_base = f"/api/research/evaluations/{evaluation.id}/artifacts"
    return ResearchPublicationProjectionOut(
        publication_id=publication.id,
        formal_research_id=publication.formal_research_id,
        publication_version=publication.version,
        status=publication.status,
        publication_sha256=publication.publication_sha256,
        supersedes_publication_id=publication.supersedes_publication_id,
        evaluation_id=evaluation.id,
        evaluation_version=evaluation.version,
        conclusion=evaluation.conclusion,
        evaluation_sha256=evaluation.evaluation_sha256,
        supersedes_evaluation_id=evaluation.supersedes_evaluation_id,
        superseded_by_evaluation_id=successor.id if successor else None,
        runs=[
            ResearchPublicationRunOut(
                run_id=item.run_id,
                status=item.status,
                result_fingerprint=item.result_fingerprint,
                artifact_root=item.artifact_root,
            )
            for item in runs
        ],
        manifest_url=publication.artifact_manifest_uri,
        summary_url=f"{artifact_base}/summary.json",
        analytics_url=f"/api/research/publications/{publication.id}/analytics",
        report_url=f"/api/research/evaluations/{evaluation.id}/report",
        issue_number=publication.issue_number,
        issue_comment_id=publication.issue_comment_id,
        published_at=publication.published_at,
    )


def _strategy_summary(db: Session, strategy: StrategyDefinition) -> StrategyProfileSummaryOut:
    research_ids = list(
        db.scalars(
            select(FormalResearch.id)
            .join(FrozenResearchPlan, FrozenResearchPlan.id == FormalResearch.plan_id)
            .where(FrozenResearchPlan.strategy_id == strategy.strategy_id)
        ).all()
    )
    latest_publication = _latest_publication(db, research_ids)
    publication_evaluation = _publication_evaluation(db, latest_publication)
    return StrategyProfileSummaryOut(
        strategy_id=strategy.strategy_id,
        display_name=strategy.display_name,
        lifecycle_status=strategy.lifecycle_status,
        registry_version=strategy.registry_version,
        code_commit=strategy.code_commit,
        formal_research_count=len(research_ids),
        latest_publication_id=latest_publication.id if latest_publication else None,
        latest_publication_evaluation_id=(
            publication_evaluation.id if publication_evaluation else None
        ),
        latest_publication_conclusion=(
            publication_evaluation.conclusion if publication_evaluation else None
        ),
        latest_publication_status=latest_publication.status if latest_publication else None,
    )


def _formal_research_summary(db: Session, research: FormalResearch) -> FormalResearchSummaryOut:
    latest_publication = _latest_publication(db, [research.id])
    publication_evaluation = _publication_evaluation(db, latest_publication)
    run_count = int(
        db.scalar(
            select(func.count()).select_from(ResearchRun).where(ResearchRun.formal_research_id == research.id)
        )
        or 0
    )
    return FormalResearchSummaryOut(
        id=research.id,
        plan_id=research.plan_id,
        origin=research.origin,
        phase=research.phase,
        run_count=run_count,
        latest_publication_id=latest_publication.id if latest_publication else None,
        latest_publication_evaluation_id=(
            publication_evaluation.id if publication_evaluation else None
        ),
        latest_publication_conclusion=(
            publication_evaluation.conclusion if publication_evaluation else None
        ),
        latest_publication_status=latest_publication.status if latest_publication else None,
        created_at=research.created_at,
        completed_at=research.completed_at,
    )


def _latest_publication(db: Session, research_ids: list[str]) -> ResearchPublication | None:
    if not research_ids:
        return None
    effective_publication_ids = _effective_publication_ids(db, research_ids)
    published = (
        db.scalar(
            select(ResearchPublication)
            .where(
                ResearchPublication.formal_research_id.in_(research_ids),
                ResearchPublication.status == "published",
                ResearchPublication.id.in_(effective_publication_ids),
            )
            .order_by(
                ResearchPublication.published_at.desc(),
                ResearchPublication.created_at.desc(),
                ResearchPublication.version.desc(),
            )
            .limit(1)
        )
        if effective_publication_ids
        else None
    )
    if published is not None:
        return published
    return db.scalar(
        select(ResearchPublication)
        .where(
            ResearchPublication.formal_research_id.in_(research_ids),
            ResearchPublication.status.in_({"pending", "failed"}),
        )
        .order_by(ResearchPublication.created_at.desc(), ResearchPublication.version.desc())
        .limit(1)
    )


def _effective_publication_ids(db: Session, research_ids: list[str]) -> set[str]:
    if not research_ids:
        return set()
    outcomes: dict[str, str] = {}
    events = db.scalars(
        select(ResearchEvent)
        .where(
            ResearchEvent.formal_research_id.in_(research_ids),
            ResearchEvent.event_type.in_(PUBLICATION_LIFECYCLE_EVENT_TYPES),
        )
        .order_by(ResearchEvent.formal_research_id, ResearchEvent.sequence_no)
    ).all()
    for event in events:
        payload = event.payload_json
        publication_id = payload.get("publicationId") if isinstance(payload, dict) else None
        if publication_id:
            outcomes[str(publication_id)] = event.event_type
    return {
        publication_id
        for publication_id, event_type in outcomes.items()
        if event_type in PUBLICATION_SUCCESS_EVENT_TYPES
    }


def is_publication_effective(db: Session, publication_id: str) -> bool:
    """判断发布是否是已完成全部一致性收敛的生效版本。"""

    publication = db.get(ResearchPublication, publication_id)
    if publication is None or publication.status != "published":
        return False
    return publication.id in _effective_publication_ids(
        db, [publication.formal_research_id]
    )


def _publication_evaluation(
    db: Session, publication: ResearchPublication | None
) -> ResearchEvaluation | None:
    if publication is None:
        return None
    evaluation = db.get(ResearchEvaluation, publication.evaluation_id)
    if evaluation is None:
        raise RuntimeError("研究发布缺少对应评价记录")
    return evaluation


def _plan_out(plan: FrozenResearchPlan) -> FrozenResearchPlanOut:
    return FrozenResearchPlanOut(
        id=plan.id,
        strategy_id=plan.strategy_id,
        issue_number=plan.issue_number,
        version=plan.version,
        schema_version=plan.schema_version,
        plan_sha256=plan.plan_sha256,
        code_commit=plan.code_commit,
        plan_json=json_safe_value(plan.plan_json or {}),
        created_at=plan.created_at,
    )


def _approval_out(approval: ResearchPlanApproval) -> ResearchPlanApprovalOut:
    return ResearchPlanApprovalOut(
        id=approval.id,
        plan_id=approval.plan_id,
        action=approval.action,
        actor_login=approval.actor_login,
        comment_id=approval.comment_id,
        source_uri=approval.source_uri,
        comment_body=approval.comment_body,
        plan_sha256=approval.plan_sha256,
        created_at=approval.created_at,
    )


def _run_out(run: ResearchRun) -> ResearchRunSummaryOut:
    return ResearchRunSummaryOut(
        run_id=run.run_id,
        formal_research_id=run.formal_research_id,
        reproducibility_key=run.reproducibility_key,
        strategy_id=run.strategy_id,
        status=run.status,
        stage=run.stage,
        config_sha256=run.config_sha256,
        data_snapshot_id=run.data_snapshot_id,
        code_commit=run.code_commit,
        environment_sha256=run.environment_sha256,
        random_seed=run.random_seed,
        result_fingerprint=run.result_fingerprint,
        artifact_root=run.artifact_root,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=run.error,
    )


def _event_out(event: ResearchEvent) -> ResearchEventOut:
    return ResearchEventOut(
        id=event.id,
        formal_research_id=event.formal_research_id,
        run_id=event.run_id,
        sequence_no=event.sequence_no,
        event_type=event.event_type,
        payload_json=json_safe_value(event.payload_json or {}),
        occurred_at=event.occurred_at,
    )


def _evaluation_out(db: Session, evaluation: ResearchEvaluation) -> ResearchEvaluationOut:
    run_ids = list(
        db.scalars(
            select(ResearchEvaluationRun.run_id)
            .where(ResearchEvaluationRun.evaluation_id == evaluation.id)
            .order_by(ResearchEvaluationRun.run_id)
        ).all()
    )
    evidence_refs = db.scalars(
        select(ResearchEvidenceRef)
        .where(ResearchEvidenceRef.evaluation_id == evaluation.id)
        .order_by(ResearchEvidenceRef.created_at, ResearchEvidenceRef.id)
    ).all()
    return ResearchEvaluationOut(
        id=evaluation.id,
        formal_research_id=evaluation.formal_research_id,
        version=evaluation.version,
        conclusion=evaluation.conclusion,
        evaluation_sha256=evaluation.evaluation_sha256,
        supersedes_evaluation_id=evaluation.supersedes_evaluation_id,
        supporting_evidence=json_safe_value(evaluation.supporting_evidence or []),
        opposing_evidence=json_safe_value(evaluation.opposing_evidence or []),
        missing_evidence=json_safe_value(evaluation.missing_evidence or []),
        limitations=json_safe_value(evaluation.limitations or []),
        follow_up_recommendations=json_safe_value(
            evaluation.follow_up_recommendations or []
        ),
        run_ids=run_ids,
        evidence_refs=[_evidence_out(item) for item in evidence_refs],
        created_at=evaluation.created_at,
    )


def _evidence_out(evidence: ResearchEvidenceRef) -> ResearchEvidenceRefOut:
    return ResearchEvidenceRefOut(
        id=evidence.id,
        evaluation_id=evidence.evaluation_id,
        run_id=evidence.run_id,
        kind=evidence.kind,
        uri=evidence.uri,
        sha256=evidence.sha256,
        metadata_json=json_safe_value(evidence.metadata_json or {}),
        created_at=evidence.created_at,
    )


def _publication_out(publication: ResearchPublication) -> ResearchPublicationOut:
    return ResearchPublicationOut(
        id=publication.id,
        formal_research_id=publication.formal_research_id,
        evaluation_id=publication.evaluation_id,
        version=publication.version,
        status=publication.status,
        publication_sha256=publication.publication_sha256,
        supersedes_publication_id=publication.supersedes_publication_id,
        artifact_manifest_uri=publication.artifact_manifest_uri,
        issue_number=publication.issue_number,
        issue_comment_id=publication.issue_comment_id,
        created_at=publication.created_at,
        published_at=publication.published_at,
    )


def _proposal_out(proposal: FollowUpResearchProposal) -> FollowUpResearchProposalOut:
    return FollowUpResearchProposalOut(
        id=proposal.id,
        strategy_id=proposal.strategy_id,
        source_evaluation_id=proposal.source_evaluation_id,
        source_evidence_ref_id=proposal.source_evidence_ref_id,
        title=proposal.title,
        rationale=proposal.rationale,
        status=proposal.status,
        proposal_json=json_safe_value(proposal.proposal_json or {}),
        converted_plan_id=proposal.converted_plan_id,
        created_at=proposal.created_at,
    )
