from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.database import DATABASE_URL
from backend.app.value_sector_strategy import (
    DEFAULT_VALUE_HORIZONS,
    RULE_VALUE_SECTOR_STOPFALL,
    attach_value_forward_returns,
    build_industry_stopfall_features,
    detect_value_sector_signals,
    simulate_rebalanced_account,
    summarize_forward_returns,
)


DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "research" / "strategy-results" / "value-sector-stopfall-20260629"


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        engine = create_engine(args.database_url, pool_pre_ping=True)
        panel = load_price_value_panel(engine, args.start_date, args.end_date, args.max_symbols)
        panel = filter_sparse_trade_dates(panel, min_daily_rows=args.min_daily_rows)
        financial = load_financial_indicators(engine)
    except Exception as exc:  # noqa: BLE001
        write_blocked_report(args.output_dir, str(exc))
        print(f"blocked: {exc}")
        return 2

    if panel.empty or financial.empty:
        write_blocked_report(args.output_dir, "price/basic panel or financial indicators returned 0 rows")
        print("blocked: price/basic panel or financial indicators returned 0 rows")
        return 2

    panel = attach_financial_asof(panel, financial)
    industry_features = build_industry_stopfall_features(panel)
    panel = panel.merge(industry_features, on=["industry", "trade_date"], how="left")
    signals = detect_value_sector_signals(
        panel,
        min_undervalued_days=args.min_undervalued_days,
        lookback_days=args.lookback_days,
        max_per_day=args.max_daily_signals,
        min_amount=args.min_amount,
        min_total_mv=args.min_total_mv,
    )
    events = attach_value_forward_returns(panel, signals, horizons=DEFAULT_VALUE_HORIZONS)
    event_summary = summarize_forward_returns(events, horizons=DEFAULT_VALUE_HORIZONS)
    account_nav = simulate_rebalanced_account(
        panel[["ts_code", "trade_date", "open", "close"]],
        signals,
        initial_cash=args.initial_cash,
        rebalance_interval=args.rebalance_interval,
        max_positions=args.max_positions,
    )
    industry_summary = summarize_industries(signals)

    write_outputs(
        output_dir=args.output_dir,
        args=args,
        panel=panel,
        financial=financial,
        industry_features=industry_features,
        signals=signals,
        events=events,
        event_summary=event_summary,
        account_nav=account_nav,
        industry_summary=industry_summary,
    )
    print(f"wrote {args.output_dir}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run value-quality + industry stopfall research from local PostgreSQL data.")
    parser.add_argument("--database-url", default=DATABASE_URL)
    parser.add_argument("--start-date", default="2023-07-19")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--min-daily-rows", type=int, default=1000)
    parser.add_argument("--min-undervalued-days", type=int, default=45)
    parser.add_argument("--lookback-days", type=int, default=60)
    parser.add_argument("--max-daily-signals", type=int, default=20)
    parser.add_argument("--min-amount", type=float, default=50_000)
    parser.add_argument("--min-total-mv", type=float, default=300_000)
    parser.add_argument("--initial-cash", type=float, default=1_000_000)
    parser.add_argument("--rebalance-interval", type=int, default=60)
    parser.add_argument("--max-positions", type=int, default=20)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def load_price_value_panel(engine: Any, start_date: str | None, end_date: str | None, max_symbols: int | None) -> pd.DataFrame:
    params: dict[str, Any] = {}
    filters = ["s.industry is not null", "s.industry <> ''"]
    if start_date:
        filters.append("b.trade_date >= :start_date")
        params["start_date"] = start_date
    if end_date:
        filters.append("b.trade_date <= :end_date")
        params["end_date"] = end_date
    symbol_clause = ""
    if max_symbols:
        symbol_clause = """
        and b.ts_code in (
          select ts_code
          from stock_daily_bars
          group by ts_code
          order by count(*) desc, ts_code
          limit :max_symbols
        )
        """
        params["max_symbols"] = max_symbols
    where_clause = " and ".join(filters)
    query = text(
        f"""
        select
          b.ts_code,
          s.industry,
          b.trade_date,
          b.open,
          b.close,
          b.pct_chg,
          b.amount,
          db.pe_ttm,
          db.pb,
          db.total_mv
        from stock_daily_bars b
        join stocks s on s.ts_code = b.ts_code
        join stock_daily_basic db on db.ts_code = b.ts_code and db.trade_date = b.trade_date
        where {where_clause}
        {symbol_clause}
        order by b.ts_code, b.trade_date
        """
    )
    frame = pd.read_sql_query(query, engine, params=params, parse_dates=["trade_date"])
    frame["bar_return"] = pd.to_numeric(frame["pct_chg"], errors="coerce") / 100
    return frame


