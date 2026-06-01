from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import finite, json_safe
from backend.app.database import SessionLocal
from scripts.research.analyze_preentry_path import (
    METRIC_KEYS,
    WINDOWS,
    annotate_trade,
    largest_separators,
    query_enriched_bars,
    select_window,
    sorted_samples,
    strategy_config,
    summarize_samples,
)
from scripts.research.run_portfolio_backtest import load_moneyflow_cache
from scripts.research.run_research_round import RUNS_ROOT, now_text, read_json, write_json, write_text


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose pre-entry paths for all completed trades in one portfolio run.")
    parser.add_argument("--run-id", required=True, help="Output diagnostic run id under docs/research/runs.")
    parser.add_argument("--source-run", required=True, help="Portfolio run with completedTrades.")
    parser.add_argument("--moneyflow-cache", default="docs/research/runs/002-moneyflow-cache-mainline-001/moneyflow-cache.jsonl")
    args = parser.parse_args()

    started_at = now_text()
    run_dir = RUNS_ROOT / args.run_id
    if (run_dir / "results.json").exists():
        raise SystemExit(f"Run already exists: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)

    source = read_json(RUNS_ROOT / args.source_run / "results.json")
    trades = list(source.get("result", {}).get("completedTrades", []))
    if not trades:
        raise SystemExit(f"No completed trades found in {args.source_run}")

    moneyflow_cache = load_moneyflow_cache(args.moneyflow_cache) if args.moneyflow_cache else {}
    cfg = strategy_config(source)
    ts_codes = sorted({str(trade["ts_code"]) for trade in trades})
    min_entry = min(date.fromisoformat(str(trade["entryDate"])) for trade in trades)
    max_entry = max(date.fromisoformat(str(trade["entryDate"])) for trade in trades)

    with SessionLocal() as db:
        bars_by_code = query_enriched_bars(db, ts_codes, min_entry - timedelta(days=260), max_entry, cfg, moneyflow_cache)

    samples = [annotate_trade(trade, bars_by_code.get(str(trade["ts_code"]), [])) for trade in trades]
    output = {
        "runId": args.run_id,
        "sourceRun": args.source_run,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "windows": {
            label: summarize_window(samples, bounds)
            for label, bounds in [("ALL", None), *WINDOWS.items()]
        },
        "thresholdScreens": screen_thresholds(samples),
    }

    write_json(run_dir / "results.json", json_safe(output))
    write_text(run_dir / "review.md", render_review(output))
    print(json.dumps({"runId": args.run_id, "summary": compact_summary(output), "runDir": str(run_dir)}, ensure_ascii=False, indent=2))


def summarize_window(samples: list[dict[str, Any]], bounds: tuple[str, str] | None) -> dict[str, Any]:
    subset = select_window(samples, bounds)
    losses = [item for item in subset if float(item.get("returnPct") or 0) <= 0]
    wins = [item for item in subset if float(item.get("returnPct") or 0) > 0]
    return {
        "trades": summarize_samples(subset),
        "losses": summarize_samples(losses),
        "wins": summarize_samples(wins),
        "lossVsWinMetricDelta": metric_delta(losses, wins),
        "largestSeparators": largest_separators(losses, wins),
        "lossSamples": sorted_samples(losses, reverse=False),
        "winSamples": sorted_samples(wins, reverse=True),
    }


def metric_delta(losses: list[dict[str, Any]], wins: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for key in METRIC_KEYS:
        loss_mean = mean_value([item.get(key) for item in losses])
        win_mean = mean_value([item.get(key) for item in wins])
        result[key] = None if loss_mean is None or win_mean is None else loss_mean - win_mean
    return result


def screen_thresholds(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    screens = []
    for metric in METRIC_KEYS:
        values = sorted({float(item[metric]) for item in samples if finite(item.get(metric))})
        if len(values) < 20:
            continue
        for quantile in (0.2, 0.3, 0.4, 0.6, 0.7, 0.8):
            threshold = values[min(len(values) - 1, max(0, int((len(values) - 1) * quantile)))]
            for operator in ("<=", ">="):
                hits = [item for item in samples if compare(item.get(metric), operator, threshold)]
                if not 8 <= len(hits) <= 90:
                    continue
                screens.append(
                    {
                        "metric": metric,
                        "operator": operator,
                        "threshold": threshold,
                        "ALL": summarize_hits(hits),
                        "Y1": summarize_hits(select_window(hits, WINDOWS["Y1"])),
                        "R18-1": summarize_hits(select_window(hits, WINDOWS["R18-1"])),
                        "R18-4": summarize_hits(select_window(hits, WINDOWS["R18-4"])),
                    }
                )
    return sorted(
        screens,
        key=lambda item: (
            float(item["Y1"]["netPnl"]) + float(item["R18-1"]["netPnl"]),
            float(item["ALL"]["netPnl"]),
        ),
    )[:30]


def summarize_hits(samples: list[dict[str, Any]]) -> dict[str, Any]:
    net_pnl = sum(float(item.get("netPnl") or 0.0) for item in samples)
    return {
        "count": len(samples),
        "netPnl": net_pnl,
        "avgReturnPct": mean_value([item.get("returnPct") for item in samples]),
        "winRate": sum(1 for item in samples if float(item.get("returnPct") or 0) > 0) / len(samples) if samples else None,
    }


def compare(value: Any, operator: str, threshold: float) -> bool:
    if not finite(value):
        return False
    return float(value) <= threshold if operator == "<=" else float(value) >= threshold


def mean_value(values: list[Any]) -> float | None:
    selected = [float(value) for value in values if finite(value)]
    return sum(selected) / len(selected) if selected else None


def render_review(output: dict[str, Any]) -> str:
    lines = [
        f"# {output['runId']} 单 run 入场前路径诊断",
        "",
        f"- 来源 run：`{output['sourceRun']}`",
        f"- 开始时间：{output['startedAt']}",
        f"- 结束时间：{output['finishedAt']}",
        "",
        "## 亏损 vs 盈利最大均值差",
        "",
        "| 窗口 | 交易 | 亏损 | 盈利 | 亏损净额 | 盈利净额 | 前三差异指标 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for label in ["ALL", "Y1", "Y2", "Y3", "R18-1", "R18-2", "R18-3", "R18-4"]:
        item = output["windows"][label]
        separators = ", ".join(
            f"{row['metric']} {float(row['lossMinusWinMean']):+.4f}"
            for row in item["largestSeparators"][:3]
        )
        lines.append(
            f"| `{label}` | `{item['trades']['count']}` | `{item['losses']['count']}` | `{item['wins']['count']}` | "
            f"{format_number(item['losses']['netPnl'])} | {format_number(item['wins']['netPnl'])} | {separators or 'NA'} |"
        )

    lines.extend(["", "## 阈值筛查", "", "| 条件 | ALL | Y1 | R18-1 | R18-4 |", "| --- | ---: | ---: | ---: | ---: |"])
    for item in output["thresholdScreens"][:12]:
        condition = f"`{item['metric']}` {item['operator']} {format_number(item['threshold'])}"
        lines.append(
            f"| {condition} | {format_hit(item['ALL'])} | {format_hit(item['Y1'])} | {format_hit(item['R18-1'])} | {format_hit(item['R18-4'])} |"
        )

    lines.extend(
        [
            "",
            "## 结论提示",
            "",
            "- 本诊断只读取已完成交易的入场前路径，不改变买入、退出、仓位、成本或成交语义。",
            "- 阈值筛查只用于寻找候选风险标签；任何条件都必须再经过默认关闭参数、完整三年回测和滚动验证。",
        ]
    )
    return "\n".join(lines) + "\n"


def compact_summary(output: dict[str, Any]) -> dict[str, Any]:
    return {
        label: {
            "trades": item["trades"]["count"],
            "losses": item["losses"]["count"],
            "topSeparators": item["largestSeparators"][:3],
        }
        for label, item in output["windows"].items()
    }


def format_number(value: Any) -> str:
    if not finite(value):
        return "n/a"
    return f"`{float(value):.2f}`"


def format_hit(item: dict[str, Any]) -> str:
    return f"`{item['count']} / {item['netPnl']:.2f}`"


if __name__ == "__main__":
    main()
