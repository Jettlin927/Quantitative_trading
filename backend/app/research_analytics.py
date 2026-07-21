from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .json_safety import json_safe_value
from .models import (
    FormalResearch,
    ResearchEvaluation,
    ResearchEvaluationRun,
    ResearchEvidenceRef,
    ResearchPublication,
    ResearchRun,
)
from .quant_research.artifacts import ArtifactIntegrityError, verify_csv_artifact
from .quant_research.manifest import build_result_fingerprint
from .research_publication import (
    PublicationArtifactError,
    _canonical_run_identity,
    _read_canonical_nav_series,
    _read_repo_json_evidence,
)
from .schemas import ResearchPublicationAnalyticsOut


class HistoricalAnalyticsError(ValueError):
    pass


_METRIC_ALIASES = {
    "totalReturn": ("totalReturn", "cumulativeNetReturn"),
    "cagr": ("cagr", "annualizedReturn"),
    "benchmarkTotalReturn": ("benchmarkTotalReturn",),
    "excessTotalReturn": ("excessTotalReturn", "activeReturn"),
    "relativeWealth": ("relativeWealth",),
    "annualizedVolatility": ("annualizedVolatility", "volatility"),
    "downsideVolatility": ("downsideVolatility",),
    "sharpe": ("sharpe",),
    "sortino": ("sortino",),
    "maxDrawdown": ("maxDrawdown", "strategyMaxDrawdown"),
    "maxDrawdownDuration": ("maxDrawdownDuration",),
    "calmar": ("calmar",),
    "var95": ("var95",),
    "es95": ("es95",),
    "skew": ("skew",),
    "excessKurtosis": ("excessKurtosis",),
    "beta": ("beta",),
    "trackingError": ("trackingError",),
    "informationRatio": ("informationRatio",),
    "turnover": ("turnover", "cumulativeOneWayTurnover", "averageOneWayTurnover"),
    "cost": ("cost", "cumulativeTransactionCostRate"),
    "averageExposure": ("averageExposure", "averageGrossExposure"),
    "maximumWeight": ("maximumWeight", "maxWeight"),
    "averageHhi": ("averageHhi", "hhi"),
    "advParticipationP95": ("advParticipationP95",),
    "blockedRequestRate": ("blockedRequestRate", "blockedRate"),
}
_CORE_METRICS = ("totalReturn", "cagr", "maxDrawdown", "sharpe", "sortino", "es95")


def build_historical_source_analytics(
    strategy_id: str,
    source_summary: Mapping[str, Any],
    *,
    source_uri: str,
    source_sha256: str,
) -> dict[str, Any]:
    primary, benchmark, comparisons, primary_run_id, yearly, regimes = (
        _historical_strategy_projection(strategy_id, source_summary)
    )
    metrics = _normalize_metric_row(primary, benchmark)
    if not _has_core_metrics(metrics):
        raise HistoricalAnalyticsError(
            f"历史策略 {strategy_id} 的冻结来源缺少规范核心指标"
        )
    return {
        "dataStatus": "complete",
        "primaryRunId": primary_run_id,
        "primaryLabel": str(primary.get("label") or primary.get("name") or strategy_id),
        "metrics": metrics,
        "benchmark": _normalize_benchmark(benchmark),
        "comparisons": [
            {
                "label": str(item.get("label") or item.get("name") or "未命名方案"),
                "metrics": _normalize_metric_row(item, benchmark),
            }
            for item in comparisons
            if isinstance(item, Mapping)
        ],
        "yearly": json_safe_value(yearly),
        "regimes": json_safe_value(regimes),
        "provenance": {
            "kind": "historical_frozen_source",
            "uri": source_uri,
            "sha256": source_sha256,
        },
    }


