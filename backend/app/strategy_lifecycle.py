from __future__ import annotations

import json
from pathlib import Path
from typing import Any


LIFECYCLE_RELATIVE_PATH = Path("docs") / "research" / "strategy-lifecycle.json"


def load_strategy_lifecycle(repo_root: Path) -> dict[str, Any]:
    path = repo_root / LIFECYCLE_RELATIVE_PATH
    if not path.exists():
        return {
            "source": LIFECYCLE_RELATIVE_PATH.as_posix(),
            "version": 0,
            "strategies": [],
            "primaryDashboardStrategies": [],
            "counts": {},
        }

    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    strategies = [normalize_lifecycle_item(item) for item in payload.get("strategies", [])]
    counts: dict[str, int] = {"total": len(strategies)}
    for item in strategies:
        status = item["lifecycleStatus"]
        counts[status] = counts.get(status, 0) + 1

    return {
        **payload,
        "source": LIFECYCLE_RELATIVE_PATH.as_posix(),
        "strategies": strategies,
        "primaryDashboardStrategies": [item for item in strategies if item["showInPrimaryDashboard"]],
        "counts": counts,
    }


def lookup_strategy_lifecycle(lifecycle: dict[str, Any], strategy_id: str) -> dict[str, Any]:
    for item in lifecycle.get("strategies", []):
        if item.get("strategyId") == strategy_id:
            return item
    return {
        "strategyId": strategy_id,
        "label": strategy_id,
        "lifecycleStatus": "unknown",
        "showInPrimaryDashboard": False,
        "evidenceRetention": "keep",
    }


def normalize_lifecycle_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        **item,
        "strategyId": str(item.get("strategyId") or ""),
        "label": str(item.get("label") or item.get("strategyId") or ""),
        "lifecycleStatus": str(item.get("lifecycleStatus") or "unknown"),
        "showInPrimaryDashboard": bool(item.get("showInPrimaryDashboard", False)),
        "evidenceRetention": str(item.get("evidenceRetention") or "keep"),
    }
