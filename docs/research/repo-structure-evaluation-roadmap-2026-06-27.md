# 仓库结构与策略评估前端收拢报告（2026-06-27）

## 结论

本次先完成低风险第一阶段：把前端从“同步/运行回测工作台”改为“策略评估驾驶舱”。前端现在只读取后端已经落盘的策略证据、指标、阶段闸门和证据文件，不再提供回测执行入口。

仓库结构建议按三条主线收拢：

1. `backend/` 成为策略引擎、数据读取、指标聚合和 API 的唯一后端边界。
2. `frontend/` 只负责呈现策略是否达标、为什么没达标、证据来自哪里。
3. 美股作为独立主线进入后端 DB，但真实持仓导入、持仓历史落库和 schema 初始化需要单独确认。

## 本次已完成

- 重写 `frontend/src/main.jsx`：
  - 默认优先拉取 `/api/research/dashboard?run_limit=160`，并以 `/api/health`、`/api/research/overview`、`/api/strategy-evaluations`、`/api/strategy-lifecycle`、`/api/research/runs` 作为降级来源。
  - 第一屏展示主线策略结论、当前阶段、研究 run、核心指标、净值/回撤、三段验证闸门、硬门槛矩阵、策略规则摘要、尾部样本和证据文件。
  - 移除前端的同步日线、同步基本面、运行单票回测、运行全市场回测等执行型按钮。
- 重写 `frontend/src/styles.css`：
  - 采用高信息密度的工业化风控终端风格。
  - 支持桌面和窄屏响应式布局，不再依赖固定 `min-width`。
- 更新 `frontend/index.html`：
  - 页面标题改为 `策略评估驾驶舱`。
  - 增加空 favicon，避免浏览器自动请求 `/favicon.ico` 产生 404 控制台错误。

## `my_quant/strategy_research` 迁移判断

不建议把整个 `my_quant/strategy_research/` 直接塞进 `backend/app/`。当前目录混合了四类内容：

- 可迁移为后端引擎的代码：`experiment/*.py`、B1 组合研究逻辑、指标计算、报告摘要函数。
- 需要保留为证据档案的产物：`results/*.csv`、`results/*.json`、`results/*.md`、`web_report/`。
- 不应进入后端包的缓存：`data_cache/`、外部下载缓存、临时日志。
- 自动化入口：`automation/run_b1_daily.sh` 和 plist，更适合作为本地任务，不应成为 FastAPI app 包的一部分。

建议第二阶段迁移路径：

```text
backend/app/research_engine/
  __init__.py
  metrics.py
  b1_trend_pullback.py
  satellite.py
  validation.py

backend/app/research_evidence.py
  只负责读取 docs/research/runs、docs/research/backtest-reports 和已落盘 manifest。

docs/research/backtest-reports/
  继续保存长期可复盘证据。

my_quant/strategy_research/
  逐步缩成兼容 CLI、历史产物和本地自动化壳，不再承载新的后端业务逻辑。
```

2026-06-27 进展：已新增 `backend/app/research_engine/metrics.py`，并把 `my_quant/strategy_research/experiment/metrics.py` 改成兼容包装器。旧 `my_quant` import 路径保留，但核心 NAV 指标、最大回撤、Sharpe、Beta、Calmar、Sortino 计算已由后端边界提供。

迁移前必须先跑当前测试，并处理 import 路径兼容：

```bash
.venv/bin/python -m unittest discover my_quant/strategy_research/tests -v
.venv/bin/python -m unittest backend.tests.test_b1_strategy -v
```

## 美股 DB 主线

用户目标是正确的：美股行情、观察池、持仓历史、持仓快照应该由后端 DB 呈现，而不是长期散落在 CSV/HTML 中。

建议第三阶段新增非破坏性表，不导入真实持仓数据：

```text
assets
asset_daily_prices
asset_snapshots
watchlist_items
portfolio_accounts
portfolio_positions
portfolio_transactions
strategy_evaluation_runs
strategy_evaluation_metrics
```

第一版 API 目标：

- `GET /api/assets?market=US`
- `GET /api/us/watchlist`
- `GET /api/us/portfolio/snapshots`
- `GET /api/us/portfolio/history`
- `GET /api/strategy-evaluations`

红线：

- 不连接真实券商。
- 不提交真实持仓、成交记录或账户文件。
- 不自动下单，不输出真实交易指令。
- 添加持久化表和导入真实持仓前，需要用户确认备份和数据边界。

## 三段回测评估流程

用户要求的三段回测应作为策略评估闸门，而不是前端按钮：

| 阶段 | 窗口 | 作用 | 是否负责判定策略 |
| --- | --- | --- | --- |
| 第一轮 | `2020-01-01` 到 `2024-12-31` | 主评估窗口；策略必须先在这里达标 | 是 |
| 第二轮 | `2025-01-01` 到当前日期 | 样本外复核；通过后才讨论当前适用性 | 是 |
| 最终观察 | 标志性熊市压力段 | 观察极端环境下的韧性、流动性和纪律 | 否 |

与当前阶段制度的关系：

- 当前活跃阶段已切换为 `001-research-reset`。
- 新三段流程不应改名、跳级或覆盖 `docs/research/stages/` 的长期阶段制度。
- 它应成为每个候选策略的 `evaluation_windows` 字段，并由后端聚合后给前端展示。

建议后端证据结构：

```json
{
  "strategyId": "new-strategy-id",
  "evaluationWindows": [
    {
      "id": "train-2020-2024",
      "startDate": "2020-01-01",
      "endDate": "2024-12-31",
      "role": "qualification",
      "status": "pass|fail|missing"
    },
    {
      "id": "oos-2025-now",
      "startDate": "2025-01-01",
      "endDate": "2026-06-27",
      "role": "out_of_sample",
      "status": "pass|fail|missing"
    },
    {
      "id": "bear-market-observe",
      "role": "observation_only",
      "status": "observed|missing"
    }
  ]
}
```

## 验证记录

已运行：

```bash
docker compose run --rm frontend npm run build
git diff --check -- frontend/index.html frontend/src/main.jsx frontend/src/styles.css
curl -fsS http://localhost:15173/
curl -fsS http://localhost:18000/api/health
```

浏览器渲染复查：

- 页面标题：`策略评估驾驶舱`。
- H1：`策略评估驾驶舱`。
- 已渲染核心指标：`年化收益`。
- 已渲染三段验证闸门。
- 已渲染证据文件与后端来源。
- 控制台错误：无。

## 下一阶段建议

1. 先确认是否允许新增后端 `research_engine` 包，并把 `my_quant/strategy_research/experiment` 中的可复用逻辑迁过去。
2. 再确认是否允许新增美股 DB 表；确认后先做空表和 sample 导入，不碰真实持仓。
3. 最后把三段评估窗口固化进后端 `/api/strategy-evaluations`，前端只读这个聚合结果。