def get_publication_analytics(
    db: Session,
    publication_id: str,
) -> ResearchPublicationAnalyticsOut | None:
    publication = db.get(ResearchPublication, publication_id)
    if publication is None:
        return None
    evaluation = db.get(ResearchEvaluation, publication.evaluation_id)
    formal = db.get(FormalResearch, publication.formal_research_id)
    if evaluation is None or formal is None:
        raise HistoricalAnalyticsError("研究发布缺少评价或正式研究")

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
                .order_by(ResearchRun.finished_at.desc(), ResearchRun.run_id)
            ).all()
        )
        if run_ids
        else []
    )

    if formal.origin == "historical_import":
        base = _historical_publication_analytics(db, evaluation, formal, runs)
    else:
        base = _native_publication_analytics(runs)

    primary_run_id = base.get("primaryRunId")
    primary_run = next((item for item in runs if item.run_id == primary_run_id), None)
    chart_series: dict[str, Any] = {}
    run_provenance: dict[str, Any] = {}
    chart_reason = "该评价没有可用的成功运行账本"
    if primary_run is not None and primary_run.status == "succeeded":
        try:
            canonical = (
                read_historical_run_chart_series(primary_run)
                if formal.origin == "historical_import"
                else _canonical_run_identity(primary_run, historical=False)
            )
            chart_series = json_safe_value(canonical.get("chartSeries") or {})
            run_provenance = {
                "runId": primary_run.run_id,
                "resultFingerprint": primary_run.result_fingerprint,
                "manifestSha256": canonical.get("manifestSha256"),
                "artifactSchemaVersion": canonical.get("artifactSchemaVersion"),
                "chartContract": canonical.get("chartContract", "canonical"),
            }
            chart_reason = "canonical 账本没有可绘制序列"
            if formal.origin != "historical_import":
                raw_metrics = canonical.get("oosMetrics") or canonical.get("metrics") or {}
                base["metrics"] = _normalize_metric_row(raw_metrics)
                base["benchmark"] = {
                    "label": "冻结计划匹配基准",
                    "totalReturn": base["metrics"].get("benchmarkTotalReturn"),
                }
                base["yearly"] = _normalize_yearly(
                    raw_metrics.get("yearly") or raw_metrics.get("yearlyMetrics") or []
                )
                base["regimes"] = _normalize_regimes(
                    raw_metrics.get("regimes") or raw_metrics.get("marketRegimes") or []
                )
                base["dataStatus"] = (
                    "complete" if _has_core_metrics(base["metrics"]) else "not_available"
                )
        except (PublicationArtifactError, OSError, ValueError):
            chart_reason = "canonical 账本不可用或未达到当前工件合同"

    availability = {
        "metrics": _availability(
            base.get("dataStatus") == "complete",
            "冻结来源缺少一项或多项规范核心指标",
        ),
        "nav": _availability(bool(chart_series.get("nav")), chart_reason),
        "benchmarkNav": _availability(
            bool(chart_series.get("benchmarkNav")),
            "冻结账本没有匹配基准净值序列",
        ),
        "drawdown": _availability(bool(chart_series.get("drawdown")), chart_reason),
        "turnoverCost": _availability(
            bool(chart_series.get("cumulativeTurnover") or chart_series.get("cumulativeCost")),
            chart_reason,
        ),
        "regimes": _availability(bool(base.get("regimes")), "冻结来源没有市场环境矩阵"),
    }
    provenance = {**base.get("provenance", {}), **run_provenance}
    return ResearchPublicationAnalyticsOut(
        publication_id=publication.id,
        evaluation_id=evaluation.id,
        evaluation_version=evaluation.version,
        data_status=base.get("dataStatus", "not_available"),
        primary_run_id=primary_run_id,
        primary_label=base.get("primaryLabel"),
        metrics=json_safe_value(base.get("metrics") or {}),
        benchmark=json_safe_value(base.get("benchmark") or {}),
        comparisons=json_safe_value(base.get("comparisons") or []),
        chart_series=chart_series,
        yearly=json_safe_value(_normalize_yearly(base.get("yearly") or [])),
        regimes=json_safe_value(_normalize_regimes(base.get("regimes") or [])),
        availability=availability,
        provenance=json_safe_value(provenance),
    )


