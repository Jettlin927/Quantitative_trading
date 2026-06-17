# B1 Walk-Forward Validation

- Symbols loaded: `299`.
- Window count: `5`.
- Config count: `20`.
- Passing all-window configs: `1`.
- Best strategy by strict gate ordering: `tp8_16_24_f100_100_100`.
- Symbols file: `my_quant/strategy_research/results/b1_tushare_active_20241231_top300_universe.csv`.
- Market MA20 > MA60 filter: `True`.
- Max entry close/BBI: `0.275`.
- Min entry 20-day momentum: `0.02`.
- Max entry 20-day momentum: `0.75`.

## Summary Top Rows

| strategy                |   windows |   return_pass_windows |   drawdown_pass_windows |   mean_annual_return |   min_annual_return |   worst_drawdown |   mean_calmar |   return_fail_windows |   drawdown_fail_windows | passes_all_windows   |
|:------------------------|----------:|----------------------:|------------------------:|---------------------:|--------------------:|-----------------:|--------------:|----------------------:|------------------------:|:---------------------|
| tp8_16_24_f100_100_100  |         5 |                     5 |                       5 |               1.1257 |              0.5257 |          -0.2080 |        7.2015 |                     0 |                       0 | True                 |
| tp12_24_36_f100_100_100 |         5 |                     4 |                       5 |               0.7075 |              0.4924 |          -0.1940 |        4.2449 |                     1 |                       0 | False                |
| tp10_20_30_f100_100_100 |         5 |                     4 |                       5 |               0.9912 |              0.4477 |          -0.1778 |        6.2390 |                     1 |                       0 | False                |
| tp5_10_15_f50_50_100    |         5 |                     3 |                       5 |               0.7027 |              0.1477 |          -0.1798 |        5.0713 |                     2 |                       0 | False                |
| tp8_16_24_f50_50_100    |         5 |                     4 |                       5 |               0.8376 |              0.1279 |          -0.1697 |        5.3284 |                     1 |                       0 | False                |
| tp5_10_15_f33_33_100    |         5 |                     4 |                       5 |               0.6569 |              0.1075 |          -0.1554 |        5.0030 |                     1 |                       0 | False                |
| tp10_20_30_f50_50_100   |         5 |                     4 |                       5 |               0.8004 |              0.1013 |          -0.2188 |        4.2504 |                     1 |                       0 | False                |
| tp5_10_15_f25_25_100    |         5 |                     4 |                       5 |               0.6373 |              0.0993 |          -0.1512 |        4.8620 |                     1 |                       0 | False                |
| tp15_30_45_f100_100_100 |         5 |                     3 |                       5 |               0.5157 |              0.0871 |          -0.2860 |        2.7836 |                     2 |                       0 | False                |
| tp8_16_24_f33_33_100    |         5 |                     3 |                       5 |               0.6616 |              0.0220 |          -0.1801 |        4.0432 |                     2 |                       0 | False                |

## Best Strategy Window Detail

| strategy               | window     | start      | end        | take_profit_levels   | take_profit_fractions   |   annual_return |   max_drawdown |   calmar | passes_return_gate   | passes_drawdown_gate   |   trade_count |   candidate_count |
|:-----------------------|:-----------|:-----------|:-----------|:---------------------|:------------------------|----------------:|---------------:|---------:|:---------------------|:-----------------------|--------------:|------------------:|
| tp8_16_24_f100_100_100 | full       | 2025-01-01 | 2026-05-15 | 0.08/0.16/0.24       | 1.00/1.00/1.00          |          1.2740 |        -0.1595 |   7.9892 | True                 | True                   |      164.0000 |          354.0000 |
| tp8_16_24_f100_100_100 | oos_2026   | 2026-01-01 | 2026-05-15 | 0.08/0.16/0.24       | 1.00/1.00/1.00          |          2.1238 |        -0.1506 |  14.0985 | True                 | True                   |       44.0000 |           85.0000 |
| tp8_16_24_f100_100_100 | train_2025 | 2025-01-01 | 2025-12-31 | 0.08/0.16/0.24       | 1.00/1.00/1.00          |          1.0521 |        -0.1463 |   7.1890 | True                 | True                   |      115.0000 |          269.0000 |
| tp8_16_24_f100_100_100 | wf_2025_h1 | 2025-01-01 | 2025-06-30 | 0.08/0.16/0.24       | 1.00/1.00/1.00          |          0.5257 |        -0.1463 |   3.5924 | True                 | True                   |       28.0000 |           68.0000 |
| tp8_16_24_f100_100_100 | wf_2025_h2 | 2025-07-01 | 2025-12-31 | 0.08/0.16/0.24       | 1.00/1.00/1.00          |          0.6527 |        -0.2080 |   3.1383 | True                 | True                   |       77.0000 |          201.0000 |

# B1 Trend Pullback Replica

- Evaluation window: `2025-01-01` to `2026-05-15`.
- Symbols loaded: `299`.
- Annual return: `127.40%`.
- Max drawdown: `-15.95%`.
- Calmar: `7.99`.
- Trades: `164`.
- Candidates: `354`.
- Passes 50% annual gate: `True`.
- Passes -30% drawdown gate: `True`.

This is a local B1 proxy replica. It is not a full reproduction of the screenshot platform until B1 score and sell rules are matched to platform trade details.
