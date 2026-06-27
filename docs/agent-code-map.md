# Agent Code Map

这份文档给后续 AI Agent 快速定位代码用。它不是产品说明，而是“从目标到代码位置”的导航图。项目主管规则仍以根目录 `AGENTS.md` 为准。

如果是接手当前分支、PR 或阶段性验收，先看 `docs/agent-handoff.md`；这里主要负责代码和文档位置导航。

## 一句话架构

本仓库是本地量化研究工作台：React/Vite 前端负责人机交互，FastAPI 后端负责数据同步、指标计算、策略回测、AI/本地复盘，PostgreSQL 负责持久化 Tushare 行情、基本面、同步记录和自选标的池。

## 四区导航

- 美股操作层：`my_quant/us_research/`，已包含 sample 持仓、观察池、yfinance 快照脚本、HTML/Markdown 操作报告和规则证据引用入口。
- A 股数据沙盘：`backend/app/`、`docker-compose.yml`、PostgreSQL volume 和 `docs/research/a-share-data/`，继续服务 Tushare 同步、A 股研究池和大样本验证。
- 策略思想库：`docs/research/strategy-lab/`，已放“不追高”“止跌后加仓”“同因子杠杆预算”等规则卡片和负证据入口。
- 回测证据档案：`docs/research/backtest-reports/`、`docs/research/runs/`、`my_quant/strategy_research/results/`、`my_quant/strategy_research/web_report/`。

```text
人类/AI Agent
  -> frontend/ React 工作台，或直接调用 http://localhost:18000 API
  -> backend/app/main.py FastAPI 路由
  -> backend/app/backtest_engine.py 策略与指标计算
  -> PostgreSQL 本地数据：stocks / stock_daily_bars / stock_daily_basic / stock_financial_indicators / stock_pools
  -> 可选 DeepSeek：策略复盘与质量诊断总结，失败时降级本地规则
```

## 启动入口

- `docker-compose.yml`：三容器入口，服务名是 `db`、`api`、`frontend`。
- `启动回测系统.cmd`：日常启动，只应 `docker compose up -d`。
- `重新构建并启动回测系统.cmd`：改 Dockerfile、依赖或代码后使用。
- `停止回测系统.cmd`：停止服务。
- `README.md`：面向人类的使用说明和截图。
- `AGENTS.md`：面向 AI Agent 的项目主管规则。
- `docs/agent-handoff.md`：面向后续 Agent 的当前 PR、活跃阶段、验证命令和本机坑点交接。

## 后端地图

- `backend/app/database.py`
  - SQLAlchemy engine、`SessionLocal`、`Base`、`get_db()`。
  - 默认本地 `DATABASE_URL` 只作开发兜底；容器内由 Compose 注入。

- `backend/app/models.py`
  - `Stock`：A 股基础信息。
  - `StockDailyBar`：日线 OHLCV，`ts_code + trade_date` 唯一。
  - `StockDailyBasic`：Tushare `daily_basic`，估值、换手率、市值等。
  - `StockFinancialIndicator`：Tushare `fina_indicator`，ROE、毛利率、负债率、增长率等。
  - `StockPool` / `StockPoolMember`：自选标的池和成员。
  - `Asset`：美股/ETF sample 资产主数据，`market + symbol` 与 `natural_key` 唯一。
  - `AssetDailyPrice`：美股 sample 行情快照，`asset_natural_key + trade_date` 唯一。
  - `WatchlistItem`：美股 sample 观察池，`watchlist_name + asset_natural_key` 唯一。
  - `PortfolioSnapshot`：美股 sample 持仓快照，`snapshot_id` 唯一，`holdings` 为 JSON。
  - `DataSyncRun`：同步记录，用于进度与复盘。

- `backend/app/schemas.py`
  - Pydantic 请求/响应模型。
  - 改 API 入参或前端依赖字段时，优先从这里确认契约。

- `backend/app/tushare_client.py`
  - Tushare 初始化、日期转换、Decimal 清洗。
  - 新增 Tushare 字段时，字段映射和空值处理应集中在后端。

