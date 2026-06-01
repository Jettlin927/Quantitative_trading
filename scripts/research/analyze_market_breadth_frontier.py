from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import date
from pathlib import Path
from statistics import mean, median
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import finite, json_safe
from backend.app.database import SessionLocal
from backend.app.main import query_backtest_stocks, stock_to_market_meta
from backend.app.schemas import MarketBacktestRequest
from scripts.research.run_portfolio_backtest import (
    build_market_breadth_payload,
    build_market_states,
    build_portfolio_rules,
    load_signal_rows,
    payload_to_dict,
)
from scripts.research.run_research_round import (
    RUNS_ROOT,
    build_market_payload,
    build_strategy,
    format_optional_percent,
    now_text,
    read_json,
    write_json,
    write_text,
)
from scripts.research.run_window_validation import build_validation_windows, with_warmup_window


FORWARD_DAYS = (1, 3, 5, 10)


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose market-breadth Risk-On coverage frontiers.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--context", required=True, help="Research context JSON.")
    parser.add_argument("--strategy", default="trend-follow-maximum-profit-no-macd", help="Strategy preset.")
    parser.add_argument("--windows", default="Y1,R18-1,R18-2,Y3", help="Comma-separated validation window labels.")
    args = parser.parse_args()

    context = read_json(Path(args.context))
    requested_labels = [item.strip() for item in args.windows.split(",") if item.strip()]
    window_defs = {item["label"]: with_warmup_window(item) for item in build_validation_windows(context)}
    windows = [window_defs[label] for label in requested_labels if label in window_defs]
    if not windows:
        raise SystemExit("No requested windows were found in the validation schedule.")

    run_dir = RUNS_ROOT / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    started_at = now_text()

    load_context = deepcopy(context)
    load_context["scope"]["start_date"] = min(window["warmupStartDate"] for window in windows)
    load_context["scope"]["end_date"] = max(window["endDate"] for window in windows)
    strategy = build_strategy(args.strategy, load_context)
    portfolio_rules = build_portfolio_rules(load_context, strategy["config"])
    market_rules = portfolio_rules["marketBreadthFilter"]
    market_payload = build_market_breadth_payload(load_context, strategy)
    if market_payload is None:
        market_payload = MarketBacktestRequest(**build_market_payload(load_context, strategy, max_stocks=None))

    with SessionLocal() as db:
        stocks = [stock_to_market_meta(stock) for stock in query_backtest_stocks(db, market_payload)]
        by_date, skipped = load_signal_rows(db, stocks, market_payload, strategy["config"], {}, {}, {})

    states = build_market_states(by_date, market_rules)
    rows_by_code = build_rows_by_code(by_date)
    index_by_code_date = build_index_by_code_date(rows_by_code)
    variants = build_variants(market_rules)
    window_results = [
        analyze_window(window, by_date, states, rows_by_code, index_by_code_date, market_rules, variants)
        for window in windows
    ]

    output = {
        "runId": args.run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "strategy": strategy,
        "context": context,
        "marketPayload": payload_to_dict(market_payload),
        "scope": {
            "marketBreadthStocks": len(stocks),
            "skipped": skipped,
            "dateCount": len(states),
        },
        "baseMarketRules": market_rules,
        "variants": variants,
        "windows": window_results,
    }
    write_json(run_dir / "context.json", context)
    write_json(run_dir / "results.json", json_safe(output))
    write_text(run_dir / "review.md", render_review(args.run_id, market_rules, window_results))
    print(json.dumps({"runId": args.run_id, "windows": summarize_for_stdout(window_results), "runDir": str(run_dir)}, ensure_ascii=False, indent=2))


