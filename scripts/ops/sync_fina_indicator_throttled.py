#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any


MAX_RATE_PER_MINUTE = 150
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Throttle Tushare fina_indicator sync by stock.")
    parser.add_argument("--start-date", required=True, type=parse_date)
    parser.add_argument("--end-date", required=True, type=parse_date)
    parser.add_argument("--rate-per-minute", type=int, default=MAX_RATE_PER_MINUTE)
    parser.add_argument("--max-stocks", type=int, default=0)
    parser.add_argument("--start-after", default="")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--progress-every", type=int, default=100)
    return parser.parse_args()


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}, expected YYYY-MM-DD") from exc


def load_stock_codes(max_stocks: int, start_after: str) -> list[str]:
    from sqlalchemy import select

    from backend.app.database import SessionLocal
    from backend.app.models import Stock

    with SessionLocal() as db:
        stmt = select(Stock.ts_code).order_by(Stock.ts_code)
        if start_after:
            stmt = stmt.where(Stock.ts_code > start_after)
        if max_stocks:
            stmt = stmt.limit(max_stocks)
        return list(db.scalars(stmt).all())


def has_existing_rows(ts_code: str, start_date: date, end_date: date) -> bool:
    from sqlalchemy import func, select

    from backend.app.database import SessionLocal
    from backend.app.models import StockFinancialIndicator

    with SessionLocal() as db:
        existing = db.scalar(
            select(func.count(StockFinancialIndicator.id)).where(
                StockFinancialIndicator.ts_code == ts_code,
                StockFinancialIndicator.ann_date >= start_date,
                StockFinancialIndicator.ann_date <= end_date,
                StockFinancialIndicator.revision_status == "observed",
            )
        )
        return bool(existing)


def sync_one_stock(pro: Any, ts_code: str, start_date: date, end_date: date) -> int:
    from backend.app.database import SessionLocal
    from backend.app.main import (
        FINA_INDICATOR_FIELDS,
        financial_indicator_record_to_row,
        insert_financial_revision_rows,
        next_financial_available_date,
        utc_now,
    )
    from backend.app.tushare_client import tushare_date

    df = pro.fina_indicator(
        ts_code=ts_code,
        start_date=tushare_date(start_date),
        end_date=tushare_date(end_date),
        fields=FINA_INDICATOR_FIELDS,
    )
    observed_at = utc_now()
    with SessionLocal() as db:
        available_from = next_financial_available_date(db, observed_at)
        rows = [
            row
            for item in df.to_dict("records")
            if (
                row := financial_indicator_record_to_row(
                    item,
                    source_observed_at=observed_at,
                    available_from=available_from,
                )
            )
        ]
        return insert_financial_revision_rows(db, rows)


def print_progress(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def record_sync_result(
    start_date: date,
    end_date: date,
    rows_upserted: int,
    status: str,
    message: str,
) -> None:
    from backend.app.database import SessionLocal
    from backend.app.main import record_sync_run

    with SessionLocal() as db:
        record_sync_run(
            db,
            target="market:fundamentals",
            start_date=start_date,
            end_date=end_date,
            rows_upserted=rows_upserted,
            status=status,
            message=message,
        )


def main() -> int:
    args = parse_args()
    if args.rate_per_minute <= 0 or args.rate_per_minute > MAX_RATE_PER_MINUTE:
        print(f"--rate-per-minute must be between 1 and {MAX_RATE_PER_MINUTE}", file=sys.stderr)
        return 2
    if args.max_stocks < 0:
        print("--max-stocks must be >= 0", file=sys.stderr)
        return 2
    if args.start_date > args.end_date:
        print("--start-date must be <= --end-date", file=sys.stderr)
        return 2

    stock_codes = load_stock_codes(args.max_stocks, args.start_after)
    interval_seconds = 60.0 / args.rate_per_minute
    from backend.app.tushare_client import get_pro_api

    pro = get_pro_api()

    started_at = datetime.now().astimezone()
    rows_upserted = 0
    skipped_stocks = 0
    failed_stocks: list[str] = []
    next_allowed_at = time.monotonic()

    print_progress(
        {
            "event": "start",
            "started_at": started_at.isoformat(),
            "start_date": args.start_date.isoformat(),
            "end_date": args.end_date.isoformat(),
            "stocks": len(stock_codes),
            "rate_per_minute": args.rate_per_minute,
            "interval_seconds": interval_seconds,
            "start_after": args.start_after or None,
            "skip_existing": args.skip_existing,
        }
    )

    for index, ts_code in enumerate(stock_codes, start=1):
        now = time.monotonic()
        if now < next_allowed_at:
            time.sleep(next_allowed_at - now)
        request_started_at = time.monotonic()
        next_allowed_at = request_started_at + interval_seconds

        try:
            if args.skip_existing and has_existing_rows(ts_code, args.start_date, args.end_date):
                skipped_stocks += 1
                upserted = 0
            else:
                upserted = sync_one_stock(pro, ts_code, args.start_date, args.end_date)
                rows_upserted += upserted
        except Exception as exc:  # noqa: BLE001
            failed_stocks.append(f"{ts_code}:{exc}")
            upserted = 0

        if args.progress_every and (index == 1 or index % args.progress_every == 0 or index == len(stock_codes)):
            print_progress(
                {
                    "event": "progress",
                    "processed": index,
                    "stocks": len(stock_codes),
                    "ts_code": ts_code,
                    "rows_upserted": rows_upserted,
                    "last_rows_upserted": upserted,
                    "skipped_stocks": skipped_stocks,
                    "failed_count": len(failed_stocks),
                }
            )

    finished_at = datetime.now().astimezone()
    status = "partial" if failed_stocks else "ok"
    message = (
        f"throttled_fina_indicator stocks={len(stock_codes)}, skipped_stocks={skipped_stocks}, "
        f"failed_stocks={len(failed_stocks)}, rate_per_minute={args.rate_per_minute}"
    )
    record_sync_result(args.start_date, args.end_date, rows_upserted, status, message)
    print_progress(
        {
            "event": "finish",
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "elapsed_seconds": round((finished_at - started_at).total_seconds(), 3),
            "status": status,
            "rows_upserted": rows_upserted,
            "stocks": len(stock_codes),
            "skipped_stocks": skipped_stocks,
            "failed_count": len(failed_stocks),
            "failed_stocks": failed_stocks,
        }
    )
    return 1 if failed_stocks else 0


if __name__ == "__main__":
    raise SystemExit(main())
