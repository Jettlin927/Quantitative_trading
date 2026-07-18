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
        phased = read_json_rows(artifact_path(results_root, artifacts, "phasedJson"))
        score_scan = read_csv_rows(
            artifact_path(results_root, artifacts, "scoreScanCsv"), limit=8
        )
        report_summary = read_json_object(
            artifact_path(results_root, artifacts, "summaryJson")
        )
        result_sets.append(
            {
                **item,
                "phases": phased,
                "scoreScanTop": score_scan,
                "summary": (
                    summarize_report(report_summary)
                    if report_summary
                    else summarize_phases(phased)
                ),
            }
        )

    return {
        "manifestVersion": manifest.get("manifestVersion", 1),
        "source": manifest.get("source", "docs/research/strategy-results"),
        "mode": manifest.get("mode", "readonly"),
        "executionEnabled": bool(manifest.get("executionEnabled", False)),
        "landingPage": manifest.get("landingPage"),
        "resultSets": result_sets,
    }


def artifact_path(
    results_root: Path,
    artifacts: dict[str, Any],
    key: str,
) -> Path | None:
    value = artifacts.get(key)
    if not isinstance(value, str) or not value:
        return None
    return results_root / value


def read_json_rows(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("rows", [])


def read_json_object(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def read_csv_rows(path: Path | None, limit: int) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
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


def summarize_report(payload: dict[str, Any]) -> dict[str, Any]:
    conclusion = payload.get("conclusion")
    if isinstance(conclusion, dict):
        conclusion_text = conclusion.get("oneLine")
    else:
        conclusion_text = conclusion
    if not conclusion_text:
        conclusion_text = payload.get("oneSentenceConclusion")
    if not conclusion_text:
        evidence = payload.get("evidence")
        against = evidence.get("against") if isinstance(evidence, dict) else None
        if isinstance(against, list) and against:
            conclusion_text = against[0]
    return {
        "status": payload.get("status"),
        "conclusion": conclusion_text,
        "reportGeneratedAt": (
            payload.get("reportGeneratedAt")
            or payload.get("researchDate")
            or payload.get("generated_at")
        ),
    }
