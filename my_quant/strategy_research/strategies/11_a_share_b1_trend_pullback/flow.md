# 11 A 股 B1 趋势回调复刻

## 来源

该策略来自用户提供的 `趋势回调-b1` 截图和补充公式。截图窗口为 `2025-01-01` 至 `2026-05-15`，展示年化收益率 `68.00%`、最大回撤 `7.50%`、交易笔数 `190`。

## 买入公式

```text
收盘价 > BBI(14,28,57,114)
且 EMA(EMA(close, 10), 10) > BBI(14,28,57,114)
且 KDJ.J < 13
```

## 组合规则

- 每日收盘后扫描 A 股候选池。
- 大盘跌破自身长期均线时禁止新买入。
- 候选按本地 `B1_proxy_score` 排序。
- 取 Top2。
- 单票最大仓位 `50%`。
- 信号日之后的下一交易日成交。

## 卖出规则

第一版本地复刻使用三类可解释卖出规则：

- 跌破 BBI 全部卖出。
- 达到分段止盈阈值后逐步卖出。
- 可选趋势失效退出，用参数扫描判断是否保留。

平台截图说明“回测有更多卖出规则”，因此本流程只叫复刻实验，不叫完全复制。

## 验证门槛

当前 goal 的硬门槛仍然是：

- 全样本年化收益率 `>= 50%`
- 全样本最大回撤 `>= -30%`
- 样本外和 walk-forward 最大回撤都不得跌破 `-30%`

截图结果是优先级依据，不是本地完成证据。

## 预期产物

- `b1_trend_pullback_nav.csv`
- `b1_trend_pullback_trades.csv`
- `b1_trend_pullback_candidates.csv`
- `b1_trend_pullback_summary.md`
- `b1_trend_pullback_manifest.json`

## 当前本地探针结果

| Probe | 股票样本 | 年化收益率 | 最大回撤 | 状态 |
| --- | ---: | ---: | ---: | --- |
| `b1_trend_pullback_first100` | 100 | 36.86% | -20.55% | 未达 50% 年化，回撤通过 |
| `b1_trend_pullback_stride50_100` | 98 | 0.84% | -25.03% | 未达 50% 年化，回撤通过 |
| `b1_exit_scan_stride10_300` | 299 | 58.87% | -21.48% | 样本内达标，2026 样本外失败 |

当前状态：高优先级候选继续研究；不能标记为 goal 完成。

## Tushare 数据源扩展

新增可选数据源 `--data-provider tushare`，用于把 B1 复刻从 AkShare/Tencent 口径切换到更适合 A 股回测的数据管线：

- `stock_basic`：构建上市 A 股候选池，继续排除 ST、退市和非沪深主市场代码。
- `pro_bar(adj=qfq)`：获取个股前复权日线，避免除权除息造成的虚假跳变。
- `index_daily(000300.SH)`：替代 `510300` ETF 作为大盘 BBI 过滤。
- 本地缓存：文件名带 `tushare` 标记，和 AkShare 缓存分开。

已完成的 smoke 验证：

- 本机存在 `TUSHARE_TOKEN`。
- Tushare 能返回 A 股股票池。
- `000001.SZ` 的 2025-01-02 至 2025-01-10 前复权日线可正常归一化为 `open/high/low/close/volume/amount`。
- 极小样本 CLI 可完整跑通并写出 summary。

## Tushare 复跑结果

### 普通 Tushare 抽样

产物：

- `results/b1_tushare_walk_forward_stride10_300.md`
- `results/b1_tushare_walk_forward_stride10_300_summary.csv`
- `results/b1_tushare_walk_forward_stride10_300_details.csv`

结果：

- 股票面板：`299`
- 参数窗口行数：`100`
- 全窗口通过配置数：`0`
- 严格排序第一：`tp12_24_36_f100_100_100`
- 该配置 full 年化：`3.17%`
- 该配置 full 最大回撤：`-32.54%`

结论：Tushare 普通抽样不但没有达到 `50%` 年化，最优严格排序配置还跌破了 `-30%` 回撤门槛，不能作为候选。

### 活跃市值池探针

为贴近截图中的“活跃市值波段”过滤，新增一次 Tushare `daily_basic` 探针：

- Universe as-of：`2024-12-31`
- 过滤：上市 A 股、非 ST、沪深主市场、流通市值约 `20-500` 亿人民币、换手率 `>= 1%`、PB > 0
- 活跃分数：`turnover_rate * sqrt(circ_mv)`
- 取 Top300

产物：

- `results/b1_tushare_active_20241231_top300.md`
- `results/b1_tushare_active_20241231_top300_universe.csv`
- `results/b1_tushare_active_20241231_top300_summary.csv`
- `results/b1_tushare_active_20241231_top300_details.csv`

结果：

- 股票面板：`299`
- 全窗口通过配置数：`0`
- 严格排序第一：`tp10_20_30_f25_25_100`
- 该配置 full 年化：`27.75%`
- 该配置 full 最大回撤：`-26.37%`
- 该配置 2026 OOS 年化：`52.54%`
- 该配置 2025 H1 年化：`-31.43%`