def analyze_window(
    window: dict[str, Any],
    by_date: dict[str, list[dict[str, Any]]],
    states: dict[str, dict[str, Any]],
    rows_by_code: dict[str, list[dict[str, Any]]],
    index_by_code_date: dict[str, dict[str, int]],
    market_rules: dict[str, Any],
    variants: list[dict[str, Any]],
) -> dict[str, Any]:
    start = date.fromisoformat(window["startDate"])
    end = date.fromisoformat(window["endDate"])
    use_previous = bool(market_rules.get("usePreviousTradingDay", True))
    records: list[dict[str, Any]] = []
    previous_state: dict[str, Any] | None = None
    for trade_date in sorted(states):
        current_state = states[trade_date]
        entry_state = previous_state if use_previous and previous_state else current_state
        current_date = date.fromisoformat(trade_date)
        if start <= current_date <= end:
            base_risk_on = risk_on_by_rules(entry_state, market_rules)
            records.append(
                {
                    "date": trade_date,
                    "baseRiskOn": base_risk_on,
                    "failedChecks": failed_checks(entry_state, market_rules),
                    "entryState": {
                        "date": entry_state.get("date"),
                        "samples": entry_state.get("samples"),
                        "aboveMa20Pct": entry_state.get("aboveMa20Pct"),
                        "aboveMa60Pct": entry_state.get("aboveMa60Pct"),
                        "upPct": entry_state.get("upPct"),
                    },
                    "forwardMarket": forward_market_returns(by_date.get(trade_date, []), rows_by_code, index_by_code_date, trade_date),
                }
            )
        previous_state = current_state

    frontier = []
    for variant in variants:
        variant_records = []
        added_records = []
        for record in records:
            variant_risk_on = risk_on_by_rules(record["entryState"], variant["rules"])
            if variant_risk_on:
                variant_records.append(record)
            if variant_risk_on and not record["baseRiskOn"]:
                added_records.append(record)
        frontier.append(
            {
                "name": variant["name"],
                "rules": variant["rules"],
                "riskOnDays": len(variant_records),
                "addedDays": len(added_records),
                "addedSummary": summarize_records(added_records),
            }
        )

    return {
        "window": window,
        "baseSummary": {
            "riskOnDays": sum(1 for record in records if record["baseRiskOn"]),
            "riskOffDays": sum(1 for record in records if not record["baseRiskOn"]),
            "riskOnForward": summarize_records([record for record in records if record["baseRiskOn"]]),
            "riskOffForward": summarize_records([record for record in records if not record["baseRiskOn"]]),
            "riskOffFailedChecks": summarize_failed_checks(record for record in records if not record["baseRiskOn"]),
            "riskOnState": summarize_states([record["entryState"] for record in records if record["baseRiskOn"]]),
            "riskOffState": summarize_states([record["entryState"] for record in records if not record["baseRiskOn"]]),
        },
        "frontier": frontier,
    }


def build_variants(base_rules: dict[str, Any]) -> list[dict[str, Any]]:
    specs = [
        ("current", {}),
        ("lower_up_40", {"minUpPct": 0.40}),
        ("lower_above20_40", {"minAboveMa20Pct": 0.40}),
        ("lower_above60_30", {"minAboveMa60Pct": 0.30}),
        ("lower_samples_800", {"minSamples": 800}),
        ("soft_all_minus_05", {"minUpPct": 0.40, "minAboveMa20Pct": 0.40, "minAboveMa60Pct": 0.30, "minSamples": 800}),
    ]
    variants = []
    for name, overrides in specs:
        rules = deepcopy(base_rules)
        rules.update(overrides)
        variants.append({"name": name, "rules": rules})
    return variants


def risk_on_by_rules(state: dict[str, Any], rules: dict[str, Any]) -> bool:
    if not bool(rules.get("enabled", False)):
        return True
    return (
        float(state.get("samples") or 0) >= int(rules["minSamples"])
        and float(state.get("aboveMa20Pct") or 0.0) >= float(rules["minAboveMa20Pct"])
        and float(state.get("aboveMa60Pct") or 0.0) >= float(rules["minAboveMa60Pct"])
        and float(state.get("upPct") or 0.0) >= float(rules["minUpPct"])
    )


def failed_checks(state: dict[str, Any], rules: dict[str, Any]) -> list[str]:
    checks = []
    if float(state.get("samples") or 0) < int(rules["minSamples"]):
        checks.append("samples")
    if float(state.get("aboveMa20Pct") or 0.0) < float(rules["minAboveMa20Pct"]):
        checks.append("aboveMa20")
    if float(state.get("aboveMa60Pct") or 0.0) < float(rules["minAboveMa60Pct"]):
        checks.append("aboveMa60")
    if float(state.get("upPct") or 0.0) < float(rules["minUpPct"]):
        checks.append("upPct")
    return checks


def build_rows_by_code(by_date: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    rows_by_code: dict[str, list[dict[str, Any]]] = {}
    for trade_date in sorted(by_date):
        for item in by_date[trade_date]:
            rows_by_code.setdefault(item["stock"]["ts_code"], []).append(item["row"])
    return rows_by_code


def build_index_by_code_date(rows_by_code: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, int]]:
    return {ts_code: {row["date"]: index for index, row in enumerate(rows)} for ts_code, rows in rows_by_code.items()}


def forward_market_returns(
    day_items: list[dict[str, Any]],
    rows_by_code: dict[str, list[dict[str, Any]]],
    index_by_code_date: dict[str, dict[str, int]],
    trade_date: str,
) -> dict[str, dict[str, Any]]:
    result = {}
    for day in FORWARD_DAYS:
        values = []
        for item in day_items:
            ts_code = item["stock"]["ts_code"]
            rows = rows_by_code.get(ts_code, [])
            index = index_by_code_date.get(ts_code, {}).get(trade_date)
            if index is None or index + day >= len(rows):
                continue
            current_close = item["row"].get("close")
            target_close = rows[index + day].get("close")
            if finite(current_close) and finite(target_close) and float(current_close) > 0:
                values.append(float(target_close) / float(current_close) - 1.0)
        result[f"fwd{day}"] = summarize_values(values)
    return result


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {"days": len(records)}
    for day in FORWARD_DAYS:
        key = f"fwd{day}"
        values = [
            float(record["forwardMarket"][key]["mean"])
            for record in records
            if finite(record["forwardMarket"].get(key, {}).get("mean"))
        ]
        summary[key] = summarize_values(values)
    return summary


