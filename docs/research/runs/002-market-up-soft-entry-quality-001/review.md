# 002-market-up-soft-entry-quality-001 软门控交易质量诊断

- 基准 run：`002-repair-indicator-pulse-moneyflow-surge-002`，完成交易 `123`。
- 候选 run：`002-repair-market-breadth-up-soft-only-001`，完成交易 `133`。
- 共同交易 `88`；基准独有 `35`；候选独有 `45`。

## 替换关系

| 关系 | 交易数 | 净盈亏 | 胜率 | 均值收益 | 中位收益 | 盈亏比 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `baseline_only` | `35` | `35195.10` | 57.14% | 5.61% | 4.64% | 2.61:1 |
| `candidate_only` | `45` | `-1938.99` | 28.89% | -0.29% | -5.28% | 2.28:1 |
| `common` | `88` | `41441.06` | 45.45% | 3.32% | -5.28% | 2.54:1 |

## 入场市场状态

| 关系 | 入场状态 | 交易数 | 净盈亏 | 胜率 | 均值收益 | 盈亏比 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `baseline_only` | `base_risk_on` | `35` | `35195.10` | 57.14% | 5.61% | 2.61:1 |
| `candidate_only` | `base_risk_on` | `33` | `-4035.83` | 24.24% | -0.86% | 2.46:1 |
| `candidate_only` | `soft_risk_on` | `12` | `2096.84` | 41.67% | 1.29% | 1.98:1 |
| `common` | `base_risk_on` | `88` | `41441.06` | 45.45% | 3.32% | 2.54:1 |

## 候选独有窗口拆分

| 窗口 | 全部数/净盈亏 | 软Risk-On数/净盈亏/胜率 | 基础Risk-On数/净盈亏/胜率 |
| --- | ---: | ---: | ---: |
| `ALL` | `45` / `-1938.99` | `12` / `2096.84` / 41.67% | `33` / `-4035.83` / 24.24% |
| `Y1` | `9` / `-4983.19` | `4` / `-1530.84` / 25.00% | `5` / `-3452.35` / 0.00% |
| `Y2` | `8` / `202.19` | `3` / `1413.17` / 33.33% | `5` / `-1210.98` / 20.00% |
| `Y3` | `28` / `2842.01` | `5` / `2214.51` / 60.00% | `23` / `627.50` / 30.43% |
| `R18-1` | `9` / `-4983.19` | `4` / `-1530.84` / 25.00% | `5` / `-3452.35` / 0.00% |
| `R18-2` | `12` / `-2652.25` | `3` / `1413.17` / 33.33% | `9` / `-4065.43` / 11.11% |
| `R18-3` | `23` / `1953.56` | `4` / `3887.47` / 50.00% | `19` / `-1933.91` / 26.32% |
| `R18-4` | `36` / `3044.20` | `8` / `3627.68` / 50.00% | `28` / `-583.48` / 28.57% |

## 画像差异

### candidate_only_soft
- scoreParts：high60Rank=92.15%；return20Rank=92.42%；rsiBalanceRank=24.21%；macdHistDeltaRank=81.59%；bollSqueezeRank=17.46%；amountEfficiency20Rank=73.45%；moneyflowMarketSurgeQualityRank=0.00%；industryReturn20Rank=78.50%
- entryRiskMetrics：entryRangePct=6.44%；gapPct=1.56%；priorGapDown60Pct=3.61%
- marketMetrics：marketAboveMa20Pct=66.90%；marketAboveMa60Pct=58.17%；marketUpPct=43.05%

### candidate_only_base
- scoreParts：high60Rank=91.20%；return20Rank=87.82%；rsiBalanceRank=27.64%；macdHistDeltaRank=75.74%；bollSqueezeRank=15.42%；amountEfficiency20Rank=73.51%；moneyflowMarketSurgeQualityRank=15.29%；industryReturn20Rank=63.72%
- entryRiskMetrics：entryRangePct=5.64%；gapPct=1.93%；priorGapDown60Pct=3.39%
- marketMetrics：marketAboveMa20Pct=55.45%；marketAboveMa60Pct=59.42%；marketUpPct=65.22%

### baseline_only_base
- scoreParts：high60Rank=92.26%；return20Rank=92.86%；rsiBalanceRank=19.54%；macdHistDeltaRank=85.86%；bollSqueezeRank=13.46%；amountEfficiency20Rank=75.18%；moneyflowMarketSurgeQualityRank=29.97%；industryReturn20Rank=64.23%
- entryRiskMetrics：entryRangePct=6.24%；gapPct=2.90%；priorGapDown60Pct=3.94%
- marketMetrics：marketAboveMa20Pct=67.18%；marketAboveMa60Pct=65.58%；marketUpPct=72.05%

