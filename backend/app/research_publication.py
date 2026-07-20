from __future__ import annotations

from contextlib import contextmanager
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
from hashlib import sha256
from html import escape
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError, TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.orm import Session

from .github_research import (
    GitHubIssueClient,
    GitHubPermissionError,
    GitHubResearchError,
    GitHubUnavailableError,
)
from .historical_publication_issues import (
    EXPECTED_REPOSITORY,
    resolve_historical_publication_issue,
    validate_historical_publication_issue_snapshot,
)
from .json_safety import json_safe_value
from .models import (
    RESEARCH_CONCLUSION_VALUES,
    FormalResearch,
    FrozenResearchPlan,
    ResearchEvaluation,
    ResearchEvaluationRun,
    ResearchEvidenceRef,
    ResearchEvent,
    ResearchOrchestration,
    ResearchPublication,
    ResearchPublicationIssueMapping,
    ResearchRun,
    ResearchWorkItem,
)
from .quant_research.artifacts import atomic_write_bytes
from .quant_research.manifest import build_result_fingerprint
from .quant_research.evaluation import validate_oos_metrics_contract
from .quant_research.run_config import (
    build_parameter_neighborhood_configs,
    canonical_json_bytes,
    canonical_run_config_sha256,
    canonical_sha256,
    validate_research_pass_policy,
)
from .quant_research.runner import validate_research_archive
from .research_catalog import get_publication_projection, is_publication_effective
from .research_orchestration import append_research_event, transition_orchestration
from .schemas import ResearchPublicationProjectionOut


SessionFactory = Callable[[], Session]
TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "interrupted"})
EVIDENCE_KINDS = frozenset(
    {
        "input_snapshot",
        "code",
        "environment",
        "parameters",
        "ledger",
        "statistics",
        "report",
        "limitation",
    }
)
PUBLICATION_ARTIFACTS = frozenset({"manifest.json", "summary.json", "report.html"})
MAX_GITHUB_ISSUE_COMMENT_BYTES = 32_000
MAX_PUBLICATION_BASE_URL_CHARS = 2_048
RESEARCH_PUBLICATION_ADVISORY_LOCK_KEY = 0x515452505542
PUBLICATION_SUCCESS_EVENT_TYPES = frozenset(
    {"research_published", "research_publication_recovered"}
)
PUBLICATION_LIFECYCLE_EVENT_TYPES = frozenset(
    {*PUBLICATION_SUCCESS_EVENT_TYPES, "research_publication_failed"}
)
RESEARCH_PASS_REQUIRED_GATES = {
    "identity_and_hypothesis": "身份与假设",
    "point_in_time_universe": "时点与宇宙",
    "execution_semantics": "执行语义",
    "net_cost_and_liquidity": "成本与流动性",
    "matched_benchmark": "匹配基准",
    "test_oos": "样本外",
    "market_regime": "市场环境",
    "trial_history": "试验历史",
    "risk_and_capacity": "风险与容量",
    "reproducibility": "可复现",
}
RESEARCH_PASS_REQUIRED_EVIDENCE_PATHS = {
    "input_snapshot": frozenset({"manifest.json"}),
    "code": frozenset({"manifest.json"}),
    "environment": frozenset({"manifest.json"}),
    "parameters": frozenset({"manifest.json"}),
    "ledger": frozenset(
        {
            "rebalance_requests.csv.gz",
            "rebalance_executions.csv.gz",
            "positions.csv.gz",
        }
    ),
    "statistics": frozenset(
        {
            "metrics.json",
            "oos_metrics.json",
            "benchmark_nav.csv.gz",
            "walk_forward_windows.csv.gz",
            "walk_forward_metrics.csv.gz",
            "risk_exposures.csv.gz",
            "risk_contributions.csv.gz",
        }
    ),
}
RESEARCH_PASS_REQUIRED_EVIDENCE_KINDS = frozenset(RESEARCH_PASS_REQUIRED_EVIDENCE_PATHS)
RESEARCH_PASS_GATE_EVIDENCE_KINDS = {
    "identity_and_hypothesis": frozenset({"code", "parameters"}),
    "point_in_time_universe": frozenset({"input_snapshot"}),
    "execution_semantics": frozenset({"ledger"}),
    "net_cost_and_liquidity": frozenset({"ledger", "statistics"}),
    "matched_benchmark": frozenset({"statistics"}),
    "test_oos": frozenset({"statistics"}),
    "market_regime": frozenset({"statistics"}),
    "trial_history": frozenset({"parameters", "statistics"}),
    "risk_and_capacity": frozenset({"ledger", "statistics"}),
    "reproducibility": frozenset(
        {"input_snapshot", "code", "environment", "parameters"}
    ),
}


class PublicationError(RuntimeError):
    pass


class PublicationConflictError(PublicationError):
    pass


class PublicationArtifactError(PublicationError):
    pass


class PublicationReadbackClient(Protocol):
    def read_publication(self, publication_id: str) -> Mapping[str, Any]: ...

    def read_artifact(self, evaluation_id: str, filename: str) -> bytes: ...

    def read_report(self, evaluation_id: str) -> str: ...


