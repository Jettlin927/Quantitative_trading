# B1 Tushare Market Regime Probe

## Purpose

Test whether the active Tushare B1 universe improves when the market entry gate is stricter than the original CSI 300 `close > BBI` rule.

The probe targets the main failure observed in `b1_tushare_active_20241231_top300`: strong 2025 H2 and 2026 OOS performance, but weak 2025 H1 performance.

## Data And Setup

- Data provider: Tushare
- Stock bars: `pro_bar(adj=qfq)`
- Market bars: `index_daily(000300.SH)`
- Universe: `b1_tushare_active_20241231_top300_universe.csv`
- Loaded panels: `299`
- Base strategy: B1 trend-pullback proxy
- Best tested exit: `tp8_16_24_f100_100_100`
- Best market gate: `CSI300 close > BBI` and `CSI300 MA20 > MA60`

The market gate is applied only to new entries. Existing positions still follow the B1 sell rules.

## Focused Filter Scan

The focused scan compared the top active-universe exit configurations across four market gates:

- `bbi_only`
- `bbi_ma20_gt_ma60`
- `bbi_mom20_pos`
- `bbi_close_gt_ma20_gt_ma60`

Best row:

| Filter | Strategy | Return pass windows | Drawdown pass windows | Min annual return | Worst drawdown | Full annual | Full drawdown | Passes all windows |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `bbi_ma20_gt_ma60` | `tp8_16_24_f100_100_100` | 4 | 5 | 11.93% | -20.45% | 69.30% | -20.45% | No |

## Best Row Window Detail

| Window | Annual return | Max drawdown | Calmar | Return gate | Drawdown gate | Trades | Candidates |
| --- | ---: | ---: | ---: | --- | --- | ---: | ---: |
| full | 69.30% | -20.45% | 3.39 | Pass | Pass | 158 | 357 |
| train_2025 | 51.91% | -19.40% | 2.68 | Pass | Pass | 105 | 270 |
| oos_2026 | 132.77% | -19.18% | 6.92 | Pass | Pass | 52 | 87 |
| wf_2025_h1 | 11.93% | -19.40% | 0.61 | Fail | Pass | 26 | 69 |
| wf_2025_h2 | 76.95% | -15.65% | 4.92 | Pass | Pass | 75 | 201 |

## Breadth And Position Checks

Adding active-universe trend breadth did not improve the failure window:

- Best breadth threshold remained effectively the loose `10%` case.
- Higher breadth thresholds reduced full-sample return and did not make all windows pass.

Changing position structure also did not solve the failure:

- `top2_pos75` and `top2_pos100` marginally improved full annual return to `70.03%`, but the 2025 H1 window stayed at `11.93%`.
- `top1` variants damaged drawdown and stability.
- `top3` variants reduced return.

## Interpretation

This is the closest B1/Tushare near-miss so far. It passes full-sample return, full-sample drawdown, train_2025, oos_2026, and wf_2025_h2. It still fails the strict all-window requirement because wf_2025_h1 annualized return is only `11.93%`.

The improvement suggests that the B1 line should keep using Tushare and a stricter market-regime gate. The remaining weakness is unlikely to be fixed by simple leverage, breadth, or TopN changes. The next useful work is to inspect 2025 H1 trades and improve entry ranking or sell-state logic without overfitting the date window.

## Decision

Keep as the current best near-miss candidate, but do not mark the goal complete.
