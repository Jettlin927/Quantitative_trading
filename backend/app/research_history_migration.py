from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import (
    FormalResearch,
    FrozenResearchPlan,
    ResearchEvaluation,
    ResearchEvaluationRun,
    ResearchEvidenceRef,
    ResearchPlanApproval,
    ResearchPublication,
    ResearchRun,
    StrategyDefinition,
)


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class ResearchHistoryMigrationError(RuntimeError):
    pass


class ResearchHistoryMigrationConflict(ResearchHistoryMigrationError):
    pass


@dataclass(frozen=True)
class RunIdentity:
    run_id: str
    result_fingerprint: str
    scenario: str
    reproducibility_key: str


@dataclass(frozen=True)
class CurrentResearchSource:
    strategy_id: str
    display_name: str
    result_set_id: str
    issue_version: int
    archive_class: str
    conclusion: str
    economic_thesis: str
    code_commit: str
    report_uri: str
    report_sha256: str
    summary_uri: str
    summary_sha256: str
    reproduction_uri: str
    reproduction_sha256: str
    generated_at: datetime | None
    plan_json: dict[str, Any]
    supporting_evidence: list[dict[str, Any]]
    opposing_evidence: list[dict[str, Any]]
    missing_evidence: list[dict[str, Any]]
    limitations: list[dict[str, Any]]
    follow_up_recommendations: list[dict[str, Any]]
    run_identities: tuple[RunIdentity, ...]


@dataclass(frozen=True)
class LegacyArchiveSource:
    strategy_id: str
    display_name: str
    result_set_id: str
    archive_class: str
    source_status: str
    structured_conclusion: None
    artifact_refs: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class HistorySource:
    issue_number: int
    expected_source_run_count: int
    source_manifest_uri: str
    source_manifest_sha256: str
    reproduction_uri: str
    reproduction_sha256: str
    source_fingerprint: str
    current_report_count: int
    current_research: tuple[CurrentResearchSource, ...]
    legacy_archives: tuple[LegacyArchiveSource, ...]


@dataclass(frozen=True)
class PlannedRunLink:
    strategy_id: str
    formal_research_id: str
    code_commit: str
    identity: RunIdentity


@dataclass(frozen=True)
class RunIssue:
    run_id: str
    strategy_id: str
    reason: str


@dataclass(frozen=True)
class HistoryMigrationPlan:
    source: HistorySource
    source_run_count: int
    declared_run_count: int
    matched_runs: tuple[PlannedRunLink, ...]
    mismatched_runs: tuple[RunIssue, ...]
    missing_runs: tuple[RunIssue, ...]
    unexpected_linked_runs: tuple[RunIssue, ...]
    unpublished_run_count: int
    source_inventory_sha256: str
    migration_fingerprint: str

    @property
    def matched_run_count(self) -> int:
        return len(self.matched_runs)

    @property
    def mismatched_run_count(self) -> int:
        return len(self.mismatched_runs)

    @property
    def missing_run_count(self) -> int:
        return len(self.missing_runs)

    @property
    def unexpected_linked_run_count(self) -> int:
        return len(self.unexpected_linked_runs)


@dataclass(frozen=True)
class MigrationResult:
    created: dict[str, int]
    unchanged: dict[str, int]

    @property
    def created_total(self) -> int:
        return sum(self.created.values())

    @property
    def unchanged_total(self) -> int:
        return sum(self.unchanged.values())