class HttpPublicationReadbackClient:
    """经前端同源入口读取 API 与报告，验证实际路由而非数据库内部函数。"""

    def __init__(self, base_url: str, *, timeout_seconds: int = 20) -> None:
        _validate_readback_base_url(base_url)
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def read_publication(self, publication_id: str) -> Mapping[str, Any]:
        payload = self._get(
            f"/api/research/publications/{quote(publication_id, safe='')}"
        )
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublicationConflictError(
                "前端入口返回的发布投影不是有效 JSON"
            ) from exc
        if not isinstance(value, dict):
            raise PublicationConflictError("前端入口返回的发布投影不是 JSON object")
        return value

    def read_report(self, evaluation_id: str) -> str:
        payload = self._get(
            f"/api/research/evaluations/{quote(evaluation_id, safe='')}/report"
        )
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PublicationConflictError("前端入口返回的报告不是 UTF-8") from exc

    def read_artifact(self, evaluation_id: str, filename: str) -> bytes:
        if filename not in PUBLICATION_ARTIFACTS:
            raise PublicationConflictError("前端读回请求了未知发布工件")
        return self._get(
            f"/api/research/evaluations/{quote(evaluation_id, safe='')}/artifacts/"
            f"{quote(filename, safe='')}"
        )

    def _get(self, path: str) -> bytes:
        request = Request(
            self.base_url + path,
            method="GET",
            headers={
                "Accept": "application/json, text/html",
                "User-Agent": "quant-research-publisher/1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code < 500 and exc.code not in {408, 425, 429}:
                raise PublicationConflictError(
                    f"前端入口读回失败：HTTP {exc.code}"
                ) from exc
            raise PublicationError(f"前端入口暂时不可用：HTTP {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise PublicationError(f"前端入口读回失败：{type(exc).__name__}") from exc


@dataclass(frozen=True)
class EvidenceDraft:
    kind: str
    uri: str
    run_id: str | None = None
    sha256: str | None = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class EvaluationDraft:
    conclusion: str
    run_ids: tuple[str, ...] = ()
    supporting_evidence: tuple[Mapping[str, Any], ...] = ()
    opposing_evidence: tuple[Mapping[str, Any], ...] = ()
    missing_evidence: tuple[Mapping[str, Any], ...] = ()
    limitations: tuple[Mapping[str, Any], ...] = ()
    follow_up_recommendations: tuple[Mapping[str, Any], ...] = ()
    evidence_refs: tuple[EvidenceDraft, ...] = ()
    supersedes_evaluation_id: str | None = None


def parse_evaluation_contract(
    payload: Mapping[str, Any],
) -> tuple[str, EvaluationDraft]:
    """把显式、冻结的 JSON 评价合同转换为发布草稿，不推断研究结论。"""

    if not isinstance(payload, Mapping):
        raise PublicationConflictError("评价合同必须是 JSON object")
    allowed = {
        "schemaVersion",
        "formalResearchId",
        "conclusion",
        "runIds",
        "supportingEvidence",
        "opposingEvidence",
        "missingEvidence",
        "limitations",
        "followUpRecommendations",
        "evidenceRefs",
        "supersedesEvaluationId",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise PublicationConflictError(f"评价合同包含未知字段：{', '.join(unknown)}")
    if payload.get("schemaVersion") != "research-evaluation-request/v1":
        raise PublicationConflictError("评价合同 schemaVersion 不受支持")
    formal_research_id = payload.get("formalResearchId")
    conclusion = payload.get("conclusion")
    if not isinstance(formal_research_id, str) or not formal_research_id.strip():
        raise PublicationConflictError("评价合同缺少 formalResearchId")
    if not isinstance(conclusion, str):
        raise PublicationConflictError("评价合同缺少 conclusion")

    run_ids_value = payload.get("runIds", [])
    if not isinstance(run_ids_value, list) or any(
        not isinstance(item, str) or not item for item in run_ids_value
    ):
        raise PublicationConflictError("评价合同 runIds 必须是非空字符串数组")

    def evidence_list(field: str) -> tuple[Mapping[str, Any], ...]:
        value = payload.get(field, [])
        if not isinstance(value, list) or any(
            not isinstance(item, Mapping) for item in value
        ):
            raise PublicationConflictError(f"评价合同 {field} 必须是 object 数组")
        return tuple(dict(item) for item in value)

    refs_value = payload.get("evidenceRefs", [])
    if not isinstance(refs_value, list):
        raise PublicationConflictError("评价合同 evidenceRefs 必须是 object 数组")
    refs: list[EvidenceDraft] = []
    ref_allowed = {"kind", "uri", "runId", "sha256", "metadata"}
    for raw in refs_value:
        if not isinstance(raw, Mapping):
            raise PublicationConflictError("评价合同 evidenceRefs 必须是 object 数组")
        unknown_ref = sorted(set(raw) - ref_allowed)
        if unknown_ref:
            raise PublicationConflictError(
                f"评价证据引用包含未知字段：{', '.join(unknown_ref)}"
            )
        if not isinstance(raw.get("kind"), str) or not isinstance(raw.get("uri"), str):
            raise PublicationConflictError("评价证据引用必须包含 kind 与 uri")
        metadata = raw.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise PublicationConflictError("评价证据 metadata 必须是 JSON object")
        run_id = raw.get("runId")
        digest = raw.get("sha256")
        if run_id is not None and not isinstance(run_id, str):
            raise PublicationConflictError("评价证据 runId 必须是字符串或 null")
        if digest is not None and not isinstance(digest, str):
            raise PublicationConflictError("评价证据 sha256 必须是字符串或 null")
        refs.append(
            EvidenceDraft(
                kind=raw["kind"],
                uri=raw["uri"],
                run_id=run_id,
                sha256=digest,
                metadata=dict(metadata),
            )
        )

    supersedes = payload.get("supersedesEvaluationId")
    if supersedes is not None and not isinstance(supersedes, str):
        raise PublicationConflictError(
            "评价合同 supersedesEvaluationId 必须是字符串或 null"
        )
    return formal_research_id, EvaluationDraft(
        conclusion=conclusion,
        run_ids=tuple(run_ids_value),
        supporting_evidence=evidence_list("supportingEvidence"),
        opposing_evidence=evidence_list("opposingEvidence"),
        missing_evidence=evidence_list("missingEvidence"),
        limitations=evidence_list("limitations"),
        follow_up_recommendations=evidence_list("followUpRecommendations"),
        evidence_refs=tuple(refs),
        supersedes_evaluation_id=supersedes,
    )


@dataclass(frozen=True)
class _PreparedPublication:
    publication_id: str
    evaluation_id: str
    already_published: bool


def publish_research_evaluation(
    session_factory: SessionFactory,
    github: GitHubIssueClient,
    *,
    formal_research_id: str,
    draft: EvaluationDraft,
    artifact_root: Path,
    public_base_url: str | None = None,
    now: datetime | None = None,
    existing_evaluation_id: str | None = None,
    readback_client: PublicationReadbackClient | None = None,
    readback_base_url: str | None = None,
) -> ResearchPublicationProjectionOut:
    """将一份评价通过可恢复步骤发布到工件、Issue 与只读投影。"""

    with _publication_claim(session_factory):
        return _publish_research_evaluation_claimed(
            session_factory,
            github,
            formal_research_id=formal_research_id,
            draft=draft,
            artifact_root=artifact_root,
            public_base_url=public_base_url,
            now=now,
            existing_evaluation_id=existing_evaluation_id,
            readback_client=readback_client,
            readback_base_url=readback_base_url,
        )


def _publish_research_evaluation_claimed(
    session_factory: SessionFactory,
    github: GitHubIssueClient,
    *,
    formal_research_id: str,
    draft: EvaluationDraft,
    artifact_root: Path,
    public_base_url: str | None,
    now: datetime | None,
    existing_evaluation_id: str | None,
    readback_client: PublicationReadbackClient | None,
    readback_base_url: str | None,
) -> ResearchPublicationProjectionOut:
    published_at = now or datetime.now(timezone.utc)
    prepared: _PreparedPublication | None = None
    recover_published_failure = False
    try:
        if existing_evaluation_id is not None:
            with session_factory() as db:
                published = db.scalar(
                    select(ResearchPublication)
                    .where(
                        ResearchPublication.evaluation_id
                        == existing_evaluation_id,
                        ResearchPublication.status == "published",
                    )
                    .order_by(ResearchPublication.version.desc())
                    .limit(1)
                )
            if published is not None:
                prepared = _PreparedPublication(
                    published.id, existing_evaluation_id, True
                )
                recover_published_failure = True
        resolved_public_base_url = _resolve_public_base_url(public_base_url)
        resolved_readback_client = readback_client or HttpPublicationReadbackClient(
            _resolve_readback_base_url(
                readback_base_url, resolved_public_base_url
            )
        )
        _assert_github_publication_allowed(
            session_factory,
            github,
            formal_research_id,
            evaluation_id=existing_evaluation_id,
        )
        prepared = _prepare_publication(
            session_factory,
            formal_research_id=formal_research_id,
            draft=draft,
            now=published_at,
            existing_evaluation_id=existing_evaluation_id,
        )
        if prepared.already_published:
            recover_published_failure = True
            with session_factory() as db:
                publication = db.get(ResearchPublication, prepared.publication_id)
                formal = (
                    db.get(FormalResearch, publication.formal_research_id)
                    if publication is not None
                    else None
                )
                if publication is None or formal is None:
                    raise PublicationConflictError("已发布记录缺少正式研究")
        summary = _ensure_publication_bundle(
            session_factory,
            prepared.publication_id,
            Path(artifact_root),
        )
        if prepared.already_published:
            with session_factory() as db:
                projection = _required_projection(db, prepared.publication_id)
                _verify_readback(projection, summary, expected_status="published")
            _verify_frontend_readback(
                resolved_readback_client,
                projection,
                summary,
                expected_status="published",
            )
            comment_id = _ensure_issue_comment(
                session_factory,
                github,
                prepared.publication_id,
                summary,
                resolved_public_base_url,
            )
            _finalize_research_state(
                session_factory,
                prepared.publication_id,
                summary,
                comment_id=comment_id,
                published_at=published_at,
            )
            with session_factory() as db:
                projection = _required_projection(db, prepared.publication_id)
            _verify_frontend_readback(
                resolved_readback_client,
                projection,
                summary,
                expected_status="published",
                expected_report_marker="当前生效评价",
            )
            _finalize_github_issue(github, summary)
            return projection

        comment_id = _ensure_issue_comment(
            session_factory,
            github,
            prepared.publication_id,
            summary,
            resolved_public_base_url,
        )

        with session_factory() as db:
            projection = _required_projection(db, prepared.publication_id)
            _verify_readback(projection, summary)
        _verify_frontend_readback(
            resolved_readback_client,
            projection,
            summary,
            expected_status="pending",
        )

        with session_factory() as db, db.begin():
            publication = db.scalar(
                select(ResearchPublication)
                .where(ResearchPublication.id == prepared.publication_id)
                .with_for_update()
            )
            if publication is None or publication.status != "pending":
                raise PublicationConflictError("待发布记录已被其他流程改变")
            if publication.issue_comment_id != comment_id:
                raise PublicationConflictError("Issue 评论读回与待发布记录不一致")
            publication.status = "published"
            publication.published_at = published_at
        recover_published_failure = True

        with session_factory() as db:
            projection = _required_projection(db, prepared.publication_id)
            _verify_readback(projection, summary, expected_status="published")
        _verify_frontend_readback(
            resolved_readback_client,
            projection,
            summary,
            expected_status="published",
        )
        _finalize_research_state(
            session_factory,
            prepared.publication_id,
            summary,
            comment_id=comment_id,
            published_at=published_at,
        )
        with session_factory() as db:
            projection = _required_projection(db, prepared.publication_id)
        _verify_frontend_readback(
            resolved_readback_client,
            projection,
            summary,
            expected_status="published",
            expected_report_marker="当前生效评价",
        )
        _finalize_github_issue(github, summary)
        return projection
    except Exception as exc:
        failed_at = _utc_now()
        if prepared is not None and recover_published_failure:
            _mark_published_publication_blocked(
                session_factory,
                prepared.publication_id,
                exc,
                now=failed_at,
            )
        elif prepared is not None and not prepared.already_published:
            _mark_publication_failed(
                session_factory,
                prepared.publication_id,
                exc,
                now=failed_at,
            )
        elif prepared is None and existing_evaluation_id is not None:
            _mark_pending_evaluation_failed(
                session_factory,
                existing_evaluation_id,
                exc,
                now=failed_at,
            )
        if isinstance(exc, PublicationError):
            raise
        raise PublicationError(f"研究发布失败：{exc}") from exc


def prepare_research_evaluation(
    session_factory: SessionFactory,
    *,
    formal_research_id: str,
    draft: EvaluationDraft,
    now: datetime | None = None,
) -> ResearchPublicationProjectionOut:
    """冻结评价与 pending 发布记录；外部发布只由 research-worker 执行。"""

    with session_factory() as db:
        formal = db.get(FormalResearch, formal_research_id)
        if formal is not None and formal.origin == "historical_import":
            raise PublicationConflictError(
                "历史导入评价已由迁移合同冻结；需先一对一映射独立的策略研究 "
                "Issue，再发布既有 pending 评价"
            )

    prepared = _prepare_publication(
        session_factory,
        formal_research_id=formal_research_id,
        draft=draft,
        now=now or datetime.now(timezone.utc),
        existing_evaluation_id=None,
    )
    with session_factory() as db:
        return _required_projection(db, prepared.publication_id)


def _assert_github_publication_allowed(
    session_factory: SessionFactory,
    github: GitHubIssueClient,
    formal_research_id: str,
    *,
    evaluation_id: str | None,
) -> None:
    with session_factory() as db:
        formal = db.get(FormalResearch, formal_research_id)
        if formal is None or formal.origin != "historical_import":
            return
        publication = (
            db.scalar(
                select(ResearchPublication)
                .where(ResearchPublication.evaluation_id == evaluation_id)
                .order_by(ResearchPublication.version.desc())
                .limit(1)
            )
            if evaluation_id is not None
            else None
        )
        _assert_historical_issue_mapping(
            db,
            github,
            formal,
            allow_published_issue=(
                publication is not None and publication.status == "published"
            ),
        )


def _assert_historical_issue_mapping(
    db: Session,
    github: GitHubIssueClient,
    formal: FormalResearch,
    *,
    allow_published_issue: bool = False,
) -> None:
    mapping = db.get(ResearchPublicationIssueMapping, formal.id)
    if mapping is None:
        raise PublicationConflictError(
            "历史导入评价必须一对一映射独立的策略研究 Issue"
        )
    plan = db.get(FrozenResearchPlan, formal.plan_id)
    if plan is None:
        raise PublicationConflictError("历史导入评价缺少冻结研究计划")
    try:
        expected = resolve_historical_publication_issue(
            plan.strategy_id, mapping.issue_number
        )
    except ValueError as exc:
        raise PublicationConflictError(f"历史研究 Issue 冻结映射无效：{exc}") from exc
    if getattr(github, "repository", None) != EXPECTED_REPOSITORY:
        raise PublicationConflictError("GitHub 仓库与历史研究 Issue 冻结映射不一致")
    issue = github.get_issue(mapping.issue_number)
    try:
        validate_historical_publication_issue_snapshot(
            issue, expected, allow_published=allow_published_issue
        )
    except ValueError as exc:
        raise PublicationConflictError(
            f"历史导入评价必须使用冻结映射中的独立的策略研究 Issue：{exc}"
        ) from exc


@contextmanager
def _publication_claim(session_factory: SessionFactory):
    """在 PostgreSQL 上串行化外部发布，避免多 Worker 重复评论。"""

    with session_factory() as claim_db:
        if claim_db.get_bind().dialect.name != "postgresql":
            yield
            return
        claim_db.execute(
            text("SELECT pg_advisory_lock(:key)"),
            {"key": RESEARCH_PUBLICATION_ADVISORY_LOCK_KEY},
        )
        try:
            yield
        finally:
            claim_db.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": RESEARCH_PUBLICATION_ADVISORY_LOCK_KEY},
            )


def publish_existing_research_evaluation(
    session_factory: SessionFactory,
    github: GitHubIssueClient,
    *,
    evaluation_id: str,
    artifact_root: Path,
    public_base_url: str | None = None,
    now: datetime | None = None,
    readback_client: PublicationReadbackClient | None = None,
    readback_base_url: str | None = None,
) -> ResearchPublicationProjectionOut:
    """发布历史迁移等流程已经冻结、但仍为 pending 的结构化评价。"""

    with session_factory() as db:
        evaluation = db.get(ResearchEvaluation, evaluation_id)
        if evaluation is None:
            raise PublicationConflictError("待发布评价不存在")
        run_ids = tuple(
            db.scalars(
                select(ResearchEvaluationRun.run_id)
                .where(ResearchEvaluationRun.evaluation_id == evaluation.id)
                .order_by(ResearchEvaluationRun.run_id)
            ).all()
        )
        refs = tuple(
            EvidenceDraft(
                kind=item.kind,
                uri=item.uri,
                run_id=item.run_id,
                sha256=item.sha256,
                metadata=dict(item.metadata_json or {}),
            )
            for item in db.scalars(
                select(ResearchEvidenceRef)
                .where(ResearchEvidenceRef.evaluation_id == evaluation.id)
                .order_by(ResearchEvidenceRef.kind, ResearchEvidenceRef.uri)
            ).all()
        )
        draft = EvaluationDraft(
            conclusion=evaluation.conclusion,
            run_ids=run_ids,
            supporting_evidence=tuple(evaluation.supporting_evidence or []),
            opposing_evidence=tuple(evaluation.opposing_evidence or []),
            missing_evidence=tuple(evaluation.missing_evidence or []),
            limitations=tuple(evaluation.limitations or []),
            follow_up_recommendations=tuple(evaluation.follow_up_recommendations or []),
            evidence_refs=refs,
            supersedes_evaluation_id=evaluation.supersedes_evaluation_id,
        )
        formal_research_id = evaluation.formal_research_id
    return publish_research_evaluation(
        session_factory,
        github,
        formal_research_id=formal_research_id,
        draft=draft,
        artifact_root=artifact_root,
        public_base_url=public_base_url,
        now=now,
        existing_evaluation_id=evaluation_id,
        readback_client=readback_client,
        readback_base_url=readback_base_url,
    )


def publish_next_pending_research_evaluation(
    session_factory: SessionFactory,
    github: GitHubIssueClient,
    *,
    artifact_root: Path,
    public_base_url: str | None = None,
    readback_base_url: str | None = None,
    readback_client: PublicationReadbackClient | None = None,
    retry_failed_after_seconds: int = 300,
    now: datetime | None = None,
) -> ResearchPublicationProjectionOut | None:
    """发布一份已冻结评价；绝不从运行状态推断研究结论。"""

    if retry_failed_after_seconds < 0:
        raise ValueError("retry_failed_after_seconds 不能为负数")
    checked_at = now or datetime.now(timezone.utc)
    with session_factory() as db:
        publications = list(
            db.scalars(
                select(ResearchPublication).order_by(
                    ResearchPublication.created_at,
                    ResearchPublication.id,
                )
            ).all()
        )
        latest_by_evaluation: dict[str, ResearchPublication] = {}
        latest_published_by_formal: dict[str, ResearchPublication] = {}
        for publication in publications:
            evaluation_attempt = latest_by_evaluation.get(publication.evaluation_id)
            if (
                evaluation_attempt is None
                or publication.version > evaluation_attempt.version
            ):
                latest_by_evaluation[publication.evaluation_id] = publication
            if publication.status == "published":
                current = latest_published_by_formal.get(
                    publication.formal_research_id
                )
                if current is None or publication.version > current.version:
                    latest_published_by_formal[
                        publication.formal_research_id
                    ] = publication
        failure_states: dict[str, tuple[bool, datetime]] = {}
        lifecycle_outcomes: dict[str, str] = {}
        for event in db.scalars(
            select(ResearchEvent)
            .where(
                ResearchEvent.event_type.in_(PUBLICATION_LIFECYCLE_EVENT_TYPES)
            )
            .order_by(ResearchEvent.formal_research_id, ResearchEvent.sequence_no)
        ).all():
            payload = event.payload_json
            if isinstance(payload, dict) and payload.get("publicationId"):
                publication_id = str(payload["publicationId"])
                lifecycle_outcomes[publication_id] = event.event_type
                if event.event_type == "research_publication_failed":
                    failure_states[publication_id] = (
                        payload.get("retryable") is True,
                        event.occurred_at,
                    )
        effective_published_by_formal: dict[str, ResearchPublication] = {}
        for publication in publications:
            if (
                publication.status != "published"
                or lifecycle_outcomes.get(publication.id)
                not in PUBLICATION_SUCCESS_EVENT_TYPES
            ):
                continue
            current = effective_published_by_formal.get(
                publication.formal_research_id
            )
            if current is None or publication.version > current.version:
                effective_published_by_formal[
                    publication.formal_research_id
                ] = publication
        candidate_id = None
        published_candidates: list[
            tuple[str, str, int, bool, int | None]
        ] = []
        for publication in latest_by_evaluation.values():
            formal = db.get(FormalResearch, publication.formal_research_id)
            if formal is None:
                raise PublicationConflictError("发布记录缺少正式研究")
            if publication.status == "pending":
                candidate_id = publication.evaluation_id
                break
            if publication.status != "failed":
                continue
            if _publication_retry_due(
                failure_states.get(publication.id),
                checked_at=checked_at,
                retry_failed_after_seconds=retry_failed_after_seconds,
            ):
                candidate_id = publication.evaluation_id
                break
        if candidate_id is None:
            monitored_publication_ids: set[str] = set()
            monitoring_order = [
                *latest_published_by_formal.values(),
                *effective_published_by_formal.values(),
            ]
            for publication in monitoring_order:
                if publication.id in monitored_publication_ids:
                    continue
                monitored_publication_ids.add(publication.id)
                formal = db.get(FormalResearch, publication.formal_research_id)
                if formal is None:
                    raise PublicationConflictError("发布记录缺少正式研究")
                core_finalized = _publication_core_finalized(db, publication, formal)
                if not core_finalized and not _publication_retry_due(
                    failure_states.get(publication.id),
                    checked_at=checked_at,
                    retry_failed_after_seconds=retry_failed_after_seconds,
                    allow_missing=True,
                ):
                    continue
                if formal.origin == "historical_import":
                    try:
                        _assert_historical_issue_mapping(
                            db, github, formal, allow_published_issue=True
                        )
                    except PublicationConflictError:
                        candidate_id = publication.evaluation_id
                        break
                published_candidates.append(
                    (
                        publication.id,
                        publication.evaluation_id,
                        publication.issue_number,
                        core_finalized,
                        publication.issue_comment_id,
                    )
                )
    if candidate_id is None:
        for (
            publication_id,
            evaluation_id,
            issue_number,
            core_finalized,
            issue_comment_id,
        ) in published_candidates:
            if not core_finalized:
                candidate_id = evaluation_id
                break
            try:
                issue = github.get_issue(issue_number)
            except Exception:
                # 预检在 advisory lock 外，只能把候选交给锁内流程重新探测；
                # 这里改数据库会与另一个正在完成 GitHub PATCH 的 Worker 竞态。
                candidate_id = evaluation_id
                break
            if not _github_issue_finalized(issue):
                candidate_id = evaluation_id
                break
            try:
                comments = github.list_comments(issue_number)
            except Exception:
                candidate_id = evaluation_id
                break
            try:
                with session_factory() as verify_db:
                    summary = _build_summary(verify_db, publication_id)
                _verify_existing_bundle(
                    _publication_directory(Path(artifact_root), evaluation_id),
                    _publication_bundle_payloads(summary),
                )
                marker = _issue_comment_marker(summary)
                expected_body = _issue_comment(
                    summary,
                    _resolve_public_base_url(public_base_url),
                    marker,
                )
            except Exception:
                candidate_id = evaluation_id
                break
            if not _github_comment_finalized(
                comments,
                comment_id=issue_comment_id,
                expected_body=expected_body,
            ):
                candidate_id = evaluation_id
                break
    if candidate_id is None:
        return None
    return publish_existing_research_evaluation(
        session_factory,
        github,
        evaluation_id=candidate_id,
        artifact_root=artifact_root,
        public_base_url=public_base_url,
        readback_base_url=readback_base_url,
        readback_client=readback_client,
        now=checked_at,
    )


def _publication_retry_due(
    failure_state: tuple[bool, datetime] | None,
    *,
    checked_at: datetime,
    retry_failed_after_seconds: int,
    allow_missing: bool = False,
) -> bool:
    if failure_state is None:
        return allow_missing
    retryable, failed_at = failure_state
    if not retryable:
        return False
    if failed_at.tzinfo is None:
        failed_at = failed_at.replace(tzinfo=timezone.utc)
    return (checked_at - failed_at).total_seconds() >= retry_failed_after_seconds


def _github_issue_finalized(issue: Mapping[str, Any]) -> bool:
    labels = {str(item.get("name") or "") for item in issue.get("labels", [])}
    return str(issue.get("state") or "").lower() == "closed" and "研究:已发布" in labels


def _github_comment_finalized(
    comments: list[dict[str, Any]],
    *,
    comment_id: int | None,
    expected_body: str,
) -> bool:
    if comment_id is None:
        return False
    return any(
        str(item.get("id")) == str(comment_id)
        and str(item.get("body") or "") == expected_body
        for item in comments
    )


def _ensure_issue_comment(
    session_factory: SessionFactory,
    github: GitHubIssueClient,
    publication_id: str,
    summary: dict[str, Any],
    public_base_url: str,
) -> int:
    issue_number = int(summary["issueNumber"])
    marker = _issue_comment_marker(summary)
    expected_body = _issue_comment(summary, public_base_url, marker)
    if len(expected_body.encode("utf-8")) > MAX_GITHUB_ISSUE_COMMENT_BYTES:
        raise PublicationConflictError("GitHub 终态评论超过安全长度上限")
    with session_factory() as db:
        publication = db.get(ResearchPublication, publication_id)
        if publication is None:
            raise PublicationConflictError("待发布记录不存在")
        publication_status = publication.status
        stored_comment_id = publication.issue_comment_id
    comments = github.list_comments(issue_number)
    if publication_status == "published":
        if not _github_comment_finalized(
            comments,
            comment_id=stored_comment_id,
            expected_body=expected_body,
        ):
            raise PublicationConflictError(
                "已发布终态评论缺失或正文漂移；不可覆盖旧版本，需人工恢复原评论或发布更正版本"
            )
        return int(stored_comment_id)
    comment = github.ensure_comment(
        issue_number,
        expected_body,
        comments,
        marker=marker,
    )
    comment_id = int(comment["id"])
    with session_factory() as db, db.begin():
        publication = db.scalar(
            select(ResearchPublication)
            .where(ResearchPublication.id == publication_id)
            .with_for_update()
        )
        if publication is None or publication.status not in {"pending", "published"}:
            raise PublicationConflictError("待发布记录已被其他流程改变")
        if publication.issue_comment_id == comment_id:
            return comment_id
        if publication.issue_comment_id is not None:
            raise PublicationConflictError("既有 Issue 评论身份与发布记录不一致")
        if publication.status == "published":
            raise PublicationConflictError("已发布记录缺少冻结 Issue 评论身份")
        publication.issue_comment_id = comment_id
    return comment_id


def _finalize_github_issue(
    github: GitHubIssueClient,
    summary: dict[str, Any],
) -> None:
    """只在 published 投影已读回后原子关闭 Issue 并设置已发布标签。"""

    issue_number = int(summary["issueNumber"])
    issue = github.get_issue(issue_number)
    github.finalize_issue(issue_number, issue)


def _finalize_research_state(
    session_factory: SessionFactory,
    publication_id: str,
    summary: dict[str, Any],
    *,
    comment_id: int,
    published_at: datetime,
) -> None:
    with session_factory() as db, db.begin():
        publication = db.scalar(
            select(ResearchPublication)
            .where(ResearchPublication.id == publication_id)
            .with_for_update()
        )
        if publication is None or publication.status != "published":
            raise PublicationConflictError("发布记录尚未通过数据库与前端读回")
        if publication.issue_comment_id != comment_id:
            raise PublicationConflictError("Issue 评论读回与已发布记录不一致")
        formal = db.get(FormalResearch, publication.formal_research_id)
        if formal is None:
            raise PublicationConflictError("正式研究不存在")
        orchestration = db.scalar(
            select(ResearchOrchestration).where(
                ResearchOrchestration.formal_research_id == formal.id
            )
        )
        event_exists = _publication_finalized_event_exists(db, publication)
        latest_event_type = _latest_publication_lifecycle_event_type(db, publication)
        if latest_event_type in PUBLICATION_SUCCESS_EVENT_TYPES and _publication_core_finalized(
            db, publication, formal, orchestration=orchestration
        ):
            return
        if orchestration is not None:
            if orchestration.state != "publishing":
                raise PublicationConflictError("研究编排未处于 publishing 状态")
            transition_orchestration(orchestration, "published", reason=None)
        formal.phase = "published"
        formal.completed_at = published_at
        if not event_exists:
            append_research_event(
                db,
                formal.id,
                "research_published",
                {
                    "publicationId": publication.id,
                    "evaluationId": publication.evaluation_id,
                    "evaluationSha256": summary["evaluation"]["sha256"],
                    "conclusion": summary["evaluation"]["conclusion"],
                    "issueCommentId": comment_id,
                    "manifestUrl": summary["urls"]["manifest"],
                },
                occurred_at=published_at,
            )
        elif latest_event_type == "research_publication_failed":
            append_research_event(
                db,
                formal.id,
                "research_publication_recovered",
                {
                    "publicationId": publication.id,
                    "evaluationId": publication.evaluation_id,
                    "evaluationSha256": summary["evaluation"]["sha256"],
                    "conclusion": summary["evaluation"]["conclusion"],
                    "issueCommentId": comment_id,
                    "manifestUrl": summary["urls"]["manifest"],
                },
                occurred_at=published_at,
            )


def _publication_finalized_event_exists(
    db: Session,
    publication: ResearchPublication,
) -> bool:
    events = db.scalars(
        select(ResearchEvent)
        .where(
            ResearchEvent.formal_research_id == publication.formal_research_id,
            ResearchEvent.event_type == "research_published",
        )
        .order_by(ResearchEvent.sequence_no)
    ).all()
    return any(
        isinstance(event.payload_json, dict)
        and str(event.payload_json.get("publicationId") or "") == publication.id
        for event in events
    )


def _latest_publication_lifecycle_event_type(
    db: Session,
    publication: ResearchPublication,
) -> str | None:
    events = db.scalars(
        select(ResearchEvent)
        .where(
            ResearchEvent.formal_research_id == publication.formal_research_id,
            ResearchEvent.event_type.in_(PUBLICATION_LIFECYCLE_EVENT_TYPES),
        )
        .order_by(ResearchEvent.sequence_no.desc())
    ).all()
    for event in events:
        if (
            isinstance(event.payload_json, dict)
            and str(event.payload_json.get("publicationId") or "") == publication.id
        ):
            return event.event_type
    return None


def _publication_core_finalized(
    db: Session,
    publication: ResearchPublication,
    formal: FormalResearch,
    *,
    orchestration: ResearchOrchestration | None = None,
) -> bool:
    if orchestration is None:
        orchestration = db.scalar(
            select(ResearchOrchestration).where(
                ResearchOrchestration.formal_research_id == formal.id
            )
        )
    return (
        _latest_publication_lifecycle_event_type(db, publication)
        in PUBLICATION_SUCCESS_EVENT_TYPES
        and formal.phase == "published"
        and (orchestration is None or orchestration.state == "published")
    )


def get_evaluation_artifact_path(
    artifact_root: Path,
    evaluation_id: str,
    filename: str,
) -> Path:
    if filename not in PUBLICATION_ARTIFACTS:
        raise FileNotFoundError("发布工件不存在")
    return (
        Path(artifact_root)
        / "publications"
        / _safe_identifier(evaluation_id, "评价 ID")
        / filename
    )


def render_evaluation_report(
    db: Session,
    artifact_root: Path,
    evaluation_id: str,
) -> str:
    path = get_evaluation_artifact_path(artifact_root, evaluation_id, "report.html")
    _ = db
    return path.read_text(encoding="utf-8")


def render_evaluation_report_page(
    db: Session,
    artifact_root: Path,
    evaluation_id: str,
) -> str:
    """渲染动态状态壳；冻结的原始报告字节始终从 artifacts URL 读取。"""

    raw_path = get_evaluation_artifact_path(
        artifact_root, evaluation_id, "report.html"
    )
    if not raw_path.is_file():
        raise FileNotFoundError("冻结研究评价报告不存在")
    publication = db.scalar(
        select(ResearchPublication)
        .where(ResearchPublication.evaluation_id == evaluation_id)
        .order_by(ResearchPublication.version.desc())
        .limit(1)
    )
    if publication is None:
        raise FileNotFoundError("研究评价缺少发布记录")
    projection = get_publication_projection(db, publication.id)
    if projection is None:
        raise FileNotFoundError("研究评价缺少发布投影")
    raw_url = _artifact_url(evaluation_id, "report.html")
    successor_id = (
        str(projection.superseded_by_evaluation_id)
        if projection.superseded_by_evaluation_id is not None
        else None
    )
    if successor_id is not None:
        state_class = "replaced"
        state_title = "此评价已被替代"
        state_detail = (
            "旧评价与原始工件保持不变；当前结论请查看替代版本："
            f'<a href="{escape(_report_url(successor_id))}">'
            f"{escape(successor_id)}</a>"
        )
    elif is_publication_effective(db, publication.id):
        state_class = "current"
        state_title = "当前生效评价"
        state_detail = (
            "数据库、API 与前端已完成一致读回；"
            "GitHub Issue 终态由发布 Worker 持续校验与补偿。"
        )
    else:
        state_class = "incomplete"
        state_title = "此评价尚未完成一致发布，不代表当前研究结论"
        state_detail = "请以仍标记为“当前生效评价”的上一版本为准。"
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(state_title)}｜研究评价</title>"
        "<style>body{margin:0;background:#080d13;color:#dbe7f3;font:15px/1.5 system-ui,sans-serif}"
        ".banner{padding:18px 24px;border-bottom:2px solid #54687c;background:#151f2a}"
        ".banner.current{border-color:#4fb286}.banner.replaced{border-color:#e8b15a}"
        ".banner.incomplete{border-color:#de6b6b}h1{margin:0 0 6px;font-size:20px}"
        ".banner p{margin:0}.raw{display:block;padding:10px 24px;color:#9dc7ee}"
        "iframe{display:block;width:100%;height:calc(100vh - 125px);border:0;background:#0b1118}"
        f'</style></head><body data-evaluation-id="{escape(evaluation_id)}">'
        f'<header class="banner {state_class}"><h1>{escape(state_title)}</h1>'
        f"<p>{state_detail}</p></header>"
        f'<a class="raw" href="{escape(raw_url)}">打开不可变原始报告工件</a>'
        f'<iframe title="冻结研究评价报告" src="{escape(raw_url)}"></iframe>'
        "</body></html>\n"
    )


def _prepare_publication(
    session_factory: SessionFactory,
    *,
    formal_research_id: str,
    draft: EvaluationDraft,
    now: datetime,
    existing_evaluation_id: str | None,
) -> _PreparedPublication:
    with session_factory() as db, db.begin():
        formal = db.scalar(
            select(FormalResearch)
            .where(FormalResearch.id == formal_research_id)
            .with_for_update()
        )
        if formal is None:
            raise PublicationConflictError("正式研究不存在")
        plan = db.get(FrozenResearchPlan, formal.plan_id)
        if plan is None:
            raise PublicationConflictError("正式研究缺少冻结计划")

        runs, normalized = _validate_and_normalize_draft(db, formal, draft)
        evaluation_sha256 = canonical_sha256(
            {
                "schemaVersion": "research-evaluation/v2",
                "formalResearchId": formal.id,
                **normalized,
            }
        )
        evaluation = (
            db.get(ResearchEvaluation, existing_evaluation_id)
            if existing_evaluation_id is not None
            else db.scalar(
                select(ResearchEvaluation).where(
                    ResearchEvaluation.evaluation_sha256 == evaluation_sha256
                )
            )
        )
        if evaluation is not None and evaluation.formal_research_id != formal.id:
            raise PublicationConflictError("评价指纹已被其他正式研究占用")
        if existing_evaluation_id is not None:
            if evaluation is None:
                raise PublicationConflictError("待发布评价不存在")
            _verify_existing_evaluation(db, formal, evaluation, runs, normalized)

        latest_evaluation = db.scalar(
            select(ResearchEvaluation)
            .where(ResearchEvaluation.formal_research_id == formal.id)
            .order_by(ResearchEvaluation.version.desc())
            .limit(1)
        )
        if evaluation is None:
            _validate_supersession(
                db, latest_evaluation, draft.supersedes_evaluation_id
            )
            evaluation = ResearchEvaluation(
                id=str(uuid4()),
                formal_research_id=formal.id,
                version=(latest_evaluation.version + 1 if latest_evaluation else 1),
                conclusion=draft.conclusion,
                evaluation_sha256=evaluation_sha256,
                supersedes_evaluation_id=draft.supersedes_evaluation_id,
                supporting_evidence=normalized["supportingEvidence"],
                opposing_evidence=normalized["opposingEvidence"],
                missing_evidence=normalized["missingEvidence"],
                limitations=normalized["limitations"],
                follow_up_recommendations=normalized["followUpRecommendations"],
            )
            db.add(evaluation)
            db.flush()
            db.add_all(
                [
                    ResearchEvaluationRun(
                        evaluation_id=evaluation.id, run_id=run.run_id
                    )
                    for run in runs
                ]
                + [
                    ResearchEvidenceRef(
                        id=str(uuid4()),
                        evaluation_id=evaluation.id,
                        run_id=item["runId"],
                        kind=item["kind"],
                        uri=item["uri"],
                        sha256=item["sha256"],
                        metadata_json=item["metadata"],
                    )
                    for item in normalized["evidenceRefs"]
                ]
            )
            db.flush()

        published = db.scalar(
            select(ResearchPublication)
            .where(
                ResearchPublication.evaluation_id == evaluation.id,
                ResearchPublication.status == "published",
            )
            .order_by(ResearchPublication.version.desc())
            .limit(1)
        )
        if published is not None:
            if not _publication_core_finalized(db, published, formal):
                _enter_publishing_state(db, formal)
            return _PreparedPublication(published.id, evaluation.id, True)
        pending = db.scalar(
            select(ResearchPublication)
            .where(
                ResearchPublication.evaluation_id == evaluation.id,
                ResearchPublication.status == "pending",
            )
            .order_by(ResearchPublication.version.desc())
            .limit(1)
        )
        if pending is not None:
            _normalize_pending_publication(
                pending,
                evaluation,
                issue_number=_publication_issue_number(db, formal, plan),
            )
            _enter_publishing_state(db, formal)
            return _PreparedPublication(pending.id, evaluation.id, False)

        previous = db.scalar(
            select(ResearchPublication)
            .where(ResearchPublication.formal_research_id == formal.id)
            .order_by(ResearchPublication.version.desc())
            .limit(1)
        )
        publication_id = str(uuid4())
        publication_version = previous.version + 1 if previous else 1
        manifest_url = _artifact_url(evaluation.id, "manifest.json")
        issue_number = _publication_issue_number(db, formal, plan)
        publication = ResearchPublication(
            id=publication_id,
            formal_research_id=formal.id,
            evaluation_id=evaluation.id,
            version=publication_version,
            status="pending",
            publication_sha256=canonical_sha256(
                {
                    "schemaVersion": "research-publication/v1",
                    "publicationId": publication_id,
                    "formalResearchId": formal.id,
                    "evaluationId": evaluation.id,
                    "evaluationSha256": evaluation.evaluation_sha256,
                    "version": publication_version,
                    "supersedesPublicationId": previous.id if previous else None,
                    "manifestUrl": manifest_url,
                    "issueNumber": issue_number,
                }
            ),
            supersedes_publication_id=previous.id if previous else None,
            artifact_manifest_uri=manifest_url,
            issue_number=issue_number,
        )
        db.add(publication)
        _enter_publishing_state(db, formal)
        append_research_event(
            db,
            formal.id,
            "research_publication_prepared",
            {
                "publicationId": publication.id,
                "evaluationId": evaluation.id,
                "evaluationSha256": evaluation.evaluation_sha256,
                "conclusion": evaluation.conclusion,
                "supersedesPublicationId": publication.supersedes_publication_id,
            },
            occurred_at=now,
        )
        return _PreparedPublication(publication.id, evaluation.id, False)


def _validate_and_normalize_draft(
    db: Session,
    formal: FormalResearch,
    draft: EvaluationDraft,
) -> tuple[list[ResearchRun], dict[str, Any]]:
    plan = db.get(FrozenResearchPlan, formal.plan_id)
    if plan is None:
        raise PublicationConflictError("正式研究缺少冻结计划")
    if draft.conclusion not in RESEARCH_CONCLUSION_VALUES:
        raise PublicationConflictError("研究结论不在五类允许值中")
    allowed_phases = (
        {"evaluating", "stopped", "published"}
        if formal.origin == "native"
        else {"stopped", "published"}
    )
    if formal.phase not in allowed_phases:
        raise PublicationConflictError(
            f"正式研究阶段 {formal.phase} 尚未终止运行，不能冻结评价"
        )
    work_item = db.scalar(
        select(ResearchWorkItem).where(ResearchWorkItem.formal_research_id == formal.id)
    )
    if formal.origin == "native" and work_item is None:
        raise PublicationConflictError("原生正式研究缺少研究工作项，不能证明运行已终态")
    if work_item is not None and work_item.status not in TERMINAL_RUN_STATUSES:
        raise PublicationConflictError(
            f"研究工作项状态 {work_item.status} 尚未终态，不能冻结评价"
        )
    run_ids = sorted(set(draft.run_ids))
    if len(run_ids) != len(draft.run_ids):
        raise PublicationConflictError("评价运行 ID 不得重复")
    runs = list(
        db.scalars(
            select(ResearchRun)
            .where(
                ResearchRun.formal_research_id == formal.id,
            )
            .order_by(ResearchRun.run_id)
        ).all()
    )
    if [item.run_id for item in runs] != run_ids:
        raise PublicationConflictError("评价必须完整包含正式研究的全部运行")
    non_terminal = [
        item.run_id for item in runs if item.status not in TERMINAL_RUN_STATUSES
    ]
    if non_terminal:
        raise PublicationConflictError("只能发布已终态的研究运行")
    if draft.conclusion == "研究通过":
        if not any(item.status == "succeeded" for item in runs):
            raise PublicationConflictError("研究通过必须至少包含一个成功运行")
        if draft.missing_evidence:
            raise PublicationConflictError("研究通过不得携带尚缺证据")
    if any(item.status == "succeeded" and not item.result_fingerprint for item in runs):
        raise PublicationConflictError("成功运行缺少结果指纹，不得发布")
    if not runs and not (draft.missing_evidence or draft.limitations):
        raise PublicationConflictError("无运行评价必须明确缺失证据或限制")

    run_by_id = {item.run_id: item for item in runs}
    evidence_refs = []
    unique_refs: set[tuple[str, str]] = set()
    for item in draft.evidence_refs:
        if item.kind not in EVIDENCE_KINDS:
            raise PublicationConflictError(f"证据类型不受支持：{item.kind}")
        if not item.uri.strip():
            raise PublicationConflictError("证据 URI 不能为空")
        if item.run_id is not None and item.run_id not in run_ids:
            raise PublicationConflictError("证据引用的运行未包含在评价中")
        if item.sha256 is not None and (
            len(item.sha256) != 64
            or any(char not in "0123456789abcdef" for char in item.sha256)
        ):
            raise PublicationConflictError("证据 SHA-256 格式无效")
        if item.sha256 is None:
            raise PublicationConflictError("发布证据必须声明 SHA-256")
        _validate_canonical_evidence_ref(item, run_by_id)
        key = (item.kind, item.uri)
        if key in unique_refs:
            raise PublicationConflictError("同类证据 URI 不得重复")
        unique_refs.add(key)
        evidence_refs.append(
            {
                "kind": item.kind,
                "uri": item.uri,
                "runId": item.run_id,
                "sha256": item.sha256,
                "metadata": json_safe_value(dict(item.metadata or {})),
            }
        )
    evidence_refs.sort(key=lambda item: (item["kind"], item["uri"]))

    supporting_evidence = _normalize_evidence_list(draft.supporting_evidence)
    opposing_evidence = _normalize_evidence_list(draft.opposing_evidence)
    missing_evidence = _normalize_evidence_list(draft.missing_evidence)
    limitations = _normalize_evidence_list(draft.limitations)
    follow_up_recommendations = _normalize_evidence_list(
        draft.follow_up_recommendations
    )
    _validate_conclusion_evidence_contract(
        draft.conclusion,
        supporting_evidence=supporting_evidence,
        opposing_evidence=opposing_evidence,
        missing_evidence=missing_evidence,
        limitations=limitations,
        follow_up_recommendations=follow_up_recommendations,
    )
    successful_run_ids = {
        item.run_id for item in runs if item.status == "succeeded"
    }
    if draft.conclusion == "研究通过":
        _validate_research_pass_contract(
            supporting_evidence,
            evidence_refs,
            successful_run_ids={
                item.run_id for item in runs if item.status == "succeeded"
            },
            plan_gates=_frozen_plan_gates(plan),
        )
        _validate_research_pass_report_contract(runs, plan)
    if formal.origin == "native" and successful_run_ids and not any(
        urlparse(item["uri"]).scheme == "artifacts"
        and item["runId"] in successful_run_ids
        for item in evidence_refs
    ):
        raise PublicationConflictError(
            "成功运行的评价必须至少引用一项绑定该运行的 canonical 证据"
        )

    normalized = {
        "conclusion": draft.conclusion,
        "supersedesEvaluationId": draft.supersedes_evaluation_id,
        "runIds": run_ids,
        "runIdentities": _evaluation_run_identities(
            runs, freeze_audit=formal.origin == "native"
        ),
        "supportingEvidence": supporting_evidence,
        "opposingEvidence": opposing_evidence,
        "missingEvidence": missing_evidence,
        "limitations": limitations,
        "followUpRecommendations": follow_up_recommendations,
        "evidenceRefs": evidence_refs,
    }
    canonical_json_bytes(normalized)
    return runs, normalized


def _validate_conclusion_evidence_contract(
    conclusion: str,
    *,
    supporting_evidence: list[dict[str, Any]],
    opposing_evidence: list[dict[str, Any]],
    missing_evidence: list[dict[str, Any]],
    limitations: list[dict[str, Any]],
    follow_up_recommendations: list[dict[str, Any]],
) -> None:
    required: dict[str, tuple[tuple[str, list[dict[str, Any]]], ...]] = {
        "有条件候选": (
            ("支持证据", supporting_evidence),
            ("明确条件或限制", limitations),
            ("后续建议", follow_up_recommendations),
        ),
        "证据不足": (
            ("尚缺证据", missing_evidence),
            ("限制项", limitations),
            ("后续建议", follow_up_recommendations),
        ),
        "受阻": (
            ("阻塞所导致的尚缺证据", missing_evidence),
            ("阻塞或限制事实", limitations),
            ("后续建议", follow_up_recommendations),
        ),
        "不通过": (
            ("反对证据", opposing_evidence),
            ("限制项", limitations),
            ("后续建议", follow_up_recommendations),
        ),
        "研究通过": (
            ("限制项", limitations),
            ("后续建议", follow_up_recommendations),
        ),
    }
    contracts = required.get(conclusion, ())
    missing = [label for label, values in contracts if not values]
    if missing:
        raise PublicationConflictError(
            f"研究结论“{conclusion}”缺少最低评价内容：{', '.join(missing)}"
        )


def _validate_research_pass_contract(
    supporting_evidence: list[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
    *,
    successful_run_ids: set[str],
    plan_gates: tuple[str, ...],
) -> None:
    non_canonical = [
        item["uri"]
        for item in evidence_refs
        if urlparse(item["uri"]).scheme != "artifacts"
    ]
    if non_canonical:
        raise PublicationConflictError("研究通过只接受可校验的 canonical 工件证据")
    declared_uris = {item["uri"] for item in evidence_refs}
    successful_refs = [
        item for item in evidence_refs if item["runId"] in successful_run_ids
    ]
    successful_uris = {item["uri"] for item in successful_refs}
    kinds_by_uri: dict[str, set[str]] = {}
    paths_by_kind: dict[str, set[str]] = {}
    for item in successful_refs:
        kinds_by_uri.setdefault(item["uri"], set()).add(item["kind"])
        path = urlparse(item["uri"]).path.removeprefix("/")
        paths_by_kind.setdefault(item["kind"], set()).add(path)
    gates: dict[str, Mapping[str, Any]] = {}
    for item in supporting_evidence:
        gate = item.get("gate")
        if gate is None:
            continue
        if not isinstance(gate, str) or gate not in RESEARCH_PASS_REQUIRED_GATES:
            raise PublicationConflictError(f"研究通过包含未知硬门禁：{gate}")
        if gate in gates:
            raise PublicationConflictError(f"研究通过硬门禁重复：{gate}")
        if item.get("status") != "passed":
            raise PublicationConflictError(
                f"研究通过硬门禁未通过：{RESEARCH_PASS_REQUIRED_GATES[gate]}"
            )
        references = item.get("evidenceRefs")
        if (
            not isinstance(references, list)
            or not references
            or any(
                not isinstance(uri, str) or uri not in declared_uris
                for uri in references
            )
        ):
            raise PublicationConflictError(
                f"研究通过硬门禁缺少已声明证据引用：{RESEARCH_PASS_REQUIRED_GATES[gate]}"
            )
        if any(uri not in successful_uris for uri in references):
            raise PublicationConflictError(
                f"研究通过硬门禁证据必须来自成功运行："
                f"{RESEARCH_PASS_REQUIRED_GATES[gate]}"
            )
        referenced_kinds = set().union(*(kinds_by_uri[uri] for uri in references))
        missing_gate_kinds = sorted(
            RESEARCH_PASS_GATE_EVIDENCE_KINDS[gate] - referenced_kinds
        )
        if missing_gate_kinds:
            raise PublicationConflictError(
                f"研究通过硬门禁证据类型不足：{RESEARCH_PASS_REQUIRED_GATES[gate]}"
            )
        gates[gate] = item
    missing_gates = [
        label
        for gate, label in RESEARCH_PASS_REQUIRED_GATES.items()
        if gate not in gates
    ]
    if missing_gates:
        raise PublicationConflictError(
            f"研究通过缺少硬门禁：{', '.join(missing_gates)}"
        )
    missing_kinds = sorted(RESEARCH_PASS_REQUIRED_EVIDENCE_KINDS - set(paths_by_kind))
    if missing_kinds:
        raise PublicationConflictError(
            f"研究通过缺少证据类型：{', '.join(missing_kinds)}"
        )
    for kind, required_paths in RESEARCH_PASS_REQUIRED_EVIDENCE_PATHS.items():
        missing_paths = sorted(required_paths - paths_by_kind[kind])
        if missing_paths:
            raise PublicationConflictError(
                f"研究通过的 {kind} 证据缺少 canonical 工件：{', '.join(missing_paths)}"
            )
    declared_plan_gates: dict[str, Mapping[str, Any]] = {}
    for item in supporting_evidence:
        plan_gate = item.get("planGate")
        if plan_gate is None:
            continue
        if not isinstance(plan_gate, str) or plan_gate not in plan_gates:
            raise PublicationConflictError(f"研究通过包含未知事前门禁：{plan_gate}")
        if plan_gate in declared_plan_gates:
            raise PublicationConflictError(f"研究通过事前门禁重复：{plan_gate}")
        references = item.get("evidenceRefs")
        if item.get("status") != "passed":
            raise PublicationConflictError(f"研究通过事前门禁未通过：{plan_gate}")
        if (
            not isinstance(references, list)
            or not references
            or any(
                not isinstance(uri, str) or uri not in successful_uris
                for uri in references
            )
        ):
            raise PublicationConflictError(
                f"研究通过事前门禁缺少成功运行的 canonical 证据：{plan_gate}"
            )
        declared_plan_gates[plan_gate] = item
    missing_plan_gates = [
        gate for gate in plan_gates if gate not in declared_plan_gates
    ]
    if missing_plan_gates:
        raise PublicationConflictError(
            f"研究通过缺少冻结计划事前门禁：{', '.join(missing_plan_gates)}"
        )


def _frozen_plan_gates(plan: FrozenResearchPlan) -> tuple[str, ...]:
    gates = plan.plan_json.get("gates") if isinstance(plan.plan_json, dict) else None
    if not isinstance(gates, list) or any(
        not isinstance(item, str) or not item.strip() for item in gates
    ):
        raise PublicationConflictError("冻结研究计划缺少有效的事前门禁")
    normalized = tuple(item.strip() for item in gates)
    if len(set(normalized)) != len(normalized):
        raise PublicationConflictError("冻结研究计划的事前门禁不得重复")
    return normalized


def _validate_research_pass_report_contract(
    runs: list[ResearchRun], plan: FrozenResearchPlan
) -> None:
    required_metrics = {
        "warmupStartDate",
        "startDate",
        "endDate",
        "observations",
        "openTradingDays",
        "rebalanceCount",
        "requestCount",
        "executionCount",
        "blockedCount",
        "independentTradeCount",
        "totalReturn",
        "annualizedVolatility",
        "maxDrawdown",
        "benchmarkTotalReturn",
        "averageOneWayTurnover",
        "cumulativeTransactionCostRate",
        "blockedRequestRate",
        "maxSingleWeight",
        "averageGrossExposure",
        "endingGrossExposure",
        "averageNetExposure",
        "endingNetExposure",
        "averageHhi",
        "endingHhi",
        "var95",
        "es95",
        "yearly",
        "marketRegimes",
        "walkForward",
        "parameterNeighborhood",
        "costStress",
        "capacity",
        "riskSummary",
    }
    for run in runs:
        if run.status != "succeeded":
            continue
        root = Path(run.artifact_root)
        manifest = _read_canonical_json(root / "manifest.json", "manifest.json")
        if manifest.get("artifactSchemaVersion", 1) < 5:
            raise PublicationConflictError(
                "研究通过只接受由 Runner 生成冻结参数、容量与风险摘要的归档 schema v5+"
            )
        metrics = _read_canonical_json(
            root / "oos_metrics.json", "oos_metrics.json"
        )
        config = manifest.get("config")
        snapshot = manifest.get("dataSnapshot")
        if not isinstance(config, Mapping) or not isinstance(snapshot, Mapping):
            raise PublicationConflictError(
                "研究通过报告缺少冻结配置或数据快照合同"
            )
        missing_config = sorted(
            {
                "benchmark",
                "executionPolicy",
                "costModel",
                "validationPolicy",
                "evaluationSampleSplits",
                "evaluationPolicy",
                "riskPolicy",
                "researchPassPolicy",
            }
            - set(config)
        )
        if missing_config:
            raise PublicationConflictError(
                "研究通过报告缺少冻结配置：" + ", ".join(missing_config)
            )
        if not isinstance(config.get("riskPolicy"), Mapping) or config[
            "riskPolicy"
        ].get("mode") == "none":
            raise PublicationConflictError(
                "研究通过报告必须启用冻结风险暴露与风险贡献策略"
            )
        plan_contract = plan.plan_json if isinstance(plan.plan_json, dict) else {}
        report_contract = plan_contract.get("reportContract")
        if (
            config.get("evaluationSampleSplits") != plan_contract.get("sampleSplits")
            or not isinstance(report_contract, Mapping)
            or config.get("evaluationPolicy")
            != report_contract.get("evaluationPolicy")
            or config.get("researchPassPolicy")
            != report_contract.get("researchPassPolicy")
        ):
            raise PublicationConflictError(
                "研究通过报告的 OOS 边界、评价策略或研究通过策略与冻结计划不一致"
            )
        try:
            validate_oos_metrics_contract(metrics, config)
        except ValueError as exc:
            raise PublicationConflictError(
                f"研究通过报告的 canonical OOS 指标无效：{exc}"
            ) from exc
        _validate_research_pass_market_regimes(
            metrics.get("marketRegimes"),
            expected_observations=metrics.get("observations"),
        )
        missing_metrics = sorted(required_metrics - set(metrics))
        if missing_metrics:
            raise PublicationConflictError(
                "研究通过报告缺少 canonical 指标："
                + ", ".join(missing_metrics)
            )
        _validate_research_pass_parameter_neighborhood(
            metrics.get("parameterNeighborhood"), config
        )
        _validate_research_pass_capacity(metrics.get("capacity"), config)
        _validate_research_pass_risk_summary(
            metrics.get("riskSummary"), metrics=metrics
        )
        if not _read_canonical_benchmark_series(root, manifest):
            raise PublicationConflictError(
                "研究通过报告缺少匹配基准的 canonical 净值路径"
            )
    contract = plan.plan_json if isinstance(plan.plan_json, dict) else {}
    trial_budget = contract.get("trialBudget")
    max_trials = (
        trial_budget.get("maxTrials")
        if isinstance(trial_budget, Mapping)
        else None
    )
    if isinstance(max_trials, int) and max_trials > 1:
        for run in runs:
            if run.status != "succeeded":
                continue
            metrics = _read_canonical_json(
                Path(run.artifact_root) / "oos_metrics.json", "oos_metrics.json"
            )
            _validate_multiple_testing_metrics(metrics, max_trials=max_trials)


def _validate_research_pass_parameter_neighborhood(
    value: Any, config: Mapping[str, Any]
) -> None:
    if not isinstance(value, Mapping) or value.get("status") != "complete":
        raise PublicationConflictError(
            "研究通过报告必须冻结完整的 canonical 参数邻域证据"
        )
    try:
        policy = validate_research_pass_policy(
            config.get("researchPassPolicy"), config
        )["parameterNeighborhood"]
        expected_configs = {
            variant_id: candidate
            for variant_id, candidate in build_parameter_neighborhood_configs(
                config
            )
        }
    except (TypeError, ValueError) as exc:
        raise PublicationConflictError(
            f"研究通过报告的冻结参数邻域策略无效：{exc}"
        ) from exc
    configurations = value.get("configurations")
    if (
        value.get("policySha256") != canonical_sha256(policy)
        or value.get("evaluatedConfigurations") != len(policy["variants"])
        or value.get("maximumAllowedAbsoluteOosReturnDifference")
        != policy["maximumAbsoluteOosReturnDifference"]
        or value.get("minimumAllowedOosTotalReturn")
        != policy["minimumOosTotalReturn"]
        or not isinstance(configurations, list)
        or len(configurations) != len(policy["variants"])
    ):
        raise PublicationConflictError(
            "研究通过报告的参数邻域未逐项绑定冻结计划"
        )
    expected_variants = {item["id"]: item for item in policy["variants"]}
    observed_returns: list[float] = []
    seen_ids: set[str] = set()
    for item in configurations:
        if not isinstance(item, Mapping):
            raise PublicationConflictError("研究通过报告包含无效参数邻域配置")
        variant_id = item.get("id")
        expected_variant = expected_variants.get(variant_id)
        expected_config = expected_configs.get(variant_id)
        if (
            not isinstance(variant_id, str)
            or variant_id in seen_ids
            or expected_variant is None
            or expected_config is None
            or item.get("changes") != expected_variant["changes"]
            or item.get("configSha256")
            != canonical_run_config_sha256(expected_config)
        ):
            raise PublicationConflictError(
                "研究通过报告的参数邻域配置身份与冻结计划不一致"
            )
        seen_ids.add(variant_id)
        for field in ("totalReturn", "maxDrawdown"):
            metric = item.get(field)
            if (
                isinstance(metric, bool)
                or not isinstance(metric, (int, float))
                or not math.isfinite(float(metric))
            ):
                raise PublicationConflictError(
                    f"研究通过报告的参数邻域指标无效：{variant_id}/{field}"
                )
        observed_returns.append(float(item["totalReturn"]))
    observed_difference = max(observed_returns) - min(observed_returns)
    observed_minimum = min(observed_returns)
    if (
        seen_ids != set(expected_variants)
        or not _numbers_close(
            value.get("maximumObservedAbsoluteOosReturnDifference"),
            observed_difference,
        )
        or not _numbers_close(
            value.get("minimumObservedOosTotalReturn"), observed_minimum
        )
        or value.get("passed") is not True
        or observed_difference
        > float(policy["maximumAbsoluteOosReturnDifference"]) + 1e-12
        or observed_minimum < float(policy["minimumOosTotalReturn"]) - 1e-12
    ):
        raise PublicationConflictError(
            "研究通过报告的参数邻域结果未通过冻结阈值或汇总不闭合"
        )


def _validate_research_pass_market_regimes(
    value: Any, *, expected_observations: Any
) -> None:
    if not isinstance(value, Mapping) or not isinstance(
        value.get("cells"), Mapping
    ):
        raise PublicationConflictError("研究通过报告缺少结构化市场环境单元")
    required_fields = {
        "startDate",
        "endDate",
        "observations",
        "openTradingDays",
        "rebalanceCount",
        "requestCount",
        "executionCount",
        "blockedCount",
        "independentTradeCount",
        "totalReturn",
        "benchmarkTotalReturn",
        "activeTotalReturn",
        "annualizedVolatility",
        "maxDrawdown",
        "averageOneWayTurnover",
        "cumulativeTransactionCostRate",
        "blockedRequestRate",
        "averageGrossExposure",
        "endingGrossExposure",
        "averageNetExposure",
        "endingNetExposure",
        "averageHhi",
        "endingHhi",
    }
    count_fields = {
        "observations",
        "openTradingDays",
        "rebalanceCount",
        "requestCount",
        "executionCount",
        "blockedCount",
        "independentTradeCount",
    }
    directions: set[str] = set()
    volatilities: set[str] = set()
    total_observations = 0
    for name, cell in value["cells"].items():
        if not isinstance(cell, Mapping) or cell.get("status") != "available":
            continue
        observations = cell.get("observations")
        if (
            not isinstance(name, str)
            or "_" not in name
            or isinstance(observations, bool)
            or not isinstance(observations, int)
            or observations <= 0
        ):
            raise PublicationConflictError("研究通过报告包含无效市场环境单元")
        missing = sorted(required_fields - set(cell))
        if missing:
            raise PublicationConflictError(
                "研究通过报告的市场环境单元缺少指标：" + ", ".join(missing)
            )
        try:
            cell_start = datetime.fromisoformat(str(cell["startDate"])).date()
            cell_end = datetime.fromisoformat(str(cell["endDate"])).date()
        except ValueError as exc:
            raise PublicationConflictError(
                f"研究通过报告的市场环境日期无效：{name}"
            ) from exc
        if cell_start > cell_end:
            raise PublicationConflictError(
                f"研究通过报告的市场环境日期倒置：{name}"
            )
        if any(
            isinstance(cell[field], bool)
            or not isinstance(cell[field], int)
            or cell[field] < 0
            for field in count_fields
        ) or cell["openTradingDays"] != observations:
            raise PublicationConflictError(
                f"研究通过报告的市场环境计数字段无效：{name}"
            )
        for field in required_fields - {"startDate", "endDate"} - count_fields:
            metric = cell[field]
            if (
                field == "annualizedVolatility"
                and metric is None
                and observations == 1
            ):
                continue
            if isinstance(metric, bool) or not isinstance(metric, (int, float)):
                raise PublicationConflictError(
                    f"研究通过报告的市场环境指标无效：{name}/{field}"
                )
            if not math.isfinite(float(metric)):
                raise PublicationConflictError(
                    f"研究通过报告的市场环境指标非有限：{name}/{field}"
                )
        direction, volatility = name.split("_", 1)
        if direction not in {"上涨", "下跌", "震荡"} or volatility not in {
            "高波",
            "低波",
        }:
            raise PublicationConflictError("研究通过报告包含未知市场环境名称")
        directions.add(direction)
        volatilities.add(volatility)
        total_observations += observations
    coverage = value.get("coverage")
    if (
        len(directions) < 2
        or len(volatilities) < 2
        or not isinstance(coverage, Mapping)
        or set(coverage.get("directionStates") or []) != directions
        or set(coverage.get("volatilityStates") or []) != volatilities
        or coverage.get("observations") != total_observations
        or isinstance(expected_observations, bool)
        or not isinstance(expected_observations, int)
        or expected_observations != total_observations
    ):
        raise PublicationConflictError(
            "研究通过报告的实际市场环境单元未覆盖至少两种方向与两种波动环境"
        )


def _validate_research_pass_capacity(
    value: Any, config: Mapping[str, Any]
) -> None:
    if not isinstance(value, Mapping) or value.get("status") != "complete":
        raise PublicationConflictError(
            "研究通过报告必须冻结预期资金规模、ADV 参与率与冲击模型"
        )
    try:
        policy = validate_research_pass_policy(
            config.get("researchPassPolicy"), config
        )["capacity"]
    except (TypeError, ValueError) as exc:
        raise PublicationConflictError(
            f"研究通过报告的冻结容量策略无效：{exc}"
        ) from exc
    if (
        value.get("policySha256") != canonical_sha256(policy)
        or value.get("expectedCapital") != policy["expectedCapital"]
        or value.get("advLookbackPeriods") != policy["advLookbackPeriods"]
        or value.get("minimumAdvObservations")
        != policy["minimumAdvObservations"]
        or value.get("marketAmountScale") != policy["marketAmountScale"]
        or value.get("maximumAllowedAdvParticipationRate")
        != policy["maximumAdvParticipationRate"]
        or value.get("impactModel") != policy["impactModel"]
        or value.get("maximumAllowedModeledImpactRate")
        != policy["maximumModeledImpactRate"]
    ):
        raise PublicationConflictError(
            "研究通过报告的容量合同与冻结计划不一致"
        )
    required_numbers = (
        "medianAdvParticipationRate",
        "p95AdvParticipationRate",
        "maxAdvParticipationRate",
        "maxModeledImpactRate",
    )
    numbers: dict[str, float] = {}
    for field in required_numbers:
        raw = value.get(field)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise PublicationConflictError(f"研究通过报告的容量字段无效：{field}")
        number = float(raw)
        if not math.isfinite(number) or number < 0:
            raise PublicationConflictError(f"研究通过报告的容量字段无效：{field}")
        numbers[field] = number
    if not (
        numbers["medianAdvParticipationRate"]
        <= numbers["p95AdvParticipationRate"]
        <= numbers["maxAdvParticipationRate"]
    ):
        raise PublicationConflictError("研究通过报告的容量数值关系无效")
    request_count = value.get("requestCount")
    covered_count = value.get("coveredRequestCount")
    observations = value.get("observations")
    if (
        isinstance(request_count, bool)
        or not isinstance(request_count, int)
        or request_count <= 0
        or covered_count != request_count
        or not isinstance(observations, list)
        or len(observations) != request_count
    ):
        raise PublicationConflictError("研究通过报告的容量请求覆盖不完整")
    observed_participation: list[float] = []
    observed_impact: list[float] = []
    for observation in observations:
        if not isinstance(observation, Mapping):
            raise PublicationConflictError("研究通过报告包含无效容量观察")
        if (
            not isinstance(observation.get("executionDate"), str)
            or not isinstance(observation.get("tsCode"), str)
            or isinstance(observation.get("advObservations"), bool)
            or not isinstance(observation.get("advObservations"), int)
            or observation["advObservations"] < policy["minimumAdvObservations"]
            or observation["advObservations"] > policy["advLookbackPeriods"]
        ):
            raise PublicationConflictError("研究通过报告包含无效容量观察身份")
        for field in (
            "requestedChange",
            "advAmount",
            "participationRate",
            "modeledImpactRate",
        ):
            metric = observation.get(field)
            if (
                isinstance(metric, bool)
                or not isinstance(metric, (int, float))
                or not math.isfinite(float(metric))
                or float(metric) < 0
            ):
                raise PublicationConflictError(
                    f"研究通过报告的容量观察字段无效：{field}"
                )
        requested_change = float(observation["requestedChange"])
        adv_amount = float(observation["advAmount"])
        if requested_change <= 0 or adv_amount <= 0:
            raise PublicationConflictError("研究通过报告的容量请求或 ADV 必须大于零")
        expected_participation = (
            float(policy["expectedCapital"]) * requested_change / adv_amount
        )
        expected_impact = (
            float(policy["impactModel"]["coefficient"])
            * expected_participation
        )
        if not _numbers_close(
            observation["participationRate"], expected_participation
        ) or not _numbers_close(
            observation["modeledImpactRate"], expected_impact
        ):
            raise PublicationConflictError(
                "研究通过报告的容量观察未按冻结资金规模与冲击模型闭合"
            )
        observed_participation.append(float(observation["participationRate"]))
        observed_impact.append(float(observation["modeledImpactRate"]))
    maximum_participation = max(observed_participation)
    maximum_impact = max(observed_impact)
    if (
        not _numbers_close(
            numbers["medianAdvParticipationRate"],
            _linear_quantile(observed_participation, 0.5),
        )
        or not _numbers_close(
            numbers["p95AdvParticipationRate"],
            _linear_quantile(observed_participation, 0.95),
        )
        or not _numbers_close(
            numbers["maxAdvParticipationRate"], maximum_participation
        )
        or not _numbers_close(numbers["maxModeledImpactRate"], maximum_impact)
        or value.get("passed") is not True
        or maximum_participation
        > float(policy["maximumAdvParticipationRate"]) + 1e-12
        or maximum_impact > float(policy["maximumModeledImpactRate"]) + 1e-12
    ):
        raise PublicationConflictError(
            "研究通过报告的容量结果未通过冻结阈值或汇总不闭合"
        )


def _validate_research_pass_risk_summary(
    value: Any, *, metrics: Mapping[str, Any]
) -> None:
    expected_observations = metrics.get("observations")
    if (
        not isinstance(value, Mapping)
        or value.get("status") != "complete"
        or isinstance(expected_observations, bool)
        or not isinstance(expected_observations, int)
        or value.get("observations") != expected_observations
        or value.get("riskContributionEndDate") != metrics.get("endDate")
    ):
        raise PublicationConflictError(
            "研究通过报告的风险摘要未完整覆盖冻结 test/OOS"
        )
    for field in (
        "averageGrossExposure",
        "endingGrossExposure",
        "averageNetExposure",
        "endingNetExposure",
        "averageHhi",
        "endingHhi",
        "averagePortfolioVolatility",
        "endingPortfolioVolatility",
    ):
        metric = value.get(field)
        if (
            isinstance(metric, bool)
            or not isinstance(metric, (int, float))
            or not math.isfinite(float(metric))
        ):
            raise PublicationConflictError(
                f"研究通过报告的风险摘要字段无效：{field}"
            )
    for risk_field, metric_field in (
        ("averageGrossExposure", "averageGrossExposure"),
        ("endingGrossExposure", "endingGrossExposure"),
        ("averageNetExposure", "averageNetExposure"),
        ("endingNetExposure", "endingNetExposure"),
        ("averageHhi", "averageHhi"),
        ("endingHhi", "endingHhi"),
    ):
        if not _numbers_close(value[risk_field], float(metrics[metric_field])):
            raise PublicationConflictError(
                "研究通过报告的风险摘要与持仓账本汇总不一致"
            )
    risk_observations = value.get("riskContributionObservations")
    contributions = value.get("endingRiskContributions")
    if (
        isinstance(risk_observations, bool)
        or not isinstance(risk_observations, int)
        or risk_observations <= 0
        or not isinstance(contributions, list)
        or not contributions
    ):
        raise PublicationConflictError("研究通过报告缺少可用总风险贡献")
    contribution_total = 0.0
    for contribution in contributions:
        if (
            not isinstance(contribution, Mapping)
            or not isinstance(contribution.get("tsCode"), str)
            or not contribution["tsCode"].strip()
        ):
            raise PublicationConflictError("研究通过报告包含无效风险贡献身份")
        for field in ("closeWeight", "totalRiskContribution"):
            metric = contribution.get(field)
            if (
                isinstance(metric, bool)
                or not isinstance(metric, (int, float))
                or not math.isfinite(float(metric))
            ):
                raise PublicationConflictError(
                    f"研究通过报告的风险贡献字段无效：{field}"
                )
        contribution_total += float(contribution["totalRiskContribution"])
    if not _numbers_close(
        contribution_total, value["endingPortfolioVolatility"]
    ):
        raise PublicationConflictError("研究通过报告的风险贡献之和与组合波动不闭合")


def _numbers_close(value: Any, expected: float) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and math.isclose(float(value), expected, rel_tol=1e-10, abs_tol=1e-12)
    )


def _linear_quantile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _validate_multiple_testing_metrics(
    metrics: Mapping[str, Any], *, max_trials: int
) -> None:
    dsr = metrics.get("dsr")
    pbo = metrics.get("pbo")
    if not isinstance(dsr, Mapping) or not isinstance(pbo, Mapping):
        raise PublicationConflictError(
            "多次试验的研究通过报告必须冻结结构化 DSR 与 PBO"
        )
    for label, value in (("DSR", dsr), ("PBO", pbo)):
        probability = value.get("probability")
        if isinstance(probability, bool) or not isinstance(
            probability, (int, float)
        ):
            raise PublicationConflictError(f"{label} 概率必须是 canonical 数值")
        if not math.isfinite(float(probability)) or not 0 <= float(probability) <= 1:
            raise PublicationConflictError(f"{label} 概率必须位于 [0,1]")
    trial_count = dsr.get("trialCount")
    if (
        isinstance(trial_count, bool)
        or not isinstance(trial_count, int)
        or trial_count != max_trials
    ):
        raise PublicationConflictError("DSR 试验数与冻结预算不一致")
    observations = dsr.get("observations")
    monthly_observations = pbo.get("monthlyObservations")
    combinations = pbo.get("combinations")
    winner_counts = pbo.get("trainingWinnerCounts")
    if (
        isinstance(observations, bool)
        or not isinstance(observations, int)
        or observations < 2
    ):
        raise PublicationConflictError("DSR 缺少有效观察数")
    if (
        isinstance(monthly_observations, bool)
        or not isinstance(monthly_observations, int)
        or monthly_observations < 2
        or not isinstance(winner_counts, Mapping)
        or len(winner_counts) != max_trials
        or any(
            isinstance(count, bool) or not isinstance(count, int) or count < 0
            for count in winner_counts.values()
        )
    ):
        raise PublicationConflictError("PBO 候选身份与冻结试验预算不一致")
    if (
        isinstance(combinations, bool)
        or not isinstance(combinations, int)
        or combinations < 1
        or sum(winner_counts.values()) != combinations
    ):
        raise PublicationConflictError("PBO 缺少有效组合数")


def _validate_canonical_evidence_ref(
    item: EvidenceDraft,
    runs: Mapping[str, ResearchRun],
) -> None:
    parsed = urlparse(item.uri)
    if parsed.scheme != "artifacts":
        return
    uri_run_id = parsed.netloc
    if (
        item.run_id is None
        or item.run_id not in runs
        or uri_run_id != item.run_id
        or parsed.query
        or parsed.fragment
    ):
        raise PublicationConflictError("canonical 证据 URI 与声明运行不一致")
    relative_text = parsed.path.removeprefix("/")
    relative = Path(relative_text)
    if (
        not relative_text
        or parsed.path != f"/{relative.as_posix()}"
        or relative.is_absolute()
        or ".." in relative.parts
        or "\\" in relative_text
    ):
        raise PublicationConflictError("canonical 证据 URI 工件路径无效")
    if item.sha256 is None:
        raise PublicationConflictError("canonical 证据必须声明 SHA-256")
    expected = _canonical_evidence_sha256(runs[item.run_id], relative)
    if item.sha256 != expected:
        raise PublicationConflictError("证据 SHA-256 与 canonical 工件不一致")


def _canonical_evidence_sha256(run: ResearchRun, relative: Path) -> str:
    root = Path(run.artifact_root).resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise PublicationConflictError("canonical 证据路径越过运行目录")
    try:
        actual = _file_sha256(path)
    except OSError as exc:
        raise PublicationArtifactError(
            f"canonical 证据工件缺失或不可读：{relative.as_posix()}"
        ) from exc
    if relative.as_posix() == "manifest.json":
        return actual
    manifest_path = root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicationArtifactError("canonical 证据缺少有效 manifest.json") from exc
    artifact_hashes = (
        manifest.get("artifactHashes") if isinstance(manifest, dict) else None
    )
    identity = (
        artifact_hashes.get(relative.as_posix())
        if isinstance(artifact_hashes, dict)
        else None
    )
    declared = identity.get("fileSha256") if isinstance(identity, Mapping) else None
    if declared != actual:
        raise PublicationArtifactError(
            f"canonical 证据未被 manifest 正确绑定：{relative.as_posix()}"
        )
    return actual


def _normalize_evidence_list(
    items: tuple[Mapping[str, Any], ...],
) -> list[dict[str, Any]]:
    normalized = []
    for item in items:
        if not isinstance(item, Mapping):
            raise PublicationConflictError("结构化证据必须是 JSON object")
        value = json_safe_value(dict(item))
        if not value or not _contains_meaningful_text(value):
            raise PublicationConflictError(
                "结构化证据必须包含非空文字事实，不能使用空 object"
            )
        normalized.append(value)
    return normalized


def _contains_meaningful_text(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_contains_meaningful_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_meaningful_text(item) for item in value)
    return False


def _evaluation_run_identities(
    runs: list[ResearchRun], *, freeze_audit: bool
) -> list[dict[str, Any]]:
    if not freeze_audit:
        return [
            {
                "runId": item.run_id,
                "status": item.status,
                "codeCommit": item.code_commit,
                "reproducibilityKey": item.reproducibility_key,
                "resultFingerprint": item.result_fingerprint,
            }
            for item in runs
        ]
    return [_frozen_run_audit_identity(item) for item in runs]


def _frozen_run_audit_identity(run: ResearchRun) -> dict[str, Any]:
    identity = {
        "runId": run.run_id,
        "status": run.status,
        "stage": run.stage,
        "error": run.error,
        "startedAt": _iso_or_none(run.started_at),
        "finishedAt": _iso_or_none(run.finished_at),
        "strategyId": run.strategy_id,
        "codeCommit": run.code_commit,
        "configSha256": run.config_sha256,
        "dataSnapshotId": run.data_snapshot_id,
        "environmentSha256": run.environment_sha256,
        "randomSeed": run.random_seed,
        "reproducibilityKey": run.reproducibility_key,
        "resultFingerprint": run.result_fingerprint,
    }
    if run.status != "succeeded":
        return {
            **identity,
            "manifestAudit": "非成功运行；部分工件不作为可信证据",
            "manifestSha256": None,
        }
    run_root = Path(run.artifact_root)
    manifest_path = run_root / "manifest.json"
    try:
        payload = manifest_path.read_bytes()
    except Exception as exc:
        raise PublicationArtifactError(
            f"成功运行 {run.run_id} 的 canonical manifest 无法读取：{exc}"
        ) from exc
    return {
        **identity,
        "manifestAudit": "canonical 归档已校验",
        "manifestSha256": sha256(payload).hexdigest(),
    }


def _verify_existing_evaluation(
    db: Session,
    formal: FormalResearch,
    evaluation: ResearchEvaluation,
    runs: list[ResearchRun],
    normalized: dict[str, Any],
) -> None:
    run_ids = list(
        db.scalars(
            select(ResearchEvaluationRun.run_id)
            .where(ResearchEvaluationRun.evaluation_id == evaluation.id)
            .order_by(ResearchEvaluationRun.run_id)
        ).all()
    )
    refs = [
        {
            "kind": item.kind,
            "uri": item.uri,
            "runId": item.run_id,
            "sha256": item.sha256,
            "metadata": json_safe_value(item.metadata_json or {}),
        }
        for item in db.scalars(
            select(ResearchEvidenceRef)
            .where(ResearchEvidenceRef.evaluation_id == evaluation.id)
            .order_by(ResearchEvidenceRef.kind, ResearchEvidenceRef.uri)
        ).all()
    ]
    actual = {
        "conclusion": evaluation.conclusion,
        "supersedesEvaluationId": evaluation.supersedes_evaluation_id,
        "runIds": run_ids,
        "runIdentities": _evaluation_run_identities(
            runs, freeze_audit=formal.origin == "native"
        ),
        "supportingEvidence": json_safe_value(evaluation.supporting_evidence or []),
        "opposingEvidence": json_safe_value(evaluation.opposing_evidence or []),
        "missingEvidence": json_safe_value(evaluation.missing_evidence or []),
        "limitations": json_safe_value(evaluation.limitations or []),
        "followUpRecommendations": json_safe_value(
            evaluation.follow_up_recommendations or []
        ),
        "evidenceRefs": refs,
    }
    requested = {
        key: normalized[key]
        for key in (
            "conclusion",
            "supersedesEvaluationId",
            "runIds",
            "runIdentities",
            "supportingEvidence",
            "opposingEvidence",
            "missingEvidence",
            "limitations",
            "followUpRecommendations",
            "evidenceRefs",
        )
    }
    if canonical_json_bytes(actual) != canonical_json_bytes(requested):
        raise PublicationConflictError("既有评价内容与待发布请求不一致")
    if formal.origin == "historical_import":
        expected_sha256 = _historical_evaluation_sha256(db, evaluation, runs)
    else:
        expected_sha256 = canonical_sha256(
            {
                "schemaVersion": "research-evaluation/v2",
                "formalResearchId": formal.id,
                **normalized,
            }
        )
    if evaluation.evaluation_sha256 != expected_sha256:
        raise PublicationConflictError("既有评价指纹与冻结内容不一致")


def _historical_evaluation_sha256(
    db: Session,
    evaluation: ResearchEvaluation,
    runs: list[ResearchRun],
) -> str:
    source_summaries = list(
        db.scalars(
            select(ResearchEvidenceRef)
            .where(
                ResearchEvidenceRef.evaluation_id == evaluation.id,
                ResearchEvidenceRef.kind == "statistics",
            )
            .order_by(ResearchEvidenceRef.uri)
        ).all()
    )
    if len(source_summaries) != 1 or not source_summaries[0].sha256:
        raise PublicationConflictError("历史评价缺少唯一的冻结来源摘要指纹")
    return canonical_sha256(
        {
            "formalResearchId": evaluation.formal_research_id,
            "version": evaluation.version,
            "conclusion": evaluation.conclusion,
            "supportingEvidence": json_safe_value(evaluation.supporting_evidence or []),
            "opposingEvidence": json_safe_value(evaluation.opposing_evidence or []),
            "missingEvidence": json_safe_value(evaluation.missing_evidence or []),
            "limitations": json_safe_value(evaluation.limitations or []),
            "followUpRecommendations": json_safe_value(
                evaluation.follow_up_recommendations or []
            ),
            "runIdentities": [
                {
                    "runId": item.run_id,
                    "codeCommit": item.code_commit,
                    "reproducibilityKey": item.reproducibility_key,
                    "resultFingerprint": item.result_fingerprint,
                }
                for item in runs
            ],
            "sourceSummarySha256": source_summaries[0].sha256,
        }
    )


def _assert_stored_evaluation_fingerprint(
    db: Session,
    formal: FormalResearch,
    evaluation: ResearchEvaluation,
    runs: list[ResearchRun],
    evidence: list[ResearchEvidenceRef],
) -> None:
    if formal.origin == "historical_import":
        expected = _historical_evaluation_sha256(db, evaluation, runs)
    else:
        expected = canonical_sha256(
            {
                "schemaVersion": "research-evaluation/v2",
                "formalResearchId": formal.id,
                "conclusion": evaluation.conclusion,
                "supersedesEvaluationId": evaluation.supersedes_evaluation_id,
                "runIds": [item.run_id for item in runs],
                "runIdentities": _evaluation_run_identities(
                    runs, freeze_audit=True
                ),
                "supportingEvidence": json_safe_value(
                    evaluation.supporting_evidence or []
                ),
                "opposingEvidence": json_safe_value(
                    evaluation.opposing_evidence or []
                ),
                "missingEvidence": json_safe_value(
                    evaluation.missing_evidence or []
                ),
                "limitations": json_safe_value(evaluation.limitations or []),
                "followUpRecommendations": json_safe_value(
                    evaluation.follow_up_recommendations or []
                ),
                "evidenceRefs": [
                    {
                        "kind": item.kind,
                        "uri": item.uri,
                        "runId": item.run_id,
                        "sha256": item.sha256,
                        "metadata": json_safe_value(item.metadata_json or {}),
                    }
                    for item in evidence
                ],
            }
        )
    if evaluation.evaluation_sha256 != expected:
        raise PublicationConflictError(
            "评价指纹与已冻结的运行审计或证据不一致"
        )


def _normalize_pending_publication(
    publication: ResearchPublication,
    evaluation: ResearchEvaluation,
    *,
    issue_number: int,
) -> None:
    manifest_url = _artifact_url(evaluation.id, "manifest.json")
    if (
        publication.artifact_manifest_uri == manifest_url
        and publication.issue_number == issue_number
    ):
        return
    publication.artifact_manifest_uri = manifest_url
    publication.issue_number = issue_number
    publication.publication_sha256 = canonical_sha256(
        {
            "schemaVersion": "research-publication/v1",
            "publicationId": publication.id,
            "formalResearchId": publication.formal_research_id,
            "evaluationId": evaluation.id,
            "evaluationSha256": evaluation.evaluation_sha256,
            "version": publication.version,
            "supersedesPublicationId": publication.supersedes_publication_id,
            "manifestUrl": manifest_url,
            "issueNumber": issue_number,
        }
    )


def _publication_issue_number(
    db: Session,
    formal: FormalResearch,
    plan: FrozenResearchPlan,
) -> int:
    if formal.origin != "historical_import":
        return plan.issue_number
    mapping = db.get(ResearchPublicationIssueMapping, formal.id)
    if mapping is None:
        raise PublicationConflictError(
            "历史导入评价尚未一对一映射独立的策略研究 Issue"
        )
    try:
        return resolve_historical_publication_issue(
            plan.strategy_id, mapping.issue_number
        ).issue_number
    except ValueError as exc:
        raise PublicationConflictError(f"历史研究 Issue 冻结映射无效：{exc}") from exc


def _validate_supersession(
    db: Session,
    latest: ResearchEvaluation | None,
    requested_id: str | None,
) -> None:
    if latest is None:
        if requested_id is not None:
            raise PublicationConflictError("首版评价不能声明替代版本")
        return
    if requested_id != latest.id:
        raise PublicationConflictError("新评价必须显式替代当前最新评价")
    latest_published = db.scalar(
        select(ResearchPublication)
        .where(
            ResearchPublication.evaluation_id == latest.id,
            ResearchPublication.status == "published",
        )
        .order_by(ResearchPublication.version.desc())
        .limit(1)
    )
    if (
        latest_published is None
        or not _publication_finalized_event_exists(db, latest_published)
    ):
        raise PublicationConflictError("上一评价尚未发布，只能重试原评价，不能改写结论")


def _enter_publishing_state(db: Session, formal: FormalResearch) -> None:
    orchestration = db.scalar(
        select(ResearchOrchestration).where(
            ResearchOrchestration.formal_research_id == formal.id
        )
    )
    if orchestration is None or orchestration.state == "publishing":
        return
    if orchestration.state not in {"running", "blocked", "stopped", "published"}:
        raise PublicationConflictError(
            f"研究编排状态 {orchestration.state} 不能进入发布"
        )
    transition_orchestration(orchestration, "publishing", reason="结构化评价一致发布中")


def _ensure_publication_bundle(
    session_factory: SessionFactory,
    publication_id: str,
    artifact_root: Path,
) -> dict[str, Any]:
    with session_factory() as db:
        summary = _build_summary(db, publication_id)
    expected = _publication_bundle_payloads(summary)
    target = _publication_directory(artifact_root, summary["evaluation"]["id"])
    if target.exists():
        _verify_existing_bundle(target, expected)
        return summary

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".evaluation-", dir=target.parent))
    try:
        for filename, payload in expected.items():
            atomic_write_bytes(temporary / filename, payload)
        try:
            os.replace(temporary, target)
        except OSError:
            if not target.exists():
                raise
            _verify_existing_bundle(target, expected)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return summary


