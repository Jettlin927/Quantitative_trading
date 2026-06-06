# Tail Minute Data Source Decision

## Purpose

尾盘活跃次日纪律策略需要严格 `14:30` 入场价验证。当前日线回测使用收盘价近似，只能作为信号可行性筛查，不能证明真实尾盘入场收益。

## Current Decision

截至 `2026-06-05 03:26 +08:00`，`mootdx` 分页在线 1 分钟 K 线已通过近端小样本分钟覆盖门槛，可以进入更大样本分钟验证。策略仍停留在观察状态，因为 `14:30` 小样本收益中枢尚不稳定，且三年全市场分钟级回测尚未完成。

## Source Matrix

| Source | Role | Evidence | Status | Decision |
| --- | --- | --- | --- | --- |
| Local `stock_daily_bars` + `stock_daily_basic` | 日线候选重建、活跃度条件、全市场近似回测 | `002-tail-active-best-risk-full-001`、`002-tail-active-minute-best-risk-full-dryrun-001` | Passed for daily approximation | 可继续用于候选生成和对照组 |
| Tencent realtime quote | 当日工作台实时筛选 | API 冒烟已返回实时行情 | Live only | 只用于盘中观察，不用于历史回测 |
| THS hot reason | 当日题材归因 | API 冒烟曾返回热点 | Live/current only | 只做实时解释，不用于历史回放 |
| Eastmoney industry realtime | 当日行业主线快照 | 曾返回行业榜，后续遇到断连兜底 | Live/current only | 不作为历史主线源 |
| Historical industry proxy from local daily bars | 历史主线代理 | `002-tail-active-mainline-full-001` | Failed alpha test | 可保留为负证据，不继续收紧排名 |
| Tushare `stk_mins` | 历史 1 分钟价探测 | `002-tail-active-minute-sample-001` | Failed for batch | 当前账号 `1次/小时`，不能批量补三年样本 |
| Eastmoney recent 1m kline | 近端分钟价探测 | `002-tail-active-minute-eastmoney-open-base-001`、`002-tail-active-minute-eastmoney-open-base-diagnostic-001` | Failed for reliability | 断连且历史日期支持不稳定，不进全量回测 |
| `mootdx` paged 1m provider | 近端历史 1 分钟价来源 | `002-tail-active-minute-mootdx-best-risk-paged-002`、`002-tail-active-minute-mootdx-best-risk-apr-jun-001`、`002-tail-active-minute-mootdx-base-apr-jun-001` | Probe passed | 可用于更大样本分钟验证；正式复现需后端镜像重建成功 |

## Promotion Gates

分钟源只有同时满足以下条件，才能进入更大样本或组合级验证：

1. 候选重建对齐：同一窗口内候选数量应与日线全市场 run 的完成交易规模大体一致，偏差需要解释。
2. 小样本覆盖：至少 `20` 条分钟匹配，覆盖率 `>=80%`，请求错误为 `0`。
3. 时间口径：匹配时间必须是同一交易日 `14:30:00`，若无精确分钟，只能使用 `14:30` 之前最近一分钟，并在结果里记录。
4. 收益口径：有次日数据时计算 `minute_entry_return_to_next_close`；最后交易日开放候选只能用于覆盖率和当日收盘价差验证，不能算次日收益。
5. 数据隔离：分钟探测结果只写入 run-local `minute_cache.jsonl`，未通过前不写入 PostgreSQL 持久表。
6. 晋级标记：`sync_tail_minute_bars.py` 输出的 `canPromoteToBacktest` 必须为 `true`。

## Next Step

下一步不是继续调尾盘参数，而是扩大 `mootdx` 分钟验证：

1. 等 Docker 基础镜像拉取恢复后，重建 `api` 镜像，确认 `backend/requirements.txt` 中的 `mootdx==0.11.7` 可复现。
2. 先扩大到最近 `3-6` 个月 `best-risk` 与 `base` 分钟样本，记录翻页页数、请求耗时和覆盖率。
3. 若覆盖稳定，再设计 PostgreSQL 分钟缓存表或 run-local 批量缓存；未确认存储口径前不写持久库。
4. 若分钟样本收益仍无正中位数，不再继续收紧参数，应转向题材持续性、涨停后结构或大盘退潮过滤。
