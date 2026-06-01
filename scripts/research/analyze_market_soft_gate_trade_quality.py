from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from statistics import mean, median
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
    "macdHistDeltaRank",
    "bollSqueezeRank",
    "maAlignmentRank",
    "indicatorPulseQualityRank",
    "indicatorConfluenceQualityRank",
    "amountEfficiency20Rank",
    "moneyflowMarketSurgeQualityRank",
    "moneyflowMarketSurgeConfirmedQualityRank",
    "stockSpecificBreakoutQualityRank",
    "stockSpecificMatureBreadthQualityRank",
    "industryReturn20Rank",
    "industryRelativeReturn20Rank",
]

RISK_METRIC_KEYS = [
    "entryRangePct",
    "gapPct",
    "intradayReturnPct",
    "lowerShadowPct",
    "upperShadowPct",
    "priorGapDown60Pct",
    "priorGapDown3Count60",
    "priorGapDown5Count60",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose market soft-gate completed-trade quality.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--baseline-run", required=True, help="Current observation baseline run id.")
    parser.add_argument("--candidate-run", required=True, help="Market soft-gate candidate run id.")
    args = parser.parse_args()

    run_dir = RUNS_ROOT / args.run_id
    if run_dir.exists():
        raise SystemExit(f"Run directory already exists: {run_dir}")

    started_at = now_text()
    baseline_payload = read_json(RUNS_ROOT / args.baseline_run / "results.json")
    candidate_payload = read_json(RUNS_ROOT / args.candidate_run / "results.json")
    baseline = result_payload(baseline_payload)
    candidate = result_payload(candidate_payload)

    baseline_trades = keyed_trades(baseline.get("completedTrades", []))
    candidate_trades = keyed_trades(candidate.get("completedTrades", []))
    state_by_date = {
        str(item.get("date")): market_state(item)
        for item in candidate.get("equity", [])
        if item.get("date")
    }

    common_keys = set(baseline_trades) & set(candidate_trades)
    baseline_only_keys = set(baseline_trades) - set(candidate_trades)
    candidate_only_keys = set(candidate_trades) - set(baseline_trades)

    baseline_only = annotate_trades([baseline_trades[key] for key in baseline_only_keys], "baseline_only", state_by_date)
    candidate_only = annotate_trades([candidate_trades[key] for key in candidate_only_keys], "candidate_only", state_by_date)
    common_candidate = annotate_trades([candidate_trades[key] for key in common_keys], "common", state_by_date)

    all_annotated = baseline_only + candidate_only + common_candidate
    output = {
        "runId": args.run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "baselineRun": args.baseline_run,
        "candidateRun": args.candidate_run,
        "baselineTradeCount": len(baseline_trades),
        "candidateTradeCount": len(candidate_trades),
        "commonTradeCount": len(common_keys),
        "baselineOnlyTradeCount": len(baseline_only),
        "candidateOnlyTradeCount": len(candidate_only),
        "relationSummary": summarize_by_relation(all_annotated),
        "relationStateSummary": summarize_by_relation_state(all_annotated),
        "candidateOnlyWindows": summarize_candidate_only_windows(candidate_only),
        "stateScoreProfile": summarize_state_profiles(all_annotated),
        "worstSamples": {
            "candidateOnlySoft": sample_trades(filter_trades(candidate_only, state="soft_risk_on"), reverse=False),
            "candidateOnlyBase": sample_trades(filter_trades(candidate_only, state="base_risk_on"), reverse=False),
            "baselineOnlyBase": sample_trades(filter_trades(baseline_only, state="base_risk_on"), reverse=False),
        },
        "summary": compact_summary(baseline_only, candidate_only),
    }

    context = {
        "runId": args.run_id,
        "baselineRun": args.baseline_run,
        "candidateRun": args.candidate_run,
        "objective": "Explain why market-breadth soft-gate forward samples did not convert into better completed trades.",
        "readOnly": True,
        "tradeKey": "ts_code + entryDate, matching analyze_trade_delta.py",
    }

    run_dir.mkdir(parents=True)
    write_json(run_dir / "context.json", context)
    write_json(run_dir / "results.json", json_safe(output))
    write_text(run_dir / "review.md", render_review(output))
    print(json.dumps({"runId": args.run_id, "summary": output["summary"], "runDir": str(run_dir)}, ensure_ascii=False, indent=2))


def result_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return payload.get("result", payload)


def keyed_trades(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for index, trade in enumerate(trades):
        key = f"{trade.get('ts_code')}|{trade.get('entryDate')}"
        if key in result:
            key = f"{key}|{index}"
        result[key] = trade
    return result


def market_state(point: dict[str, Any]) -> dict[str, Any]:
    if point.get("marketBaseRiskOn"):
        state = "base_risk_on"
    elif point.get("marketSoftRiskOn"):
        state = "soft_risk_on"
    elif point.get("marketRiskOn"):
        state = "risk_on_other"
    else:
        state = "risk_off"
    return {
        "state": state,
        "marketRiskOn": point.get("marketRiskOn"),
        "marketBaseRiskOn": point.get("marketBaseRiskOn"),
        "marketSoftRiskOn": point.get("marketSoftRiskOn"),
        "marketBreadthFailedChecks": point.get("marketBreadthFailedChecks"),
        "marketAboveMa20Pct": point.get("marketAboveMa20Pct"),
        "marketAboveMa60Pct": point.get("marketAboveMa60Pct"),
        "marketUpPct": point.get("marketUpPct"),
    }


def annotate_trades(trades: list[dict[str, Any]], relation: str, state_by_date: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    annotated = []
    for trade in trades:
        state = state_by_date.get(str(trade.get("entryDate")), {"state": "unknown"})
        item = dict(trade)
        item["relation"] = relation
        item["entryMarketState"] = state["state"]
        item["entryMarketMetrics"] = {key: value for key, value in state.items() if key != "state"}
        annotated.append(item)
    return annotated


def summarize_by_relation(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"relation": relation, **summarize_trades([trade for trade in trades if trade["relation"] == relation])}
        for relation in ["baseline_only", "candidate_only", "common"]
    ]


def summarize_by_relation_state(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for relation in ["baseline_only", "candidate_only", "common"]:
        relation_trades = [trade for trade in trades if trade["relation"] == relation]
        for state in ["base_risk_on", "soft_risk_on", "risk_on_other", "risk_off", "unknown"]:
            selected = [trade for trade in relation_trades if trade.get("entryMarketState") == state]
            if selected:
                result.append({"relation": relation, "entryMarketState": state, **summarize_trades(selected)})
    return result


def summarize_candidate_only_windows(candidate_only: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for label, bounds in [("ALL", None), *WINDOWS.items()]:
        window_trades = select_window(candidate_only, bounds)
        soft = filter_trades(window_trades, state="soft_risk_on")
        base = filter_trades(window_trades, state="base_risk_on")
        result.append(
            {
                "label": label,
                "candidateOnly": summarize_trades(window_trades),
                "candidateOnlySoft": summarize_trades(soft),
                "candidateOnlyBase": summarize_trades(base),
            }
        )
    return result


def summarize_state_profiles(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    profiles = []
    groups = [
        ("candidate_only_soft", filter_trades(trades, relation="candidate_only", state="soft_risk_on")),
        ("candidate_only_base", filter_trades(trades, relation="candidate_only", state="base_risk_on")),
        ("baseline_only_base", filter_trades(trades, relation="baseline_only", state="base_risk_on")),
        ("common_base", filter_trades(trades, relation="common", state="base_risk_on")),
    ]
    for label, selected in groups:
        profiles.append(
            {
                "label": label,
                "tradeSummary": summarize_trades(selected),
                "scoreParts": summarize_nested_fields(selected, "entryScoreParts", SCORE_PART_KEYS),
                "entryRiskMetrics": summarize_nested_fields(selected, "entryRiskMetrics", RISK_METRIC_KEYS),
                "marketMetrics": summarize_nested_fields(selected, "entryMarketMetrics", [
                    "marketBreadthFailedChecks",
                    "marketAboveMa20Pct",
                    "marketAboveMa60Pct",
                    "marketUpPct",
                ]),
            }
        )
    return profiles


def summarize_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(trades),
        "netPnl": sum_float(trades, "netPnl"),
        "avgReturnPct": avg([trade.get("returnPct") for trade in trades]),
        "medianReturnPct": med([trade.get("returnPct") for trade in trades]),
        "winRate": sum(1 for trade in trades if float(trade.get("returnPct") or 0.0) > 0.0) / len(trades) if trades else None,
        "profitLossRatio": profit_loss_ratio(trades),
        "exitPriceRules": dict(Counter(str(trade.get("exitPriceRule")) for trade in trades).most_common()),
    }


def summarize_nested_fields(trades: list[dict[str, Any]], nested_key: str, keys: list[str]) -> dict[str, Any]:
    result = {}
    for key in keys:
        values = [
            float((trade.get(nested_key) or {}).get(key))
            for trade in trades
            if finite((trade.get(nested_key) or {}).get(key))
        ]
        if values:
            result[key] = {"mean": mean(values), "median": median(values)}
    return result


def compact_summary(baseline_only: list[dict[str, Any]], candidate_only: list[dict[str, Any]]) -> dict[str, Any]:
    candidate_soft = filter_trades(candidate_only, state="soft_risk_on")
    candidate_base = filter_trades(candidate_only, state="base_risk_on")
    baseline_base = filter_trades(baseline_only, state="base_risk_on")
    return {
        "candidateOnlyTradeCount": len(candidate_only),
        "candidateOnlyNetPnl": sum_float(candidate_only, "netPnl"),
        "candidateOnlySoftTradeCount": len(candidate_soft),
        "candidateOnlySoftNetPnl": sum_float(candidate_soft, "netPnl"),
        "candidateOnlySoftWinRate": summarize_trades(candidate_soft).get("winRate"),
        "candidateOnlyBaseTradeCount": len(candidate_base),
        "candidateOnlyBaseNetPnl": sum_float(candidate_base, "netPnl"),
        "candidateOnlyBaseWinRate": summarize_trades(candidate_base).get("winRate"),
        "baselineOnlyBaseTradeCount": len(baseline_base),
        "baselineOnlyBaseNetPnl": sum_float(baseline_base, "netPnl"),
    }


def filter_trades(trades: list[dict[str, Any]], relation: str | None = None, state: str | None = None) -> list[dict[str, Any]]:
    result = trades
    if relation:
        result = [trade for trade in result if trade.get("relation") == relation]
    if state:
        result = [trade for trade in result if trade.get("entryMarketState") == state]
    return result


def select_window(trades: list[dict[str, Any]], bounds: tuple[str, str] | None) -> list[dict[str, Any]]:
    if not bounds:
        return trades
    start = date.fromisoformat(bounds[0])
    end = date.fromisoformat(bounds[1])
    return [trade for trade in trades if start <= date.fromisoformat(str(trade["entryDate"])) <= end]


def sample_trades(trades: list[dict[str, Any]], reverse: bool) -> list[dict[str, Any]]:
    selected = sorted(trades, key=lambda trade: float(trade.get("returnPct") or 0.0), reverse=reverse)[:5]
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
            "entryMarketState": trade.get("entryMarketState"),
            "entryMarketMetrics": trade.get("entryMarketMetrics"),
            "entryScoreParts": {key: (trade.get("entryScoreParts") or {}).get(key) for key in SCORE_PART_KEYS if key in (trade.get("entryScoreParts") or {})},
            "entryRiskMetrics": {key: (trade.get("entryRiskMetrics") or {}).get(key) for key in RISK_METRIC_KEYS if key in (trade.get("entryRiskMetrics") or {})},
        }
        for trade in selected
    ]


def profit_loss_ratio(trades: list[dict[str, Any]]) -> float | None:
    wins = [float(trade.get("returnPct") or 0.0) for trade in trades if float(trade.get("returnPct") or 0.0) > 0.0]
    losses = [abs(float(trade.get("returnPct") or 0.0)) for trade in trades if float(trade.get("returnPct") or 0.0) < 0.0]
    if not wins or not losses:
        return None
    avg_loss = mean(losses)
    return mean(wins) / avg_loss if avg_loss else None


def sum_float(trades: list[dict[str, Any]], key: str) -> float:
    return sum(float(trade.get(key) or 0.0) for trade in trades)


def avg(values: list[Any]) -> float | None:
    selected = [float(value) for value in values if finite(value)]
    return mean(selected) if selected else None


def med(values: list[Any]) -> float | None:
    selected = [float(value) for value in values if finite(value)]
    return median(selected) if selected else None


def render_review(output: dict[str, Any]) -> str:
    lines = [
        f"# {output['runId']} 软门控交易质量诊断",
        "",
        f"- 基准 run：`{output['baselineRun']}`，完成交易 `{output['baselineTradeCount']}`。",
        f"- 候选 run：`{output['candidateRun']}`，完成交易 `{output['candidateTradeCount']}`。",
        f"- 共同交易 `{output['commonTradeCount']}`；基准独有 `{output['baselineOnlyTradeCount']}`；候选独有 `{output['candidateOnlyTradeCount']}`。",
        "",
        "## 替换关系",
        "",
        "| 关系 | 交易数 | 净盈亏 | 胜率 | 均值收益 | 中位收益 | 盈亏比 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in output["relationSummary"]:
        lines.append(render_summary_row(item.get("relation"), item))

    lines.extend(["", "## 入场市场状态", "", "| 关系 | 入场状态 | 交易数 | 净盈亏 | 胜率 | 均值收益 | 盈亏比 |", "| --- | --- | ---: | ---: | ---: | ---: | ---: |"])
    for item in output["relationStateSummary"]:
        lines.append(
            f"| `{item['relation']}` | `{item['entryMarketState']}` | `{item['count']}` | `{item['netPnl']:.2f}` | "
            f"{format_optional_percent(item.get('winRate'))} | {format_optional_percent(item.get('avgReturnPct'))} | {format_optional_ratio(item.get('profitLossRatio'))} |"
        )

    lines.extend(["", "## 候选独有窗口拆分", "", "| 窗口 | 全部数/净盈亏 | 软Risk-On数/净盈亏/胜率 | 基础Risk-On数/净盈亏/胜率 |", "| --- | ---: | ---: | ---: |"])
    for item in output["candidateOnlyWindows"]:
        total = item["candidateOnly"]
        soft = item["candidateOnlySoft"]
        base = item["candidateOnlyBase"]
        lines.append(
            f"| `{item['label']}` | `{total['count']}` / `{total['netPnl']:.2f}` | "
            f"`{soft['count']}` / `{soft['netPnl']:.2f}` / {format_optional_percent(soft.get('winRate'))} | "
            f"`{base['count']}` / `{base['netPnl']:.2f}` / {format_optional_percent(base.get('winRate'))} |"
        )

    lines.extend(["", "## 画像差异", ""])
    for profile in output["stateScoreProfile"]:
        lines.append(f"### {profile['label']}")
        lines.append(render_profile_line(profile, "scoreParts", ["high60Rank", "return20Rank", "rsiBalanceRank", "macdHistDeltaRank", "bollSqueezeRank", "amountEfficiency20Rank", "moneyflowMarketSurgeQualityRank", "industryReturn20Rank"]))
        lines.append(render_profile_line(profile, "entryRiskMetrics", ["entryRangePct", "gapPct", "priorGapDown60Pct"]))
        lines.append(render_profile_line(profile, "marketMetrics", ["marketBreadthFailedChecks", "marketAboveMa20Pct", "marketAboveMa60Pct", "marketUpPct"]))
        lines.append("")

    lines.extend(["## 最差样本", ""])
    for label, trades in output["worstSamples"].items():
        lines.append(f"### {label}")
        if not trades:
            lines.append("- 无。")
        for trade in trades:
            metrics = trade.get("entryMarketMetrics") or {}
            lines.append(
                f"- `{trade['ts_code']}` {trade.get('name') or ''} {trade['entryDate']}->{trade['exitDate']} "
                f"收益 {format_optional_percent(trade.get('returnPct'))}，净盈亏 {float(trade.get('netPnl') or 0):.2f}，"
                f"退出 {trade.get('exitPriceRule')}，状态 `{trade.get('entryMarketState')}`，"
                f"MA20 {format_optional_percent(metrics.get('marketAboveMa20Pct'))}，"
                f"upPct {format_optional_percent(metrics.get('marketUpPct'))}"
            )
        lines.append("")

    lines.extend(["## 结论提示", ""])
    lines.append("- 若候选独有亏损主要集中在 `soft_risk_on`，说明软门控新增日期本身不可用。")
    lines.append("- 若候选独有亏损主要集中在 `base_risk_on`，说明软门控通过资金占用、持仓路径或周频买入限制挤掉了原本更好的基础 Risk-On 交易。")
    return "\n".join(lines)


def render_summary_row(label: str, item: dict[str, Any]) -> str:
    return (
        f"| `{label}` | `{item['count']}` | `{item['netPnl']:.2f}` | {format_optional_percent(item.get('winRate'))} | "
        f"{format_optional_percent(item.get('avgReturnPct'))} | {format_optional_percent(item.get('medianReturnPct'))} | "
        f"{format_optional_ratio(item.get('profitLossRatio'))} |"
    )


def format_optional_ratio(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}:1"
    except (TypeError, ValueError):
        return str(value)


def render_profile_line(profile: dict[str, Any], section: str, keys: list[str]) -> str:
    values = profile.get(section) or {}
    pieces = []
    for key in keys:
        stat = values.get(key)
        if stat:
            pieces.append(f"{key}={format_optional_percent(stat.get('mean'))}")
    return "- " + section + "：" + ("；".join(pieces) if pieces else "n/a")


if __name__ == "__main__":
    main()
