# 美股 DB 入库确认清单（2026-06-27）

## 背景

用户希望美股行情、持仓历史和持仓数据也进入 DB，由后端统一呈现。这个方向正确，但它会改变持久化 schema：当前 API 启动时会执行 `Base.metadata.create_all()`，只要新增 SQLAlchemy model，Docker DB 中就可能自动出现新表。

2026-06-27 16:14 后，用户已明确确认“新增持久化 DB 的 schema”。本清单从确认清单更新为实施记录。

## 当前可用 sample 数据

- 观察池：`my_quant/us_research/config/watchlist_symbols.csv`
- sample 持仓：`my_quant/us_research/data/holdings_sample.csv`
- yfinance 快照：`my_quant/us_research/data/snapshots/us_snapshot_latest.json`
- sample 回测报告：`my_quant/us_research/reports/latest_us_watchlist_backtest.json`

这些文件都不应被当作真实账户数据；第一版入库只允许 sample 或脱敏数据。

## 已实施的最小表边界

第一版只做只读展示与研究辅助，不做交易执行。

| 表 | 用途 | 第一版数据来源 | 关键约束 |
| --- | --- | --- | --- |
| `assets` | 统一美股/ETF/杠杆 ETF 的资产主数据 | sample 观察池 | `market + symbol` 唯一，`natural_key` 唯一 |
| `asset_daily_prices` | 美股日线行情与快照 | yfinance sample 快照 | `asset_natural_key + trade_date` 唯一 |
| `watchlist_items` | 美股观察池、主题、风险标签 | sample 观察池 | `watchlist_name + asset_natural_key` 唯一 |
| `portfolio_snapshots` | sample 持仓快照 | `holdings_sample.csv` | `snapshot_id` 唯一，明确 `is_sample` / `source` |

## 已确认的红线

1. 已允许在本地 PostgreSQL 中创建上述 sample 表。
2. 第一版只导入 `my_quant/us_research/` 下 sample 数据，不导入真实 HSBC 或券商导出。
3. sample 与未来真实持仓暂不同表拆分，而是在表内保留 `is_sample` 和 `source`；真实持仓导入前仍需再次确认。
4. 已新增只读 API：`GET /api/us-research/db-overview`，以及 sample 导入 API：`POST /api/us-research/import-sample`。

## 已执行验证

- 新增 SQLAlchemy models：`Asset`、`AssetDailyPrice`、`WatchlistItem`、`PortfolioSnapshot`。
- 新增 DB 测试：`backend/tests/test_us_research_db.py`。
- 重建并启动 API：`docker compose up -d --build api`。
- 执行 sample 导入：`POST /api/us-research/import-sample`。
- PostgreSQL 表行数：
  - `assets`: `4`
  - `asset_daily_prices`: `4`
  - `watchlist_items`: `4`
  - `portfolio_snapshots`: `1`

`GET /api/us-research/db-overview` 返回 `source=db-sample`、`dbPersistence=sample_persisted`。

## 仍然暂不做

- 不连接真实券商、HSBC、交易账户或订单接口。
- 不导入真实持仓、真实成交、真实成本和资金余额。
- 不把研究标签改写成买卖指令。
- 不修改 A 股现有行情表结构或删除 PostgreSQL volume。
