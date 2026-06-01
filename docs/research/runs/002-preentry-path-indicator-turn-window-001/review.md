# 002-preentry-path-indicator-turn-window-001

## 结论摘要

- baseline: `002-repair-indicator-pulse-moneyflow-surge-002-window-validation`
- candidate: `002-repair-indicator-turn-quality-001-window-validation`
- mode: `window_validation`

## 候选独有亏损 vs 盈利最大均值差

| 窗口 | 替换净差 | 候选独有 | 候选亏损 | 前三差异指标 |
| --- | ---: | ---: | ---: | --- |
| `Y1` | `-191.96` | `3` | `3` | NA |
| `Y2` | `-1048.84` | `3` | `3` | NA |
| `Y3` | `-3520.12` | `8` | `5` | rsiDelta3d +22.3471, rsiStrategy +17.1945, amountRatio +1.7504 |
| `R18-1` | `806.38` | `1` | `1` | NA |
| `R18-2` | `-2177.30` | `2` | `2` | NA |
| `R18-3` | `-3506.25` | `9` | `8` | rsiStrategy -4.2546, rsiDelta3d -3.7354, priorGapDown3Count60 -2.7500 |
| `R18-4` | `-710.91` | `5` | `2` | rsiDelta3d +15.4570, rsiStrategy +2.7051, amountRatio +0.9145 |
