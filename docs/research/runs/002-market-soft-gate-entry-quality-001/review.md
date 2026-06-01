# 002-market-soft-gate-entry-quality-001 软门控交易质量诊断

- 基准 run：`002-repair-indicator-pulse-moneyflow-surge-002`，完成交易 `123`。
- 候选 run：`002-repair-market-breadth-soft-gate-001`，完成交易 `142`。
- 共同交易 `84`；基准独有 `39`；候选独有 `58`。

## 替换关系

| 关系 | 交易数 | 净盈亏 | 胜率 | 均值收益 | 中位收益 | 盈亏比 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `baseline_only` | `39` | `37189.76` | 56.41% | 5.07% | 4.62% | 2.42:1 |
| `candidate_only` | `58` | `4384.23` | 31.03% | 0.41% | -5.28% | 2.47:1 |
| `common` | `84` | `41976.05` | 45.24% | 3.50% | -5.28% | 2.65:1 |

## 入场市场状态

| 关系 | 入场状态 | 交易数 | 净盈亏 | 胜率 | 均值收益 | 盈亏比 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `baseline_only` | `base_risk_on` | `39` | `37189.76` | 56.41% | 5.07% | 2.42:1 |
| `candidate_only` | `base_risk_on` | `32` | `7720.16` | 34.38% | 1.20% | 2.58:1 |
| `candidate_only` | `soft_risk_on` | `26` | `-3335.94` | 26.92% | -0.56% | 2.32:1 |
| `common` | `base_risk_on` | `84` | `41976.05` | 45.24% | 3.50% | 2.65:1 |

## 候选独有窗口拆分

| 窗口 | 全部数/净盈亏 | 软Risk-On数/净盈亏/胜率 | 基础Risk-On数/净盈亏/胜率 |
| --- | ---: | ---: | ---: |
| `ALL` | `58` / `4384.23` | `26` / `-3335.94` / 26.92% | `32` / `7720.16` / 34.38% |
| `Y1` | `9` / `-4983.19` | `4` / `-1530.84` / 25.00% | `5` / `-3452.35` / 0.00% |
| `Y2` | `13` / `1258.51` | `9` / `1851.14` / 33.33% | `4` / `-592.63` / 25.00% |
| `Y3` | `36` / `8108.90` | `13` / `-3656.24` / 23.08% | `23` / `11765.14` / 43.48% |
| `R18-1` | `13` / `-4355.36` | `8` / `-903.01` / 25.00% | `5` / `-3452.35` / 0.00% |
| `R18-2` | `17` / `-1595.93` | `9` / `1851.14` / 33.33% | `8` / `-3447.07` / 12.50% |
| `R18-3` | `30` / `5041.82` | `12` / `2294.03` / 33.33% | `18` / `2747.78` / 33.33% |
| `R18-4` | `45` / `8739.59` | `18` / `-2432.92` / 27.78% | `27` / `11172.52` / 40.74% |

## 画像差异

### candidate_only_soft
- scoreParts：high60Rank=90.10%；return20Rank=88.33%；rsiBalanceRank=29.02%；macdHistDeltaRank=80.47%；bollSqueezeRank=19.95%；amountEfficiency20Rank=70.92%；moneyflowMarketSurgeQualityRank=5.36%；industryReturn20Rank=65.08%
- entryRiskMetrics：entryRangePct=6.50%；gapPct=1.80%；priorGapDown60Pct=3.42%
- marketMetrics：marketAboveMa20Pct=53.52%；marketAboveMa60Pct=60.90%；marketUpPct=55.98%

### candidate_only_base
- scoreParts：high60Rank=91.25%；return20Rank=88.15%；rsiBalanceRank=23.45%；macdHistDeltaRank=76.55%；bollSqueezeRank=15.22%；amountEfficiency20Rank=75.66%；moneyflowMarketSurgeQualityRank=14.86%；industryReturn20Rank=70.62%
- entryRiskMetrics：entryRangePct=5.77%；gapPct=1.99%；priorGapDown60Pct=3.48%
- marketMetrics：marketAboveMa20Pct=57.11%；marketAboveMa60Pct=58.91%；marketUpPct=63.17%

### baseline_only_base
- scoreParts：high60Rank=92.00%；return20Rank=92.23%；rsiBalanceRank=18.05%；macdHistDeltaRank=85.39%；bollSqueezeRank=14.31%；amountEfficiency20Rank=73.09%；moneyflowMarketSurgeQualityRank=25.52%；industryReturn20Rank=62.90%
- entryRiskMetrics：entryRangePct=6.45%；gapPct=2.70%；priorGapDown60Pct=3.87%
- marketMetrics：marketAboveMa20Pct=64.44%；marketAboveMa60Pct=63.85%；marketUpPct=70.75%

