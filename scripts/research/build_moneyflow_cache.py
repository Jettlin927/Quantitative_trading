from __future__ import annotations

import argparse
import json
import os
import sys
import time
from bisect import bisect_left
from datetime import date
from pathlib import Path
from typing import Any

import tushare as ts
from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import json_safe
from backend.app.database import SessionLocal
from backend.app.main import query_backtest_stocks
from backend.app.models import StockDailyBar
from backend.app.schemas import MarketBacktestRequest
from scripts.research.analyze_moneyflow_entry_edges import rank_moneyflow
from scripts.research.run_research_round import DEFAULT_CONTEXT_PATH, RUNS_ROOT, build_market_payload, build_strategy, now_text, read_json, write_json, write_text


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a run-local point-in-time Tushare moneyflow rank cache.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--context", default=str(DEFAULT_CONTEXT_PATH), help="Research context JSON path.")
    parser.add_argument("--strategy", default="trend-follow-maximum-profit-no-macd", help="Strategy preset from run_research_round.py.")
    parser.add_argument("--lookback-days", type=int, default=5, help="Previous trading dates to cache for each allowed entry date.")
    parser.add_argument("--sleep-seconds", type=float, default=0.12, help="Sleep between Tushare moneyflow calls.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing cache instead of resuming it.")
    args = parser.parse_args()

    if args.lookback_days <= 0:
        raise SystemExit("--lookback-days must be positive.")
    token = os.getenv("TUSHARE_TOKEN")
    if not token:
        raise SystemExit("TUSHARE_TOKEN is not configured in the api container.")

    started_at = now_text()
    context = read_json(Path(args.context))
    strategy = build_strategy(args.strategy, context)
    run_dir = RUNS_ROOT / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    cache_path = run_dir / "moneyflow-cache.jsonl"
    if args.force and cache_path.exists():
        cache_path.unlink()

    with SessionLocal() as db:
        payload = MarketBacktestRequest(**build_market_payload(context, strategy, max_stocks=None))
        candidate_codes = {stock.ts_code for stock in query_backtest_stocks(db, payload)}
        trade_dates = query_trade_dates(db, payload.start_date, payload.end_date)

    entry_dates = context_entry_dates(context, trade_dates)
    needed_dates = needed_prior_dates(trade_dates, entry_dates, args.lookback_days)
    cached_dates = existing_cache_dates(cache_path)

    pro = ts.pro_api(token)
    fetch_errors: list[dict[str, Any]] = []
    per_date: list[dict[str, Any]] = []
    mode = "a" if cached_dates else "w"
    with cache_path.open(mode, encoding="utf-8", newline="\n") as handle:
        for item in needed_dates:
            if item in cached_dates:
                per_date.append({"tradeDate": item.isoformat(), "status": "cached"})
                continue
            trade_date = item.strftime("%Y%m%d")
            try:
                df = pro.moneyflow(trade_date=trade_date)
                ranked = rank_moneyflow(df)
                saved = 0
                for ts_code, metrics in ranked.items():
                    if ts_code not in candidate_codes:
                        continue
                    record = {"tradeDate": item.isoformat(), "ts_code": ts_code, **metrics}
                    handle.write(json.dumps(json_safe(record), ensure_ascii=False, separators=(",", ":")) + "\n")
                    saved += 1
                handle.flush()
                per_date.append({"tradeDate": item.isoformat(), "status": "fetched", "rowsSaved": saved, "moneyflowRows": len(ranked)})
            except Exception as exc:
                error = {"tradeDate": item.isoformat(), "endpoint": "moneyflow", "error": type(exc).__name__, "message": str(exc)[:200]}
                fetch_errors.append(error)
                per_date.append({"tradeDate": item.isoformat(), "status": "error", "error": error})
            if args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)

    summary = {
        "runId": args.run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "context": str(Path(args.context)),
        "strategy": args.strategy,
        "cachePath": str(cache_path.relative_to(REPO_ROOT)),
        "lookbackDays": args.lookback_days,
        "candidateCount": len(candidate_codes),
        "entryDateCount": len(entry_dates),
        "neededPriorDateCount": len(needed_dates),
        "cachedDateCountBeforeRun": len(cached_dates),
        "fetchedDateCount": sum(1 for item in per_date if item.get("status") == "fetched"),
        "errorCount": len(fetch_errors),
        "fetchErrors": fetch_errors,
        "perDate": per_date,
    }
    write_json(run_dir / "cache-summary.json", json_safe(summary))
    write_text(run_dir / "review.md", render_review(summary))
    print(json.dumps({"runId": args.run_id, "cachePath": summary["cachePath"], "summary": compact_summary(summary)}, ensure_ascii=False, indent=2))


def query_trade_dates(db: Any, start_date: date, end_date: date) -> list[date]:
    stmt = (
        select(StockDailyBar.trade_date)
        .where(StockDailyBar.trade_date >= start_date, StockDailyBar.trade_date <= end_date)
        .distinct()
        .order_by(StockDailyBar.trade_date)
    )
    return [item for item in db.scalars(stmt)]


def context_entry_dates(context: dict[str, Any], trade_dates: list[date]) -> list[date]:
    raw_dates = context.get("strategy_overrides", {}).get("allowedEntryDates") or []
    if not raw_dates:
        return trade_dates
    available = set(trade_dates)
    return sorted(date.fromisoformat(item) for item in raw_dates if date.fromisoformat(item) in available)


def needed_prior_dates(trade_dates: list[date], entry_dates: list[date], lookback_days: int) -> list[date]:
    needed: set[date] = set()
    for entry_date in entry_dates:
        index = bisect_left(trade_dates, entry_date)
        for item in trade_dates[max(0, index - lookback_days) : index]:
            needed.add(item)
    return sorted(needed)


def existing_cache_dates(path: Path) -> set[date]:
    if not path.exists():
        return set()
    dates: set[date] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            trade_date = item.get("tradeDate")
            if trade_date:
                dates.add(date.fromisoformat(str(trade_date)))
    return dates


def compact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidateCount": summary["candidateCount"],
        "entryDateCount": summary["entryDateCount"],
        "neededPriorDateCount": summary["neededPriorDateCount"],
        "fetchedDateCount": summary["fetchedDateCount"],
        "errorCount": summary["errorCount"],
    }


def render_review(summary: dict[str, Any]) -> str:
    lines = [
        f"# {summary['runId']} moneyflow cache",
        "",
        f"- Context: `{summary['context']}`",
        f"- Cache: `{summary['cachePath']}`",
        f"- Candidate universe: `{summary['candidateCount']}` stocks.",
        f"- Entry dates: `{summary['entryDateCount']}`; prior dates needed: `{summary['neededPriorDateCount']}`.",
        f"- Fetched dates this run: `{summary['fetchedDateCount']}`; errors: `{summary['errorCount']}`.",
        "- Point-in-time rule: cache contains only trading dates before allowed entry dates.",
        "",
    ]
    if summary["fetchErrors"]:
        lines.extend(["## Fetch Errors", ""])
        for item in summary["fetchErrors"][:20]:
            lines.append(f"- `{item['tradeDate']}` {item['error']}: {item['message']}")
        if len(summary["fetchErrors"]) > 20:
            lines.append(f"- ... {len(summary['fetchErrors']) - 20} more")
        lines.append("")
    lines.extend(["## Use", "", f"`--moneyflow-cache {summary['cachePath']}`", ""])
    return "\n".join(lines)


if __name__ == "__main__":
    main()
