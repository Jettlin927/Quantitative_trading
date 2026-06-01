from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import finite, json_safe
from backend.app.database import SessionLocal
from backend.app.main import query_backtest_stocks, stock_to_market_meta
from backend.app.schemas import MarketBacktestRequest
from scripts.research.run_portfolio_backtest import (
    build_entry_signals,
    build_industry_moneyflow_cache,
    build_industry_states,
    build_portfolio_rules,
    default_market_state,
    load_concept_cache,
    load_moneyflow_cache,
    load_signal_rows,
    update_failure_throttle,
    update_gap_stop_cooldowns,
    update_industry_overnight_risk,
)
from scripts.research.run_research_round import RUNS_ROOT, build_market_payload, build_strategy, now_text, read_json, write_json, write_text


WINDOWS = {
    "Y1": ("2023-05-30", "2024-05-30"),
    "Y2": ("2024-05-30", "2025-05-30"),
    "Y3": ("2025-05-30", "2026-05-30"),
    "R18-1": ("2023-05-30", "2024-11-30"),
    "R18-2": ("2023-11-30", "2025-05-30"),
    "R18-3": ("2024-05-30", "2025-11-30"),
    "R18-4": ("2024-11-30", "2026-05-30"),
}

GROUPS = ["actualTraded", "sameDayUntradedTop5", "capacityBlockedTop5", "riskOnTop5Untraded"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose full candidate-universe pre-entry paths for a portfolio run.")
    parser.add_argument("--run-id", required=True, help="Output diagnostic run id under docs/research/runs.")
    parser.add_argument("--source-run", required=True, help="Portfolio run to diagnose.")
    parser.add_argument("--strategy", default="trend-follow-maximum-profit-no-macd")
    parser.add_argument("--context", default=None, help="Context JSON path. Defaults to source run context.json.")
    parser.add_argument("--moneyflow-cache", default="docs/research/runs/002-moneyflow-cache-mainline-001/moneyflow-cache.jsonl")
    parser.add_argument("--concept-cache", default=None)
    parser.add_argument("--date-mode", choices=["actual-entry", "all-risk-on"], default="actual-entry")
    parser.add_argument("--top-n", type=int, default=5, help="Top untraded signals retained per date/category.")
    args = parser.parse_args()

    started_at = now_text()
    run_dir = RUNS_ROOT / args.run_id
    if (run_dir / "results.json").exists():
        raise SystemExit(f"Run already exists: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)

    source_dir = RUNS_ROOT / args.source_run
    source = read_json(source_dir / "results.json")
    context = read_json(Path(args.context) if args.context else source_dir / "context.json")
    strategy = build_strategy(args.strategy, context)
    portfolio_rules = build_portfolio_rules(context, strategy["config"])
    if args.moneyflow_cache is not None:
        portfolio_rules["moneyflowCachePath"] = args.moneyflow_cache
    if args.concept_cache is not None:
        portfolio_rules["conceptCachePath"] = args.concept_cache
    payload = MarketBacktestRequest(**build_market_payload(context, strategy, max_stocks=None))

    with SessionLocal() as db:
        samples, diagnostics = build_candidate_samples(db, source, payload, strategy["config"], portfolio_rules, int(args.top_n), args.date_mode)

    output = {
        "runId": args.run_id,
        "sourceRun": args.source_run,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "topN": int(args.top_n),
        "dateMode": args.date_mode,
        "diagnostics": diagnostics,
        "windows": {
            label: summarize_window(samples, bounds)
            for label, bounds in [("ALL", None), *WINDOWS.items()]
        },
        "samples": sorted(samples, key=lambda item: (item["date"], item["rank"]))[:2000],
    }

    write_json(run_dir / "results.json", json_safe(output))
    write_text(run_dir / "review.md", render_review(output))
    print(json.dumps({"runId": args.run_id, "summary": compact_summary(output), "runDir": str(run_dir)}, ensure_ascii=False, indent=2))


def build_candidate_samples(
    db: Any,
    source: dict[str, Any],
    payload: MarketBacktestRequest,
    cfg: dict[str, Any],
    portfolio_rules: dict[str, Any],
    top_n: int,
    date_mode: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stocks = [stock_to_market_meta(stock) for stock in query_backtest_stocks(db, payload)]
    moneyflow_cache = load_moneyflow_cache(portfolio_rules.get("moneyflowCachePath"))
    concept_cache = load_concept_cache(portfolio_rules.get("conceptCachePath"))
    industry_moneyflow_cache = build_industry_moneyflow_cache(moneyflow_cache, stocks)
    by_date, skipped = load_signal_rows(db, stocks, payload, cfg, moneyflow_cache, industry_moneyflow_cache, concept_cache)
    rows_by_code, index_by_code_date = build_row_indexes(by_date)

    trades = source.get("result", {}).get("trades", [])
    completed = source.get("result", {}).get("completedTrades", [])
    actual_buys_by_date = actual_buy_keys_by_date(trades)
    actual_buy_symbols_by_date = {trade_date: {key[0] for key in keys} for trade_date, keys in actual_buys_by_date.items()}
    completed_by_exit = group_completed_by_exit(completed)
    weekly_actual_buys = weekly_buy_counts(trades)

    symbol_cooldowns: dict[str, date] = {}
    gap_stop_symbol_cooldowns: dict[str, date] = {}
    symbol_failure_counts: dict[str, int] = {}
    industry_cooldowns: dict[str, date] = {}
    gap_stop_industry_cooldowns: dict[str, date] = {}
    industry_weekly_losses: dict[str, int] = {}
    industry_overnight_gap_history: dict[str, list[tuple[int, int]]] = defaultdict(list)
    gap_stop_market_cooldown_until: date | None = None
    traded_symbols: set[str] = set()
    throttle_stats = {"symbolCooldownEvents": 0, "industryCooldownEvents": 0, "blockedSymbolCooldownSignals": 0, "blockedIndustryCooldownSignals": 0}
    market_stats: dict[str, int | float | dict[str, int]] = defaultdict(int)

    samples: list[dict[str, Any]] = []
    signal_days = 0
    risk_on_signal_days = 0
    source_market_states = source_entry_market_states(source)
    target_dates = set(by_date) if date_mode == "all-risk-on" else set(actual_buys_by_date)
    for trade_date in sorted(by_date):
        if trade_date not in target_dates:
            continue
        current_date = date.fromisoformat(trade_date)
        day_items = by_date[trade_date]
        entry_market_state = source_market_states.get(trade_date, default_market_state(trade_date))

        for completed_trade in completed_by_exit.get(trade_date, []):
            if float(completed_trade.get("returnPct") or 0.0) > 0:
                continue
            update_failure_throttle(
                completed_trade,
                current_date,
                symbol_cooldowns,
                symbol_failure_counts,
                industry_cooldowns,
                industry_weekly_losses,
                portfolio_rules,
                throttle_stats,
            )
            gap_stop_market_cooldown_until = update_gap_stop_cooldowns(
                completed_trade,
                current_date,
                gap_stop_symbol_cooldowns,
                gap_stop_industry_cooldowns,
                gap_stop_market_cooldown_until,
                cfg,
                market_stats,
            )

        positions = open_positions_from_completed(completed, current_date)
        profitable_exits = {
            str(item["ts_code"])
            for item in completed_by_exit.get(trade_date, [])
            if float(item.get("returnPct") or 0.0) > 0
        }
        traded_symbols.update(prior_buy_symbols(trades, current_date))
        industry_overnight_risk = update_industry_overnight_risk(industry_overnight_gap_history, day_items, cfg)
        industry_states = build_industry_states(day_items)
        signals = build_entry_signals(
            day_items,
            positions,
            profitable_exits,
            cfg,
            portfolio_rules,
            current_date,
            symbol_cooldowns,
            symbol_failure_counts,
            industry_cooldowns,
            gap_stop_symbol_cooldowns,
            gap_stop_industry_cooldowns,
            industry_overnight_risk,
            industry_states,
            traded_symbols,
            throttle_stats,
            market_stats,
            entry_market_state,
        )
        if signals:
            signal_days += 1
        actual_symbols = actual_buy_symbols_by_date.get(trade_date, set())
        buy_slots = max(0, int(portfolio_rules["maxPositions"]) - len(positions))
        weekly_remaining = max(0, int(portfolio_rules["weeklyBuyLimit"]) - weekly_actual_buys.get(week_key(trade_date), 0))
        capacity_blocked = bool(entry_market_state.get("riskOn")) and bool(signals) and not actual_symbols and (buy_slots <= 0 or weekly_remaining <= 0)
        if bool(entry_market_state.get("riskOn")) and signals:
            risk_on_signal_days += 1
            samples.extend(
                build_signal_samples(
                    trade_date,
                    signals,
                    actual_symbols,
                    rows_by_code,
                    index_by_code_date,
                    top_n,
                    capacity_blocked,
                    buy_slots,
                    weekly_remaining,
                )
            )

    diagnostics = {
        "tradeCandidateStocks": len(stocks),
        "tradeCandidateSkipped": skipped,
        "dateMode": date_mode,
        "targetDates": len(target_dates),
        "signalDays": signal_days,
        "riskOnSignalDays": risk_on_signal_days,
        "sampleCount": len(samples),
    }
    return samples, diagnostics


def build_signal_samples(
    trade_date: str,
    signals: list[dict[str, Any]],
    actual_symbols: set[str],
    rows_by_code: dict[str, list[dict[str, Any]]],
    index_by_code_date: dict[tuple[str, str], int],
    top_n: int,
    capacity_blocked: bool,
    buy_slots: int,
    weekly_remaining: int,
) -> list[dict[str, Any]]:
    rows = []
    actual_count = len(actual_symbols)
    for rank, signal in enumerate(signals, start=1):
        ts_code = str(signal["stock"]["ts_code"])
        is_actual = ts_code in actual_symbols
        is_top_untraded = (not is_actual) and rank <= max(top_n, actual_count + top_n)
        is_capacity = (not is_actual) and capacity_blocked and rank <= top_n
        if not is_actual and not is_top_untraded and not is_capacity:
            continue
        row = signal["row"]
        sample = {
            "date": trade_date,
            "ts_code": ts_code,
            "name": signal["stock"].get("name"),
            "industry": signal["stock"].get("industry"),
            "rank": rank,
            "score": signal.get("score"),
            "group": group_name(is_actual, bool(actual_symbols), is_capacity),
            "buySlots": buy_slots,
            "weeklyRemaining": weekly_remaining,
            "entryRangePct": signal.get("riskMetrics", {}).get("entryRangePct"),
            "gapPct": signal.get("riskMetrics", {}).get("gapPct"),
            "priorVolumeRatioBasic": signal.get("riskMetrics", {}).get("priorVolumeRatioBasic"),
            "amountRatio": row.get("amountRatio"),
            "rsiStrategy": row.get("rsiStrategy"),
            "macdHist": row.get("macdHist"),
            "macdHistDelta1d": macd_delta(rows_by_code, index_by_code_date, ts_code, trade_date, 1),
            "rsiDelta3d": rsi_delta(rows_by_code, index_by_code_date, ts_code, trade_date, 3),
            "fwd1": forward_return(rows_by_code, index_by_code_date, ts_code, trade_date, 1),
            "fwd3": forward_return(rows_by_code, index_by_code_date, ts_code, trade_date, 3),
            "fwd5": forward_return(rows_by_code, index_by_code_date, ts_code, trade_date, 5),
            "fwd10": forward_return(rows_by_code, index_by_code_date, ts_code, trade_date, 10),
        }
        for key in [
            "baseScore",
            "rsiLevelRank",
            "rsiBalanceRank",
            "amountRatioRank",
            "amountEfficiencyRsiRank",
            "macdHistDeltaRank",
            "moneyflowMarketSurgeQualityRank",
            "entryVolumeInefficiencyCrowdingScorePenalty",
        ]:
            sample[key] = (signal.get("scoreParts") or {}).get(key)
        rows.append(sample)
    return rows


def group_name(is_actual: bool, has_actual_same_day: bool, is_capacity: bool) -> str:
    if is_actual:
        return "actualTraded"
    if is_capacity:
        return "capacityBlockedTop5"
    if has_actual_same_day:
        return "sameDayUntradedTop5"
    return "riskOnTop5Untraded"


def summarize_window(samples: list[dict[str, Any]], bounds: tuple[str, str] | None) -> dict[str, Any]:
    subset = select_window(samples, bounds)
    return {
        group: summarize_samples([item for item in subset if item["group"] == group])
        for group in GROUPS
    }


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(samples),
        "avgRank": mean_or_none([item.get("rank") for item in samples]),
        "avgScore": mean_or_none([item.get("score") for item in samples]),
        "avgFwd1": mean_or_none([item.get("fwd1") for item in samples]),
        "avgFwd3": mean_or_none([item.get("fwd3") for item in samples]),
        "avgFwd5": mean_or_none([item.get("fwd5") for item in samples]),
        "avgFwd10": mean_or_none([item.get("fwd10") for item in samples]),
        "winRateFwd5": win_rate([item.get("fwd5") for item in samples]),
        "avgRsiDelta3d": mean_or_none([item.get("rsiDelta3d") for item in samples]),
        "avgAmountRatio": mean_or_none([item.get("amountRatio") for item in samples]),
        "avgEntryRangePct": mean_or_none([item.get("entryRangePct") for item in samples]),
    }


def render_review(output: dict[str, Any]) -> str:
    lines = [
        f"# {output['runId']} 候选全集入场前路径诊断",
        "",
        f"- 来源 run：`{output['sourceRun']}`",
        f"- 开始时间：{output['startedAt']}",
        f"- 结束时间：{output['finishedAt']}",
        f"- topN：`{output['topN']}`",
        f"- 日期模式：`{output['dateMode']}`",
        f"- 样本数：`{output['diagnostics']['sampleCount']}`",
        f"- Risk-On 有信号日期：`{output['diagnostics']['riskOnSignalDays']}`",
        "",
        "## 分组说明",
        "",
        "- `actualTraded`：真实买入的信号。",
        "- `sameDayUntradedTop5`：真实买入日里，排名靠前但未成交的信号。",
        "- `capacityBlockedTop5`：Risk-On 且有信号，但因持仓/周频名额已满而未买的 top 信号。",
        "- `riskOnTop5Untraded`：Risk-On 有信号但当日无真实买入，且不是容量满的 top 信号。",
        "",
        "## 窗口摘要",
        "",
        "| 窗口 | 分组 | 样本 | 平均排名 | Fwd3 | Fwd5 | Fwd10 | Fwd5胜率 | RSI 3日变化 | 成交额比 | 入场振幅 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label in ["ALL", "Y1", "Y2", "Y3", "R18-1", "R18-4"]:
        for group in GROUPS:
            item = output["windows"][label][group]
            lines.append(
                f"| `{label}` | `{group}` | `{item['count']}` | {fmt(item['avgRank'])} | {fmt_pct(item['avgFwd3'])} | "
                f"{fmt_pct(item['avgFwd5'])} | {fmt_pct(item['avgFwd10'])} | {fmt_pct(item['winRateFwd5'])} | "
                f"{fmt(item['avgRsiDelta3d'])} | {fmt(item['avgAmountRatio'])} | {fmt_pct(item['avgEntryRangePct'])} |"
            )
    lines.extend(
        [
            "",
            "## 结论提示",
            "",
            "- 本诊断重建每日通过过滤的候选信号池，并用真实买入记录标记成交；它不改变组合回测语义。",
            "- 未成交候选只看后续 close-to-close 路径，不考虑资金释放、真实成交价格、周频复买和组合路径反馈，因此只能作为下一轮因子研究线索。",
        ]
    )
    return "\n".join(lines) + "\n"


def compact_summary(output: dict[str, Any]) -> dict[str, Any]:
    return {
        label: {
            group: {
                "count": item["count"],
                "avgFwd5": item["avgFwd5"],
                "winRateFwd5": item["winRateFwd5"],
            }
            for group, item in output["windows"][label].items()
        }
        for label in ["ALL", "Y1", "R18-1", "R18-4"]
    }


def actual_buy_keys_by_date(trades: list[dict[str, Any]]) -> dict[str, set[tuple[str, str]]]:
    result: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for trade in trades:
        if trade.get("action") == "买入":
            trade_date = str(trade["date"])
            result[trade_date].add((str(trade["ts_code"]), trade_date))
    return result


def source_entry_market_states(source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for item in source.get("result", {}).get("equity", []):
        trade_date = str(item.get("date"))
        result[trade_date] = {
            "date": trade_date,
            "riskOn": bool(item.get("marketRiskOn")),
            "baseRiskOn": bool(item.get("marketBaseRiskOn", item.get("marketRiskOn"))),
            "softRiskOn": bool(item.get("marketSoftRiskOn", False)),
            "failedChecks": item.get("marketBreadthFailedChecks") or [],
            "aboveMa20Pct": item.get("marketAboveMa20Pct"),
            "aboveMa60Pct": item.get("marketAboveMa60Pct"),
            "upPct": item.get("marketUpPct"),
        }
    return result


def weekly_buy_counts(trades: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for trade in trades:
        if trade.get("action") == "买入":
            result[week_key(str(trade["date"]))] += 1
    return result


def prior_buy_symbols(trades: list[dict[str, Any]], current_date: date) -> set[str]:
    return {
        str(trade["ts_code"])
        for trade in trades
        if trade.get("action") == "买入" and date.fromisoformat(str(trade["date"])) < current_date
    }


def group_completed_by_exit(completed: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in completed:
        result[str(trade["exitDate"])].append(trade)
    return result


def open_positions_from_completed(completed: list[dict[str, Any]], current_date: date) -> dict[str, dict[str, Any]]:
    positions = {}
    for trade in completed:
        entry = date.fromisoformat(str(trade["entryDate"]))
        exit_date = date.fromisoformat(str(trade["exitDate"]))
        if entry < current_date < exit_date:
            positions[str(trade["ts_code"])] = {"ts_code": str(trade["ts_code"])}
    return positions


def build_row_indexes(by_date: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, list[dict[str, Any]]], dict[tuple[str, str], int]]:
    rows_by_code: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for trade_date, items in by_date.items():
        for item in items:
            rows_by_code[str(item["stock"]["ts_code"])][trade_date] = item["row"]
    sorted_rows = {ts_code: [mapping[key] for key in sorted(mapping)] for ts_code, mapping in rows_by_code.items()}
    index = {
        (ts_code, row["date"]): row_index
        for ts_code, rows in sorted_rows.items()
        for row_index, row in enumerate(rows)
    }
    return sorted_rows, index


def forward_return(rows_by_code: dict[str, list[dict[str, Any]]], index_by_code_date: dict[tuple[str, str], int], ts_code: str, trade_date: str, horizon: int) -> float | None:
    rows = rows_by_code.get(ts_code) or []
    index = index_by_code_date.get((ts_code, trade_date))
    if index is None or index + horizon >= len(rows):
        return None
    close = rows[index].get("close")
    future = rows[index + horizon].get("close")
    return float(future) / float(close) - 1 if finite(close) and finite(future) and float(close) else None


def macd_delta(rows_by_code: dict[str, list[dict[str, Any]]], index_by_code_date: dict[tuple[str, str], int], ts_code: str, trade_date: str, horizon: int) -> float | None:
    rows = rows_by_code.get(ts_code) or []
    index = index_by_code_date.get((ts_code, trade_date))
    if index is None or index - horizon < 0:
        return None
    current = rows[index].get("macdHist")
    past = rows[index - horizon].get("macdHist")
    return float(current) - float(past) if finite(current) and finite(past) else None


def rsi_delta(rows_by_code: dict[str, list[dict[str, Any]]], index_by_code_date: dict[tuple[str, str], int], ts_code: str, trade_date: str, horizon: int) -> float | None:
    rows = rows_by_code.get(ts_code) or []
    index = index_by_code_date.get((ts_code, trade_date))
    if index is None or index - horizon < 0:
        return None
    current = rows[index].get("rsiStrategy")
    past = rows[index - horizon].get("rsiStrategy")
    return float(current) - float(past) if finite(current) and finite(past) else None


def select_window(samples: list[dict[str, Any]], bounds: tuple[str, str] | None) -> list[dict[str, Any]]:
    if bounds is None:
        return list(samples)
    start, end = bounds
    return [item for item in samples if start <= item["date"] < end]


def week_key(value: str) -> str:
    parsed = date.fromisoformat(value)
    year, week, _ = parsed.isocalendar()
    return f"{year}-W{week:02d}"


def mean_or_none(values: list[Any]) -> float | None:
    selected = [float(value) for value in values if finite(value)]
    return mean(selected) if selected else None


def win_rate(values: list[Any]) -> float | None:
    selected = [float(value) for value in values if finite(value)]
    return sum(1 for value in selected if value > 0) / len(selected) if selected else None


def fmt(value: Any) -> str:
    if not finite(value):
        return "n/a"
    return f"`{float(value):.2f}`"


def fmt_pct(value: Any) -> str:
    if not finite(value):
        return "n/a"
    return f"`{float(value):.2%}`"


if __name__ == "__main__":
    main()
