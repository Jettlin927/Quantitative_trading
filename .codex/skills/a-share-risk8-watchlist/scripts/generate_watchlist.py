from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any


NO_PERMISSION_PREFIXES = ("300", "301", "688", "689")
DEFAULT_SPEC_PATH = Path("docs/research/executable-strategy-cross-section-risk8.json")


def find_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in [current, *current.parents]:
        if (parent / "docker-compose.yml").exists() and (parent / "backend").exists():
            return parent
    return Path.cwd()


REPO_ROOT = find_repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from sqlalchemy import text

    from backend.app.database import SessionLocal
    from backend.app.main import query_backtest_stocks, stock_to_market_meta
    from backend.app.schemas import MarketBacktestRequest
    from scripts.research.run_research_round import build_market_payload, build_strategy, read_json
    from scripts.research.run_portfolio_backtest import (
        build_entry_signals,
        build_market_breadth_payload,
        build_market_states,
        build_portfolio_rules,
        cap_quantity_for_entry_size_haircut,
        default_market_state,
        execution_buy_price,
        load_signal_rows,
        size_position,
    )
except ModuleNotFoundError as error:
    raise SystemExit(
        "Missing project runtime dependencies. Run this script inside the API container, for example:\n"
        "docker compose exec -T api python .codex\\skills\\a-share-risk8-watchlist\\scripts\\generate_watchlist.py --date latest"
    ) from error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an A-share Risk8 watchlist from the current fixed strategy spec.")
    parser.add_argument("--date", default="latest", help="Target trade date, YYYY-MM-DD, or latest.")
    parser.add_argument("--top", type=int, default=30, help="Maximum rows to print.")
    parser.add_argument("--capital", type=float, default=100000.0, help="Reference capital for sizing.")
    parser.add_argument("--warmup-days", type=int, default=260, help="Calendar days of indicator warmup before target date.")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Output format.")
    parser.add_argument("--spec", default=str(DEFAULT_SPEC_PATH), help="Path to executable strategy spec JSON.")
    parser.add_argument("--include-no-permission-boards", action="store_true", help="Include ChiNext and STAR Market prefixes.")
    parser.add_argument("--affordable-only", action="store_true", help="Only output rows with at least one lot for --capital.")
    return parser.parse_args()


def latest_trade_date() -> date:
    with SessionLocal() as db:
        value = db.execute(text("select max(trade_date) from stock_daily_bars")).scalar()
    if value is None:
        raise SystemExit("No daily bars found in local database.")
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def load_current_spec(spec_path: Path) -> tuple[dict[str, Any], dict[str, Any], Path]:
    spec = read_json(spec_path)
    evidence_run = spec.get("evidenceRun")
    context_path = None
    if evidence_run:
        candidate = REPO_ROOT / "docs" / "research" / "runs" / str(evidence_run) / "context.json"
        if candidate.exists():
            context_path = candidate
    if context_path is None:
        raw_context_path = spec.get("contextPath")
        if not raw_context_path:
            raise SystemExit(f"Spec has no evidenceRun context or contextPath: {spec_path}")
        context_path = REPO_ROOT / raw_context_path
    if not context_path.exists():
        raise SystemExit(f"Context path does not exist: {context_path}")
    return spec, read_json(context_path), context_path


def is_no_permission_board(ts_code: str) -> bool:
    symbol = ts_code.split(".", 1)[0]
    return symbol.startswith(NO_PERMISSION_PREFIXES)


def pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def money(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2f}"


