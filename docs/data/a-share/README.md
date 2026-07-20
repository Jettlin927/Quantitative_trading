# A 股实际市场数据

本模块描述 A 股数据的稳定来源、schema 边界、同步和质量规则。实时覆盖、行数与最新交易日必须从 PostgreSQL/API 现场读取，不能引用 dated 文档当作当前事实。

## 数据范围

- 股票与历史上市状态、交易日历、日线、复权因子、每日涨跌停和停复牌事件。
- 每日估值与财务指标。
- 指数、ETF/基金、申万行业分类和历史成员。
- 自选数据池只表达研究范围，不是交易组合。

所有业务表继续使用既有自然键与幂等 upsert。具体研究还必须满足 point-in-time、历史 universe、可交易性、基准和冻结快照合同。

## 验证入口

- 数据覆盖：`GET /api/db/overview`
- 股票筛选与分页：`GET /api/stocks/screen`
- 股票聚合详情：`GET /api/stocks/{ts_code}/detail`
- 估值与财务历史：`GET /api/stocks/{ts_code}/valuation-history`、`GET /api/stocks/{ts_code}/financial-history`
- 指数、ETF 与行业目录：`GET /api/indices`、`GET /api/funds`、`GET /api/industries`
- 同步进度：`GET /api/tushare/sync-progress`
- 研究 readiness：`GET /api/research/readiness`
- 数据质量与研究合同：[量化研究可信合同](../../research/contracts/quant-foundation-trust-contract.md)

## 历史审计

- [数据源审计（2026-06-26）](../../archive/data/a-share/data-source-audit-2026-06-26.md)
- [数据库覆盖审计（2026-06-26）](../../archive/data/a-share/db-coverage-audit-2026-06-26.md)
- [旧覆盖入口快照（2026-06-26）](../../archive/data/a-share/coverage-overview-2026-06-26.md)