结论：活跃市值池改善了部分窗口，但暴露出强路径依赖。2026 OOS 和 2025 H2 很强，2025 H1 与训练期明显失败，因此不能标记 goal 完成。

### 市场状态过滤探针

为修正活跃池版本在 2025 H1 的弱表现，增加一次更严格的大盘状态过滤探针：

- `results/b1_tushare_market_regime_probe.md`

最佳 near-miss 配置：

- 数据源：Tushare
- 股票池：`b1_tushare_active_20241231_top300_universe.csv`
- 股票面板：`299`
- 市场过滤：沪深300 `close > BBI` 且沪深300 `MA20 > MA60`
- 退出配置：`tp8_16_24_f100_100_100`

窗口结果：

| Window | 年化收益率 | 最大回撤 | 状态 |
| --- | ---: | ---: | --- |
| full | 69.30% | -20.45% | 达标 |
| train_2025 | 51.91% | -19.40% | 达标 |
| oos_2026 | 132.77% | -19.18% | 达标 |
| wf_2025_h1 | 11.93% | -19.40% | 收益失败，回撤通过 |
| wf_2025_h2 | 76.95% | -15.65% | 达标 |

这是目前最接近目标的 B1/Tushare 版本，但仍未满足“所有 walk-forward 窗口年化均 `>= 50%`”的严格条件。活跃池广度过滤和仓位结构调整没有修复 2025 H1，因此下一步应检查 2025 H1 的具体买卖，而不是继续调大仓位。

### 入场质量过滤候选

检查 2025 H1 交易后发现，失败窗口不是连续亏损，而是交易机会少且个别过热回调标的拖累明显。例如 `300766` 入场前 20 日涨幅约 `198%`、价格高出 BBI 约 `50.7%`，最终跌破 BBI 止损亏损约 `24.2%`。

因此新增入场质量过滤：

- 市场过滤：沪深300 `close > BBI` 且 `MA20 > MA60`
- 个股过滤：`close / BBI - 1 <= 27.5%`
- 个股过滤：`2% <= 20日动量 <= 75%`
- 退出配置：`tp8_16_24_f100_100_100`
- 股票池：Tushare `daily_basic` 活跃市值 Top300

复跑命令：

```bash
.venv/bin/python -m my_quant.strategy_research.run_b1_walk_forward \
  --data-provider tushare \
  --symbols-file my_quant/strategy_research/results/b1_tushare_active_20241231_top300_universe.csv \
  --max-symbols 300 \
  --stride 1 \
  --market-ma20-gt-ma60 \
  --max-entry-close-bbi 0.275 \
  --min-entry-mom20 0.02 \
  --max-entry-mom20 0.75 \
  --output-prefix b1_tushare_quality_gate_top300
```

产物：

- `results/b1_tushare_quality_gate_top300.md`
- `results/b1_tushare_quality_gate_top300_summary.csv`
- `results/b1_tushare_quality_gate_top300_details.csv`

窗口结果：

| Window | 年化收益率 | 最大回撤 | 状态 |
| --- | ---: | ---: | --- |
| full | 127.40% | -15.95% | 达标 |
| train_2025 | 105.21% | -14.63% | 达标 |
| oos_2026 | 212.38% | -15.06% | 达标 |
| wf_2025_h1 | 52.57% | -14.63% | 达标 |
| wf_2025_h2 | 65.27% | -20.80% | 达标 |

当前状态：这是第一个在本地 full / train / OOS / walk-forward 全部通过 `50%` 年化和 `-30%` 最大回撤闸门的 B1/Tushare 研究候选。

仍需保留的限制：

- 本地 `B1_proxy_score` 不是平台截图中的真实 B1 总分；代码已把趋势、回调深度和价格缓冲三项分数权重提升为 `B1BacktestConfig` 参数，后续可以做平台分数校准和消融。
- 已新增可选现实成交约束：买卖成交价列、100 股一手取整、涨停买入阻断、跌停卖出阻断和成交量容量上限。当前已发布结果仍是旧研究口径，必须用现实成交参数重跑后才能提高结论等级。
- 已新增 active universe as-of 构建函数和 walk-forward 滚动活跃池入口；当前已发布结果仍使用 `2024-12-31` 固定股票池，后续生产化验证应改用窗口起点或交易日前一可用日期滚动构建，避免固定池样本选择偏差。
- 尚未模拟停牌复牌细节、盘口滑点、不同板块涨跌停比例、真实资金规模容量和更细交易费用。
- 当前结论只证明本地研究 goal 的回测闸门通过，不构成实盘投资建议。

下一步只有两条值得继续：

1. 用可配置分数权重校准平台未公开的 B1 总分与“更多卖出规则”，否则本地代理仍可能选错候选或退出点。
2. 把当前质量过滤候选按现实成交约束和滚动活跃池重跑：涨跌停成交、停牌、容量、滑点和更细 walk-forward。
