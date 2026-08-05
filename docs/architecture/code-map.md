# 代码地图

本页记录美股优先工作台的稳定职责与代码入口。生产提交、容器状态、表行数和数据新鲜度必须现场核验。整体流程见[系统流程](system-flow.md)，退役边界见 [ADR 0010](../adr/0010-us-first-workbench-and-retired-legacy-data-paths.md)。

## 根入口

- `docker-compose.yml`：公共基础拓扑（db/api/research-worker/frontend）。
- `docker-compose.personal.yml`：个人工作台私有覆盖（私有 API 配置、Alpaca/DeepSeek secret、personal-analysis-worker）。
- `docker-compose.test.yml`：隔离 PostgreSQL schema 与合成夹具测试。
- `AGENTS.md` / `CONTEXT.md`：长期规则、安全红线与统一领域语言。
- `README.md`：工程概览和本地 schema-only 启动入口。

## 后端（`backend/app/`）

### 应用与 schema

- `main.py`：FastAPI 根应用、健康、研究目录/发布投影和数据质量接口；不执行市场数据长同步。
- `database.py`：Session 与 schema revision 校验。
- `models.py`：公共 schema 合同。部分退役市场模型继续与 Alembic 历史对齐，不代表活动 API。
- `schemas.py`：公共研究 API 输入输出 schema。
- `json_safety.py`：将 NaN/Infinity 收敛为 JSON-safe 值。
- `backend/migrations/`：唯一 schema 演进入口；历史 revision 不因运行时退役而改写。

### 美股个人工作台（`personal_workspace/`）

- `router.py`：今日、持仓、标的、规则、分析和 synthetic trace API；缺少私有配置时 fail-closed。
- `runtime.py`：组装私有存储、Alpaca 市场读取、规则、分析和安全入口。
- `portfolio.py`：手工持仓、价格观察、权益快照与已实现交易事实。
- `instrument.py` / `rules.py`：标的工作台与确定性观察规则。
- `analysis.py` / `agent/`：个人分析草稿、运行及 tool-use agent。
- `journey.py` / `persistence.py` / `synthetic.py`：今日旅程投影、私有持久化与不依赖真实数据的合成 tracer。
- `security.py` / `crypto.py`：gateway、Origin、幂等与私有字段加密。
- `personal_analysis_worker.py`：个人 AI 分析队列；只有私有覆盖中的该进程可读取 DeepSeek secret。

个人不可变记录版本链路已删除；`private_workbench` 中的历史表仍由 Alembic revision 保留。

### 市场观察与证据

- `market_observation/`：Alpaca 市场数据适配、用途授权、来源健康与追加式授权注册表。
- `official_evidence/`：online observation、traceable history 与 formal research 的证据资格。
- `data_quality/`：研究质量运行、规则与 readiness。历史市场 scope 可用于旧研究读回和 schema 测试，不构成数据拉取入口。

### 正式研究治理

- `research_plan.py`：冻结计划 `research-plan/v3` 的解析、校验和 SHA-256 指纹。
- `research_orchestration.py`：人工批准校验、租约队列和编排状态机。
- `github_research.py`：GitHub Issue、评论和标签的最小控制面。
- `research_worker.py`：正式研究队列，受 `research-automation` profile 与人工批准门控制。
- `work_coordination.py`：跨进程重型任务 advisory lock。

### 研究执行内核（`quant_research/`）

- `strategy_registry.py`：当前静态登记的 ETF 策略定义。
- `run_config.py` / `runner.py`：冻结运行配置、执行和检查点。
- `snapshot.py` / `dataset.py` / `universe.py` / `features.py` / `calendar.py`：point-in-time 输入、数据集、universe、特征和交易日历。
- `execution.py` / `portfolio.py` / `allocation.py` / `baselines.py` / `metrics.py` / `risk.py`：执行、组合、基准和统计口径。
- `evaluation.py` / `research_evaluation.py`：结构化研究评价与门禁。
- `artifacts.py` / `manifest.py` / `repository.py` / `reporting.py`：canonical 工件、清单、存储和报告统计。
- `readiness.py` / `validation.py`：研究就绪度与合同验证。

历史 A 股策略源文件和活动登记已退役；既有 A 股研究工件和历史发布投影继续保留。

### 评价、发布与归档

- `research_publication.py`：不可覆盖的一致发布与恢复。
- `research_catalog.py`：策略档案、正式研究、评价、发布和提案的只读投影。
- `research_analytics.py`：从同一评价版本的 canonical 工件读取指标和图表；其中的历史策略分支只服务既有发布读回。
- `research_history_migration.py` / `historical_publication_issues.py`：治理合同建立前的历史来源映射。

## 前端（`frontend/src/`）

路由定义在 `main.jsx`，前端只做读写编排和投影，不承担研究计算或批准逻辑。

| 路由 | 视图 | 职责 |
| --- | --- | --- |
| `/today` | `PersonalTodayView.jsx` | 今日持仓事项与旅程入口 |
| `/portfolio` | `PortfolioView.jsx` | 私有手工持仓与每日权益 |
| `/markets/us` | `InstrumentWorkspaceView.jsx` + `MarketChart.jsx` | 美股标的与证据 |
| `/rules` | `RulesView.jsx` | 确定性观察规则 |
| `/system` | `OperationsView.jsx` | 来源授权、schema 与服务健康 |
| 个人 AI 分析 | `AnalysisWorkspaceView.jsx` | 分析草稿、确认、运行与事件 |

已无 `/markets/a-share`、`/records`、`/research` 或旧美股实验页面。旧研究驾驶舱只展示 A 股历史策略，已从产品入口和前端代码退役；正式研究后端合同继续作为隔离的治理基础设施保留。支撑模块包括 `readAdapter.js`、`personalJourneyClient.js`、图表 adapter、`viewSupport.jsx` 与 `styles.css`。

## 脚本与配置

- `scripts/ops/`：部署、巡检、PostgreSQL 角色、隔离测试和个人读延迟核验；不含 A 股或旧美股实验 cron/回填入口。
- `scripts/research/`：正式研究运行、复现、质量、发布和历史迁移 CLI。
- `configs/research-entrypoints-v1.json`：当前研究入口清单。
- `configs/research/`：历史迁移与发布 Issue 的冻结合同。
- `outputs/research-runs/`：被 Git 忽略的 canonical 工件。
- `docs/research/strategy-results/`：既有历史研究报告投影，不是当前策略执行入口。

## 测试边界

- `backend/tests/`：单元、合同和 PostgreSQL 集成测试。
- `frontend/src/*.test.*`：前端 Vitest 测试。
- schema-only 测试可创建包括退役历史表在内的完整 Alembic schema，并只写合成数据；不得依赖 Tushare、AKShare、yfinance、真实 A 股数据或真实个人数据。
- 验证门禁见[变更验证](../agents/validation.md)。