def read_historical_run_chart_series(run: ResearchRun) -> dict[str, Any]:
    root = Path(run.artifact_root)
    manifest_path = root / "manifest.json"
    try:
        payload = manifest_path.read_bytes()
        manifest = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoricalAnalyticsError("历史运行 manifest 无法读取") from exc
    if not isinstance(manifest, Mapping):
        raise HistoricalAnalyticsError("历史运行 manifest 不是 JSON object")

    expected_identity = {
        "runId": run.run_id,
        "strategyId": run.strategy_id,
        "codeCommit": run.code_commit,
        "configSha256": run.config_sha256,
        "randomSeed": run.random_seed,
        "reproducibilityKey": run.reproducibility_key,
        "resultFingerprint": run.result_fingerprint,
    }
    if any(manifest.get(key) != value for key, value in expected_identity.items()):
        raise HistoricalAnalyticsError("历史运行 manifest 与数据库冻结身份不一致")
    environment = manifest.get("environment")
    snapshot = manifest.get("dataSnapshot")
    if (
        not isinstance(environment, Mapping)
        or environment.get("sha256") != run.environment_sha256
        or not isinstance(snapshot, Mapping)
        or snapshot.get("snapshotId") != run.data_snapshot_id
    ):
        raise HistoricalAnalyticsError("历史运行环境或数据快照身份不一致")

    artifact_hashes = manifest.get("artifactHashes")
    if not isinstance(artifact_hashes, Mapping) or not artifact_hashes:
        raise HistoricalAnalyticsError("历史运行 manifest 缺少 artifactHashes")
    try:
        fingerprint = build_result_fingerprint(artifact_hashes)
        verify_csv_artifact(root / "nav.csv.gz", artifact_hashes["nav.csv.gz"])
        chart_series = _read_canonical_nav_series(root / "nav.csv.gz")
    except (ArtifactIntegrityError, PublicationArtifactError) as exc:
        raise HistoricalAnalyticsError(str(exc)) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise HistoricalAnalyticsError("历史运行结果指纹无效") from exc
    if fingerprint != run.result_fingerprint:
        raise HistoricalAnalyticsError("历史运行结果指纹与数据库冻结身份不一致")

    return {
        "chartSeries": chart_series,
        "manifestSha256": sha256(payload).hexdigest(),
        "artifactSchemaVersion": manifest.get("artifactSchemaVersion", 1),
        "chartContract": "historical_artifact_fingerprint_verified",
    }


def _historical_publication_analytics(
    db: Session,
    evaluation: ResearchEvaluation,
    formal: FormalResearch,
    runs: list[ResearchRun],
) -> dict[str, Any]:
    source_ref = db.scalar(
        select(ResearchEvidenceRef)
        .where(
            ResearchEvidenceRef.evaluation_id == evaluation.id,
            ResearchEvidenceRef.kind == "statistics",
        )
        .order_by(ResearchEvidenceRef.uri)
        .limit(1)
    )
    if source_ref is None or not source_ref.sha256:
        raise HistoricalAnalyticsError("历史研究缺少冻结统计来源")
    try:
        source_summary = _read_repo_json_evidence(source_ref)
    except PublicationArtifactError as exc:
        raise HistoricalAnalyticsError("历史研究冻结统计来源无法通过指纹校验") from exc
    strategy_id = next(
        (item.strategy_id for item in runs if item.formal_research_id == formal.id),
        None,
    )
    if not strategy_id:
        raise HistoricalAnalyticsError("历史研究缺少已冻结的运行身份")
    result = build_historical_source_analytics(
        strategy_id,
        source_summary,
        source_uri=source_ref.uri,
        source_sha256=source_ref.sha256,
    )
    if result["primaryRunId"] not in {item.run_id for item in runs}:
        raise HistoricalAnalyticsError("冻结来源的主运行不属于当前评价")
    return result


def _native_publication_analytics(runs: list[ResearchRun]) -> dict[str, Any]:
    primary = next((item for item in runs if item.status == "succeeded"), None)
    return {
        "dataStatus": "not_available",
        "primaryRunId": primary.run_id if primary else None,
        "primaryLabel": primary.strategy_id if primary else None,
        "metrics": {},
        "benchmark": {},
        "comparisons": [],
        "yearly": [],
        "regimes": [],
        "provenance": {"kind": "canonical"},
    }


