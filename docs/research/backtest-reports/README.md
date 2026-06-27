# 回测报告与策略证据索引

本目录保留策略报告、规则验证和历史负证据。2026-06-27 之后旧策略全部退出当前主线，生命周期统一为 `legacy_reset`；旧回测结果不删除，用于复盘、对照和解释为什么不继续推进。

## 历史保留资产

- 当前主线策略资产：`0`
- 历史保留策略资产：`3`
- `my_quant/strategy_research/strategies/` 历史策略目录：`2`
- `docs/research/` 历史 Risk8 规格：`1`
- 历史规则验证报告：`1`
- 历史负证据和对照结果：保留

| 策略资产 | 位置 | 主口径窗口 | 年化收益 | 最大回撤 | Sharpe | Beta | Calmar | 当前结论 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| A 股 B1 趋势回调复刻 | `my_quant/strategy_research/strategies/11_a_share_b1_trend_pullback/` | `2025-01-01` 到 `2026-05-15`，Tushare active Top300 质量过滤研究口径 | `127.40%` | `-15.95%` | `2.96` | `0.74` vs 沪深 300 | `7.99` | `legacy_reset`，仅作历史证据。 |
| 小仓卫星策略 | `my_quant/strategy_research/strategies/10_satellite_50pct_dd30/` | `2021-01-04` 到 `2026-06-15`，`fixed_513100_518880_50_50_x2_0` | `28.16%` | `-26.68%` | `1.13` | `0.84` vs `513100` | `1.06` | `legacy_reset`，仅作历史证据。 |
| 横截面择强 Risk8 | `docs/research/executable-strategy-cross-section-risk8.md` | `2023-05-30` 到 `2026-05-30`，`105` 三年全窗口 | `12.89%` | `-6.72%` | `1.66` | 待补同窗口基准序列 | `1.92` | `legacy_reset`，仅作历史证据。 |

Beta 口径：B1 使用沪深 300 指数日收益作为基准；小仓卫星使用 `513100` 日收益作为高 beta 权益基准；Risk8 当前结果文件缺同窗口基准序列，不能伪造 Beta。

## 保留证据

| run id / 报告 | 关联策略/规则 | 窗口 | 关键产物 | 结论 |
| --- | --- | --- | --- | --- |
| `b1_tushare_quality_gate_top300` | A 股 B1 趋势回调复刻 | `2025-01-01` 到 `2026-05-15` | `my_quant/strategy_research/results/b1_tushare_quality_gate_top300_*` | 本地代理研究口径通过 `50%` 年化和 `-30%` 回撤门。 |
| `b1_tushare_quality_gate_top300_realistic_lot_limit_20260617` | A 股 B1 现实成交复核 | `2025-01-01` 到 `2026-06-17` | `my_quant/strategy_research/results/b1_tushare_quality_gate_top300_realistic_lot_limit_20260617_*` | 年化 `48.90%`，低于 `50%` 门槛；仍为观察。 |
| `b1_small_capital_mainboard_final_20260617` | A 股 B1 小本金主板候选 | `2025-01-01` 到 `2026-06-17` | `my_quant/strategy_research/results/b1_small_capital_mainboard_final_20260617_*` | 本地研究候选；仍需现实成交和滚动股票池复核。 |
| `satellite_50pct_dd30` | 小仓卫星策略 | `2021-01-04` 到 `2026-06-15` | `my_quant/strategy_research/results/satellite_manifest.json`；`satellite_final_candidate.md`；`satellite_*` CSV | 未通过 `50%` 年化门，near-miss 观察。 |
| `105-portfolio-cross-section-risk8-pos135-entry-gap6-netreturn-limitdelay-slip10bp-gap` | 横截面择强 Risk8 | `2023-05-30` 到 `2026-05-30` | `docs/research/runs/105-portfolio-cross-section-risk8-pos135-entry-gap6-netreturn-limitdelay-slip10bp-gap/` | 全窗口可看，但年化低于新目标。 |
| `110-portfolio-cross-section-risk8-pos135-entry-gap6-netreturn-window-riskreason-validation` | 横截面择强 Risk8 滚动窗口诊断 | 多滚动窗口 | `docs/research/runs/110-portfolio-cross-section-risk8-pos135-entry-gap6-netreturn-window-riskreason-validation/` | 仅 `3/7` 通过，失败集中在 `Y1/Y2/R18-1/R18-2`。 |
| `no-chase-validation-2026-06-26` | 规则 002 不追高 | `2021-06-28` 到 `2026-05-29` | `docs/research/backtest-reports/no-chase-validation-2026-06-26/index.html`；`summary.json`；`trades.csv` | Phase 3 规则验证阶段通过；它是纪律规则，不计入当前策略数。 |

## 历史策略目录移除说明

以下旧目录已按当前排名清理，不再作为策略资产保留：

- `00_baseline_china_permanent`
- `01_universe_diversification`
- `02_risk_parity_permanent`
- `03_ram_topn_switch`
- `04_rebalance_cost_control`
- `05_stoploss_trend_filter`
- `06_parameter_sensitivity`
- `07_oos_walk_forward`
- `08_factor_diagnostics`
- `09_final_candidate_ram_top2`
- `12_kronos_forecast_slope`

这些路线对应的 CSV、HTML 和 JSON 证据仍留在 `results/` 或本目录下。保留它们是为了避免丢失负证据和历史验收上下文，不代表它们仍是当前策略。
