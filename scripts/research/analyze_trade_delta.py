from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import finite, json_safe
from scripts.research.run_research_round import RUNS_ROOT, format_optional_percent, now_text, read_json, write_json, write_text


WINDOWS = {
    "Y1": ("2023-05-30", "2024-05-30"),
    "Y2": ("2024-05-30", "2025-05-30"),
    "Y3": ("2025-05-30", "2026-05-30"),
    "R18-1": ("2023-05-30", "2024-11-30"),
    "R18-2": ("2023-11-30", "2025-05-30"),
    "R18-3": ("2024-05-30", "2025-11-30"),
    "R18-4": ("2024-11-30", "2026-05-30"),
}

SCORE_PART_KEYS = [
    "return20Rank",
    "high60Rank",
    "rsiBalanceRank",
    "macdHistRank",
    "bollSqueezeRank",
    "indicatorPulseQualityRank",
    "indicatorConfluenceQualityRank",
    "amountEfficiency20Rank",
    "moneyflowMainNetRank1Rank",
    "moneyflowMarketStrongRank",
    "moneyflowMarketQualityRank",
    "moneyflowMarketSurgeQualityRank",
    "moneyflowMarketSurgeStrictQualityRank",
    "moneyflowMarketSurgeRelativeQualityRank",
    "moneyflowMarketSurgeConfirmedQualityRank",
    "rsiMomentumConfirmedQualityRank",
    "stockSpecificBreakoutQualityRank",
    "stockSpecificMatureBreadthQualityRank",
    "industryReturn20Rank",
    "industryRelativeReturn20Rank",
    "entryPriorVolumeRatioBasicScorePenalty",
    "entryVolumeInefficiencyCrowdingScorePenalty",
    "entryIndustryReturnOverheatScorePenalty",
    "entryUnsupportedBollSqueezeScorePenalty",
    "entryIndustryMoneyflowCrowdingScorePenalty",
    "entryMoneyflowSurgeRsiCrowdingScorePenalty",
    "entryIndicatorConfluenceMoneyflowCrowdingScorePenalty",
    "entryUnconfirmedGapRangeScorePenalty",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare completed-trade deltas between two portfolio runs.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--baseline-run", required=True, help="Baseline portfolio run id.")
    parser.add_argument("--candidate-run", required=True, help="Candidate portfolio run id.")
    args = parser.parse_args()

    started_at = now_text()
    baseline = read_json(RUNS_ROOT / args.baseline_run / "results.json")
    candidate = read_json(RUNS_ROOT / args.candidate_run / "results.json")
    if "windows" in baseline and "windows" in candidate:
        output = analyze_window_runs(args.run_id, args.baseline_run, args.candidate_run, baseline, candidate, started_at)
        run_dir = RUNS_ROOT / args.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        write_json(run_dir / "results.json", json_safe(output))
        write_text(run_dir / "review.md", render_window_review(output))
        print(json.dumps({"runId": args.run_id, "summary": compact_window_summary(output), "runDir": str(run_dir)}, ensure_ascii=False, indent=2))
        return

    baseline_trades = keyed_trades(baseline["result"].get("completedTrades", []))
    candidate_trades = keyed_trades(candidate["result"].get("completedTrades", []))

    common_keys = set(baseline_trades) & set(candidate_trades)
    baseline_only_keys = set(baseline_trades) - set(candidate_trades)
    candidate_only_keys = set(candidate_trades) - set(baseline_trades)

    comparisons = []
    for label, bounds in [("ALL", None), *WINDOWS.items()]:
        baseline_only = select_window([baseline_trades[key] for key in baseline_only_keys], bounds)
        candidate_only = select_window([candidate_trades[key] for key in candidate_only_keys], bounds)
        common_baseline = select_window([baseline_trades[key] for key in common_keys], bounds)
        common_candidate = select_window([candidate_trades[key] for key in common_keys], bounds)
        comparisons.append(
            {
                "label": label,
                "baselineOnly": summarize_trades(baseline_only),
                "candidateOnly": summarize_trades(candidate_only),
                "commonBaseline": summarize_trades(common_baseline),
                "commonCandidate": summarize_trades(common_candidate),
                "replacementNetPnlDelta": sum_value(candidate_only, "netPnl") - sum_value(baseline_only, "netPnl"),
                "replacementCountDelta": len(candidate_only) - len(baseline_only),
                "baselineOnlyWorst": sorted_sample(baseline_only, reverse=False),
                "candidateOnlyWorst": sorted_sample(candidate_only, reverse=False),
                "candidateOnlyBest": sorted_sample(candidate_only, reverse=True),
            }
        )

    output = {
        "runId": args.run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "baselineRun": args.baseline_run,
        "candidateRun": args.candidate_run,
        "baselineTradeCount": len(baseline_trades),
        "candidateTradeCount": len(candidate_trades),
        "commonTradeCount": len(common_keys),
        "baselineOnlyTradeCount": len(baseline_only_keys),
        "candidateOnlyTradeCount": len(candidate_only_keys),
        "comparisons": comparisons,
    }

    run_dir = RUNS_ROOT / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "results.json", json_safe(output))
    write_text(run_dir / "review.md", render_review(output))
    print(json.dumps({"runId": args.run_id, "summary": compact_summary(output), "runDir": str(run_dir)}, ensure_ascii=False, indent=2))


