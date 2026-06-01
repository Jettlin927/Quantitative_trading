# 002-preentry-path-volume-inefficiency-001

## 结论摘要

- baseline: `002-repair-unconfirmed-gap-range-crowding-penalty-002`
- candidate: `002-repair-volume-inefficiency-crowding-penalty-003`
- mode: `full`

## 候选独有亏损 vs 盈利最大均值差

| 窗口 | 替换净差 | 候选独有 | 候选亏损 | 前三差异指标 |
| --- | ---: | ---: | ---: | --- |
| `ALL` | `3651.69` | `7` | `5` | rsiDelta3d -4.4671, rsiStrategy -2.3354, macdHist -1.9012 |
| `Y1` | `2843.91` | `3` | `2` | rsiStrategy -13.9256, rsiDelta3d -3.1477, amountRatio +1.1019 |
| `Y2` | `-44.16` | `1` | `1` | NA |
| `Y3` | `851.94` | `3` | `2` | rsiDelta3d -8.0229, rsiStrategy +5.6591, macdHist -3.9000 |
| `R18-1` | `2799.75` | `4` | `3` | rsiStrategy -10.5232, amountRatio +1.5001, volumeRatio +1.2309 |
| `R18-2` | `2799.75` | `4` | `3` | rsiStrategy -10.5232, amountRatio +1.5001, volumeRatio +1.2309 |
| `R18-3` | `-44.16` | `1` | `1` | NA |
| `R18-4` | `851.94` | `3` | `2` | rsiDelta3d -8.0229, rsiStrategy +5.6591, macdHist -3.9000 |
