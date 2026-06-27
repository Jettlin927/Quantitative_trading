# B1 Walk-Forward Validation

- Symbols loaded: `299`.
- Window count: `5`.
- Config count: `20`.
- Passing all-window configs: `0`.
- Best strategy by strict gate ordering: `tp8_16_24_f50_50_100`.

## Summary Top Rows

| strategy                |   windows |   return_pass_windows |   drawdown_pass_windows |   mean_annual_return |   min_annual_return |   worst_drawdown |   mean_calmar |   return_fail_windows |   drawdown_fail_windows | passes_all_windows   |
|:------------------------|----------:|----------------------:|------------------------:|---------------------:|--------------------:|-----------------:|--------------:|----------------------:|------------------------:|:---------------------|
| tp8_16_24_f50_50_100    |         5 |                     1 |                       5 |               0.4908 |              0.0838 |          -0.1958 |        3.8416 |                     4 |                       0 | False                |
| tp5_10_15_f100_100_100  |         5 |                     0 |                       5 |               0.2179 |              0.0498 |          -0.2741 |        1.0693 |                     5 |                       0 | False                |
| tp8_16_24_f33_33_100    |         5 |                     1 |                       5 |               0.3564 |             -0.0038 |          -0.2197 |        2.8564 |                     4 |                       0 | False                |
| tp10_20_30_f50_50_100   |         5 |                     1 |                       5 |               0.5149 |             -0.0681 |          -0.1830 |        4.1589 |                     4 |                       0 | False                |
| tp8_16_24_f25_25_100    |         5 |                     1 |                       5 |               0.2798 |             -0.0722 |          -0.2305 |        2.2686 |                     4 |                       0 | False                |
| tp15_30_45_f100_100_100 |         5 |                     0 |                       5 |               0.1556 |             -0.1036 |          -0.2449 |        0.6917 |                     5 |                       0 | False                |
| tp10_20_30_f100_100_100 |         5 |                     3 |                       5 |               0.4154 |             -0.1057 |          -0.2208 |        2.1969 |                     2 |                       0 | False                |
| tp8_16_24_f100_100_100  |         5 |                     4 |                       5 |               0.6275 |             -0.1078 |          -0.2148 |        3.9989 |                     1 |                       0 | False                |
| tp10_20_30_f33_33_100   |         5 |                     1 |                       5 |               0.3557 |             -0.1263 |          -0.1733 |        2.4410 |                     4 |                       0 | False                |
| tp5_10_15_f50_50_100    |         5 |                     1 |                       5 |               0.3055 |             -0.1435 |          -0.1925 |        2.5836 |                     4 |                       0 | False                |

## Best Strategy Window Detail

| strategy             | window     | start      | end        | take_profit_levels   | take_profit_fractions   |   annual_return |   max_drawdown |   calmar | passes_return_gate   | passes_drawdown_gate   |   trade_count |   candidate_count |
|:---------------------|:-----------|:-----------|:-----------|:---------------------|:------------------------|----------------:|---------------:|---------:|:---------------------|:-----------------------|--------------:|------------------:|
| tp8_16_24_f50_50_100 | full       | 2025-01-01 | 2026-05-15 | 0.08/0.16/0.24       | 0.50/0.50/1.00          |          0.4935 |        -0.1754 |   2.8135 | False                | True                   |      311.0000 |          445.0000 |
| tp8_16_24_f50_50_100 | oos_2026   | 2026-01-01 | 2026-05-15 | 0.08/0.16/0.24       | 0.50/0.50/1.00          |          0.0838 |        -0.1958 |   0.4279 | False                | True                   |       37.0000 |           77.0000 |
| tp8_16_24_f50_50_100 | train_2025 | 2025-01-01 | 2025-12-31 | 0.08/0.16/0.24       | 0.50/0.50/1.00          |          0.4989 |        -0.1754 |   2.8443 | False                | True                   |      252.0000 |          368.0000 |
| tp8_16_24_f50_50_100 | wf_2025_h1 | 2025-01-01 | 2025-06-30 | 0.08/0.16/0.24       | 0.50/0.50/1.00          |          0.1366 |        -0.1754 |   0.7786 | False                | True                   |       90.0000 |          128.0000 |
| tp8_16_24_f50_50_100 | wf_2025_h2 | 2025-07-01 | 2025-12-31 | 0.08/0.16/0.24       | 0.50/0.50/1.00          |          1.2411 |        -0.1005 |  12.3440 | True                 | True                   |      142.0000 |          240.0000 |

# B1 Trend Pullback Replica

- Evaluation window: `2025-01-01` to `2026-05-15`.
- Symbols loaded: `299`.
- Annual return: `39.16%`.
- Max drawdown: `-17.71%`.
- Calmar: `2.21`.
- Trades: `311`.
- Candidates: `445`.
- Passes 50% annual gate: `False`.
- Passes -30% drawdown gate: `True`.

This is a local B1 proxy replica. It is not a full reproduction of the screenshot platform until B1 score and sell rules are matched to platform trade details.
