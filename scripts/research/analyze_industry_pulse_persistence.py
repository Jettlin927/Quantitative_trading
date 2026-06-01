from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from sqlalchemy import func, select

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import finite, json_safe
from backend.app.database import SessionLocal
from backend.app.models import Stock, StockDailyBar
from scripts.research.run_research_round import RUNS_ROOT, format_optional_percent, now_text, read_json, write_json, write_text


WINDOWS: dict[str, tuple[str, str] | None] = {
    "ALL": None,
    "Y1": ("2023-05-30", "2024-05-30"),
    "Y2": ("2024-05-30", "2025-05-30"),
    "Y3": ("2025-05-30", "2026-05-30"),
    "R18-1": ("2023-05-30", "2024-11-30"),
    "R18-2": ("2023-11-30", "2025-05-30"),
    "R18-3": ("2024-05-30", "2025-11-30"),
    "R18-4": ("2024-11-30", "2026-05-30"),
}
PREV_HORIZONS = (3, 5, 10, 20)
FWD_HORIZONS = (1, 3, 5, 10)


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose industry pulse persistence for trade replacements.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--baseline-run", required=True, help="Baseline portfolio run id.")
    parser.add_argument("--candidate-run", required=True, help="Candidate portfolio run id.")
    parser.add_argument("--windows", default="ALL,Y1,R18-1,Y3,R18-4", help="Comma-separated window labels.")
    args = parser.parse_args()

    started_at = now_text()
    baseline = read_json(RUNS_ROOT / args.baseline_run / "results.json")
    candidate = read_json(RUNS_ROOT / args.candidate_run / "results.json")
    window_mode = bool(baseline.get("windows")) and bool(candidate.get("windows"))
    baseline_trades = all_completed_trades(baseline)
    candidate_trades = all_completed_trades(candidate)
    trades = baseline_trades + candidate_trades
    if not trades:
        raise SystemExit("No completed trades found.")

    industries = sorted({str(trade.get("industry") or "") for trade in trades if trade.get("industry")})
    min_entry = min(date.fromisoformat(str(trade["entryDate"])) for trade in trades)
    max_entry = max(date.fromisoformat(str(trade["entryDate"])) for trade in trades)
    with SessionLocal() as db:
        industry_returns = query_industry_returns(db, industries, min_entry - timedelta(days=90), max_entry + timedelta(days=30))

    requested_windows = [item.strip() for item in args.windows.split(",") if item.strip()]
    coverage_samples = [analyze_trade(trade, industry_returns) for trade in trades]
    if window_mode:
        windows = analyze_window_validation(requested_windows, baseline, candidate, industry_returns)
    else:
        baseline_samples = [analyze_trade(trade, industry_returns) for trade in baseline_trades]
        candidate_samples = [analyze_trade(trade, industry_returns) for trade in candidate_trades]
        windows = [
            analyze_window(label, WINDOWS[label], baseline_samples, candidate_samples)
            for label in requested_windows
            if label in WINDOWS
        ]
    output = {
        "runId": args.run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "baselineRun": args.baseline_run,
        "candidateRun": args.candidate_run,
        "mode": "window_validation" if window_mode else "full_run",
        "prevHorizons": PREV_HORIZONS,
        "forwardHorizons": FWD_HORIZONS,
        "coverage": coverage_summary(coverage_samples),
        "windows": windows,
    }

    run_dir = RUNS_ROOT / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "results.json", json_safe(output))
    write_text(run_dir / "review.md", render_review(output))
    print(json.dumps({"runId": args.run_id, "coverage": output["coverage"], "summary": compact_summary(windows), "runDir": str(run_dir)}, ensure_ascii=False, indent=2))


def completed_trades(run: dict[str, Any]) -> list[dict[str, Any]]:
    return list((run.get("result") or {}).get("completedTrades") or [])


def all_completed_trades(run: dict[str, Any]) -> list[dict[str, Any]]:
    if not run.get("windows"):
        return completed_trades(run)
    trades: list[dict[str, Any]] = []
    for window in run.get("windows", []):
        trades.extend((window.get("result") or {}).get("completedTrades") or [])
    return trades