def analyze_window_runs(
    run_id: str,
    baseline_run: str,
    candidate_run: str,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    started_at: str,
) -> dict[str, Any]:
    candidate_windows = {item["window"]["label"]: item for item in candidate.get("windows", [])}
    windows = []
    for baseline_window in baseline.get("windows", []):
        label = baseline_window["window"]["label"]
        candidate_window = candidate_windows.get(label)
        if not candidate_window:
            continue
        windows.append(
            {
                "label": label,
                "window": baseline_window["window"],
                "baselineAnalysis": baseline_window.get("analysis", {}),
                "candidateAnalysis": candidate_window.get("analysis", {}),
                "tradeDelta": compare_trade_lists(
                    baseline_window["result"].get("completedTrades", []),
                    candidate_window["result"].get("completedTrades", []),
                ),
            }
        )
    return {
        "runId": run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "baselineRun": baseline_run,
        "candidateRun": candidate_run,
        "mode": "window_validation",
        "windows": windows,
    }


def compare_trade_lists(baseline_list: list[dict[str, Any]], candidate_list: list[dict[str, Any]]) -> dict[str, Any]:
    baseline_trades = keyed_trades(baseline_list)
    candidate_trades = keyed_trades(candidate_list)
    common_keys = set(baseline_trades) & set(candidate_trades)
    baseline_only_keys = set(baseline_trades) - set(candidate_trades)
    candidate_only_keys = set(candidate_trades) - set(baseline_trades)
    baseline_only = [baseline_trades[key] for key in baseline_only_keys]
    candidate_only = [candidate_trades[key] for key in candidate_only_keys]
    common_baseline = [baseline_trades[key] for key in common_keys]
    common_candidate = [candidate_trades[key] for key in common_keys]
    return {
        "baselineTradeCount": len(baseline_trades),
        "candidateTradeCount": len(candidate_trades),
        "commonTradeCount": len(common_keys),
        "baselineOnly": summarize_trades(baseline_only),
        "candidateOnly": summarize_trades(candidate_only),
        "commonBaseline": summarize_trades(common_baseline),
        "commonCandidate": summarize_trades(common_candidate),
        "replacementNetPnlDelta": sum_value(candidate_only, "netPnl") - sum_value(baseline_only, "netPnl"),
        "replacementCountDelta": len(candidate_only) - len(baseline_only),
        "baselineOnlyWorst": sorted_sample(baseline_only, reverse=False),
        "candidateOnlyWorst": sorted_sample(candidate_only, reverse=False),
        "candidateOnlyBest": sorted_sample(candidate_only, reverse=True),
    }