### common_base
- scoreParts：high60Rank=93.72%；return20Rank=90.22%；rsiBalanceRank=20.93%；macdHistDeltaRank=84.59%；bollSqueezeRank=17.16%；amountEfficiency20Rank=67.32%；moneyflowMarketSurgeQualityRank=23.57%；industryReturn20Rank=64.65%
- entryRiskMetrics：entryRangePct=6.15%；gapPct=2.55%；priorGapDown60Pct=3.30%
- marketMetrics：marketAboveMa20Pct=70.73%；marketAboveMa60Pct=65.26%；marketUpPct=72.30%

## 最差样本

### candidateOnlySoft
- `603198.SH` 迎驾贡酒 2026-02-02->2026-02-13 收益 -5.28%，净盈亏 -881.09，退出 stop，状态 `soft_risk_on`，MA20 46.24%，upPct 43.56%
- `688331.SH` 荣昌生物 2023-11-28->2023-12-18 收益 -5.28%，净盈亏 -735.37，退出 stop，状态 `soft_risk_on`，MA20 54.52%，upPct 44.97%
- `603283.SH` 赛腾股份 2026-01-19->2026-01-27 收益 -5.28%，净盈亏 -849.24，退出 stop，状态 `soft_risk_on`，MA20 74.42%，upPct 41.13%
- `002987.SZ` 京北方 2025-02-17->2025-02-19 收益 -5.28%，净盈亏 -707.06，退出 stop，状态 `soft_risk_on`，MA20 80.85%，upPct 44.60%
- `000625.SZ` 长安汽车 2023-11-28->2023-11-29 收益 -5.28%，净盈亏 -681.93，退出 stop，状态 `soft_risk_on`，MA20 54.52%，upPct 44.97%

### candidateOnlyBase
- `000970.SZ` 中科三环 2025-03-04->2025-04-07 收益 -7.31%，净盈亏 -1030.50，退出 gap_open_stop，状态 `base_risk_on`，MA20 45.77%，upPct 58.85%
- `600185.SH` 珠免集团 2026-02-10->2026-02-11 收益 -5.28%，净盈亏 -969.93，退出 stop，状态 `base_risk_on`，MA20 51.34%，upPct 84.81%
- `603131.SH` 上海沪工 2025-08-11->2025-08-12 收益 -5.28%，净盈亏 -946.91，退出 stop，状态 `base_risk_on`，MA20 67.56%，upPct 47.41%
- `002602.SZ` 世纪华通 2025-06-17->2025-06-26 收益 -5.28%，净盈亏 -880.46，退出 stop，状态 `base_risk_on`，MA20 46.58%，upPct 64.71%
- `002215.SZ` 诺普信 2025-05-27->2025-05-29 收益 -5.28%，净盈亏 -848.13，退出 stop，状态 `base_risk_on`，MA20 51.14%，upPct 67.71%

### baselineOnlyBase
- `603960.SH` 克来机电 2025-03-17->2025-03-19 收益 -5.28%，净盈亏 -918.92，退出 stop，状态 `base_risk_on`，MA20 72.91%，upPct 83.88%
- `601179.SH` 中国西电 2026-01-20->2026-01-29 收益 -5.28%，净盈亏 -1240.52，退出 stop，状态 `base_risk_on`，MA20 80.65%，upPct 67.11%
- `600452.SH` 涪陵电力 2026-03-02->2026-03-03 收益 -5.28%，净盈亏 -1236.87，退出 stop，状态 `base_risk_on`，MA20 65.19%，upPct 62.82%
- `000967.SZ` 盈峰环境 2025-02-18->2025-02-19 收益 -5.28%，净盈亏 -894.58，退出 stop，状态 `base_risk_on`，MA20 82.46%，upPct 66.13%
- `300953.SZ` 震裕科技 2026-04-21->2026-04-29 收益 -5.28%，净盈亏 -1152.27，退出 stop，状态 `base_risk_on`，MA20 73.84%，upPct 61.84%

## 结论提示

- 若候选独有亏损主要集中在 `soft_risk_on`，说明软门控新增日期本身不可用。
- 若候选独有亏损主要集中在 `base_risk_on`，说明软门控通过资金占用、持仓路径或周频买入限制挤掉了原本更好的基础 Risk-On 交易。