def query_industry_returns(db: Any, industries: list[str], start_date: date, end_date: date) -> dict[str, list[dict[str, Any]]]:
    stmt = (
        select(
            Stock.industry,
            StockDailyBar.trade_date,
            func.avg(StockDailyBar.pct_chg).label("avg_pct_chg"),
            func.count(StockDailyBar.ts_code).label("sample_count"),
        )
        .join(Stock, Stock.ts_code == StockDailyBar.ts_code)
        .where(
            Stock.industry.in_(industries),
            StockDailyBar.trade_date >= start_date,
            StockDailyBar.trade_date <= end_date,
            StockDailyBar.pct_chg.is_not(None),
        )
        .group_by(Stock.industry, StockDailyBar.trade_date)
        .order_by(Stock.industry, StockDailyBar.trade_date)
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in db.execute(stmt):
        avg_pct_chg = float(row.avg_pct_chg) if row.avg_pct_chg is not None else None
        if not row.industry or not finite(avg_pct_chg):
            continue
        grouped[str(row.industry)].append(
            {
                "date": row.trade_date.isoformat(),
                "returnPct": float(avg_pct_chg) / 100.0,
                "sampleCount": int(row.sample_count or 0),
            }
        )
    return dict(grouped)


def analyze_trade(trade: dict[str, Any], industry_returns: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    industry = str(trade.get("industry") or "")
    entry_date = str(trade["entryDate"])
    series = industry_returns.get(industry, [])
    index_by_date = {item["date"]: index for index, item in enumerate(series)}
    entry_index = index_by_date.get(entry_date)
    sample: dict[str, Any] = {
        "ts_code": trade.get("ts_code"),
        "name": trade.get("name"),
        "industry": industry,
        "entryDate": entry_date,
        "exitDate": trade.get("exitDate"),
        "actualReturnPct": trade.get("returnPct"),
        "netPnl": trade.get("netPnl"),
        "exitPriceRule": trade.get("exitPriceRule"),
        "industrySeriesAvailable": entry_index is not None,
    }
    if entry_index is None:
        return sample
    sample["industrySampleCountAtEntry"] = series[entry_index].get("sampleCount")
    for horizon in PREV_HORIZONS:
        prior = series[max(0, entry_index - horizon + 1) : entry_index + 1]
        sample[f"industryPrev{horizon}dPct"] = cumulative_return(prior)
        sample[f"industryPrev{horizon}dUpRatio"] = up_ratio(prior)
    for horizon in FWD_HORIZONS:
        future = series[entry_index + 1 : entry_index + 1 + horizon]
        sample[f"industryFwd{horizon}dPct"] = cumulative_return(future)
        sample[f"industryFwd{horizon}dUpRatio"] = up_ratio(future)
    sample["industryPulseDecay5dPct"] = subtract(sample.get("industryFwd5dPct"), sample.get("industryPrev5dPct"))
    sample["industryPulseDecay10dPct"] = subtract(sample.get("industryFwd10dPct"), sample.get("industryPrev10dPct"))
    sample["industryPositivePrev5ThenNegativeFwd5"] = bool(
        finite(sample.get("industryPrev5dPct"))
        and finite(sample.get("industryFwd5dPct"))
        and float(sample["industryPrev5dPct"]) > 0
        and float(sample["industryFwd5dPct"]) < 0
    )
    return sample


def cumulative_return(items: list[dict[str, Any]]) -> float | None:
    if not items:
        return None
    value = 1.0
    for item in items:
        if not finite(item.get("returnPct")):
            return None
        value *= 1.0 + float(item["returnPct"])
    return value - 1.0


def up_ratio(items: list[dict[str, Any]]) -> float | None:
    if not items:
        return None
    valid = [float(item["returnPct"]) for item in items if finite(item.get("returnPct"))]
    return sum(1 for value in valid if value > 0) / len(valid) if valid else None


def subtract(left: Any, right: Any) -> float | None:
    if not finite(left) or not finite(right):
        return None
    return float(left) - float(right)


def analyze_window(
    label: str,
    bounds: tuple[str, str] | None,
    baseline_samples: list[dict[str, Any]],
    candidate_samples: list[dict[str, Any]],
) -> dict[str, Any]:
    return analyze_replacement(
        label,
        select_window(baseline_samples, bounds),
        select_window(candidate_samples, bounds),
    )


def analyze_window_validation(
    labels: list[str],
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    industry_returns: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    baseline_windows = {item["window"]["label"]: item for item in baseline.get("windows", [])}
    candidate_windows = {item["window"]["label"]: item for item in candidate.get("windows", [])}
    windows = []
    if "ALL" in labels:
        windows.append(
            analyze_replacement(
                "ALL",
                [analyze_trade(trade, industry_returns) for trade in all_completed_trades(baseline)],
                [analyze_trade(trade, industry_returns) for trade in all_completed_trades(candidate)],
            )
        )
    for label in labels:
        if label == "ALL":
            continue
        baseline_window = baseline_windows.get(label)
        candidate_window = candidate_windows.get(label)
        if not baseline_window or not candidate_window:
            continue
        windows.append(
            analyze_replacement(
                label,
                [analyze_trade(trade, industry_returns) for trade in (baseline_window.get("result") or {}).get("completedTrades", [])],
                [analyze_trade(trade, industry_returns) for trade in (candidate_window.get("result") or {}).get("completedTrades", [])],
            )
        )
    return windows


def analyze_replacement(label: str, baseline_samples: list[dict[str, Any]], candidate_samples: list[dict[str, Any]]) -> dict[str, Any]:
    baseline_selected = keyed_samples(baseline_samples)
    candidate_selected = keyed_samples(candidate_samples)
    common_keys = set(baseline_selected) & set(candidate_selected)
    baseline_only = [baseline_selected[key] for key in sorted(set(baseline_selected) - set(candidate_selected))]
    candidate_only = [candidate_selected[key] for key in sorted(set(candidate_selected) - set(baseline_selected))]
    return {
        "label": label,
        "baselineTradeCount": len(baseline_selected),
        "candidateTradeCount": len(candidate_selected),
        "commonTradeCount": len(common_keys),
        "baselineOnly": summarize_samples(baseline_only),
        "candidateOnly": summarize_samples(candidate_only),
        "replacementNetPnlDelta": sum_value(candidate_only, "netPnl") - sum_value(baseline_only, "netPnl"),
        "baselineOnlyWorst": sorted_examples(baseline_only, reverse=False),
        "candidateOnlyWorst": sorted_examples(candidate_only, reverse=False),
        "candidateOnlyBest": sorted_examples(candidate_only, reverse=True),
    }


def keyed_samples(samples: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for index, sample in enumerate(samples):
        key = f"{sample.get('ts_code')}|{sample.get('entryDate')}"
        if key in result:
            key = f"{key}|{index}"
        result[key] = sample
    return result


def select_window(samples: list[dict[str, Any]], bounds: tuple[str, str] | None) -> list[dict[str, Any]]:
    if not bounds:
        return samples
    start = date.fromisoformat(bounds[0])
    end = date.fromisoformat(bounds[1])
    return [sample for sample in samples if start <= date.fromisoformat(str(sample["entryDate"])) <= end]


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "count": len(samples),
        "netPnl": sum_value(samples, "netPnl"),
        "avgActualReturnPct": avg(sample.get("actualReturnPct") for sample in samples),
        "winRate": sum(1 for sample in samples if float(sample.get("actualReturnPct") or 0) > 0) / len(samples) if samples else None,
        "coverage": coverage_summary(samples),
    }
    for horizon in PREV_HORIZONS:
        result[f"avgIndustryPrev{horizon}dPct"] = avg(sample.get(f"industryPrev{horizon}dPct") for sample in samples)
        result[f"avgIndustryPrev{horizon}dUpRatio"] = avg(sample.get(f"industryPrev{horizon}dUpRatio") for sample in samples)
    for horizon in FWD_HORIZONS:
        result[f"avgIndustryFwd{horizon}dPct"] = avg(sample.get(f"industryFwd{horizon}dPct") for sample in samples)
        result[f"avgIndustryFwd{horizon}dUpRatio"] = avg(sample.get(f"industryFwd{horizon}dUpRatio") for sample in samples)
    result["avgIndustryPulseDecay5dPct"] = avg(sample.get("industryPulseDecay5dPct") for sample in samples)
    result["avgIndustryPulseDecay10dPct"] = avg(sample.get("industryPulseDecay10dPct") for sample in samples)
    reversal_flags = [sample.get("industryPositivePrev5ThenNegativeFwd5") for sample in samples if sample.get("industrySeriesAvailable")]
    result["positivePrev5NegativeFwd5Rate"] = sum(1 for item in reversal_flags if item) / len(reversal_flags) if reversal_flags else None
    return result


def coverage_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(samples),
        "industrySeriesAvailable": sum(1 for sample in samples if sample.get("industrySeriesAvailable")),
    }


def sorted_examples(samples: list[dict[str, Any]], reverse: bool) -> list[dict[str, Any]]:
    selected = sorted(samples, key=lambda sample: float(sample.get("actualReturnPct") or 0), reverse=reverse)[:5]
    keys = [
        "ts_code",
        "name",
        "industry",
        "entryDate",
        "exitDate",
        "actualReturnPct",
        "netPnl",
        "exitPriceRule",
        "industryPrev5dPct",
        "industryFwd5dPct",
        "industryPulseDecay5dPct",
        "industryPositivePrev5ThenNegativeFwd5",
    ]
    return [{key: sample.get(key) for key in keys} for sample in selected]


def sum_value(samples: list[dict[str, Any]], key: str) -> float:
    return sum(float(sample.get(key) or 0.0) for sample in samples)


def avg(values: Any) -> float | None:
    selected = [float(value) for value in values if finite(value)]
    return mean(selected) if selected else None


def compact_summary(windows: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for window in windows:
        if window["label"] in {"ALL", "Y1", "R18-1", "Y3", "R18-4"}:
            result[window["label"]] = {
                "replacementNetPnlDelta": window["replacementNetPnlDelta"],
                "candidateOnly": {
                    "count": window["candidateOnly"]["count"],
                    "avgIndustryPrev5dPct": window["candidateOnly"].get("avgIndustryPrev5dPct"),
                    "avgIndustryFwd5dPct": window["candidateOnly"].get("avgIndustryFwd5dPct"),
                    "avgIndustryPulseDecay5dPct": window["candidateOnly"].get("avgIndustryPulseDecay5dPct"),
                    "positivePrev5NegativeFwd5Rate": window["candidateOnly"].get("positivePrev5NegativeFwd5Rate"),
                },
            }
    return result


def render_review(output: dict[str, Any]) -> str:
    lines = [
        f"# {output['runId']} Industry Pulse Persistence Diagnostic",
        "",
        f"- Baseline: `{output['baselineRun']}`",
        f"- Candidate: `{output['candidateRun']}`",
        f"- Coverage: `{output['coverage']['industrySeriesAvailable']}/{output['coverage']['count']}` trades with industry series.",
        "",
        "| Window | Replacement PnL Delta | Candidate-only | Cand prev5 | Cand fwd5 | Cand decay5 | Cand prev+ / fwd- rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for window in output["windows"]:
        candidate = window["candidateOnly"]
        lines.append(
            "| {label} | {delta:.2f} | {count} | {prev5} | {fwd5} | {decay5} | {reversal} |".format(
                label=window["label"],
                delta=float(window["replacementNetPnlDelta"]),
                count=candidate["count"],
                prev5=format_optional_percent(candidate.get("avgIndustryPrev5dPct")),
                fwd5=format_optional_percent(candidate.get("avgIndustryFwd5dPct")),
                decay5=format_optional_percent(candidate.get("avgIndustryPulseDecay5dPct")),
                reversal=format_optional_percent(candidate.get("positivePrev5NegativeFwd5Rate")),
            )
        )
    lines.extend(["", "## Candidate-only worst examples", ""])
    for window in output["windows"]:
        if window["label"] not in {"ALL", "Y1", "R18-1", "Y3", "R18-4"}:
            continue
        lines.append(f"### {window['label']}")
        for item in window["candidateOnlyWorst"]:
            lines.append(
                "- `{code}` {name} {industry} {entry} return {ret}, industry prev5 {prev5}, fwd5 {fwd5}, decay5 {decay5}".format(
                    code=item.get("ts_code"),
                    name=item.get("name") or "",
                    industry=item.get("industry") or "",
                    entry=item.get("entryDate"),
                    ret=format_optional_percent(item.get("actualReturnPct")),
                    prev5=format_optional_percent(item.get("industryPrev5dPct")),
                    fwd5=format_optional_percent(item.get("industryFwd5dPct")),
                    decay5=format_optional_percent(item.get("industryPulseDecay5dPct")),
                )
            )
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
