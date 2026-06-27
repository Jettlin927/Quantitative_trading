# Research Dashboard Aggregation API - 2026-06-27

## 结论

`阶段通过`：前端策略评估页已优先读取后端统一聚合 API，旧的多接口读取路径保留为降级兜底。

## 背景

此前前端启动时并行读取多个接口：

- `/api/health`
- `/api/research/overview`
- `/api/strategies/executable/cross-section-strength-risk8`（旧策略退场后返回 `410`，仅作为兼容降级路径）
- `/api/strategy-evaluations`
- `/api/strategy-lifecycle`
- `/api/us-research/overview`
- `/api/us-research/import-preview`
- `/api/research/runs`

这能工作，但前端仍承担了较多拼装责任，不符合“后端呈现统一策略评估结果”的方向。

## 已新增接口

`GET /api/research/dashboard?run_limit=160`

返回内容：

- `health`
- `overview`
- `baseline`
- `strategyEvaluation`
- `strategyLifecycle`
- `usOverview`
- `usImportPreview`
- `researchRuns`

关键边界：

- 三段评估仍由后端生成。
- 旧策略全量 `legacy_reset` 后，`baseline=null`，`strategyEvaluation.evaluations=[]`。
- `train-2020-2024` 和 `oos-2025-now` 当前仍为 `missing`，用于下一条新策略进入评估。
- 美股入库仍是 preview，`writesEnabled=false`。
- 不创建 PostgreSQL 新表，不导入真实持仓，不连接券商。

## 前端变更

`frontend/src/main.jsx` 的 `refreshAll()` 现在优先读取 `/api/research/dashboard?run_limit=160` 并用该 payload 填充页面状态。若聚合接口失败，会回落到旧的多接口读取路径。

## 验证命令

```bash
.venv/bin/python -m unittest backend.tests.test_api_contracts backend.tests.test_strategy_evaluation backend.tests.test_strategy_lifecycle backend.tests.test_us_research backend.tests.test_research_engine_metrics backend.tests.test_research_engine_portfolio -v
.venv/bin/python -m py_compile backend/app/main.py backend/app/strategy_evaluation.py backend/app/strategy_lifecycle.py backend/app/us_research.py backend/app/research_engine/metrics.py backend/app/research_engine/portfolio.py
docker compose run --rm frontend npm run lint
docker compose run --rm frontend npm run build
curl -fsS 'http://localhost:18000/api/research/dashboard?run_limit=5'
```

## 验证结果

- 后端合同/研究相关测试：`23` 个测试通过。
- Python 编译检查通过。
- 前端 `lint` 通过。
- 前端 `build` 通过。
- 运行中 API 返回 `200`，摘要为：
  - `source=backend`
  - `baseline=null`
  - `resetStatus=legacy_strategies_removed_from_primary`
  - `window0=missing`
  - `usImportPreview.writesEnabled=false`
  - `researchRuns.count=5`

## 后续事项

美股 sample schema 已新增，默认仍只写 sample/脱敏数据。真实持仓、成交明细和券商导出仍不能入库，除非用户单独确认数据边界。
