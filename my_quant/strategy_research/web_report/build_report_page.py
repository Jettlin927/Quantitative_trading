from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"
REPORT_DIR = ROOT / "web_report"
ASSETS_DIR = REPORT_DIR / "assets"
INDEX_PATH = REPORT_DIR / "index.html"


plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "STHeiti", "Heiti TC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def ensure_dirs() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def save_base_chart(base_df: pd.DataFrame) -> None:
    chart_df = base_df.copy()
    chart_df["label"] = chart_df["strategy"].map(
        {
            "baseline_china_permanent_25_annual_no_cost": "中国永久组合",
            "equal_weight_5_assets_monthly_cost": "五资产等权",
            "risk_parity_5_assets_v20_monthly_cost": "风险平价 v20",
            "risk_parity_5_assets_v60_monthly_cost": "风险平价 v60",
            "ram_top1_m60_v60_monthly_cost": "RAM Top1 60/60",
            "ram_top2_m60_v60_monthly_cost": "RAM Top2 60/60",
            "ram_top3_m60_v60_monthly_cost": "RAM Top3 60/60",
            "ram_top2_m60_v60_monthly_trend_filter_cost": "RAM Top2 + 趋势过滤",
        }
    ).fillna(chart_df["strategy"])
    chart_df = chart_df.sort_values("annual_return", ascending=True)

    fig, ax = plt.subplots(figsize=(10, 5.6))
    colors = ["#13a88b" if "永久" in label else "#456990" for label in chart_df["label"]]
    ax.barh(chart_df["label"], chart_df["annual_return"] * 100, color=colors, height=0.62)
    ax.axvline(5.25, color="#d1495b", linestyle="--", linewidth=1.4, label="正式门槛 5.25%")
    ax.set_title("基础策略年化收益对比", fontsize=15, weight="bold", pad=16)
    ax.set_xlabel("年化收益率 (%)")
    ax.grid(axis="x", color="#d8dee9", linewidth=0.8)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.legend(loc="lower right", frameon=False)
    for i, value in enumerate(chart_df["annual_return"] * 100):
        ax.text(value + (0.35 if value >= 0 else -0.35), i, f"{value:.1f}%", va="center", ha="left" if value >= 0 else "right", fontsize=9)
    fig.tight_layout()
    fig.savefig(ASSETS_DIR / "base-strategy-return.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def save_top_scan_chart(scan_df: pd.DataFrame) -> None:
    top = scan_df.sort_values(["calmar", "annual_return"], ascending=False).head(10).copy()
    top = top.sort_values("annual_return", ascending=True)
    fig, ax = plt.subplots(figsize=(10, 5.8))
    colors = ["#efb036" if strategy == "ram_top2_m20_v120_f21_cost" else "#5166b3" for strategy in top["strategy"]]
    ax.barh(top["strategy"], top["annual_return"] * 100, color=colors, height=0.58)
    ax.set_title("RAM 参数扫描 Top 10：收益排序", fontsize=15, weight="bold", pad=16)
    ax.set_xlabel("年化收益率 (%)")
    ax.grid(axis="x", color="#d8dee9", linewidth=0.8)
    ax.spines[["top", "right", "left"]].set_visible(False)
    for i, (_, row) in enumerate(top.iterrows()):
        ax.text(row["annual_return"] * 100 + 0.35, i, f"Calmar {row['calmar']:.2f}", va="center", fontsize=8.5, color="#22303c")
    fig.tight_layout()
    fig.savefig(ASSETS_DIR / "ram-top10-scan.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def save_walk_forward_chart(wf_df: pd.DataFrame) -> None:
    summary = wf_df.groupby("mode")[["oos_annual_return", "oos_max_drawdown", "oos_calmar"]].mean()
    labels = ["Rolling", "Anchored"]
    annual = [summary.loc["rolling", "oos_annual_return"] * 100, summary.loc["anchored", "oos_annual_return"] * 100]
    drawdown = [summary.loc["rolling", "oos_max_drawdown"] * 100, summary.loc["anchored", "oos_max_drawdown"] * 100]

    fig, ax = plt.subplots(figsize=(8.4, 4.8))
    x = range(len(labels))
    ax.bar([v - 0.18 for v in x], annual, width=0.34, color="#13a88b", label="OOS 年化")
    ax.bar([v + 0.18 for v in x], drawdown, width=0.34, color="#d1495b", label="OOS 最大回撤")
    ax.axhline(0, color="#1f2937", linewidth=0.8)
    ax.set_xticks(list(x), labels)
    ax.set_ylabel("%")
    ax.set_title("Walk-Forward shortlist：样本外表现", fontsize=15, weight="bold", pad=16)
    ax.grid(axis="y", color="#d8dee9", linewidth=0.8)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.legend(frameon=False, loc="upper left")
    for i, value in enumerate(annual):
        ax.text(i - 0.18, value + 0.8, f"{value:.1f}%", ha="center", fontsize=9)
    for i, value in enumerate(drawdown):
        ax.text(i + 0.18, value - 1.6, f"{value:.1f}%", ha="center", fontsize=9)
    fig.tight_layout()
    fig.savefig(ASSETS_DIR / "walk-forward-summary.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def save_factor_chart(factor_df: pd.DataFrame) -> None:
    all_regime = factor_df[factor_df["regime"] == "all"].copy()
    all_regime["label"] = all_regime["factor"].map(
        {
            "momentum": "Momentum",
            "ram": "RAM",
            "trend_strength": "Trend Strength",
            "low_volatility": "Low Volatility",
        }
    )
    all_regime = all_regime.sort_values("mean_ic")
    fig, ax = plt.subplots(figsize=(8.4, 4.8))
    colors = ["#d1495b" if value < 0 else "#13a88b" for value in all_regime["mean_ic"]]
    ax.barh(all_regime["label"], all_regime["mean_ic"], color=colors, height=0.56)
    ax.axvline(0, color="#1f2937", linewidth=0.8)
    ax.set_title("因子 IC：全市场状态", fontsize=15, weight="bold", pad=16)
    ax.set_xlabel("平均 IC")
    ax.grid(axis="x", color="#d8dee9", linewidth=0.8)
    ax.spines[["top", "right", "left"]].set_visible(False)
    for i, (_, row) in enumerate(all_regime.iterrows()):
        ax.text(row["mean_ic"] + (0.006 if row["mean_ic"] >= 0 else -0.006), i, f"{row['mean_ic']:.3f}", va="center", ha="left" if row["mean_ic"] >= 0 else "right", fontsize=9)
    fig.tight_layout()
    fig.savefig(ASSETS_DIR / "factor-ic-summary.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def build_html(base_df: pd.DataFrame, scan_df: pd.DataFrame, wf_df: pd.DataFrame, factor_df: pd.DataFrame, manifest: dict) -> str:
    baseline = base_df.loc[base_df["strategy"] == "baseline_china_permanent_25_annual_no_cost"].iloc[0]
    best = scan_df.loc[scan_df["strategy"] == manifest["best_research_candidate"]].iloc[0]
    train_oos = pd.read_csv(RESULTS_DIR / "train_best_oos_result.csv").iloc[0]
    wf_summary = wf_df.groupby("mode")[["oos_annual_return", "oos_max_drawdown", "oos_calmar"]].mean()
    factor_all = factor_df[factor_df["regime"] == "all"].set_index("factor")
    artifact_items = "\n".join(f"<li><code>{name}</code></li>" for name in manifest["artifacts"])

    top_rows = []
    for _, row in scan_df.sort_values(["calmar", "annual_return"], ascending=False).head(6).iterrows():
        top_rows.append(
            f"<tr><td><code>{row['strategy']}</code></td><td>{pct(row['annual_return'])}</td><td>{pct(row['max_drawdown'])}</td><td>{row['calmar']:.2f}</td></tr>"
        )

    factor_rows = []
    for key, label in [("momentum", "Momentum"), ("ram", "RAM"), ("trend_strength", "趋势强度"), ("low_volatility", "低波动")]:
        row = factor_all.loc[key]
        factor_rows.append(
            f"<tr><td>{label}</td><td>{row['mean_ic']:.4f}</td><td>{row['ic_win_rate'] * 100:.2f}%</td><td>{int(row['observations'])}</td></tr>"
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>XQuant 策略实验工程汇报</title>
  <style>
    :root {{
      --ink: #17202a;
      --muted: #5d6b78;
      --line: #d8dee9;
      --paper: #f6f8fb;
      --surface: #ffffff;
      --teal: #13a88b;
      --blue: #456990;
      --violet: #5166b3;
      --amber: #efb036;
      --red: #d1495b;
      --shadow: 0 18px 48px rgba(23, 32, 42, 0.10);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      color: var(--ink);
      background: var(--paper);
      line-height: 1.62;
    }}
    a {{ color: inherit; }}
    .shell {{ width: min(1180px, calc(100% - 40px)); margin: 0 auto; }}
    header {{
      background: #ffffff;
      border-bottom: 1px solid var(--line);
      position: sticky;
      top: 0;
      z-index: 10;
    }}
    .nav {{
      min-height: 68px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
    }}
    .brand {{ font-weight: 800; letter-spacing: 0; }}
    .nav-links {{ display: flex; gap: 18px; color: var(--muted); font-size: 14px; flex-wrap: wrap; }}
    .hero {{ padding: 58px 0 42px; background: linear-gradient(135deg, #ffffff 0%, #eef7f4 45%, #f2f4fb 100%); border-bottom: 1px solid var(--line); }}
    .hero-grid {{ display: grid; grid-template-columns: 1.05fr 0.95fr; gap: 34px; align-items: center; }}
    h1 {{ font-size: clamp(34px, 5vw, 62px); line-height: 1.05; margin: 0 0 22px; letter-spacing: 0; }}
    .lead {{ font-size: 18px; color: var(--muted); max-width: 760px; margin: 0; }}
    .hero-panel {{ background: rgba(255,255,255,.86); border: 1px solid rgba(216,222,233,.9); box-shadow: var(--shadow); border-radius: 8px; padding: 22px; }}
    .metric-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    .metric {{ border-left: 4px solid var(--teal); padding: 10px 12px; background: #fff; border-radius: 6px; }}
    .metric:nth-child(2) {{ border-left-color: var(--amber); }}
    .metric:nth-child(3) {{ border-left-color: var(--red); }}
    .metric:nth-child(4) {{ border-left-color: var(--violet); }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 2px; font-size: 24px; line-height: 1.15; overflow-wrap: anywhere; }}
    .metric:first-child strong {{ font-size: 17px; line-height: 1.28; }}
    section {{ padding: 56px 0; }}
    .section-head {{ display: flex; align-items: end; justify-content: space-between; gap: 24px; margin-bottom: 24px; }}
    h2 {{ font-size: clamp(26px, 3.4vw, 38px); line-height: 1.16; margin: 0; letter-spacing: 0; }}
    .section-copy {{ color: var(--muted); max-width: 720px; margin: 10px 0 0; }}
    .band-white {{ background: #fff; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }}
    .two-col {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 22px; align-items: stretch; }}
    .two-col > *, .chart-grid > * {{ min-width: 0; }}
    .panel {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 22px; box-shadow: 0 8px 28px rgba(23,32,42,.06); }}
    .panel h3 {{ margin: 0 0 10px; font-size: 20px; }}
    .panel p {{ margin: 0; color: var(--muted); }}
    .timeline {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }}
    .timeline-item {{ position: relative; padding: 22px; background: #fff; border: 1px solid var(--line); border-radius: 8px; }}
    .timeline-item::before {{ content: ""; display: block; width: 42px; height: 4px; background: var(--teal); border-radius: 999px; margin-bottom: 18px; }}
    .timeline-item.next::before {{ background: var(--violet); }}
    .chart-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
    figure {{ margin: 0; background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 14px; box-shadow: 0 8px 28px rgba(23,32,42,.06); }}
    figure img {{ width: 100%; display: block; border-radius: 5px; }}
    figcaption {{ color: var(--muted); font-size: 13px; margin: 12px 4px 2px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 12px 14px; text-align: left; border-bottom: 1px solid var(--line); vertical-align: top; }}
    th {{ font-size: 13px; color: var(--muted); background: #f6f8fb; }}
    tr:last-child td {{ border-bottom: none; }}
    code {{ font-family: "SFMono-Regular", Menlo, Consolas, monospace; font-size: .92em; background: #eef2f7; padding: 2px 5px; border-radius: 4px; overflow-wrap: anywhere; }}
    .callout {{ border-left: 5px solid var(--amber); background: #fffaf0; padding: 18px 20px; border-radius: 8px; color: #4b3b12; }}
    .artifact-list {{ columns: 2; padding-left: 20px; margin: 0; }}
    .footer {{ padding: 32px 0 48px; color: var(--muted); font-size: 14px; }}
    @media (max-width: 880px) {{
      .hero-grid, .two-col, .timeline, .chart-grid {{ grid-template-columns: 1fr; }}
      .nav {{ align-items: flex-start; flex-direction: column; padding: 16px 0; }}
      .metric-grid {{ grid-template-columns: 1fr; }}
      .artifact-list {{ columns: 1; }}
      section {{ padding: 42px 0; }}
      .shell {{ width: min(100% - 28px, 1180px); }}
      table {{ display: block; max-width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; }}
      th, td {{ min-width: 88px; }}
      th:first-child, td:first-child {{ min-width: 220px; }}
    }}
  </style>
</head>
<body>
  <header>
    <nav class="shell nav">
      <div class="brand">XQuant 策略实验工程汇报</div>
      <div class="nav-links">
        <a href="#work">工作内容</a>
        <a href="#results">实验结果</a>
        <a href="#validation">验证结论</a>
        <a href="#artifacts">产物清单</a>
      </div>
    </nav>
  </header>

  <main>
    <section class="hero">
      <div class="shell hero-grid">
        <div>
          <h1>从 TODO 到可复跑策略实验工程</h1>
          <p class="lead">这页汇总上次和本次工作：先把 `my_quant/TODO.md` 拆成可对比的研究档案，再升级为带数据缓存、回测引擎、参数扫描、Walk-Forward、因子 IC 和测试的完整实验管线。</p>
        </div>
        <aside class="hero-panel" aria-label="核心实验指标">
          <div class="metric-grid">
            <div class="metric"><span>当前研究候选</span><strong>{manifest["best_research_candidate"]}</strong></div>
            <div class="metric"><span>候选年化收益</span><strong>{pct(best["annual_return"])}</strong></div>
            <div class="metric"><span>候选最大回撤</span><strong>{pct(best["max_drawdown"])}</strong></div>
            <div class="metric"><span>RAM 参数扫描</span><strong>{manifest["ram_scan_rows"]} 组</strong></div>
          </div>
        </aside>
      </div>
    </section>

    <section id="work">
      <div class="shell">
        <div class="section-head">
          <div>
            <h2>两次工作分别做了什么</h2>
            <p class="section-copy">上次解决“研究设计如何落地”，本次解决“如何变成可复跑工程”。</p>
          </div>
        </div>
        <div class="timeline">
          <article class="timeline-item">
            <h3>上次：策略研究档案</h3>
            <p>建立 `strategy_research/`，为 TODO 中的 10 条路线分别创建 `flow.md`，补齐统一评估框架、候选说明和后续 goal。核心产出是“每条策略怎么跑、和什么比、什么条件通过、什么条件淘汰”。</p>
          </article>
          <article class="timeline-item next">
            <h3>本次：完整实验工程</h3>
            <p>把单脚本拆成 `experiment/` 工程模块，加入标准库测试、主入口、兼容入口、重型 Walk-Forward 入口、manifest、shortlist Walk-Forward 和因子 IC 诊断。核心产出是“任何人能在本地复跑同一套结果”。</p>
          </article>
        </div>
      </div>
    </section>

    <section class="band-white" id="results">
      <div class="shell">
        <div class="section-head">
          <div>
            <h2>产出结果怎么样</h2>
            <p class="section-copy">当前最优研究候选是 <code>{manifest["best_research_candidate"]}</code>。它跑赢脚本重算的永久组合，但仍然不是最终稳定策略。</p>
          </div>
        </div>
        <div class="two-col">
          <div class="panel">
            <h3>和永久组合的差异</h3>
            <p>脚本重算永久组合年化为 <strong>{pct(baseline["annual_return"])}</strong>，最大回撤 <strong>{pct(baseline["max_drawdown"])}</strong>。候选策略年化 <strong>{pct(best["annual_return"])}</strong>，超额年化 <strong>{pct(best["annual_return"] - baseline["annual_return"])}</strong>，最大回撤 <strong>{pct(best["max_drawdown"])}</strong>。</p>
          </div>
          <div class="panel">
            <h3>最重要的风险提示</h3>
            <p>训练期最优参数 <code>{train_oos["strategy"]}</code> 样本外年化只有 <strong>{pct(train_oos["annual_return"])}</strong>，最大回撤 <strong>{pct(train_oos["max_drawdown"])}</strong>。这说明单次回测排名不能直接等于最终最优。</p>
          </div>
        </div>
      </div>
    </section>

    <section>
      <div class="shell chart-grid">
        <figure>
          <img src="assets/base-strategy-return.png" alt="基础策略年化收益对比图" />
          <figcaption>基础策略里，固定 60/60 RAM Top2 表现很差，不能照搬 TODO 默认参数。</figcaption>
        </figure>
        <figure>
          <img src="assets/ram-top10-scan.png" alt="RAM 参数扫描 Top 10 图" />
          <figcaption>当前候选来自参数扫描：Top2、20 日动量、120 日波动、21 日调仓。</figcaption>
        </figure>
      </div>
    </section>

    <section class="band-white" id="validation">
      <div class="shell">
        <div class="section-head">
          <div>
            <h2>稳定性与因子验证</h2>
            <p class="section-copy">这部分回答“是不是只有某个历史区间好看”。结果是：方向有价值，但参数稳定性还要继续验证。</p>
          </div>
        </div>
        <div class="chart-grid">
          <figure>
            <img src="assets/walk-forward-summary.png" alt="Walk Forward 样本外表现图" />
            <figcaption>Shortlist Walk-Forward 的 OOS 平均表现仍高于永久组合，但窗口间会切换不同参数。</figcaption>
          </figure>
          <figure>
            <img src="assets/factor-ic-summary.png" alt="因子 IC 诊断图" />
            <figcaption>Momentum、RAM、趋势强度为弱正 IC；低波动在当前口径下是负 IC。</figcaption>
          </figure>
        </div>
        <div style="height:24px"></div>
        <div class="two-col">
          <div>
            <table>
              <thead><tr><th>Walk-Forward 模式</th><th>OOS 年化</th><th>OOS 最大回撤</th><th>OOS 卡玛</th></tr></thead>
              <tbody>
                <tr><td>Rolling</td><td>{pct(wf_summary.loc["rolling", "oos_annual_return"])}</td><td>{pct(wf_summary.loc["rolling", "oos_max_drawdown"])}</td><td>{wf_summary.loc["rolling", "oos_calmar"]:.2f}</td></tr>
                <tr><td>Anchored</td><td>{pct(wf_summary.loc["anchored", "oos_annual_return"])}</td><td>{pct(wf_summary.loc["anchored", "oos_max_drawdown"])}</td><td>{wf_summary.loc["anchored", "oos_calmar"]:.2f}</td></tr>
              </tbody>
            </table>
          </div>
          <div>
            <table>
              <thead><tr><th>因子</th><th>平均 IC</th><th>IC 胜率</th><th>观测数</th></tr></thead>
              <tbody>{''.join(factor_rows)}</tbody>
            </table>
          </div>
        </div>
      </div>
    </section>

    <section>
      <div class="shell">
        <div class="section-head">
          <div>
            <h2>参数扫描 Top 6</h2>
            <p class="section-copy">排序优先看卡玛比，再看年化收益。Top 1 是当前候选，但不是唯一有研究价值的参数。</p>
          </div>
        </div>
        <table>
          <thead><tr><th>策略</th><th>年化收益</th><th>最大回撤</th><th>卡玛比</th></tr></thead>
          <tbody>{''.join(top_rows)}</tbody>
        </table>
      </div>
    </section>

    <section class="band-white" id="artifacts">
      <div class="shell">
        <div class="section-head">
          <div>
            <h2>最终产物清单</h2>
            <p class="section-copy">这些文件组成了当前策略实验工程。页面数据来自 `results/`，不是手写猜测。</p>
          </div>
        </div>
        <div class="two-col">
          <div class="panel">
            <h3>工程模块</h3>
            <p><code>experiment/config.py</code>、<code>data.py</code>、<code>strategies.py</code>、<code>backtest.py</code>、<code>metrics.py</code>、<code>validation.py</code>、<code>factor_diagnostics.py</code>、<code>reports.py</code>、<code>pipeline.py</code>。</p>
          </div>
          <div class="panel">
            <h3>验证命令</h3>
            <p><code>.venv/bin/python -m unittest discover my_quant/strategy_research/tests -v</code><br><code>.venv/bin/python my_quant/strategy_research/run_full_experiment.py</code></p>
          </div>
        </div>
        <div style="height:24px"></div>
        <div class="panel">
          <h3>结果文件</h3>
          <ul class="artifact-list">{artifact_items}</ul>
        </div>
        <div style="height:24px"></div>
        <div class="callout">结论纪律：当前最优研究候选是 <code>{manifest["best_research_candidate"]}</code>，不是最终可执行投资建议。下一步要把这套工程迁移/复刻到课程 notebook，并继续做全量 Walk-Forward 和更严格的样本外验证。</div>
      </div>
    </section>
  </main>

  <footer class="shell footer">
    生成时间基于本地结果文件；数据最新日期：{manifest["latest_price_date"]}。历史收益不代表未来表现，所有内容只用于课程研究与回测学习。
  </footer>
</body>
</html>
"""


def main() -> None:
    ensure_dirs()
    base_df = pd.read_csv(RESULTS_DIR / "base_strategy_comparison.csv")
    scan_df = pd.read_csv(RESULTS_DIR / "ram_parameter_scan.csv")
    wf_df = pd.read_csv(RESULTS_DIR / "walk_forward_shortlist_summary.csv")
    factor_df = pd.read_csv(RESULTS_DIR / "factor_ic_summary.csv")
    manifest = json.loads((RESULTS_DIR / "experiment_manifest.json").read_text(encoding="utf-8"))

    save_base_chart(base_df)
    save_top_scan_chart(scan_df)
    save_walk_forward_chart(wf_df)
    save_factor_chart(factor_df)
    INDEX_PATH.write_text(build_html(base_df, scan_df, wf_df, factor_df, manifest), encoding="utf-8")
    print(INDEX_PATH)


if __name__ == "__main__":
    main()
