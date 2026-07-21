from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from backend.app.data_quality.contracts import QualityCheckContract
from backend.app.database import Base
from backend.app.models import (
    DataQualityResult,
    DataQualityRun,
    Fund,
    FundAdjustFactor,
    FundDailyBar,
    Index,
    IndexDailyBar,
    IndustryClassification,
    IndustryMember,
    StockAdjustFactor,
    StockDailyBar,
    StockLimitPrice,
    StockListing,
    TradeCalendar,
)
from backend.app.quant_research.universe import (
    build_explicit_universe,
    build_industry_membership_universe,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "quant_research_golden"


def create_golden_database(path: Path) -> tuple[Engine, str, str]:
    engine = create_engine(f"sqlite+pysqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        quality_run_id, universe_hash = seed_golden_database(db)
    return engine, quality_run_id, universe_hash


def seed_golden_database(db: Session) -> tuple[str, str]:
    for row in _records("trade_calendars.csv"):
        db.add(
            TradeCalendar(
                exchange=row["exchange"],
                cal_date=_date(row["cal_date"]),
                is_open=bool(int(row["is_open"])),
                pretrade_date=_optional_date(row.get("pretrade_date")),
            )
        )
    for row in _records("funds.csv"):
        db.add(
            Fund(
                ts_code=row["ts_code"],
                name=row["name"],
                market=row["market"],
                fund_type=row["fund_type"],
                list_date=_date(row["list_date"]),
            )
        )
    for row in _records("fund_daily_bars.csv"):
        db.add(
            FundDailyBar(
                ts_code=row["ts_code"],
                trade_date=_date(row["trade_date"]),
                open=_decimal(row["open"]),
                high=_decimal(row["high"]),
                low=_decimal(row["low"]),
                close=_decimal(row["close"]),
                pre_close=_decimal(row["pre_close"]),
                change_amount=_decimal(row["change_amount"]),
                pct_chg=_decimal(row["pct_chg"]),
                vol=_decimal(row["vol"]),
                amount=_decimal(row["amount"]),
            )
        )
    for row in _records("fund_adjust_factors.csv"):
        db.add(
            FundAdjustFactor(
                ts_code=row["ts_code"],
                trade_date=_date(row["trade_date"]),
                adj_factor=_decimal(row["adj_factor"]),
            )
        )
    for row in _records("indices.csv"):
        db.add(
            Index(
                ts_code=row["ts_code"],
                name=row["name"],
                market=row["market"],
                publisher=row["publisher"],
                category=row["category"],
                base_date=_date(row["base_date"]),
                list_date=_date(row["list_date"]),
            )
        )
    for row in _records("index_daily_bars.csv"):
        db.add(
            IndexDailyBar(
                ts_code=row["ts_code"],
                trade_date=_date(row["trade_date"]),
                open=_decimal(row["open"]),
                high=_decimal(row["high"]),
                low=_decimal(row["low"]),
                close=_decimal(row["close"]),
                pre_close=_decimal(row["pre_close"]),
                change_amount=_decimal(row["change_amount"]),
                pct_chg=_decimal(row["pct_chg"]),
                vol=_decimal(row["vol"]),
                amount=_decimal(row["amount"]),
            )
        )

    contract = QualityCheckContract.create(
        scope="etf_time_series",
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 23),
        universe=["SYNETF.SZ"],
        benchmark="SYNIDX.SH",
        universe_type="explicit_snapshot",
        universe_source="backend/tests/fixtures/quant_research_golden/universe.txt",
        universe_as_of_date=date(2026, 1, 5),
    )
    quality_run_id = "golden-quality-ready"
    db.add(
        DataQualityRun(
            id=quality_run_id,
            scope=contract.scope,
            start_date=contract.start_date,
            end_date=contract.end_date,
            universe_hash=contract.universe_hash,
            status="ready",
            config=contract.to_config(),
            summary={
                "status": "ready",
                "resultCount": 1,
                "passedCount": 1,
                "warningCount": 0,
                "blockerCount": 0,
                "failedCount": 0,
                "blockers": [],
                "warnings": [],
                "failedRules": [],
                "limitations": [],
                "requiredDatasets": [],
                "benchmark": "SYNIDX.SH",
            },
            code_commit="golden",
            started_at=datetime(2026, 1, 24, tzinfo=timezone.utc),
            finished_at=datetime(2026, 1, 24, tzinfo=timezone.utc),
        )
    )
    db.flush()
    db.add(
        DataQualityResult(
            run_id=quality_run_id,
            rule_id="fixture.complete",
            table_name="fund_daily_bars",
            severity="info",
            status="passed",
            checked_rows=1,
            failed_rows=0,
            sample_issues=[],
        )
    )
    db.commit()
    return quality_run_id, contract.universe_hash


def golden_run_config(quality_run_id: str, _quality_universe_hash: str) -> dict[str, Any]:
    return {
        "strategyId": "sentinel_etf_baseline",
        "strategyVersion": "1",
        "scope": "etf_time_series",
        "universe": build_explicit_universe(
            ["SYNETF.SZ"],
            as_of_date="2026-01-05",
            source="backend/tests/fixtures/quant_research_golden/universe.txt",
        ),
        "warmupStart": "2026-01-05",
        "startDate": "2026-01-05",
        "endDate": "2026-01-23",
        "benchmark": "SYNIDX.SH",
        "featureParameters": {},
        "targetWeightParameters": {
            "signalDate": "2026-01-09",
            "targetWeight": "1",
        },
        "executionPolicy": {
            "signalPrice": "close",
            "executionPrice": "next_trade_open",
        },
        "costModel": {
            "buyRate": "0",
            "sellRate": "0",
            "slippageRate": "0",
        },
        "randomSeed": 7,
        "timezone": "Asia/Shanghai",
        "qualityRunId": quality_run_id,
        "allowedWarnings": [],
    }


def seed_a_share_snapshot_database(db: Session) -> QualityCheckContract:
    trade_dates = (date(2026, 1, 2), date(2026, 1, 5))
    db.add_all(
        [
            IndustryClassification(
                index_code="SYNIND.SI",
                industry_name="合成行业",
                level="L1",
                src="test",
            ),
            IndustryMember(
                index_code="SYNIND.SI",
                con_code="SYN001.SZ",
                con_name="合成一号",
                in_date=date(2020, 1, 1),
            ),
            IndustryMember(
                index_code="SYNIND.SI",
                con_code="SYN002.SH",
                con_name="合成二号",
                in_date=date(2020, 1, 1),
            ),
            StockListing(
                ts_code="SYN001.SZ",
                symbol="SYN001",
                name="合成一号",
                exchange="SZSE",
                list_status="L",
                list_date=date(2020, 1, 1),
            ),
            StockListing(
                ts_code="SYN002.SH",
                symbol="SYN002",
                name="合成二号",
                exchange="SSE",
                list_status="L",
                list_date=date(2020, 1, 1),
            ),
            Index(
                ts_code="SYNIDX.SH",
                name="合成基准",
                market="CSI",
                publisher="test",
                category="综合",
                base_date=date(2020, 1, 1),
                list_date=date(2020, 1, 1),
            ),
        ]
    )
    for offset, trade_date in enumerate(trade_dates):
        db.add(
            TradeCalendar(
                exchange="SSE",
                cal_date=trade_date,
                is_open=True,
            )
        )
        db.add(
            IndexDailyBar(
                ts_code="SYNIDX.SH",
                trade_date=trade_date,
                open=100 + offset,
                high=101 + offset,
                low=99 + offset,
                close=100 + offset,
                vol=1000,
                amount=10000,
            )
        )
        for symbol, base in (("SYN001.SZ", 10), ("SYN002.SH", 20)):
            close = base + offset
            db.add(
                StockDailyBar(
                    ts_code=symbol,
                    trade_date=trade_date,
                    open=close,
                    high=close + 1,
                    low=close - 1,
                    close=close,
                    pre_close=close,
                    vol=100,
                    amount=1000,
                )
            )
            db.add(
                StockAdjustFactor(
                    ts_code=symbol,
                    trade_date=trade_date,
                    adj_factor=1,
                )
            )
            db.add(
                StockLimitPrice(
                    ts_code=symbol,
                    trade_date=trade_date,
                    pre_close=close,
                    up_limit=close * 1.1,
                    down_limit=close * 0.9,
                )
            )
    db.commit()
    return QualityCheckContract.create(
        scope="a_share_cross_section",
        start_date=trade_dates[0],
        end_date=trade_dates[-1],
        universe=[],
        benchmark="SYNIDX.SH",
        universe_type="industry_membership",
        universe_source="industry_members",
        universe_source_key="SYNIND.SI",
    )


def a_share_snapshot_config(quality_run_id: str) -> dict[str, Any]:
    return {
        "strategyId": "a_share_price_baseline",
        "strategyVersion": "1",
        "scope": "a_share_cross_section",
        "universe": build_industry_membership_universe("SYNIND.SI"),
        "warmupStart": "2026-01-02",
        "startDate": "2026-01-02",
        "endDate": "2026-01-05",
        "benchmark": "SYNIDX.SH",
        "featureParameters": {
            "momentumLongWindow": 120,
            "momentumSkipWindow": 20,
            "volatilityWindow": 60,
        },
        "targetWeightParameters": {
            "rebalanceFrequency": "month_end",
            "topN": 2,
            "maxWeight": "0.5",
        },
        "executionPolicy": {
            "signalPrice": "close",
            "executionPrice": "next_trade_open",
        },
        "costModel": {
            "buyRate": "0",
            "sellRate": "0",
            "slippageRate": "0",
        },
        "randomSeed": 7,
        "timezone": "Asia/Shanghai",
        "qualityRunId": quality_run_id,
        "allowedWarnings": [],
    }


def _records(name: str) -> list[dict[str, Any]]:
    return pd.read_csv(FIXTURE_DIR / name, dtype=str, keep_default_na=False).to_dict("records")


def _date(value: Any) -> date:
    return date.fromisoformat(str(value))


def _optional_date(value: Any) -> date | None:
    return _date(value) if value else None


def _decimal(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value not in {"", None} else None
