from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from sqlalchemy import select

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

SUMMARY_KEYS = [
    "entryUpPct",
    "entryStrongUpPct",
    "entryStrongDownPct",
    "entryLimitUpLikePct",
    "entryLimitDownLikePct",
    "entryLimitImbalancePct",
    "entryDispersionPct",
    "prev1UpPct",
    "prev1StrongUpPct",
    "prev1StrongDownPct",
    "prev1LimitUpLikePct",
    "prev1LimitDownLikePct",
    "prev1LimitImbalancePct",
    "prev1DispersionPct",
    "prev3AvgUpPct",
    "prev3AvgLimitUpLikePct",
    "prev3AvgLimitDownLikePct",
    "prev3AvgLimitImbalancePct",
    "prev3AvgDispersionPct",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose point-in-time industry limit-up/down structure around trade replacements.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--baseline-run", required=True, help="Baseline portfolio run id.")
    parser.add_argument("--candidate-run", required=True, help="Candidate portfolio run id.")
    parser.add_argument("--windows", default="ALL,Y1,R18-1,Y3,R18-4", help="Comma-separated window labels.")
    args = parser.parse_args()

    started_at = now_text()
    baseline = read_json(RUNS_ROOT / args.baseline_run / "results.json")
    candidate = read_json(RUNS_ROOT / args.candidate_run / "results.json")
    window_mode = bool(baseline.get("windows")) and bool(candidate.get("windows"))
    trades = all_completed_trades(baseline) + all_completed_trades(candidate)
    if not trades:
        raise SystemExit("No completed trades found.")

    industries = sorted({str(trade.get("industry") or "") for trade in trades if trade.get("industry")})
    min_entry = min(date.fromisoformat(str(trade["entryDate"])) for trade in trades)
    max_entry = max(date.fromisoformat(str(trade["entryDate"])) for trade in trades)
    with SessionLocal() as db:
        industry_metrics = query_industry_metrics(db, industries, min_entry - timedelta(days=45), max_entry)

    requested_windows = [item.strip() for item in args.windows.split(",") if item.strip()]
    coverage_samples = [analyze_trade(trade, industry_metrics) for trade in trades]
    if window_mode:
        windows = analyze_window_validation(requested_windows, baseline, candidate, industry_metrics)
    else:
        baseline_samples = [analyze_trade(trade, industry_metrics) for trade in completed_trades(baseline)]
        candidate_samples = [analyze_trade(trade, industry_metrics) for trade in completed_trades(candidate)]
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
        "thresholds": {
            "strongUpPct": 0.07,
            "strongDownPct": -0.05,
            "limitUpLikePct": 0.095,
            "limitDownLikePct": -0.095,
        },
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


def query_industry_metrics(db: Any, industries: list[str], start_date: date, end_date: date) -> dict[str, list[dict[str, Any]]]:
    stmt = (
        select(Stock.industry, StockDailyBar.trade_date, StockDailyBar.pct_chg)
        .join(Stock, Stock.ts_code == StockDailyBar.ts_code)
        .where(
            Stock.industry.in_(industries),
            StockDailyBar.trade_date >= start_date,
            StockDailyBar.trade_date <= end_date,
            StockDailyBar.pct_chg.is_not(None),
        )
        .order_by(Stock.industry, StockDailyBar.trade_date)
    )
    grouped_values: dict[tuple[str, date], list[float]] = defaultdict(list)
    for row in db.execute(stmt):
        pct_chg = float(row.pct_chg) if row.pct_chg is not None else None
        if not row.industry or not finite(pct_chg):
            continue
        grouped_values[(str(row.industry), row.trade_date)].append(float(pct_chg) / 100.0)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (industry, trade_date), values in sorted(grouped_values.items(), key=lambda item: (item[0][0], item[0][1])):
        metrics = day_metrics(values)
        metrics["date"] = trade_date.isoformat()
        grouped[industry].append(metrics)
    return dict(grouped)


def day_metrics(values: list[float]) -> dict[str, Any]:
    sorted_values = sorted(value for value in values if finite(value))
    count = len(sorted_values)
    if count == 0:
        return {"sampleCount": 0}
    p10 = percentile(sorted_values, 0.10)
    p90 = percentile(sorted_values, 0.90)
    up_pct = ratio(sorted_values, lambda value: value > 0.0)
    down_pct = ratio(sorted_values, lambda value: value < 0.0)
    strong_up_pct = ratio(sorted_values, lambda value: value >= 0.07)
    strong_down_pct = ratio(sorted_values, lambda value: value <= -0.05)
    limit_up_like_pct = ratio(sorted_values, lambda value: value >= 0.095)
    limit_down_like_pct = ratio(sorted_values, lambda value: value <= -0.095)
    return {
        "sampleCount": count,
        "avgReturnPct": mean(sorted_values),
        "medianReturnPct": percentile(sorted_values, 0.50),
        "p10ReturnPct": p10,
        "p90ReturnPct": p90,
        "upPct": up_pct,
        "downPct": down_pct,
        "breadthImbalancePct": up_pct - down_pct,
        "strongUpPct": strong_up_pct,
        "strongDownPct": strong_down_pct,
        "limitUpLikePct": limit_up_like_pct,
        "limitDownLikePct": limit_down_like_pct,
        "limitImbalancePct": limit_up_like_pct - limit_down_like_pct,
        "dispersionPct": p90 - p10,
    }


def percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return float("nan")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def ratio(values: list[float], predicate: Any) -> float:
    return sum(1 for value in values if predicate(value)) / len(values) if values else float("nan")


def analyze_trade(trade: dict[str, Any], industry_metrics: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    industry = str(trade.get("industry") or "")
    entry_date = str(trade["entryDate"])
    series = industry_metrics.get(industry, [])
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
    attach_prefixed_metrics(sample, "entry", series[entry_index])
    if entry_index > 0:
        attach_prefixed_metrics(sample, "prev1", series[entry_index - 1])
    attach_average_metrics(sample, "prev3Avg", series[max(0, entry_index - 3) : entry_index])
    attach_average_metrics(sample, "prev5Avg", series[max(0, entry_index - 5) : entry_index])
    return sample


def attach_prefixed_metrics(sample: dict[str, Any], prefix: str, metrics: dict[str, Any]) -> None:
    for key, value in metrics.items():
        if key == "date":
            sample[f"{prefix}Date"] = value
        elif key == "sampleCount":
            sample[f"{prefix}SampleCount"] = value
        else:
            sample[f"{prefix}{key[0].upper()}{key[1:]}"] = value


def attach_average_metrics(sample: dict[str, Any], prefix: str, items: list[dict[str, Any]]) -> None:
    if not items:
        return
    keys = [key for key in items[0] if key not in {"date", "sampleCount"}]
    for key in keys:
        values = [float(item[key]) for item in items if finite(item.get(key))]
        if values:
            sample[f"{prefix}{key[0].upper()}{key[1:]}"] = mean(values)
    counts = [float(item["sampleCount"]) for item in items if finite(item.get("sampleCount"))]
    if counts:
        sample[f"{prefix}SampleCount"] = mean(counts)


def analyze_window(
    label: str,
    bounds: tuple[str, str] | None,
    baseline_samples: list[dict[str, Any]],
    candidate_samples: list[dict[str, Any]],
) -> dict[str, Any]:
    return analyze_replacement(label, select_window(baseline_samples, bounds), select_window(candidate_samples, bounds))


def analyze_window_validation(
    labels: list[str],
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    industry_metrics: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    baseline_windows = {item["window"]["label"]: item for item in baseline.get("windows", [])}
    candidate_windows = {item["window"]["label"]: item for item in candidate.get("windows", [])}
    windows = []
    if "ALL" in labels:
        windows.append(
            analyze_replacement(
                "ALL",
                [analyze_trade(trade, industry_metrics) for trade in all_completed_trades(baseline)],
                [analyze_trade(trade, industry_metrics) for trade in all_completed_trades(candidate)],
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
                [analyze_trade(trade, industry_metrics) for trade in (baseline_window.get("result") or {}).get("completedTrades", [])],
                [analyze_trade(trade, industry_metrics) for trade in (candidate_window.get("result") or {}).get("completedTrades", [])],
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
        "candidateOnlyWorst": sorted_examples(candidate_only, reverse=False),
        "candidateOnlyBest": sorted_examples(candidate_only, reverse=True),
    }


def keyed_samples(samples: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    return {
        (str(item.get("ts_code")), str(item.get("entryDate")), str(item.get("exitDate"))): item
        for item in samples
        if item.get("ts_code") and item.get("entryDate") and item.get("exitDate")
    }


def select_window(samples: list[dict[str, Any]], bounds: tuple[str, str] | None) -> list[dict[str, Any]]:
    if bounds is None:
        return samples
    start, end = [date.fromisoformat(item) for item in bounds]
    return [
        sample
        for sample in samples
        if sample.get("entryDate") and start <= date.fromisoformat(str(sample["entryDate"])) < end
    ]


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "count": len(samples),
        "netPnl": sum_value(samples, "netPnl"),
        "avgActualReturnPct": average(samples, "actualReturnPct"),
        "winRate": ratio_from_values([sample.get("actualReturnPct") for sample in samples], lambda value: value > 0),
        "coverage": coverage_summary(samples),
    }
    for key in SUMMARY_KEYS:
        summary[f"avg{key[0].upper()}{key[1:]}"] = average(samples, key)
    return summary


def sorted_examples(samples: list[dict[str, Any]], reverse: bool) -> list[dict[str, Any]]:
    selected = sorted(samples, key=lambda item: float(item.get("netPnl") or 0), reverse=reverse)[:5]
    return [
        {
            "ts_code": item.get("ts_code"),
            "name": item.get("name"),
            "industry": item.get("industry"),
            "entryDate": item.get("entryDate"),
            "exitDate": item.get("exitDate"),
            "actualReturnPct": item.get("actualReturnPct"),
            "netPnl": item.get("netPnl"),
            "exitPriceRule": item.get("exitPriceRule"),
            "entryUpPct": item.get("entryUpPct"),
            "entryLimitUpLikePct": item.get("entryLimitUpLikePct"),
            "entryLimitDownLikePct": item.get("entryLimitDownLikePct"),
            "prev1UpPct": item.get("prev1UpPct"),
            "prev1LimitUpLikePct": item.get("prev1LimitUpLikePct"),
            "prev1LimitDownLikePct": item.get("prev1LimitDownLikePct"),
            "prev3AvgLimitImbalancePct": item.get("prev3AvgLimitImbalancePct"),
        }
        for item in selected
    ]


def sum_value(samples: list[dict[str, Any]], key: str) -> float:
    return sum(float(sample.get(key) or 0.0) for sample in samples if finite(sample.get(key)))


def average(samples: list[dict[str, Any]], key: str) -> float | None:
    values = [float(sample[key]) for sample in samples if finite(sample.get(key))]
    return mean(values) if values else None


def ratio_from_values(values: list[Any], predicate: Any) -> float | None:
    valid = [float(value) for value in values if finite(value)]
    return sum(1 for value in valid if predicate(value)) / len(valid) if valid else None


def coverage_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(samples)
    covered = sum(1 for sample in samples if sample.get("industrySeriesAvailable"))
    return {"covered": covered, "total": total, "rate": covered / total if total else None}


def compact_summary(windows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        window["label"]: {
            "replacementNetPnlDelta": window["replacementNetPnlDelta"],
            "candidateOnlyCount": window["candidateOnly"]["count"],
            "candidateOnlyPrev1LimitUpLikePct": window["candidateOnly"].get("avgPrev1LimitUpLikePct"),
            "candidateOnlyPrev1LimitDownLikePct": window["candidateOnly"].get("avgPrev1LimitDownLikePct"),
            "candidateOnlyEntryLimitImbalancePct": window["candidateOnly"].get("avgEntryLimitImbalancePct"),
        }
        for window in windows
    }


def render_review(output: dict[str, Any]) -> str:
    lines = [
        f"# {output['runId']} 行业涨跌停结构诊断",
        "",
        f"- Baseline: `{output['baselineRun']}`",
        f"- Candidate: `{output['candidateRun']}`",
        f"- 模式：`{output['mode']}`",
        f"- 覆盖：`{output['coverage']['covered']}/{output['coverage']['total']}`",
        "",
        "## 替换汇总",
        "",
        "| 窗口 | 替换净差 | 候选独有 | 候选前日涨停类 | 候选前日跌停类 | 候选当日涨停类 | 候选当日跌停类 | 候选当日宽度 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for window in output["windows"]:
        candidate = window["candidateOnly"]
        lines.append(
            "| `{label}` | `{delta:.2f}` | `{count}` | {prev_lu} | {prev_ld} | {entry_lu} | {entry_ld} | {entry_up} |".format(
                label=window["label"],
                delta=float(window["replacementNetPnlDelta"]),
                count=candidate["count"],
                prev_lu=format_optional_percent(candidate.get("avgPrev1LimitUpLikePct")),
                prev_ld=format_optional_percent(candidate.get("avgPrev1LimitDownLikePct")),
                entry_lu=format_optional_percent(candidate.get("avgEntryLimitUpLikePct")),
                entry_ld=format_optional_percent(candidate.get("avgEntryLimitDownLikePct")),
                entry_up=format_optional_percent(candidate.get("avgEntryUpPct")),
            )
        )
    lines.extend(["", "## 候选独有最差样本", ""])
    for window in output["windows"]:
        if window["label"] not in {"ALL", "Y1", "R18-1", "Y3", "R18-4"}:
            continue
        lines.append(f"### {window['label']}")
        for item in window["candidateOnlyWorst"]:
            lines.append(
                "- `{code}` {name} {entry}->{exit} 收益 {ret}，净盈亏 `{pnl:.2f}`，"
                "前日涨停类 {prev_lu} / 跌停类 {prev_ld}，当日涨停类 {entry_lu} / 跌停类 {entry_ld}，当日上涨占比 {entry_up}。".format(
                    code=item.get("ts_code"),
                    name=item.get("name"),
                    entry=item.get("entryDate"),
                    exit=item.get("exitDate"),
                    ret=format_optional_percent(item.get("actualReturnPct")),
                    pnl=float(item.get("netPnl") or 0.0),
                    prev_lu=format_optional_percent(item.get("prev1LimitUpLikePct")),
                    prev_ld=format_optional_percent(item.get("prev1LimitDownLikePct")),
                    entry_lu=format_optional_percent(item.get("entryLimitUpLikePct")),
                    entry_ld=format_optional_percent(item.get("entryLimitDownLikePct")),
                    entry_up=format_optional_percent(item.get("entryUpPct")),
                )
            )
        lines.append("")
    lines.extend(
        [
            "## 结论提示",
            "",
            "- 若失败窗口候选独有坏样本的前日跌停类/强跌占比显著高于赢家，可转成 point-in-time 风险扣分或门控。",
            "- 若只有入场当日结构有解释力，需要确认当前引擎是否允许使用收盘后排序；否则只能作为复盘解释。",
            "- 若候选独有与基准独有结构差异很小，不应新增涨跌停结构因子。",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
