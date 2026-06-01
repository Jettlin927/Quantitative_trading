# 002-preentry-path-pulse-moneyflow-window-001

## 结论摘要

- baseline: `002-repair-indicator-ablate-ma-001-window-validation`
- candidate: `002-repair-indicator-pulse-moneyflow-surge-002-window-validation`
- mode: `window_validation`

## 候选独有亏损 vs 盈利最大均值差

| 窗口 | 替换净差 | 候选独有 | 候选亏损 | 前三差异指标 |
| --- | ---: | ---: | ---: | --- |
| `Y1` | `2568.26` | `2` | `0` | NA |
| `Y2` | `-2932.27` | `2` | `2` | NA |
| `Y3` | `3783.82` | `4` | `1` | rsiDelta3d -13.2595, rsiStrategy -7.5101, priorGapDown3Count60 -1.0000 |
| `R18-1` | `-4199.01` | `5` | `4` | rsiDelta3d -1.3013, amountRatio +0.6243, volumeRatio +0.4046 |
| `R18-2` | `-11.19` | `3` | `2` | rsiStrategy -10.4388, rsiDelta3d -7.1294, amountRatio +1.4254 |
| `R18-3` | `-5349.77` | `6` | `4` | rsiDelta3d -6.5986, rsiStrategy -0.8429, macdHistDelta3d +0.5657 |
| `R18-4` | `5257.11` | `11` | `6` | rsiStrategy -8.8645, rsiDelta3d -6.7598, amountRatio -0.9029 |
