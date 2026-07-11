from __future__ import annotations

from typing import Any, Iterable


SCOPES = {
    "etf_time_series": {
        "nonempty": {"trade_calendars", "funds", "fund_daily_bars", "fund_adjust_factors", "indices", "index_daily_bars"},
        "present": set(),
    },
    "a_share_cross_section": {
        "nonempty": {
            "trade_calendars",
            "stocks",
            "stock_daily_bars",
            "stock_adjust_factors",
            "indices",
            "index_daily_bars",
            "stock_listings",
            "stock_limit_prices",
        },
        "present": {"stock_suspend_events"},
    },
}


def evaluate_research_readiness(
    scope: str,
    available_tables: Iterable[str],
    table_counts: dict[str, int],
) -> dict[str, Any]:
    if scope not in SCOPES:
        raise ValueError(f"未知研究范围：{scope}")
    available = set(available_tables)
    requirements = SCOPES[scope]
    required = requirements["nonempty"] | requirements["present"]
    missing = sorted(required - available)
    empty = sorted(table for table in requirements["nonempty"] if table in available and int(table_counts.get(table, 0)) <= 0)
    blockers = [f"missing_table:{table}" for table in missing] + [f"empty_table:{table}" for table in empty]
    return {
        "level": "inventory",
        "scope": scope,
        "status": "inventory_incomplete" if blockers else "inventory_available",
        "researchReady": False,
        "requiredTables": sorted(required),
        "missingTables": missing,
        "emptyTables": empty,
        "blockers": blockers,
        "tableCounts": {table: int(table_counts.get(table, 0)) for table in sorted(required)},
        "limitations": ["inventory_only_requires_quality_run_for_research_readiness"],
        "boundaries": {
            "researchOnly": True,
            "executionEnabled": False,
            "brokerConnected": False,
        },
    }


def evaluate_quality_run_readiness(run: Any, results: Iterable[Any]) -> dict[str, Any]:
    quality_results = list(results)
    blockers = [
        f"{result.rule_id}:{result.table_name}"
        for result in quality_results
        if result.status in {"blocked", "failed"}
    ]
    warnings = [
        f"{result.rule_id}:{result.table_name}"
        for result in quality_results
        if result.status == "warning"
    ]
    summary = dict(run.summary or {})
    config = dict(run.config or {})
    status = run.status
    if run.scope == "a_share_cross_section":
        universe_type = config.get("universeType")
        if universe_type not in {"explicit_snapshot", "static_current", "industry_membership"}:
            blockers.append("universe.provenance:stock_listings")
        if universe_type == "static_current":
            blockers.append("universe.survivorship_risk:stock_listings")
        if universe_type == "explicit_snapshot" and (
            not config.get("universeSource") or not config.get("universeAsOfDate")
        ):
            blockers.append("universe.provenance:stock_listings")
        elif universe_type == "explicit_snapshot" and str(config.get("universeAsOfDate")) > run.start_date.isoformat():
            blockers.append("universe.provenance:stock_listings")
        if universe_type == "industry_membership":
            blockers.append("universe.provenance:industry_members")
    blockers = list(dict.fromkeys(blockers))
    if blockers and status in {"ready", "ready_with_warnings"}:
        status = "blocked"
    if status == "running" and not blockers:
        blockers = ["quality_run_incomplete"]
    return {
        "level": "research",
        "scope": run.scope,
        "status": status,
        "researchReady": status in {"ready", "ready_with_warnings"} and not blockers,
        "qualityRunId": run.id,
        "universeHash": run.universe_hash,
        "startDate": run.start_date.isoformat(),
        "endDate": run.end_date.isoformat(),
        "requiredDatasets": list(config.get("requiredDatasets") or []),
        "benchmark": config.get("benchmark"),
        "blockers": blockers,
        "warnings": warnings,
        "limitations": list(summary.get("limitations") or []),
        "boundaries": {
            "researchOnly": True,
            "executionEnabled": False,
            "brokerConnected": False,
        },
    }