def filter_sparse_trade_dates(panel: pd.DataFrame, min_daily_rows: int) -> pd.DataFrame:
    counts = panel.groupby("trade_date")["ts_code"].count()
    keep_dates = set(counts[counts >= min_daily_rows].index)
    return panel[panel["trade_date"].isin(keep_dates)].copy()


def load_financial_indicators(engine: Any) -> pd.DataFrame:
    query = text(
        """
        select
          ts_code,
          ann_date,
          end_date,
          roe,
          netprofit_margin,
          debt_to_assets,
          tr_yoy,
          netprofit_yoy
        from stock_financial_indicators
        where ann_date is not null
        order by ts_code, ann_date, end_date
        """
    )
    return pd.read_sql_query(query, engine, parse_dates=["ann_date", "end_date"])


def attach_financial_asof(panel: pd.DataFrame, financial: pd.DataFrame) -> pd.DataFrame:
    financial_symbols = set(financial["ts_code"].dropna())
    left = panel[panel["ts_code"].isin(financial_symbols)].sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    right = financial.sort_values(["ts_code", "ann_date", "end_date"]).drop_duplicates(["ts_code", "ann_date"], keep="last")
    merged_parts = []
    for ts_code, group in left.groupby("ts_code", sort=False):
        fin_group = right[right["ts_code"] == ts_code]
        merged = pd.merge_asof(
            group.sort_values("trade_date"),
            fin_group.sort_values("ann_date"),
            left_on="trade_date",
            right_on="ann_date",
            direction="backward",
            suffixes=("", "_financial"),
        )
        merged["ts_code"] = ts_code
        if "ts_code_financial" in merged.columns:
            merged = merged.drop(columns=["ts_code_financial"])
        merged_parts.append(merged)
    if not merged_parts:
        return pd.DataFrame()
    return pd.concat(merged_parts, ignore_index=True)


def summarize_industries(signals: pd.DataFrame) -> pd.DataFrame:
    if signals.empty:
        return pd.DataFrame()
    return (
        signals.groupby("industry", as_index=False)
        .agg(signal_count=("ts_code", "count"), avg_score=("score", "mean"))
        .sort_values(["signal_count", "avg_score"], ascending=[False, False])
    )


