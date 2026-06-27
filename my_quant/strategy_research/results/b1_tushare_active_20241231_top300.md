# B1 Tushare Active Universe Probe

- Universe as-of date: `20241231`.
- Universe filter: listed A shares, non-ST, SH/SZ main growth markets, circ_mv 20-500 CNY bn, turnover_rate >= 1%, pb > 0.
- Active score: `turnover_rate * sqrt(circ_mv)` using Tushare daily_basic.
- Requested symbols: `300`.
- Symbols loaded: `299`.
- Passing all-window configs: `0`.
- Best strategy by strict gate ordering: `tp10_20_30_f25_25_100`.
- Universe CSV: `b1_tushare_active_20241231_top300_universe.csv`.

## Summary Top Rows

| strategy                | windows | return_pass_windows | drawdown_pass_windows | mean_annual_return | min_annual_return | worst_drawdown | mean_calmar | return_fail_windows | drawdown_fail_windows | passes_all_windows |
| ----------------------- | ------- | ------------------- | --------------------- | ------------------ | ----------------- | -------------- | ----------- | ------------------- | --------------------- | ------------------ |
| tp10_20_30_f25_25_100   | 5       | 2                   | 5                     | 0.3133             | -0.3143           | -0.2637        | 1.2523      | 3                   | 0                     | False              |
| tp10_20_30_f33_33_100   | 5       | 2                   | 5                     | 0.3443             | -0.3179           | -0.2567        | 1.4660      | 3                   | 0                     | False              |
| tp10_20_30_f50_50_100   | 5       | 2                   | 5                     | 0.3773             | -0.3184           | -0.2607        | 1.7279      | 3                   | 0                     | False              |
| tp15_30_45_f33_33_100   | 5       | 1                   | 5                     | 0.3282             | -0.3757           | -0.2936        | 1.3473      | 4                   | 0                     | False              |
| tp15_30_45_f25_25_100   | 5       | 1                   | 2                     | 0.3041             | -0.3767           | -0.3050        | 1.2307      | 4                   | 3                     | False              |
| tp15_30_45_f50_50_100   | 5       | 1                   | 5                     | 0.3414             | -0.3795           | -0.2882        | 1.4681      | 4                   | 0                     | False              |
| tp8_16_24_f100_100_100  | 5       | 2                   | 5                     | 0.3204             | -0.3910           | -0.2915        | 1.6436      | 3                   | 0                     | False              |
| tp10_20_30_f100_100_100 | 5       | 2                   | 2                     | 0.5375             | -0.4801           | -0.3185        | 2.6681      | 3                   | 3                     | False              |
| tp12_24_36_f33_33_100   | 5       | 2                   | 5                     | 0.3195             | -0.4824           | -0.2986        | 1.4259      | 3                   | 0                     | False              |
| tp12_24_36_f25_25_100   | 5       | 2                   | 2                     | 0.2771             | -0.4833           | -0.3029        | 1.1858      | 3                   | 3                     | False              |

## Best Strategy Window Detail

| strategy              | window     | start      | end        | take_profit_levels | take_profit_fractions | annual_return | max_drawdown | calmar  | passes_return_gate | passes_drawdown_gate | trade_count | candidate_count |
| --------------------- | ---------- | ---------- | ---------- | ------------------ | --------------------- | ------------- | ------------ | ------- | ------------------ | -------------------- | ----------- | --------------- |
| tp10_20_30_f25_25_100 | full       | 2025-01-01 | 2026-05-15 | 0.10/0.20/0.30     | 0.25/0.25/1.00        | 0.2775        | -0.2637      | 1.0523  | False              | True                 | 311.0000    | 463.0000        |
| tp10_20_30_f25_25_100 | oos_2026   | 2026-01-01 | 2026-05-15 | 0.10/0.20/0.30     | 0.25/0.25/1.00        | 0.5254        | -0.2491      | 2.1093  | True               | True                 | 58.0000     | 99.0000         |
| tp10_20_30_f25_25_100 | train_2025 | 2025-01-01 | 2025-12-31 | 0.10/0.20/0.30     | 0.25/0.25/1.00        | 0.0679        | -0.2637      | 0.2576  | False              | True                 | 230.0000    | 364.0000        |
| tp10_20_30_f25_25_100 | wf_2025_h1 | 2025-01-01 | 2025-06-30 | 0.10/0.20/0.30     | 0.25/0.25/1.00        | -0.3143       | -0.2637      | -1.1917 | False              | True                 | 56.0000     | 125.0000        |
| tp10_20_30_f25_25_100 | wf_2025_h2 | 2025-07-01 | 2025-12-31 | 0.10/0.20/0.30     | 0.25/0.25/1.00        | 1.0097        | -0.2503      | 4.0342  | True               | True                 | 138.0000    | 239.0000        |

# B1 Trend Pullback Replica

- Evaluation window: `2025-01-01` to `2026-05-15`.
- Symbols loaded: `299`.
- Annual return: `8.69%`.
- Max drawdown: `-37.49%`.
- Calmar: `0.23`.
- Trades: `357`.
- Candidates: `463`.
- Passes 50% annual gate: `False`.
- Passes -30% drawdown gate: `False`.

This is a local B1 proxy replica. It is not a full reproduction of the screenshot platform until B1 score and sell rules are matched to platform trade details.
