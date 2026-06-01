# 002-preentry-path-pulse-moneyflow-delta-001

## 结论摘要

- baseline: `002-repair-indicator-ablate-ma-001`
- candidate: `002-repair-indicator-pulse-moneyflow-surge-002`
- mode: `full`

## 候选独有亏损 vs 盈利最大均值差

| 窗口 | 替换净差 | 候选独有 | 候选亏损 | 前三差异指标 |
| --- | ---: | ---: | ---: | --- |
| `ALL` | `5939.98` | `12` | `6` | rsiDelta3d -11.1595, rsiStrategy -8.6093, macdHist +1.0061 |
| `Y1` | `3950.51` | `4` | `2` | rsiStrategy -4.4971, rsiDelta3d +1.6015, amountRatio +1.5307 |
| `Y2` | `1976.47` | `3` | `2` | rsiDelta3d -26.0355, rsiStrategy -9.0195, bollPositionDelta3d -0.7779 |
| `Y3` | `531.54` | `5` | `2` | rsiDelta3d -11.4409, rsiStrategy -6.2180, macdHist +2.9227 |
| `R18-1` | `2497.36` | `5` | `3` | rsiStrategy -8.1616, rsiDelta3d -3.6472, amountRatio +1.4384 |
| `R18-2` | `4746.91` | `6` | `4` | rsiDelta3d -14.4643, rsiStrategy -8.3123, amountRatio +0.4908 |
| `R18-3` | `-1579.39` | `6` | `3` | rsiDelta3d -22.1736, rsiStrategy -8.7665, amountRatio -1.0633 |
| `R18-4` | `3442.62` | `7` | `3` | rsiDelta3d -15.9498, rsiStrategy -5.6621, macdHist +1.9589 |
