# 回测报告与策略证据索引

本目录收拢阶段报告、规则验证和原有策略目录的回测证据。索引用于回答两个问题：

1. `my_quant/strategy_research/strategies/` 里的原策略文件是否有对应回测结果。
2. 哪些结果能被美股操作报告引用，哪些只是 A 股或 ETF 研究证据。

本索引只记录已落盘证据，不把研究结论写成真实交易建议。

## 当前验收判断

- 策略目录总数：`13`
- 已有明确回测/报告证据：`12`
- 部分覆盖：`0`
- 缺历史回测：`1`
- 规则验证报告：`1`

未完全关闭最终验收第 2 项：`12_kronos_forecast_slope` 只有信号函数、单测和数据缺口报告，缺滚动历史预测回测。

## 原策略目录索引

| 策略目录 | 主题 | 证据状态 | 对应结果 | 当前结论 |
| --- | --- | --- | --- | --- |
| `00_baseline_china_permanent` | 中国永久组合基准 | 已有结果 | `my_quant/strategy_research/results/base_strategy_comparison.csv`；`latest_summary.md`；`experiment_manifest.json`；`web_report/index.html` | 基准年化 `5.30%`，最大回撤 `-8.30%`，作为后续策略对照。 |
| `01_universe_diversification` | 资产池扩展与低相关筛选 | 已有结果 | `experiment_manifest.json`；`latest_summary.md`；`base_strategy_comparison.csv` | 五资产池进入基准比较，后续 RAM/风险平价共用该资产池证据。 |
| `02_risk_parity_permanent` | 风险平价版永久组合 | 已有结果 | `base_strategy_comparison.csv`；`latest_summary.md` | v20/v60 风险平价收益偏低，作为观察或淘汰候选，不是当前候选。 |
| `03_ram_topn_switch` | RAM TopN 进攻/防守切换 | 已有结果 | `ram_parameter_scan.csv`；`latest_summary.md`；`latest_summary.json` | 当前最佳轻量候选为 `ram_top2_m20_v120_f21_cost`，年化 `12.13%`、最大回撤 `-10.57%`。 |
| `04_rebalance_cost_control` | 调仓频率与成本控制 | 已有结果 | `ram_parameter_scan.csv`；`latest_summary.md` | 参数扫描包含 `10/21/42/63` 日调仓频率和 `0.1%` 单边成本。 |
| `05_stoploss_trend_filter` | 止损与趋势过滤 | 已有结果 | `docs/research/backtest-reports/stoploss-trend-filter-2026-06-26/index.html`；`summary.csv`；`nav.csv` | 已补止损三档独立报告；5% 止损拖累明显，10% 弱于原 RAM，15% 基本不触发。 |
| `06_parameter_sensitivity` | 参数敏感性 | 已有结果 | `ram_parameter_scan.csv`；`train_parameter_scan.csv`；`latest_summary.md` | 已扫动量、波动、TopN 和调仓频率；Top 10 写在 `latest_summary.md`。 |
| `07_oos_walk_forward` | 样本外与 Walk-Forward | 已有结果 | `train_parameter_scan.csv`；`train_best_oos_result.csv`；`walk_forward_shortlist_summary.csv`；`latest_summary.md` | 训练期最优参数样本外显著衰减，提示路径依赖风险。 |
| `08_factor_diagnostics` | 因子诊断 | 已有结果 | `factor_ic_summary.csv`；`web_report/assets/factor-ic-summary.png` | 已有因子 IC 摘要，可作为信号解释证据。 |
| `09_final_candidate_ram_top2` | 默认最终候选 RAM Top2 | 已有结果 | `latest_summary.md`；`latest_summary.json`；`experiment_manifest.json` | 轻量候选存在，但样本外风险提示仍然有效，不能称为生产策略。 |
| `10_satellite_50pct_dd30` | 小仓卫星策略 | 已有结果 | `satellite_manifest.json`；`satellite_final_candidate.md`；`satellite_*` CSV；外部探针 summary | 没有候选同时通过 `50%` 年化和 `-30%` 回撤门槛；当前是 near-miss 观察。 |
| `11_a_share_b1_trend_pullback` | A 股 B1 趋势回调复刻 | 已有结果 | `b1_tushare_quality_gate_top300_daily_20260617_*`；`b1_small_capital_mainboard_final_20260617_*`；`web_report/b1_quality_strategy_latest.html`；`web_report/b1_small_capital_mainboard_strategy.html` | 本地代理回测通过 `50%` 年化和 `-30%` 回撤门；仍需注意平台规则未完全复刻和现实成交约束。 |
| `12_kronos_forecast_slope` | Kronos 预测斜率信号 | 缺历史回测 | `experiment/kronos_forecast_slope.py`；`tests/test_kronos_forecast_slope.py`；`docs/research/backtest-reports/kronos-forecast-slope-data-gap-2026-06-26.md` | 已有信号函数、单测和数据缺口报告，但未找到历史 Kronos 预测数据，不能生成真实滚动回测。 |

