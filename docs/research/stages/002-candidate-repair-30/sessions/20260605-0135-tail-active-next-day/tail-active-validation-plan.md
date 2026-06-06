# Tail Active Next-Day Validation Plan

## Objective

把用户提出的尾盘活跃次日纪律策略转成可复现研究目标：先证明数据源口径，再比较参数方案，最后决定是否进入组合级和更长窗口验证。

## Current Best Candidate

`best-risk` 是当前唯一值得继续观察的参数方案：

- 涨幅区间：`2.5%` 至 `5.0%`
- 近涨停记忆：前 `15` 个交易日内出现涨停
- 量比：`>= 2.0`
- 换手率：`>= 7.0%`
- 入场 K 线风险过滤：当日振幅 `<= 6.0%`
- 出场纪律：次日除涨停收盘外，收盘退出

## Stage Gates

| Stage | Goal | Required Evidence | Status |
| --- | --- | --- | --- |
| 1. 日线候选可行性 | 用全市场日线和 daily_basic 证明信号不是手选样本 | `002-tail-active-best-risk-full-001` | Done, but failed alpha |
| 2. 分钟源准入 | 严格 `14:30` 入场价至少 `20` 条匹配、覆盖率 `>=80%`、无请求错误 | `002-tail-active-minute-mootdx-best-risk-paged-002` | Done |
| 3. 三个月分钟对照 | 同窗口比较 `best-risk` 与 `base`，确认风险过滤是否改善收益 | `002-tail-active-minute-mootdx-best-risk-mar-jun-001`、`002-tail-active-minute-mootdx-base-mar-jun-n71-001` | Done, observe |
| 4. 扩大分钟窗口 | 最近 `3-6` 个月或更长窗口，比较全量 `best-risk` 与足够规模 `base` 对照 | `002-tail-active-minute-mootdx-best-risk-jan-jun-001`、`002-tail-active-minute-mootdx-base-jan-jun-n99-001` | Done, failed gate |
| 5. 组合级验证 | 只有分钟样本中位数为正且 profit factor `>1.2`，才进入共享资金组合回测 | 停止条件已触发 | Skipped |
| 6. 阶段结论 | 根据当前阶段门槛标记 `阶段通过` / `观察` / `淘汰` | `tail-active-interim-conclusion.md` | Observe |

## Stop Conditions

任一条件成立，应停止继续调日线阈值：

- 扩大分钟样本后 `best-risk` 中位数仍为负。
- profit factor 长期低于 `1.2`。
- 收益主要来自少数极端样本，收益后 10 出现不可接受尾部亏损。
- `mootdx` 翻页覆盖不稳定，无法复现同一日期的 `14:30` 价格。

## Next Runs

优先级从高到低：

1. 不再继续调同一组尾盘日线阈值。
2. 若继续此策略族，优先验证题材持续性、涨停结构和市场退潮过滤。
3. 若继续分钟研究，先设计分钟缓存，避免每次重复翻页请求在线源。
4. 任何改变次日退出方式的实验，都必须作为新口径，不能覆盖本策略结论。
