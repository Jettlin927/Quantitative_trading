# 美股 sample 只读 API 记录（2026-06-27）

## 结论

后端可以从 `my_quant/us_research/` 读取 sample 观察池、sample 持仓和 sample 快照，并通过 `/api/us-research/overview` 与 `/api/us-research/import-preview` 返回统一结构。

## 当前 API

```text
GET /api/us-research/overview
GET /api/us-research/import-preview
```

返回核心结构：

- `source`: 当前为 `file-sample`。
- `isSample`: 当前固定为 `true`。
- `dataBoundary`: 明确 `brokerConnected=false`、`realHoldingsImported=false`、`executionEnabled=false`。
- `assets`: 由 sample 观察池、sample 快照和 sample 持仓合并得到。
- `watchlist`: sample 观察池原始语义。
- `portfolioSnapshots`: sample 持仓快照。
- `marketSnapshot`: sample 快照状态和明细。
- `evidenceFiles`: sample 文件路径。

`/api/us-research/import-preview` 返回核心结构：

- `mode`: `preview`
- `writesEnabled`: `false`
- `targetTables`: `assets`、`asset_daily_prices`、`watchlist_items`、`portfolio_snapshots`
- `records`: 可 upsert 的规范化行

## 输入文件

- `my_quant/us_research/config/watchlist_symbols.csv`
- `my_quant/us_research/data/holdings_sample.csv`
- `my_quant/us_research/data/snapshots/us_snapshot_latest.json`

## 验证命令

```bash
.venv/bin/python -m unittest backend.tests.test_us_research -v
.venv/bin/python -m py_compile backend/app/main.py backend/app/us_research.py backend/tests/test_us_research.py
curl -fsS http://localhost:18000/api/us-research/overview
curl -fsS http://localhost:18000/api/us-research/import-preview
```

## 后续事项

如需导入真实持仓或真实券商数据，必须重新确认数据治理、脱敏、表边界和安全流程。当前接口只服务 sample 数据。
