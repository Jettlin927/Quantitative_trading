from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean, median
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import finite, json_safe, should_enter
from backend.app.database import SessionLocal
from backend.app.main import query_backtest_stocks, stock_to_market_meta
from backend.app.schemas import MarketBacktestRequest
from scripts.research.run_portfolio_backtest import (
    apply_cross_section_strength_scores,
    build_industry_states,
    build_industry_moneyflow_cache,
    build_market_breadth_payload,
    build_market_states,
    build_portfolio_rules,
    entry_risk_filter_ok,
    industry_state_filter_ok,
    limit_up_entry_blocked,
    load_concept_cache,
    load_moneyflow_cache,
    load_signal_rows,
    signal_score,
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
from scripts.research.run_window_validation import build_validation_windows, filter_allowed_entry_dates, with_warmup_window


FORWARD_DAYS = (1, 3, 5, 10)


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose entry-state behavior in weak rolling windows.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--context", required=True, help="Mainline research context JSON.")
    parser.add_argument("--source-window-run", required=True, help="Window-validation run used for actual executed trades.")
    parser.add_argument("--strategy", default="trend-follow-maximum-profit-no-macd", help="Strategy preset.")
    parser.add_argument("--windows", default="Y1,R18-1,Y3", help="Comma-separated validation window labels.")
    parser.add_argument("--top-per-day", type=int, default=3, help="Unbought top signals to audit per Risk-On day.")
    args = parser.parse_args()

    context = read_json(Path(args.context))
    source = read_json(RUNS_ROOT / args.source_window_run / "results.json")
    requested_labels = [item.strip() for item in args.windows.split(",") if item.strip()]
    window_defs = {item["label"]: with_warmup_window(item) for item in build_validation_windows(context)}
    windows = [window_defs[label] for label in requested_labels if label in window_defs]
    if not windows:
        raise SystemExit("No requested windows were found in the validation schedule.")

    started_at = now_text()
    strategy = build_strategy(args.strategy, context)
    run_dir = RUNS_ROOT / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    source_windows = {item["window"]["label"]: item for item in source.get("windows", [])}
    results = []
    with SessionLocal() as db:
        for window in windows:
            label = window["label"]
            source_window = source_windows.get(label)
            if not source_window:
                raise SystemExit(f"Source run does not contain window {label}.")
            results.append(analyze_window(db, context, strategy, window, source_window, args.top_per_day))

    output = {
        "runId": args.run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "sourceWindowRun": args.source_window_run,
        "strategy": strategy,
        "context": context,
        "windows": results,
    }
    write_json(run_dir / "context.json", context)
    write_json(run_dir / "results.json", json_safe(output))
    write_text(run_dir / "review.md", render_review(args.run_id, args.source_window_run, results))
    print(json.dumps({"runId": args.run_id, "windows": summarize_for_stdout(results), "runDir": str(run_dir)}, ensure_ascii=False, indent=2))


def analyze_window(db: Any, context: dict[str, Any], strategy: dict[str, Any], window: dict[str, Any], source_window: dict[str, Any], top_per_day: int) -> dict[str, Any]:
    window_context = json.loads(json.dumps(context, ensure_ascii=False))
    window_start = date.fromisoformat(window["startDate"])
    window_end = date.fromisoformat(window["endDate"])
    window_context["scope"]["start_date"] = window["warmupStartDate"]
    window_context["scope"]["end_date"] = window["endDate"]
    filter_allowed_entry_dates(window_context, window_start, window_end)

    window_strategy = build_strategy(strategy["name"], window_context) if "name" in strategy else build_strategy("trend-follow-maximum-profit-no-macd", window_context)
    portfolio_rules = build_portfolio_rules(window_context, window_strategy["config"])
    payload = MarketBacktestRequest(**build_market_payload(window_context, window_strategy, max_stocks=None))
    market_state_payload = build_market_breadth_payload(window_context, window_strategy)

    stocks = [stock_to_market_meta(stock) for stock in query_backtest_stocks(db, payload)]
    moneyflow_cache = load_moneyflow_cache(portfolio_rules.get("moneyflowCachePath"))
    concept_cache = load_concept_cache(portfolio_rules.get("conceptCachePath"))
    industry_moneyflow_cache = build_industry_moneyflow_cache(moneyflow_cache, stocks)
    by_date, skipped = load_signal_rows(db, stocks, payload, window_strategy["config"], moneyflow_cache, industry_moneyflow_cache, concept_cache)
    rows_by_code = build_rows_by_code(by_date)

    market_state_by_date = by_date
    if market_state_payload is not None:
        market_state_stocks = [stock_to_market_meta(stock) for stock in query_backtest_stocks(db, market_state_payload)]
        market_state_by_date, _ = load_signal_rows(db, market_state_stocks, market_state_payload, window_strategy["config"])
    market_states = build_market_states(market_state_by_date, portfolio_rules["marketBreadthFilter"])

    actual_entries = defaultdict(set)
    for trade in source_window["result"].get("completedTrades", []):
        actual_entries[trade["entryDate"]].add(trade["ts_code"])

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    daily_rows = []
    previous_market_state: dict[str, Any] | None = None
    for trade_date in sorted(by_date):
        current_date = date.fromisoformat(trade_date)
        current_market_state = market_states.get(trade_date, default_market_state(trade_date))
        entry_market_state = previous_market_state if portfolio_rules["marketBreadthFilter"]["usePreviousTradingDay"] and previous_market_state else current_market_state
        if current_date < window_start or current_date > window_end:
            previous_market_state = current_market_state
            continue
        if not entry_market_state["riskOn"]:
            previous_market_state = current_market_state
            continue

        day_items = by_date[trade_date]
        industry_states = build_industry_states(day_items)
        classified = classify_day_candidates(day_items, window_strategy["config"], portfolio_rules, industry_states)
        passed = classified["passed"]
        apply_cross_section_strength_scores(passed, {}, portfolio_rules, industry_states)
        passed.sort(key=lambda item: item["score"], reverse=True)

        actual_codes = actual_entries.get(trade_date, set())
        for signal in passed:
            sample = sample_from_signal(signal, rows_by_code, "eligible_signal")
            if signal["stock"]["ts_code"] in actual_codes:
                buckets["actual_bought"].append({**sample, "bucket": "actual_bought"})
            elif len([item for item in buckets["top_unbought"] if item["date"] == trade_date]) < top_per_day:
                buckets["top_unbought"].append({**sample, "bucket": "top_unbought"})

        for reason, items in classified["blocked"].items():
            bucket_name = f"blocked_{reason}"
            for item in items:
                buckets[bucket_name].append({**sample_from_item(item, rows_by_code), "bucket": bucket_name})

        daily_rows.append(
            {
                "date": trade_date,
                "actualBuys": len(actual_codes),
                "eligibleSignals": len(passed),
                "topUnboughtAudited": min(top_per_day, max(0, len(passed) - len(actual_codes))),
                "blockedByEntryRange": len(classified["blocked"].get("entryRange", [])),
                "blockedByEntryGap": len(classified["blocked"].get("entryGap", [])),
                "blockedByIndustryState": len(classified["blocked"].get("industryState", [])),
                "blockedByStrategyRisk": len(classified["blocked"].get("strategyRisk", [])),
                "marketAboveMa20Pct": entry_market_state.get("aboveMa20Pct"),
                "marketAboveMa60Pct": entry_market_state.get("aboveMa60Pct"),
                "marketUpPct": entry_market_state.get("upPct"),
            }
        )
        previous_market_state = current_market_state

    bucket_summaries = {name: summarize_samples(samples) for name, samples in sorted(buckets.items())}
    return {
        "window": window,
        "sourceMetrics": {
            "annualizedReturn": source_window["analysis"].get("annualizedReturn"),
            "sharpeRatio": source_window["analysis"].get("sharpeRatio"),
            "maxDrawdown": source_window["analysis"].get("maxDrawdown"),
            "completedTradeCount": source_window["analysis"].get("completedTradeCount"),
            "marketRiskOnDays": source_window["analysis"].get("marketRiskOnDays"),
        },
        "scope": {
            "candidates": len(stocks),
            "skipped": skipped,
            "riskOnDaysAnalyzed": len(daily_rows),
        },
        "bucketSummaries": bucket_summaries,
        "dailySummary": summarize_daily_rows(daily_rows),
        "examples": {
            name: worst_examples(samples)
            for name, samples in sorted(buckets.items())
            if name in {"actual_bought", "top_unbought", "blocked_entryRange", "blocked_entryGap", "blocked_industryState"}
        },
    }


def classify_day_candidates(day_items: list[dict[str, Any]], cfg: dict[str, Any], portfolio_rules: dict[str, Any], industry_states: dict[str, dict[str, float]]) -> dict[str, Any]:
    passed = []
    blocked: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in day_items:
        if limit_up_entry_blocked(item["row"], item.get("prev"), cfg):
            blocked["limitUp"].append(item)
            continue
        signal = should_enter(item["row"], item.get("prev"), cfg)
        if not signal["ok"]:
            if signal.get("blockedByRisk"):
                blocked["strategyRisk"].append(item)
            continue
        industry = str(item["stock"].get("industry") or "unknown")
        if not industry_state_filter_ok(industry, industry_states, portfolio_rules):
            blocked["industryState"].append(item)
            continue
        risk_ok, risk_metrics, risk_reason = entry_risk_filter_ok(item["row"], item.get("prev"), portfolio_rules)
        if not risk_ok:
            blocked[entry_risk_bucket(risk_reason)].append({**item, "riskMetrics": risk_metrics})
            continue
        score = signal_score(item["row"], portfolio_rules)
        passed.append({**item, "score": score, "reason": signal["reason"], "knownSymbol": False, "riskMetrics": risk_metrics})
    return {"passed": passed, "blocked": blocked}


def entry_risk_bucket(reason: str | None) -> str:
    if not reason:
        return "entryRisk"
    if "振幅" in reason:
        return "entryRange"
    if "缺口" in reason:
        return "entryGap"
    return "entryRisk"


def build_rows_by_code(by_date: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    rows_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade_date in sorted(by_date):
        for item in by_date[trade_date]:
            rows_by_code[item["stock"]["ts_code"]].append(item["row"])
    return rows_by_code


def sample_from_signal(signal: dict[str, Any], rows_by_code: dict[str, list[dict[str, Any]]], bucket: str) -> dict[str, Any]:
    sample = sample_from_item(signal, rows_by_code)
    sample["score"] = signal.get("score")
    sample["scoreParts"] = signal.get("scoreParts")
    sample["riskMetrics"] = signal.get("riskMetrics")
    sample["bucket"] = bucket
    return sample


def sample_from_item(item: dict[str, Any], rows_by_code: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    row = item["row"]
    stock = item["stock"]
    sample = {
        "date": row["date"],
        "ts_code": stock["ts_code"],
        "name": stock.get("name"),
        "industry": stock.get("industry"),
        "close": row.get("close"),
        "return20": row.get("return20"),
        "return60": row.get("return60"),
        "distanceFromHigh60Pct": row.get("distanceFromHigh60Pct"),
        "amountRatio": row.get("amountRatio"),
        "amountEfficiency20": row.get("amountEfficiency20"),
    }
    sample.update(forward_returns(rows_by_code.get(stock["ts_code"], []), row["date"]))
    return sample


def forward_returns(rows: list[dict[str, Any]], trade_date: str) -> dict[str, float | None]:
    index_by_date = {row["date"]: index for index, row in enumerate(rows)}
    index = index_by_date.get(trade_date)
    if index is None:
        return {f"fwd{day}": None for day in FORWARD_DAYS}
    current_close = rows[index].get("close")
    result: dict[str, float | None] = {}
    for day in FORWARD_DAYS:
        target_index = index + day
        if not finite(current_close) or target_index >= len(rows) or not finite(rows[target_index].get("close")):
            result[f"fwd{day}"] = None
        else:
            result[f"fwd{day}"] = float(rows[target_index]["close"]) / float(current_close) - 1.0
    return result


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {"count": len(samples)}
    for day in FORWARD_DAYS:
        key = f"fwd{day}"
        values = [float(item[key]) for item in samples if finite(item.get(key))]
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


def summarize_daily_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"riskOnDays": 0}
    return {
        "riskOnDays": len(rows),
        "avgEligibleSignals": mean(row["eligibleSignals"] for row in rows),
        "avgActualBuys": mean(row["actualBuys"] for row in rows),
        "avgBlockedByEntryRange": mean(row["blockedByEntryRange"] for row in rows),
        "avgBlockedByEntryGap": mean(row["blockedByEntryGap"] for row in rows),
        "avgBlockedByIndustryState": mean(row["blockedByIndustryState"] for row in rows),
        "avgBlockedByStrategyRisk": mean(row["blockedByStrategyRisk"] for row in rows),
        "avgMarketUpPct": mean(float(row["marketUpPct"]) for row in rows if finite(row.get("marketUpPct"))),
    }


def worst_examples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = [item for item in samples if finite(item.get("fwd5"))]
    return sorted(valid, key=lambda item: float(item["fwd5"]))[:10]


def default_market_state(trade_date: str) -> dict[str, Any]:
    return {"date": trade_date, "riskOn": True, "samples": 0, "aboveMa20Pct": None, "aboveMa60Pct": None, "upPct": None}


def render_review(run_id: str, source_window_run: str, windows: list[dict[str, Any]]) -> str:
    lines = [
        f"# {run_id} 弱窗口入场状态诊断",
        "",
        f"- 来源滚动验证：`{source_window_run}`",
        "- 口径：按当前主线上下文重建 Risk-On 日候选；前瞻收益为信号日收盘到后续 1/3/5/10 个交易日收盘的诊断收益，不代表真实成交收益。",
        "",
        "## 窗口概览",
        "",
        "| 窗口 | Risk-On天 | 实际年化 | 实际交易 | 合格信号/日 | 买入/日 | 振幅拦截/日 | 缺口拦截/日 | 行业状态拦截/日 | 策略风险拦截/日 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in windows:
        daily = item["dailySummary"]
        source = item["sourceMetrics"]
        lines.append(
            f"| `{item['window']['label']}` | {daily.get('riskOnDays', 0)} | {format_optional_percent(source.get('annualizedReturn'))} | "
            f"{source.get('completedTradeCount')} | {format_number(daily.get('avgEligibleSignals'))} | {format_number(daily.get('avgActualBuys'))} | "
            f"{format_number(daily.get('avgBlockedByEntryRange'))} | {format_number(daily.get('avgBlockedByEntryGap'))} | "
            f"{format_number(daily.get('avgBlockedByIndustryState'))} | {format_number(daily.get('avgBlockedByStrategyRisk'))} |"
        )

    lines.extend(["", "## 前瞻收益摘要", ""])
    for item in windows:
        lines.append(f"### {item['window']['label']}")
        lines.append("")
        lines.append("| 分组 | 样本 | Fwd1均值 | Fwd3均值 | Fwd5均值 | Fwd10均值 | Fwd5胜率 |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
        for bucket in ["actual_bought", "top_unbought", "blocked_entryRange", "blocked_entryGap", "blocked_industryState", "blocked_strategyRisk"]:
            summary = item["bucketSummaries"].get(bucket)
            if not summary:
                continue
            fwd5 = summary["fwd5"]
            lines.append(
                f"| `{bucket}` | {summary['count']} | {format_optional_percent(summary['fwd1']['mean'])} | "
                f"{format_optional_percent(summary['fwd3']['mean'])} | {format_optional_percent(fwd5['mean'])} | "
                f"{format_optional_percent(summary['fwd10']['mean'])} | {format_optional_percent(fwd5['winRate'])} |"
            )
        lines.append("")

    lines.extend(["## 初步结论", ""])
    lines.append("- 若 `top_unbought` 的前瞻收益明显优于 `actual_bought`，优先研究排序与持仓/周频约束导致的机会替换。")
    lines.append("- 若 `blocked_entryRange` 或 `blocked_entryGap` 前瞻收益为正，不代表可直接放宽硬过滤；它只说明需要额外的状态确认或预算约束来区分可交易强势与过热陷阱。")
    lines.append("- 若实际买入和高分未买入都没有稳定正前瞻收益，则下一步应优先扩展资金流、主题持续性或公告/财务质量数据，而不是继续技术指标微调。")
    return "\n".join(lines) + "\n"


def format_number(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2f}"


def summarize_for_stdout(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "label": item["window"]["label"],
            "riskOnDays": item["dailySummary"].get("riskOnDays"),
            "actualBoughtFwd5": item["bucketSummaries"].get("actual_bought", {}).get("fwd5", {}).get("mean"),
            "topUnboughtFwd5": item["bucketSummaries"].get("top_unbought", {}).get("fwd5", {}).get("mean"),
            "blockedRangeFwd5": item["bucketSummaries"].get("blocked_entryRange", {}).get("fwd5", {}).get("mean"),
            "blockedGapFwd5": item["bucketSummaries"].get("blocked_entryGap", {}).get("fwd5", {}).get("mean"),
            "blockedIndustryStateFwd5": item["bucketSummaries"].get("blocked_industryState", {}).get("fwd5", {}).get("mean"),
        }
        for item in results
    ]


if __name__ == "__main__":
    main()
