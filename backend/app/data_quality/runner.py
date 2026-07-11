from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models import DataQualityResult, DataQualityRun
from .contracts import QualityCheckContract, QualityRuleResult, result_reference, summarize_quality_status
from .rules import evaluate_quality_rules


def configure_quality_read_transaction(db: Session, statement_timeout_ms: int) -> None:
    """在 PostgreSQL 上为质量查询启用一致性、只读和有限时事务。"""
    if not db.bind or db.bind.dialect.name != "postgresql":
        return
    timeout = int(statement_timeout_ms)
    if not 500 <= timeout <= 60_000:
        raise ValueError("statement_timeout_ms 必须在 500 到 60000 之间")
    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
    db.execute(text("SET TRANSACTION READ ONLY"))
    db.execute(text(f"SET LOCAL statement_timeout = '{timeout}ms'"))


def run_data_quality_check(
    registry_db: Session,
    contract: QualityCheckContract,
    *,
    code_commit: str | None = None,
    evaluator: Callable[[Session, QualityCheckContract], list[QualityRuleResult]] = evaluate_quality_rules,
) -> dict[str, Any]:
    run = DataQualityRun(
        id=str(uuid4()),
        scope=contract.scope,
        start_date=contract.start_date,
        end_date=contract.end_date,
        universe_hash=contract.universe_hash,
        status="running",
        config=contract.to_config(),
        summary={},
        code_commit=code_commit,
        started_at=datetime.now(timezone.utc),
    )
    registry_db.add(run)
    registry_db.commit()

    try:
        with Session(bind=registry_db.get_bind(), autoflush=False, expire_on_commit=False) as inspection_db:
            with inspection_db.begin():
                configure_quality_read_transaction(inspection_db, contract.statement_timeout_ms)
                results = evaluator(inspection_db, contract)
    except Exception as exc:  # noqa: BLE001
        results = [QualityRuleResult.failed("engine.execution", "data_quality_runs", f"{type(exc).__name__}: {exc}")]

    run.status = summarize_quality_status(results)
    run.finished_at = datetime.now(timezone.utc)
    run.summary = build_quality_summary(results, contract)
    for result in results:
        registry_db.add(
            DataQualityResult(
                run_id=run.id,
                rule_id=result.rule_id,
                table_name=result.table_name,
                severity=result.severity,
                status=result.status,
                checked_rows=result.checked_rows,
                failed_rows=result.failed_rows,
                sample_issues=result.sample_issues,
            )
        )
    registry_db.commit()
    registry_db.refresh(run)
    return quality_run_to_dict(run, list_quality_results(registry_db, run.id))


def build_quality_summary(results: list[QualityRuleResult], contract: QualityCheckContract) -> dict[str, Any]:
    blockers = [result_reference(result) for result in results if result.status == "blocked"]
    failed = [result_reference(result) for result in results if result.status == "failed"]
    warnings = [result_reference(result) for result in results if result.status == "warning"]
    limitations: list[str] = []
    if contract.scope == "a_share_cross_section" and contract.universe_type == "static_current":
        limitations.append("static_current_universe_has_survivorship_risk")
    if any(result.rule_id == "universe.provenance" and result.status == "blocked" for result in results):
        limitations.append("universe_provenance_unverified")
    if any(result.rule_id == "domain.unlisted_codes" and result.status == "warning" for result in results):
        limitations.append("out_of_domain_history_reported_not_deleted")
    if any(result.rule_id == "adjustment.factor_jump" and result.status == "warning" for result in results):
        limitations.append("corporate_action_registry_unavailable")
    if any(
        result.rule_id == "point_in_time.financial_revision_history" and result.status == "blocked"
        for result in results
    ):
        limitations.append("financial_revision_history_unavailable")
    return {
        "status": summarize_quality_status(results),
        "resultCount": len(results),
        "passedCount": sum(result.status == "passed" for result in results),
        "warningCount": len(warnings),
        "blockerCount": len(blockers),
        "failedCount": len(failed),
        "blockers": blockers,
        "warnings": warnings,
        "failedRules": failed,
        "limitations": limitations,
        "requiredDatasets": list(contract.required_datasets),
        "benchmark": contract.benchmark,
    }


def list_quality_results(db: Session, run_id: str) -> list[DataQualityResult]:
    return list(
        db.scalars(
            select(DataQualityResult)
            .where(DataQualityResult.run_id == run_id)
            .order_by(DataQualityResult.rule_id, DataQualityResult.table_name)
        ).all()
    )


def quality_run_to_dict(run: DataQualityRun, results: list[DataQualityResult] | None = None) -> dict[str, Any]:
    return {
        "qualityRunId": run.id,
        "scope": run.scope,
        "startDate": run.start_date.isoformat(),
        "endDate": run.end_date.isoformat(),
        "universeHash": run.universe_hash,
        "status": run.status,
        "config": run.config or {},
        "summary": run.summary or {},
        "codeCommit": run.code_commit,
        "startedAt": run.started_at.isoformat() if run.started_at else None,
        "finishedAt": run.finished_at.isoformat() if run.finished_at else None,
        "results": [quality_result_to_dict(result) for result in (results or [])],
    }


def quality_result_to_dict(result: DataQualityResult) -> dict[str, Any]:
    return {
        "ruleId": result.rule_id,
        "tableName": result.table_name,
        "severity": result.severity,
        "status": result.status,
        "checkedRows": int(result.checked_rows or 0),
        "failedRows": int(result.failed_rows or 0),
        "sampleIssues": list((result.sample_issues or [])[:20]),
    }