def _historical_strategy_projection(
    strategy_id: str,
    summary: Mapping[str, Any],
) -> tuple[
    Mapping[str, Any],
    Mapping[str, Any],
    list[Mapping[str, Any]],
    str,
    list[Any],
    list[Any],
]:
    if strategy_id == "etf_trend_120d":
        comparisons = _mapping_list(summary.get("comparison"))
        primary = _find(comparisons, "labelKey", "base_cost")
        benchmark = _find(comparisons, "labelKey", "passive")
        primary_run_id = _scenario_run_id(summary.get("runIdentities"), "基础成本")
        yearly, regimes = summary.get("yearly"), summary.get("regimes")
    elif strategy_id == "etf_volatility_managed":
        comparisons = _mapping_list(summary.get("comparison"))
        primary = _find_label(comparisons, "逆方差强力降风险版（T0）")
        benchmark = _find_label(comparisons, "被动 ETF")
        reproduction = _mapping(summary.get("reproduction"))
        primary_run_id = str(_mapping(reproduction.get("T0")).get("runId") or "")
        yearly, regimes = summary.get("yearly"), summary.get("regimes")
    elif strategy_id == "etf_low_volatility_gate":
        section = _mapping(summary.get("lowVolatilityGateFollowup"))
        comparisons = _mapping_list(section.get("comparison"))
        primary = _find_label(comparisons, "沪深300 ETF 低波动准入策略")
        benchmark = _find_label(comparisons, "50% ETF + 50% 现金持有基准")
        reproduction = _mapping(section.get("reproduction"))
        primary_run_id = str(
            _mapping(reproduction.get("base_cost")).get("runId") or ""
        )
        yearly, regimes = section.get("yearly"), section.get("regimes")
    elif strategy_id == "a_share_b1_trend_pullback":
        primary = _mapping(summary.get("primary"))
        benchmark = _mapping(summary.get("benchmark"))
        comparisons = _mapping_list(summary.get("longComparison"))
        primary_run_id = _scenario_run_id(summary.get("runIdentities"), "长历史主版本")
        yearly, regimes = summary.get("yearly"), summary.get("regimes")
    else:
        raise HistoricalAnalyticsError(f"历史策略 {strategy_id} 没有冻结适配器")

    if not primary or not benchmark or not primary_run_id:
        raise HistoricalAnalyticsError(f"历史策略 {strategy_id} 的冻结指标映射不完整")
    return (
        primary,
        benchmark,
        comparisons,
        primary_run_id,
        list(yearly) if isinstance(yearly, list) else [],
        list(regimes) if isinstance(regimes, list) else [],
    )


def _normalize_metric_row(
    row: Mapping[str, Any], benchmark: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for target, aliases in _METRIC_ALIASES.items():
        value = next((row.get(key) for key in aliases if key in row), None)
        if target == "benchmarkTotalReturn" and value is None and benchmark:
            value = benchmark.get("totalReturn")
        if value is not None:
            normalized[target] = json_safe_value(value)
    return normalized


def _has_core_metrics(metrics: Mapping[str, Any]) -> bool:
    return all(metrics.get(key) is not None for key in _CORE_METRICS)


def _normalize_benchmark(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "label": str(row.get("label") or row.get("name") or "匹配基准"),
        **_normalize_metric_row(row),
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mapping_list(value: Any) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _find(rows: list[Mapping[str, Any]], key: str, expected: str) -> Mapping[str, Any]:
    return next((item for item in rows if item.get(key) == expected), {})


def _find_label(rows: list[Mapping[str, Any]], expected: str) -> Mapping[str, Any]:
    return next((item for item in rows if item.get("label") == expected), {})


def _scenario_run_id(value: Any, token: str) -> str:
    rows = _mapping_list(value)
    item = next((row for row in rows if token in str(row.get("scenario") or "")), {})
    return str(item.get("runId") or "")


def _availability(available: bool, reason: str) -> dict[str, str]:
    return {"status": "complete"} if available else {"status": "not_available", "reason": reason}


def _normalize_yearly(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [json_safe_value(item) for item in value if isinstance(item, Mapping)]
    if not isinstance(value, Mapping):
        return []
    rows = []
    for year, raw in sorted(value.items(), key=lambda item: str(item[0])):
        item = _mapping(raw)
        rows.append(
            json_safe_value(
                {
                    **item,
                    "year": year,
                    "strategyReturn": item.get("strategyReturn", item.get("totalReturn")),
                    "benchmarkReturn": item.get("benchmarkReturn", item.get("benchmarkTotalReturn")),
                    "activeReturn": item.get(
                        "activeReturn",
                        item.get("activeTotalReturn", item.get("excessTotalReturn")),
                    ),
                }
            )
        )
    return rows


def _normalize_regimes(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [json_safe_value(item) for item in value if isinstance(item, Mapping)]
    if not isinstance(value, Mapping):
        return []
    cells = value.get("cells") if isinstance(value.get("cells"), Mapping) else value
    rows = []
    for name, raw in sorted(cells.items(), key=lambda item: str(item[0])):
        item = _mapping(raw)
        direction, _, volatility = str(name).partition("_")
        rows.append(
            json_safe_value(
                {
                    **item,
                    "direction": item.get("direction") or direction,
                    "volatility": item.get("volatility") or volatility or "未分类",
                    "strategyReturn": item.get("strategyReturn", item.get("totalReturn")),
                    "benchmarkReturn": item.get("benchmarkReturn", item.get("benchmarkTotalReturn")),
                    "activeReturn": item.get(
                        "activeReturn",
                        item.get("activeTotalReturn", item.get("excessTotalReturn")),
                    ),
                }
            )
        )
    return rows