def write_outputs(
    output_dir: Path,
    args: argparse.Namespace,
    panel: pd.DataFrame,
    financial: pd.DataFrame,
    industry_features: pd.DataFrame,
    signals: pd.DataFrame,
    events: pd.DataFrame,
    event_summary: pd.DataFrame,
    account_nav: pd.DataFrame,
    industry_summary: pd.DataFrame,
) -> None:
    signals.to_csv(output_dir / "signals.csv", index=False)
    events.to_csv(output_dir / "events.csv", index=False)
    event_summary.to_csv(output_dir / "event_summary.csv", index=False)
    account_nav.to_csv(output_dir / "account_nav.csv", index=False)
    industry_summary.to_csv(output_dir / "industry_summary.csv", index=False)

    summary = {
        "status": "ok",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "strategy": "低估质量 + 行业止跌",
        "rule": RULE_VALUE_SECTOR_STOPFALL,
        "data_coverage": {
            "min_date": str(panel["trade_date"].min().date()),
            "max_date": str(panel["trade_date"].max().date()),
            "rows": int(len(panel)),
            "symbols": int(panel["ts_code"].nunique()),
            "industries": int(panel["industry"].nunique()),
            "financial_rows": int(len(financial)),
            "financial_symbols": int(financial["ts_code"].nunique()),
        },
        "assumptions": {
            "market_filter": "不使用沪深300或大盘过滤，只使用行业等权走势止跌。",
            "industry_stopfall": "行业等权净值从20日低点反弹至少3%，且行业20日均线5日斜率为正。",
            "undervaluation_persistence": f"过去 {args.lookback_days} 个交易日中至少 {args.min_undervalued_days} 天满足 pe_ttm<=15 且 pb<=1.5。",
            "quality": "ROE>=8，净利率>0，资产负债率<=70，营收同比>=-10，净利润同比>=-20。",
            "tradability": f"20日平均成交额 amount>={args.min_amount}，总市值 total_mv>={args.min_total_mv}。",
            "event_returns": "收盘后确认，下一交易日开盘买入，观察 20/60/120/180/360 日。",
            "account_curve": f"100万初始资金，每 {args.rebalance_interval} 个交易日再平衡，最多 {args.max_positions} 只等权持有。",
        },
        "counts": {
            "signals": int(len(signals)),
            "events": int(len(events)),
            "nav_rows": int(len(account_nav)),
        },
        "event_summary": event_summary.to_dict("records"),
        "account_summary": summarize_account_nav(account_nav),
        "top_industries": industry_summary.head(20).to_dict("records"),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (output_dir / "index.html").write_text(render_html(summary, event_summary, account_nav, industry_summary), encoding="utf-8")


def summarize_account_nav(account_nav: pd.DataFrame) -> dict[str, Any]:
    if account_nav.empty:
        return {}
    nav = account_nav["nav"]
    drawdown = nav / nav.cummax() - 1
    return {
        "start_date": str(account_nav.iloc[0]["trade_date"]),
        "end_date": str(account_nav.iloc[-1]["trade_date"]),
        "ending_equity": float(account_nav.iloc[-1]["equity"]),
        "total_return": float(account_nav.iloc[-1]["nav"] - 1),
        "max_drawdown": float(drawdown.min()),
        "final_position_count": int(account_nav.iloc[-1]["position_count"]),
    }


def write_blocked_report(output_dir: Path, reason: str) -> None:
    payload = {
        "status": "blocked",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "reason": reason,
    }
    (output_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "index.html").write_text(f"<html><body><h1>blocked</h1><p>{reason}</p></body></html>", encoding="utf-8")


def render_html(summary: dict[str, Any], event_summary: pd.DataFrame, account_nav: pd.DataFrame, industry_summary: pd.DataFrame) -> str:
    nav_points = ""
    if not account_nav.empty:
        sampled = account_nav.iloc[:: max(len(account_nav) // 180, 1)]
        x_step = 740 / max(len(sampled) - 1, 1)
        points = [
            f"{10 + index * x_step:.2f},{180 - min(max((row.nav - 0.5) * 160, 0), 160):.2f}"
            for index, row in enumerate(sampled.itertuples(index=False))
        ]
        nav_points = " ".join(points)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>低估质量 + 行业止跌策略</title>
  <style>
    body {{ font-family: Arial, "Microsoft YaHei", sans-serif; margin: 32px; color: #172026; background: #f7f8fa; }}
    section {{ margin-bottom: 28px; }}
    table {{ border-collapse: collapse; width: 100%; background: #fff; }}
    th, td {{ border: 1px solid #d8dde3; padding: 8px 10px; text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
    th {{ background: #eef1f4; }}
    .meta {{ color: #5b6672; line-height: 1.7; }}
    svg {{ width: 100%; max-width: 760px; height: 220px; background: #fff; border: 1px solid #d8dde3; }}
  </style>
</head>
<body>
  <h1>低估质量 + 行业止跌策略</h1>
  <section class="meta">
    <div>状态：{summary["status"]}</div>
    <div>生成时间：{summary["generated_at"]}</div>
    <div>信号数：{summary["counts"]["signals"]}，事件数：{summary["counts"]["events"]}</div>
    <div>账户结果：总收益 {summary["account_summary"].get("total_return", 0):.2%}，最大回撤 {summary["account_summary"].get("max_drawdown", 0):.2%}</div>
  </section>
  <section>
    <h2>账户净值曲线</h2>
    <svg viewBox="0 0 760 220" role="img" aria-label="账户净值曲线">
      <polyline fill="none" stroke="#2459a6" stroke-width="2" points="{nav_points}" />
      <line x1="0" y1="180" x2="760" y2="180" stroke="#c8d0d8" stroke-width="1" />
    </svg>
  </section>
  <section>
    <h2>选出后收益</h2>
    {event_summary.to_html(index=False, border=0)}
  </section>
  <section>
    <h2>行业分布</h2>
    {industry_summary.head(30).to_html(index=False, border=0)}
  </section>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
