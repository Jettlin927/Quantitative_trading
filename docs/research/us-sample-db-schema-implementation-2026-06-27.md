# US Sample DB Schema Implementation - 2026-06-27

## 结论

`阶段通过`：用户已确认新增持久化 DB schema，本阶段已创建美股 sample 数据表、sample 导入 API 和 DB 只读 overview，并已把 sample 数据写入本地 PostgreSQL。

## 新增 DB 表

| 表 | 用途 | 唯一约束 |
| --- | --- | --- |
| `assets` | 美股/ETF sample 资产主数据 | `market + symbol`，`natural_key` |
| `asset_daily_prices` | 美股 sample 行情快照 | `asset_natural_key + trade_date`，`natural_key` |
| `watchlist_items` | 美股 sample 观察池、主题、风险标签 | `watchlist_name + asset_natural_key`，`natural_key` |
| `portfolio_snapshots` | sample 持仓快照 | `snapshot_id` |

模型位置：`backend/app/models.py`

## 新增/更新 API

- `GET /api/us-research/import-preview`
  - 仍为只读 preview。
  - `writesEnabled=false`
  - `validation.dbSchema=ready`
  - `validation.canExecute=true`
  - `importEndpoint=POST /api/us-research/import-sample`

- `POST /api/us-research/import-sample`
  - 只读取 `my_quant/us_research/` 下 sample 文件。
  - upsert 到四张新表。
  - 不连接券商，不导入真实持仓。

- `GET /api/us-research/db-overview`
  - 从 DB 返回 `db-sample` overview。

- `GET /api/us-research/overview`
  - 优先返回 DB overview；DB 为空时回落 file sample overview。

- `GET /api/research/dashboard`
  - `usOverview` 现在优先读 DB sample。

## 实际导入结果

`POST /api/us-research/import-sample` 返回：

- `dbPersistence=sample_persisted`
- `assets=4`
- `assetDailyPrices=4`
- `watchlistItems=4`
- `portfolioSnapshots=1`
- `rowsUpserted=13`

PostgreSQL 表行数验证：

| 表 | 行数 |
| --- | ---: |
| `assets` | 4 |
| `asset_daily_prices` | 4 |
| `watchlist_items` | 4 |
| `portfolio_snapshots` | 1 |

## 验证命令

```bash
.venv/bin/python -m unittest backend.tests.test_us_research_db backend.tests.test_us_research backend.tests.test_api_contracts backend.tests.test_strategy_evaluation backend.tests.test_strategy_lifecycle backend.tests.test_research_engine_metrics backend.tests.test_research_engine_portfolio backend.tests.test_research_engine_reports -v
.venv/bin/python -m py_compile backend/app/database.py backend/app/models.py backend/app/schemas.py backend/app/backtest_engine.py backend/app/tushare_client.py backend/app/ai_client.py backend/app/main.py backend/app/us_research.py backend/app/strategy_lifecycle.py backend/app/strategy_evaluation.py backend/app/research_engine/metrics.py backend/app/research_engine/portfolio.py backend/app/research_engine/reports.py my_quant/strategy_research/experiment/reports.py
docker compose up -d --build api
curl -fsS -X POST http://localhost:18000/api/us-research/import-sample
curl -fsS http://localhost:18000/api/us-research/db-overview
curl -fsS 'http://localhost:18000/api/research/dashboard?run_limit=5'
docker compose exec -T db psql -U quant -d quant_trading -c "select 'assets' as table_name, count(*) from assets union all select 'asset_daily_prices', count(*) from asset_daily_prices union all select 'watchlist_items', count(*) from watchlist_items union all select 'portfolio_snapshots', count(*) from portfolio_snapshots order by table_name;"
docker compose run --rm frontend npm run lint
docker compose run --rm frontend npm run build
```

## 安全边界

- 未导入真实 HSBC/券商持仓。
- 未连接券商、交易账户或订单接口。
- 未删除或重置 PostgreSQL volume。
- 未修改 A 股行情、基本面或研究池表结构。
- sample 表保留 `is_sample` 与 `source` 字段；真实持仓导入前仍需再次确认。