def _publication_bundle_payloads(summary: dict[str, Any]) -> dict[str, bytes]:
    summary_bytes = canonical_json_bytes(summary) + b"\n"
    report_bytes = _render_report(summary).encode("utf-8")
    manifest = {
        "schemaVersion": "research-publication-manifest/v1",
        "formalResearchId": summary["formalResearchId"],
        "evaluationId": summary["evaluation"]["id"],
        "evaluationSha256": summary["evaluation"]["sha256"],
        "artifacts": [
            _artifact_identity("summary.json", "application/json", summary_bytes),
            _artifact_identity("report.html", "text/html; charset=utf-8", report_bytes),
        ],
        "runArtifacts": summary["runs"],
    }
    manifest_bytes = canonical_json_bytes(manifest) + b"\n"
    return {
        "summary.json": summary_bytes,
        "report.html": report_bytes,
        "manifest.json": manifest_bytes,
    }


def _build_summary(db: Session, publication_id: str) -> dict[str, Any]:
    publication = db.get(ResearchPublication, publication_id)
    if publication is None:
        raise PublicationConflictError("发布记录不存在")
    evaluation = db.get(ResearchEvaluation, publication.evaluation_id)
    formal = db.get(FormalResearch, publication.formal_research_id)
    if evaluation is None or formal is None:
        raise PublicationConflictError("发布记录缺少评价或正式研究")
    plan = db.get(FrozenResearchPlan, formal.plan_id)
    if plan is None:
        raise PublicationConflictError("正式研究缺少冻结计划")
    run_ids = list(
        db.scalars(
            select(ResearchEvaluationRun.run_id)
            .where(ResearchEvaluationRun.evaluation_id == evaluation.id)
            .order_by(ResearchEvaluationRun.run_id)
        ).all()
    )
    runs = (
        list(
            db.scalars(
                select(ResearchRun)
                .where(ResearchRun.run_id.in_(run_ids))
                .order_by(ResearchRun.run_id)
            ).all()
        )
        if run_ids
        else []
    )
    evidence = list(
        db.scalars(
            select(ResearchEvidenceRef)
            .where(ResearchEvidenceRef.evaluation_id == evaluation.id)
            .order_by(ResearchEvidenceRef.kind, ResearchEvidenceRef.uri)
        ).all()
    )
    _assert_stored_evaluation_fingerprint(
        db, formal, evaluation, runs, evidence
    )
    strategy = _frozen_strategy_contract(
        plan,
        evidence=evidence,
        historical=formal.origin == "historical_import",
    )
    return {
        "schemaVersion": "research-publication-summary/v1",
        "formalResearchId": formal.id,
        "issueNumber": publication.issue_number,
        "strategy": {
            "id": strategy["id"],
            "displayName": strategy["displayName"],
            "version": strategy["version"],
            "planStatus": "已冻结",
            "economicThesis": strategy["economicThesis"],
        },
        "researchPlan": {
            "id": plan.id,
            "schemaVersion": plan.schema_version,
            "sha256": plan.plan_sha256,
            "codeCommit": plan.code_commit,
            "gates": json_safe_value(
                plan.plan_json.get("gates", [])
                if isinstance(plan.plan_json, dict)
                else []
            ),
            "contract": json_safe_value(plan.plan_json or {}),
        },
        "evaluation": {
            "id": evaluation.id,
            "version": evaluation.version,
            "conclusion": evaluation.conclusion,
            "sha256": evaluation.evaluation_sha256,
            "supersedesEvaluationId": evaluation.supersedes_evaluation_id,
            "supportingEvidence": json_safe_value(evaluation.supporting_evidence or []),
            "opposingEvidence": json_safe_value(evaluation.opposing_evidence or []),
            "missingEvidence": json_safe_value(evaluation.missing_evidence or []),
            "limitations": json_safe_value(evaluation.limitations or []),
            "followUpRecommendations": json_safe_value(
                evaluation.follow_up_recommendations or []
            ),
        },
        "runs": [
            _canonical_run_identity(
                item, historical=formal.origin == "historical_import"
            )
            for item in runs
        ],
        "evidenceRefs": [
            {
                "kind": item.kind,
                "uri": item.uri,
                "runId": item.run_id,
                "sha256": item.sha256,
                "metadata": json_safe_value(item.metadata_json or {}),
            }
            for item in evidence
        ],
        "followUpProposalUrl": f"/api/research/formal-researches/{formal.id}",
        "urls": {
            "manifest": _artifact_url(evaluation.id, "manifest.json"),
            "summary": _artifact_url(evaluation.id, "summary.json"),
            "report": _report_url(evaluation.id),
            "rawReport": _artifact_url(evaluation.id, "report.html"),
        },
    }


