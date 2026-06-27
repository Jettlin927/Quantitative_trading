# 美股 sample 只读 API 接入报告（2026-06-27）

## 结论

本次已补齐美股 sample 数据的后端只读呈现合同：后端现在可以从 `my_quant/us_research/` 读取 sample 观察池、sample 持仓、yfinance 快照和 sample 规则回测，并通过 `/api/us-research/overview` 统一返回给前端。

2026-06-27 15:59 追加：已新增 `/api/us-research/import-preview`，把 sample 文件转换成未来 DB upsert 的预览行、目标表和自然唯一键；该接口明确 `writesEnabled=false`，不创建表、不写 DB。

结论标签：`观察`。本次不创建 PostgreSQL 表，不导入真实持仓，不连接券商。

## 当前 API

```text
GET /api/us-research/overview
GET /api/us-research/import-preview
```

返回核心结构：

- `source`: 当前为 `file-sample`。
- `isSample`: 当前固定为 `true`。
- `dataBoundary`: 明确 `brokerConnected=false`、`realHoldingsImported=false`、`dbPersistence=pending_confirmation`、`executionEnabled=false`。
- `assets`: 由 sample 观察池合并快照、sample 持仓和规则回测得到。
- `watchlist`: sample 观察池原始语义。
- `portfolioSnapshots`: sample 持仓快照。
- `marketSnapshot`: yfinance 快照状态和明细。
- `watchlistBacktest`: sample 观察池规则回测。
- `evidenceFiles`: sample 文件路径。

`/api/us-research/import-preview` 返回核心结构：

- `mode`: `preview`
- `writesEnabled`: `false`
- `requiresConfirmation`: `true`
- `validation.blockers`: 包含 `db_schema_confirmation_required`
- `targetTables`: `assets`、`asset_daily_prices`、`watchlist_items`、`portfolio_snapshots`
- `records`: 未来可 upsert 的规范化行

当前运行态预览计数：

| 目标 | 行数 | 唯一键示例 |
| --- | ---: | --- |
| `assets` | 4 | `US:NVDA` |
| `assetDailyPrices` | 4 | `US:NVDA:2026-06-25` |
| `watchlistItems` | 4 | `sample-watchlist:US:NVDA` |
| `portfolioSnapshots` | 1 | `sample-latest` |

## 输入文件

- `my_quant/us_research/config/watchlist_symbols.csv`
- `my_quant/us_research/data/holdings_sample.csv`
- `my_quant/us_research/data/snapshots/us_snapshot_latest.json`
- `my_quant/us_research/reports/latest_us_watchlist_backtest.json`

## 修改文件

- `backend/app/us_research.py`
- `backend/app/main.py`
- `backend/tests/test_us_research.py`
- `frontend/src/main.jsx`
- `frontend/src/styles.css`
- `docs/agent-code-map.md`

## 前端呈现

前端右侧新增 `美股 sample 数据` 面板，展示：

- sample/live 边界。
- 快照状态。
- DB 持久化状态：`pending_confirmation`。
- sample 资产数与 sample 持仓数。
- 券商连接状态。
- 头部美股 sample 资产最新价。
- 入库预览行数：`assets 4`、`prices 4`、`watchlist 4`。
- 写入状态：`writes disabled`。
- sample 观察池来源文件。

## 验证

已运行：

```bash
.venv/bin/python -m unittest backend.tests.test_us_research backend.tests.test_strategy_evaluation backend.tests.test_research_engine_metrics -v
.venv/bin/python -m py_compile backend/app/main.py backend/app/us_research.py backend/app/strategy_evaluation.py backend/app/research_engine/metrics.py backend/tests/test_us_research.py
docker compose run --rm frontend npm run lint
docker compose run --rm frontend npm run build
git diff --check -- backend/app/main.py backend/app/us_research.py backend/tests/test_us_research.py frontend/src/main.jsx frontend/src/styles.css
curl -fsS http://localhost:18000/api/us-research/overview
curl -fsS http://localhost:18000/api/us-research/import-preview
```

验证结果：

- 新增后端测试覆盖 sample 只读合同和 DB import preview；连同生命周期、策略评估和指标测试共 `8` 个测试通过。
- 前端 lint 和 build 通过。
- 运行中 API 返回 `file-sample True 4 1 pending_confirmation`。
- 运行中 import preview 返回 `{'assets': 4, 'assetDailyPrices': 4, 'watchlistItems': 4, 'portfolioSnapshots': 1}`，示例自然键 `US:NVDA`、`US:NVDA:2026-06-25`。
- Playwright 使用本机 Chrome 渲染 `http://localhost:15173/`，确认右栏出现 `美股 sample 数据`、`pending_confirmation`、`NVDA`、`SOXL`，控制台错误 `0`。
- 追加渲染复查：右栏出现 `入库预览`、`assets 4`、`prices 4`、`watchlist 4`、`writes disabled`，控制台错误 `0`。

## 后续事项

真正完成“美股 sample 数据入库”仍需用户确认是否允许在本地 PostgreSQL 创建 sample/空表。确认后可让 `assets`、`asset_daily_prices`、`watchlist_items` 和 `portfolio_snapshots` 复用当前 import preview 合同，把 preview 行转换为真实 upsert，再把数据来源从文件适配器切到 DB 查询。