### common_base
- scoreParts：high60Rank=93.91%；return20Rank=90.38%；rsiBalanceRank=21.69%；macdHistDeltaRank=84.75%；bollSqueezeRank=16.95%；amountEfficiency20Rank=67.91%；moneyflowMarketSurgeQualityRank=25.33%；industryReturn20Rank=65.28%
- entryRiskMetrics：entryRangePct=6.05%；gapPct=2.63%；priorGapDown60Pct=3.30%
- marketMetrics：marketAboveMa20Pct=72.17%；marketAboveMa60Pct=66.05%；marketUpPct=72.91%

## 最差样本

### candidateOnlySoft
- `601618.SH` 中国中冶 2025-10-09->2025-10-13 收益 -5.84%，净盈亏 -1113.27，退出 gap_open_stop，状态 `soft_risk_on`，MA20 41.14%，upPct 48.92%
- `688800.SH` 瑞可达 2025-12-09->2025-12-11 收益 -5.28%，净盈亏 -950.30，退出 stop，状态 `soft_risk_on`，MA20 41.83%，upPct 60.31%
- `603893.SH` 瑞芯微 2024-12-23->2025-01-02 收益 -5.28%，净盈亏 -566.16，退出 stop，状态 `soft_risk_on`，MA20 40.34%，upPct 64.96%
- `600487.SH` 亨通光电 2025-12-22->2025-12-29 收益 -5.28%，净盈亏 -968.18，退出 stop，状态 `soft_risk_on`，MA20 44.05%，upPct 83.11%
- `603198.SH` 迎驾贡酒 2026-02-02->2026-02-13 收益 -5.28%，净盈亏 -881.09，退出 stop，状态 `soft_risk_on`，MA20 46.24%，upPct 43.56%

### candidateOnlyBase
- `000970.SZ` 中科三环 2025-03-04->2025-04-07 收益 -7.31%，净盈亏 -1124.18，退出 gap_open_stop，状态 `base_risk_on`，MA20 45.77%，upPct 58.85%
- `603131.SH` 上海沪工 2025-08-11->2025-08-12 收益 -5.28%，净盈亏 -946.91，退出 stop，状态 `base_risk_on`，MA20 67.56%，upPct 47.41%
- `002602.SZ` 世纪华通 2025-06-17->2025-06-26 收益 -5.28%，净盈亏 -880.46，退出 stop，状态 `base_risk_on`，MA20 46.58%，upPct 64.71%
- `000899.SZ` 赣能股份 2025-10-22->2025-11-17 收益 -5.28%，净盈亏 -971.47，退出 stop，状态 `base_risk_on`，MA20 46.97%，upPct 84.98%
- `002149.SZ` 西部材料 2026-04-21->2026-04-24 收益 -5.28%，净盈亏 -973.53，退出 stop，状态 `base_risk_on`，MA20 73.84%，upPct 61.84%

### baselineOnlyBase
- `600362.SH` 江西铜业 2025-10-10->2025-10-13 收益 -8.19%，净盈亏 -1725.23，退出 gap_open_stop，状态 `base_risk_on`，MA20 46.99%，upPct 59.66%
- `002941.SZ` 新疆交建 2025-10-10->2025-10-13 收益 -5.41%，净盈亏 -1268.09，退出 gap_open_stop，状态 `base_risk_on`，MA20 46.99%，upPct 59.66%
- `603960.SH` 克来机电 2025-03-17->2025-03-19 收益 -5.28%，净盈亏 -918.92，退出 stop，状态 `base_risk_on`，MA20 72.91%，upPct 83.88%
- `601179.SH` 中国西电 2026-01-20->2026-01-29 收益 -5.28%，净盈亏 -1240.52，退出 stop，状态 `base_risk_on`，MA20 80.65%，upPct 67.11%
- `000967.SZ` 盈峰环境 2025-02-18->2025-02-19 收益 -5.28%，净盈亏 -894.58，退出 stop，状态 `base_risk_on`，MA20 82.46%，upPct 66.13%

## 结论提示

- 若候选独有亏损主要集中在 `soft_risk_on`，说明软门控新增日期本身不可用。
- 若候选独有亏损主要集中在 `base_risk_on`，说明软门控通过资金占用、持仓路径或周频买入限制挤掉了原本更好的基础 Risk-On 交易。
