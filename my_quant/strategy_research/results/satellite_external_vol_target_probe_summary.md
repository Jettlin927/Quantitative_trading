# External Vol-Target Satellite Probe

## Purpose

Test whether the external high-volatility RAM satellite can improve the previous near-miss by replacing fixed gross exposure with a realized-volatility target plus earlier drawdown de-risking.

This is an exploratory rejection note. It is not a production strategy and not an execution instruction.

## Universe

- Risk assets: `TQQQ`, `SOXL`, `TECL`, `UPRO`, `BTC-USD`, `ETH-USD`
- Defense assets tested in the broader scratch pass: `CASH`, `GLD`, `TLT`
- Focused pass persisted here: `GLD` defense, because the prior external probe's top-return rows concentrated there
- Evaluation window: `2021-01-01` to `2026-06-15`

## Focused Grid

- TopN: `1`, `2`
- Momentum windows: `10`, `20`
- Volatility windows: `10`, `60`
- Trend windows: `50`, `100`, `200`
- Rebalance intervals: `5`, `10`, `21`
- Target annualized volatility: `55%`, `70%`, `90%`, `110%`
- Maximum gross exposure: `2.0x`, `2.5x`, `3.0x`
- Drawdown step-down sets:
  - half at `-5%`, quarter at `-10%`, hard stop at `-20%`
  - half at `-8%`, quarter at `-14%`, hard stop at `-24%`
  - half at `-10%`, quarter at `-18%`, hard stop at `-28%`
  - half at `-12%`, quarter at `-20%`, hard stop at `-30%`

## Result

- Configs scanned: `3,456`
- Drawdown-qualified configs: `25`
- Configs passing both full-sample `50%` annual return and `-30%` max drawdown: `0`

Best drawdown-qualified row:

| Config | Annual return | Max drawdown | Comment |
| --- | ---: | ---: | --- |
| `top1_m10_v60_t200_f5_target110_x2_dd5_10_20_GLD` | `24.63%` | `-29.74%` | Return far below target |

Top return row:

| Config | Annual return | Max drawdown | Comment |
| --- | ---: | ---: | --- |
| `top1_m10_v60_t200_f10_target110_x2_5_dd8_14_24_GLD` | `44.60%` | `-45.11%` | Return still below target and drawdown fails badly |

Strict-window check on the best drawdown-qualified row:

| Window | Annual return | Max drawdown |
| --- | ---: | ---: |
| Full 2021-01-01 to 2026-06-15 | `24.63%` | `-29.74%` |
| Train 2021-01-01 to 2024-12-31 | `8.15%` | `-29.74%` |
| OOS 2025-01-01 to 2026-06-15 | `-6.03%` | `-51.51%` |
| WF 2021-2022 | `11.82%` | `-29.74%` |
| WF 2023-2024 | `6.83%` | `-27.06%` |
| WF 2025-2026 | `-6.03%` | `-51.51%` |

## Interpretation

Volatility targeting did not solve the satellite goal. Lower drawdown settings preserved the `-30%` full-sample gate only by cutting the return far below `50%`. Higher-return settings still suffered `40%+` drawdowns. The OOS 2025-2026 window is especially weak for the best drawdown-qualified configuration.

This rules out a simple "more leverage plus vol target" extension of the external RAM probe. Future work should either change the alpha source or use a different payoff shape, rather than continuing to tune exposure controls around the same signal.
