# Candidate Backtest Summary

- Data source: AkShare `fund_etf_hist_sina`, cached under `data_cache/`.
- Evaluation window: `2021-01-04` to `2026-06-15`.
- Latest available date in cache: `2026-06-15`.
- Dynamic strategy cost assumption: `0.100%` one-way turnover cost.
- Baseline annual return: `5.30%`; max drawdown: `-8.30%`; Calmar: `0.64`.
- Gate result: 存在参数组同时通过年化、卡玛比和 -12% 回撤门槛；以下为当前研究候选。

## Current Best Research Candidate

- Strategy: `ram_top2_m20_v120_f21_cost`
- Annual return: `12.13%`
- Excess annual return vs baseline: `6.83%`
- Max drawdown: `-10.57%`
- Max drawdown difference vs baseline: `-2.27%`
- Calmar: `1.15`
- Rebalance count: `63`
- Estimated cost drag from turnover: `8.05%`

Interpretation: this is the current best research candidate under the lightweight screen, not a production strategy. It still needs full Walk-Forward, factor IC, and notebook-level reproduction before being called stable alpha.

## Base Strategy Comparison

| strategy                                   |   annual_return |   annual_volatility |   max_drawdown |   sharpe |   calmar |   estimated_cost |
|:-------------------------------------------|----------------:|--------------------:|---------------:|---------:|---------:|-----------------:|
| baseline_china_permanent_25_annual_no_cost |          0.0530 |              0.0660 |        -0.0830 |   0.8027 |   0.6382 |           0.0000 |
| equal_weight_5_assets_monthly_cost         |          0.0503 |              0.1062 |        -0.2389 |   0.4734 |   0.2104 |           0.0010 |
| risk_parity_5_assets_v20_monthly_cost      |          0.0067 |              0.0185 |        -0.0273 |   0.3641 |   0.2461 |           0.0190 |
| risk_parity_5_assets_v60_monthly_cost      |          0.0077 |              0.0212 |        -0.0371 |   0.3624 |   0.2066 |           0.0158 |
| ram_top1_m60_v60_monthly_cost              |          0.0384 |              0.1598 |        -0.2169 |   0.2404 |   0.1772 |           0.0710 |
| ram_top2_m60_v60_monthly_cost              |         -0.0099 |              0.1862 |        -0.3992 |  -0.0531 |  -0.0248 |           0.0517 |
| ram_top3_m60_v60_monthly_cost              |          0.0179 |              0.1639 |        -0.3529 |   0.1091 |   0.0507 |           0.0458 |
| ram_top2_m60_v60_monthly_trend_filter_cost |         -0.0108 |              0.1862 |        -0.3992 |  -0.0582 |  -0.0272 |           0.0520 |

## Top 10 RAM Parameter Scan

| strategy                   |   annual_return |   annual_volatility |   max_drawdown |   sharpe |   calmar |   estimated_cost |
|:---------------------------|----------------:|--------------------:|---------------:|---------:|---------:|-----------------:|
| ram_top2_m20_v120_f21_cost |          0.1213 |              0.1184 |        -0.1057 |   1.0249 |   1.1474 |           0.0805 |
| ram_top2_m20_v60_f21_cost  |          0.1128 |              0.1179 |        -0.0992 |   0.9567 |   1.1367 |           0.0814 |
| ram_top3_m20_v60_f21_cost  |          0.1001 |              0.1124 |        -0.0992 |   0.8907 |   1.0086 |           0.0777 |
| ram_top3_m20_v120_f21_cost |          0.1024 |              0.1130 |        -0.1057 |   0.9057 |   0.9684 |           0.0768 |
| ram_top2_m20_v60_f10_cost  |          0.1066 |              0.1256 |        -0.1348 |   0.8490 |   0.7910 |           0.1256 |
| ram_top2_m20_v120_f10_cost |          0.1087 |              0.1279 |        -0.1391 |   0.8504 |   0.7820 |           0.1239 |
| ram_top3_m20_v60_f10_cost  |          0.1051 |              0.1210 |        -0.1348 |   0.8684 |   0.7795 |           0.1176 |
| ram_top1_m120_v20_f63_cost |          0.1567 |              0.1507 |        -0.2020 |   1.0397 |   0.7757 |           0.0250 |
| ram_top3_m20_v20_f10_cost  |          0.1112 |              0.1137 |        -0.1463 |   0.9778 |   0.7598 |           0.1184 |
| ram_top1_m180_v20_f63_cost |          0.1491 |              0.1508 |        -0.1968 |   0.9886 |   0.7577 |           0.0170 |

## Simple In-Sample / Out-of-Sample Check

- Best train-period config selected on `2017-09-01` to `2020-12-31`: `ram_top2_m180_v20_f10_cost`.
- Train annual return: `13.36%`; train max drawdown: `-11.67%`; train Calmar: `1.14`.
- Same config on `2021-01-04` to `2026-06-15` annual return: `3.38%`; max drawdown: `-21.42%`; Calmar: `0.16`.

Use this as a warning label: if the train-selected config collapses out of sample, the apparent best current-period parameters are likely path-dependent.