def build_watchlist(args: argparse.Namespace) -> dict[str, Any]:
    target_date = latest_trade_date() if args.date == "latest" else date.fromisoformat(args.date)
    warmup_start = target_date - timedelta(days=int(args.warmup_days))
    spec_path = (REPO_ROOT / args.spec).resolve() if not Path(args.spec).is_absolute() else Path(args.spec)
    spec, context, context_path = load_current_spec(spec_path)
    strategy_name = spec.get("entry", {}).get("strategyPreset") or "trend-follow-maximum-profit-no-macd"
    strategy = build_strategy(strategy_name, context)
    strategy_config = dict(strategy["config"])
    strategy_config["allowedEntryDates"] = []
    strategy_for_watch = {**strategy, "config": strategy_config}
    portfolio_rules = build_portfolio_rules(context, strategy_config)

    original_payload = MarketBacktestRequest(**build_market_payload(context, strategy_for_watch, max_stocks=None))
    warm_payload_data = build_market_payload(context, strategy_for_watch, max_stocks=None)
    warm_payload_data["start_date"] = warmup_start
    warm_payload_data["end_date"] = target_date
    warm_payload = MarketBacktestRequest(**warm_payload_data)

    market_state_payload = build_market_breadth_payload(context, strategy_for_watch)
    warm_market_state_payload = None
    if market_state_payload is not None:
        market_state_data = market_state_payload.model_dump() if hasattr(market_state_payload, "model_dump") else market_state_payload.dict()
        market_state_data["start_date"] = warmup_start
        market_state_data["end_date"] = target_date
        warm_market_state_payload = MarketBacktestRequest(**market_state_data)

    with SessionLocal() as db:
        all_stocks = [stock_to_market_meta(stock) for stock in query_backtest_stocks(db, original_payload)]
        stocks = all_stocks if args.include_no_permission_boards else [stock for stock in all_stocks if not is_no_permission_board(stock["ts_code"])]
        by_date, skipped = load_signal_rows(db, stocks, warm_payload, strategy_config)

        market_state_by_date = by_date
        market_state_scope = {
            "source": "trade_candidates",
            "tested": len(stocks) - skipped,
            "candidates": len(stocks),
        }
        if warm_market_state_payload is not None and market_state_payload is not None:
            market_stocks = [stock_to_market_meta(stock) for stock in query_backtest_stocks(db, market_state_payload)]
            market_state_by_date, market_skipped = load_signal_rows(db, market_stocks, warm_market_state_payload, strategy_config)
            market_state_scope = {
                "source": "independent_breadth_scope",
                "tested": len(market_stocks) - market_skipped,
                "candidates": len(market_stocks),
            }

    available_dates = sorted(by_date)
    if not available_dates:
        raise SystemExit("No signal rows loaded for the selected universe/date window.")
    target_key = target_date.isoformat()
    if target_key not in by_date:
        usable_dates = [item for item in available_dates if item <= target_key]
        if not usable_dates:
            raise SystemExit(f"No trading day at or before {target_key} in loaded rows.")
        target_key = usable_dates[-1]
        target_date = date.fromisoformat(target_key)

    target_index = available_dates.index(target_key)
    previous_key = available_dates[target_index - 1] if target_index > 0 else None
    market_states = build_market_states(market_state_by_date, portfolio_rules["marketBreadthFilter"])
    current_market_state = market_states.get(target_key, default_market_state(target_key))
    if previous_key and portfolio_rules["marketBreadthFilter"]["usePreviousTradingDay"]:
        entry_market_state = market_states.get(previous_key, default_market_state(previous_key))
        entry_gate_date = previous_key
    else:
        entry_market_state = current_market_state
        entry_gate_date = target_key

    market_stats: dict[str, int | float] = {
        "blockedRiskSignals": 0,
        "blockedLimitUpSignals": 0,
        "blockedGapStopSymbolCooldownSignals": 0,
        "blockedGapStopIndustryCooldownSignals": 0,
        "blockedIndustryOvernightRiskSignals": 0,
        "entrySizeHaircutReducedEntries": 0,
        "entrySizeHaircutReductionShares": 0,
    }
    throttle_stats = {"blockedSymbolCooldownSignals": 0, "blockedIndustryCooldownSignals": 0}
    signals = build_entry_signals(
        by_date[target_key],
        {},
        set(),
        strategy_config,
        portfolio_rules,
        target_date,
        {},
        {},
        {},
        {},
        {},
        set(),
        set(),
        throttle_stats,
        market_stats,
    )

    rows = []
    for signal in signals:
        row = signal["row"]
        stock = signal["stock"]
        quantity, stop = size_position(row, args.capital, args.capital, {}, stock, portfolio_rules, strategy_config)
        raw_quantity = quantity
        quantity = cap_quantity_for_entry_size_haircut(quantity, signal.get("riskMetrics") or {}, portfolio_rules, strategy_config, market_stats)
        buy_ref = execution_buy_price(row["close"], strategy_config)
        item = {
            "rank": len(rows) + 1,
            "ts_code": stock["ts_code"],
            "name": stock.get("name"),
            "industry": stock.get("industry"),
            "score": signal["score"],
            "close": row["close"],
            "buy_ref": buy_ref,
            "stop": stop,
            "tp1": buy_ref * (1 + float(strategy_config["takeProfit1Pct"])),
            "tp2": buy_ref * (1 + float(strategy_config["takeProfit2Pct"])),
            "qty_for_capital": quantity,
            "raw_qty_for_capital": raw_quantity,
            "volume_ratio": row["volume"] / row["volMa"] if row.get("volume") and row.get("volMa") else None,
            "return20": row.get("return20"),
            "return60": row.get("return60"),
            "risk": signal.get("riskMetrics") or {},
            "scoreParts": signal.get("scoreParts") or {},
        }
        if args.affordable_only and item["qty_for_capital"] <= 0:
            continue
        item["rank"] = len(rows) + 1
        rows.append(item)
        if len(rows) >= int(args.top):
            break

    mode = "ACTIONABLE_BY_STRATEGY" if entry_market_state.get("riskOn") else "OBSERVE_ONLY_MARKET_RISK_OFF"
    return {
        "mode": mode,
        "notInvestmentAdvice": True,
        "targetDate": target_key,
        "requestedDate": args.date,
        "strategyId": spec.get("id"),
        "strategyStatus": spec.get("status"),
        "evidenceRun": spec.get("evidenceRun"),
        "contextPath": str(context_path.relative_to(REPO_ROOT)),
        "strategyPreset": strategy_name,
        "boardExclusion": None if args.include_no_permission_boards else list(NO_PERMISSION_PREFIXES),
        "capital": args.capital,
        "candidateCounts": {
            "beforeBoardExclusion": len(all_stocks),
            "afterBoardExclusion": len(stocks),
            "skipped": skipped,
            "stockLevelSignals": len(signals),
            "printed": len(rows),
        },
        "marketStateScope": market_state_scope,
        "entryGateDate": entry_gate_date,
        "entryMarketState": entry_market_state,
        "currentMarketState": current_market_state,
        "entryRiskFilter": portfolio_rules["entryRiskFilter"],
        "marketStats": market_stats,
        "rows": rows,
    }


