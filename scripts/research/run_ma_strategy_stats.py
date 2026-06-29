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
from backend.app.ma_strategy_stats import (
    DEFAULT_HORIZONS,
    TradeCost,
    attach_forward_returns,
    detect_filtered_trend_entry_signals,
    detect_ma_signals,
    prepare_price_panel,
    select_daily_top_signals,
    simulate_horizon_portfolio,
    summarize_event_returns,
)


DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "research" / "strategy-results" / "ma-trend-reversal-20260629"


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        engine = create_engine(args.database_url, pool_pre_ping=True)
        coverage = load_coverage(engine)
        bars = load_daily_bars(engine, args.start_date, args.end_date, args.max_symbols)
    except Exception as exc:  # noqa: BLE001
        write_blocked_report(output_dir, str(exc))
        print(f"blocked: {exc}")
        return 2

    if bars.empty:
        write_blocked_report(output_dir, "stock_daily_bars returned 0 rows")
        print("blocked: stock_daily_bars returned 0 rows")
        return 2

    panel = prepare_price_panel(bars.to_dict("records"))
    raw_panel = panel.copy()
    raw_panel["market_filter_pass"] = True
    raw_signals = detect_ma_signals(raw_panel)
    base_signals = detect_ma_signals(panel)
    filtered_signals = detect_filtered_trend_entry_signals(panel)
    top_signals = select_daily_top_signals(filtered_signals, max_per_day=args.max_daily_buys)
    signals = pd.concat([base_signals, filtered_signals, top_signals], ignore_index=True)
    events = attach_forward_returns(panel, signals, horizons=DEFAULT_HORIZONS)
    event_summary = summarize_event_returns(events, horizons=DEFAULT_HORIZONS)
    regime_summary = summarize_by_market_regime(events)
    market_filter_summary = summarize_market_filter(raw_signals, signals)
    portfolio_summary = build_portfolio_summary(events, initial_cash=args.initial_cash)

    write_outputs(
        output_dir=output_dir,
        coverage=coverage,
        args=args,
        signals=signals,
        events=events,
        event_summary=event_summary,
        regime_summary=regime_summary,
        market_filter_summary=market_filter_summary,
        portfolio_summary=portfolio_summary,
    )
    print(f"wrote {output_dir}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MA5/MA20/MA60 event statistics from local PostgreSQL daily bars.")
    parser.add_argument("--database-url", default=DATABASE_URL)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--initial-cash", type=float, default=1_000_000)
    parser.add_argument("--max-daily-buys", type=int, default=10)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def load_coverage(engine: Any) -> dict[str, Any]:
    query = text(
        """
        select
          min(trade_date) as min_date,
          max(trade_date) as max_date,
          count(*) as rows,
          count(distinct ts_code) as symbols,
          count(distinct trade_date) as trade_dates
        from stock_daily_bars
        """
    )
    with engine.connect() as connection:
        row = connection.execute(query).mappings().one()
    return {
        "min_date": str(row["min_date"]) if row["min_date"] else None,
        "max_date": str(row["max_date"]) if row["max_date"] else None,
        "rows": int(row["rows"] or 0),
        "symbols": int(row["symbols"] or 0),
        "trade_dates": int(row["trade_dates"] or 0),
    }


def load_daily_bars(engine: Any, start_date: str | None, end_date: str | None, max_symbols: int | None) -> pd.DataFrame:
    params: dict[str, Any] = {}
    filters = []
    if start_date:
        filters.append("trade_date >= :start_date")
        params["start_date"] = start_date
    if end_date:
        filters.append("trade_date <= :end_date")
        params["end_date"] = end_date
    symbol_clause = ""
    if max_symbols:
        symbol_clause = """
        and ts_code in (
          select ts_code
          from stock_daily_bars
          group by ts_code
          order by count(*) desc, ts_code
          limit :max_symbols
        )
        """
        params["max_symbols"] = max_symbols
    where_clause = f"where {' and '.join(filters)}" if filters else "where true"
    query = text(
        f"""
        select ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount
        from stock_daily_bars
        {where_clause}
        {symbol_clause}
        order by ts_code, trade_date
        """
    )
    return pd.read_sql_query(query, engine, params=params, parse_dates=["trade_date"])