- `backend/app/backtest_engine.py`
  - `run_backtest()`：单票回测主入口。
  - `enrich_rows()`：MA、BOLL、MACD、RSI、KDJ、ATR 等指标计算。
  - `should_enter()` / `common_factor_filters()`：入场模式与共同过滤条件。
  - `build_strategy_analysis()`：本地策略复盘基线。
  - `normalize_config()`：策略参数默认值。
  - 全市场批量验证会调用 `run_backtest(..., include_ai=False, include_details=False)`，避免每只股票都请求外部 AI 或传输完整明细。

- `backend/app/ai_client.py`
  - `analyze_with_deepseek()`：单票回测复盘的 DeepSeek 调用。
  - `analyze_stock_quality_with_deepseek()`：质量诊断总结。
  - 没有 token、调用失败或 JSON 不合格时，应保留本地规则兜底。

- `backend/app/us_research.py`
  - 美股 sample 数据的文件适配器，读取 `my_quant/us_research/` 下的 sample 观察池、sample 持仓、yfinance 快照和 sample 规则回测。
  - 文件 preview 仍只返回研究展示合同：`isSample=true`、`brokerConnected=false`、`realHoldingsImported=false`。
  - 持久化 schema 已确认后，sample 导入通过 `POST /api/us-research/import-sample` 写入 `assets`、`asset_daily_prices`、`watchlist_items`、`portfolio_snapshots`。
  - 不连接券商、不导入真实持仓；真实持仓导入前必须再次确认。

- `backend/app/strategy_lifecycle.py`
  - 策略生命周期索引读取器，读取 `docs/research/strategy-lifecycle.json`。
  - 用 `active`、`frozen`、`archived_negative_evidence` 控制主视图展示与证据保留。
  - 旧策略可以隐藏出主视图，但不应物理删除证据；删除只能在用户明确确认后做。

- `backend/app/research_engine/`
  - 从 `my_quant/strategy_research/experiment/` 迁出的可复用研究逻辑。
  - `metrics.py`：NAV 指标、最大回撤、Sharpe/Sortino/Calmar、beta 等纯计算函数。
  - `portfolio.py`：权重归一化、等权、风险平价、RAM Top-N 组合权重函数。
  - `reports.py`：Markdown 表格 fallback、候选选择、summary/manifest payload 纯构造函数；不负责写文件。
  - `validation.py`：rolling/anchored walk-forward 窗口生成；不负责执行回测。
  - `my_quant` 下的同名实验入口保留为兼容层，历史脚本和证据路径不应物理删除。

- `backend/app/main.py`
  - FastAPI 应用入口、所有路由、同步流程、全市场后台任务、质量诊断本地 agent。
  - 文件较长，先用函数名定位，不要盲目大改。

## 主要 API

- `GET /api/health`：健康检查。
- `GET /api/stocks`：按代码、名称、简称、行业搜索股票。
- `GET /api/stocks/screen`：选股池候选筛选，支持技术形态和排行口径。
- `GET/POST/DELETE /api/stock-pools...`：自选标的池 CRUD 和成员管理。
- `POST /api/tushare/sync-stock-basic`：同步 A 股基础列表。
- `POST /api/tushare/sync-daily`：同步单票日线。
- `POST /api/tushare/sync-market-daily`：按交易日补齐全市场日线。
- `POST /api/tushare/sync-fundamentals`：同步单票估值和财务指标。
- `POST /api/tushare/sync-market-daily-basic`：补齐全市场估值指标。
- `GET /api/tushare/sync-progress`：查询日线或估值覆盖进度。
- `GET /api/daily-bars`：读取单票日线并附带技术指标。
- `GET /api/stocks/{ts_code}/fundamentals`：读取单票基本面概览。
- `GET /api/news/trends`：读取财经热点源。
- `GET /api/stocks/{ts_code}/quality-analysis`：四类分析师质量诊断，可选 DeepSeek 汇总。
- `GET /api/research/dashboard`：策略评估工作台聚合入口，统一返回健康状态、研究 overview、主策略证据、三段评估、生命周期、美股 sample 数据、入库 preview 和研究 run 列表；前端优先读取这个接口。
- `GET /api/strategy-evaluations`：当前主策略的三段评估、指标和生命周期字段。
- `GET /api/strategy-lifecycle`：策略生命周期索引，区分主视图 active、冻结和归档负证据。
- `GET /api/us-research/overview`：读取美股 sample 观察池、sample 持仓、快照和规则回测，只读展示，不连接券商，不写 DB。
- `GET /api/us-research/db-overview`：从 DB 读取已持久化的美股 sample 资产、行情、观察池和持仓快照。
- `GET /api/us-research/import-preview`：把美股 sample 文件转换成 DB upsert 的目标表、行数和自然键；`writesEnabled=false`，只做预览。
- `POST /api/us-research/import-sample`：将 `my_quant/us_research/` 下 sample 数据 upsert 到 DB；不导入真实持仓，不连接券商。
- `POST /api/backtests/run`：单票数据库回测，默认可带 AI 复盘。
- `POST /api/backtests/market`：同步执行全市场或池内回测。
- `POST /api/backtests/market/jobs` 与 `GET /api/backtests/market/jobs/{job_id}`：后台回测任务和轮询进度。

