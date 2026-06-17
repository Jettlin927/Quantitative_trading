from __future__ import annotations

import argparse
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from my_quant.strategy_research.experiment.b1_trend_pullback import (
    B1BacktestConfig,
    build_b1_panels,
    rank_b1_candidates,
    run_b1_backtest,
)
from my_quant.strategy_research.experiment.config import DATA_DIR, RESULTS_DIR
from my_quant.strategy_research.run_b1_trend_pullback import build_market_frame
from my_quant.strategy_research.run_b1_walk_forward import load_symbols_from_csv_file


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "web_report"
DEFAULT_SYMBOLS_FILE = RESULTS_DIR / "b1_tushare_active_20241231_top300_universe.csv"
DEFAULT_OUTPUT_PREFIX = "b1_premarket_plan"
DEFAULT_HTML_PATH = REPORT_DIR / "b1_premarket_plan_latest.html"


def _compact_date(value: str) -> str:
    return value.replace("-", "")


def _escape(value: object) -> str:
    if pd.isna(value):
        return ""
    return html.escape(str(value))


def _pct(value: float | int | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value) * 100:.{digits}f}%"


def _date_str(value: object) -> str:
    if pd.isna(value):
        return ""
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _read_symbol_names(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    raw = pd.read_csv(path, dtype=str)
    if "symbol" not in raw.columns or "name" not in raw.columns:
        return {}
    names: dict[str, str] = {}
    for _, row in raw.iterrows():
        names[str(row["symbol"]).split(".")[0].zfill(6)] = str(row["name"])
    return names


def next_business_day(signal_date: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(signal_date) + pd.offsets.BDay(1)


def extract_open_positions(trades: pd.DataFrame) -> list[dict[str, object]]:
    if trades.empty:
        return []
    open_lots: dict[str, list[dict[str, object]]] = {}
    ordered = trades.copy()
    ordered["date"] = pd.to_datetime(ordered["date"])
    ordered = ordered.sort_values(["date", "side"]).reset_index(drop=True)
    for _, trade in ordered.iterrows():
        symbol = str(trade["symbol"]).split(".")[0].zfill(6)
        shares = float(trade["shares"])
        price = float(trade["price"])
        if shares <= 0 or price <= 0:
            continue
        if str(trade["side"]) == "buy":
            open_lots.setdefault(symbol, []).append(
                {
                    "symbol": symbol,
                    "buy_date": pd.Timestamp(trade["date"]),
                    "buy_price": price,
                    "cost_basis": price,
                    "shares": shares,
                }
            )
            continue
        if str(trade["side"]) != "sell":
            continue
        shares_to_match = shares
        lots = open_lots.get(symbol, [])
        while shares_to_match > 1e-12 and lots:
            lot = lots[0]
            matched = min(float(lot["shares"]), shares_to_match)
            lot["shares"] = float(lot["shares"]) - matched
            shares_to_match -= matched
            if float(lot["shares"]) <= 1e-10:
                lots.pop(0)
        if not lots:
            open_lots.pop(symbol, None)
    return [lot for lots in open_lots.values() for lot in lots if float(lot["shares"]) > 1e-10]


def _market_allows_entry(market: pd.DataFrame, signal_date: pd.Timestamp) -> bool:
    if signal_date not in market.index:
        return False
    row = market.loc[signal_date]
    if pd.isna(row.get("close")) or pd.isna(row.get("bbi")):
        return False
    return bool(float(row["close"]) > float(row["bbi"]))


def build_entry_plan_rows(
    panels: dict[str, pd.DataFrame],
    market: pd.DataFrame,
    signal_date: pd.Timestamp,
    plan_date: pd.Timestamp,
    config: B1BacktestConfig,
    held_symbols: set[str],
    symbol_names: dict[str, str],
) -> list[dict[str, object]]:
    if not _market_allows_entry(market, signal_date):
        return []
    rows: list[dict[str, object]] = []
    for candidate in rank_b1_candidates(panels, signal_date, config):
        symbol = str(candidate["symbol"])
        if symbol in held_symbols:
            continue
        frame = panels[symbol]
        signal = frame.loc[signal_date]
        close = float(signal["close"])
        rows.append(
            {
                "action": "candidate_buy",
                "priority": "候选买入",
                "signal_date": _date_str(signal_date),
                "plan_date": _date_str(plan_date),
                "symbol": symbol,
                "name": symbol_names.get(symbol, ""),
                "target_weight": float(candidate["target_weight"]),
                "reference_close": close,
                "max_chase_price": close * 1.02,
                "bbi": float(signal.get("bbi", 0.0)),
                "kdj_j": float(signal.get("kdj_j", 0.0)),
                "entry_close_bbi": float(signal.get("entry_close_bbi", 0.0)),
                "entry_mom20": float(signal.get("entry_mom20", 0.0)),
                "score": float(candidate["score"]),
                "guardrail": "只在参考收盘价附近执行；高开超过约2%不追高，等回落或放弃。",
            }
        )
    return rows


def build_exit_plan_rows(
    open_positions: Iterable[dict[str, object]],
    panels: dict[str, pd.DataFrame],
    signal_date: pd.Timestamp,
    plan_date: pd.Timestamp,
    config: B1BacktestConfig,
    symbol_names: dict[str, str],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    first_take_profit = config.take_profit_levels[0] if config.take_profit_levels else 0.08
    for position in open_positions:
        symbol = str(position["symbol"])
        if symbol not in panels or signal_date not in panels[symbol].index:
            continue
        signal = panels[symbol].loc[signal_date]
        close = float(signal["close"])
        bbi = float(signal["bbi"]) if pd.notna(signal.get("bbi")) else float("nan")
        cost_basis = float(position["cost_basis"])
        unrealized_return = close / cost_basis - 1.0 if cost_basis else 0.0
        reason = ""
        if pd.notna(bbi) and close < bbi:
            reason = "break_bbi"
        elif unrealized_return >= first_take_profit:
            reason = f"take_profit_{int(first_take_profit * 100)}"
        if not reason:
            continue
        rows.append(
            {
                "action": "exit_sell",
                "priority": "先卖出/降风险",
                "reason": reason,
                "signal_date": _date_str(signal_date),
                "plan_date": _date_str(plan_date),
                "symbol": symbol,
                "name": symbol_names.get(symbol, ""),
                "buy_date": _date_str(position["buy_date"]),
                "buy_price": float(position["buy_price"]),
                "reference_close": close,
                "bbi": bbi,
                "unrealized_return": unrealized_return,
                "guardrail": "盘前预案优先处理卖出信号；若大幅低开，先降风险，不把止损拖成补仓。",
            }
        )
    return rows


def build_hold_plan_rows(
    open_positions: Iterable[dict[str, object]],
    exit_rows: list[dict[str, object]],
    panels: dict[str, pd.DataFrame],
    signal_date: pd.Timestamp,
    plan_date: pd.Timestamp,
    symbol_names: dict[str, str],
) -> list[dict[str, object]]:
    exiting = {str(row["symbol"]) for row in exit_rows}
    rows: list[dict[str, object]] = []
    for position in open_positions:
        symbol = str(position["symbol"])
        if symbol in exiting or symbol not in panels or signal_date not in panels[symbol].index:
            continue
        signal = panels[symbol].loc[signal_date]
        close = float(signal["close"])
        cost_basis = float(position["cost_basis"])
        rows.append(
            {
                "action": "hold_watch",
                "priority": "继续观察",
                "signal_date": _date_str(signal_date),
                "plan_date": _date_str(plan_date),
                "symbol": symbol,
                "name": symbol_names.get(symbol, ""),
                "buy_date": _date_str(position["buy_date"]),
                "buy_price": float(position["buy_price"]),
                "reference_close": close,
                "bbi": float(signal.get("bbi", 0.0)),
                "unrealized_return": close / cost_basis - 1.0 if cost_basis else 0.0,
                "guardrail": "未触发止盈/跌破BBI，明日不主动加仓；等待新信号。",
            }
        )
    return rows


def _market_summary(market: pd.DataFrame, signal_date: pd.Timestamp) -> dict[str, object]:
    row = market.loc[signal_date]
    return {
        "signal_date": _date_str(signal_date),
        "market_close": float(row["close"]),
        "market_bbi": float(row["bbi"]),
        "market_ma20": float(row.get("ma20", 0.0)),
        "market_ma60": float(row.get("ma60", 0.0)),
        "allows_entry": _market_allows_entry(market, signal_date),
    }


def _table(rows: list[dict[str, object]], columns: list[tuple[str, str]], empty: str) -> str:
    if not rows:
        return f"<p class='empty'>{_escape(empty)}</p>"
    head = "".join(f"<th>{_escape(label)}</th>" for _key, label in columns)
    body = []
    for row in rows:
        body.append("<tr>" + "".join(f"<td>{_escape(row.get(key, ''))}</td>" for key, _label in columns) + "</tr>")
    return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


def build_html(
    plan: dict[str, object],
    entry_rows: list[dict[str, object]],
    exit_rows: list[dict[str, object]],
    hold_rows: list[dict[str, object]],
    generated_at: str | None = None,
) -> str:
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    market = plan["market"]
    assert isinstance(market, dict)
    verdict = "允许小仓候选买入" if market["allows_entry"] and entry_rows else "没有新的买入动作"
    if exit_rows:
        verdict = "先处理卖出/风控，再看候选买入"
    entry_render_rows = [
        {
            **row,
            "target_weight": _pct(float(row["target_weight"])),
            "reference_close": f"{float(row['reference_close']):.3f}",
            "max_chase_price": f"{float(row['max_chase_price']):.3f}",
            "entry_close_bbi": _pct(float(row["entry_close_bbi"])),
            "entry_mom20": _pct(float(row["entry_mom20"])),
            "score": f"{float(row['score']):.2f}",
        }
        for row in entry_rows
    ]
    exit_render_rows = [
        {
            **row,
            "buy_price": f"{float(row['buy_price']):.3f}",
            "reference_close": f"{float(row['reference_close']):.3f}",
            "bbi": f"{float(row['bbi']):.3f}",
            "unrealized_return": _pct(float(row["unrealized_return"])),
        }
        for row in exit_rows
    ]
    hold_render_rows = [
        {
            **row,
            "buy_price": f"{float(row['buy_price']):.3f}",
            "reference_close": f"{float(row['reference_close']):.3f}",
            "bbi": f"{float(row['bbi']):.3f}",
            "unrealized_return": _pct(float(row["unrealized_return"])),
        }
        for row in hold_rows
    ]
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>B1 次日盘前预案</title>
  <style>
    :root {{ --ink:#16202a; --muted:#617080; --line:#dce3ea; --paper:#f6f8fb; --surface:#fff; --red:#c0392b; --green:#13795b; --blue:#2764a2; --amber:#a56714; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",Arial,sans-serif; color:var(--ink); background:var(--paper); line-height:1.58; }}
    .shell {{ width:min(1220px, calc(100% - 36px)); margin:0 auto; }}
    header {{ background:#fff; border-bottom:1px solid var(--line); padding:28px 0; }}
    h1 {{ margin:0 0 10px; font-size:clamp(30px,4vw,46px); letter-spacing:0; line-height:1.1; }}
    h2 {{ font-size:25px; margin:0 0 14px; }}
    h3 {{ font-size:17px; margin:0 0 8px; }}
    section {{ padding:28px 0; }}
    .lead {{ color:var(--muted); margin:0; max-width:960px; }}
    .meta {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:16px; }}
    .pill {{ border:1px solid var(--line); border-radius:999px; padding:6px 10px; background:#fff; color:var(--muted); font-size:13px; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
    .metric,.panel {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:15px; }}
    .metric span {{ display:block; color:var(--muted); font-size:12px; }}
    .metric strong {{ display:block; font-size:22px; margin-top:4px; overflow-wrap:anywhere; }}
    .panel-row {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; background:#fff; }}
    th,td {{ padding:10px 11px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; white-space:nowrap; }}
    th {{ color:var(--muted); background:#f7f9fb; position:sticky; top:0; }}
    .table-wrap {{ border:1px solid var(--line); border-radius:8px; overflow:auto; max-height:430px; background:#fff; }}
    .empty {{ color:var(--muted); background:#fff; border:1px dashed var(--line); border-radius:8px; padding:16px; }}
    .note {{ border-left:5px solid var(--amber); background:#fff9eb; color:#47320d; padding:14px 16px; border-radius:8px; }}
    code {{ background:#edf2f7; border-radius:4px; padding:2px 5px; }}
    @media (max-width:900px) {{ .grid,.panel-row {{ grid-template-columns:1fr; }} .shell {{ width:min(100% - 26px,1220px); }} }}
  </style>
</head>
<body>
  <header>
    <div class="shell">
      <h1>B1 次日盘前预案</h1>
      <p class="lead">基于 {_escape(plan['signal_date'])} 收盘后的 B1 趋势回调信号，生成 {_escape(plan['plan_date'])} 盘前执行预案。它不是回测收益展示，也不会自动下单。</p>
      <div class="meta">
        <span class="pill">结论：{_escape(verdict)}</span>
        <span class="pill">信号日：{_escape(plan['signal_date'])}</span>
        <span class="pill">预案日：{_escape(plan['plan_date'])}</span>
        <span class="pill">数据源：Tushare/本地缓存</span>
      </div>
    </div>
  </header>
  <main>
    <section>
      <div class="shell grid">
        <div class="metric"><span>市场门</span><strong>{'打开' if market['allows_entry'] else '关闭'}</strong></div>
        <div class="metric"><span>候选买入</span><strong>{len(entry_rows)} 只</strong></div>
        <div class="metric"><span>优先卖出</span><strong>{len(exit_rows)} 只</strong></div>
        <div class="metric"><span>继续观察</span><strong>{len(hold_rows)} 只</strong></div>
      </div>
    </section>
    <section>
      <div class="shell panel-row">
        <div class="panel">
          <h3>市场状态</h3>
          <p>沪深300 close={float(market['market_close']):.2f}，BBI={float(market['market_bbi']):.2f}，MA20={float(market['market_ma20']):.2f}，MA60={float(market['market_ma60']):.2f}。</p>
        </div>
        <div class="panel">
          <h3>执行顺序</h3>
          <p>先看卖出/风控，再看候选买入；若市场门关闭，不新增仓。候选只用于小仓卫星策略，不做全仓组合。</p>
        </div>
      </div>
    </section>
    <section>
      <div class="shell">
        <h2>1. 明日优先卖出/风控</h2>
        {_table(exit_render_rows, [('symbol','代码'),('name','名称'),('reason','触发原因'),('buy_date','买入日'),('buy_price','买入价'),('reference_close','信号收盘'),('bbi','BBI'),('unrealized_return','浮盈亏'),('guardrail','执行约束')], '没有持仓触发跌破 BBI 或 8% 止盈。')}
      </div>
    </section>
    <section>
      <div class="shell">
        <h2>2. 明日候选买入</h2>
        {_table(entry_render_rows, [('symbol','代码'),('name','名称'),('target_weight','目标仓位'),('reference_close','参考收盘'),('max_chase_price','不追高上限'),('entry_close_bbi','close/BBI'),('entry_mom20','20日动量'),('score','B1分数'),('guardrail','执行约束')], '没有新的候选买入；空仓优先等待，不为了交易而交易。')}
      </div>
    </section>
    <section>
      <div class="shell">
        <h2>3. 继续观察持仓</h2>
        {_table(hold_render_rows, [('symbol','代码'),('name','名称'),('buy_date','买入日'),('buy_price','买入价'),('reference_close','信号收盘'),('bbi','BBI'),('unrealized_return','浮盈亏'),('guardrail','执行约束')], '模型没有需要继续观察的旧持仓。')}
      </div>
    </section>
    <section>
      <div class="shell">
        <p class="note">纪律提醒：低位不是入场信号，止跌才是入场信号。盘前预案只给条件和顺序，不保证收益，也不替代你自己的下单确认。</p>
      </div>
    </section>
  </main>
  <footer class="shell" style="padding:24px 0 40px;color:var(--muted);font-size:13px;">生成时间：{_escape(generated_at)}</footer>
</body>
</html>
"""


def write_outputs(
    output_prefix: str,
    html_path: Path,
    plan: dict[str, object],
    entry_rows: list[dict[str, object]],
    exit_rows: list[dict[str, object]],
    hold_rows: list[dict[str, object]],
) -> dict[str, str]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(build_html(plan, entry_rows, exit_rows, hold_rows), encoding="utf-8")
    entry_path = RESULTS_DIR / f"{output_prefix}_entry_plan.csv"
    exit_path = RESULTS_DIR / f"{output_prefix}_exit_plan.csv"
    hold_path = RESULTS_DIR / f"{output_prefix}_hold_plan.csv"
    json_path = RESULTS_DIR / f"{output_prefix}_plan.json"
    pd.DataFrame(entry_rows).to_csv(entry_path, index=False)
    pd.DataFrame(exit_rows).to_csv(exit_path, index=False)
    pd.DataFrame(hold_rows).to_csv(hold_path, index=False)
    json_path.write_text(
        json.dumps(
            {
                "plan": plan,
                "entry_count": len(entry_rows),
                "exit_count": len(exit_rows),
                "hold_count": len(hold_rows),
                "html": str(html_path),
                "artifacts": {
                    "entry_plan": entry_path.name,
                    "exit_plan": exit_path.name,
                    "hold_plan": hold_path.name,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"html": str(html_path), "entry_plan": str(entry_path), "exit_plan": str(exit_path), "hold_plan": str(hold_path), "json": str(json_path)}


def build_premarket_plan(
    symbols_file: Path = DEFAULT_SYMBOLS_FILE,
    start: str = "2025-01-01",
    end: str = "2026-05-15",
    history_start: str = "2024-06-01",
    data_dir: Path = DATA_DIR / "b1_a_share",
    output_prefix: str = DEFAULT_OUTPUT_PREFIX,
    html_path: Path = DEFAULT_HTML_PATH,
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], dict[str, str]]:
    config = B1BacktestConfig(
        take_profit_levels=(0.08, 0.16, 0.24),
        take_profit_fractions=(1.0, 1.0, 1.0),
        max_entry_close_bbi=0.275,
        min_entry_mom20=0.02,
        max_entry_mom20=0.75,
    )
    symbols = load_symbols_from_csv_file(symbols_file)
    panels = build_b1_panels(
        symbols=symbols,
        start=_compact_date(history_start),
        end=_compact_date(end),
        data_dir=data_dir,
        config=config,
        refresh=False,
        data_provider="tushare",
    )
    market = build_market_frame(
        history_start,
        end,
        start,
        data_provider="tushare",
        data_dir=data_dir,
        require_ma20_gt_ma60=True,
    )
    if market.empty:
        raise RuntimeError("market frame is empty; cannot build B1 premarket plan")
    signal_date = pd.Timestamp(market.index.max())
    plan_date = next_business_day(signal_date)
    market_dates = list(pd.DatetimeIndex(market.index).sort_values())
    previous_dates = [date for date in market_dates if date < signal_date]
    if previous_dates:
        prev_market = market.loc[: previous_dates[-1]]
        prev_result = run_b1_backtest(panels, prev_market, config)
        open_positions = extract_open_positions(prev_result.trades)
    else:
        open_positions = []
    names = _read_symbol_names(symbols_file)
    exit_rows = build_exit_plan_rows(open_positions, panels, signal_date, plan_date, config, names)
    hold_rows = build_hold_plan_rows(open_positions, exit_rows, panels, signal_date, plan_date, names)
    held_symbols = {str(position["symbol"]) for position in open_positions}
    entry_rows = build_entry_plan_rows(panels, market, signal_date, plan_date, config, held_symbols, names)
    plan = {
        "strategy": output_prefix,
        "signal_date": _date_str(signal_date),
        "plan_date": _date_str(plan_date),
        "symbol_count": len(panels),
        "market": _market_summary(market, signal_date),
        "rule": "B1趋势回调：收盘>BBI、双EMA>BBI、KDJ.J<13，市场门打开才允许次日候选买入。",
    }
    artifacts = write_outputs(output_prefix, html_path, plan, entry_rows, exit_rows, hold_rows)
    return plan, entry_rows, exit_rows, hold_rows, artifacts


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the B1 next-session premarket execution plan.")
    parser.add_argument("--symbols-file", type=Path, default=DEFAULT_SYMBOLS_FILE)
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--history-start", default="2024-06-01")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "b1_a_share")
    parser.add_argument("--output-prefix", default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--html-path", type=Path, default=DEFAULT_HTML_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    plan, entry_rows, exit_rows, hold_rows, artifacts = build_premarket_plan(
        symbols_file=args.symbols_file,
        start=args.start,
        end=args.end,
        history_start=args.history_start,
        data_dir=args.data_dir,
        output_prefix=args.output_prefix,
        html_path=args.html_path,
    )
    print(f"html={artifacts['html']}")
    print(f"signal_date={plan['signal_date']}")
    print(f"plan_date={plan['plan_date']}")
    print(f"entry_count={len(entry_rows)}")
    print(f"exit_count={len(exit_rows)}")
    print(f"hold_count={len(hold_rows)}")
    print(f"market_allows_entry={plan['market']['allows_entry']}")  # type: ignore[index]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
