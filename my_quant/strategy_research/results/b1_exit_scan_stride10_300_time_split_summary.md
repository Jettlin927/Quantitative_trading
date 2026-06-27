# B1 Exit Scan Stride10 300 Time Split

## Candidate

- Universe probe: `stride10_300`
- Exit config: `tp8_16_24_f100_100_100`
- Rule: hit `8%`, `16%`, or `24%` profit thresholds and sell the full remaining position.

## Results

| Window | Annual return | Max drawdown | Calmar | Trades | Return gate | Drawdown gate |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| `2025-01-01` to `2026-05-15` | 58.87% | -21.48% | 2.74 | 235 | Pass | Pass |
| `2025-01-01` to `2025-12-31` | 100.03% | -18.15% | 5.51 | 194 | Pass | Pass |
| `2026-01-01` to `2026-05-15` | -10.78% | -21.48% | -0.50 | 31 | Fail | Pass |

## Interpretation

The `stride10_300` probe produced an in-sample candidate that clears the `50%` annual return and `-30%` drawdown gates, but it failed the 2026 out-of-sample return check. The drawdown control remains useful, while the return engine is not yet robust enough to mark the active goal complete.

Next work should focus on active-liquidity universe construction, B1 score calibration, and walk-forward validation rather than accepting the in-sample parameter.