def render_markdown(data: dict[str, Any]) -> str:
    lines = [
        f"# A-share Risk8 Watchlist - {data['targetDate']}",
        "",
        f"- Mode: `{data['mode']}`",
        f"- Strategy: `{data['strategyId']}` / `{data['evidenceRun']}`",
        f"- Status: `{data['strategyStatus']}`",
        f"- Context: `{data['contextPath']}`",
        f"- Board exclusion: `{data['boardExclusion']}`",
        f"- Counts: {data['candidateCounts']}",
        f"- Entry gate date: `{data['entryGateDate']}`; riskOn={data['entryMarketState'].get('riskOn')}; "
        f"MA20={pct(data['entryMarketState'].get('aboveMa20Pct'))}; "
        f"MA60={pct(data['entryMarketState'].get('aboveMa60Pct'))}; "
        f"UP={pct(data['entryMarketState'].get('upPct'))}",
        "",
        "| # | Code | Name | Industry | Score | Close | Buy Ref | Stop | TP1 | TP2 | Qty | Ret20 | Ret60 | Gap | Range | Intra |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in data["rows"]:
        risk = row["risk"]
        lines.append(
            "| {rank} | `{ts_code}` | {name} | {industry} | {score:.2f} | {close} | {buy_ref} | {stop} | {tp1} | {tp2} | {qty} | {ret20} | {ret60} | {gap} | {range_} | {intra} |".format(
                rank=row["rank"],
                ts_code=row["ts_code"],
                name=row["name"] or "",
                industry=row["industry"] or "",
                score=float(row["score"]),
                close=money(row["close"]),
                buy_ref=money(row["buy_ref"]),
                stop=money(row["stop"]),
                tp1=money(row["tp1"]),
                tp2=money(row["tp2"]),
                qty=row["qty_for_capital"],
                ret20=pct(row["return20"]),
                ret60=pct(row["return60"]),
                gap=pct(risk.get("gapPct")),
                range_=pct(risk.get("entryRangePct")),
                intra=pct(risk.get("intradayReturnPct")),
            )
        )
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    if args.top <= 0:
        raise SystemExit("--top must be positive.")
    if args.capital <= 0:
        raise SystemExit("--capital must be positive.")
    data = build_watchlist(args)
    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_markdown(data))


if __name__ == "__main__":
    main()
