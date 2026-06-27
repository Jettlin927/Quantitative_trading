from __future__ import annotations

import argparse
import html
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

import pandas as pd

from my_quant.strategy_research.experiment.config import COST_RATE, RESULTS_DIR
from my_quant.strategy_research.web_report.build_b1_quality_report import (
    build_round_trips,
    svg_nav_curve,
    svg_pct_histogram,
    svg_sorted_pnl_bars,
)


REPORT_DIR = Path(__file__).resolve().parent
DEFAULT_PREFIX = "b1_small_capital_mainboard_final_20260617"
DEFAULT_HTML_PATH = REPORT_DIR / "b1_small_capital_mainboard_strategy.html"
INITIAL_CASH = 20_000.0


@dataclass
class SmallCapitalReportData:
    prefix: str
    nav: pd.DataFrame
    trades: pd.DataFrame
    round_trips: pd.DataFrame
    summary: dict[str, object]
    details: pd.DataFrame
    manifest: dict[str, object]
    artifacts: dict[str, str]


def _escape(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return html.escape(str(value))


def _money(value: float | int | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value):,.{digits}f}"


def _pct(value: float | int | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value) * 100:.{digits}f}%"


def _format_date(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _shares(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return ""
    number = float(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}"


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not pd.isna(value):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _status_text(value: object) -> str:
    return "通过" if _truthy(value) else "未通过"


def _now_text() -> str:
    now = datetime.now().astimezone()
    offset = now.strftime("%z")
    if len(offset) == 5:
        offset = f"{offset[:3]}:{offset[3:]}"
    return f"{now:%Y-%m-%d %H:%M} {offset}"


def _read_csv(path: Path, dtype: dict[str, object] | None = None) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"missing report input: {path}")
    return pd.read_csv(path, dtype=dtype)


def load_report_data(prefix: str = DEFAULT_PREFIX, results_dir: Path = RESULTS_DIR) -> SmallCapitalReportData:
    results_dir = Path(results_dir)
    nav_path = results_dir / f"{prefix}_full_nav.csv"
    trades_path = results_dir / f"{prefix}_full_trades.csv"
    summary_path = results_dir / f"{prefix}_summary.csv"
    details_path = results_dir / f"{prefix}_details.csv"
    manifest_path = results_dir / f"{prefix}_full_manifest.json"

    nav = _read_csv(nav_path)
    trades = _read_csv(trades_path, dtype={"symbol": str})
    summary_frame = _read_csv(summary_path)
    details = _read_csv(details_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    summary = summary_frame.iloc[0].to_dict() if not summary_frame.empty else {}
    round_trips = build_round_trips(trades, cost_rate=COST_RATE, display_capital=1.0)

    return SmallCapitalReportData(
        prefix=prefix,
        nav=nav,
        trades=trades,
        round_trips=round_trips,
        summary=summary,
        details=details,
        manifest=manifest,
        artifacts={
            "净值": str(nav_path),
            "买卖流水": str(trades_path),
            "窗口明细": str(details_path),
            "全窗口汇总": str(summary_path),
            "回测 manifest": str(manifest_path),
        },
    )


def _closed_round_trips(data: SmallCapitalReportData) -> pd.DataFrame:
    if data.round_trips.empty:
        return data.round_trips
    return data.round_trips[data.round_trips["status"] == "closed"].copy()


def _metric_grid(data: SmallCapitalReportData) -> str:
    nav = data.nav["nav"].astype(float)
    final_nav = float(nav.iloc[-1]) if len(nav) else 1.0
    closed = _closed_round_trips(data)
    wins = int((closed["net_pnl"] > 0).sum()) if not closed.empty else 0
    win_rate = wins / len(closed) if len(closed) else None
    final_equity = INITIAL_CASH * final_nav
    total_pnl = final_equity - INITIAL_CASH
    summary = data.summary
    details_full = data.details[data.details["window"] == "full"] if not data.details.empty else pd.DataFrame()
    full = details_full.iloc[0].to_dict() if not details_full.empty else {}

    cells = [
        ("主口径结论", "阶段通过" if _truthy(summary.get("passes_all_windows")) else "观察"),
        ("起始本金", f"{_money(INITIAL_CASH, 0)} 元"),
        ("最终净值", f"{final_nav:.2f}x"),
        ("累计盈亏", f"{_money(total_pnl)} 元"),
        ("全窗口年化", _pct(full.get("annual_return"))),
        ("最大回撤", _pct(full.get("max_drawdown"))),
        ("收益门通过", f"{int(float(summary.get('return_pass_windows', 0)))}/{int(float(summary.get('windows', 0)))}"),
        ("回撤门通过", f"{int(float(summary.get('drawdown_pass_windows', 0)))}/{int(float(summary.get('windows', 0)))}"),
        ("闭环交易胜率", _pct(win_rate)),
        ("闭环交易数", f"{len(closed)} 笔"),
        ("买卖流水数", f"{len(data.trades)} 行"),
        ("标的池数量", f"{int(float(full.get('symbol_count', data.manifest.get('symbol_count', 0))))} 只"),
    ]
    return "".join(
        f"<div class='metric'><span>{_escape(label)}</span><strong>{_escape(value)}</strong></div>"
        for label, value in cells
    )


def _window_table(data: SmallCapitalReportData) -> str:
    if data.details.empty:
        return "<p class='empty'>没有窗口明细。</p>"
    rows = data.details.copy()
    rows["window_order"] = rows["window"].map(
        {
            "full": 0,
            "train_2025": 1,
            "oos_2026": 2,
            "wf_2025_h1": 3,
            "wf_2025_h2": 4,
            "wf_2026_h1": 5,
        }
    ).fillna(99)
    rows = rows.sort_values("window_order")
    body = []
    for _, row in rows.iterrows():
        annual_ok = _truthy(row["passes_return_gate"])
        drawdown_ok = _truthy(row["passes_drawdown_gate"])
        body.append(
            "<tr>"
            f"<td>{_escape(row['window'])}</td>"
            f"<td>{_escape(row['start'])}</td>"
            f"<td>{_escape(row['end'])}</td>"
            f"<td>{int(float(row['symbol_count']))}</td>"
            f"<td>{_pct(row['annual_return'])}</td>"
            f"<td>{_pct(row['max_drawdown'])}</td>"
            f"<td>{float(row['calmar']):.2f}</td>"
            f"<td class='{ 'pass' if annual_ok else 'fail' }'>{_status_text(row['passes_return_gate'])}</td>"
            f"<td class='{ 'pass' if drawdown_ok else 'fail' }'>{_status_text(row['passes_drawdown_gate'])}</td>"
            f"<td>{int(float(row['trade_count']))}</td>"
            f"<td>{int(float(row['candidate_count']))}</td>"
            "</tr>"
        )
    return (
        "<div class='table-wrap'><table>"
        "<thead><tr><th>窗口</th><th>开始</th><th>结束</th><th>标的数</th><th>年化</th>"
        "<th>最大回撤</th><th>Calmar</th><th>50% 年化门</th><th>-30% 回撤门</th><th>买卖行</th><th>候选数</th></tr></thead>"
        "<tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def _round_trip_table(data: SmallCapitalReportData) -> str:
    round_trips = data.round_trips.copy()
    if round_trips.empty:
        return "<p class='empty'>没有闭环交易。</p>"
    body = []
    for _, row in round_trips.iterrows():
        pnl = float(row["net_pnl_display"])
        ret = float(row["net_return_pct"])
        body.append(
            "<tr>"
            f"<td>{int(row['trade_id'])}</td>"
            f"<td><code>{_escape(row['symbol'])}</code></td>"
            f"<td>{_escape(row['status'])}</td>"
            f"<td>{_format_date(row['buy_date'])}</td>"
            f"<td>{_format_date(row['sell_date'])}</td>"
            f"<td>{int(row['holding_days'])}</td>"
            f"<td>{_shares(row['buy_shares'])}</td>"
            f"<td>{float(row['buy_price']):.3f}</td>"
            f"<td>{float(row['average_sell_price']):.3f}</td>"
            f"<td>{_money(row['buy_value'])}</td>"
            f"<td>{_money(row['sell_value'])}</td>"
            f"<td class='{ 'pos' if pnl >= 0 else 'neg' }'>{_money(pnl)}</td>"
            f"<td class='{ 'pos' if ret >= 0 else 'neg' }'>{_pct(ret)}</td>"
            f"<td>{_escape(row['exit_reasons'])}</td>"
            "</tr>"
        )
    return (
        "<div class='table-wrap tall'><table>"
        "<thead><tr><th>#</th><th>代码</th><th>状态</th><th>买入日</th><th>卖出日</th><th>持有天数</th>"
        "<th>股数</th><th>买入价</th><th>均卖价</th><th>买入金额</th><th>卖出金额</th><th>净盈亏(元)</th><th>净盈亏率</th><th>退出原因</th></tr></thead>"
        "<tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def _raw_trade_table(data: SmallCapitalReportData) -> str:
    trades = data.trades.copy()
    if trades.empty:
        return "<p class='empty'>没有买卖流水。</p>"
    trades["date"] = pd.to_datetime(trades["date"])
    trades = trades.reset_index(drop=True)
    body = []
    for index, row in trades.iterrows():
        symbol = str(row["symbol"]).split(".")[0].zfill(6)
        side = str(row["side"])
        side_label = "买入" if side == "buy" else "卖出"
        value = float(row["value"]) if pd.notna(row["value"]) else float(row["shares"]) * float(row["price"])
        body.append(
            "<tr>"
            f"<td>{index + 1}</td>"
            f"<td>{_format_date(row['date'])}</td>"
            f"<td><code>{_escape(symbol)}</code></td>"
            f"<td class='{ 'buy' if side == 'buy' else 'sell' }'>{side_label}</td>"
            f"<td>{_shares(row['shares'])}</td>"
            f"<td>{float(row['price']):.3f}</td>"
            f"<td>{_money(value)}</td>"
            f"<td>{_escape(row['reason'])}</td>"
            f"<td>{_money(row['cash_after'])}</td>"
            "</tr>"
        )
    return (
        "<div class='table-wrap tall'><table>"
        "<thead><tr><th>#</th><th>日期</th><th>代码</th><th>方向</th><th>股数</th><th>价格</th>"
        "<th>成交金额(元)</th><th>原因</th><th>成交后现金(元)</th></tr></thead>"
        "<tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def _artifact_list(data: SmallCapitalReportData) -> str:
    items = []
    for label, path in data.artifacts.items():
        items.append(f"<li><span>{_escape(label)}</span><code>{_escape(Path(path).name)}</code></li>")
    return "<ul class='artifact-list'>" + "".join(items) + "</ul>"


def build_html(data: SmallCapitalReportData, generated_at: str | None = None) -> str:
    generated_at = generated_at or _now_text()
    closed = _closed_round_trips(data)
    amount_values = closed["net_pnl_display"].astype(float).tolist() if not closed.empty else []
    pct_values = closed["net_return_pct"].astype(float).tolist() if not closed.empty else []
    final_nav = float(data.nav["nav"].iloc[-1]) if not data.nav.empty else 1.0
    summary = data.summary
    conclusion = "阶段通过" if _truthy(summary.get("passes_all_windows")) else "观察"
    fail_note = (
        f"收益门失败 {int(float(summary.get('return_fail_windows', 0)))} 个窗口，"
        f"回撤门失败 {int(float(summary.get('drawdown_fail_windows', 0)))} 个窗口。"
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>B1 小本金主板策略执行报告</title>
  <style>
    :root {{
      --ink: #1f2933;
      --muted: #66727f;
      --paper: #f5f7fa;
      --surface: #ffffff;
      --line: #dce3ea;
      --soft-line: #edf1f5;
      --red: #d83b2d;
      --red-dark: #a92d24;
      --green: #16865f;
      --blue: #2e6f9e;
      --amber: #b7791f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", Arial, sans-serif;
      color: var(--ink);
      background: var(--paper);
      line-height: 1.56;
    }}
    a {{ color: inherit; }}
    .topbar {{
      min-height: 56px;
      background: var(--red);
      color: #fff;
      display: flex;
      align-items: center;
      padding: 0 24px;
      font-weight: 800;
      letter-spacing: 0;
    }}
    .layout {{ display: grid; grid-template-columns: 78px minmax(0, 1fr); min-height: calc(100vh - 56px); }}
    .rail {{
      background: #151a1f;
      color: #b9c2cc;
      border-right: 1px solid #252c33;
      padding: 18px 10px;
    }}
    .rail a {{
      display: block;
      text-decoration: none;
      color: #b9c2cc;
      font-size: 12px;
      text-align: center;
      padding: 12px 4px;
      border-radius: 6px;
      margin-bottom: 8px;
    }}
    .rail a:hover {{ background: #222a31; color: #fff; }}
    .content {{ min-width: 0; }}
    .shell {{ width: min(1380px, calc(100% - 48px)); margin: 0 auto; }}
    .subnav {{
      background: #fff;
      border-bottom: 1px solid var(--line);
      position: sticky;
      top: 0;
      z-index: 4;
    }}
    .subnav .shell {{
      min-height: 58px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
    }}
    .subnav strong {{ color: var(--red); }}
    .subnav span {{ color: var(--muted); font-size: 13px; }}
    .hero {{ background: #fff; border-bottom: 1px solid var(--line); padding: 28px 0 24px; }}
    h1 {{ margin: 0 0 10px; font-size: clamp(28px, 3.2vw, 44px); line-height: 1.12; letter-spacing: 0; }}
    h2 {{ margin: 0 0 14px; font-size: 25px; line-height: 1.2; letter-spacing: 0; }}
    h3 {{ margin: 0 0 10px; font-size: 17px; }}
    .lead {{ margin: 0; max-width: 940px; color: var(--muted); font-size: 16px; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }}
    .pill {{
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 5px 10px;
      color: var(--muted);
      background: #fff;
      font-size: 12px;
    }}
    .pill.strong {{ color: var(--red-dark); border-color: #f2bbb5; background: #fff5f3; font-weight: 700; }}
    section {{ padding: 28px 0; }}
    .metric-grid {{ display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; }}
    .metric {{
      min-height: 82px;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
    }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 4px; font-size: 21px; line-height: 1.15; overflow-wrap: anywhere; }}
    .panel {{
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }}
    .panel + .panel {{ margin-top: 14px; }}
    .grid-2 {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    .grid-3 {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }}
    .grid-2 > *, .grid-3 > * {{ min-width: 0; }}
    .plan-step {{ border-left: 4px solid var(--blue); }}
    .plan-step:nth-child(2) {{ border-left-color: var(--red); }}
    .plan-step:nth-child(3) {{ border-left-color: var(--amber); }}
    .muted {{ color: var(--muted); }}
    .chart-svg {{ width: 100%; height: auto; display: block; }}
    .table-wrap {{
      width: 100%;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }}
    .table-wrap.tall {{ max-height: 560px; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 980px; }}
    th, td {{
      padding: 9px 10px;
      border-bottom: 1px solid var(--soft-line);
      text-align: left;
      vertical-align: top;
      font-size: 13px;
      white-space: nowrap;
    }}
    th {{
      color: var(--muted);
      background: #f8fafc;
      font-weight: 700;
      position: sticky;
      top: 0;
      z-index: 1;
    }}
    tr:last-child td {{ border-bottom: none; }}
    code {{
      font-family: "SFMono-Regular", Menlo, Consolas, monospace;
      font-size: 12px;
      background: #eef2f6;
      padding: 2px 5px;
      border-radius: 4px;
    }}
    .pos, .sell, .pass {{ color: var(--red-dark); font-weight: 700; }}
    .neg, .buy, .fail {{ color: var(--green); font-weight: 700; }}
    .artifact-list {{ margin: 0; padding-left: 18px; columns: 2; }}
    .artifact-list li {{ margin: 6px 0; break-inside: avoid; }}
    .artifact-list span {{ display: inline-block; min-width: 86px; color: var(--muted); }}
    .empty {{ color: var(--muted); margin: 0; }}
    .foot {{ color: var(--muted); font-size: 13px; padding: 18px 0 34px; }}
    @media (max-width: 1120px) {{
      .metric-grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
      .grid-3 {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 760px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .rail {{ display: none; }}
      .shell {{ width: min(100% - 28px, 1380px); }}
      .subnav .shell {{ align-items: flex-start; flex-direction: column; padding: 12px 0; }}
      .metric-grid, .grid-2 {{ grid-template-columns: 1fr; }}
      table {{ min-width: 880px; }}
    }}
  </style>
</head>
<body>
  <div class="topbar">投资科学 · B1 小本金主板策略回测报告</div>
  <div class="layout">
    <aside class="rail" aria-label="报告导航">
      <a href="#overview">概览</a>
      <a href="#plan">方案</a>
      <a href="#nav">净值</a>
      <a href="#dist">分布</a>
      <a href="#trades">明细</a>
    </aside>
    <main class="content">
      <div class="subnav">
        <div class="shell">
          <strong>B1 Trend Pullback · 20k Mainboard</strong>
          <span>生成时间：{_escape(generated_at)}</span>
        </div>
      </div>

      <section class="hero" id="overview">
        <div class="shell">
          <h1>新策略与执行方案：2 万本金主板可买口径</h1>
          <p class="lead">本页描述的是本地研究回测结果，不是实盘买卖指令。策略把 B1 趋势回调逻辑约束到 20,000 元本金、A 股 100 股一手、剔除科创板/创业板/新三板/北交所等权限票，并在候选股买不起时顺延到下一只可买主板票。</p>
          <div class="meta">
            <span class="pill strong">主口径：{_escape(conclusion)}</span>
            <span class="pill">最终净值：{final_nav:.2f}x</span>
            <span class="pill">本金：{_money(INITIAL_CASH, 0)} 元</span>
            <span class="pill">交易成本：{_pct(COST_RATE)}</span>
            <span class="pill">{_escape(fail_note)}</span>
          </div>
        </div>
      </section>

      <section>
        <div class="shell">
          <div class="metric-grid">
            {_metric_grid(data)}
          </div>
        </div>
      </section>

      <section id="plan">
        <div class="shell">
          <h2>策略假设与执行方案</h2>
          <div class="grid-3">
            <article class="panel plan-step">
              <h3>1. 先过滤可交易股票</h3>
              <p class="muted">标的池只保留普通主板股票，剔除 <code>30*</code> 创业板、<code>68*</code> 科创板、北交所和新三板相关代码；同时保留上市时间、流动性和候选质量过滤。</p>
            </article>
            <article class="panel plan-step">
              <h3>2. 只在强势环境开仓</h3>
              <p class="muted">市场门槛要求指数处在 BBI 上方、短中期均线结构偏强，并用候选股广度过滤弱势窗口。弱势环境不新增仓位。</p>
            </article>
            <article class="panel plan-step">
              <h3>3. 用小账户真实约束成交</h3>
              <p class="muted">初始本金固定为 20,000 元；按 100 股一手取整；优先买 Top1，若 Top1 一手都买不起，则顺延到下一只可买候选；止损 5%，止盈 5% 一次性退出，跌破 BBI 退出。</p>
            </article>
          </div>
        </div>
      </section>

      <section>
        <div class="shell">
          <h2>全窗口复核</h2>
          <p class="muted">阶段判断按窗口硬门槛看，不只看全窗口净值曲线。本次回撤门 6/6 通过，收益门 4/6 通过，所以结论是观察。</p>
          {_window_table(data)}
        </div>
      </section>

      <section id="nav">
        <div class="shell">
          <h2>净值曲线</h2>
          <div class="panel">
            {svg_nav_curve(data.nav)}
          </div>
        </div>
      </section>

      <section id="dist">
        <div class="shell">
          <h2>每笔交易盈亏分布</h2>
          <div class="grid-2">
            <div class="panel">
              <h3>每笔交易盈亏金额分布</h3>
              {svg_sorted_pnl_bars(amount_values)}
            </div>
            <div class="panel">
              <h3>每笔交易盈亏比例分布</h3>
              {svg_pct_histogram(pct_values)}
            </div>
          </div>
        </div>
      </section>

      <section id="trades">
        <div class="shell">
          <h2>闭环交易盈亏明细</h2>
          <p class="muted">这里把一次买入及其对应卖出合并成一笔经济交易，盈亏金额已扣双边成本，并保持 20,000 元本金的真实金额口径。</p>
          {_round_trip_table(data)}
        </div>
      </section>

      <section>
        <div class="shell">
          <h2>买卖明细</h2>
          <p class="muted">以下为回测引擎输出的原始买入/卖出流水，成交金额和成交后现金不做展示倍数放大。</p>
          {_raw_trade_table(data)}
        </div>
      </section>

      <section>
        <div class="shell">
          <h2>产物与复现入口</h2>
          <div class="panel">
            {_artifact_list(data)}
          </div>
        </div>
      </section>

      <div class="shell foot">研究边界：本页只用于本地量化研究、复盘和执行方案讨论，不连接券商，不触发真实交易。</div>
    </main>
  </div>
</body>
</html>
"""


def write_report(
    prefix: str = DEFAULT_PREFIX,
    results_dir: Path = RESULTS_DIR,
    output_path: Path = DEFAULT_HTML_PATH,
    generated_at: str | None = None,
) -> Path:
    data = load_report_data(prefix=prefix, results_dir=Path(results_dir))
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_html(data, generated_at=generated_at), encoding="utf-8")
    return output_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build B1 small-capital mainboard HTML report.")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_HTML_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output_path = write_report(prefix=args.prefix, results_dir=args.results_dir, output_path=args.output)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
