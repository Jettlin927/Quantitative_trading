# US Sample DB Schema Implementation - 2026-06-27

## 结论

已创建美股 sample 数据表、sample 导入 API 和 DB 只读 overview。第一版只写入 `my_quant/us_research/` 下的 sample 文件，不连接券商，不导入真实持仓。

## DB 表

| 表 | 用途 | 唯一约束 |
| --- | --- | --- |
| `assets` | 美股/ETF sample 资产主数据 | `market + symbol`，`natural_key` |
| `asset_daily_prices` | 美股 sample 行情快照 | `asset_natural_key + trade_date`，`natural_key` |
| `watchlist_items` | 美股 sample 观察池、主题、风险标签 | `watchlist_name + asset_natural_key`，`natural_key` |
| `portfolio_snapshots` | sample 持仓快照 | `snapshot_id` |

模型位置：`backend/app/models.py`

## API

- `GET /api/us-research/import-preview`
  - 只读 preview。
  - 返回目标表、行数和自然键。

- `POST /api/us-research/import-sample`
  - 读取 sample 文件。
  - upsert 到四张 sample 表。
  - 同步日志写入 `data_sync_runs`，`source=local-sample`。

- `GET /api/us-research/db-overview`
  - 从 DB 返回 sample overview。

- `GET /api/us-research/overview`
  - 返回文件 sample overview。

## sample 导入口径

当前 sample 文件：

- `my_quant/us_research/config/watchlist_symbols.csv`
- `my_quant/us_research/data/holdings_sample.csv`
- `my_quant/us_research/data/snapshots/us_snapshot_latest.json`

当前预期行数：

| 目标 | 行数 |
| --- | ---: |
| `assets` | 4 |
| `asset_daily_prices` | 4 |
| `watchlist_items` | 4 |
| `portfolio_snapshots` | 1 |

## 验证命令

```bash
.venv/bin/python -m unittest backend.tests.test_us_research_db backend.tests.test_us_research backend.tests.test_data_api_contracts -v
.venv/bin/python -m py_compile backend/app/database.py backend/app/models.py backend/app/schemas.py backend/app/tushare_client.py backend/app/us_research.py backend/app/main.py
curl -fsS -X POST http://localhost:18000/api/us-research/import-sample
curl -fsS http://localhost:18000/api/us-research/db-overview
```

## 安全边界

- 未导入真实 HSBC/券商持仓。
- 未连接券商、交易账户或订单接口。
- 未删除或重置 PostgreSQL volume。
- 未修改 A 股行情、估值或财务表的已有口径。
- sample 表保留 `is_sample` 与 `source` 字段；真实持仓导入前仍需再次确认。