def summarize_by_market_regime(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty or "market_regime" not in events.columns:
        return pd.DataFrame()
    rows = []
    for (rule, regime), group in events.groupby(["rule", "market_regime"], dropna=False, sort=True):
        summary = summarize_event_returns(group, horizons=DEFAULT_HORIZONS)
        for item in summary.to_dict("records"):
            item["market_regime"] = regime
            rows.append(item)
    return pd.DataFrame(rows)


def summarize_market_filter(raw_signals: pd.DataFrame, filtered_signals: pd.DataFrame) -> pd.DataFrame:
    if raw_signals.empty:
        return pd.DataFrame(columns=["rule", "market_regime", "raw_signals", "passed_signals", "filtered_out"])
    raw_counts = raw_signals.groupby(["rule", "market_regime"], dropna=False).size().reset_index(name="raw_signals")
    passed_counts = filtered_signals.groupby(["rule", "market_regime"], dropna=False).size().reset_index(name="passed_signals")
    summary = raw_counts.merge(passed_counts, on=["rule", "market_regime"], how="left")
    summary["passed_signals"] = summary["passed_signals"].fillna(0).astype(int)
    summary["filtered_out"] = summary["raw_signals"] - summary["passed_signals"]
    return summary.sort_values(["rule", "market_regime"]).reset_index(drop=True)


def build_portfolio_summary(events: pd.DataFrame, initial_cash: float) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    rows = []
    for rule, rule_events in events.groupby("rule", sort=True):
        for horizon in DEFAULT_HORIZONS:
            summary = simulate_horizon_portfolio(rule_events, horizon=horizon, initial_cash=initial_cash)
            summary["rule"] = rule
            rows.append(summary)
    return pd.DataFrame(rows)


def write_outputs(
    output_dir: Path,
    coverage: dict[str, Any],
    args: argparse.Namespace,
    signals: pd.DataFrame,
    events: pd.DataFrame,
    event_summary: pd.DataFrame,
    regime_summary: pd.DataFrame,
    market_filter_summary: pd.DataFrame,
    portfolio_summary: pd.DataFrame,
) -> None:
    signals.to_csv(output_dir / "signals.csv", index=False)
    events.to_csv(output_dir / "events.csv", index=False)
    event_summary.to_csv(output_dir / "event_summary.csv", index=False)
    regime_summary.to_csv(output_dir / "market_regime_summary.csv", index=False)
    market_filter_summary.to_csv(output_dir / "market_filter_summary.csv", index=False)
    portfolio_summary.to_csv(output_dir / "portfolio_summary.csv", index=False)

    summary = {
        "status": "ok",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "strategy": "MA5/MA20/MA60 趋势跟踪 + 趋势反转",
        "data_coverage": coverage,
        "assumptions": {
            "signal_timing": "收盘后确认信号，下一交易日开盘买入。",
            "market_filter": "从 stock_daily_bars 构造全市场等权市场代理，market_nav > MA60 且 MA20 > MA60 时允许开仓。",
            "trend_following": "MA5 / MA20 - 1 >= 5% 连续 3 个交易日，且只记录首次确认日。",
            "trend_reversal": "MA5 当日上穿 MA20，MA5 较前日抬升，且 MA5 与 MA20 均在 MA60 上方。",
            "filtered_trend_entry": (
                "大盘 risk_on，MA5 > MA20 连续 3 天，MA20 5日斜率为正，"
                "MA20 > MA60 或 MA60 10日斜率为正，收盘价位于 MA20 上方且距离不超过 8%，"
                "20日平均成交额不低于 5000 万元等价的 Tushare amount=50000，并对同一标的设置 20 个交易日冷却。"
            ),
            "filtered_trend_entry_top10": f"在过滤版信号上按强度评分排序，每个交易日最多保留 {args.max_daily_buys} 个信号。",
            "costs": TradeCost().__dict__,
            "portfolio": {
                "initial_cash": args.initial_cash,
                "max_positions": 20,
                "target_position_pct": 0.05,
                "lot_size": 100,
                "volume_capacity_pct": 0.05,
            },
        },
        "counts": {
            "signals": int(len(signals)),
            "events": int(len(events)),
        },
        "event_summary": event_summary.to_dict("records"),
        "market_filter_summary": market_filter_summary.to_dict("records"),
        "portfolio_summary": portfolio_summary.to_dict("records"),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (output_dir / "index.html").write_text(render_html(summary, event_summary, regime_summary, market_filter_summary, portfolio_summary), encoding="utf-8")


def write_blocked_report(output_dir: Path, reason: str) -> None:
    summary = {
        "status": "blocked",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "reason": reason,
        "required_data_source": "local PostgreSQL table stock_daily_bars",
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "index.html").write_text(render_blocked_html(summary), encoding="utf-8")


def render_html(
    summary: dict[str, Any],
    event_summary: pd.DataFrame,
    regime_summary: pd.DataFrame,
    market_filter_summary: pd.DataFrame,
    portfolio_summary: pd.DataFrame,
) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>MA 趋势跟踪与趋势反转统计</title>
  <style>
    body {{ font-family: Arial, "Microsoft YaHei", sans-serif; margin: 32px; color: #172026; background: #f7f8fa; }}
    h1, h2 {{ margin: 0 0 14px; }}
    section {{ margin: 0 0 28px; }}
    table {{ border-collapse: collapse; width: 100%; background: #fff; }}
    th, td {{ border: 1px solid #d8dde3; padding: 8px 10px; text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
    th {{ background: #eef1f4; }}
    .meta {{ color: #5b6672; line-height: 1.6; }}
  </style>
</head>
<body>
  <h1>MA 趋势跟踪与趋势反转统计</h1>
  <section class="meta">
    <div>状态：{summary["status"]}</div>
    <div>生成时间：{summary["generated_at"]}</div>
    <div>信号数：{summary["counts"]["signals"]}，可计算事件数：{summary["counts"]["events"]}</div>
  </section>
  <section>
    <h2>事件收益</h2>
    {event_summary.to_html(index=False, border=0)}
  </section>
  <section>
    <h2>市场环境拆分</h2>
    {regime_summary.to_html(index=False, border=0)}
  </section>
  <section>
    <h2>大盘过滤前后</h2>
    {market_filter_summary.to_html(index=False, border=0)}
  </section>
  <section>
    <h2>100 万组合模拟</h2>
    {portfolio_summary.to_html(index=False, border=0)}
  </section>
</body>
</html>
"""


def render_blocked_html(summary: dict[str, Any]) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>MA 策略统计未完成</title></head>
<body>
  <h1>MA 策略统计未完成</h1>
  <p>状态：{summary["status"]}</p>
  <p>原因：{summary["reason"]}</p>
  <p>需要的数据源：{summary["required_data_source"]}</p>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
