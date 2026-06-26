# Agent Code Map

这份文档给后续 AI Agent 快速定位代码用。它不是产品说明，而是“从目标到代码位置”的导航图。项目主管规则仍以根目录 `AGENTS.md` 为准。

## 一句话架构

本仓库是本地量化研究工作台：React/Vite 前端负责人机交互，FastAPI 后端负责数据同步、指标计算、策略回测、AI/本地复盘，PostgreSQL 负责持久化 Tushare 行情、基本面、同步记录和自选标的池。

## 四区导航

- 美股操作层：`my_quant/us_research/`，后续放 sample 持仓、观察池、yfinance 快照脚本、HTML/Markdown 操作报告和规则证据引用。
- A 股数据沙盘：`backend/app/`、`docker-compose.yml`、PostgreSQL volume 和 `docs/research/a-share-data/`，继续服务 Tushare 同步、A 股研究池和大样本验证。
- 策略思想库：`docs/research/strategy-lab/`，后续放“不追高”“止跌后加仓”“同因子杠杆预算”等规则卡片和负证据。
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
- `POST /api/backtests/run`：单票数据库回测，默认可带 AI 复盘。
- `POST /api/backtests/market`：同步执行全市场或池内回测。
- `POST /api/backtests/market/jobs` 与 `GET /api/backtests/market/jobs/{job_id}`：后台回测任务和轮询进度。

## 前端地图

- `frontend/src/main.jsx`
  - 当前是主要应用文件，包含状态、API 调用、页面组件和图表组件。
  - 顶部常量：`API_BASE`、表单默认值、页签、技术形态选项、策略预设。
  - 关键状态：`form`、`screenResults`、`stockPools`、`result`、`marketResult`、`qualityAnalysis`、`bars`、`syncProgress`、`marketBacktestJob`。
  - 关键动作：`syncStockBasic()`、`runScreener()`、`syncDaily()`、`syncMarketDaily()`、`syncFundamentals()`、`syncMarketDailyBasic()`、`runBacktest()`、`runMarketBacktest()`、`runQualityAnalysis()`。

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

- `my_quant/strategy_research/experiment/b1_trend_pullback.py`
  - B1 A 股趋势回调组合实验核心，包含 Tushare/AkShare 数据读取、候选排序、现实成交约束和组合回测。
  - `fetch_tushare_index_bars()` 会检查指数缓存最大日期，缓存不覆盖请求结束日时刷新，避免盘前预案读到半旧指数。

- `my_quant/strategy_research/experiment/kronos_forecast_slope.py`
  - 把 Kronos 预测统计路径转换成研究用 `buy` / `sell` / `hold` 信号。
  - 只评估预测路径斜率、预测收益和下行分位过滤，不连接券商、不产生真实交易动作。

- `my_quant/strategy_research/run_kronos_hk_forecast.py`
  - 从 `kronos-预测` 收拢来的港股 Kronos 预测包装入口。
  - 需要通过 `--kronos-dir` 或 `KRONOS_DIR` 指向外部 Kronos checkout；本仓不内置第三方模型仓库和虚拟环境。

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
