# Research Job API

目标：把策略研究主路径从 `docker compose run --rm api python scripts/research/...` 迁移到常驻后端服务的 `/api/research/jobs`。研究任务通过 HTTP 提交，后端在已运行的 `api` 容器内启动 Python 子进程执行现有研究脚本，避免每轮研究反复创建临时 Docker 容器。

## 支持任务

- `portfolio_backtest`：执行组合级全窗口回测，对应 `scripts/research/run_portfolio_backtest.py`。
- `window_validation`：执行固定参数滚动窗口验证，对应 `scripts/research/run_window_validation.py`。
- `trade_delta`：执行成交替换诊断，对应 `scripts/research/analyze_trade_delta.py`。

## Agent 默认规则

- 策略研究主路径优先使用本 API，不再默认使用 `docker compose run --rm api python scripts/research/...`。
- 脚本直接执行只作为调试兜底；需要直接跑脚本时，优先用 `docker compose exec -T api python ...` 复用已运行的 `api` 容器。
- 新增因子算法或修改后端代码后，通常只需 `docker compose restart api`；只改研究参数或权重时不需要重启。

## 接口

- `POST /api/research/jobs`：提交研究任务，立即返回 `jobId`。
- `GET /api/research/jobs`：查看当前内存中的研究任务列表。
- `GET /api/research/jobs/{jobId}`：查询任务状态、命令、输出摘要和结果文件。
- `POST /api/research/jobs/{jobId}/cancel`：取消排队中或运行中的任务。
- `GET /api/research/jobs/{jobId}/result`：任务完成后读取研究 run 详情。

## 示例

```json
{
  "jobType": "portfolio_backtest",
  "runId": "002-repair-example-001",
  "baseContextRunId": "002-repair-indicator-ablate-ma-001",
  "moneyflowCacheRunId": "002-moneyflow-cache-mainline-001",
  "params": {
    "crossSectionScoreWeights": {
      "moneyflowMarketSurgeQuality": 0.25
    },
    "entryRiskFilter": {
      "maxGapPct": 0.06,
      "maxEntryRangePct": 0.08
    }
  }
}
```

`trade_delta` 示例：

```json
{
  "jobType": "trade_delta",
  "runId": "002-delta-example-001",
  "baselineRun": "002-repair-indicator-ablate-ma-001",
  "candidateRun": "002-repair-moneyflow-surge-quality-001"
}
```

## 约束

- `runId` 不允许复用；已存在目录会返回 `409`，避免覆盖既有研究证据。
- `context`、`moneyflowCache`、`conceptCache` 只能指向项目目录内的已存在文件。
- API 不执行任意 Python 代码，只接受白名单参数并转换为现有研究脚本参数。
- 只改参数和权重时不需要重启 Docker；新增因子算法或修改后端接口代码后，需要 `docker compose restart api` 让服务加载新代码，通常不需要 rebuild。
- 当前 job 状态存于 API 进程内存；重启 API 后历史 job 状态会清空，但已经落盘的 `docs/research/runs/<runId>/` 结果仍可通过 `/api/research/runs/{runId}` 读取。
