# 代码地图

本页只记录个人投资工作台的当前职责与入口。生产提交、容器、表行数和数据新鲜度
必须现场核验。整体流程见[系统流程](system-flow.md)。

## 根入口

- `docker-compose.yml`：PostgreSQL、API 和前端公共拓扑。
- `docker-compose.personal.yml`：私有 API 配置、secret 和 `personal-analysis-worker`。
- `docker-compose.test.yml`：隔离 PostgreSQL 与合成夹具测试。
- `AGENTS.md` / `CONTEXT.md`：仓库规则与统一领域语言。
- `README.md`：工程概览和本地启动入口。

## 后端

### 应用与 schema（`backend/app/`）

- `main.py`：FastAPI 根应用、健康检查与路由装配；长任务不在请求进程内执行。
- `database.py`：Session 与 schema revision 校验。
- `models.py`：应用 schema 合同；遗留模型继续与 Alembic 历史对齐，不代表当前能力。
- `json_safety.py`：把 `NaN`/`Infinity` 收敛为 JSON-safe 值。
- `backend/migrations/`：唯一 schema 演进入口；不改写历史 revision。

### 私有工作台（`personal_workspace/`）

- `router.py`：今日、持仓、标的、规则、分析和 synthetic trace API；缺少配置时 fail-closed。
- `runtime.py`：组装私有存储、市场读取、规则、分析和安全入口。
- `portfolio.py`：手工持仓、现金、权益快照与已实现交易事实。
- `instrument.py`：美股标的工作区投影。
- `rules.py` / `rule_automation.py`：确定性观察规则及 XNYS 交易时段自动评估。
- `journey.py` / `persistence.py`：今日事项聚合与私有持久化。
- `analysis.py` / `agent/`：AI 分析草稿、统一 Runtime 和受控工具调用。
- `automatic_briefing.py` / `automatic_briefing_store.py`：固定证据配方、XNYS 自动简报触发、幂等调用与每日费用账本。
- `market_runtime.py`：把获准的 Alpaca reader 注入私有领域服务。
- `security.py` / `crypto.py`：gateway、Origin、幂等与私有字段加密。
- `synthetic.py`：不依赖真实数据的合成 tracer。

### Worker 与市场观察

- `personal_analysis_worker.py`：AI 分析队列，以及美东盘前、结构化事件盘中、盘后自动简报与持仓规则调度；
  只有私有覆盖中的该进程可读取模型 secret。
- `market_observation/`：Alpaca adapter、用途授权、来源健康与追加式授权注册表。

### MCP 与 DeepSeek 工具边界

- `personal_workspace/mcp_gateway.py`：单 actor、exact-five、固定只读权限、限额与审计的
  MCP 安全模块，只调用唯一 `DomainToolRegistry`。
- `personal_workspace/mcp_server.py`：现有 `stdio` 协议 adapter；只用于非生产和测试，
  不是生产拓扑。
- 远端生产目标运行在 `quant-trading-prod`：本机客户端经 SSH 隧道访问服务器 loopback
  上的单一 `/mcp` Streamable HTTP adapter；ASGI adapter 已实现，但尚未加入 Compose、
  部署或启用。
  它必须用不可由客户端覆盖的服务端入口上下文构造 gateway：审计 channel 为
  `mcp_streamable_http`，领域 purpose 为 `mcp_remote_read`；现有 `mcp_stdio` 硬编码只属于
  stdio 行为，不能原样复用为 HTTP 入口。
- `personal_workspace/agent/completion_runtime.py` 与 `client_tool_runtime.py`：DeepSeek
  Chat Completions `tool_calls` 的内部 adapter，直接调用同一个 `DomainToolRegistry`，不导入
  或调用 MCP。MCP 与 DeepSeek 不共享 transport、session、token、provider 或 secret。

## 前端（`frontend/src/`）

路由和一级导航定义在 `main.jsx`。前端只做交互和投影，不复制持仓、权益、规则或权限计算。

| 路由 | 视图 | 职责 |
| --- | --- | --- |
| `/today` | `PersonalTodayView.jsx` | 当前持仓注意事项与旅程入口 |
| `/portfolio` | `PortfolioView.jsx` | 私有手工持仓、成交与每日权益 |
| `/markets/us/:symbol` | `InstrumentWorkspaceView.jsx` + `MarketChart.jsx` | 美股标的与行情证据 |
| 个人 AI 分析 | `AnalysisWorkspaceView.jsx` | 草稿、外发预览、确认、运行与事件 |
| `/system` | `OperationsView.jsx` | 来源授权、schema 与服务健康 |

旧 A 股、规则独立页、研究页和策略页只允许重定向到当前入口，不恢复产品界面。
客户端访问集中在 `personalJourneyClient.js` 和 `readAdapter.js`。

## 脚本与配置

- `scripts/ops/`：部署、巡检、PostgreSQL 角色、隔离测试和读延迟核验。
- `.env.example`：可发现的配置键；真实值只存在于受保护环境。
- `docs/operations/`：部署、安全访问、secret 和数据库角色合同。

研究 API、研究 Worker、`scripts/research/`、`backend/app/data_quality/` 与
`backend/app/quant_research/` 及其运行支持代码已物理删除。仍保留 `configs/research*`、
历史模型（含 `data_snapshots` 等研究审计链表）、migration 与 `docs/research/` 文档作为
遗留兼容或审计资产，不是当前入口；继续删除前仍须按 ADR 0011 审计现役复用、生产
数据和外部依赖。

## 测试边界

- `backend/tests/`：单元、合同和 PostgreSQL 集成测试。
- `frontend/src/*.test.*`：前端 Vitest 测试。
- 测试只使用合成数据，不依赖真实个人数据、真实账户或未显式授权的网络来源。
- 验证门禁见[变更验证](../agents/validation.md)。
