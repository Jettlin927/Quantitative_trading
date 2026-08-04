# 代码地图

本页只记录稳定职责。接手任务时仍须现场读取目标 Issue、分支、运行环境和生产事实。

## 根入口

- `docker-compose.yml`：本地与服务器 Compose 的统一基础入口。
- `docker-compose.test.yml`：隔离 PostgreSQL 集成测试。
- `AGENTS.md`：长期规则、安全红线和验证入口。
- `CONTEXT.md`：统一领域语言。
- `操作日志.md`：append-only 阶段事实，不承担路线图。

## 后端

- `backend/app/main.py`：API 路由、持久任务入队和兼容入口。
- `backend/app/models.py`：SQLAlchemy schema 合同；生产演进必须走 Alembic。
- `backend/app/schemas.py`：API 输入输出 schema。
- `backend/app/sync_worker.py`：数据任务租约、心跳、退避和恢复。
- `backend/app/personal_analysis_worker.py`：与正式研究隔离的个人 AI 分析队列；只有该进程可读取 DeepSeek secret file。
- `backend/app/personal_workspace/`：个人投研工作台（真实美股持仓、观察规则、AI 分析、个人记录）；`agent/` 子包是 tool-use agent 运行时（多轮工具循环、DeepSeek agent 适配器、持仓/K线/新闻工具、技能），与单发冻结证据路径并行共存，通过 `PERSONAL_ANALYSIS_MODE=agent` 启用。
- `backend/app/data_quality/`：研究范围质量、canonical 输入快照和质量运行。
- `backend/app/quant_research/`：无券商副作用的研究协议、模拟、指标、复现和报告统计。
- `backend/app/research_analytics.py`：把同一评价版本的 canonical 工件或已校验历史冻结来源投影为只读指标与图表数据；不计算新研究结论。
- `backend/app/strategy_results.py`：现有结果的只读兼容投影。
- `backend/migrations/`：Alembic revision；不得由 API 启动隐式执行生产 migration。

## 前端与脚本

- `frontend/`：只读产品界面，不承担研究计算或批准逻辑。
- `scripts/ops/`：部署、同步、巡检、备份与隔离验证入口。
- `scripts/research/`：正式研究 CLI 与报告组装；不得复制研究核心公式。
- `my_quant/us_research/`：美股 sample/实验夹具，不是研究级实际市场数据。
- `backend/app/us_experiment.py`：美股免费实验目录、yfinance 主日线、AKShare 独立校验、覆盖投影与只读查询；不得把实验状态提升为正式研究资格。

## 工件与报告

- `outputs/research-runs/`：被 Git 忽略的 canonical 运行工件。
- `docs/research/strategy-results/`：保持兼容路径的可提交报告投影和只读清单。
- `docs/acceptance/`：带日期、不可改写的生产迁移与验收证据。

具体研究必须先读[策略画像与评价规范](../research/contracts/strategy-evaluation-standard.md)和[量化研究可信合同](../research/contracts/quant-foundation-trust-contract.md)。