def _frozen_strategy_contract(
    plan: FrozenResearchPlan,
    *,
    evidence: list[ResearchEvidenceRef] | None = None,
    historical: bool = False,
) -> dict[str, str]:
    if historical:
        return _historical_strategy_contract(plan, evidence or [])
    contract = plan.plan_json if isinstance(plan.plan_json, dict) else {}
    nested = contract.get("strategy")
    nested = nested if isinstance(nested, Mapping) else {}
    strategy_id = nested.get("id") or contract.get("strategyId")
    display_name = nested.get("displayName") or contract.get("displayName")
    version = (
        nested.get("version")
        or contract.get("strategyVersion")
        or contract.get("registryVersion")
    )
    economic_thesis = (
        contract.get("economicHypothesis") or contract.get("economicThesis")
    )
    values = (strategy_id, display_name, version, economic_thesis)
    if (
        strategy_id != plan.strategy_id
        or any(not isinstance(value, str) or not value.strip() for value in values)
    ):
        raise PublicationConflictError(
            "冻结研究计划缺少策略编号、中文名称、版本或经济假设"
        )
    return {
        "id": str(strategy_id),
        "displayName": str(display_name),
        "version": str(version),
        "economicThesis": str(economic_thesis),
    }


def _historical_strategy_contract(
    plan: FrozenResearchPlan,
    evidence: list[ResearchEvidenceRef],
) -> dict[str, str]:
    contract = plan.plan_json if isinstance(plan.plan_json, dict) else {}
    nested = contract.get("strategy")
    nested = nested if isinstance(nested, Mapping) else {}
    frozen_values = {
        "id": nested.get("id") or contract.get("strategyId"),
        "displayName": nested.get("displayName") or contract.get("displayName"),
        "version": nested.get("version") or contract.get("strategyVersion"),
        "economicThesis": contract.get("economicHypothesis")
        or contract.get("economicThesis"),
    }
    if frozen_values["id"] == plan.strategy_id and all(
        isinstance(value, str) and value.strip()
        for value in frozen_values.values()
    ):
        return {key: str(value) for key, value in frozen_values.items()}
    source_uris = contract.get("sourceUris")
    summary_uri = (
        source_uris.get("summary") if isinstance(source_uris, Mapping) else None
    )
    source_ref = next(
        (
            item
            for item in evidence
            if item.kind == "statistics"
            and item.uri == summary_uri
            and item.sha256
        ),
        None,
    )
    if source_ref is None:
        raise PublicationConflictError("历史导入策略缺少冻结来源摘要")
    summary = _read_repo_json_evidence(source_ref)
    strategy_id = plan.strategy_id
    profile = summary.get("strategyProfile")
    profile = profile if isinstance(profile, Mapping) else {}
    if strategy_id == "etf_low_volatility_gate":
        profile = summary.get("lowVolatilityGateFollowup")
        profile = profile if isinstance(profile, Mapping) else {}
        display_name = profile.get("strategyName")
        economic_thesis = profile.get("rule")
        version = "1"
    else:
        display_name = profile.get("name") or summary.get("title")
        economic_thesis = (
            profile.get("economicHypothesis") or profile.get("rule")
        )
        version = profile.get("strategyVersion") or "1"
    values = (display_name, economic_thesis, version)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise PublicationConflictError(
            "历史导入的冻结来源摘要缺少中文名称、版本或经济假设"
        )
    return {
        "id": strategy_id,
        "displayName": str(display_name),
        "version": str(version),
        "economicThesis": str(economic_thesis),
    }


