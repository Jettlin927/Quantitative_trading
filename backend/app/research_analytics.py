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
    "averageOneWayTurnover": ("averageOneWayTurnover",),
    "cumulativeOneWayTurnover": ("cumulativeOneWayTurnover",),
    "cumulativeTransactionCostRate": ("cumulativeTransactionCostRate",),
    "averageExposure": ("averageExposure", "averageGrossExposure"),
    "maximumWeight": ("maximumWeight", "maxWeight", "maxSingleWeight"),
    "averageHhi": ("averageHhi", "hhi"),
    "advParticipationP95": ("advParticipationP95", "p95AdvParticipationRate"),
    "blockedRequestRate": ("blockedRequestRate", "blockedRate"),
}
_REQUIRED_METRICS = (
    "totalReturn",
    "cagr",
    "benchmarkTotalReturn",
    "maxDrawdown",
    "sharpe",
    "sortino",
    "es95",
)
_METRIC_LABELS = {
    "totalReturn": "累计净收益",
    "cagr": "CAGR",
    "benchmarkTotalReturn": "匹配基准累计收益",
    "excessTotalReturn": "累计超额收益",
    "relativeWealth": "相对财富",
    "annualizedVolatility": "年化波动",
    "downsideVolatility": "下行波动",
    "sharpe": "Sharpe",
    "sortino": "Sortino",
    "maxDrawdown": "最大回撤",
    "maxDrawdownDuration": "最大回撤持续期",
    "calmar": "Calmar",
    "var95": "VaR95",
    "es95": "ES95",
    "skew": "偏度",
    "excessKurtosis": "超额峰度",
    "beta": "Beta",
    "trackingError": "跟踪误差",
    "informationRatio": "信息比率",
    "averageOneWayTurnover": "平均单边换手",
    "cumulativeOneWayTurnover": "累计单边换手",
    "cumulativeTransactionCostRate": "累计成本率",
    "averageExposure": "平均暴露",
    "maximumWeight": "最大权重",
    "averageHhi": "平均 HHI",
    "advParticipationP95": "ADV 参与率 P95",
    "blockedRequestRate": "阻塞率",
}


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
    supplement = _historical_supplementary_projection(
        strategy_id, source_summary, primary, comparisons
    )
    metrics = {
        **_normalize_metric_row(primary, benchmark),
        **supplement["metrics"],
    }
    data_status = _metric_data_status(metrics)
    return {
        "dataStatus": data_status,
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
        "robustness": supplement["robustness"],
        "capacity": supplement["capacity"],
        "metricAvailability": _metric_availability(metrics),
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
                base["robustness"] = _robustness_projection(
                    walk_forward=raw_metrics.get("walkForward"),
                    parameter_neighborhood=raw_metrics.get("parameterNeighborhood"),
                    cost_stress=raw_metrics.get("costStress"),
                    dsr=raw_metrics.get("dsr"),
                    pbo=raw_metrics.get("pbo"),
                )
                raw_capacity = raw_metrics.get("capacity")
                base["capacity"] = _evidence_projection(
                    raw_capacity,
                    "canonical OOS 指标没有容量证据",
                )
                _put_metric(
                    base["metrics"],
                    "advParticipationP95",
                    _mapping(raw_capacity).get("p95AdvParticipationRate"),
                )
                _put_metric(
                    base["metrics"],
                    "cumulativeOneWayTurnover",
                    _last_series_value(chart_series.get("cumulativeTurnover")),
                )
                base["metricAvailability"] = _metric_availability(base["metrics"])
                base["dataStatus"] = _metric_data_status(base["metrics"])
        except (PublicationArtifactError, OSError, ValueError):
            chart_reason = "canonical 账本不可用或未达到当前工件合同"

    missing_metrics = _missing_required_metrics(base.get("metrics") or {})
    availability = {
        "metrics": _availability(
            base.get("dataStatus") == "complete",
            "冻结来源缺少规范核心指标：" + "、".join(missing_metrics),
        ),
        "metricFields": base.get("metricAvailability")
        or _metric_availability(base.get("metrics") or {}),
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
        "yearly": _availability(bool(base.get("yearly")), "冻结来源没有逐年结果"),
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
        robustness=json_safe_value(base.get("robustness") or {}),
        capacity=json_safe_value(base.get("capacity") or {}),
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
        "robustness": _robustness_projection(
            walk_forward=None,
            parameter_neighborhood=None,
            cost_stress=None,
            dsr=None,
            pbo=None,
        ),
        "capacity": _evidence_projection(None, "该评价没有可用的成功运行容量证据"),
        "metricAvailability": _metric_availability({}),
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


def _historical_supplementary_projection(
    strategy_id: str,
    summary: Mapping[str, Any],
    primary: Mapping[str, Any],
    comparisons: list[Mapping[str, Any]],
) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    walk_forward: Any = None
    parameter_neighborhood: Any = None
    cost_stress: Any = None
    multiple_testing: Mapping[str, Any] = {}
    capacity: Any = None
    capacity_reason = "冻结来源没有资金规模与 ADV 容量证据"

    if strategy_id == "etf_volatility_managed":
        execution = _mapping(summary.get("executionAndCost"))
        risk = _mapping(summary.get("riskAndCapacity"))
        tail = _find_label(
            _mapping_list(summary.get("tailRisk")),
            str(primary.get("label") or ""),
        )
        metrics.update(_normalize_metric_row(tail))
        _put_metric(metrics, "averageOneWayTurnover", primary.get("turnover"))
        _put_metric(
            metrics,
            "cumulativeOneWayTurnover",
            execution.get("cumulativeOneWayTurnover"),
        )
        _put_metric(
            metrics,
            "cumulativeTransactionCostRate",
            execution.get("cumulativeTransactionCostRate"),
        )
        _put_metric(metrics, "maximumWeight", risk.get("maximumSingleWeight"))
        _put_metric(metrics, "averageHhi", risk.get("averageHhi"))
        _put_metric(metrics, "blockedRequestRate", risk.get("blockedRequestRate"))
        walk_forward = summary.get("walkForward")
        multiple_testing = _mapping(summary.get("multipleTesting"))
        cost_stress = _historical_cost_stress(primary, comparisons)
        capacity = risk.get("advParticipation") or risk.get("capacity")
    elif strategy_id == "etf_low_volatility_gate":
        section = _mapping(summary.get("lowVolatilityGateFollowup"))
        _put_metric(
            metrics,
            "cumulativeTransactionCostRate",
            section.get("cumulativeTransactionCostRate"),
        )
        walk_forward = section.get("walkForward")
        multiple_testing = _mapping(section.get("multipleTesting"))
        cost_stress = _historical_cost_stress(primary, comparisons)
    elif strategy_id == "etf_trend_120d":
        execution = _mapping(summary.get("execution"))
        _put_metric(
            metrics,
            "cumulativeOneWayTurnover",
            execution.get("turnover", primary.get("turnover")),
        )
        _put_metric(
            metrics,
            "cumulativeTransactionCostRate",
            execution.get("cumulativeCostRate", primary.get("cost")),
        )
        walk_forward = summary.get("walkForward")
        multiple_testing = _mapping(summary.get("overfitting"))
        cost_stress = summary.get("costAttribution")
        if multiple_testing.get("trialCount") == 1:
            parameter_neighborhood = "not_applicable：固定单一规则，没有参数网格。"
    elif strategy_id == "a_share_b1_trend_pullback":
        execution = _mapping(_mapping(summary.get("execution")).get("long_primary"))
        risk = _mapping(summary.get("risk"))
        tail = _mapping(_mapping(risk.get("tail")).get("strategy"))
        metrics.update(_normalize_metric_row(tail))
        _put_metric(metrics, "cumulativeOneWayTurnover", execution.get("turnover"))
        _put_metric(
            metrics, "cumulativeTransactionCostRate", execution.get("cost")
        )
        _put_metric(metrics, "averageExposure", execution.get("averageExposure"))
        _put_metric(metrics, "blockedRequestRate", execution.get("blockedRate"))
        _put_metric(metrics, "maximumWeight", risk.get("maxSingleWeight"))
        _put_metric(metrics, "averageHhi", risk.get("averageHhi"))
        walk_forward = summary.get("walkForward")
        multiple_testing = _mapping(summary.get("overfitting"))
        cost_stress = _historical_cost_stress(primary, comparisons)
        capacity = risk.get("advParticipation")
        if multiple_testing.get("localTrialCount") == 1:
            parameter_neighborhood = "not_applicable：固定单一规则，没有参数网格。"

    if parameter_neighborhood is None:
        parameter_neighborhood = "not_available：冻结来源没有参数邻域证据。"
    robustness = _robustness_projection(
        walk_forward=walk_forward,
        parameter_neighborhood=parameter_neighborhood,
        cost_stress=cost_stress,
        dsr=multiple_testing.get("deflatedSharpeRatio")
        or multiple_testing.get("dsr"),
        pbo=multiple_testing.get("pbo"),
    )
    return {
        "metrics": json_safe_value(metrics),
        "robustness": robustness,
        "capacity": _evidence_projection(capacity, capacity_reason),
    }


def _historical_cost_stress(
    primary: Mapping[str, Any], comparisons: list[Mapping[str, Any]]
) -> dict[str, Any] | None:
    stressed = next(
        (
            item
            for item in comparisons
            if "双倍成本" in str(item.get("label") or item.get("name") or "")
        ),
        None,
    )
    if stressed is None:
        return None
    return {
        "multiplier": "2",
        "baseTotalReturn": primary.get("totalReturn"),
        "stressedTotalReturn": stressed.get("totalReturn"),
    }


def _robustness_projection(
    *,
    walk_forward: Any,
    parameter_neighborhood: Any,
    cost_stress: Any,
    dsr: Any,
    pbo: Any,
) -> dict[str, Any]:
    return {
        "walkForward": _walk_forward_projection(walk_forward),
        "parameterNeighborhood": _evidence_projection(
            parameter_neighborhood, "冻结来源没有参数邻域证据"
        ),
        "costStress": _evidence_projection(
            cost_stress, "冻结来源没有成本压力证据"
        ),
        "dsr": _evidence_projection(dsr, "冻结来源没有 DSR 证据"),
        "pbo": _evidence_projection(pbo, "冻结来源没有 PBO 证据"),
    }


def _walk_forward_projection(value: Any) -> dict[str, Any]:
    projection = _evidence_projection(value, "冻结来源没有 walk-forward 证据")
    if projection.get("status") != "complete" or "windowCount" in projection:
        return projection
    rows = projection.get("windows") or projection.get("rows")
    if isinstance(rows, list):
        projection["windowCount"] = len(rows)
    return projection


def _evidence_projection(value: Any, missing_reason: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        status = str(value.get("status") or "complete")
        if status not in {"complete", "not_available", "not_applicable"}:
            status = "complete"
        return json_safe_value({**value, "status": status})
    if isinstance(value, str) and value.strip():
        lowered = value.lower()
        if "not_applicable" in lowered or "不适用" in value:
            return {
                "status": "not_applicable",
                "reason": _strip_evidence_status(value, "not_applicable"),
            }
        if (
            "not_available" in lowered
            or "暂不可" in value
            or "无法判断" in value
            or "未绑定" in value
        ):
            return {
                "status": "not_available",
                "reason": _strip_evidence_status(value, "not_available"),
            }
        return {"status": "complete", "summary": value}
    return {"status": "not_available", "reason": missing_reason}


def _strip_evidence_status(value: str, status: str) -> str:
    for separator in (":", "："):
        prefix = f"{status}{separator}"
        if value.lower().startswith(prefix):
            return value[len(prefix) :].strip()
    return value


def _put_metric(metrics: dict[str, Any], key: str, value: Any) -> None:
    if _metric_present(value):
        metrics[key] = json_safe_value(value)


def _last_series_value(value: Any) -> Any:
    if not isinstance(value, list) or not value:
        return None
    return _mapping(value[-1]).get("value")


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


def _metric_present(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _missing_required_metrics(metrics: Mapping[str, Any]) -> list[str]:
    missing = [
        _METRIC_LABELS[key]
        for key in _REQUIRED_METRICS
        if not _metric_present(metrics.get(key))
    ]
    if not any(
        _metric_present(metrics.get(key))
        for key in ("excessTotalReturn", "relativeWealth")
    ):
        missing.append("累计超额收益或相对财富")
    return missing


def _metric_data_status(metrics: Mapping[str, Any]) -> str:
    return "not_available" if _missing_required_metrics(metrics) else "complete"


def _metric_availability(metrics: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    return {
        key: (
            {"status": "complete"}
            if _metric_present(metrics.get(key))
            else {"status": "not_available", "reason": f"冻结来源没有{label}"}
        )
        for key, label in _METRIC_LABELS.items()
    }


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
