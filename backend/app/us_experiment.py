from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import math
from typing import Any, Protocol

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (
    DataOverviewSnapshot,
    DataSyncJob,
    DataSyncRun,
    UsExperimentDailyBar,
    UsExperimentDailyCheck,
    UsExperimentInstrument,
)
from .schemas import SyncUsExperimentPricesRequest


TARGET_START_DATE = date(2010, 1, 1)
MARKET_NAMES = {"105": "NASDAQ", "106": "NYSE", "107": "US_OTHER"}
PRICE_RELATIVE_TOLERANCE = Decimal("0.005")
VOLUME_RELATIVE_TOLERANCE = Decimal("0.05")
OVERVIEW_SNAPSHOT_KEY = "us_experiment"
VALIDATION_STATUSES = {"match", "mismatch", "source_missing", "error"}


class UniverseProvider(Protocol):
    def fetch_universe(self) -> list[dict[str, Any]]: ...


class DailyPriceProvider(Protocol):
    def fetch(self, symbols: list[str], start_date: date, end_date: date) -> dict[str, list[dict[str, Any]]]: ...


class ValidationProvider(Protocol):
    def fetch_history(self, source_code: str, start_date: date, end_date: date) -> list[dict[str, Any]]: ...


class AkshareProvider:
    def fetch_universe(self) -> list[dict[str, Any]]:
        import akshare as ak

        frame = ak.stock_us_spot_em()
        return frame.to_dict("records")

    def fetch_history(self, source_code: str, start_date: date, end_date: date) -> list[dict[str, Any]]:
        import akshare as ak

        frame = ak.stock_us_hist(
            symbol=source_code,
            period="daily",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust="",
        )
        return [normalize_akshare_history_record(item) for item in frame.to_dict("records")]


class YFinanceProvider:
    def fetch(self, symbols: list[str], start_date: date, end_date: date) -> dict[str, list[dict[str, Any]]]:
        import yfinance as yf

        if not symbols:
            return {}
        frame = yf.download(
            tickers=symbols,
            start=start_date.isoformat(),
            end=(end_date + timedelta(days=1)).isoformat(),
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            actions=True,
            # 免费源请求必须串行，批次级限速和退避由回填编排器负责。
            threads=False,
            progress=False,
            repair=False,
            keepna=False,
            multi_level_index=True,
            timeout=30,
        )
        return yfinance_frame_to_rows(frame, symbols)


def yahoo_symbol_for(symbol: str) -> str:
    return symbol.strip().upper().replace(".", "-")


