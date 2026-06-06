# Tail Active Next-Day Interim Conclusion

## Verdict

结论：`观察，不进入组合级候选`。

`best-risk` 是已比较方案里的相对最优，但在 `mootdx` 在线源最大可覆盖窗口内，严格 `14:30` 入场收益仍未满足进入组合级验证的门槛。

## Compared Variants

| Variant | Window | Sample | Coverage | Avg 14:30 return | Median 14:30 return | Win rate | Profit factor | Decision |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `best-risk` | 2026-01-07 to 2026-06-04 | 98 matched / 99 selected | 98.99% | -0.08% | -0.67% | 44.90% | 0.951 | Observe / failed gate |
| `base` equal-size | 2026-01-07 to 2026-06-04 | 99 matched / 99 selected | 100.00% | -0.63% | -0.84% | 40.40% | 0.696 | Weaker baseline |
| `best-risk` | 2026-03-01 to 2026-06-04 | 71 matched / 71 selected | 100.00% | 0.23% | -0.17% | 47.89% | 1.167 | Better but still failed gate |
| `base` equal-size | 2026-03-01 to 2026-06-04 | 71 matched / 71 selected | 100.00% | -0.39% | -0.07% | 46.48% | 0.814 | Weaker baseline |

## Data Source Boundary

- 6-month dry-run from `2025-12-01` to `2026-06-04` produced `122` `best-risk` candidates and `626` `base` candidates.
- `mootdx` online 1-minute paging can reach about `2026-01-07` for the tested symbol, but not the full `2025-12` history.
- Current online source is enough for near-5-month validation, not enough to claim a complete 6-month or 3-year minute backtest.

## Why It Fails

- Expanded `best-risk` median return remains negative.
- Expanded `best-risk` profit factor is below `1.2`.
- Only `2026-04` shows a strong positive monthly segment; `2026-01`、`2026-02`、`2026-03`、`2026-05` do not confirm stable edge.
- Tail losses remain material: worst 10 include `-12.64%` and `-10.99%` even under `best-risk`.

## Current Best Parameters

Keep as an observation preset only:

- `tailEntryMinPctChg = 0.025`
- `tailEntryMaxPctChg = 0.05`
- `tailMinVolumeRatio = 2.0`
- `tailMinTurnoverRatePct = 7.0`
- `tailPriorLimitUpLookback = 15`
- `entryRiskFilter.enabled = true`
- `entryRiskFilter.maxEntryRangePct = 0.06`

## Next Optimization Path

Do not continue tuning the same daily thresholds. The next useful hypotheses are:

1. 题材持续性：把实时题材/热点转成历史可复现缓存，验证是否只有连续主线题材才有隔夜溢价。
2. 涨停结构：区分近 15 日涨停是首板、连板、反包、断板后修复，避免把不同情绪周期混在一起。
3. 市场退潮过滤：引入全市场涨停家数、跌停家数、炸板率、连板高度或指数尾盘状态，过滤情绪退潮日。
4. 次日开盘处置：单独验证次日开盘/冲高退出是否比固定收盘退出更适合该策略；这会改变退出语义，需作为新实验口径。
5. 分钟缓存工程：若继续分钟研究，应先设计 run-local 或 PostgreSQL 分钟缓存，避免每次重复翻页请求在线源。
