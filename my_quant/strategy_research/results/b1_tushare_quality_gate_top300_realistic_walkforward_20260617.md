# B1 Realistic Constraint Walk-Forward

- Data provider: `tushare` cached qfq bars.
- Universe: `b1_tushare_active_20241231_top300_universe.csv`.
- Market gate: CSI 300 close > BBI and MA20 > MA60.
- Quality filters: max close/BBI 27.5%, 20-day momentum 2%-75%, first take profit exits 100%.
- Realistic execution: next-day open entry, 100-share lot, 10% limit-up buy block, 10% limit-down sell block.
- Capacity stress: same as realistic execution plus 5% volume cap; Tushare volume unit needs separate normalization, so it is a stress row, not the main verdict.

## Strategy Summary

| strategy                 |   windows |   return_pass_windows |   drawdown_pass_windows |   mean_annual_return |   min_annual_return |   worst_drawdown |   mean_calmar |   return_fail_windows |   drawdown_fail_windows | passes_all_windows   |
|:-------------------------|----------:|----------------------:|------------------------:|---------------------:|--------------------:|-----------------:|--------------:|----------------------:|------------------------:|:---------------------|
| old_close_fractional     |         6 |                     6 |                       6 |               1.0202 |              0.5259 |          -0.2079 |        6.6340 |                     0 |                       0 | True                 |
| realistic_lot_limit      |         6 |                     2 |                       6 |               0.4701 |              0.1692 |          -0.2444 |        2.3350 |                     4 |                       0 | False                |
| capacity_stress_5pct_vol |         6 |                     1 |                       6 |               0.3931 |              0.0907 |          -0.2546 |        2.1235 |                     5 |                       0 | False                |

## Window Details

| strategy                 | window                 | start      | end        |   symbol_count |   annual_return |   max_drawdown |   calmar | passes_return_gate   | passes_drawdown_gate   |   trade_count |   candidate_count |
|:-------------------------|:-----------------------|:-----------|:-----------|---------------:|----------------:|---------------:|---------:|:---------------------|:-----------------------|--------------:|------------------:|
| capacity_stress_5pct_vol | full_to_20260617       | 2025-01-01 | 2026-06-17 |            299 |          0.3374 |        -0.2546 |   1.3252 | False                | True                   |      340.0000 |          388.0000 |
| capacity_stress_5pct_vol | train_2025             | 2025-01-01 | 2025-12-31 |            299 |          0.3894 |        -0.2090 |   1.8632 | False                | True                   |      225.0000 |          269.0000 |
| capacity_stress_5pct_vol | wf_2025_h1             | 2025-01-01 | 2025-06-30 |            299 |          0.0907 |        -0.2090 |   0.4342 | False                | True                   |       58.0000 |           68.0000 |
| capacity_stress_5pct_vol | wf_2025_h2             | 2025-07-01 | 2025-12-31 |            299 |          0.6005 |        -0.1424 |   4.2153 | True                 | True                   |      157.0000 |          201.0000 |
| capacity_stress_5pct_vol | oos_2026_to_20260617   | 2026-01-01 | 2026-06-17 |            299 |          0.4703 |        -0.1918 |   2.4515 | False                | True                   |      112.0000 |          119.0000 |
| capacity_stress_5pct_vol | wf_2026_h1_to_20260617 | 2026-01-01 | 2026-06-17 |            299 |          0.4703 |        -0.1918 |   2.4515 | False                | True                   |      112.0000 |          119.0000 |
| old_close_fractional     | full_to_20260617       | 2025-01-01 | 2026-06-17 |            299 |          1.2745 |        -0.1506 |   8.4603 | True                 | True                   |      189.0000 |          388.0000 |
| old_close_fractional     | train_2025             | 2025-01-01 | 2025-12-31 |            299 |          1.3296 |        -0.1463 |   9.0859 | True                 | True                   |      116.0000 |          269.0000 |
| old_close_fractional     | wf_2025_h1             | 2025-01-01 | 2025-06-30 |            299 |          0.5259 |        -0.1463 |   3.5939 | True                 | True                   |       28.0000 |           68.0000 |
| old_close_fractional     | wf_2025_h2             | 2025-07-01 | 2025-12-31 |            299 |          0.6523 |        -0.2079 |   3.1379 | True                 | True                   |       81.0000 |          201.0000 |
| old_close_fractional     | oos_2026_to_20260617   | 2026-01-01 | 2026-06-17 |            299 |          1.1694 |        -0.1506 |   7.7629 | True                 | True                   |       75.0000 |          119.0000 |
| old_close_fractional     | wf_2026_h1_to_20260617 | 2026-01-01 | 2026-06-17 |            299 |          1.1694 |        -0.1506 |   7.7629 | True                 | True                   |       75.0000 |          119.0000 |
| realistic_lot_limit      | full_to_20260617       | 2025-01-01 | 2026-06-17 |            299 |          0.4890 |        -0.2214 |   2.2088 | False                | True                   |      228.0000 |          388.0000 |
| realistic_lot_limit      | train_2025             | 2025-01-01 | 2025-12-31 |            299 |          0.6270 |        -0.2213 |   2.8328 | True                 | True                   |      137.0000 |          269.0000 |
| realistic_lot_limit      | wf_2025_h1             | 2025-01-01 | 2025-06-30 |            299 |          0.1692 |        -0.2213 |   0.7647 | False                | True                   |       34.0000 |           68.0000 |
| realistic_lot_limit      | wf_2025_h2             | 2025-07-01 | 2025-12-31 |            299 |          0.8656 |        -0.1585 |   5.4627 | True                 | True                   |       95.0000 |          201.0000 |
| realistic_lot_limit      | oos_2026_to_20260617   | 2026-01-01 | 2026-06-17 |            299 |          0.3350 |        -0.2444 |   1.3706 | False                | True                   |       85.0000 |          119.0000 |
| realistic_lot_limit      | wf_2026_h1_to_20260617 | 2026-01-01 | 2026-06-17 |            299 |          0.3350 |        -0.2444 |   1.3706 | False                | True                   |       85.0000 |          119.0000 |
