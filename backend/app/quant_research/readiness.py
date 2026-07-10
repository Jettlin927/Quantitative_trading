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
        "scope": scope,
        "status": "blocked" if blockers else "ready",
        "requiredTables": sorted(required),
        "missingTables": missing,
        "emptyTables": empty,
        "blockers": blockers,
        "tableCounts": {table: int(table_counts.get(table, 0)) for table in sorted(required)},
        "boundaries": {
            "researchOnly": True,
            "executionEnabled": False,
            "brokerConnected": False,
        },
    }