## 前端地图

- `frontend/src/main.jsx`
  - 当前是 QuantConnect 式策略数据呈现页，包含状态、API 调用、页面组件和图表组件。
  - 顶部常量：`API_BASE`、`EXECUTABLE_STRATEGY_ID`、三段验证兜底窗口。
  - 关键 API：优先读取 `/api/research/dashboard`；失败时回落到 `/api/health`、`/api/research/overview`、`/api/strategies/executable/{id}`、`/api/strategy-evaluations`、`/api/strategy-lifecycle`、`/api/us-research/overview`、`/api/us-research/import-preview`、`/api/research/runs`。
  - 第一屏展示策略标题、指标横条、权益曲线、图表网格、概览指标表、滚动统计表和右侧证据栏；不提供回测执行入口。

- `frontend/src/styles.css`
  - 工业化风控终端视觉，维护高信息密度和可扫描性。
  - 改布局后要检查桌面宽度和关键表格、卡片、进度条是否挤压。

- `frontend/package.json`
  - `npm run lint`、`npm run typecheck`、`npm run build`。
  - 依赖包含 `react`、`vite`、`lightweight-charts`、`lucide-react`。

## 独立研究工作区地图

- `my_quant/`
  - 从 `xquant-beginner` 收拢来的独立研究工作区，依赖和运行方式与主 FastAPI/React 栈分开。
  - 接手说明看 `my_quant/README.md`，策略研究说明看 `my_quant/strategy_research/README.md`。

- `my_quant/us_research/`
  - 文件化美股操作层，只使用 sample 持仓和 sample 观察池，不读取真实券商导出。
  - `scripts/refresh_us_snapshot.py` 使用 yfinance 生成 `data/snapshots/us_snapshot_latest.{json,csv}`，字段包含 `fetched_at`、`source`、`is_stale` 和趋势指标。
  - `scripts/build_us_operations_report.py` 从快照生成 `reports/latest_us_operations.html` 和 `reports/latest_us_operations.md`，报告中的动作标签只作研究辅助。

- `my_quant/strategy_research/experiment/b1_trend_pullback.py`
  - B1 A 股趋势回调组合实验核心，包含 Tushare/AkShare 数据读取、候选排序、现实成交约束和组合回测。
  - `fetch_tushare_index_bars()` 会检查指数缓存最大日期，缓存不覆盖请求结束日时刷新，避免盘前预案读到半旧指数。

- `my_quant/strategy_research/experiment/metrics.py` / `strategies.py` / `reports.py`
  - 兼容入口，实际可复用指标、组合权重、报告 payload 逻辑已迁到 `backend/app/research_engine/`。
  - 后续新后端 API 应优先 import `backend.app.research_engine`，旧实验脚本可继续使用 `my_quant` 路径。

