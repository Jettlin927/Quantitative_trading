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
    DataQualityRun,
    Fund,
    FundAdjustFactor,
    FundDailyBar,
    Index,
    IndexDailyBar,
    TradeCalendar,
)
from backend.app.quant_research.universe import build_explicit_universe


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
                "warnings": [],
                "limitations": [],
                "benchmark": "SYNIDX.SH",
            },
            code_commit="golden",
            started_at=datetime(2026, 1, 24, tzinfo=timezone.utc),
            finished_at=datetime(2026, 1, 24, tzinfo=timezone.utc),
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


def _records(name: str) -> list[dict[str, Any]]:
    return pd.read_csv(FIXTURE_DIR / name, dtype=str, keep_default_na=False).to_dict("records")


def _date(value: Any) -> date:
    return date.fromisoformat(str(value))


def _optional_date(value: Any) -> date | None:
    return _date(value) if value else None


def _decimal(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value not in {"", None} else None