## 核心 run 索引

| run id | 策略/规则 | 窗口 | 关键产物 | 结论 |
| --- | --- | --- | --- | --- |
| `permanent_portfolio_alpha_research` | 00-09 永久组合、RAM、参数敏感性、样本外和因子诊断 | `2021-01-04` 到 `2026-06-15`；训练期 `2017-09-01` 到 `2020-12-31` | `my_quant/strategy_research/results/experiment_manifest.json`；`latest_summary.md`；`base_strategy_comparison.csv`；`ram_parameter_scan.csv`；`factor_ic_summary.csv` | `ram_top2_m20_v120_f21_cost` 是轻量候选；训练期最优参数样本外衰减，结论为观察。 |
| `satellite_50pct_dd30` | 10 小仓卫星策略 | `2021-01-04` 到 `2026-06-15`；训练期 `2017-09-01` 到 `2020-12-31` | `satellite_manifest.json`；`satellite_final_candidate.md`；`satellite_walk_forward.csv` | 未通过 `50%` 年化门，near-miss 观察。 |
| `b1_tushare_quality_gate_top300_daily_20260617` | 11 A 股 B1 趋势回调复刻 | `2025-01-01` 到 `2026-06-17` | `b1_tushare_quality_gate_top300_daily_20260617_manifest.json`；`*_nav.csv`；`*_trades.csv`；`web_report/daily/b1_quality_strategy_20260617.html` | 本地代理回测通过阶段门，但平台规则未完全复刻。 |
| `b1_small_capital_mainboard_final_20260617` | 11 小资金主板最终候选 | `2025-01-01` 到 `2026-06-17` | `b1_small_capital_mainboard_final_20260617_full_manifest.json`；`*_full_nav.csv`；`*_full_trades.csv`；`web_report/b1_small_capital_mainboard_strategy.html` | 本地研究候选，仍需现实成交和滚动股票池复核。 |
| `stoploss-trend-filter-2026-06-26` | 05 止损与趋势过滤 | `2021-01-04` 到 `2026-06-15` | `docs/research/backtest-reports/stoploss-trend-filter-2026-06-26/index.html`；`summary.csv`；`nav.csv` | 已补止损三档；5%/10% 未改善 RAM，15% 未触发。 |
| `no-chase-validation-2026-06-26` | 规则 002 不追高 | `2021-06-28` 到 `2026-05-29` | `docs/research/backtest-reports/no-chase-validation-2026-06-26/index.html`；`summary.json`；`trades.csv` | Phase 3 规则验证阶段通过，可引用 `只等回调`。 |

## 规则验证报告

| 规则 | 报告 | 原始明细 | 可引用标签 | 结论 |
| --- | --- | --- | --- | --- |
| `002-no-chase-after-extended-gap` | `docs/research/backtest-reports/no-chase-validation-2026-06-26/index.html` | `summary.json`；`summary.csv`；`trades.csv` | `只等回调` | Phase 3 规则验证阶段通过；仅作为 A 股大样本纪律证据，不证明美股单票收益。 |

## 仍需补齐

1. 为 `12_kronos_forecast_slope` 补滚动历史预测回测；当前缺历史 Kronos 预测数据，不能伪造回测。
2. 将本索引中的可引用证据接入 `my_quant/us_research/reports/latest_us_operations.html`，让美股关注标的报告能引用策略或规则证据。