- `my_quant/strategy_research/experiment/validation.py`
  - `walk_forward_analysis` 仍留在旧实验区，因为它依赖 experiment-local `run_config` 和策略配置。
  - 纯窗口生成逻辑已迁到 `backend/app/research_engine/validation.py`，旧路径通过兼容调用继续可用。

- Kronos / RAM / 风险平价等历史实验脚本
  - 这些脚本保留为负证据和历史报告生成工具，不再列入当前策略清单。
  - Kronos 包装仍只评估预测路径斜率、预测收益和下行分位过滤，不连接券商、不产生真实交易动作。

## AI 科研闭环落点

后续让 AI 自动进化策略时，建议把每轮研究拆成以下可验证单元：

1. 假设文档
   - 写清楚市场假设、入场/出场变化、目标指标、失败条件。
   - 不确定时先放到 `docs/`，不要直接改核心规则。

2. 策略实现
   - 策略参数和默认值：`backend/app/backtest_engine.py` 的 `DEFAULT_CONFIG` / `normalize_config()`。
   - 指标：`enrich_rows()`。
   - 入场逻辑：`should_enter()`。
   - 出场、仓位、风控纪律：`run_backtest()`。
   - 请求契约：`backend/app/schemas.py`。
   - 前端控件：`frontend/src/main.jsx`。

3. 回测评估
   - 单票：`POST /api/backtests/run`。
   - 自选池或全市场：`POST /api/backtests/market/jobs`，再轮询 job。
   - 默认记录总收益、最大回撤、胜率、交易数、纪律评分、tested/skipped/failed、样本区间和参数。

4. 复盘反思
   - 代码阶段写 `操作日志.md`。
   - 若是完整研究轮次，新增 `docs/` 下的研究记录，避免只有 UI 截图没有可追溯结论。
   - 结论必须区分“数据支持”“数据不支持”“证据不足”。

## 常见改动路线

新增 Tushare 数据字段：

1. 查 Tushare 当前文档。
2. 改 `backend/app/models.py` 加字段或表。
3. 改 `backend/app/main.py` 的 record-to-row 与 upsert。
4. 改 `backend/app/schemas.py` 和前端展示。
5. 运行后端 py_compile，必要时运行一次小范围同步。

新增策略因子：

1. 在 `enrich_rows()` 增加指标，确保不用未来数据。
2. 在 `should_enter()` 或共同过滤器中接入。
3. 在 `normalize_config()` 设默认值。
4. 在 `schemas.py` 和 `frontend/src/main.jsx` 暴露参数。
5. 用单票和小样本市场回测验证，再扩大范围。

新增质量诊断 agent：

1. 先在 `backend/app/main.py` 生成本地证据和本地评分。
2. 再在 `backend/app/ai_client.py` 把证据交给 DeepSeek 汇总。
3. DeepSeek 只能总结证据，不应凭空补数据。
4. 前端展示必须标明本地规则、AI 成功或 AI 降级状态。

新增前端页面或面板：

1. 先读 `.codex/skills/frontend-design/SKILL.md`。
2. 保持第一屏为操作工作台。
3. 尽量复用现有按钮、面板、表格、进度条风格。
4. 改完运行前端 lint/typecheck/build，能启动时做浏览器截图验证。

## 验证命令

后端最小检查：

```powershell
python -m py_compile backend\app\database.py backend\app\models.py backend\app\schemas.py backend\app\backtest_engine.py backend\app\tushare_client.py backend\app\ai_client.py backend\app\main.py
```

按文件检查：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check-file.ps1 <改过的文件路径>
```

Compose 检查：

```powershell
docker compose config
```

前端构建：

```powershell
docker compose run --rm frontend npm run build
```

## 不要做

- 不要提交 `.env`、token、密码或真实凭据。
- 不要执行 `docker compose down -v`，除非用户明确确认会丢数据。
- 不要把研究评级写成投资建议或确定收益。
- 不要为了一个假设引入大型框架、迁移工具或真实交易链路。
- 不要在全市场回测中默认逐票调用 DeepSeek。
- 不要把临时调试字段散落在前端；数据语义优先落在后端。
