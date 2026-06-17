# B1 Small-Capital Mainboard Validation

- Initial cash: `20,000`.
- Symbols loaded after permission-board filter: `179`.
- Top N: `1`.
- Max position: `1.00`.
- Require affordable 100-share lot: `True`.
- Mainboard style gate: `True`.
- Style gate min above BBI pct: `0.3`.
- Style gate min median mom20: `0.0`.
- Style gate min sample size: `20`.

## Summary

| strategy                                  |   windows |   return_pass_windows |   drawdown_pass_windows |   mean_annual_return |   min_annual_return |   worst_drawdown |   mean_calmar |   return_fail_windows |   drawdown_fail_windows | passes_all_windows   |
|:------------------------------------------|----------:|----------------------:|------------------------:|---------------------:|--------------------:|-----------------:|--------------:|----------------------:|------------------------:|:---------------------|
| b1_small_capital_mainboard_final_20260617 |         6 |                     4 |                       6 |               0.5873 |              0.2091 |          -0.1909 |        5.8227 |                     2 |                       0 | False                |

## Window Details

| strategy                                  | window     | start      | end        |   symbol_count |   annual_return |   max_drawdown |   calmar | passes_return_gate   | passes_drawdown_gate   |   trade_count |   candidate_count |
|:------------------------------------------|:-----------|:-----------|:-----------|---------------:|----------------:|---------------:|---------:|:---------------------|:-----------------------|--------------:|------------------:|
| b1_small_capital_mainboard_final_20260617 | full       | 2025-01-01 | 2026-06-17 |            179 |          0.5100 |        -0.1909 |   2.6716 | True                 | True                   |       88.0000 |           46.0000 |
| b1_small_capital_mainboard_final_20260617 | train_2025 | 2025-01-01 | 2025-12-31 |            179 |          0.2911 |        -0.1909 |   1.5248 | False                | True                   |       58.0000 |           29.0000 |
| b1_small_capital_mainboard_final_20260617 | oos_2026   | 2026-01-01 | 2026-06-17 |            179 |          0.7228 |        -0.0686 |  10.5299 | True                 | True                   |       30.0000 |           23.0000 |
| b1_small_capital_mainboard_final_20260617 | wf_2025_h1 | 2025-01-01 | 2025-06-30 |            179 |          0.2091 |        -0.1473 |   1.4200 | False                | True                   |       17.0000 |            9.0000 |
| b1_small_capital_mainboard_final_20260617 | wf_2025_h2 | 2025-07-01 | 2025-12-31 |            179 |          1.0678 |        -0.1293 |   8.2599 | True                 | True                   |       44.0000 |           24.0000 |
| b1_small_capital_mainboard_final_20260617 | wf_2026_h1 | 2026-01-01 | 2026-06-17 |            179 |          0.7228 |        -0.0686 |  10.5299 | True                 | True                   |       30.0000 |           23.0000 |