def _read_repo_json_evidence(item: ResearchEvidenceRef) -> dict[str, Any]:
    parsed = urlparse(item.uri)
    if parsed.scheme != "repo" or not parsed.netloc or not item.sha256:
        raise PublicationConflictError("历史来源摘要 URI 或 SHA-256 无效")
    relative = Path(parsed.netloc, parsed.path.removeprefix("/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise PublicationConflictError("历史来源摘要路径无效")
    root = Path(__file__).resolve().parents[2]
    path = (root / relative).resolve()
    if root not in path.parents:
        raise PublicationConflictError("历史来源摘要越过仓库边界")
    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicationArtifactError("历史来源摘要无法读取") from exc
    if sha256(payload).hexdigest() != item.sha256 or not isinstance(value, dict):
        raise PublicationArtifactError("历史来源摘要与冻结指纹不一致")
    return json_safe_value(value)


def _artifact_identity(
    filename: str, media_type: str, payload: bytes
) -> dict[str, Any]:
    return {
        "filename": filename,
        "mediaType": media_type,
        "sizeBytes": len(payload),
        "sha256": sha256(payload).hexdigest(),
    }


def _canonical_run_identity(
    run: ResearchRun, *, historical: bool = False
) -> dict[str, Any]:
    run_root = Path(run.artifact_root)
    manifest_path = run_root / "manifest.json"
    manifest: dict[str, Any] = {}
    manifest_sha256 = None
    artifact_hashes: dict[str, Any] = {}
    if run.status == "succeeded" and not historical:
        if not manifest_path.is_file():
            raise PublicationArtifactError(
                f"成功运行 {run.run_id} 缺少 canonical manifest.json"
            )
        try:
            payload = manifest_path.read_bytes()
            manifest, _config = validate_research_archive(run_root)
        except Exception as exc:
            raise PublicationArtifactError(
                f"运行 {run.run_id} 的 canonical 归档校验失败：{exc}"
            ) from exc
        manifest_sha256 = sha256(payload).hexdigest()
        required_identity = {
            "runId": run.run_id,
            "strategyId": run.strategy_id,
            "codeCommit": run.code_commit,
            "reproducibilityKey": run.reproducibility_key,
            "configSha256": run.config_sha256,
            "randomSeed": run.random_seed,
        }
        identity_mismatch = any(
            manifest.get(key) != expected
            for key, expected in required_identity.items()
        )
        if identity_mismatch or manifest.get("runId") != run.run_id:
            raise PublicationArtifactError(
                f"运行 {run.run_id} 的 canonical manifest 与数据库运行身份不一致"
            )
        environment = manifest.get("environment")
        snapshot = manifest.get("dataSnapshot")
        if (
            manifest.get("resultFingerprint") != run.result_fingerprint
            or not isinstance(environment, Mapping)
            or environment.get("sha256") != run.environment_sha256
            or not isinstance(snapshot, Mapping)
            or snapshot.get("snapshotId") != run.data_snapshot_id
        ):
            raise PublicationArtifactError(
                f"运行 {run.run_id} 的 canonical manifest 与数据库指纹不一致"
            )
        artifact_hashes = manifest.get("artifactHashes")
        if not isinstance(artifact_hashes, dict) or not artifact_hashes:
            raise PublicationArtifactError(
                f"运行 {run.run_id} 的 canonical manifest 缺少 artifactHashes"
            )
        try:
            fingerprint = build_result_fingerprint(artifact_hashes)
        except (KeyError, TypeError, ValueError) as exc:
            raise PublicationArtifactError(
                f"运行 {run.run_id} 的 canonical artifactHashes 无效"
            ) from exc
        if fingerprint != run.result_fingerprint:
            raise PublicationArtifactError(
                f"运行 {run.run_id} 的 canonical artifactHashes 与结果指纹不一致"
            )
        _verify_run_artifact_hashes(run_root, artifact_hashes)
    canonical_metrics: dict[str, Any] = {}
    canonical_oos_metrics: dict[str, Any] = {}
    chart_series: dict[str, list[dict[str, Any]]] = {}
    walk_forward_evidence: dict[str, Any] = {}
    manifest_contract: dict[str, Any] = {}
    if manifest:
        manifest_contract = {
            key: json_safe_value(manifest.get(key))
            for key in (
                "generatedAt",
                "config",
                "qualityRun",
                "dataSnapshot",
                "universe",
                "environment",
                "limitations",
                "boundaries",
            )
            if key in manifest
        }
    if run.status == "succeeded" and not historical:
        canonical_metrics = _read_canonical_json(run_root / "metrics.json", "metrics.json")
        if (run_root / "oos_metrics.json").is_file():
            canonical_oos_metrics = _read_canonical_json(
                run_root / "oos_metrics.json", "oos_metrics.json"
            )
        chart_start = (
            canonical_oos_metrics.get("sampleStartDate")
            if canonical_oos_metrics.get("status") == "complete"
            else None
        )
        chart_end = (
            canonical_oos_metrics.get("sampleEndDate")
            if canonical_oos_metrics.get("status") == "complete"
            else None
        )
        chart_series = _read_canonical_nav_series(
            run_root / "nav.csv.gz",
            start_date=chart_start,
            end_date=chart_end,
        )
        chart_series["benchmarkNav"] = _read_canonical_benchmark_series(
            run_root,
            manifest,
            start_date=chart_start,
            end_date=chart_end,
        )
        if (run_root / "walk_forward_windows.csv.gz").is_file() and (
            run_root / "walk_forward_metrics.csv.gz"
        ).is_file():
            walk_forward_evidence = {
                "summary": canonical_oos_metrics.get("walkForward"),
                "windows": _read_canonical_csv_records(
                    run_root / "walk_forward_windows.csv.gz",
                    "walk_forward_windows.csv.gz",
                ),
                "metrics": _read_canonical_csv_records(
                    run_root / "walk_forward_metrics.csv.gz",
                    "walk_forward_metrics.csv.gz",
                ),
            }
    audit_identity = (
        _frozen_run_audit_identity(run)
        if not historical
        else {
            "runId": run.run_id,
            "status": run.status,
            "stage": run.stage,
            "error": run.error,
            "startedAt": _iso_or_none(run.started_at),
            "finishedAt": _iso_or_none(run.finished_at),
            "strategyId": run.strategy_id,
            "codeCommit": run.code_commit,
            "configSha256": run.config_sha256,
            "dataSnapshotId": run.data_snapshot_id,
            "environmentSha256": run.environment_sha256,
            "randomSeed": run.random_seed,
            "reproducibilityKey": run.reproducibility_key,
            "resultFingerprint": run.result_fingerprint,
            "manifestAudit": "历史导入运行；以冻结来源证据为准",
            "manifestSha256": None,
        }
    )
    if manifest_sha256 != audit_identity["manifestSha256"]:
        raise PublicationArtifactError("运行 manifest 指纹在评价冻结后发生变化")
    return {
        **audit_identity,
        "artifactHashes": json_safe_value(artifact_hashes),
        "manifestContract": manifest_contract,
        "metrics": canonical_metrics,
        "oosMetrics": canonical_oos_metrics,
        "walkForwardEvidence": walk_forward_evidence,
        "chartSeries": chart_series,
    }


def _read_canonical_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicationArtifactError(f"canonical {label} 无法读取") from exc
    if not isinstance(payload, dict):
        raise PublicationArtifactError(f"canonical {label} 不是 JSON object")
    return json_safe_value(payload)


def _read_canonical_csv_records(path: Path, label: str) -> list[dict[str, str]]:
    try:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise PublicationArtifactError(f"canonical {label} 无法读取") from exc
    if not rows or not rows[0]:
        raise PublicationArtifactError(f"canonical {label} 不能为空")
    return [{str(key): str(value) for key, value in row.items()} for row in rows]


def _read_canonical_nav_series(
    path: Path,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    try:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise PublicationArtifactError("canonical nav.csv.gz 无法读取") from exc
    if not rows or any(not row.get("trade_date") or not row.get("nav") for row in rows):
        raise PublicationArtifactError("canonical nav.csv.gz 缺少日期或净值")
    parsed: list[dict[str, Any]] = []
    # canonical NAV 以显式初始净值 1.0 为基准；不能用首个收盘值重置，
    # 否则会漏掉首个执行日的收益、费用与回撤。
    peak = 1.0
    cumulative_turnover = 0.0
    cumulative_cost = 0.0
    for row in rows:
        try:
            nav = float(row["nav"])
            turnover = float(row.get("one_way_turnover") or 0)
            cost = float(row.get("transaction_cost_rate") or 0)
            gross = float(row.get("gross_exposure") or 0)
            cash = float(row.get("cash_weight") or 0)
        except (TypeError, ValueError) as exc:
            raise PublicationArtifactError("canonical NAV 图表字段不是有限数值") from exc
        values = (nav, turnover, cost, gross, cash)
        if any(not _finite_number(value) for value in values) or nav <= 0:
            raise PublicationArtifactError("canonical NAV 图表字段不是有限正当数值")
        peak = max(peak, nav)
        cumulative_turnover += turnover
        cumulative_cost += cost
        parsed.append(
            {
                "date": str(row["trade_date"]),
                "nav": nav,
                "drawdown": nav / peak - 1,
                "cumulativeTurnover": cumulative_turnover,
                "cumulativeCost": cumulative_cost,
                "grossExposure": gross,
                "cashWeight": cash,
            }
        )
    if start_date is not None or end_date is not None:
        if not isinstance(start_date, str) or not isinstance(end_date, str):
            raise PublicationArtifactError("canonical OOS 图表边界无效")
        prior = [row for row in parsed if row["date"] < start_date]
        selected = [
            row for row in parsed if start_date <= row["date"] <= end_date
        ]
        if not prior or not selected:
            raise PublicationArtifactError("canonical OOS 图表缺少边界前净值或区间数据")
        base = prior[-1]
        peak = 1.0
        rebased: list[dict[str, Any]] = []
        for row in selected:
            nav = row["nav"] / base["nav"]
            peak = max(peak, nav)
            rebased.append(
                {
                    **row,
                    "nav": nav,
                    "drawdown": nav / peak - 1.0,
                    "cumulativeTurnover": row["cumulativeTurnover"]
                    - base["cumulativeTurnover"],
                    "cumulativeCost": row["cumulativeCost"]
                    - base["cumulativeCost"],
                }
            )
        parsed = rebased
    sampled = _downsample_points(parsed, 240)
    fields = (
        "nav",
        "drawdown",
        "cumulativeTurnover",
        "cumulativeCost",
        "grossExposure",
        "cashWeight",
    )
    return {
        field: [{"date": row["date"], "value": row[field]} for row in sampled]
        for field in fields
    }


def _read_canonical_benchmark_series(
    run_root: Path,
    manifest: Mapping[str, Any],
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    artifact_hashes = manifest.get("artifactHashes")
    if (
        not isinstance(artifact_hashes, Mapping)
        or "benchmark_nav.csv.gz" not in artifact_hashes
    ):
        return []
    path = run_root / "benchmark_nav.csv.gz"
    try:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeDecodeError, csv.Error):
        return []
    rows = sorted(rows, key=lambda row: str(row.get("trade_date") or ""))
    if not rows:
        return []
    try:
        points = [
            {
                "date": str(row["trade_date"]),
                "value": float(row["nav"]),
            }
            for row in rows
        ]
    except (KeyError, TypeError, ValueError):
        return []
    if any(
        not _finite_number(float(item["value"])) or float(item["value"]) <= 0
        for item in points
    ):
        return []
    if start_date is not None or end_date is not None:
        if not isinstance(start_date, str) or not isinstance(end_date, str):
            return []
        prior = [item for item in points if item["date"] < start_date]
        selected = [
            item for item in points if start_date <= item["date"] <= end_date
        ]
        if not prior or not selected:
            return []
        initial_nav = float(prior[-1]["value"])
        points = [
            {"date": item["date"], "value": float(item["value"]) / initial_nav}
            for item in selected
        ]
    return _downsample_points(points, 240)


def _downsample_points(
    rows: list[dict[str, Any]], maximum: int
) -> list[dict[str, Any]]:
    if len(rows) <= maximum:
        return rows
    indices = sorted(
        {round(index * (len(rows) - 1) / (maximum - 1)) for index in range(maximum)}
    )
    return [rows[index] for index in indices]


def _finite_number(value: float) -> bool:
    return value == value and value not in {float("inf"), float("-inf")}


def _iso_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _verify_run_artifact_hashes(
    artifact_root: Path,
    artifact_hashes: Mapping[str, Any],
) -> None:
    root = artifact_root.resolve()
    for filename, identity in sorted(artifact_hashes.items()):
        relative = Path(filename)
        if (
            not filename
            or relative.is_absolute()
            or ".." in relative.parts
            or not isinstance(identity, Mapping)
        ):
            raise PublicationArtifactError(
                "canonical artifactHashes 含无效工件路径或身份"
            )
        expected = identity.get("fileSha256")
        if (
            not isinstance(expected, str)
            or len(expected) != 64
            or any(char not in "0123456789abcdef" for char in expected)
        ):
            raise PublicationArtifactError(f"canonical 工件 {filename} 缺少 fileSha256")
        path = (root / relative).resolve()
        if root not in path.parents:
            raise PublicationArtifactError("canonical 工件路径越过运行目录")
        try:
            actual = _file_sha256(path)
        except OSError as exc:
            raise PublicationArtifactError(
                f"canonical 工件缺失或不可读：{filename}"
            ) from exc
        if actual != expected:
            raise PublicationArtifactError(f"canonical 工件 SHA-256 不匹配：{filename}")


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_existing_bundle(target: Path, expected: dict[str, bytes]) -> None:
    for filename, payload in expected.items():
        path = target / filename
        try:
            actual = path.read_bytes()
        except OSError as exc:
            raise PublicationArtifactError(f"发布工件缺失或不可读：{filename}") from exc
        if actual != payload:
            raise PublicationArtifactError(f"发布工件内容不匹配，拒绝覆盖：{filename}")


def _render_report(summary: dict[str, Any]) -> str:
    evaluation = summary["evaluation"]
    strategy = summary["strategy"]
    plan = summary["researchPlan"]
    run_rows = (
        "".join(
            "<tr>"
            f"<td>{escape(item['runId'])}</td>"
            f"<td>{escape(item['status'])}</td>"
            f"<td>{escape(item.get('stage') or 'not_available')}</td>"
            f"<td>{escape(item.get('startedAt') or 'not_available')}</td>"
            f"<td>{escape(item.get('finishedAt') or 'not_available')}</td>"
            f"<td>{escape(item.get('error') or '无')}</td>"
            f"<td><code>{escape(item['resultFingerprint'] or '无')}</code></td>"
            "</tr>"
            for item in summary["runs"]
        )
        or '<tr><td colspan="7">本评价未关联研究运行；原因见尚缺证据或限制项。</td></tr>'
    )
    strongest_support = _first_evidence_text(evaluation["supportingEvidence"])
    strongest_opposition = _first_evidence_text(evaluation["opposingEvidence"])
    plan_contract = plan["contract"]
    run_config = (
        plan_contract.get("runConfig", {})
        if isinstance(plan_contract, dict)
        else {}
    )
    if not run_config and summary["runs"]:
        manifest = summary["runs"][0].get("manifestContract") or {}
        run_config = manifest.get("config") or {}
    data_rows = "".join(_run_data_evidence_html(item) for item in summary["runs"])
    execution_rows = "".join(_run_execution_html(item) for item in summary["runs"])
    metric_sections = "".join(_run_metrics_html(item) for item in summary["runs"])
    environment_sections = "".join(
        _run_environment_html(item) for item in summary["runs"]
    )
    robustness_sections = "".join(
        _run_robustness_html(item, plan_contract) for item in summary["runs"]
    )
    risk_sections = "".join(_run_risk_html(item) for item in summary["runs"])
    reproducibility_sections = "".join(
        _run_reproducibility_html(item) for item in summary["runs"]
    )
    chart_sections = "".join(_run_chart_html(item) for item in summary["runs"])
    gate_rows = _gate_table(evaluation["supportingEvidence"], plan["gates"])
    evidence_sections = "".join(
        f"<h3>{escape(title)}</h3>{_evidence_html(evaluation[key])}"
        for title, key in (
            ("支持证据", "supportingEvidence"),
            ("反对证据", "opposingEvidence"),
            ("尚缺证据", "missingEvidence"),
        )
    )
    return (
        '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(strategy['displayName'])} v{escape(str(strategy['version']))} "
        f"研究评价｜{escape(evaluation['conclusion'])}</title>"
        "<style>body{margin:0;background:#0b1118;color:#dbe7f3;font:15px/1.6 system-ui,sans-serif}"
        "main{max-width:1180px;margin:auto;padding:32px}h1,h2{letter-spacing:.03em}"
        "h2{margin-top:0}.status{border:1px solid #e8b15a;background:#251d0f;padding:18px}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}"
        ".card,figure{border:1px solid #2a3948;background:#101923;padding:14px;margin:10px 0}"
        "section{border-top:1px solid #2a3948;padding:24px 0}table{width:100%;border-collapse:collapse}"
        "th,td{border:1px solid #2a3948;padding:8px;text-align:left;vertical-align:top}"
        "th{background:#14202b}code,pre{overflow-wrap:anywhere;white-space:pre-wrap}"
        "pre{max-height:420px;overflow:auto;background:#080d13;padding:12px}"
        ".muted{color:#9fb0c1}.warning{color:#ffd18a}.error{color:#ff9b9b}"
        "svg{width:100%;height:auto;background:#080d13}.axis{stroke:#405264;stroke-width:1}"
        ".line{fill:none;stroke:#62b0f5;stroke-width:2}.benchmark{fill:none;stroke:#e8b15a;stroke-width:2;stroke-dasharray:6 4}.drawdown{stroke:#e47777}"
        ".cost{stroke:#e8b15a}.exposure{stroke:#66c49a}</style>"
        "</head><body><main>"
        f"<h1>{escape(strategy['displayName'])} v{escape(str(strategy['version']))} 研究评价</h1>"
        f'<p class="muted">策略编号 <code>{escape(strategy["id"])}</code>；'
        f"评价版本 {evaluation['version']}；仅用于离线量化研究。</p>"
        '<section><h2>0. 结论卡</h2><div class="status">'
        f"<p><strong>强制状态：{escape(evaluation['conclusion'])}</strong></p>"
        f"<p>最强支持证据：{escape(strongest_support)}</p>"
        f"<p>最大反对证据：{escape(strongest_opposition)}</p>"
        f"<p>评价指纹：<code>{escape(evaluation['sha256'])}</code></p>"
        "<p>边界：本结论不构成实盘指令、买卖评级或收益承诺。</p></div></section>"
        '<section><h2>1. 策略画像</h2><div class="grid">'
        f'<div class="card"><h3>身份与经济假设</h3><p>{escape(strategy["economicThesis"])}</p>'
        f'<p>计划状态：{escape(strategy["planStatus"])}</p></div>'
        f'<div class="card"><h3>冻结计划</h3><p>计划指纹：<code>{escape(plan["sha256"])}</code></p>'
        f'<p>代码提交：<code>{escape(plan["codeCommit"])}</code></p></div></div>'
        f"<h3>目标、基准、宇宙、信号、组合、风控与失效条件</h3>{_json_html(plan_contract)}"
        "</section>"
        f'<section><h2>2. 数据与时点证据</h2>{data_rows or _not_available("无运行数据证据")}</section>'
        f'<section><h2>3. 执行、成本与容量</h2>{execution_rows or _not_available("无执行账本")}'
        f"<h3>冻结运行配置</h3>{_json_html(run_config)}</section>"
        f'<section><h2>4. 样本外总体指标</h2>{metric_sections or _not_available("无 canonical 指标")}'
        f"{chart_sections}</section>"
        f'<section><h2>5. 市场环境矩阵</h2>{environment_sections or _not_available("未冻结市场环境拆分")}</section>'
        f'<section><h2>6. 稳健性与过拟合</h2>{robustness_sections or _not_available("未冻结稳健性证据")}</section>'
        f'<section><h2>7. 风险与容量</h2>{risk_sections or _not_available("未冻结风险与容量指标")}</section>'
        f'<section><h2>8. 支持、反对与尚缺证据</h2>{gate_rows}{evidence_sections}</section>'
        f'<section><h2>9. 限制、下一步与停止条件</h2><h3>限制项</h3>{_evidence_html(evaluation["limitations"])}'
        f'<h3>后续研究建议</h3>{_evidence_html(evaluation["followUpRecommendations"])}</section>'
        '<section><h2>10. 复现身份与失败审计</h2><table><thead><tr>'
        "<th>运行 ID</th><th>状态</th><th>阶段</th><th>开始</th><th>结束</th>"
        f"<th>失败原因</th><th>结果指纹</th></tr></thead><tbody>{run_rows}</tbody></table>"
        f"<h3>完整复现身份</h3>{reproducibility_sections or _not_available('无运行复现身份')}"
        f"<h3>发布工件 URL</h3>{_json_html(summary['urls'])}</section>"
        "</main></body></html>\n"
    )


def _first_evidence_text(items: list[dict[str, Any]]) -> str:
    return _evidence_text(items[0]) if items else "无；见尚缺证据或限制项。"


def _json_html(value: Any) -> str:
    return "<pre>" + escape(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    ) + "</pre>"


def _not_available(reason: str) -> str:
    return f'<p class="warning">not_available：{escape(reason)}</p>'


def _run_data_evidence_html(run: Mapping[str, Any]) -> str:
    manifest = run.get("manifestContract") or {}
    if not manifest:
        return _not_available(f"运行 {run['runId']} 没有可读 canonical manifest")
    contract = {
        "runId": run["runId"],
        "status": run["status"],
        "dataSnapshot": manifest.get("dataSnapshot", "not_available"),
        "qualityRun": manifest.get("qualityRun", "not_available"),
        "universe": manifest.get("universe", "not_available"),
        "limitations": manifest.get("limitations", []),
    }
    return f"<h3>运行 {escape(run['runId'])}</h3>{_json_html(contract)}"


def _run_execution_html(run: Mapping[str, Any]) -> str:
    manifest = run.get("manifestContract") or {}
    config = manifest.get("config") if isinstance(manifest, Mapping) else None
    metrics = run.get("oosMetrics") or run.get("metrics") or {}
    contract = {
        "runId": run["runId"],
        "executionPolicy": (
            config.get("executionPolicy", "not_available")
            if isinstance(config, Mapping)
            else "not_available"
        ),
        "costModel": (
            config.get("costModel", "not_available")
            if isinstance(config, Mapping)
            else "not_available"
        ),
        "executionFacts": _select_mapping(
            metrics,
            (
                "warmupStartDate",
                "sampleStartDate",
                "sampleEndDate",
                "observations",
                "openTradingDays",
                "rebalanceCount",
                "requestCount",
                "executionCount",
                "blockedCount",
                "independentTradeCount",
                "averageOneWayTurnover",
                "maxOneWayTurnover",
                "cumulativeTransactionCostRate",
                "blockedRequestRate",
                "partialRequestRate",
                "cumulativeBlockedChange",
                "averageGrossExposure",
                "endingGrossExposure",
                "averageNetExposure",
                "endingNetExposure",
            ),
        ),
    }
    return _json_html(contract)


def _run_metrics_html(run: Mapping[str, Any]) -> str:
    metrics = run.get("oosMetrics") or {}
    if not metrics:
        return _not_available(f"运行 {run['runId']} 无 canonical oos_metrics.json")
    return (
        f"<h3>运行 {escape(run['runId'])} 的冻结 test/OOS 指标</h3>"
        + _mapping_table(metrics)
    )


def _run_environment_html(run: Mapping[str, Any]) -> str:
    metrics = run.get("oosMetrics") or {}
    selected = {
        key: value
        for key, value in metrics.items()
        if any(
            token in key.lower()
            for token in ("regime", "year", "stress", "direction", "volatility")
        )
    }
    if not selected:
        return _not_available(
            f"运行 {run['runId']} 未在 canonical OOS 指标中冻结方向、波动率、逐年或压力期矩阵"
        )
    return f"<h3>运行 {escape(run['runId'])}</h3>{_json_html(selected)}"


def _run_robustness_html(
    run: Mapping[str, Any], plan_contract: Mapping[str, Any]
) -> str:
    metrics = run.get("oosMetrics") or {}
    trial_budget = plan_contract.get("trialBudget", {})
    max_trials = trial_budget.get("maxTrials") if isinstance(trial_budget, Mapping) else None
    overfitting = _select_mapping(
        metrics,
        ("walkForward", "parameterNeighborhood", "costStress", "dsr", "pbo"),
    )
    if max_trials == 1:
        overfitting.setdefault(
            "dsr",
            "not_applicable：冻结计划仅允许 1 次试验，没有候选冠军筛选。",
        )
        overfitting.setdefault(
            "pbo",
            "not_applicable：冻结计划仅允许 1 次试验，没有候选冠军筛选。",
        )
    else:
        overfitting.setdefault("dsr", "not_available")
        overfitting.setdefault("pbo", "not_available")
    contract = {
        "runId": run["runId"],
        "validationPolicy": (
            (run.get("manifestContract") or {})
            .get("config", {})
            .get("validationPolicy", "not_available")
        ),
        "parameterSpace": plan_contract.get("parameterSpace", "not_available"),
        "trialBudget": trial_budget or "not_available",
        "evidence": overfitting,
        "walkForwardWindowsAndMetrics": run.get(
            "walkForwardEvidence", "not_available"
        ),
    }
    return _json_html(contract)


def _run_risk_html(run: Mapping[str, Any]) -> str:
    metrics = run.get("oosMetrics") or {}
    selected = {
        key: value
        for key, value in metrics.items()
        if any(
            token in key.lower()
            for token in (
                "drawdown",
                "var",
                "es",
                "exposure",
                "weight",
                "hhi",
                "holding",
                "capacity",
                "adv",
                "volatility",
                "skew",
                "kurtosis",
                "risk",
            )
        )
    }
    if not selected:
        return _not_available(f"运行 {run['runId']} 未冻结风险与容量指标")
    return f"<h3>运行 {escape(run['runId'])}</h3>{_mapping_table(selected)}"


def _run_reproducibility_html(run: Mapping[str, Any]) -> str:
    identity = {
        key: run.get(key, "not_available")
        for key in (
            "runId",
            "reproducibilityKey",
            "configSha256",
            "dataSnapshotId",
            "codeCommit",
            "environmentSha256",
            "randomSeed",
            "manifestSha256",
            "resultFingerprint",
        )
    }
    return _json_html(identity)


def _run_chart_html(run: Mapping[str, Any]) -> str:
    series = run.get("chartSeries") or {}
    if not series:
        return _not_available(f"运行 {run['runId']} 无可绘制 canonical NAV 账本")
    benchmark_series = series.get("benchmarkNav", [])
    is_oos = (run.get("oosMetrics") or {}).get("status") == "complete"
    benchmark_note = (
        (
            "策略与匹配基准均按冻结 test/OOS 边界前一交易日净值显式归一化。"
            if is_oos
            else "策略与匹配基准路径均来自本运行 canonical 工件。"
        )
        if benchmark_series
        else "匹配基准完整路径：not_available；不绘制伪造曲线。"
    )
    return (
        f"<h3>运行 {escape(run['runId'])} 的"
        f"{'冻结 test/OOS' if is_oos else 'canonical'} 图表</h3>"
        f'<p class="muted">{escape(benchmark_note)}</p>'
        + _comparison_chart(
            series.get("nav", []),
            benchmark_series,
        )
        + _line_chart("策略回撤", series.get("drawdown", []), "drawdown")
        + _line_chart("累计单边换手", series.get("cumulativeTurnover", []), "cost")
        + _line_chart("累计成本率", series.get("cumulativeCost", []), "cost")
        + _line_chart("Gross 暴露", series.get("grossExposure", []), "exposure")
        + _line_chart("现金权重", series.get("cashWeight", []), "line")
    )


def _comparison_chart(
    strategy_points: list[Mapping[str, Any]],
    benchmark_points: list[Mapping[str, Any]],
) -> str:
    if not strategy_points or not benchmark_points:
        return _line_chart("策略净值", strategy_points, "line") + _not_available(
            "匹配基准净值路径缺失"
        )
    all_values = [
        float(item["value"])
        for item in [*strategy_points, *benchmark_points]
    ]
    low, high = min(all_values), max(all_values)
    if high == low:
        high, low = high + 0.5, low - 0.5
    width, height, pad = 760, 230, 32
    drawable_width = width - 2 * pad
    drawable_height = height - 2 * pad
    dated_points = [
        (datetime.fromisoformat(str(item["date"])[:10]), item)
        for item in [*strategy_points, *benchmark_points]
    ]
    first_date = min(item[0] for item in dated_points)
    last_date = max(item[0] for item in dated_points)
    total_days = max((last_date - first_date).days, 1)

    def coordinates(points: list[Mapping[str, Any]]) -> str:
        result = []
        for item in points:
            value = float(item["value"])
            point_date = datetime.fromisoformat(str(item["date"])[:10])
            x = pad + drawable_width * (point_date - first_date).days / total_days
            y = pad + drawable_height * (high - value) / (high - low)
            result.append(f"{x:.2f},{y:.2f}")
        return " ".join(result)

    return (
        "<figure><figcaption>策略净值与匹配基准总收益净值（canonical）</figcaption>"
        '<p class="muted">蓝线：策略；黄虚线：匹配基准。</p>'
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="策略与匹配基准净值">'
        f'<line class="axis" x1="{pad}" y1="{height-pad}" x2="{width-pad}" y2="{height-pad}"/>'
        f'<line class="axis" x1="{pad}" y1="{pad}" x2="{pad}" y2="{height-pad}"/>'
        f'<polyline class="line" points="{coordinates(strategy_points)}"/>'
        f'<polyline class="benchmark" points="{coordinates(benchmark_points)}"/>'
        f'<text x="{pad}" y="{height-8}" fill="#9fb0c1">{escape(first_date.date().isoformat())}</text>'
        f'<text x="{width-pad}" y="{height-8}" text-anchor="end" fill="#9fb0c1">{escape(last_date.date().isoformat())}</text>'
        "</svg></figure>"
    )


def _line_chart(
    title: str, points: list[Mapping[str, Any]], css_class: str
) -> str:
    if not points:
        return _not_available(f"{title}序列缺失")
    values = [float(item["value"]) for item in points]
    low, high = min(values), max(values)
    if high == low:
        high, low = high + 0.5, low - 0.5
    width, height, pad = 760, 210, 32
    drawable_width = width - 2 * pad
    drawable_height = height - 2 * pad
    coordinates = []
    for index, value in enumerate(values):
        x = pad + drawable_width * index / max(len(values) - 1, 1)
        y = pad + drawable_height * (high - value) / (high - low)
        coordinates.append(f"{x:.2f},{y:.2f}")
    return (
        f"<figure><figcaption>{escape(title)}（来源：canonical NAV 账本）</figcaption>"
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">'
        f'<line class="axis" x1="{pad}" y1="{height-pad}" x2="{width-pad}" y2="{height-pad}"/>'
        f'<line class="axis" x1="{pad}" y1="{pad}" x2="{pad}" y2="{height-pad}"/>'
        f'<polyline class="{css_class}" points="{" ".join(coordinates)}"/>'
        f'<text x="{pad}" y="{height-8}" fill="#9fb0c1">{escape(str(points[0]["date"]))}</text>'
        f'<text x="{width-pad}" y="{height-8}" text-anchor="end" fill="#9fb0c1">{escape(str(points[-1]["date"]))}</text>'
        f'<text x="{pad+4}" y="{pad+12}" fill="#9fb0c1">{escape(_format_metric(high))}</text>'
        f'<text x="{pad+4}" y="{height-pad-5}" fill="#9fb0c1">{escape(_format_metric(low))}</text>'
        "</svg></figure>"
    )


def _gate_table(
    evidence: list[Mapping[str, Any]], plan_gates: list[Any]
) -> str:
    rows = []
    for item in evidence:
        gate = item.get("planGate") or item.get("gate")
        if gate is None:
            continue
        rows.append(
            "<tr>"
            f"<td>{escape(str(gate))}</td>"
            f"<td>{escape(str(item.get('status') or 'not_available'))}</td>"
            f"<td>{escape(', '.join(map(str, item.get('evidenceRefs') or [])))}</td>"
            "</tr>"
        )
    missing = [
        str(gate)
        for gate in plan_gates
        if not any(item.get("planGate") == gate for item in evidence)
    ]
    if missing:
        rows.append(
            '<tr><td colspan="3" class="warning">未逐项评价的冻结计划门禁：'
            + escape("、".join(missing))
            + "</td></tr>"
        )
    return (
        "<h3>通用硬门禁与冻结计划事前门禁</h3><table><thead><tr>"
        "<th>门禁</th><th>状态</th><th>canonical 证据</th></tr></thead><tbody>"
        + ("".join(rows) or '<tr><td colspan="3">无结构化门禁声明。</td></tr>')
        + "</tbody></table>"
    )


def _select_mapping(
    source: Mapping[str, Any], keys: tuple[str, ...]
) -> dict[str, Any]:
    return {key: source[key] for key in keys if key in source}


def _mapping_table(source: Mapping[str, Any]) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(_metric_label(str(key)))}</td>"
        f"<td>{escape(_format_metric(value))}</td>"
        "</tr>"
        for key, value in sorted(source.items())
    )
    return (
        "<table><thead><tr><th>指标</th><th>值</th></tr></thead><tbody>"
        + (rows or '<tr><td colspan="2">not_available</td></tr>')
        + "</tbody></table>"
    )


def _metric_label(key: str) -> str:
    labels = {
        "warmupStartDate": "预热开始日（warmupStartDate）",
        "sampleStartDate": "样本外开始日（sampleStartDate）",
        "sampleEndDate": "样本外结束日（sampleEndDate）",
        "startDate": "研究开始日（startDate）",
        "endDate": "研究结束日（endDate）",
        "observations": "样本数（observations）",
        "openTradingDays": "开市日数（openTradingDays）",
        "rebalanceCount": "调仓次数（rebalanceCount）",
        "requestCount": "调仓请求数（requestCount）",
        "executionCount": "成交请求数（executionCount）",
        "blockedCount": "受阻请求数（blockedCount）",
        "independentTradeCount": "独立交易数（independentTradeCount）",
        "totalReturn": "累计收益（totalReturn）",
        "annualizedReturn": "年化收益（annualizedReturn）",
        "annualizedVolatility": "年化波动率（annualizedVolatility）",
        "maxDrawdown": "最大回撤（maxDrawdown）",
        "benchmarkTotalReturn": "匹配基准累计收益（benchmarkTotalReturn）",
        "averageOneWayTurnover": "平均单边换手（averageOneWayTurnover）",
        "cumulativeTransactionCostRate": "累计交易成本率（cumulativeTransactionCostRate）",
        "blockedRequestRate": "受阻请求比率（blockedRequestRate）",
        "maxSingleWeight": "最大单一权重（maxSingleWeight）",
        "averageGrossExposure": "平均总暴露（averageGrossExposure）",
        "endingGrossExposure": "期末总暴露（endingGrossExposure）",
        "averageNetExposure": "平均净暴露（averageNetExposure）",
        "endingNetExposure": "期末净暴露（endingNetExposure）",
        "averageHhi": "平均集中度 HHI（averageHhi）",
        "endingHhi": "期末集中度 HHI（endingHhi）",
        "var95": "95% 风险价值（var95）",
        "es95": "95% 预期短缺（es95）",
        "grossExposure": "总暴露（grossExposure）",
        "cashWeight": "现金权重（cashWeight）",
        "riskSummary": "组合风险与总风险贡献（riskSummary）",
    }
    return labels.get(key, f"原始指标（{key}）")


def _format_metric(value: Any) -> str:
    if value is None:
        return "not_available"
    if isinstance(value, float):
        return f"{value:.8g}"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


def _evidence_html(items: list[dict[str, Any]]) -> str:
    if not items:
        return "<p>无。</p>"
    return (
        "<ul>"
        + "".join(f"<li>{escape(_evidence_text(item))}</li>" for item in items)
        + "</ul>"
    )


def _evidence_text(item: Mapping[str, Any]) -> str:
    for key in ("statement", "title", "rationale", "summary"):
        value = item.get(key)
        if value:
            return str(value)
    return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _issue_comment(summary: dict[str, Any], base_url: str, marker: str) -> str:
    evaluation = summary["evaluation"]
    runs = summary["runs"]
    status_counts = {
        status: sum(1 for item in runs if item["status"] == status)
        for status in ("succeeded", "failed", "interrupted")
    }
    run_line = (
        f"- 共 {len(runs)} 次：成功 {status_counts['succeeded']}、"
        f"失败 {status_counts['failed']}、中断 {status_counts['interrupted']}。"
        "完整运行身份与结果指纹见机器摘要和冻结报告。"
        if runs
        else "- 本评价未关联研究运行；原因已记录在机器摘要的缺失证据或限制项中。"
    )
    sections = []
    for title, key in (
        ("支持证据", "supportingEvidence"),
        ("反对证据", "opposingEvidence"),
        ("尚缺证据", "missingEvidence"),
        ("限制项", "limitations"),
        ("后续研究建议", "followUpRecommendations"),
    ):
        values = evaluation[key]
        line = (
            f"- 共 {len(values)} 项；完整内容见机器摘要与冻结报告。"
            if values
            else "- 无。"
        )
        sections.append(f"### {title}\n\n{line}")
    proposal_url = _absolute_url(base_url, summary["followUpProposalUrl"])
    sections.append(
        f"### 后续研究提案\n\n- [查看该评价的结构化后续研究提案]({proposal_url})"
    )
    report_url = _absolute_url(base_url, summary["urls"]["report"])
    summary_url = _absolute_url(base_url, summary["urls"]["summary"])
    return (
        f"{marker}\n"
        f"## 研究评价版本：{evaluation['conclusion']}\n\n"
        "> 本评论冻结该评价版本；是否为当前生效结论，以研究评价状态页顶部状态为准。"
        "若显示未完成或已替代，本评论不替代当前结论。\n\n"
        f"- 策略：{_bounded_comment_text(summary['strategy']['displayName'])}"
        f"（`{summary['strategy']['id']}`）\n"
        f"- 评价版本：`{evaluation['version']}`\n"
        f"- 评价指纹：`{evaluation['sha256']}`\n"
        f"- [研究评价状态页与冻结 HTML 报告]({report_url})\n"
        f"- [机器摘要]({summary_url})\n\n"
        "### 运行事实\n\n"
        + run_line
        + "\n\n"
        + "\n\n".join(sections)
        + "\n\n> 本结论仅用于离线量化研究，不构成实盘指令、买卖评级或收益承诺。"
    )


def _bounded_comment_text(value: Any, *, max_chars: int = 160) -> str:
    normalized = " ".join(str(value).split()) or "未命名策略"
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1] + "…"