def normalize_universe_records(records: list[dict[str, Any]], observed_at: datetime) -> list[dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for item in records:
        raw_code = str(item.get("代码") or item.get("code") or "").strip().upper()
        market_code, separator, symbol = raw_code.partition(".")
        if separator != "." or market_code not in MARKET_NAMES or not symbol:
            continue
        source_code = f"{market_code}.{symbol}"
        normalized[source_code] = {
            "source_code": source_code,
            "symbol": symbol,
            "yahoo_symbol": yahoo_symbol_for(symbol),
            "name": clean_text(item.get("名称") or item.get("name"), 200),
            "market_code": market_code,
            "market_name": MARKET_NAMES[market_code],
            "is_current": True,
            "last_seen_at": observed_at,
            "updated_at": observed_at,
        }
    return [normalized[key] for key in sorted(normalized)]


def refresh_universe(
    db: Session,
    *,
    provider: UniverseProvider | None = None,
    observed_at: datetime | None = None,
    minimum_rows: int = 1000,
) -> dict[str, Any]:
    fetched_at = observed_at or datetime.now(timezone.utc)
    rows = normalize_universe_records((provider or AkshareProvider()).fetch_universe(), fetched_at)
    if len(rows) < minimum_rows:
        raise RuntimeError(f"AKShare 美股目录仅返回 {len(rows)} 条，低于安全阈值 {minimum_rows}，拒绝覆盖当前目录")

    db.execute(update(UsExperimentInstrument).values(is_current=False, updated_at=fetched_at))
    _upsert_universe_rows(db, rows, fetched_at)
    db.flush()
    by_market = dict(
        db.execute(
            select(UsExperimentInstrument.market_code, func.count())
            .where(UsExperimentInstrument.is_current.is_(True))
            .group_by(UsExperimentInstrument.market_code)
        ).all()
    )
    db.add(
        DataSyncRun(
            source="akshare",
            target="us_experiment_universe",
            rows_upserted=len(rows),
            status="ok",
            message=f"current={len(rows)}; markets={by_market}",
        )
    )
    db.commit()
    return {
        "status": "ok",
        "rows_upserted": len(rows),
        "current_instruments": len(rows),
        "by_market": {key: int(value) for key, value in sorted(by_market.items())},
        "observed_at": fetched_at.isoformat(),
    }


def sync_daily_prices(
    db: Session,
    request: SyncUsExperimentPricesRequest,
    *,
    price_provider: DailyPriceProvider | None = None,
    validation_provider: ValidationProvider | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    fetched_at = observed_at or datetime.now(timezone.utc)
    instruments = list(
        db.scalars(
            select(UsExperimentInstrument)
            .where(UsExperimentInstrument.source_code.in_(request.source_codes))
            .order_by(UsExperimentInstrument.source_code)
        )
    )
    instrument_by_code = {item.source_code: item for item in instruments}
    unknown_codes = [code for code in request.source_codes if code not in instrument_by_code]
    yahoo_symbols = sorted({item.yahoo_symbol for item in instruments})
    provider = price_provider or YFinanceProvider()
    fetch_errors: dict[str, str] = {}
    try:
        fetched = provider.fetch(yahoo_symbols, request.start_date, request.end_date)
    except Exception as exc:  # noqa: BLE001
        fetched = {}
        if len(yahoo_symbols) == 1:
            fetch_errors[yahoo_symbols[0]] = f"{type(exc).__name__}: {exc}"[:500]
        else:
            for yahoo_symbol in yahoo_symbols:
                try:
                    fetched.update(provider.fetch([yahoo_symbol], request.start_date, request.end_date))
                except Exception as symbol_exc:  # noqa: BLE001
                    fetch_errors[yahoo_symbol] = f"{type(symbol_exc).__name__}: {symbol_exc}"[:500]

    bar_rows: list[dict[str, Any]] = []
    rows_by_source: dict[str, list[dict[str, Any]]] = {}
    failures: list[dict[str, str]] = [
        {"sourceCode": code, "error": "目录中不存在该代码"} for code in unknown_codes
    ]
    successful_codes: list[str] = []
    for instrument in instruments:
        normalized_rows = normalize_daily_rows(fetched.get(instrument.yahoo_symbol, []))
        rows_by_source[instrument.source_code] = normalized_rows
        instrument.last_sync_at = fetched_at
        if not normalized_rows:
            instrument.last_sync_status = "failed"
            instrument.last_sync_error = fetch_errors.get(
                instrument.yahoo_symbol,
                "yfinance 在请求区间内未返回可用日线",
            )
            failures.append({"sourceCode": instrument.source_code, "error": instrument.last_sync_error})
            continue
        successful_codes.append(instrument.source_code)
        instrument.last_sync_status = "ok"
        instrument.last_sync_error = None
        first_date = normalized_rows[0]["trade_date"]
        last_date = normalized_rows[-1]["trade_date"]
        instrument.history_start_date = min(filter(None, [instrument.history_start_date, first_date]))
        instrument.history_end_date = max(filter(None, [instrument.history_end_date, last_date]))
        for row in normalized_rows:
            bar_rows.append(
                {
                    "source_code": instrument.source_code,
                    **row,
                    "source": "yfinance",
                    "fetched_at": fetched_at,
                    "updated_at": fetched_at,
                }
            )

    _bulk_upsert(
        db,
        UsExperimentDailyBar,
        bar_rows,
        ["source_code", "trade_date"],
        exclude_updates={"id", "created_at"},
    )

    requested_checks = set(request.validation_source_codes)
    requested_checks.update(item["sourceCode"] for item in failures if item["sourceCode"] in instrument_by_code)
    check_rows: list[dict[str, Any]] = []
    validation_errors: list[dict[str, str]] = []
    checker = validation_provider or AkshareProvider()
    for source_code in sorted(requested_checks):
        try:
            akshare_rows = checker.fetch_history(source_code, request.start_date, request.end_date)
            check_rows.append(
                build_validation_row(
                    source_code,
                    rows_by_source.get(source_code, []),
                    normalize_daily_rows(akshare_rows),
                    request.end_date,
                    fetched_at,
                )
            )
        except Exception as exc:  # noqa: BLE001
            message = f"{type(exc).__name__}: {exc}"[:500]
            validation_errors.append({"sourceCode": source_code, "error": message})
            check_rows.append(
                {
                    "source_code": source_code,
                    "trade_date": request.end_date,
                    "status": "error",
                    "message": message,
                    "checked_at": fetched_at,
                    "updated_at": fetched_at,
                }
            )
    _bulk_upsert(
        db,
        UsExperimentDailyCheck,
        check_rows,
        ["source_code", "trade_date"],
        exclude_updates={"id", "created_at"},
    )

    validation_alerts = [
        row for row in check_rows if row["status"] in {"mismatch", "source_missing", "error"}
    ]
    status = "partial" if failures or validation_alerts else "ok"
    db.add(
        DataSyncRun(
            source="yfinance+akshare",
            target="us_experiment_daily",
            start_date=request.start_date,
            end_date=request.end_date,
            rows_upserted=len(bar_rows) + len(check_rows),
            status=status,
            message=(
                f"symbols={len(instruments)}; successful={len(successful_codes)}; "
                f"failed={len(failures)}; checks={len(check_rows)}; alerts={len(validation_alerts)}"
            ),
        )
    )
    db.commit()
    return {
        "status": status,
        "rows_upserted": len(bar_rows) + len(check_rows),
        "daily_bars_upserted": len(bar_rows),
        "checks_upserted": len(check_rows),
        "successfulSourceCodes": successful_codes,
        "failed": failures,
        "validationErrors": validation_errors,
        "validationAlerts": [
            {"sourceCode": row["source_code"], "status": row["status"], "message": row.get("message")}
            for row in validation_alerts
        ],
        "start_date": request.start_date.isoformat(),
        "end_date": request.end_date.isoformat(),
    }


def refresh_overview_snapshot(db: Session) -> DataOverviewSnapshot:
    """显式刷新重型覆盖聚合；普通页面读取只消费已持久化快照。"""
    current_instruments = int(
        db.scalar(select(func.count()).select_from(UsExperimentInstrument).where(UsExperimentInstrument.is_current.is_(True)))
        or 0
    )
    bar_stats = db.execute(
        select(
            func.count(UsExperimentDailyBar.id),
            func.count(func.distinct(UsExperimentDailyBar.source_code)),
            func.min(UsExperimentDailyBar.trade_date),
            func.max(UsExperimentDailyBar.trade_date),
        )
    ).one()
    current_priced = int(
        db.scalar(
            select(func.count(func.distinct(UsExperimentDailyBar.source_code)))
            .join(UsExperimentInstrument, UsExperimentInstrument.source_code == UsExperimentDailyBar.source_code)
            .where(UsExperimentInstrument.is_current.is_(True))
        )
        or 0
    )
    sync_statuses = dict(
        db.execute(
            select(UsExperimentInstrument.last_sync_status, func.count())
            .where(UsExperimentInstrument.is_current.is_(True))
            .group_by(UsExperimentInstrument.last_sync_status)
        ).all()
    )
    check_stats = db.execute(
        select(
            func.count(UsExperimentDailyCheck.id),
            func.min(UsExperimentDailyCheck.trade_date),
            func.max(UsExperimentDailyCheck.trade_date),
            func.max(UsExperimentDailyCheck.checked_at),
        )
    ).one()
    check_statuses = dict(
        db.execute(select(UsExperimentDailyCheck.status, func.count()).group_by(UsExperimentDailyCheck.status)).all()
    )
    payload = {
        "coverage": {
            "instrumentsWithBars": int(bar_stats[1] or 0),
            "currentInstrumentsWithBars": current_priced,
            "currentPercent": round(current_priced / current_instruments * 100, 2) if current_instruments else 0.0,
            "dailyBars": int(bar_stats[0] or 0),
            "startDate": bar_stats[2].isoformat() if bar_stats[2] else None,
            "endDate": bar_stats[3].isoformat() if bar_stats[3] else None,
            "syncStatuses": {str(key or "never"): int(value) for key, value in sync_statuses.items()},
        },
        "validation": {
            "checks": int(check_stats[0] or 0),
            "byStatus": {str(key): int(value) for key, value in check_statuses.items()},
            "startDate": check_stats[1].isoformat() if check_stats[1] else None,
            "endDate": check_stats[2].isoformat() if check_stats[2] else None,
            "lastCheckedAt": check_stats[3].isoformat() if check_stats[3] else None,
        },
    }
    snapshot = db.get(DataOverviewSnapshot, OVERVIEW_SNAPSHOT_KEY)
    snapshot = snapshot or DataOverviewSnapshot(key=OVERVIEW_SNAPSHOT_KEY, payload=payload)
    snapshot.payload = payload
    db.add(snapshot)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        snapshot = db.get(DataOverviewSnapshot, OVERVIEW_SNAPSHOT_KEY)
        if snapshot is None:
            raise
        snapshot.payload = payload
        db.commit()
    db.refresh(snapshot)
    return snapshot


def build_overview(db: Session) -> dict[str, Any]:
    current_instruments = int(
        db.scalar(select(func.count()).select_from(UsExperimentInstrument).where(UsExperimentInstrument.is_current.is_(True)))
        or 0
    )
    total_instruments = int(db.scalar(select(func.count()).select_from(UsExperimentInstrument)) or 0)
    by_market = dict(
        db.execute(
            select(UsExperimentInstrument.market_code, func.count())
            .where(UsExperimentInstrument.is_current.is_(True))
            .group_by(UsExperimentInstrument.market_code)
        ).all()
    )
    snapshot = db.get(DataOverviewSnapshot, OVERVIEW_SNAPSHOT_KEY)
    snapshot_payload = dict(snapshot.payload) if snapshot else {}
    coverage = dict(snapshot_payload.get("coverage") or {})
    validation = dict(snapshot_payload.get("validation") or {})
    coverage.setdefault("instrumentsWithBars", 0)
    coverage.setdefault("currentInstrumentsWithBars", 0)
    coverage.setdefault("currentPercent", 0.0)
    coverage.setdefault("dailyBars", 0)
    coverage.setdefault("startDate", None)
    coverage.setdefault("endDate", None)
    coverage.setdefault("syncStatuses", {})
    validation.setdefault("checks", 0)
    validation.setdefault("byStatus", {})
    validation.setdefault("startDate", None)
    validation.setdefault("endDate", None)
    validation.setdefault("lastCheckedAt", None)
    validation.update({
        "priceTolerancePct": float(PRICE_RELATIVE_TOLERANCE * 100),
        "volumeTolerancePct": float(VOLUME_RELATIVE_TOLERANCE * 100),
    })
    jobs = list(
        db.scalars(
            select(DataSyncJob)
            .where(DataSyncJob.action.in_(("us_experiment_universe", "us_experiment_prices")))
            .order_by(DataSyncJob.created_at.desc())
            .limit(8)
        )
    )
    failed_instruments = list(
        db.scalars(
            select(UsExperimentInstrument)
            .where(UsExperimentInstrument.is_current.is_(True), UsExperimentInstrument.last_sync_status == "failed")
            .order_by(UsExperimentInstrument.last_sync_at.desc(), UsExperimentInstrument.source_code)
            .limit(20)
        )
    )
    validation_alerts = list(
        db.scalars(
            select(UsExperimentDailyCheck)
            .where(UsExperimentDailyCheck.status.in_(("mismatch", "source_missing", "error")))
            .order_by(UsExperimentDailyCheck.trade_date.desc(), UsExperimentDailyCheck.source_code)
            .limit(20)
        )
    )
    return {
        "isExperimental": True,
        "researchEligible": False,
        "executionEnabled": False,
        "sources": {
            "universe": "AKShare stock_us_spot_em / Eastmoney current snapshot",
            "primaryDaily": "yfinance 1d auto_adjust=false",
            "validationDaily": "AKShare stock_us_hist adjust=''",
        },
        "schedule": {"timezone": "Asia/Shanghai", "dailyAt": "10:00"},
        "targetStartDate": TARGET_START_DATE.isoformat(),
        "snapshotAt": snapshot.updated_at.isoformat() if snapshot and snapshot.updated_at else None,
        "snapshotStatus": "ready" if snapshot else "pending_refresh",
        "universe": {
            "total": total_instruments,
            "current": current_instruments,
            "byMarket": {key: int(value) for key, value in sorted(by_market.items())},
            "selection": "m:105,m:106,m:107 全量当前目录；不设人工票数上限",
            "historicalUniverse": False,
        },
        "coverage": coverage,
        "validation": validation,
        "failedInstruments": [instrument_to_dict(item) for item in failed_instruments],
        "recentValidationAlerts": [check_to_dict(item) for item in validation_alerts],
        "recentJobs": [
            {
                "id": job.id,
                "action": job.action,
                "status": job.status,
                "rowsUpserted": int(job.rows_upserted or 0),
                "createdAt": job.created_at.isoformat() if job.created_at else None,
                "finishedAt": job.finished_at.isoformat() if job.finished_at else None,
                "message": job.message,
            }
            for job in jobs
        ],
        "limitations": [
            "当前目录不是历史 point-in-time universe，退市与历史成分尚未补齐。",
            "免费源可能限流、缺失或调整历史；失败和对照差异会单独留痕。",
            "该数据仅供实验与工程验证，researchEligible=false，不可作为正式研究输入。",
        ],
    }


def list_instruments(
    db: Session,
    *,
    q: str | None,
    current_only: bool,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    filters: list[Any] = []
    if current_only:
        filters.append(UsExperimentInstrument.is_current.is_(True))
    if q and q.strip():
        like = f"%{q.strip()}%"
        filters.append(
            or_(
                UsExperimentInstrument.source_code.ilike(like),
                UsExperimentInstrument.symbol.ilike(like),
                UsExperimentInstrument.name.ilike(like),
            )
        )
    total = int(
        db.scalar(select(func.count()).select_from(UsExperimentInstrument).where(*filters)) or 0
    )
    instruments = list(
        db.scalars(
            select(UsExperimentInstrument)
            .where(*filters)
            .order_by(UsExperimentInstrument.source_code)
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 1000))
        )
    )
    return {
        "isExperimental": True,
        "researchEligible": False,
        "executionEnabled": False,
        "items": [instrument_to_dict(item) for item in instruments],
        "total": total,
        "limit": min(max(limit, 1), 1000),
        "offset": max(offset, 0),
    }


def list_daily_checks(
    db: Session,
    *,
    source_code: str | None,
    status: str | None,
    start_date: date | None,
    end_date: date | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    filters: list[Any] = []
    if source_code and source_code.strip():
        filters.append(UsExperimentDailyCheck.source_code == source_code.strip().upper())
    if status:
        if status not in VALIDATION_STATUSES:
            raise ValueError(f"未知校验状态：{status}")
        filters.append(UsExperimentDailyCheck.status == status)
    if start_date:
        filters.append(UsExperimentDailyCheck.trade_date >= start_date)
    if end_date:
        filters.append(UsExperimentDailyCheck.trade_date <= end_date)
    page_limit = min(max(limit, 1), 500)
    page_offset = max(offset, 0)
    rows = list(
        db.scalars(
            select(UsExperimentDailyCheck)
            .where(*filters)
            .order_by(UsExperimentDailyCheck.trade_date.desc(), UsExperimentDailyCheck.source_code)
            .offset(page_offset)
            .limit(page_limit + 1)
        )
    )
    return {
        "isExperimental": True,
        "researchEligible": False,
        "executionEnabled": False,
        "items": [check_to_dict(item) for item in rows[:page_limit]],
        "limit": page_limit,
        "offset": page_offset,
        "hasMore": len(rows) > page_limit,
    }


def list_daily_bars(
    db: Session,
    source_code: str,
    *,
    start_date: date | None,
    end_date: date | None,
    limit: int,
) -> list[dict[str, Any]]:
    code = source_code.strip().upper()
    if db.get(UsExperimentInstrument, code) is None:
        return []
    stmt = (
        select(UsExperimentDailyBar)
        .where(UsExperimentDailyBar.source_code == code)
        .order_by(UsExperimentDailyBar.trade_date)
        .limit(min(max(limit, 1), 10000))
    )
    if start_date:
        stmt = stmt.where(UsExperimentDailyBar.trade_date >= start_date)
    if end_date:
        stmt = stmt.where(UsExperimentDailyBar.trade_date <= end_date)
    return [bar_to_dict(row) for row in db.scalars(stmt)]


def yfinance_frame_to_rows(frame: Any, symbols: list[str]) -> dict[str, list[dict[str, Any]]]:
    if frame is None or getattr(frame, "empty", True):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        subframe = _symbol_frame(frame, symbol, len(symbols))
        if subframe is None or getattr(subframe, "empty", True):
            continue
        rows: list[dict[str, Any]] = []
        for index, item in subframe.iterrows():
            close = finite_decimal(item.get("Close"))
            if close is None:
                continue
            rows.append(
                {
                    "trade_date": parse_date(index),
                    "open": finite_decimal(item.get("Open")),
                    "high": finite_decimal(item.get("High")),
                    "low": finite_decimal(item.get("Low")),
                    "close": close,
                    "adj_close": finite_decimal(item.get("Adj Close")),
                    "volume": finite_int(item.get("Volume")),
                    "cash_dividend": finite_decimal(item.get("Dividends")),
                    "split_ratio": finite_decimal(item.get("Stock Splits")),
                }
            )
        if rows:
            result[symbol] = normalize_daily_rows(rows)
    return result


def normalize_akshare_history_record(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "trade_date": item.get("日期") or item.get("date") or item.get("trade_date"),
        "open": item.get("开盘") if "开盘" in item else item.get("open"),
        "high": item.get("最高") if "最高" in item else item.get("high"),
        "low": item.get("最低") if "最低" in item else item.get("low"),
        "close": item.get("收盘") if "收盘" in item else item.get("close"),
        "volume": item.get("成交量") if "成交量" in item else item.get("volume"),
    }


def normalize_daily_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date: dict[date, dict[str, Any]] = {}
    for item in rows:
        try:
            trade_date = parse_date(item.get("trade_date") or item.get("date"))
        except (TypeError, ValueError):
            continue
        close = finite_decimal(item.get("close"))
        if close is None:
            continue
        by_date[trade_date] = {
            "trade_date": trade_date,
            "open": finite_decimal(item.get("open")),
            "high": finite_decimal(item.get("high")),
            "low": finite_decimal(item.get("low")),
            "close": close,
            "adj_close": finite_decimal(item.get("adj_close")),
            "volume": finite_int(item.get("volume")),
            "cash_dividend": finite_decimal(item.get("cash_dividend")),
            "split_ratio": finite_decimal(item.get("split_ratio")),
        }
    return [by_date[key] for key in sorted(by_date)]


def build_validation_row(
    source_code: str,
    yfinance_rows: list[dict[str, Any]],
    akshare_rows: list[dict[str, Any]],
    fallback_date: date,
    checked_at: datetime,
) -> dict[str, Any]:
    y_by_date = {row["trade_date"]: row for row in yfinance_rows}
    a_by_date = {row["trade_date"]: row for row in akshare_rows}
    common_dates = sorted(set(y_by_date) & set(a_by_date))
    if common_dates:
        trade_date = common_dates[-1]
        y_row, a_row = y_by_date[trade_date], a_by_date[trade_date]
        price_diffs = [
            relative_diff(y_row.get(key), a_row.get(key))
            for key in ("open", "high", "low", "close")
        ]
        price_diffs = [value for value in price_diffs if value is not None]
        max_price_diff = max(price_diffs) if price_diffs else None
        volume_diff = relative_diff(y_row.get("volume"), a_row.get("volume"))
        matches = (
            max_price_diff is not None
            and max_price_diff <= PRICE_RELATIVE_TOLERANCE
            and (volume_diff is None or volume_diff <= VOLUME_RELATIVE_TOLERANCE)
        )
        status = "match" if matches else "mismatch"
        message = (
            f"price_diff={max_price_diff}; volume_diff={volume_diff}"
            if not matches
            else None
        )
    else:
        available_dates = sorted(set(y_by_date) | set(a_by_date))
        trade_date = available_dates[-1] if available_dates else fallback_date
        y_row = y_by_date.get(trade_date, {})
        a_row = a_by_date.get(trade_date, {})
        max_price_diff = None
        volume_diff = None
        status = "source_missing"
        message = "同一交易日缺少 yfinance 或 AKShare 数据"
    return {
        "source_code": source_code,
        "trade_date": trade_date,
        **{f"yfinance_{key}": y_row.get(key) for key in ("open", "high", "low", "close", "volume")},
        **{f"akshare_{key}": a_row.get(key) for key in ("open", "high", "low", "close", "volume")},
        "max_price_relative_diff": max_price_diff,
        "volume_relative_diff": volume_diff,
        "status": status,
        "message": message,
        "checked_at": checked_at,
        "updated_at": checked_at,
    }


def instrument_to_dict(item: UsExperimentInstrument) -> dict[str, Any]:
    return {
        "sourceCode": item.source_code,
        "symbol": item.symbol,
        "yahooSymbol": item.yahoo_symbol,
        "name": item.name,
        "marketCode": item.market_code,
        "marketName": item.market_name,
        "isCurrent": item.is_current,
        "historyStartDate": item.history_start_date.isoformat() if item.history_start_date else None,
        "historyEndDate": item.history_end_date.isoformat() if item.history_end_date else None,
        "lastSyncAt": item.last_sync_at.isoformat() if item.last_sync_at else None,
        "lastSyncStatus": item.last_sync_status,
        "lastSyncError": item.last_sync_error,
    }


def check_to_dict(row: UsExperimentDailyCheck) -> dict[str, Any]:
    return {
        "sourceCode": row.source_code,
        "tradeDate": row.trade_date.isoformat(),
        "yfinance": {
            key: decimal_to_float(getattr(row, f"yfinance_{key}"))
            for key in ("open", "high", "low", "close")
        } | {"volume": row.yfinance_volume},
        "akshare": {
            key: decimal_to_float(getattr(row, f"akshare_{key}"))
            for key in ("open", "high", "low", "close")
        } | {"volume": row.akshare_volume},
        "maxPriceRelativeDiff": decimal_to_float(row.max_price_relative_diff),
        "volumeRelativeDiff": decimal_to_float(row.volume_relative_diff),
        "status": row.status,
        "message": row.message,
        "checkedAt": row.checked_at.isoformat() if row.checked_at else None,
    }


def bar_to_dict(row: UsExperimentDailyBar) -> dict[str, Any]:
    return {
        "sourceCode": row.source_code,
        "tradeDate": row.trade_date.isoformat(),
        "open": decimal_to_float(row.open),
        "high": decimal_to_float(row.high),
        "low": decimal_to_float(row.low),
        "close": decimal_to_float(row.close),
        "adjClose": decimal_to_float(row.adj_close),
        "volume": row.volume,
        "cashDividend": decimal_to_float(row.cash_dividend),
        "splitRatio": decimal_to_float(row.split_ratio),
        "source": row.source,
    }


def _upsert_universe_rows(db: Session, rows: list[dict[str, Any]], observed_at: datetime) -> None:
    if db.bind and db.bind.dialect.name == "postgresql":
        for chunk in chunks(rows, 1000):
            stmt = pg_insert(UsExperimentInstrument.__table__).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["source_code"],
                set_={
                    "symbol": stmt.excluded.symbol,
                    "yahoo_symbol": stmt.excluded.yahoo_symbol,
                    "name": stmt.excluded.name,
                    "market_code": stmt.excluded.market_code,
                    "market_name": stmt.excluded.market_name,
                    "is_current": True,
                    "last_seen_at": stmt.excluded.last_seen_at,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            db.execute(stmt)
        return
    existing = {
        item.source_code: item
        for item in db.scalars(select(UsExperimentInstrument).where(UsExperimentInstrument.source_code.in_([row["source_code"] for row in rows])))
    }
    for row in rows:
        item = existing.get(row["source_code"])
        if item is None:
            db.add(UsExperimentInstrument(first_seen_at=observed_at, **row))
            continue
        for key, value in row.items():
            setattr(item, key, value)


def _bulk_upsert(
    db: Session,
    model: type[Any],
    rows: list[dict[str, Any]],
    conflict_columns: list[str],
    *,
    exclude_updates: set[str],
) -> None:
    if not rows:
        return
    if db.bind and db.bind.dialect.name == "postgresql":
        for chunk in chunks(rows, 1000):
            stmt = pg_insert(model.__table__).values(chunk)
            updates = {
                key: getattr(stmt.excluded, key)
                for key in chunk[0]
                if key not in set(conflict_columns) | exclude_updates
            }
            db.execute(stmt.on_conflict_do_update(index_elements=conflict_columns, set_=updates))
        return
    for row in rows:
        item = db.scalar(
            select(model).where(*(getattr(model, key) == row[key] for key in conflict_columns))
        )
        if item is None:
            db.add(model(**row))
            continue
        for key, value in row.items():
            if key not in exclude_updates:
                setattr(item, key, value)


def _symbol_frame(frame: Any, symbol: str, symbol_count: int) -> Any | None:
    columns = frame.columns
    if getattr(columns, "nlevels", 1) == 1:
        return frame if symbol_count == 1 else None
    level_zero = set(columns.get_level_values(0))
    if symbol in level_zero:
        return frame[symbol]
    level_one = set(columns.get_level_values(1))
    if symbol in level_one:
        return frame.xs(symbol, axis=1, level=1)
    return None


def relative_diff(left: Any, right: Any) -> Decimal | None:
    left_value, right_value = finite_decimal(left), finite_decimal(right)
    if left_value is None or right_value is None:
        return None
    denominator = max(abs(left_value), abs(right_value), Decimal("0.00000001"))
    return abs(left_value - right_value) / denominator


def finite_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def finite_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if math.isfinite(number) else None


def parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        parsed = value.date()
        if isinstance(parsed, date):
            return parsed
    return date.fromisoformat(str(value)[:10])


def clean_text(value: Any, length: int) -> str | None:
    text = str(value or "").strip()
    return text[:length] or None


def decimal_to_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def chunks(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]
