from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

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
    ResearchRunSummaryOut,
    StrategyProfileOut,
    StrategyProfileSummaryOut,
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
        metadata_json=dict(strategy.metadata_json or {}),
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


def _strategy_summary(db: Session, strategy: StrategyDefinition) -> StrategyProfileSummaryOut:
    research_ids = list(
        db.scalars(
            select(FormalResearch.id)
            .join(FrozenResearchPlan, FrozenResearchPlan.id == FormalResearch.plan_id)
            .where(FrozenResearchPlan.strategy_id == strategy.strategy_id)
        ).all()
    )
    latest_evaluation = _latest_evaluation(db, research_ids)
    latest_publication = _latest_publication(db, research_ids)
    return StrategyProfileSummaryOut(
        strategy_id=strategy.strategy_id,
        display_name=strategy.display_name,
        lifecycle_status=strategy.lifecycle_status,
        registry_version=strategy.registry_version,
        code_commit=strategy.code_commit,
        formal_research_count=len(research_ids),
        latest_conclusion=latest_evaluation.conclusion if latest_evaluation else None,
        latest_publication_status=latest_publication.status if latest_publication else None,
    )


def _formal_research_summary(db: Session, research: FormalResearch) -> FormalResearchSummaryOut:
    latest_evaluation = db.scalar(
        select(ResearchEvaluation)
        .where(ResearchEvaluation.formal_research_id == research.id)
        .order_by(ResearchEvaluation.version.desc())
        .limit(1)
    )
    latest_publication = db.scalar(
        select(ResearchPublication)
        .where(ResearchPublication.formal_research_id == research.id)
        .order_by(ResearchPublication.version.desc())
        .limit(1)
    )
    run_count = int(
        db.scalar(
            select(func.count()).select_from(ResearchRun).where(ResearchRun.formal_research_id == research.id)
        )
        or 0
    )
    return FormalResearchSummaryOut(
        id=research.id,
        plan_id=research.plan_id,
        phase=research.phase,
        run_count=run_count,
        latest_evaluation_id=latest_evaluation.id if latest_evaluation else None,
        latest_conclusion=latest_evaluation.conclusion if latest_evaluation else None,
        latest_publication_id=latest_publication.id if latest_publication else None,
        publication_status=latest_publication.status if latest_publication else None,
        created_at=research.created_at,
        completed_at=research.completed_at,
    )


def _latest_evaluation(db: Session, research_ids: list[str]) -> ResearchEvaluation | None:
    if not research_ids:
        return None
    return db.scalar(
        select(ResearchEvaluation)
        .where(ResearchEvaluation.formal_research_id.in_(research_ids))
        .order_by(ResearchEvaluation.created_at.desc(), ResearchEvaluation.version.desc())
        .limit(1)
    )


def _latest_publication(db: Session, research_ids: list[str]) -> ResearchPublication | None:
    if not research_ids:
        return None
    return db.scalar(
        select(ResearchPublication)
        .where(ResearchPublication.formal_research_id.in_(research_ids))
        .order_by(ResearchPublication.created_at.desc(), ResearchPublication.version.desc())
        .limit(1)
    )


def _plan_out(plan: FrozenResearchPlan) -> FrozenResearchPlanOut:
    return FrozenResearchPlanOut(
        id=plan.id,
        strategy_id=plan.strategy_id,
        issue_number=plan.issue_number,
        version=plan.version,
        schema_version=plan.schema_version,
        plan_sha256=plan.plan_sha256,
        code_commit=plan.code_commit,
        plan_json=dict(plan.plan_json or {}),
        created_at=plan.created_at,
    )


def _approval_out(approval: ResearchPlanApproval) -> ResearchPlanApprovalOut:
    return ResearchPlanApprovalOut(
        id=approval.id,
        plan_id=approval.plan_id,
        action=approval.action,
        actor_login=approval.actor_login,
        comment_id=approval.comment_id,
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
        payload_json=dict(event.payload_json or {}),
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
        supporting_evidence=list(evaluation.supporting_evidence or []),
        opposing_evidence=list(evaluation.opposing_evidence or []),
        missing_evidence=list(evaluation.missing_evidence or []),
        limitations=list(evaluation.limitations or []),
        follow_up_recommendations=list(evaluation.follow_up_recommendations or []),
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
        metadata_json=dict(evidence.metadata_json or {}),
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
        proposal_json=dict(proposal.proposal_json or {}),
        converted_plan_id=proposal.converted_plan_id,
        created_at=proposal.created_at,
    )