def _issue_comment_marker(summary: Mapping[str, Any]) -> str:
    evaluation = summary["evaluation"]
    return f"<!-- research-publication:evaluation:{evaluation['sha256']} -->"


def _verify_readback(
    projection: ResearchPublicationProjectionOut,
    summary: dict[str, Any],
    *,
    expected_status: str = "pending",
) -> None:
    actual = {
        "status": projection.status,
        "evaluationId": str(projection.evaluation_id),
        "evaluationVersion": projection.evaluation_version,
        "conclusion": projection.conclusion,
        "evaluationSha256": projection.evaluation_sha256,
        "manifestUrl": projection.manifest_url,
        "summaryUrl": projection.summary_url,
        "reportUrl": projection.report_url,
        "runs": [
            (item.run_id, item.status, item.result_fingerprint)
            for item in projection.runs
        ],
    }
    if actual != _expected_readback_identity(summary, expected_status):
        raise PublicationConflictError("API/前端发布读回与 canonical 工件不一致")


def _verify_frontend_readback(
    client: PublicationReadbackClient,
    projection: ResearchPublicationProjectionOut,
    summary: dict[str, Any],
    *,
    expected_status: str,
    expected_report_marker: str | None = None,
) -> None:
    raw = client.read_publication(str(projection.publication_id))
    runs = raw.get("runs")
    actual = {
        "status": raw.get("status"),
        "evaluationId": str(raw.get("evaluation_id") or ""),
        "evaluationVersion": raw.get("evaluation_version"),
        "conclusion": raw.get("conclusion"),
        "evaluationSha256": raw.get("evaluation_sha256"),
        "manifestUrl": raw.get("manifest_url"),
        "summaryUrl": raw.get("summary_url"),
        "reportUrl": raw.get("report_url"),
        "runs": (
            [
            (
                str(item.get("run_id") or ""),
                str(item.get("status") or ""),
                item.get("result_fingerprint"),
            )
            for item in runs
            ]
            if isinstance(runs, list)
            else []
        ),
    }
    if actual != _expected_readback_identity(summary, expected_status):
        raise PublicationConflictError("前端入口发布读回与 canonical 工件不一致")
    evaluation = summary["evaluation"]
    expected_artifacts = _publication_bundle_payloads(summary)
    for filename in ("manifest.json", "summary.json", "report.html"):
        if (
            client.read_artifact(evaluation["id"], filename)
            != expected_artifacts[filename]
        ):
            raise PublicationConflictError(
                f"前端入口 {filename} 与 canonical 发布工件不一致"
            )
    report_page = client.read_report(evaluation["id"])
    raw_url = summary["urls"]["rawReport"]
    status_markers = (
        "当前生效评价",
        "此评价已被替代",
        "此评价尚未完成一致发布，不代表当前研究结论",
    )
    if (
        f'data-evaluation-id="{evaluation["id"]}"' not in report_page
        or raw_url not in report_page
        or not any(marker in report_page for marker in status_markers)
        or (
            expected_report_marker is not None
            and expected_report_marker not in report_page
        )
    ):
        raise PublicationConflictError("前端入口研究评价状态页与评价版本不一致")


