# Satellite 50% DD30 Candidate

- Gate: 没有候选同时通过 50% 年化和 -30% 最大回撤门槛；以下为先过回撤闸门、再按年化收益排序的 near-miss。
- Target annual return: `50%+`.
- Maximum drawdown floor: `-30%`.
- Candidate: `fixed_513100_518880_50_50_x2_0`
- Annual return: `28.16%`
- Max drawdown: `-26.68%`
- Sharpe: `1.13`
- Beta vs 513100: `0.84`
- Calmar: `1.06`
- Estimated turnover cost drag: `1.10%`
- Rebalance count: `9`
- Cooldown days: `0`
- Average risk-asset exposure: `200.00%`

This is a satellite-sleeve research result, not a full-portfolio strategy and not an execution instruction.

## Top 10 Candidates

| strategy                                 |   annual_return |   max_drawdown |   calmar | passes_return_gate   | passes_drawdown_gate   |   estimated_cost |
|:-----------------------------------------|----------------:|---------------:|---------:|:---------------------|:-----------------------|-----------------:|
| fixed_513100_518880_50_50_x2_0           |          0.2816 |        -0.2668 |   1.0556 | False                | True                   |           0.0110 |
| fixed_513100_518880_512480_40_40_20_x2_5 |          0.2492 |        -0.2890 |   0.8623 | False                | True                   |           0.0230 |
| fixed_518880_x1_5                        |          0.2358 |        -0.2924 |   0.8064 | False                | True                   |           0.0112 |
| fixed_513100_518880_50_50_x1_5           |          0.2322 |        -0.2234 |   1.0392 | False                | True                   |           0.0075 |
| fixed_513100_518880_512480_40_40_20_x1_5 |          0.2092 |        -0.2355 |   0.8882 | False                | True                   |           0.0125 |
| fixed_513100_518880_60_40_x1_0           |          0.2027 |        -0.1665 |   1.2172 | False                | True                   |           0.0010 |
| fixed_513100_518880_512480_40_40_20_x2_0 |          0.2004 |        -0.2809 |   0.7135 | False                | True                   |           0.0200 |
| fixed_513100_518880_50_50_x1_0           |          0.1996 |        -0.1803 |   1.1072 | False                | True                   |           0.0010 |
| fixed_513100_518880_60_40_x2_0           |          0.1970 |        -0.2765 |   0.7125 | False                | True                   |           0.0180 |
| fixed_513100_518880_60_40_x1_5           |          0.1903 |        -0.2452 |   0.7761 | False                | True                   |           0.0125 |