def load_history_source(repo_root: Path, contract_path: Path) -> HistorySource:
    root = repo_root.resolve()
    contract_file = _resolve_inside(root, contract_path)
    contract = _read_json(contract_file)
    if contract.get("schemaVersion") != 1:
        raise ResearchHistoryMigrationError("历史迁移合同 schemaVersion 必须为 1")

    manifest_path = _resolve_inside(root, root / _required_text(contract, "sourceManifest"))
    reproduction_path = _resolve_inside(
        root, root / _required_text(contract, "reproductionEvidence")
    )
    manifest = _read_json(manifest_path)
    reproduction = _read_json(reproduction_path)
    result_sets = {
        item["id"]: item
        for item in manifest.get("resultSets", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    evidence_fingerprints = _verified_reproduction_fingerprints(reproduction)
    evidence_commit = _required_text(reproduction, "codeCommit")
    if not GIT_COMMIT_PATTERN.fullmatch(evidence_commit):
        raise ResearchHistoryMigrationError("复现证据 codeCommit 不是 40 位 Git 提交")
    results_root = manifest_path.parent

    current: list[CurrentResearchSource] = []
    declared_run_ids: set[str] = set()
    for declaration in contract.get("currentResearch", []):
        result_set_id = _required_text(declaration, "resultSetId")
        result_set = result_sets.get(result_set_id)
        if result_set is None:
            raise ResearchHistoryMigrationError(f"清单缺少当前研究：{result_set_id}")
        if result_set.get("archiveClass") != "current-trustworthy":
            raise ResearchHistoryMigrationError(f"当前研究档案分类不可信：{result_set_id}")

        expected_conclusion = _required_text(declaration, "expectedConclusion")
        if expected_conclusion != "不通过" or result_set.get("status") != expected_conclusion:
            raise ResearchHistoryMigrationError(
                f"当前研究结论不符合冻结合同：{result_set_id}"
            )
        artifacts = result_set.get("artifacts")
        if not isinstance(artifacts, dict):
            raise ResearchHistoryMigrationError(f"当前研究缺少工件清单：{result_set_id}")
        report_path = _resolve_inside(
            root, results_root / _required_text(artifacts, "reportHtml")
        )
        summary_path = _resolve_inside(
            root, results_root / _required_text(artifacts, "summaryJson")
        )
        declared_reproduction_path = _resolve_inside(
            root, results_root / _required_text(artifacts, "reproductionEvidence")
        )
        if declared_reproduction_path != reproduction_path:
            raise ResearchHistoryMigrationError(
                f"复现证据路径与冻结合同不一致：{result_set_id}"
            )
        summary = _read_json(summary_path)
        evaluation = _drill(summary, declaration.get("evaluationPath", []))
        if not isinstance(evaluation, dict) or evaluation.get("status") != expected_conclusion:
            raise ResearchHistoryMigrationError(
                f"机器摘要结论与冻结合同不一致：{result_set_id}"
            )
        run_source = _drill(summary, declaration.get("runIdentityPath", []))
        identities = _parse_run_identities(
            run_source,
            evidence_fingerprints,
            evidence_commit,
        )
        for identity in identities:
            if identity.run_id in declared_run_ids:
                raise ResearchHistoryMigrationError(f"运行被重复声明：{identity.run_id}")
            declared_run_ids.add(identity.run_id)

        economic_thesis = _required_path_text(
            summary,
            declaration.get("economicThesisPath"),
            field_name="economicThesisPath",
        )
        supporting_evidence = _records_from_paths(
            summary,
            declaration.get("supportingEvidencePaths"),
            field_name="supportingEvidencePaths",
        )
        opposing_evidence = _records_from_paths(
            summary,
            declaration.get("opposingEvidencePaths"),
            field_name="opposingEvidencePaths",
        )
        missing_evidence = _records_from_paths(
            summary,
            declaration.get("missingEvidencePaths"),
            field_name="missingEvidencePaths",
        )
        limitations = _records_from_paths(
            summary,
            declaration.get("limitationPaths"),
            field_name="limitationPaths",
        )
        follow_up_recommendations = _records_from_paths(
            summary,
            declaration.get("followUpRecommendationPaths"),
            field_name="followUpRecommendationPaths",
        )
        generated_at = _parse_optional_datetime(summary.get("reportGeneratedAt"))
        strategy_id = _required_text(declaration, "strategyId")
        source_uris = {
            "manifest": _repo_uri(root, manifest_path),
            "report": _repo_uri(root, report_path),
            "summary": _repo_uri(root, summary_path),
            "reproduction": _repo_uri(root, reproduction_path),
        }
        plan_json = {
            "schemaVersion": "history-import-v1",
            "origin": "historical_import",
            "executionAuthorized": False,
            "issueNumber": int(contract["issueNumber"]),
            "strategyId": strategy_id,
            "resultSetId": result_set_id,
            "archiveClass": result_set["archiveClass"],
            "preservedConclusion": expected_conclusion,
            "sourceUris": source_uris,
            "runIdentities": [
                {
                    "runId": identity.run_id,
                    "strategyId": strategy_id,
                    "codeCommit": evidence_commit,
                    "reproducibilityKey": identity.reproducibility_key,
                    "resultFingerprint": identity.result_fingerprint,
                }
                for identity in identities
            ],
        }
        limitations.append(
            {"statement": "历史研究导入不构成用户批准，不授权新运行或补跑。"}
        )
        current.append(
            CurrentResearchSource(
                strategy_id=strategy_id,
                display_name=_required_text(declaration, "displayName"),
                result_set_id=result_set_id,
                issue_version=int(declaration["issueVersion"]),
                archive_class=str(result_set["archiveClass"]),
                conclusion=expected_conclusion,
                economic_thesis=economic_thesis[:2000],
                code_commit=evidence_commit,
                report_uri=source_uris["report"],
                report_sha256=_file_sha256(report_path),
                summary_uri=source_uris["summary"],
                summary_sha256=_file_sha256(summary_path),
                reproduction_uri=source_uris["reproduction"],
                reproduction_sha256=_file_sha256(reproduction_path),
                generated_at=generated_at,
                plan_json=plan_json,
                supporting_evidence=supporting_evidence,
                opposing_evidence=opposing_evidence,
                missing_evidence=missing_evidence,
                limitations=limitations,
                follow_up_recommendations=follow_up_recommendations,
                run_identities=identities,
            )
        )

    legacy: list[LegacyArchiveSource] = []
    for declaration in contract.get("legacyArchives", []):
        result_set_id = _required_text(declaration, "resultSetId")
        result_set = result_sets.get(result_set_id)
        if result_set is None or result_set.get("archiveClass") != "legacy":
            raise ResearchHistoryMigrationError(f"legacy 档案分类不正确：{result_set_id}")
        artifacts = result_set.get("artifacts")
        if not isinstance(artifacts, dict) or not artifacts:
            raise ResearchHistoryMigrationError(f"legacy 档案缺少工件：{result_set_id}")
        declared_artifacts: dict[Path, str] = {}
        for kind, relative_path in sorted(artifacts.items()):
            if not isinstance(relative_path, str):
                raise ResearchHistoryMigrationError(f"legacy 工件路径无效：{result_set_id}")
            artifact_path = _resolve_inside(root, results_root / relative_path)
            declared_artifacts[artifact_path] = kind
        artifact_paths = set(declared_artifacts)
        artifact_parents = {path.parent for path in declared_artifacts}
        if len(artifact_parents) == 1:
            artifact_parent = next(iter(artifact_parents))
            if artifact_parent != results_root:
                artifact_paths.update(
                    _resolve_inside(root, path)
                    for path in artifact_parent.rglob("*")
                    if path.is_file()
                )
        artifact_refs = []
        for artifact_path in sorted(artifact_paths):
            artifact_refs.append(
                {
                    "kind": declared_artifacts.get(artifact_path, "rawEvidence"),
                    "uri": _repo_uri(root, artifact_path),
                    "sha256": _file_sha256(artifact_path),
                }
            )
        legacy.append(
            LegacyArchiveSource(
                strategy_id=_required_text(declaration, "strategyId"),
                display_name=_required_text(declaration, "displayName"),
                result_set_id=result_set_id,
                archive_class="legacy",
                source_status=_required_text(result_set, "status"),
                structured_conclusion=None,
                artifact_refs=tuple(artifact_refs),
            )
        )

    current_report_count = len({item.result_set_id for item in current})
    if current_report_count != 3 or len(current) != 4 or len(legacy) != 3:
        raise ResearchHistoryMigrationError("冻结合同必须包含 3 份当前报告、4 个研究和 3 份 legacy 档案")
    source_fingerprint = _canonical_sha256(
        {
            "contract": contract,
            "contractSha256": _file_sha256(contract_file),
            "manifestSha256": _file_sha256(manifest_path),
            "reproductionSha256": _file_sha256(reproduction_path),
            "currentSources": {
                item.strategy_id: {
                    "resultSetId": item.result_set_id,
                    "summarySha256": item.summary_sha256,
                    "reportSha256": item.report_sha256,
                }
                for item in current
            },
            "legacyArtifacts": {
                item.result_set_id: list(item.artifact_refs) for item in legacy
            },
        }
    )
    return HistorySource(
        issue_number=int(contract["issueNumber"]),
        expected_source_run_count=int(contract["expectedSourceRunCount"]),
        source_manifest_uri=_repo_uri(root, manifest_path),
        source_manifest_sha256=_file_sha256(manifest_path),
        reproduction_uri=_repo_uri(root, reproduction_path),
        reproduction_sha256=_file_sha256(reproduction_path),
        source_fingerprint=source_fingerprint,
        current_report_count=current_report_count,
        current_research=tuple(current),
        legacy_archives=tuple(legacy),
    )


def build_history_migration_plan(db: Session, source: HistorySource) -> HistoryMigrationPlan:
    runs = list(db.scalars(select(ResearchRun).order_by(ResearchRun.run_id)).all())
    by_id = {run.run_id: run for run in runs}
    matched: list[PlannedRunLink] = []
    mismatched: list[RunIssue] = []
    missing: list[RunIssue] = []

    for research in source.current_research:
        formal_id = _target_id("formal-research", research.strategy_id)
        for identity in research.run_identities:
            run = by_id.get(identity.run_id)
            if run is None:
                missing.append(RunIssue(identity.run_id, research.strategy_id, "运行不存在"))
                continue
            reasons = []
            if run.strategy_id != research.strategy_id:
                reasons.append("策略 ID 不一致")
            if run.status != "succeeded":
                reasons.append("运行不是 succeeded")
            if run.result_fingerprint != identity.result_fingerprint:
                reasons.append("结果指纹不一致")
            if run.reproducibility_key != identity.reproducibility_key:
                reasons.append("复现键不一致")
            if run.code_commit != research.code_commit:
                reasons.append("代码提交不一致")
            if run.formal_research_id not in (None, formal_id):
                reasons.append("已关联其他正式研究")
            if reasons:
                mismatched.append(
                    RunIssue(identity.run_id, research.strategy_id, "；".join(reasons))
                )
                continue
            matched.append(
                PlannedRunLink(
                    research.strategy_id,
                    formal_id,
                    research.code_commit,
                    identity,
                )
            )

    matched_run_ids = {item.identity.run_id for item in matched}
    unexpected_linked = [
        RunIssue(
            run.run_id,
            run.strategy_id,
            f"已关联非本次可靠目标的正式研究：{run.formal_research_id}",
        )
        for run in runs
        if run.formal_research_id is not None and run.run_id not in matched_run_ids
    ]
    unpublished_run_count = sum(
        1
        for run in runs
        if run.formal_research_id is None and run.run_id not in matched_run_ids
    )

    inventory = [
        {
            "runId": run.run_id,
            "strategyId": run.strategy_id,
            "status": run.status,
            "reproducibilityKey": run.reproducibility_key,
            "codeCommit": run.code_commit,
            "resultFingerprint": run.result_fingerprint,
        }
        for run in runs
    ]
    inventory_sha256 = _canonical_sha256(inventory)
    fingerprint_payload = {
        "sourceFingerprint": source.source_fingerprint,
        "sourceInventorySha256": inventory_sha256,
        "matched": [
            {
                "runId": item.identity.run_id,
                "strategyId": item.strategy_id,
                "codeCommit": item.code_commit,
                "reproducibilityKey": item.identity.reproducibility_key,
                "resultFingerprint": item.identity.result_fingerprint,
            }
            for item in matched
        ],
        "mismatched": [item.__dict__ for item in mismatched],
        "missing": [item.__dict__ for item in missing],
        "unexpectedLinked": [item.__dict__ for item in unexpected_linked],
    }
    return HistoryMigrationPlan(
        source=source,
        source_run_count=len(runs),
        declared_run_count=sum(len(item.run_identities) for item in source.current_research),
        matched_runs=tuple(matched),
        mismatched_runs=tuple(mismatched),
        missing_runs=tuple(missing),
        unexpected_linked_runs=tuple(unexpected_linked),
        unpublished_run_count=unpublished_run_count,
        source_inventory_sha256=inventory_sha256,
        migration_fingerprint=_canonical_sha256(fingerprint_payload),
    )


def apply_history_migration(db: Session, plan: HistoryMigrationPlan) -> MigrationResult:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SET LOCAL lock_timeout = '5s'"))
        db.execute(text("LOCK TABLE research_runs IN SHARE ROW EXCLUSIVE MODE"))
    current_plan = build_history_migration_plan(db, plan.source)
    if current_plan.migration_fingerprint != plan.migration_fingerprint:
        raise ResearchHistoryMigrationConflict(
            "来源运行清单在 preview 后发生变化，已拒绝写入；请重新 preview 并确认新指纹"
        )
    if plan.source_run_count != plan.source.expected_source_run_count:
        raise ResearchHistoryMigrationError(
            "来源运行数与冻结合同不一致："
            f"actual={plan.source_run_count}, expected={plan.source.expected_source_run_count}"
        )
    if plan.unexpected_linked_runs:
        raise ResearchHistoryMigrationConflict(
            "发现非本次可靠目标的既有正式研究关联，已拒绝写入；请先人工审计异常关联"
        )
    created: dict[str, int] = {}
    unchanged: dict[str, int] = {}

    for item in plan.source.current_research:
        _ensure(
            db,
            StrategyDefinition,
            item.strategy_id,
            {
                "strategy_id": item.strategy_id,
                "display_name": item.display_name,
                "lifecycle_status": "已归档",
                "economic_thesis": item.economic_thesis,
                "registry_version": "history-import-v1",
                "code_commit": item.code_commit,
                "metadata_json": {
                    "historyImport": True,
                    "archiveClass": item.archive_class,
                    "sourceResultSetId": item.result_set_id,
                    "structuredConclusion": item.conclusion,
                    "sourceFingerprint": plan.source.source_fingerprint,
                },
            },
            "strategy_definitions",
            created,
            unchanged,
        )
    for item in plan.source.legacy_archives:
        _ensure(
            db,
            StrategyDefinition,
            item.strategy_id,
            {
                "strategy_id": item.strategy_id,
                "display_name": item.display_name,
                "lifecycle_status": "已归档",
                "economic_thesis": "历史来源仅保留原始档案，未按当前研究合同重新评价。",
                "registry_version": "history-import-v1",
                "code_commit": "unknown-legacy-source",
                "metadata_json": {
                    "historyImport": True,
                    "archiveClass": item.archive_class,
                    "sourceResultSetId": item.result_set_id,
                    "sourceStatus": item.source_status,
                    "structuredConclusion": None,
                    "artifactRefs": list(item.artifact_refs),
                    "sourceFingerprint": plan.source.source_fingerprint,
                },
            },
            "strategy_definitions",
            created,
            unchanged,
        )
    db.flush()

    for item in plan.source.current_research:
        plan_id = _target_id("frozen-plan", item.strategy_id)
        approval_id = _target_id("history-provenance", item.strategy_id)
        formal_id = _target_id("formal-research", item.strategy_id)
        plan_sha256 = _canonical_sha256(item.plan_json)
        _ensure(
            db,
            FrozenResearchPlan,
            plan_id,
            {
                "id": plan_id,
                "strategy_id": item.strategy_id,
                "issue_number": plan.source.issue_number,
                "version": item.issue_version,
                "schema_version": "history-import-v1",
                "plan_sha256": plan_sha256,
                "code_commit": item.code_commit,
                "plan_json": item.plan_json,
            },
            "frozen_research_plans",
            created,
            unchanged,
        )
        _ensure(
            db,
            ResearchPlanApproval,
            approval_id,
            {
                "id": approval_id,
                "plan_id": plan_id,
                "action": "historical_import",
                "actor_login": "history-migration-v1",
                "comment_id": None,
                "source_uri": item.summary_uri,
                "comment_body": "历史导入：保留既有评价，不构成用户批准，不授权新运行或补跑。",
                "plan_sha256": plan_sha256,
            },
            "research_plan_approvals",
            created,
            unchanged,
        )
        _ensure(
            db,
            FormalResearch,
            formal_id,
            {
                "id": formal_id,
                "plan_id": plan_id,
                "approval_id": approval_id,
                "origin": "historical_import",
                "phase": "stopped",
            },
            "formal_researches",
            created,
            unchanged,
        )
    db.flush()

    for link in plan.matched_runs:
        run = db.get(ResearchRun, link.identity.run_id)
        if run is None:
            raise ResearchHistoryMigrationConflict(f"计划中的运行已消失：{link.identity.run_id}")
        if (
            run.strategy_id != link.strategy_id
            or run.status != "succeeded"
            or run.result_fingerprint != link.identity.result_fingerprint
            or run.reproducibility_key != link.identity.reproducibility_key
            or run.code_commit != link.code_commit
        ):
            raise ResearchHistoryMigrationConflict(f"计划后的运行事实发生漂移：{link.identity.run_id}")
        if run.formal_research_id is None:
            run.formal_research_id = link.formal_research_id
            _increment(created, "research_run_links")
        elif run.formal_research_id == link.formal_research_id:
            _increment(unchanged, "research_run_links")
        else:
            raise ResearchHistoryMigrationConflict(f"运行已关联其他研究：{link.identity.run_id}")
    db.flush()

    links_by_strategy: dict[str, list[PlannedRunLink]] = {}
    for link in plan.matched_runs:
        links_by_strategy.setdefault(link.strategy_id, []).append(link)
    for item in plan.source.current_research:
        formal_id = _target_id("formal-research", item.strategy_id)
        evaluation_id = _target_id("evaluation", item.strategy_id)
        migration_missing_evidence = [
            {
                "statement": f"历史运行 {issue.run_id} 未关联：{issue.reason}",
                "origin": "historical_import",
            }
            for issue in (*plan.mismatched_runs, *plan.missing_runs)
            if issue.strategy_id == item.strategy_id
        ]
        missing_evidence = [*item.missing_evidence, *migration_missing_evidence]
        structured_evaluation = {
            "formalResearchId": formal_id,
            "version": 1,
            "conclusion": item.conclusion,
            "supportingEvidence": item.supporting_evidence,
            "opposingEvidence": item.opposing_evidence,
            "missingEvidence": missing_evidence,
            "limitations": item.limitations,
            "followUpRecommendations": item.follow_up_recommendations,
            "runIdentities": [
                {
                    "runId": link.identity.run_id,
                    "codeCommit": link.code_commit,
                    "reproducibilityKey": link.identity.reproducibility_key,
                    "resultFingerprint": link.identity.result_fingerprint,
                }
                for link in links_by_strategy.get(item.strategy_id, [])
            ],
            "sourceSummarySha256": item.summary_sha256,
        }
        evaluation_sha256 = _canonical_sha256(structured_evaluation)
        _ensure(
            db,
            ResearchEvaluation,
            evaluation_id,
            {
                "id": evaluation_id,
                "formal_research_id": formal_id,
                "version": 1,
                "conclusion": item.conclusion,
                "evaluation_sha256": evaluation_sha256,
                "supersedes_evaluation_id": None,
                "supporting_evidence": item.supporting_evidence,
                "opposing_evidence": item.opposing_evidence,
                "missing_evidence": missing_evidence,
                "limitations": item.limitations,
                "follow_up_recommendations": item.follow_up_recommendations,
            },
            "research_evaluations",
            created,
            unchanged,
        )
        db.flush()
        for link in links_by_strategy.get(item.strategy_id, []):
            _ensure(
                db,
                ResearchEvaluationRun,
                (evaluation_id, link.identity.run_id),
                {"evaluation_id": evaluation_id, "run_id": link.identity.run_id},
                "research_evaluation_runs",
                created,
                unchanged,
            )
        evidence_specs = (
            ("report", item.report_uri, item.report_sha256, "text/html"),
            ("statistics", item.summary_uri, item.summary_sha256, "application/json"),
            (
                "environment",
                item.reproduction_uri,
                item.reproduction_sha256,
                "application/json",
            ),
        )
        for kind, uri, digest, media_type in evidence_specs:
            evidence_id = _target_id("evidence", f"{item.strategy_id}:{kind}:{uri}")
            _ensure(
                db,
                ResearchEvidenceRef,
                evidence_id,
                {
                    "id": evidence_id,
                    "evaluation_id": evaluation_id,
                    "run_id": None,
                    "kind": kind,
                    "uri": uri,
                    "sha256": digest,
                    "metadata_json": {
                        "mediaType": media_type,
                        "origin": "historical_import",
                        "resultSetId": item.result_set_id,
                        "sourceGeneratedAt": (
                            item.generated_at.isoformat() if item.generated_at else None
                        ),
                    },
                },
                "research_evidence_refs",
                created,
                unchanged,
            )
        db.flush()
        publication_id = _target_id("publication", item.strategy_id)
        publication_payload = {
            "formalResearchId": formal_id,
            "evaluationId": evaluation_id,
            "evaluationSha256": evaluation_sha256,
            "resultSetId": item.result_set_id,
            "status": "pending",
            "reportSha256": item.report_sha256,
            "sourceManifestSha256": plan.source.source_manifest_sha256,
        }
        _ensure(
            db,
            ResearchPublication,
            publication_id,
            {
                "id": publication_id,
                "formal_research_id": formal_id,
                "evaluation_id": evaluation_id,
                "version": 1,
                "status": "pending",
                "publication_sha256": _canonical_sha256(publication_payload),
                "supersedes_publication_id": None,
                "artifact_manifest_uri": plan.source.source_manifest_uri,
                "issue_number": plan.source.issue_number,
                "issue_comment_id": None,
                "published_at": None,
            },
            "research_publications",
            created,
            unchanged,
            compare_fields=(
                "id",
                "formal_research_id",
                "evaluation_id",
                "version",
                "status",
                "publication_sha256",
                "supersedes_publication_id",
                "artifact_manifest_uri",
                "issue_number",
                "issue_comment_id",
                "published_at",
            ),
        )
    db.flush()
    return MigrationResult(created=created, unchanged=unchanged)


def migration_report(
    plan: HistoryMigrationPlan,
    result: MigrationResult | None,
    *,
    mode: str,
    committed: bool,
) -> dict[str, Any]:
    warnings = [
        "legacy 不推断结论：旧 status=ok 或观察状态只保留在来源元数据中。",
        "历史导入不构成用户批准，也不授权补跑。",
        "仅运行 ID、策略 ID、succeeded 状态、结果指纹、复现键和代码提交全部一致的运行会被关联。",
        "统一发布记录保持 pending；生产迁移及 Issue、API、前端同版本读回须另行过门。",
    ]
    if plan.source_run_count != plan.source.expected_source_run_count:
        warnings.append(
            "来源运行数与冻结合同不一致，apply 会被拒绝："
            f"actual={plan.source_run_count}, expected={plan.source.expected_source_run_count}。"
        )
    if plan.mismatched_runs or plan.missing_runs:
        warnings.append("存在不可靠运行；这些运行保持未发布，不补猜结论。")
    if plan.unexpected_linked_runs:
        warnings.append("存在非本次可靠目标的既有正式研究关联；apply 会拒绝写入。")
    return {
        "模式": mode,
        "已提交": committed,
        "迁移指纹": plan.migration_fingerprint,
        "来源指纹": plan.source.source_fingerprint,
        "来源运行清单指纹": plan.source_inventory_sha256,
        "来源运行数": plan.source_run_count,
        "冻结来源运行数": plan.source.expected_source_run_count,
        "当前可信报告数": plan.source.current_report_count,
        "结构化当前研究数": len(plan.source.current_research),
        "legacy 档案数": len(plan.source.legacy_archives),
        "声明运行数": plan.declared_run_count,
        "可靠关联数": plan.matched_run_count,
        "不一致运行数": plan.mismatched_run_count,
        "缺失运行数": plan.missing_run_count,
        "异常既有关联数": plan.unexpected_linked_run_count,
        "未发布运行数": plan.unpublished_run_count,
        "创建记录": dict(result.created) if result else {},
        "既有一致记录": dict(result.unchanged) if result else {},
        "不一致运行": [item.__dict__ for item in plan.mismatched_runs],
        "缺失运行": [item.__dict__ for item in plan.missing_runs],
        "异常既有关联": [item.__dict__ for item in plan.unexpected_linked_runs],
        "警告": warnings,
    }


def render_migration_report_markdown(report: dict[str, Any]) -> str:
    committed = "是" if report["已提交"] else "否"
    lines = [
        "# 研究历史迁移报告",
        "",
        f"- 模式：{report['模式']}",
        f"- 已提交：{committed}",
        f"- 迁移指纹：`{report['迁移指纹']}`",
        f"- 来源运行清单指纹：`{report['来源运行清单指纹']}`",
        f"- 来源运行：{report['来源运行数']}",
        f"- 当前可信报告：{report['当前可信报告数']}",
        f"- 结构化当前研究：{report['结构化当前研究数']}",
        f"- legacy 档案：{report['legacy 档案数']}",
        f"- 可靠关联：{report['可靠关联数']}",
        f"- 未发布运行：{report['未发布运行数']}",
        f"- 不一致运行：{report['不一致运行数']}",
        f"- 缺失运行：{report['缺失运行数']}",
        f"- 异常既有关联：{report['异常既有关联数']}",
        "",
        "## 安全结论",
        "",
    ]
    lines.extend(f"- {warning}" for warning in report["警告"])
    if report["创建记录"]:
        lines.extend(("", "## 写入统计", ""))
        lines.extend(
            f"- {name}：{count}" for name, count in sorted(report["创建记录"].items())
        )
    return "\n".join(lines) + "\n"


def _ensure(
    db: Session,
    model: type[Any],
    key: Any,
    values: dict[str, Any],
    counter_name: str,
    created: dict[str, int],
    unchanged: dict[str, int],
    *,
    compare_fields: tuple[str, ...] | None = None,
) -> Any:
    existing = db.get(model, key)
    if existing is None:
        existing = model(**values)
        db.add(existing)
        _increment(created, counter_name)
        return existing
    fields = compare_fields or tuple(values)
    differences = [
        field for field in fields if getattr(existing, field) != values[field]
    ]
    if differences:
        raise ResearchHistoryMigrationConflict(
            f"{counter_name} 已存在但与冻结来源不一致：key={key}, fields={','.join(differences)}"
        )
    _increment(unchanged, counter_name)
    return existing


def _increment(counters: dict[str, int], name: str) -> None:
    counters[name] = counters.get(name, 0) + 1


def _target_id(kind: str, identity: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"quantitative-trading/history-import-v1/{kind}/{identity}"))


def _verified_reproduction_fingerprints(reproduction: dict[str, Any]) -> dict[str, str]:
    rounds = reproduction.get("rounds")
    if not isinstance(rounds, list) or len(rounds) < 2:
        raise ResearchHistoryMigrationError("复现证据必须至少包含两轮结果")
    round_maps = []
    for item in rounds:
        mapping = item.get("resultFingerprints") if isinstance(item, dict) else None
        if not isinstance(mapping, dict):
            raise ResearchHistoryMigrationError("复现证据缺少 resultFingerprints")
        round_maps.append(mapping)
    first = round_maps[0]
    if any(mapping != first for mapping in round_maps[1:]):
        raise ResearchHistoryMigrationError("复现证据各轮结果指纹不一致")
    for run_id, digest in first.items():
        _validate_run_identity(run_id, digest)
    return {str(run_id): str(digest) for run_id, digest in first.items()}


def _parse_run_identities(
    value: Any,
    evidence: dict[str, str],
    evidence_commit: str,
) -> tuple[RunIdentity, ...]:
    candidates: list[tuple[str, dict[str, Any]]] = []
    if isinstance(value, dict):
        candidates = [
            (str(name), item) for name, item in value.items() if isinstance(item, dict)
        ]
    elif isinstance(value, list):
        candidates = [
            (str(item.get("scenario") or index), item)
            for index, item in enumerate(value)
            if isinstance(item, dict)
        ]
    if not candidates:
        raise ResearchHistoryMigrationError("机器摘要没有可解析的运行身份")
    identities = []
    for scenario, item in candidates:
        run_id = _required_text(item, "runId")
        digest = _required_text(item, "resultFingerprint")
        _validate_run_identity(run_id, digest)
        if evidence.get(run_id) != digest:
            raise ResearchHistoryMigrationError(f"运行身份未通过双轮复现证据：{run_id}")
        key = _required_text(item, "reproducibilityKey")
        if not SHA256_PATTERN.fullmatch(key):
            raise ResearchHistoryMigrationError(f"复现键不是 SHA-256：{run_id}")
        item_commit = item.get("codeCommit")
        if item_commit is not None and item_commit != evidence_commit:
            raise ResearchHistoryMigrationError(f"运行代码提交与复现证据不一致：{run_id}")
        identities.append(
            RunIdentity(
                run_id=run_id,
                result_fingerprint=digest,
                scenario=scenario,
                reproducibility_key=key,
            )
        )
    return tuple(sorted(identities, key=lambda item: item.run_id))


def _validate_run_identity(run_id: Any, digest: Any) -> None:
    try:
        UUID(str(run_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ResearchHistoryMigrationError(f"运行 ID 不是 UUID：{run_id}") from exc
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        raise ResearchHistoryMigrationError(f"结果指纹不是 SHA-256：{run_id}")


def _evidence_records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    records = []
    for item in value:
        if isinstance(item, dict):
            records.append(item)
        elif isinstance(item, str) and item.strip():
            records.append({"statement": item})
        else:
            records.append({"statement": str(item)})
    return records


def _required_path_text(
    source: dict[str, Any],
    path: Any,
    *,
    field_name: str,
) -> str:
    if not isinstance(path, list) or not path:
        raise ResearchHistoryMigrationError(f"冻结合同缺少非空 JSON 路径：{field_name}")
    value = _drill(source, path)
    if not isinstance(value, str) or not value.strip():
        raise ResearchHistoryMigrationError(f"JSON 路径没有非空文本：{'/'.join(path)}")
    return value.strip()


def _records_from_paths(
    source: dict[str, Any],
    paths: Any,
    *,
    field_name: str,
) -> list[dict[str, Any]]:
    if not isinstance(paths, list):
        raise ResearchHistoryMigrationError(f"冻结合同缺少 JSON 路径列表：{field_name}")
    records: list[dict[str, Any]] = []
    for path in paths:
        if not isinstance(path, list) or not path:
            raise ResearchHistoryMigrationError(f"冻结合同包含无效 JSON 路径：{field_name}")
        value = _drill(source, path)
        extracted = _evidence_records(value)
        if not extracted:
            raise ResearchHistoryMigrationError(f"JSON 路径没有证据内容：{'/'.join(path)}")
        records.extend(extracted)
    return records


def _drill(value: Any, path: Any) -> Any:
    if not isinstance(path, list):
        raise ResearchHistoryMigrationError("JSON 路径必须是数组")
    current = value
    for key in path:
        if not isinstance(current, dict) or key not in current:
            raise ResearchHistoryMigrationError(f"机器摘要缺少路径：{'/'.join(path)}")
        current = current[key]
    return current


def _required_text(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ResearchHistoryMigrationError(f"缺少文本字段：{key}")
    return value


def _parse_optional_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ResearchHistoryMigrationError(f"时间格式无效：{value}") from exc


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchHistoryMigrationError(f"无法读取 JSON：{path}") from exc
    if not isinstance(value, dict):
        raise ResearchHistoryMigrationError(f"JSON 根节点必须是对象：{path}")
    return value


def _resolve_inside(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ResearchHistoryMigrationError(f"路径越出仓库：{path}")
    if not resolved.is_file():
        raise ResearchHistoryMigrationError(f"来源文件不存在：{path}")
    return resolved


def _repo_uri(root: Path, path: Path) -> str:
    return f"repo://{path.relative_to(root).as_posix()}"


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(payload).hexdigest()
