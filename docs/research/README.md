# 数据与量化研究文档

`docs/research/` 记录数据源、DB schema、覆盖审计、sample 入库和 2026-07-10 重新开启的量化研究底座。

旧策略研究、旧回测报告和旧研究阶段仍不恢复。新的研究能力以 `backend/app/quant_research/` 为唯一协议层，并严格保持离线研究、下一交易日执行、point-in-time、基准对照和运行可复现边界。

## 当前研究底座

- `quant-foundation-trust-contract.md`：新研究链路的统一可信合同，定义 quality scope、宇宙血缘、信息可得时点、下一交易日执行、输入快照和可复现键。
- `quant-research-foundation-plan-2026-07-10.md`：专业研究底座能力矩阵、缺口、实施顺序和验收标准。
- `backend/tests/fixtures/quant_research_golden/`：完全合成的最小黄金数据集，用于锁定周末、停牌、涨跌停、复权、公告可用日和退市边界。
- 一次性运行产物写入被 Git 忽略的 `outputs/research-runs/`，不再把大型逐事件 CSV 提交到主仓库。
- `docs/research/strategy-results/` 仅为历史只读档案，不代表当前策略候选或新底座验收结果。

## 当前保留文档

- `a-share-data/README.md`：A 股 DB 覆盖结论入口。
- `a-share-data/db-coverage-audit-2026-06-26.md`：A 股五年日线和 daily_basic 覆盖审计。
- `a-share-data/data-source-audit-2026-06-26.md`：A 股数据源和字段覆盖说明。
- `us-db-confirmation-checklist-2026-06-27.md`：美股 sample DB 表创建和入库确认记录。
- `us-sample-db-schema-implementation-2026-06-27.md`：美股 sample schema 与 API 实施记录。
- `us-sample-readonly-api-2026-06-27.md`：美股 sample 文件预览 API 记录。

## 当前 DB 主线

A 股：

- `stocks`
- `stock_daily_bars`
- `stock_daily_basic`
- `stock_financial_indicators`
- `stock_listings`
- `stock_limit_prices`
- `stock_suspend_events`
- `trade_calendars`
- `stock_adjust_factors`
- `indices`
- `index_daily_bars`
- `funds`
- `fund_daily_bars`
- `fund_adjust_factors`
- `industry_classifications`
- `industry_members`
- `stock_pools`
- `stock_pool_members`
- `data_sync_runs`
- `data_sync_jobs`

美股 sample：

- `assets`
- `asset_daily_prices`
- `watchlist_items`
- `portfolio_snapshots`

## 安全边界

- 不保存真实持仓、真实成交或券商导出。
- 不连接真实券商或真实账户。
- 不删除 PostgreSQL volume。
- 不把 sample 数据写成交易建议。