def _expected_readback_identity(
    summary: Mapping[str, Any], expected_status: str
) -> dict[str, Any]:
    evaluation = summary["evaluation"]
    return {
        "status": expected_status,
        "evaluationId": evaluation["id"],
        "evaluationVersion": evaluation["version"],
        "conclusion": evaluation["conclusion"],
        "evaluationSha256": evaluation["sha256"],
        "manifestUrl": summary["urls"]["manifest"],
        "summaryUrl": summary["urls"]["summary"],
        "reportUrl": summary["urls"]["report"],
        "runs": [
            (item["runId"], item["status"], item["resultFingerprint"])
            for item in summary["runs"]
        ],
    }


def _mark_publication_failed(
    session_factory: SessionFactory,
    publication_id: str,
    exc: Exception,
    *,
    now: datetime,
) -> None:
    reason = f"{type(exc).__name__}: {exc}"[:2000]
    retryable = _is_retryable_publication_failure(exc)
    try:
        with session_factory() as db, db.begin():
            publication = db.scalar(
                select(ResearchPublication)
                .where(ResearchPublication.id == publication_id)
                .with_for_update()
            )
            if publication is None or publication.status != "pending":
                return
            publication.status = "failed"
            orchestration = db.scalar(
                select(ResearchOrchestration).where(
                    ResearchOrchestration.formal_research_id
                    == publication.formal_research_id
                )
            )
            if orchestration is not None and orchestration.state == "publishing":
                transition_orchestration(
                    orchestration,
                    "blocked",
                    reason=(
                        "一致发布遇到暂时故障，可按同一评价指纹重试"
                        if retryable
                        else "一致发布合同或工件校验失败，需要人工修复后显式重试"
                    ),
                )
            append_research_event(
                db,
                publication.formal_research_id,
                "research_publication_failed",
                {
                    "publicationId": publication.id,
                    "evaluationId": publication.evaluation_id,
                    "error": reason,
                    "retryable": retryable,
                },
                occurred_at=now,
            )
    except Exception as failure_exc:
        raise PublicationError(
            f"研究发布失败，且失败审计未能持久化：{failure_exc}；原始错误：{exc}"
        ) from failure_exc


