# 002-candidate-preentry-universe-001 候选全集入场前路径诊断

- 来源 run：`002-repair-volume-inefficiency-crowding-penalty-003`
- 开始时间：2026-06-01 20:41 +0800
- 结束时间：2026-06-01 20:44 +0800
- topN：`5`
- 日期模式：`actual-entry`
- 样本数：`513`
- Risk-On 有信号日期：`79`

## 分组说明

- `actualTraded`：真实买入的信号。
- `sameDayUntradedTop5`：真实买入日里，排名靠前但未成交的信号。
- `capacityBlockedTop5`：Risk-On 且有信号，但因持仓/周频名额已满而未买的 top 信号。
- `riskOnTop5Untraded`：Risk-On 有信号但当日无真实买入，且不是容量满的 top 信号。

## 窗口摘要

| 窗口 | 分组 | 样本 | 平均排名 | Fwd3 | Fwd5 | Fwd10 | Fwd5胜率 | RSI 3日变化 | 成交额比 | 入场振幅 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `ALL` | `actualTraded` | `123` | `1.39` | `3.37%` | `4.39%` | `6.85%` | `54.47%` | `14.06` | `3.25` | `6.07%` |
| `ALL` | `sameDayUntradedTop5` | `390` | `4.55` | `1.01%` | `0.94%` | `1.64%` | `44.36%` | `10.59` | `2.40` | `5.67%` |
| `ALL` | `capacityBlockedTop5` | `0` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| `ALL` | `riskOnTop5Untraded` | `0` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| `Y1` | `actualTraded` | `25` | `1.36` | `3.17%` | `3.71%` | `5.34%` | `52.00%` | `12.51` | `3.07` | `5.14%` |
| `Y1` | `sameDayUntradedTop5` | `85` | `4.46` | `-0.11%` | `0.15%` | `-1.52%` | `42.35%` | `10.46` | `2.24` | `4.92%` |
| `Y1` | `capacityBlockedTop5` | `0` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| `Y1` | `riskOnTop5Untraded` | `0` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| `Y2` | `actualTraded` | `38` | `1.42` | `6.53%` | `6.19%` | `11.70%` | `65.79%` | `15.37` | `3.86` | `6.68%` |
| `Y2` | `sameDayUntradedTop5` | `110` | `4.67` | `1.34%` | `1.15%` | `-0.08%` | `36.36%` | `10.20` | `2.67` | `6.07%` |
| `Y2` | `capacityBlockedTop5` | `0` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| `Y2` | `riskOnTop5Untraded` | `0` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| `Y3` | `actualTraded` | `60` | `1.38` | `1.45%` | `3.52%` | `4.34%` | `48.33%` | `13.89` | `2.94` | `6.07%` |
| `Y3` | `sameDayUntradedTop5` | `195` | `4.53` | `1.31%` | `1.17%` | `4.05%` | `49.74%` | `10.86` | `2.32` | `5.76%` |
| `Y3` | `capacityBlockedTop5` | `0` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| `Y3` | `riskOnTop5Untraded` | `0` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| `R18-1` | `actualTraded` | `42` | `1.40` | `3.45%` | `4.04%` | `9.84%` | `50.00%` | `12.27` | `3.33` | `5.73%` |
| `R18-1` | `sameDayUntradedTop5` | `130` | `4.56` | `0.78%` | `1.44%` | `-0.19%` | `40.00%` | `10.44` | `2.53` | `5.51%` |
| `R18-1` | `capacityBlockedTop5` | `0` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| `R18-1` | `riskOnTop5Untraded` | `0` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| `R18-4` | `actualTraded` | `81` | `1.38` | `3.33%` | `4.57%` | `5.27%` | `56.79%` | `15.00` | `3.21` | `6.25%` |
| `R18-4` | `sameDayUntradedTop5` | `260` | `4.55` | `1.12%` | `0.69%` | `2.57%` | `46.54%` | `10.66` | `2.34` | `5.74%` |
| `R18-4` | `capacityBlockedTop5` | `0` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| `R18-4` | `riskOnTop5Untraded` | `0` | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

## 结论提示

- 本诊断重建每日通过过滤的候选信号池，并用真实买入记录标记成交；它不改变组合回测语义。
- 未成交候选只看后续 close-to-close 路径，不考虑资金释放、真实成交价格、周频复买和组合路径反馈，因此只能作为下一轮因子研究线索。