def summarize_values(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "winRate": None}
    return {
        "count": len(values),
        "mean": mean(values),
        "median": median(values),
        "winRate": sum(1 for value in values if value > 0) / len(values),
    }


def summarize_failed_checks(records: Any) -> dict[str, int]:
    counts = {"samples": 0, "aboveMa20": 0, "aboveMa60": 0, "upPct": 0}
    for record in records:
        for check in record["failedChecks"]:
            counts[check] += 1
    return counts


def summarize_states(states: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "days": len(states),
        "samples": summarize_values([float(state["samples"]) for state in states if finite(state.get("samples"))]),
        "aboveMa20Pct": summarize_values([float(state["aboveMa20Pct"]) for state in states if finite(state.get("aboveMa20Pct"))]),
        "aboveMa60Pct": summarize_values([float(state["aboveMa60Pct"]) for state in states if finite(state.get("aboveMa60Pct"))]),
        "upPct": summarize_values([float(state["upPct"]) for state in states if finite(state.get("upPct"))]),
    }


def render_review(run_id: str, rules: dict[str, Any], windows: list[dict[str, Any]]) -> str:
    lines = [
        f"# {run_id} 市场宽度前沿诊断",
        "",
        "- 口径：只读重建市场宽度状态；不改变策略回测语义。",
        "- 前瞻收益：宽度样本等权平均收盘到后续 1/3/5/10 个交易日收盘的市场路径，仅用于诊断新增 Risk-On 日是否可靠。",
        f"- 当前阈值：samples >= `{rules['minSamples']}`，aboveMa20 >= `{rules['minAboveMa20Pct']}`，aboveMa60 >= `{rules['minAboveMa60Pct']}`，upPct >= `{rules['minUpPct']}`。",
        "",
        "## 基础状态",
        "",
        "| 窗口 | Risk-On天 | Risk-Off天 | Off失败 samples | Off失败 MA20 | Off失败 MA60 | Off失败 upPct | On Fwd5均值 | Off Fwd5均值 | Off Fwd10均值 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in windows:
        base = item["baseSummary"]
        failed = base["riskOffFailedChecks"]
        lines.append(
            f"| `{item['window']['label']}` | {base['riskOnDays']} | {base['riskOffDays']} | {failed['samples']} | {failed['aboveMa20']} | "
            f"{failed['aboveMa60']} | {failed['upPct']} | {format_optional_percent(base['riskOnForward']['fwd5']['mean'])} | "
            f"{format_optional_percent(base['riskOffForward']['fwd5']['mean'])} | {format_optional_percent(base['riskOffForward']['fwd10']['mean'])} |"
        )

    lines.extend(["", "## 阈值前沿", ""])
    for item in windows:
        lines.append(f"### {item['window']['label']}")
        lines.append("")
        lines.append("| 方案 | Risk-On天 | 新增天 | 新增 Fwd5均值 | 新增 Fwd10均值 | 新增 Fwd5胜率 |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
        for variant in item["frontier"]:
            added = variant["addedSummary"]
            lines.append(
                f"| `{variant['name']}` | {variant['riskOnDays']} | {variant['addedDays']} | "
                f"{format_optional_percent(added['fwd5']['mean'])} | {format_optional_percent(added['fwd10']['mean'])} | "
                f"{format_optional_percent(added['fwd5']['winRate'])} |"
            )
        lines.append("")

    lines.extend(["## 初步结论提示", ""])
    lines.append("- 若某个放宽方案新增天数少且 Fwd5/Fwd10 为负，不应把它转成回测参数。")
    lines.append("- 若新增天数前瞻路径为正，也只能作为下一轮状态化门控假设，不能直接替代现有市场宽度硬线。")
    lines.append("- 若 Risk-Off 失败主要集中在 `upPct`，说明问题是短线扩散不足；若集中在 MA20/MA60，说明趋势宽度不足。")
    return "\n".join(lines) + "\n"


def summarize_for_stdout(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "label": item["window"]["label"],
            "riskOnDays": item["baseSummary"]["riskOnDays"],
            "riskOffDays": item["baseSummary"]["riskOffDays"],
            "riskOffFwd5": item["baseSummary"]["riskOffForward"]["fwd5"]["mean"],
            "bestAdded": best_added_variant(item["frontier"]),
        }
        for item in windows
    ]


def best_added_variant(frontier: list[dict[str, Any]]) -> dict[str, Any] | None:
    added = [item for item in frontier if item["addedDays"] > 0 and finite(item["addedSummary"]["fwd5"].get("mean"))]
    if not added:
        return None
    best = max(added, key=lambda item: float(item["addedSummary"]["fwd5"]["mean"]))
    return {
        "name": best["name"],
        "addedDays": best["addedDays"],
        "fwd5Mean": best["addedSummary"]["fwd5"]["mean"],
        "fwd10Mean": best["addedSummary"]["fwd10"]["mean"],
    }


if __name__ == "__main__":
    main()
