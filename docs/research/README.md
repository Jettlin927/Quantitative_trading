# 数据文档

`docs/research/` 当前只保留数据源、DB schema、覆盖审计和 sample 入库记录。

历史策略研究、回测报告、研究阶段和运行台账已从当前主线移除。后续如需重新开启策略研究，必须先作为新需求重新定义目录、接口和验证口径。

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
- `stock_pools`
- `stock_pool_members`
- `data_sync_runs`

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
