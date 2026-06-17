# Satellite Inverse Leveraged Trend Probe

## Purpose

Test whether changing payoff shape from long-only high beta ETFs to a long/inverse leveraged ETF universe can improve the satellite goal:

- annual return `>= 50%`
- max drawdown no worse than `-30%`
- no direct shorting; downside views are expressed through listed inverse leveraged ETFs

This is a research-only probe. It is not an executable recommendation.

## Universe

The probe used daily close data for:

- Long risk assets: `TQQQ`, `SOXL`, `TECL`, `UPRO`
- Inverse risk assets: `SQQQ`, `SOXS`, `TECS`, `SPXU`
- Defensive assets: `GLD`, `TLT`, `CASH`

## Grid

The scratch grid covered `5,184` configurations:

- `top_n`: `1`, `2`
- momentum windows: `5`, `10`, `20`
- volatility windows: `5`, `10`, `20`
- trend windows: `20`, `50`, `100`
- rebalance interval: `1`, `3`, `5`, `10`
- gross exposure: `0.75`, `1.0`, `1.25`, `1.5`
- defensive fallback: `CASH`, `GLD`
- drawdown de-risk sets:
  - `-5% / -10% / -20%`
  - `-8% / -14% / -24%`
  - `-12% / -20% / -30%`

## Result

- Total configurations scanned: `5,184`
- Configurations with max drawdown no worse than `-30%`: `1,718`
- Configurations passing both `50%` annual return and `-30%` drawdown: `0`

Best return row:

- Config: `top_n=2`, `momentum=20`, `volatility=5`, `trend=50`, `rebalance=5`, `gross=1.5`, `defense=GLD`
- Annual return: `28.19%`
- Max drawdown: `-33.38%`
- Status: drawdown failed

Best drawdown-qualified row:

- Config: `top_n=2`, `momentum=10`, `volatility=20`, `trend=100`, `rebalance=10`, `gross=0.75`, `defense=GLD`
- Annual return: `18.69%`
- Max drawdown: `-26.36%`
- Status: return failed

## Interpretation

The inverse leveraged ETF payoff shape did not solve the satellite goal. The best-return path still breached the drawdown gate, while the best drawdown-qualified path produced less than `20%` annualized return.

This also weakens the hypothesis that simply adding bearish leveraged instruments can create a robust `50% / 30%` satellite. In this daily trend framework, inverse products add path dependency and whipsaw risk faster than they add durable alpha.

## Decision

Reject this line as a near-term candidate.

Future satellite work should not continue scanning the same daily RAM/trend family with more leveraged ETF variants unless a materially different signal is introduced.
