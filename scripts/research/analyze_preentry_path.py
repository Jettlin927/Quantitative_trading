from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any

from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import enrich_rows, finite, json_safe, normalize_config
from backend.app.database import SessionLocal
from backend.app.models import StockDailyBar
from scripts.research.analyze_trade_delta import keyed_trades, select_window
from scripts.research.run_portfolio_backtest import attach_amount_features, attach_moneyflow_features, load_moneyflow_cache
from scripts.research.run_research_round import RUNS_ROOT, now_text, read_json, write_json, write_text


WINDOWS = {
    "Y1": ("2023-05-30", "2024-05-30"),
    "Y2": ("2024-05-30", "2025-05-30"),
    "Y3": ("2025-05-30", "2026-05-30"),
    "R18-1": ("2023-05-30", "2024-11-30"),
    "R18-2": ("2023-11-30", "2025-05-30"),
    "R18-3": ("2024-05-30", "2025-11-30"),
    "R18-4": ("2024-11-30", "2026-05-30"),
}

METRIC_KEYS = [
    "closeReturn1d",
    "closeReturn3d",
    "closeReturn5d",
    "closeReturn10d",
    "amountRatio",
    "volumeRatio",
    "macdHist",
    "macdHistDelta1d",
    "macdHistDelta3d",
    "bollBandwidthPct",
    "bollPosition",
    "bollPositionDelta3d",
    "rsiStrategy",
    "rsiDelta3d",
    "ma20ExtensionPct",
    "maAlignment",
    "entryGapPct",
    "entryRangePct",
    "upperShadowPct",
    "priorGapDown3Count60",
    "moneyflowMainNetRank1",
    "moneyflowMainNetRank3",
    "moneyflowMainNetRank5",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose pre-entry price/volume/indicator/moneyflow paths around replacement trades.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--baseline-run", required=True, help="Baseline portfolio run id.")
    parser.add_argument("--candidate-run", required=True, help="Candidate portfolio run id.")
    parser.add_argument("--moneyflow-cache", default="docs/research/runs/002-moneyflow-cache-mainline-001/moneyflow-cache.jsonl")
    args = parser.parse_args()

    started_at = now_text()
    run_dir = RUNS_ROOT / args.run_id
    if (run_dir / "results.json").exists():
        raise SystemExit(f"Run already exists: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)

    baseline = read_json(RUNS_ROOT / args.baseline_run / "results.json")
    candidate = read_json(RUNS_ROOT / args.candidate_run / "results.json")
    moneyflow_cache = load_moneyflow_cache(args.moneyflow_cache) if args.moneyflow_cache else {}
    cfg = strategy_config(candidate) or strategy_config(baseline)

    all_trades = collect_replacement_trades(baseline, candidate)
    ts_codes = sorted({str(trade["ts_code"]) for trade in all_trades})
    min_entry = min(date.fromisoformat(str(trade["entryDate"])) for trade in all_trades)
    max_entry = max(date.fromisoformat(str(trade["entryDate"])) for trade in all_trades)

    with SessionLocal() as db:
        bars_by_code = query_enriched_bars(db, ts_codes, min_entry - timedelta(days=260), max_entry, cfg, moneyflow_cache)

    if "windows" in baseline and "windows" in candidate:
        output = analyze_window_runs(args, baseline, candidate, bars_by_code, started_at)
    else:
        output = analyze_full_runs(args, baseline, candidate, bars_by_code, started_at)

    write_json(run_dir / "results.json", json_safe(output))
    write_text(run_dir / "review.md", render_review(output))
    print(json.dumps({"runId": args.run_id, "summary": compact_summary(output), "runDir": str(run_dir)}, ensure_ascii=False, indent=2))


def strategy_config(run: dict[str, Any]) -> dict[str, Any]:
    strategy = run.get("strategy") or {}
    return normalize_config(strategy.get("config") or {})


def collect_replacement_trades(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    if "windows" in baseline and "windows" in candidate:
        trades: list[dict[str, Any]] = []
        candidate_windows = {item["window"]["label"]: item for item in candidate.get("windows", [])}
        for baseline_window in baseline.get("windows", []):
            candidate_window = candidate_windows.get(baseline_window["window"]["label"])
            if not candidate_window:
                continue
            trades.extend(replacement_trades(baseline_window["result"].get("completedTrades", []), candidate_window["result"].get("completedTrades", [])))
        return trades
    return replacement_trades(baseline["result"].get("completedTrades", []), candidate["result"].get("completedTrades", []))


def replacement_trades(baseline_trades: list[dict[str, Any]], candidate_trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    baseline_keyed = keyed_trades(baseline_trades)
    candidate_keyed = keyed_trades(candidate_trades)
    baseline_only = [baseline_keyed[key] for key in set(baseline_keyed) - set(candidate_keyed)]
    candidate_only = [candidate_keyed[key] for key in set(candidate_keyed) - set(baseline_keyed)]
    return baseline_only + candidate_only


def query_enriched_bars(
    db: Any,
    ts_codes: list[str],
    start_date: date,
    end_date: date,
    cfg: dict[str, Any],
    moneyflow_cache: dict[str, dict[str, dict[str, float]]],
) -> dict[str, list[dict[str, Any]]]:
    stmt = (
        select(
            StockDailyBar.ts_code,
            StockDailyBar.trade_date,
            StockDailyBar.open,
            StockDailyBar.high,
            StockDailyBar.low,
            StockDailyBar.close,
            StockDailyBar.vol,
            StockDailyBar.amount,
            StockDailyBar.pct_chg,
        )
        .where(
            StockDailyBar.ts_code.in_(ts_codes),
            StockDailyBar.trade_date >= start_date,
            StockDailyBar.trade_date <= end_date,
        )
        .order_by(StockDailyBar.ts_code, StockDailyBar.trade_date)
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in db.execute(stmt):
        grouped[row.ts_code].append(
            {
                "date": row.trade_date.isoformat(),
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": float(row.vol) if row.vol is not None else float("nan"),
                "amount": float(row.amount) if row.amount is not None else float("nan"),
                "pctChg": float(row.pct_chg) if row.pct_chg is not None else float("nan"),
            }
        )
    result: dict[str, list[dict[str, Any]]] = {}
    for ts_code, rows in grouped.items():
        enriched = enrich_rows(rows, cfg)
        attach_amount_features(enriched)
        attach_moneyflow_features(enriched, ts_code, None, moneyflow_cache, {})
        result[ts_code] = enriched
    return result


def analyze_full_runs(args: argparse.Namespace, baseline: dict[str, Any], candidate: dict[str, Any], bars_by_code: dict[str, list[dict[str, Any]]], started_at: str) -> dict[str, Any]:
    baseline_trades = keyed_trades(baseline["result"].get("completedTrades", []))
    candidate_trades = keyed_trades(candidate["result"].get("completedTrades", []))
    baseline_only_keys = set(baseline_trades) - set(candidate_trades)
    candidate_only_keys = set(candidate_trades) - set(baseline_trades)
    comparisons = []
    for label, bounds in [("ALL", None), *WINDOWS.items()]:
        baseline_only = select_window([baseline_trades[key] for key in baseline_only_keys], bounds)
        candidate_only = select_window([candidate_trades[key] for key in candidate_only_keys], bounds)
        comparisons.append(build_comparison(label, baseline_only, candidate_only, bars_by_code))
    return {
        "runId": args.run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "baselineRun": args.baseline_run,
        "candidateRun": args.candidate_run,
        "mode": "full",
        "comparisons": comparisons,
    }


def analyze_window_runs(args: argparse.Namespace, baseline: dict[str, Any], candidate: dict[str, Any], bars_by_code: dict[str, list[dict[str, Any]]], started_at: str) -> dict[str, Any]:
    candidate_windows = {item["window"]["label"]: item for item in candidate.get("windows", [])}
    windows = []
    for baseline_window in baseline.get("windows", []):
        label = baseline_window["window"]["label"]
        candidate_window = candidate_windows.get(label)
        if not candidate_window:
            continue
        baseline_trades = keyed_trades(baseline_window["result"].get("completedTrades", []))
        candidate_trades = keyed_trades(candidate_window["result"].get("completedTrades", []))
        baseline_only = [baseline_trades[key] for key in set(baseline_trades) - set(candidate_trades)]
        candidate_only = [candidate_trades[key] for key in set(candidate_trades) - set(baseline_trades)]
        windows.append({"label": label, "window": baseline_window["window"], "comparison": build_comparison(label, baseline_only, candidate_only, bars_by_code)})
    return {
        "runId": args.run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "baselineRun": args.baseline_run,
        "candidateRun": args.candidate_run,
        "mode": "window_validation",
        "windows": windows,
    }


def build_comparison(label: str, baseline_only: list[dict[str, Any]], candidate_only: list[dict[str, Any]], bars_by_code: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    baseline_samples = [annotate_trade(trade, bars_by_code.get(str(trade["ts_code"]), [])) for trade in baseline_only]
    candidate_samples = [annotate_trade(trade, bars_by_code.get(str(trade["ts_code"]), [])) for trade in candidate_only]
    candidate_losses = [item for item in candidate_samples if float(item.get("returnPct") or 0) < 0]
    candidate_wins = [item for item in candidate_samples if float(item.get("returnPct") or 0) > 0]
    return {
        "label": label,
        "replacementNetPnlDelta": sum_value(candidate_samples, "netPnl") - sum_value(baseline_samples, "netPnl"),
        "baselineOnly": summarize_samples(baseline_samples),
        "candidateOnly": summarize_samples(candidate_samples),
        "candidateOnlyLosses": summarize_samples(candidate_losses),
        "candidateOnlyWins": summarize_samples(candidate_wins),
        "lossVsWinMetricDelta": metric_delta(candidate_losses, candidate_wins),
        "largestSeparators": largest_separators(candidate_losses, candidate_wins),
        "candidateLossSamples": sorted_samples(candidate_losses, reverse=False),
        "candidateWinSamples": sorted_samples(candidate_wins, reverse=True),
    }


def annotate_trade(trade: dict[str, Any], bars: list[dict[str, Any]]) -> dict[str, Any]:
    entry_date = str(trade["entryDate"])
    by_date = {bar["date"]: index for index, bar in enumerate(bars)}
    index = by_date.get(entry_date)
    metrics = preentry_metrics(bars, index) if index is not None else {}
    return {
        "ts_code": trade.get("ts_code"),
        "name": trade.get("name"),
        "industry": trade.get("industry"),
        "entryDate": entry_date,
        "exitDate": trade.get("exitDate"),
        "returnPct": trade.get("returnPct"),
        "netPnl": trade.get("netPnl"),
        "exitPriceRule": trade.get("exitPriceRule"),
        "pathMatched": index is not None,
        **metrics,
    }


def preentry_metrics(bars: list[dict[str, Any]], index: int) -> dict[str, Any]:
    row = bars[index]
    prev = bars[index - 1] if index > 0 else None
    metrics: dict[str, Any] = {
        "entryGapPct": row["open"] / prev["close"] - 1 if prev and prev.get("close") else None,
        "entryIntradayPct": row["close"] / row["open"] - 1 if row.get("open") else None,
        "entryRangePct": (row["high"] - row["low"]) / row["close"] if row.get("close") else None,
        "upperShadowPct": (row["high"] - max(row["open"], row["close"])) / row["close"] if row.get("close") else None,
        "lowerShadowPct": (min(row["open"], row["close"]) - row["low"]) / row["close"] if row.get("close") else None,
        "amountRatio": row.get("amountRatio"),
        "volumeRatio": row["volume"] / row["volMa"] if row.get("volume") and finite(row.get("volMa")) and row["volMa"] else None,
        "macdHist": row.get("macdHist"),
        "bollBandwidthPct": row.get("bollBandwidthPct"),
        "bollPosition": boll_position(row),
        "rsiStrategy": row.get("rsiStrategy"),
        "ma20ExtensionPct": row["close"] / row["ma20"] - 1 if finite(row.get("ma20")) and row["ma20"] else None,
        "maAlignment": ma_alignment(row),
        "priorGapDown3Count60": row.get("priorGapDown3Count60"),
        "moneyflowMainNetRank1": row.get("moneyflowMainNetRank1"),
        "moneyflowMainNetRank3": row.get("moneyflowMainNetRank3"),
        "moneyflowMainNetRank5": row.get("moneyflowMainNetRank5"),
    }
    for horizon in (1, 3, 5, 10):
        past_index = index - horizon
        past = bars[past_index] if past_index >= 0 else None
        metrics[f"closeReturn{horizon}d"] = row["close"] / past["close"] - 1 if past and past.get("close") else None
    for horizon in (1, 3):
        past_index = index - horizon
        past = bars[past_index] if past_index >= 0 else None
        metrics[f"macdHistDelta{horizon}d"] = diff(row.get("macdHist"), past.get("macdHist") if past else None)
        metrics[f"rsiDelta{horizon}d"] = diff(row.get("rsiStrategy"), past.get("rsiStrategy") if past else None)
        metrics[f"bollPositionDelta{horizon}d"] = diff(boll_position(row), boll_position(past) if past else None)
    return metrics


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(samples),
        "matchedCount": sum(1 for item in samples if item.get("pathMatched")),
        "netPnl": sum_value(samples, "netPnl"),
        "avgReturnPct": avg([item.get("returnPct") for item in samples]),
        "winRate": rate(samples, lambda item: float(item.get("returnPct") or 0) > 0),
        "metricMeans": {key: avg([item.get(key) for item in samples]) for key in METRIC_KEYS},
        "metricMedians": {key: med([item.get(key) for item in samples]) for key in METRIC_KEYS},
    }


def metric_delta(losses: list[dict[str, Any]], wins: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for key in METRIC_KEYS:
        loss_mean = avg([item.get(key) for item in losses])
        win_mean = avg([item.get(key) for item in wins])
        result[key] = None if loss_mean is None or win_mean is None else loss_mean - win_mean
    return result


def largest_separators(losses: list[dict[str, Any]], wins: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    deltas = metric_delta(losses, wins)
    for key, delta in deltas.items():
        if delta is not None:
            rows.append({"metric": key, "lossMinusWinMean": delta, "lossMean": avg([item.get(key) for item in losses]), "winMean": avg([item.get(key) for item in wins])})
    return sorted(rows, key=lambda item: abs(float(item["lossMinusWinMean"])), reverse=True)[:8]


def sorted_samples(samples: list[dict[str, Any]], reverse: bool) -> list[dict[str, Any]]:
    selected = sorted(samples, key=lambda item: float(item.get("returnPct") or 0), reverse=reverse)[:6]
    return [
        {
            "ts_code": item.get("ts_code"),
            "name": item.get("name"),
            "entryDate": item.get("entryDate"),
            "returnPct": item.get("returnPct"),
            "netPnl": item.get("netPnl"),
            "exitPriceRule": item.get("exitPriceRule"),
            "metrics": {key: item.get(key) for key in METRIC_KEYS if finite(item.get(key))},
        }
        for item in selected
    ]


def boll_position(row: dict[str, Any] | None) -> float | None:
    if not row:
        return None
    mid = row.get("bollMid")
    upper = row.get("bollUpper")
    close = row.get("close")
    if not finite(mid) or not finite(upper) or not close:
        return None
    width = float(upper) - float(mid)
    return (float(close) - float(mid)) / width if width else None


def ma_alignment(row: dict[str, Any]) -> float | None:
    values = [row.get(key) for key in ["close", "ma5", "ma10", "ma20", "ma60"]]
    if any(not finite(value) or float(value) <= 0 for value in values):
        return None
    close, ma5, ma10, ma20, ma60 = [float(value) for value in values]
    return (close / ma20 - 1) + (ma5 / ma10 - 1) * 2 + (ma20 / ma60 - 1)


def diff(left: Any, right: Any) -> float | None:
    return float(left) - float(right) if finite(left) and finite(right) else None


def avg(values: list[Any]) -> float | None:
    selected = [float(value) for value in values if finite(value)]
    return mean(selected) if selected else None


def med(values: list[Any]) -> float | None:
    selected = [float(value) for value in values if finite(value)]
    return median(selected) if selected else None


def rate(samples: list[dict[str, Any]], predicate: Any) -> float | None:
    return sum(1 for item in samples if predicate(item)) / len(samples) if samples else None


def sum_value(samples: list[dict[str, Any]], key: str) -> float:
    return sum(float(item.get(key) or 0.0) for item in samples)


def render_review(output: dict[str, Any]) -> str:
    lines = [
        f"# {output['runId']}",
        "",
        "## 结论摘要",
        "",
        f"- baseline: `{output['baselineRun']}`",
        f"- candidate: `{output['candidateRun']}`",
        f"- mode: `{output['mode']}`",
        "",
        "## 候选独有亏损 vs 盈利最大均值差",
        "",
        "| 窗口 | 替换净差 | 候选独有 | 候选亏损 | 前三差异指标 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for item in comparison_items(output):
        separators = ", ".join(
            f"{row['metric']} {float(row['lossMinusWinMean']):+.4f}"
            for row in item["largestSeparators"][:3]
        )
        lines.append(
            f"| `{item['label']}` | `{item['replacementNetPnlDelta']:.2f}` | "
            f"`{item['candidateOnly']['count']}` | `{item['candidateOnlyLosses']['count']}` | {separators or 'NA'} |"
        )
    return "\n".join(lines) + "\n"


def comparison_items(output: dict[str, Any]) -> list[dict[str, Any]]:
    if output["mode"] == "window_validation":
        return [item["comparison"] for item in output["windows"]]
    return output["comparisons"]


def compact_summary(output: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for item in comparison_items(output):
        result[item["label"]] = {
            "replacementNetPnlDelta": item["replacementNetPnlDelta"],
            "candidateOnly": item["candidateOnly"]["count"],
            "candidateLosses": item["candidateOnlyLosses"]["count"],
            "topSeparators": item["largestSeparators"][:3],
        }
    return result


if __name__ == "__main__":
    main()
