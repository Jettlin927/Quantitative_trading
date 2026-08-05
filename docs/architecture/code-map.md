# 代码地图

本页记录稳定职责与代码入口。接手任务时仍须现场读取目标 Issue、分支、运行环境
和生产事实；容器实例、提交、端口占用和部署状态不在这里冻结。

整体拓扑、研究生命周期与数据流见 [系统流程](system-flow.md)；仓库级规则、安全
红线与验证入口以根 [AGENTS.md](../../AGENTS.md) 为准。

## 根入口

- `docker-compose.yml`：本地与服务器 Compose 的统一基础入口（db/api/worker/
  research-worker/frontend）。
- `docker-compose.test.yml`：隔离 PostgreSQL 集成测试。
- `docker-compose.personal.yml`：个人工作台私密覆盖（DeepSeek secret、持仓账本）。
- `AGENTS.md`：长期规则、安全红线和验证入口。
- `AGENTS.local.md`：本机凭据与 Codex 入口约定（仅当前 Mac）。
- `CONTEXT.md`：统一领域语言。
- `README.md`：面向人的工程概览与快速开始。

## 后端（`backend/app/`）

按职责分组；`models.py` 是应用 schema 合同，生产演进必须走 `backend/migrations/`
的 Alembic revision，不得由 API 隐式执行 migration。

### 应用入口与基础设施

- `main.py`：FastAPI 路由、持久任务入队和兼容入口；不在请求进程执行长同步。
- `database.py`：Session 与 schema revision 校验。
- `models.py`：SQLAlchemy 模型，schema 合同。
- `schemas.py`：API 输入输出 schema。
- `json_safety.py`：API 数值 JSON-safe 化（NaN/Infinity 转 null）。

### 市场数据同步

- `sync_worker.py`：数据任务租约、心跳、退避和恢复（worker 进程入口）。
- `market_data_ingestion.py`：A 股 Tushare 实际市场数据摄取（行情、基本面、列表、
  交易日历、复权因子、指数、基金、行业）。
- `legacy_market_data_ingestion.py`：早期/历史数据摄取路径。
- `tushare_client.py`：Tushare token 解析与客户端封装。
- `data_quality/`：研究范围质量、canonical 输入快照和质量运行（`contracts.py`、
  `rules.py`、`runner.py`）。

### 研究治理（计划、批准、编排、执行）

- `research_plan.py`：冻结研究计划解析与校验（`research-plan/v3`）、规范化 JSON
  与 SHA-256 指纹。
- `research_orchestration.py`：编排器——冻结计划校验、人工批准校验、租约队列、
  work item 状态机（待批准/已批准/排队/运行中/停止中/发布中/已发布/受阻）。
- `github_research.py`：GitHub 研究 Issue 投影与标签收敛；只暴露 Issue、评论、
  标签接口，不提供代码推送或设置接口。
- `research_worker.py`：正式研究队列 worker（research-worker 进程入口），轮询
  GitHub Issue 并驱动编排；含研究停止、恢复信号处理。
- `quant_research/`：无券商副作用的研究协议、模拟、指标、复现与报告统计（详见
  下方子节）。
- `work_coordination.py`：跨进程重型任务全局锁（advisory lock）。

### 研究执行内核（`backend/app/quant_research/`）

- `strategy_registry.py`：策略静态登记与解析（`resolve_strategy_definition`）。
- `run_config.py`：运行配置校验、canonical JSON、SHA-256、参数邻域、评价/通过策略。
- `runner.py`：研究运行执行器。
- `snapshot.py` / `dataset.py` / `universe.py` / `features.py` / `calendar.py`：
  point-in-time 输入快照、数据集、universe、特征与交易日历。
- `execution.py` / `portfolio.py` / `allocation.py` / `baselines.py` / `metrics.py` /
  `risk.py`：下一交易日执行、组合、配置、匹配基准、指标与风险统计。
- `evaluation.py` / `research_evaluation.py`：研究评价与通过门槛（DSR/PBO 等）。
- `artifacts.py` / `manifest.py` / `repository.py`：canonical 工件、清单与存储。
- `reporting.py`：报告统计口径；渲染脚本只组装，不复制公式。
- `readiness.py` / `validation.py`：研究就绪度与校验。
- 策略实现：`a_share_b1_trend_pullback.py`、`etf_volatility_managed.py`、
  `etf_trend_baseline.py`、`a_share_price_baseline.py`。

### 评价、发布与归档

- `research_publication.py`：把冻结评价合同发布到工件、Issue 与只读投影；含前端
  同源读回验证（`_validate_readback_base_url`）与恢复步骤。
- `research_catalog.py`：研究目录只读投影（策略档案、正式研究、评价、发布、提案）。
- `official_evidence/`：官方证据资格与来源授权（`contracts.py`、`adapters.py`、
  `policy.py`）——online observation / traceable history / formal research 资格。
- `historical_publication_issues.py`：历史发布 Issue 映射合同
  （`configs/research/historical_publication_issues_v1.json`）。
