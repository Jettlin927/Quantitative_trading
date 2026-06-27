# 美股 DB 入库确认记录（2026-06-27）

## 背景

用户希望美股行情、持仓历史和持仓数据也进入 DB，由后端统一呈现。2026-06-27 用户已明确确认“新增持久化 DB 的 schema”。

第一版只导入本仓 sample 文件，不导入真实 HSBC 或券商导出。

## 当前 sample 数据

- 观察池：`my_quant/us_research/config/watchlist_symbols.csv`
- sample 持仓：`my_quant/us_research/data/holdings_sample.csv`
- sample 快照：`my_quant/us_research/data/snapshots/us_snapshot_latest.json`

这些文件都不应被当作真实账户数据。

## 已实施表边界

| 表 | 用途 | 第一版数据来源 | 关键约束 |
| --- | --- | --- | --- |
| `assets` | 统一美股/ETF sample 资产主数据 | sample 观察池 | `market + symbol` 唯一，`natural_key` 唯一 |
| `asset_daily_prices` | sample 行情快照 | sample 快照 | `asset_natural_key + trade_date` 唯一 |
| `watchlist_items` | sample 观察池、主题、风险标签 | sample 观察池 | `watchlist_name + asset_natural_key` 唯一 |
| `portfolio_snapshots` | sample 持仓快照 | `holdings_sample.csv` | `snapshot_id` 唯一，明确 `is_sample` / `source` |

## 已确认红线

1. 已允许在本地 PostgreSQL 中创建上述 sample 表。
2. 第一版只导入 `my_quant/us_research/` 下 sample 数据。
3. sample 与未来真实持仓暂不拆表，而是在表内保留 `is_sample` 和 `source`。
4. 真实持仓导入前仍需再次确认。

## 已执行验证

- 新增 SQLAlchemy models：`Asset`、`AssetDailyPrice`、`WatchlistItem`、`PortfolioSnapshot`。
- 新增 DB 测试：`backend/tests/test_us_research_db.py`。
- 新增只读 API：`GET /api/us-research/db-overview`。
- 新增 sample 导入 API：`POST /api/us-research/import-sample`。

预期 sample 行数：

- `assets`: `4`
- `asset_daily_prices`: `4`
- `watchlist_items`: `4`
- `portfolio_snapshots`: `1`

## 暂不做

- 不连接真实券商、HSBC、交易账户或订单接口。
- 不导入真实持仓、真实成交、真实成本和资金余额。
- 不修改 A 股现有数据表口径或删除 PostgreSQL volume。