def keyed_trades(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for index, trade in enumerate(trades):
        key = f"{trade.get('ts_code')}|{trade.get('entryDate')}"
        if key in result:
            key = f"{key}|{index}"
        result[key] = trade
    return result


def select_window(trades: list[dict[str, Any]], bounds: tuple[str, str] | None) -> list[dict[str, Any]]:
    if not bounds:
        return trades
    start = date.fromisoformat(bounds[0])
    end = date.fromisoformat(bounds[1])
    return [trade for trade in trades if start <= date.fromisoformat(str(trade["entryDate"])) <= end]


def summarize_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(trades),
        "netPnl": sum_value(trades, "netPnl"),
        "avgReturnPct": avg([trade.get("returnPct") for trade in trades]),
        "winRate": sum(1 for trade in trades if float(trade.get("returnPct") or 0) > 0) / len(trades) if trades else None,
        "profitLossRatio": profit_loss_ratio(trades),
        "scoreParts": summarize_score_parts(trades),
    }


def summarize_score_parts(trades: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for key in SCORE_PART_KEYS:
        values = [
            float((trade.get("entryScoreParts") or {}).get(key))
            for trade in trades
            if finite((trade.get("entryScoreParts") or {}).get(key))
        ]
        if values:
            result[key] = mean(values)
    return result


def profit_loss_ratio(trades: list[dict[str, Any]]) -> float | None:
    wins = [float(trade.get("returnPct") or 0) for trade in trades if float(trade.get("returnPct") or 0) > 0]
    losses = [abs(float(trade.get("returnPct") or 0)) for trade in trades if float(trade.get("returnPct") or 0) < 0]
    if not wins or not losses:
        return None
    return mean(wins) / mean(losses) if mean(losses) else None


def sorted_sample(trades: list[dict[str, Any]], reverse: bool) -> list[dict[str, Any]]:
    selected = sorted(trades, key=lambda trade: float(trade.get("returnPct") or 0), reverse=reverse)[:5]
    return [
        {
            "ts_code": trade.get("ts_code"),
            "name": trade.get("name"),
            "industry": trade.get("industry"),
            "entryDate": trade.get("entryDate"),
            "exitDate": trade.get("exitDate"),
            "returnPct": trade.get("returnPct"),
            "netPnl": trade.get("netPnl"),
            "exitPriceRule": trade.get("exitPriceRule"),
            "scoreParts": {key: (trade.get("entryScoreParts") or {}).get(key) for key in SCORE_PART_KEYS if key in (trade.get("entryScoreParts") or {})},
        }
        for trade in selected
    ]


def sum_value(trades: list[dict[str, Any]], key: str) -> float:
    return sum(float(trade.get(key) or 0.0) for trade in trades)


def avg(values: list[Any]) -> float | None:
    selected = [float(value) for value in values if finite(value)]
    return mean(selected) if selected else None


def compact_summary(output: dict[str, Any]) -> dict[str, Any]:
    by_label = {item["label"]: item for item in output["comparisons"]}
    return {
        "baselineOnlyTradeCount": output["baselineOnlyTradeCount"],
        "candidateOnlyTradeCount": output["candidateOnlyTradeCount"],
        "allReplacementNetPnlDelta": by_label["ALL"]["replacementNetPnlDelta"],
        "Y1ReplacementNetPnlDelta": by_label["Y1"]["replacementNetPnlDelta"],
        "R18_1ReplacementNetPnlDelta": by_label["R18-1"]["replacementNetPnlDelta"],
    }


def render_review(output: dict[str, Any]) -> str:
    lines = [
        f"# {output['runId']} 交易替换诊断",
        "",
        f"- 基准 run：`{output['baselineRun']}`，完成交易 `{output['baselineTradeCount']}`。",
        f"- 候选 run：`{output['candidateRun']}`，完成交易 `{output['candidateTradeCount']}`。",
        f"- 共同交易：`{output['commonTradeCount']}`；基准独有：`{output['baselineOnlyTradeCount']}`；候选独有：`{output['candidateOnlyTradeCount']}`。",
        "",
        "## 替换汇总",
        "",
        "| 窗口 | 基准独有 | 基准独有净盈亏 | 候选独有 | 候选独有净盈亏 | 替换净差 | 候选独有胜率 | 候选独有均值 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in output["comparisons"]:
        lines.append(render_row(item))
    lines.extend(["", "## 候选独有最差样本", ""])
    for item in output["comparisons"]:
        if item["label"] not in {"ALL", "Y1", "R18-1", "Y3"}:
            continue
        lines.append(f"### {item['label']}")
        for trade in item["candidateOnlyWorst"]:
            lines.append(
                f"- `{trade['ts_code']}` {trade.get('name') or ''} {trade['entryDate']}->{trade['exitDate']} "
                f"收益 {format_optional_percent(trade.get('returnPct'))}，净盈亏 {float(trade.get('netPnl') or 0):.2f}，退出 {trade.get('exitPriceRule')}"
            )
        lines.append("")
    lines.extend(["## 结论提示", ""])
    lines.append("- 若候选独有在早段窗口净盈亏为负，而全窗口为正，说明该因子只在后段有效，需要状态化启用。")
    lines.append("- 若候选独有最差样本集中在同一行业或低市场状态，应优先测试状态交互，而不是继续提高权重。")
    return "\n".join(lines)


def render_window_review(output: dict[str, Any]) -> str:
    lines = [
        f"# {output['runId']} 滚动窗口交易替换诊断",
        "",
        f"- 基准滚动 run：`{output['baselineRun']}`。",
        f"- 候选滚动 run：`{output['candidateRun']}`。",
        "",
        "## 窗口替换汇总",
        "",
        "| 窗口 | 基准年化 | 候选年化 | 基准交易 | 候选交易 | 基准独有净盈亏 | 候选独有净盈亏 | 替换净差 | 候选独有胜率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in output["windows"]:
        delta = item["tradeDelta"]
        baseline = item.get("baselineAnalysis") or {}
        candidate = item.get("candidateAnalysis") or {}
        lines.append(
            f"| `{item['label']}` | {format_optional_percent(baseline.get('annualizedReturn'))} | "
            f"{format_optional_percent(candidate.get('annualizedReturn'))} | `{delta['baselineTradeCount']}` | "
            f"`{delta['candidateTradeCount']}` | `{delta['baselineOnly']['netPnl']:.2f}` | "
            f"`{delta['candidateOnly']['netPnl']:.2f}` | `{delta['replacementNetPnlDelta']:.2f}` | "
            f"{format_optional_percent(delta['candidateOnly'].get('winRate'))} |"
        )
    lines.extend(["", "## 失败窗口候选独有最差样本", ""])
    for item in output["windows"]:
        if item["label"] not in {"Y1", "R18-1"}:
            continue
        lines.append(f"### {item['label']}")
        for trade in item["tradeDelta"]["candidateOnlyWorst"]:
            lines.append(
                f"- `{trade['ts_code']}` {trade.get('name') or ''} {trade['entryDate']}->{trade['exitDate']} "
                f"收益 {format_optional_percent(trade.get('returnPct'))}，净盈亏 {float(trade.get('netPnl') or 0):.2f}，退出 {trade.get('exitPriceRule')}"
            )
        lines.append("")
    lines.extend(["## 结论提示", ""])
    lines.append("- 若失败窗口候选独有净盈亏为负，说明因子在该窗口替换进了坏交易。")
    lines.append("- 若替换净差不差但候选年化下降，应进一步检查共同交易的仓位路径、窗口初始化和资金占用。")
    return "\n".join(lines)


def compact_window_summary(output: dict[str, Any]) -> dict[str, Any]:
    windows = {item["label"]: item["tradeDelta"] for item in output.get("windows", [])}
    return {
        label: {
            "replacementNetPnlDelta": windows[label]["replacementNetPnlDelta"],
            "baselineOnlyNetPnl": windows[label]["baselineOnly"]["netPnl"],
            "candidateOnlyNetPnl": windows[label]["candidateOnly"]["netPnl"],
        }
        for label in ["Y1", "R18-1", "Y3"]
        if label in windows
    }


def render_row(item: dict[str, Any]) -> str:
    candidate = item["candidateOnly"]
    baseline = item["baselineOnly"]
    return (
        f"| `{item['label']}` | `{baseline['count']}` | `{baseline['netPnl']:.2f}` | "
        f"`{candidate['count']}` | `{candidate['netPnl']:.2f}` | `{item['replacementNetPnlDelta']:.2f}` | "
        f"{format_optional_percent(candidate.get('winRate'))} | {format_optional_percent(candidate.get('avgReturnPct'))} |"
    )


if __name__ == "__main__":
    main()
