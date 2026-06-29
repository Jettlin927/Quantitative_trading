from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def build_strategy_results_overview(repo_root: Path) -> dict[str, Any]:
    results_root = repo_root / "docs" / "research" / "strategy-results"
    manifest_path = results_root / "manifest.json"
    if not manifest_path.exists():
        return {
            "source": "docs/research/strategy-results",
            "mode": "readonly",
            "executionEnabled": False,
            "resultSets": [],
        }

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result_sets = []
    for item in manifest.get("resultSets", []):
        artifacts = item.get("artifacts", {})
        phased = read_json_rows(results_root / artifacts.get("phasedJson", ""))
        score_scan = read_csv_rows(results_root / artifacts.get("scoreScanCsv", ""), limit=8)
        result_sets.append(
            {
                **item,
                "phases": phased,
                "scoreScanTop": score_scan,
                "summary": summarize_phases(phased),
            }
        )

    return {
        "source": manifest.get("source", "docs/research/strategy-results"),
        "mode": manifest.get("mode", "readonly"),
        "executionEnabled": bool(manifest.get("executionEnabled", False)),
        "resultSets": result_sets,
    }


def read_json_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("rows", [])


def read_csv_rows(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [normalize_csv_row(row) for row in rows[:limit]]


def normalize_csv_row(row: dict[str, str]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in row.items():
        if value in {"True", "False"}:
            normalized[key] = value == "True"
            continue
        try:
            normalized[key] = float(value)
        except (TypeError, ValueError):
            normalized[key] = value
    return normalized


def summarize_phases(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"phaseCount": 0}
    best_annual = max(rows, key=lambda row: row.get("annual_return") or float("-inf"))
    worst_drawdown = min(rows, key=lambda row: row.get("max_drawdown") or 0)
    return {
        "phaseCount": len(rows),
        "anyReturnGatePassed": any(bool(row.get("passes_return_gate")) for row in rows),
        "allDrawdownGatePassed": all(bool(row.get("passes_drawdown_gate")) for row in rows),
        "bestAnnualReturnPhase": best_annual.get("phase"),
        "bestAnnualReturn": best_annual.get("annual_return"),
        "worstDrawdownPhase": worst_drawdown.get("phase"),
        "worstDrawdown": worst_drawdown.get("max_drawdown"),
    }
