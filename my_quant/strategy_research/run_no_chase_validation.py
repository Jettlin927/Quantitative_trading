from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from my_quant.strategy_research.experiment.no_chase import (
    NoChaseConfig,
    evaluate_no_chase_market,
    summarize_no_chase_trades,
    write_no_chase_outputs,
)


DEFAULT_DATABASE_URL = "postgresql+psycopg://quant:quant_password@localhost:5432/quant_trading"
DEFAULT_OUTPUT_DIR = Path("docs/research/backtest-reports/no-chase-validation-2026-06-26")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate no-chase rule on local A-share daily bars.")
    parser.add_argument("--start-date", default="2021-06-28")
    parser.add_argument("--end-date", default="2026-05-29")
    parser.add_argument("--min-avg-amount", type=float, default=50000.0)
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))
    return parser.parse_args()


def load_liquid_a_share_bars(
    database_url: str,
    start_date: str,
    end_date: str,
    min_avg_amount: float,
    max_symbols: int | None,
) -> pd.DataFrame:
    limit_clause = "" if max_symbols is None else "LIMIT :max_symbols"
    query = text(
        f"""
        WITH liquid_universe AS (
            SELECT b.ts_code
            FROM stock_daily_bars b
            LEFT JOIN stocks s ON s.ts_code = b.ts_code
            WHERE b.trade_date BETWEEN :start_date AND :end_date
              AND b.amount IS NOT NULL
              AND b.open > 0
              AND b.high > 0
              AND b.low > 0
              AND b.close > 0
              AND b.pre_close > 0
              AND (s.name IS NULL OR s.name NOT LIKE '%%ST%%')
            GROUP BY b.ts_code
            HAVING AVG(b.amount) >= :min_avg_amount
            ORDER BY AVG(b.amount) DESC
            {limit_clause}
        )
        SELECT b.ts_code, b.trade_date, b.open, b.high, b.low, b.close, b.pre_close, b.amount
        FROM stock_daily_bars b
        JOIN liquid_universe u ON u.ts_code = b.ts_code
        WHERE b.trade_date BETWEEN :start_date AND :end_date
          AND b.open > 0
          AND b.high > 0
          AND b.low > 0
          AND b.close > 0
          AND b.pre_close > 0
        ORDER BY b.ts_code, b.trade_date
        """
    )
    params = {
        "start_date": start_date,
        "end_date": end_date,
        "min_avg_amount": min_avg_amount,
        "max_symbols": max_symbols,
    }
    engine = create_engine(database_url, pool_pre_ping=True)
    return pd.read_sql_query(query, engine, params=params, parse_dates=["trade_date"])


def main() -> None:
    args = parse_args()
    config = NoChaseConfig(min_amount=0.0)
    bars = load_liquid_a_share_bars(
        database_url=args.database_url,
        start_date=args.start_date,
        end_date=args.end_date,
        min_avg_amount=args.min_avg_amount,
        max_symbols=args.max_symbols,
    )
    trades = evaluate_no_chase_market(bars, config)
    summary = summarize_no_chase_trades(trades)
    summary.update(
        {
            "rule_id": "002-no-chase-after-extended-gap",
            "data_source": "docker-postgresql.stock_daily_bars",
            "start_date": args.start_date,
            "end_date": args.end_date,
            "symbol_count": int(bars["ts_code"].nunique()) if not bars.empty else 0,
            "bar_count": int(len(bars)),
            "min_avg_amount": args.min_avg_amount,
            "max_symbols": args.max_symbols,
            "config": asdict(config),
        }
    )
    generated_at = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M %z")
    paths = write_no_chase_outputs(args.output_dir, trades, summary, generated_at=generated_at)
    print(json.dumps({"summary": summary, "paths": paths}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
