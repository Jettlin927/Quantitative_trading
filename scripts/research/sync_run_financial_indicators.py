from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.database import SessionLocal
from backend.app.main import (
    FINA_INDICATOR_FIELDS,
    compact_error,
    dedupe_rows,
    financial_indicator_record_to_row,
    upsert_financial_indicator_rows,
)
from backend.app.models import DataSyncRun, StockFinancialIndicator
from backend.app.tushare_client import get_pro_api, tushare_date

RUNS_ROOT = REPO_ROOT / "docs" / "research" / "runs"


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Tushare fina_indicator rows for symbols traded by a portfolio run.")
    parser.add_argument("--run-id", required=True, help="Portfolio run id under docs/research/runs.")
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--limit", type=int, default=0, help="Optional first-N symbol limit for smoke tests.")
    parser.add_argument("--sleep-seconds", type=float, default=0.12, help="Small delay between Tushare calls.")
    parser.add_argument("--force", action="store_true", help="Re-fetch symbols that already have financial rows in the date range.")
    args = parser.parse_args()

    if args.end_date < args.start_date:
        raise SystemExit("--end-date must be on or after --start-date.")
    if args.limit < 0:
        raise SystemExit("--limit must be >= 0.")
    if args.sleep_seconds < 0:
        raise SystemExit("--sleep-seconds must be >= 0.")

    symbols = load_completed_trade_symbols(args.run_id)
    if args.limit:
        symbols = symbols[: args.limit]
    if not symbols:
        raise SystemExit(f"No completed trade symbols found for run {args.run_id}.")

    pro = get_pro_api(None)
    rows_upserted = 0
    skipped_symbols = 0
    failed_symbols: list[dict[str, str]] = []
    synced_symbols: list[str] = []

    with SessionLocal() as db:
        for index, ts_code in enumerate(symbols, start=1):
            if not args.force and has_financial_rows(db, ts_code, args.start_date, args.end_date):
                skipped_symbols += 1
                continue
            try:
                df = pro.fina_indicator(
                    ts_code=ts_code,
                    start_date=tushare_date(args.start_date),
                    end_date=tushare_date(args.end_date),
                    fields=FINA_INDICATOR_FIELDS,
                )
                rows = dedupe_rows(
                    [row for item in df.to_dict("records") if (row := financial_indicator_record_to_row(item))],
                    ("ts_code", "end_date", "ann_date"),
                )
                rows_upserted += upsert_financial_indicator_rows(db, rows)
                db.commit()
                synced_symbols.append(ts_code)
            except Exception as error:
                db.rollback()
                failed_symbols.append({"ts_code": ts_code, "error": compact_error(error)})
            if args.sleep_seconds and index < len(symbols):
                time.sleep(args.sleep_seconds)

        status = "partial" if failed_symbols else "ok"
        if failed_symbols and not rows_upserted and not skipped_symbols:
            status = "failed"
        db.add(
            DataSyncRun(
                target=f"{args.run_id}:financials",
                start_date=args.start_date,
                end_date=args.end_date,
                rows_upserted=rows_upserted,
                status=status,
                message=f"symbols={len(symbols)}, synced_symbols={len(synced_symbols)}, skipped_symbols={skipped_symbols}, failed_symbols={len(failed_symbols)}",
            )
        )
        db.commit()

    print(
        json.dumps(
            {
                "runId": args.run_id,
                "status": status,
                "symbols": len(symbols),
                "syncedSymbols": len(synced_symbols),
                "skippedSymbols": skipped_symbols,
                "rowsUpserted": rows_upserted,
                "failedSymbols": failed_symbols,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def load_completed_trade_symbols(run_id: str) -> list[str]:
    path = RUNS_ROOT / run_id / "results.json"
    if not path.exists():
        raise SystemExit(f"Missing run results: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    trades = data.get("result", {}).get("completedTrades", [])
    symbols = sorted({trade.get("ts_code") for trade in trades if trade.get("ts_code")})
    return [str(symbol) for symbol in symbols]


def has_financial_rows(db: Any, ts_code: str, start_date: date, end_date: date) -> bool:
    count = db.scalar(
        select(func.count())
        .select_from(StockFinancialIndicator)
        .where(
            StockFinancialIndicator.ts_code == ts_code,
            StockFinancialIndicator.end_date >= start_date,
            StockFinancialIndicator.end_date <= end_date,
        )
    )
    return bool(count)


if __name__ == "__main__":
    main()