def _mark_published_publication_blocked(
    session_factory: SessionFactory,
    publication_id: str,
    exc: Exception,
    *,
    now: datetime,
) -> None:
    """保留已读回的发布版本，同时把未完成的一致收敛恢复为可审计阻塞态。"""

    reason = f"{type(exc).__name__}: {exc}"[:2000]
    retryable = _is_retryable_publication_failure(exc)
    try:
        with session_factory() as db, db.begin():
            publication = db.scalar(
                select(ResearchPublication)
                .where(ResearchPublication.id == publication_id)
                .with_for_update()
            )
            if publication is None or publication.status != "published":
                return
            formal = db.scalar(
                select(FormalResearch)
                .where(FormalResearch.id == publication.formal_research_id)
                .with_for_update()
            )
            if formal is None:
                raise PublicationConflictError("发布记录缺少正式研究")
            orchestration = db.scalar(
                select(ResearchOrchestration).where(
                    ResearchOrchestration.formal_research_id == formal.id
                )
            )
            if orchestration is not None and orchestration.state == "published":
                transition_orchestration(
                    orchestration,
                    "publishing",
                    reason="GitHub 终态尚未一致收敛",
                )
            if orchestration is not None and orchestration.state == "publishing":
                transition_orchestration(
                    orchestration,
                    "blocked",
                    reason=(
                        "一致发布遇到暂时故障，可按同一评价指纹重试"
                        if retryable
                        else "一致发布读回或合同不一致，需要人工修复后显式重试"
                    ),
                )
            if formal.phase == "published":
                work_item = db.scalar(
                    select(ResearchWorkItem).where(
                        ResearchWorkItem.formal_research_id == formal.id
                    )
                )
                formal.phase = (
                    "evaluating"
                    if formal.origin == "native"
                    and work_item is not None
                    and work_item.status == "succeeded"
                    else "stopped"
                )
            formal.completed_at = None
            append_research_event(
                db,
                publication.formal_research_id,
                "research_publication_failed",
                {
                    "publicationId": publication.id,
                    "evaluationId": publication.evaluation_id,
                    "error": reason,
                    "retryable": retryable,
                    "publicationStatus": "published",
                },
                occurred_at=now,
            )
    except Exception as failure_exc:
        raise PublicationError(
            f"研究发布失败，且已发布阶段失败审计未能持久化：{failure_exc}；"
            f"原始错误：{exc}"
        ) from failure_exc


def _mark_pending_evaluation_failed(
    session_factory: SessionFactory,
    evaluation_id: str,
    exc: Exception,
    *,
    now: datetime,
) -> None:
    with session_factory() as db:
        publication_id = db.scalar(
            select(ResearchPublication.id)
            .where(
                ResearchPublication.evaluation_id == evaluation_id,
                ResearchPublication.status == "pending",
            )
            .order_by(ResearchPublication.version.desc())
            .limit(1)
        )
    if publication_id is not None:
        _mark_publication_failed(session_factory, publication_id, exc, now=now)


def _is_retryable_publication_failure(exc: Exception) -> bool:
    if isinstance(
        exc,
        (PublicationConflictError, PublicationArtifactError, GitHubPermissionError),
    ):
        return False
    if isinstance(exc, GitHubUnavailableError):
        return True
    if isinstance(exc, GitHubResearchError):
        return False
    if isinstance(exc, PublicationError):
        return True
    return isinstance(
        exc,
        (
            TimeoutError,
            ConnectionError,
            OSError,
            OperationalError,
            SQLAlchemyTimeoutError,
        ),
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _required_projection(
    db: Session, publication_id: str
) -> ResearchPublicationProjectionOut:
    projection = get_publication_projection(db, publication_id)
    if projection is None:
        raise PublicationConflictError("发布读回不存在")
    return projection


def _publication_directory(artifact_root: Path, evaluation_id: str) -> Path:
    return (
        Path(artifact_root)
        / "publications"
        / _safe_identifier(evaluation_id, "评价 ID")
    )


def _safe_identifier(value: str, label: str) -> str:
    if not value or any(char not in "0123456789abcdef-" for char in value.lower()):
        raise PublicationConflictError(f"{label} 格式无效")
    return value


def _artifact_url(evaluation_id: str, filename: str) -> str:
    return f"/api/research/evaluations/{evaluation_id}/artifacts/{filename}"


def _report_url(evaluation_id: str) -> str:
    return f"/api/research/evaluations/{evaluation_id}/report"


def _validate_public_base_url(value: str) -> None:
    if len(value) > MAX_PUBLICATION_BASE_URL_CHARS:
        raise PublicationConflictError("RESEARCH_PUBLIC_BASE_URL 超过安全长度上限")
    parsed = urlparse(value)
    is_loopback_http = parsed.scheme == "http" and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }
    if (
        not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or (parsed.scheme != "https" and not is_loopback_http)
    ):
        raise PublicationConflictError(
            "RESEARCH_PUBLIC_BASE_URL 必须是完整 HTTPS URL 或 loopback HTTP URL"
        )


def _validate_readback_base_url(value: str) -> None:
    if len(value) > MAX_PUBLICATION_BASE_URL_CHARS:
        raise PublicationConflictError("RESEARCH_READBACK_BASE_URL 超过安全长度上限")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PublicationConflictError(
            "RESEARCH_READBACK_BASE_URL 必须是完整 HTTP(S) URL"
        )


def _resolve_public_base_url(value: str | None) -> str:
    resolved = (value or os.getenv("RESEARCH_PUBLIC_BASE_URL") or "").strip()
    _validate_public_base_url(resolved)
    return resolved.rstrip("/")


def _resolve_readback_base_url(value: str | None, public_base_url: str) -> str:
    resolved = (
        value or os.getenv("RESEARCH_READBACK_BASE_URL") or public_base_url
    ).strip()
    _validate_readback_base_url(resolved)
    return resolved.rstrip("/")


def _absolute_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"
