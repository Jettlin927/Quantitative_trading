# B1 Walk-Forward Validation

- Symbols loaded: `299`.
- Window count: `5`.
- Config count: `20`.
- Passing all-window configs: `0`.
- Best strategy by strict gate ordering: `tp12_24_36_f100_100_100`.

## Summary Top Rows

| strategy                | windows | return_pass_windows | drawdown_pass_windows | mean_annual_return | min_annual_return | worst_drawdown | mean_calmar | return_fail_windows | drawdown_fail_windows | passes_all_windows |
| ----------------------- | ------- | ------------------- | --------------------- | ------------------ | ----------------- | -------------- | ----------- | ------------------- | --------------------- | ------------------ |
| tp12_24_36_f100_100_100 | 5       | 0                   | 4                     | 0.1917             | 0.0317            | -0.3254        | 1.1849      | 5                   | 1                     | False              |
| tp5_10_15_f100_100_100  | 5       | 1                   | 5                     | 0.3237             | 0.0268            | -0.2842        | 1.7335      | 4                   | 0                     | False              |
| tp10_20_30_f50_50_100   | 5       | 1                   | 5                     | 0.2230             | -0.0150           | -0.2325        | 1.4859      | 4                   | 0                     | False              |
| tp12_24_36_f50_50_100   | 5       | 0                   | 5                     | 0.2038             | -0.0394           | -0.2744        | 1.5090      | 5                   | 0                     | False              |
| tp5_10_15_f50_50_100    | 5       | 0                   | 5                     | 0.0391             | -0.0683           | -0.2689        | 0.2382      | 5                   | 0                     | False              |
| tp8_16_24_f25_25_100    | 5       | 1                   | 5                     | 0.1321             | -0.0713           | -0.2333        | 1.1197      | 4                   | 0                     | False              |
| tp10_20_30_f33_33_100   | 5       | 1                   | 5                     | 0.1773             | -0.0838           | -0.2499        | 1.3849      | 4                   | 0                     | False              |
| tp8_16_24_f33_33_100    | 5       | 1                   | 5                     | 0.1586             | -0.0940           | -0.2261        | 1.3343      | 4                   | 0                     | False              |
| tp5_10_15_f33_33_100    | 5       | 0                   | 4                     | 0.1138             | -0.0951           | -0.3101        | 0.8514      | 5                   | 1                     | False              |
| tp5_10_15_f25_25_100    | 5       | 1                   | 4                     | 0.1721             | -0.0959           | -0.3134        | 1.3445      | 4                   | 1                     | False              |

## Best Strategy Window Detail

| strategy                | window     | start      | end        | take_profit_levels | take_profit_fractions | annual_return | max_drawdown | calmar | passes_return_gate | passes_drawdown_gate | trade_count | candidate_count |
| ----------------------- | ---------- | ---------- | ---------- | ------------------ | --------------------- | ------------- | ------------ | ------ | ------------------ | -------------------- | ----------- | --------------- |
| tp12_24_36_f100_100_100 | full       | 2025-01-01 | 2026-05-15 | 0.12/0.24/0.36     | 1.00/1.00/1.00        | 0.0317        | -0.3254      | 0.0974 | False              | False                | 175.0000    | 478.0000        |
| tp12_24_36_f100_100_100 | oos_2026   | 2026-01-01 | 2026-05-15 | 0.12/0.24/0.36     | 1.00/1.00/1.00        | 0.3536        | -0.1792      | 1.9727 | False              | True                 | 41.0000     | 114.0000        |
| tp12_24_36_f100_100_100 | train_2025 | 2025-01-01 | 2025-12-31 | 0.12/0.24/0.36     | 1.00/1.00/1.00        | 0.0620        | -0.2535      | 0.2445 | False              | True                 | 138.0000    | 364.0000        |
| tp12_24_36_f100_100_100 | wf_2025_h1 | 2025-01-01 | 2025-06-30 | 0.12/0.24/0.36     | 1.00/1.00/1.00        | 0.0825        | -0.2535      | 0.3253 | False              | True                 | 52.0000     | 124.0000        |
| tp12_24_36_f100_100_100 | wf_2025_h2 | 2025-07-01 | 2025-12-31 | 0.12/0.24/0.36     | 1.00/1.00/1.00        | 0.4287        | -0.1305      | 3.2844 | False              | True                 | 75.0000     | 240.0000        |

# B1 Trend Pullback Replica

- Evaluation window: `2025-01-01` to `2026-05-15`.
- Symbols loaded: `299`.
- Annual return: `19.40%`.
- Max drawdown: `-21.86%`.
- Calmar: `0.89`.
- Trades: `330`.
- Candidates: `478`.
- Passes 50% annual gate: `False`.
- Passes -30% drawdown gate: `True`.

This is a local B1 proxy replica. It is not a full reproduction of the screenshot platform until B1 score and sell rules are matched to platform trade details.