- `research_history_migration.py`：研究治理合同建立前的历史运行/证据映射。

### 美股边界与个人工作台

- `us_experiment.py`：美股免费实验目录（yfinance 主日线、AKShare 独立校验、
  覆盖投影、只读查询）；`researchEligible=false` 固定，不得提升为正式研究资格。
- `us_research.py`：美股 sample 只读概览与导入预览。
- `personal_workspace/`：个人投研工作台（真实美股持仓、观察规则、AI 分析、个人
  记录）；`agent/` 子包是 tool-use agent 运行时（多轮工具循环、DeepSeek agent
  适配器、持仓/K线/新闻工具、技能），经 `PERSONAL_ANALYSIS_MODE=agent` 启用。
- `personal_analysis_worker.py`：与正式研究隔离的个人 AI 分析队列；只有该进程
  可读取 DeepSeek secret file。
- `market_observation/`：市场观察（Alpaca 数据源、来源授权与健康度、追加式授权
  注册表）。

### 只读投影与兼容入口

- `research_analytics.py`：把同一评价版本的 canonical 工件投影为只读指标与图表
  数据；不计算新研究结论。
- `strategy_results.py`：既有策略结果的只读兼容投影。
- `ma_strategy_stats.py` / `value_sector_strategy.py`：历史策略统计与价值质量
  行业强度策略（兼容入口）。

## 前端（`frontend/src/`）

只读产品界面，不承担研究计算或批准逻辑。路由定义在 `main.jsx`。

| 路由 | 视图 | 职责 |
| --- | --- | --- |
| `/today` | `PersonalTodayView.jsx` | 今日工作台：持仓事项优先 |
| `/portfolio` | `PortfolioView.jsx` | 我的持仓：私有手工账本、每日权益 |
| `/markets/us` | `InstrumentWorkspaceView.jsx` + `MarketChart.jsx` | 市场与标的：标的与证据 |
| `/markets/a-share` | `AShareDataView.jsx` | A 股数据浏览 |
| `/rules` | `RulesView.jsx` | 规则与策略：确定性四态 |
| `/research` | `ResearchCockpitView.jsx` | 研究驾驶舱：正式研究隔离 |
| `/records` | `RecordsView.jsx` | 研究记录：不可变版本 |
| `/system` | `OperationsView.jsx` | 数据与系统：授权与健康 |
| 个人 AI 分析 | `AnalysisWorkspaceView.jsx` | 个人 AI 分析工作区 |

支撑模块：`readAdapter.js`（浏览器读取适配）、`personalJourneyClient.js`（个人
旅程/持仓 API 客户端）、`equityChartAdapter.js` / `marketChartAdapter.js`（图表
适配）、`stockResearch.js`（股票研究 hook）、`viewSupport.jsx`（共享 UI 组件与
状态翻译）、`styles.css`。

## 脚本（`scripts/`）

- `scripts/ops/`：部署、同步、巡检、备份与隔离验证。
  - `test_postgres_integration.sh`、`test_frontend_production_image.sh`：验证门禁。
  - `deploy_server.sh` / `deploy_remote.sh` / `inspect_server_docker.sh` /
    `inspect_remote_docker.sh`：部署与远端巡检。
  - `bootstrap_new_server_runtime.sh`、`postgres_roles/`：生产运行时与角色。
  - `install_daily_sync_cron.sh` / `sync_today_market_data.sh`：A 股日常同步。
  - `install_us_experiment_cron.sh` / `sync_us_experiment_daily.sh` /
    `backfill_us_experiment.py`：美股实验同步与回填。
  - `backfill_a_share_history.py`、`backfill_equity_history.py`：历史回填。
  - `verify_personal_read_latency.py`、`audit_postgres_indexes.sql`：巡检。
- `scripts/research/`：正式研究 CLI 与报告组装；不得复制研究核心公式。
  - `run_quant_research.py`、`reproduce_quant_research.py`、`audit_quant_research.py`。
  - `check_data_quality.py`、`report_evidence.py`。
  - `publish_research_evaluation.py`、`migrate_research_history.py`、
    `register_historical_issue_mapping.py`。
  - `render_*.py`：各策略报告渲染。
  - `verify_entrypoint_inventory.py`：研究入口清单核验。

## 配置、数据与工件

- `configs/research/`：研究入口清单、历史迁移与历史发布 Issue 合同（冻结 JSON）。
- `my_quant/`：sample/实验夹具（`us_research/` 美股 sample 观察池与快照、
  `us_holdings/`）；不是研究级实际市场数据。
- `outputs/research-runs/`：被 Git 忽略的 canonical 运行工件。
- `docs/research/strategy-results/`：保持兼容路径的可提交报告投影和只读清单。
- `docs/acceptance/`：带日期、不可改写的生产迁移与验收证据。

## 测试

- `backend/tests/`：后端单元与集成测试。
- `frontend/src/*.test.jsx`：前端 Vitest 测试。
- 验证方法与门禁见 [变更验证](../agents/validation.md)。
