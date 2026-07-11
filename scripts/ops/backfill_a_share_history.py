#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable


DEFAULT_START_DATE = date(2012, 1, 1)
DEFAULT_CORE_INDICES = ("000001.SH", "399001.SZ", "000300.SH", "000905.SH")
DATASETS = (
    "stock_listings",
    "trade_calendar",
    "daily",
    "daily_basic",
    "adjust_factors",
    "limit_prices",
    "suspend_events",
    "fund_adjust_factors",
    "index_daily",
)
MAX_RATE_PER_MINUTE = 500
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}, expected YYYY-MM-DD") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill the A-share research foundation from Tushare without deleting existing data."
    )
    parser.add_argument("--start-date", type=parse_date, default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", type=parse_date, default=date.today())
    parser.add_argument("--rate", type=int, default=120, help="Maximum Tushare requests per minute (1-500).")
    parser.add_argument("--resume", action="store_true", help="Skip completed checkpoints and sufficiently covered items.")
    parser.add_argument("--datasets", nargs="+", choices=DATASETS, default=list(DATASETS))
    parser.add_argument("--core-indices", default=",".join(DEFAULT_CORE_INDICES))
    parser.add_argument("--max-items", type=int, default=0, help="Limit date/index work items for a smoke run; 0 means all.")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-backoff", type=float, default=2.0)
    parser.add_argument("--progress-every", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    if args.start_date > args.end_date:
        raise ValueError("--start-date must be <= --end-date")
    if not 1 <= args.rate <= MAX_RATE_PER_MINUTE:
        raise ValueError(f"--rate must be between 1 and {MAX_RATE_PER_MINUTE}")
    if args.max_items < 0:
        raise ValueError("--max-items must be >= 0")
    if args.retries < 1:
        raise ValueError("--retries must be >= 1")
    if args.retry_backoff < 0:
        raise ValueError("--retry-backoff must be >= 0")
    if args.progress_every < 0:
        raise ValueError("--progress-every must be >= 0")


def emit(event: str, **values: Any) -> None:
    print(json.dumps({"event": event, "at": datetime.now().astimezone().isoformat(), **values}, ensure_ascii=False), flush=True)


class RateLimiter:
    def __init__(self, rate_per_minute: int) -> None:
        self.interval = 60.0 / rate_per_minute
        self.next_allowed_at = time.monotonic()

    def wait(self) -> None:
        now = time.monotonic()
        if now < self.next_allowed_at:
            time.sleep(self.next_allowed_at - now)
        self.next_allowed_at = time.monotonic() + self.interval


def call_with_retry(
    label: str,
    operation: Callable[[], Any],
    limiter: RateLimiter,
    retries: int,
    retry_backoff: float,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        limiter.wait()
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            emit("retry", label=label, attempt=attempt, retries=retries, error=str(exc))
            if attempt < retries:
                time.sleep(retry_backoff * (2 ** (attempt - 1)))
    assert last_error is not None
    raise last_error


def iter_year_ranges(start_date: date, end_date: date) -> list[tuple[date, date]]:
    ranges: list[tuple[date, date]] = []
    cursor = start_date
    while cursor <= end_date:
        range_end = min(date(cursor.year, 12, 31), end_date)
        ranges.append((cursor, range_end))
        cursor = range_end + timedelta(days=1)
    return ranges


def checkpoint_target(dataset: str) -> str:
    return f"backfill:{dataset}"


def has_checkpoint(dataset: str, item_start: date, item_end: date | None = None) -> bool:
    from sqlalchemy import select

    from backend.app.database import SessionLocal
    from backend.app.models import DataSyncRun

    with SessionLocal() as db:
        return bool(
            db.scalar(
                select(DataSyncRun.id).where(
                    DataSyncRun.target == checkpoint_target(dataset),
                    DataSyncRun.start_date == item_start,
                    DataSyncRun.end_date == (item_end or item_start),
                    DataSyncRun.status == "ok",
                ).limit(1)
            )
        )


def record_checkpoint(dataset: str, item_start: date, item_end: date, rows: int, status: str, message: str) -> None:
    from backend.app.database import SessionLocal
    from backend.app.main import record_sync_run

    with SessionLocal() as db:
        record_sync_run(
            db,
            target=checkpoint_target(dataset),
            start_date=item_start,
            end_date=item_end,
            rows_upserted=rows,
            status=status,
            message=message[:500],
        )


def active_listing_count(trade_day: date) -> int:
    from sqlalchemy import func, or_, select

    from backend.app.database import SessionLocal
    from backend.app.models import StockListing

    with SessionLocal() as db:
        return int(
            db.scalar(
                select(func.count(StockListing.ts_code)).where(
                    StockListing.list_date.is_not(None),
                    StockListing.list_date <= trade_day,
                    or_(StockListing.delist_date.is_(None), StockListing.delist_date >= trade_day),
                )
            )
            or 0
        )


def existing_count(model: type[Any], date_column: Any, trade_day: date) -> int:
    from sqlalchemy import func, select

    from backend.app.database import SessionLocal

    with SessionLocal() as db:
        return int(db.scalar(select(func.count()).select_from(model).where(date_column == trade_day)) or 0)


def load_stock_listing_codes() -> set[str]:
    from sqlalchemy import select

    from backend.app.database import SessionLocal
    from backend.app.models import Stock, StockListing

    with SessionLocal() as db:
        codes = set(db.scalars(select(StockListing.ts_code)).all())
        return codes or set(db.scalars(select(Stock.ts_code)).all())


def trade_calendar_range_complete(start_date: date, end_date: date) -> bool:
    from sqlalchemy import func, select

    from backend.app.database import SessionLocal
    from backend.app.models import TradeCalendar

    expected_days = (end_date - start_date).days + 1
    with SessionLocal() as db:
        existing_days = db.scalar(
            select(func.count(func.distinct(TradeCalendar.cal_date))).where(
                TradeCalendar.exchange == "SSE",
                TradeCalendar.cal_date >= start_date,
                TradeCalendar.cal_date <= end_date,
            )
        )
    return int(existing_days or 0) >= expected_days


def existing_index_coverage(ts_code: str, start_date: date, end_date: date) -> tuple[date | None, date | None, int]:
    from sqlalchemy import func, select

    from backend.app.database import SessionLocal
    from backend.app.models import IndexDailyBar

    with SessionLocal() as db:
        first, last, rows = db.execute(
            select(func.min(IndexDailyBar.trade_date), func.max(IndexDailyBar.trade_date), func.count()).where(
                IndexDailyBar.ts_code == ts_code,
                IndexDailyBar.trade_date >= start_date,
                IndexDailyBar.trade_date <= end_date,
            )
        ).one()
        return first, last, int(rows or 0)


def upsert(model: type[Any], rows: list[dict[str, Any]], conflicts: list[str]) -> int:
    from backend.app.database import SessionLocal
    from backend.app.main import upsert_rows

    with SessionLocal() as db:
        return upsert_rows(db, model, rows, conflicts)


def sync_stock_listings(pro: Any, args: argparse.Namespace, limiter: RateLimiter) -> tuple[int, list[str]]:
    from backend.app.main import STOCK_LISTING_FIELDS, dedupe_rows, stock_listing_record_to_row
    from backend.app.models import StockListing

    if args.resume and has_checkpoint("stock_listings", args.start_date, args.end_date):
        emit("skip", dataset="stock_listings", reason="checkpoint")
        return 0, []
    if args.dry_run:
        emit("plan", dataset="stock_listings", statuses=["L", "D", "P", "G"])
        return 0, []

    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for status in ("L", "D", "P", "G"):
        try:
            frame = call_with_retry(
                f"stock_listings:{status}",
                lambda status=status: pro.stock_basic(exchange="", list_status=status, fields=STOCK_LISTING_FIELDS),
                limiter,
                args.retries,
                args.retry_backoff,
            )
            rows.extend(row for item in frame.to_dict("records") if (row := stock_listing_record_to_row(item)))
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{status}:{exc}")
    clean_rows = dedupe_rows(rows, ("ts_code",))
    written = upsert(StockListing, clean_rows, ["ts_code"])
    status = "partial" if failures else "ok"
    record_checkpoint("stock_listings", args.start_date, args.end_date, written, status, f"statuses=4; failures={len(failures)}")
    emit("dataset_finish", dataset="stock_listings", status=status, rows=written, failures=failures)
    return written, failures


def sync_trade_calendar(pro: Any, args: argparse.Namespace, limiter: RateLimiter) -> tuple[int, list[str]]:
    from backend.app.main import TRADE_CALENDAR_FIELDS, dedupe_rows, trade_calendar_record_to_row
    from backend.app.models import TradeCalendar
    from backend.app.tushare_client import tushare_date

    written = 0
    failures: list[str] = []
    ranges = iter_year_ranges(args.start_date, args.end_date)
    if args.max_items:
        ranges = ranges[: args.max_items]
    for item_start, item_end in ranges:
        if args.resume:
            reason = "checkpoint" if has_checkpoint("trade_calendar", item_start, item_end) else None
            if reason is None and trade_calendar_range_complete(item_start, item_end):
                reason = "existing_coverage"
            if reason:
                emit("skip", dataset="trade_calendar", item=f"{item_start}:{item_end}", reason=reason)
                continue
        if args.dry_run:
            emit("plan", dataset="trade_calendar", item=f"{item_start}:{item_end}")
            continue
        try:
            frame = call_with_retry(
                f"trade_calendar:{item_start}:{item_end}",
                lambda: pro.trade_cal(
                    exchange="SSE",
                    start_date=tushare_date(item_start),
                    end_date=tushare_date(item_end),
                    fields=TRADE_CALENDAR_FIELDS,
                ),
                limiter,
                args.retries,
                args.retry_backoff,
            )
            rows = [row for item in frame.to_dict("records") if (row := trade_calendar_record_to_row(item, "SSE"))]
            if not rows:
                raise RuntimeError("Tushare returned no trade-calendar rows")
            item_written = upsert(TradeCalendar, dedupe_rows(rows, ("exchange", "cal_date")), ["exchange", "cal_date"])
            written += item_written
            record_checkpoint("trade_calendar", item_start, item_end, item_written, "ok", f"source_rows={len(rows)}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{item_start}:{item_end}:{exc}")
            record_checkpoint("trade_calendar", item_start, item_end, 0, "failed", str(exc))
    emit("dataset_finish", dataset="trade_calendar", status="partial" if failures else "ok", rows=written, failures=len(failures))
    return written, failures


def load_open_trade_dates(start_date: date, end_date: date, max_items: int) -> list[date]:
    from sqlalchemy import select

    from backend.app.database import SessionLocal
    from backend.app.models import TradeCalendar

    with SessionLocal() as db:
        days = list(
            db.scalars(
                select(TradeCalendar.cal_date)
                .where(
                    TradeCalendar.exchange == "SSE",
                    TradeCalendar.is_open.is_(True),
                    TradeCalendar.cal_date >= start_date,
                    TradeCalendar.cal_date <= end_date,
                )
                .order_by(TradeCalendar.cal_date)
            ).all()
        )
    return days[:max_items] if max_items else days


@dataclass(frozen=True)
class DailyDataset:
    name: str
    model: type[Any]
    date_column: Any
    conflicts: list[str]
    minimum_ratio: float
    allow_empty: bool
    fetch: Callable[[Any, date], Any]
    convert: Callable[[dict[str, Any]], dict[str, Any] | None]


@dataclass(frozen=True)
class DateCoverage:
    ts_code: str
    first: date
    last: date
    rows: int


def validate_source_rows(dataset: DailyDataset, rows: list[dict[str, Any]], expected: int) -> None:
    if not rows and not dataset.allow_empty and expected:
        raise RuntimeError(f"Tushare returned no rows for an open day with {expected} active listings")


def coverage_is_complete(required: DateCoverage, actual: DateCoverage | None) -> bool:
    return bool(
        actual
        and actual.ts_code == required.ts_code
        and actual.first <= required.first
        and actual.last >= required.last
        and actual.rows >= required.rows
    )


def build_daily_datasets() -> dict[str, DailyDataset]:
    from backend.app.main import (
        ADJUST_FACTOR_FIELDS,
        DAILY_BASIC_FIELDS,
        DAILY_FIELDS,
        STOCK_LIMIT_FIELDS,
        STOCK_SUSPEND_FIELDS,
        adjust_factor_record_to_row,
        daily_basic_record_to_row,
        daily_record_to_row,
        stock_limit_price_record_to_row,
        stock_suspend_event_record_to_row,
    )
    from backend.app.models import StockAdjustFactor, StockDailyBar, StockDailyBasic, StockLimitPrice, StockSuspendEvent
    from backend.app.tushare_client import tushare_date

    return {
        "daily": DailyDataset(
            "daily", StockDailyBar, StockDailyBar.trade_date, ["ts_code", "trade_date"], 0.75, False,
            lambda pro, day: pro.daily(trade_date=tushare_date(day), fields=DAILY_FIELDS), daily_record_to_row,
        ),
        "daily_basic": DailyDataset(
            "daily_basic", StockDailyBasic, StockDailyBasic.trade_date, ["ts_code", "trade_date"], 0.75, False,
            lambda pro, day: pro.query("daily_basic", ts_code="", trade_date=tushare_date(day), fields=DAILY_BASIC_FIELDS), daily_basic_record_to_row,
        ),
        "adjust_factors": DailyDataset(
            "adjust_factors", StockAdjustFactor, StockAdjustFactor.trade_date, ["ts_code", "trade_date"], 0.90, False,
            lambda pro, day: pro.adj_factor(ts_code="", trade_date=tushare_date(day), fields=ADJUST_FACTOR_FIELDS), adjust_factor_record_to_row,
        ),
        "limit_prices": DailyDataset(
            "limit_prices", StockLimitPrice, StockLimitPrice.trade_date, ["ts_code", "trade_date"], 0.90, False,
            lambda pro, day: pro.stk_limit(trade_date=tushare_date(day), fields=STOCK_LIMIT_FIELDS), stock_limit_price_record_to_row,
        ),
        "suspend_events": DailyDataset(
            "suspend_events", StockSuspendEvent, StockSuspendEvent.trade_date,
            ["ts_code", "trade_date", "suspend_type", "suspend_timing"], 0.0, True,
            lambda pro, day: pro.suspend_d(trade_date=tushare_date(day), fields=STOCK_SUSPEND_FIELDS), stock_suspend_event_record_to_row,
        ),
    }


def resume_item_is_complete(dataset: DailyDataset, trade_day: date, expected: int) -> bool:
    if has_checkpoint(dataset.name, trade_day):
        return True
    current = existing_count(dataset.model, dataset.date_column, trade_day)
    if dataset.allow_empty:
        return current > 0
    required = max(1, math.floor(expected * dataset.minimum_ratio))
    return current >= required


def sync_daily_dataset(
    dataset: DailyDataset,
    trade_dates: list[date],
    pro: Any,
    args: argparse.Namespace,
    limiter: RateLimiter,
) -> tuple[int, list[str]]:
    from backend.app.main import dedupe_rows

    written = 0
    skipped = 0
    failures: list[str] = []
    allowed_codes = load_stock_listing_codes() if dataset.name == "limit_prices" else set()
    for index, trade_day in enumerate(trade_dates, start=1):
        expected = active_listing_count(trade_day)
        if args.resume and resume_item_is_complete(dataset, trade_day, expected):
            skipped += 1
            continue
        if args.dry_run:
            emit("plan", dataset=dataset.name, item=trade_day.isoformat(), active_listings=expected)
            continue
        try:
            frame = call_with_retry(
                f"{dataset.name}:{trade_day}",
                lambda: dataset.fetch(pro, trade_day),
                limiter,
                args.retries,
                args.retry_backoff,
            )
            rows = [
                row
                for item in frame.to_dict("records")
                if (row := dataset.convert(item)) and (not allowed_codes or row["ts_code"] in allowed_codes)
            ]
            validate_source_rows(dataset, rows, expected)
            clean_rows = dedupe_rows(rows, tuple(dataset.conflicts))
            item_written = upsert(dataset.model, clean_rows, dataset.conflicts)
            written += item_written
            empty_note = "; empty_result_checkpoint=true" if not rows else ""
            record_checkpoint(dataset.name, trade_day, trade_day, item_written, "ok", f"source_rows={len(rows)}{empty_note}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{trade_day}:{exc}")
            record_checkpoint(dataset.name, trade_day, trade_day, 0, "failed", str(exc))
        if args.progress_every and (index == 1 or index % args.progress_every == 0 or index == len(trade_dates)):
            emit(
                "progress",
                dataset=dataset.name,
                processed=index,
                total=len(trade_dates),
                item=trade_day.isoformat(),
                rows=written,
                skipped=skipped,
                failures=len(failures),
            )
    emit("dataset_finish", dataset=dataset.name, status="partial" if failures else "ok", rows=written, skipped=skipped, failures=len(failures))
    return written, failures


def sync_index_daily(pro: Any, args: argparse.Namespace, limiter: RateLimiter) -> tuple[int, list[str]]:
    from backend.app.main import INDEX_DAILY_FIELDS, dedupe_rows, index_daily_record_to_row
    from backend.app.models import IndexDailyBar
    from backend.app.tushare_client import tushare_date

    indices = [value.strip().upper() for value in args.core_indices.split(",") if value.strip()]
    if args.max_items:
        indices = indices[: args.max_items]
    written = 0
    failures: list[str] = []
    for ts_code in indices:
        if args.resume and has_checkpoint(f"index_daily:{ts_code}", args.start_date, args.end_date):
            emit("skip", dataset="index_daily", item=ts_code, reason="checkpoint")
            continue
        if args.dry_run:
            emit("plan", dataset="index_daily", item=ts_code, start=str(args.start_date), end=str(args.end_date))
            continue
        first, last, rows_present = existing_index_coverage(ts_code, args.start_date, args.end_date)
        if args.resume and rows_present and first and last and first <= args.start_date + timedelta(days=7) and last >= args.end_date - timedelta(days=7):
            emit("skip", dataset="index_daily", item=ts_code, reason="existing_coverage", first=str(first), last=str(last))
            continue
        try:
            frame = call_with_retry(
                f"index_daily:{ts_code}",
                lambda ts_code=ts_code: pro.index_daily(
                    ts_code=ts_code,
                    start_date=tushare_date(args.start_date),
                    end_date=tushare_date(args.end_date),
                    fields=INDEX_DAILY_FIELDS,
                ),
                limiter,
                args.retries,
                args.retry_backoff,
            )
            source_rows = [row for item in frame.to_dict("records") if (row := index_daily_record_to_row(item))]
            if not source_rows:
                raise RuntimeError("Tushare returned no index rows")
            item_written = upsert(IndexDailyBar, dedupe_rows(source_rows, ("ts_code", "trade_date")), ["ts_code", "trade_date"])
            written += item_written
            record_checkpoint(f"index_daily:{ts_code}", args.start_date, args.end_date, item_written, "ok", f"source_rows={len(source_rows)}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{ts_code}:{exc}")
            record_checkpoint(f"index_daily:{ts_code}", args.start_date, args.end_date, 0, "failed", str(exc))
    emit("dataset_finish", dataset="index_daily", status="partial" if failures else "ok", rows=written, failures=failures)
    return written, failures


def load_fund_coverages(
    model: type[Any],
    start_date: date,
    end_date: date,
    ts_code: str | None = None,
) -> dict[str, DateCoverage]:
    from sqlalchemy import func, select

    from backend.app.database import SessionLocal

    statement = (
        select(
            model.ts_code,
            func.min(model.trade_date),
            func.max(model.trade_date),
            func.count(),
        )
        .where(model.trade_date >= start_date, model.trade_date <= end_date)
        .group_by(model.ts_code)
        .order_by(model.ts_code)
    )
    if ts_code:
        statement = statement.where(model.ts_code == ts_code)
    with SessionLocal() as db:
        rows = db.execute(statement).all()
    return {
        code: DateCoverage(ts_code=code, first=first, last=last, rows=int(count))
        for code, first, last, count in rows
        if first is not None and last is not None
    }


def is_research_etf_name(name: str | None) -> bool:
    normalized = (name or "").upper()
    return "ETF" in normalized and "联接" not in normalized


def load_research_etf_codes() -> set[str]:
    from sqlalchemy import select

    from backend.app.database import SessionLocal
    from backend.app.models import Fund

    with SessionLocal() as db:
        rows = db.execute(select(Fund.ts_code, Fund.name)).all()
    return {ts_code for ts_code, name in rows if is_research_etf_name(name)}


def fetch_fund_adjust_factors(
    pro: Any,
    ts_code: str,
    start_date: date,
    end_date: date,
    args: argparse.Namespace,
    limiter: RateLimiter,
) -> list[dict[str, Any]]:
    from backend.app.main import ADJUST_FACTOR_FIELDS, fund_adjust_factor_record_to_row
    from backend.app.tushare_client import tushare_date

    page_size = 2000
    offset = 0
    rows: list[dict[str, Any]] = []
    while True:
        frame = call_with_retry(
            f"fund_adjust_factors:{ts_code}:offset={offset}",
            lambda offset=offset: pro.fund_adj(
                ts_code=ts_code,
                start_date=tushare_date(start_date),
                end_date=tushare_date(end_date),
                offset=offset,
                limit=page_size,
                fields=ADJUST_FACTOR_FIELDS,
            ),
            limiter,
            args.retries,
            args.retry_backoff,
        )
        records = frame.to_dict("records")
        page = [row for item in records if (row := fund_adjust_factor_record_to_row(item))]
        rows.extend(page)
        if len(records) < page_size:
            return rows
        offset += page_size


def sync_fund_adjust_factors(pro: Any, args: argparse.Namespace, limiter: RateLimiter) -> tuple[int, list[str]]:
    from backend.app.main import dedupe_rows
    from backend.app.models import FundAdjustFactor, FundDailyBar

    eligible_codes = load_research_etf_codes()
    daily_coverages = {
        ts_code: coverage
        for ts_code, coverage in load_fund_coverages(FundDailyBar, args.start_date, args.end_date).items()
        if ts_code in eligible_codes
    }
    adjust_coverages = load_fund_coverages(FundAdjustFactor, args.start_date, args.end_date) if args.resume else {}
    funds = sorted(daily_coverages)
    if args.max_items:
        funds = funds[: args.max_items]

    written = 0
    skipped = 0
    failures: list[str] = []
    for index, ts_code in enumerate(funds, start=1):
        required = daily_coverages[ts_code]
        checkpoint_dataset = f"fund_adjust_factors:{ts_code}"
        if args.resume and coverage_is_complete(required, adjust_coverages.get(ts_code)):
            skipped += 1
            continue
        if args.dry_run:
            emit(
                "plan",
                dataset="fund_adjust_factors",
                item=ts_code,
                start=str(required.first),
                end=str(required.last),
                daily_rows=required.rows,
            )
            continue
        item_written = 0
        try:
            source_rows = fetch_fund_adjust_factors(pro, ts_code, required.first, required.last, args, limiter)
            if not source_rows:
                raise RuntimeError("Tushare returned no fund adjustment factors")
            item_written = upsert(
                FundAdjustFactor,
                dedupe_rows(source_rows, ("ts_code", "trade_date")),
                ["ts_code", "trade_date"],
            )
            written += item_written
            actual = load_fund_coverages(FundAdjustFactor, required.first, required.last, ts_code).get(ts_code)
            if not coverage_is_complete(required, actual):
                actual_summary = "none" if actual is None else f"{actual.first}:{actual.last}:{actual.rows}"
                raise RuntimeError(
                    f"fund adjustment coverage incomplete; daily={required.first}:{required.last}:{required.rows}; "
                    f"adjust={actual_summary}"
                )
            record_checkpoint(
                checkpoint_dataset,
                args.start_date,
                args.end_date,
                item_written,
                "ok",
                f"daily_rows={required.rows}; source_rows={len(source_rows)}",
            )
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{ts_code}:{exc}")
            record_checkpoint(checkpoint_dataset, args.start_date, args.end_date, item_written, "failed", str(exc))
        if args.progress_every and (index == 1 or index % args.progress_every == 0 or index == len(funds)):
            emit(
                "progress",
                dataset="fund_adjust_factors",
                processed=index,
                total=len(funds),
                item=ts_code,
                rows=written,
                skipped=skipped,
                failures=len(failures),
            )
    emit(
        "dataset_finish",
        dataset="fund_adjust_factors",
        status="partial" if failures else "ok",
        rows=written,
        skipped=skipped,
        failures=len(failures),
    )
    return written, failures


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        validate_args(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    from backend.app.tushare_client import get_pro_api

    limiter = RateLimiter(args.rate)
    pro = None if args.dry_run else get_pro_api()
    selected = list(dict.fromkeys(args.datasets))
    emit(
        "start",
        start_date=str(args.start_date),
        end_date=str(args.end_date),
        datasets=selected,
        rate=args.rate,
        resume=args.resume,
        dry_run=args.dry_run,
    )
    total_rows = 0
    all_failures: list[str] = []

    if "stock_listings" in selected:
        rows, failures = sync_stock_listings(pro, args, limiter)
        total_rows += rows
        all_failures.extend(f"stock_listings:{item}" for item in failures)
    if "trade_calendar" in selected:
        rows, failures = sync_trade_calendar(pro, args, limiter)
        total_rows += rows
        all_failures.extend(f"trade_calendar:{item}" for item in failures)

    daily_datasets = build_daily_datasets()
    requested_daily = [name for name in selected if name in daily_datasets]
    if requested_daily:
        trade_dates = load_open_trade_dates(args.start_date, args.end_date, args.max_items)
        if not trade_dates:
            all_failures.append("trade_calendar:no open trade dates found; include trade_calendar in this run")
            emit("error", dataset="trade_calendar", error=all_failures[-1])
        else:
            for name in requested_daily:
                rows, failures = sync_daily_dataset(daily_datasets[name], trade_dates, pro, args, limiter)
                total_rows += rows
                all_failures.extend(f"{name}:{item}" for item in failures)

    if "index_daily" in selected:
        rows, failures = sync_index_daily(pro, args, limiter)
        total_rows += rows
        all_failures.extend(f"index_daily:{item}" for item in failures)

    if "fund_adjust_factors" in selected:
        rows, failures = sync_fund_adjust_factors(pro, args, limiter)
        total_rows += rows
        all_failures.extend(f"fund_adjust_factors:{item}" for item in failures)

    status = "partial" if all_failures else "ok"
    emit("finish", status=status, rows=total_rows, failures=len(all_failures), failure_samples=all_failures[:20])
    return 1 if all_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
