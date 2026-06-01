# 002-industry-pulse-surge-quality-window-delta-001 Industry Pulse Persistence Diagnostic

- Baseline: `002-repair-indicator-ablate-ma-001-window-validation`
- Candidate: `002-repair-moneyflow-surge-quality-001-window-validation`
- Coverage: `750/750` trades with industry series.

| Window | Replacement PnL Delta | Candidate-only | Cand prev5 | Cand fwd5 | Cand decay5 | Cand prev+ / fwd- rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ALL | 3650.08 | 75 | 4.84% | 1.45% | -3.40% | 34.67% |
| Y1 | 1180.07 | 1 | 3.19% | 0.70% | -2.49% | 0.00% |
| R18-1 | -618.79 | 3 | 5.10% | -0.06% | -5.16% | 33.33% |
| Y3 | 1155.97 | 6 | 3.55% | -0.32% | -3.87% | 33.33% |
| R18-4 | 3030.85 | 6 | 4.47% | 2.13% | -2.34% | 16.67% |

## Candidate-only worst examples

### ALL
- `600362.SH` 江西铜业 铜 2025-10-10 return -8.19%, industry prev5 13.22%, fwd5 -3.99%, decay5 -17.22%
- `000960.SZ` 锡业股份 小金属 2026-03-02 return -6.65%, industry prev5 13.19%, fwd5 -6.97%, decay5 -20.16%
- `688095.SH` 福昕软件 软件服务 2025-11-04 return -6.59%, industry prev5 2.12%, fwd5 -2.96%, decay5 -5.09%
- `002941.SZ` 新疆交建 建筑工程 2025-10-10 return -5.41%, industry prev5 2.84%, fwd5 -1.25%, decay5 -4.09%
- `000783.SZ` 长江证券 证券 2026-05-06 return -5.28%, industry prev5 3.22%, fwd5 0.70%, decay5 -2.52%

### Y1
- `603087.SH` XD甘李药 生物制药 2023-11-24 return 4.79%, industry prev5 3.19%, fwd5 0.70%, decay5 -2.49%

### R18-1
- `603979.SH` 金诚信 铜 2024-01-02 return -5.28%, industry prev5 4.09%, fwd5 -2.25%, decay5 -6.35%
- `605117.SH` 德业股份 电气设备 2024-03-12 return -5.28%, industry prev5 8.02%, fwd5 1.36%, decay5 -6.66%
- `603087.SH` XD甘李药 生物制药 2023-11-24 return 4.79%, industry prev5 3.19%, fwd5 0.70%, decay5 -2.49%

### Y3
- `603778.SH` 国晟科技 电气设备 2025-11-14 return -7.45%, industry prev5 -0.10%, fwd5 -10.35%, decay5 -10.25%
- `688095.SH` 福昕软件 软件服务 2025-11-04 return -6.59%, industry prev5 2.12%, fwd5 -2.96%, decay5 -5.09%
- `688578.SH` 艾力斯 化学制药 2025-05-30 return -5.28%, industry prev5 2.63%, fwd5 5.88%, decay5 3.26%
- `002605.SZ` 姚记科技 互联网 2025-06-10 return -5.28%, industry prev5 4.09%, fwd5 1.14%, decay5 -2.95%
- `601898.SH` 中煤能源 煤炭开采 2025-10-16 return 4.34%, industry prev5 7.80%, fwd5 4.90%, decay5 -2.90%

### R18-4
- `688578.SH` 艾力斯 化学制药 2025-05-30 return -5.28%, industry prev5 2.63%, fwd5 5.88%, decay5 3.26%
- `002246.SZ` 北化股份 化工原料 2025-05-07 return -5.28%, industry prev5 4.44%, fwd5 1.32%, decay5 -3.12%
- `002605.SZ` 姚记科技 互联网 2025-06-10 return -5.28%, industry prev5 4.09%, fwd5 1.14%, decay5 -2.95%
- `600366.SH` 宁波韵升 矿物制品 2025-06-12 return 2.98%, industry prev5 7.41%, fwd5 -5.18%, decay5 -12.59%
- `601898.SH` 中煤能源 煤炭开采 2025-10-16 return 4.79%, industry prev5 7.80%, fwd5 4.90%, decay5 -2.90%
