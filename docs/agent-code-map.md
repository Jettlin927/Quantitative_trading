# Agent Code Map

这份文档给后续 AI Agent 快速定位代码用。项目主管规则仍以根目录 `AGENTS.md` 为准。

如果是接手当前分支、PR 或阶段性验收，先看 `docs/agent-handoff.md`。

## 一句话架构

本仓库是本地数据工作台：React/Vite 前端负责展示，FastAPI 后端负责 Tushare 同步、美股 sample 文件入库和 DB 查询，PostgreSQL 负责持久化 A 股与 sample 美股数据。

```text
人类/AI Agent
  -> frontend/ React 数据工作台，或直接调用 http://localhost:18000 API
  -> backend/app/main.py FastAPI 数据路由
  -> backend/app/models.py SQLAlchemy schema
  -> PostgreSQL 本地数据
```

## 启动入口

- `docker-compose.yml`：三容器入口，服务名是 `db`、`api`、`frontend`。
- `启动数据工作台.cmd`：日常启动，只应 `docker compose up -d`。
- `重新构建并启动数据工作台.cmd`：改 Dockerfile、依赖或代码后使用。
- `停止数据工作台.cmd`：停止服务。
- `README.md`：面向人类的使用说明。
- `AGENTS.md`：面向 AI Agent 的项目主管规则。
- `docs/agent-handoff.md`：当前分支、PR、验证命令和本机坑点交接。

## 后端地图

- `backend/app/database.py`
  - SQLAlchemy engine、`SessionLocal`、`Base`、`get_db()`。
  - 默认本地 `DATABASE_URL` 只作开发兜底；容器内由 Compose 注入。

- `backend/app/models.py`
  - `Stock`：A 股基础信息。
  - `StockDailyBar`：A 股日线 OHLCV，`ts_code + trade_date` 唯一。
  - `StockDailyBasic`：Tushare `daily_basic`，估值、换手率、市值等。
  - `StockFinancialIndicator`：Tushare `fina_indicator`，ROE、毛利率、负债率、增长率等。
  - `StockPool` / `StockPoolMember`：自选数据池和成员。
  - `Asset`：美股/ETF sample 资产主数据，`market + symbol` 与 `natural_key` 唯一。
  - `AssetDailyPrice`：美股 sample 快照，`asset_natural_key + trade_date` 唯一。
  - `WatchlistItem`：美股 sample 观察池，`watchlist_name + asset_natural_key` 唯一。
  - `PortfolioSnapshot`：美股 sample 持仓快照，`snapshot_id` 唯一，`holdings` 为 JSON。
  - `DataSyncRun`：同步记录。

- `backend/app/schemas.py`
  - Pydantic 请求/响应模型。
  - 改 API 入参或前端依赖字段时，优先从这里确认契约。

- `backend/app/tushare_client.py`
  - Tushare 初始化、日期转换、Decimal 清洗。
  - 新增 Tushare 字段时，字段映射和空值处理应集中在后端。

- `backend/app/us_research.py`
  - 美股 sample 文件适配器。
  - 读取 `my_quant/us_research/` 下的 sample 观察池、sample 持仓和 sample 快照。
  - 文件 preview 只返回数据入库合同：`isSample=true`、`brokerConnected=false`、`realHoldingsImported=false`。

- `backend/app/main.py`
  - FastAPI 应用入口。
  - 包含 A 股 Tushare 同步、DB overview、股票池 CRUD、美股 sample import preview/import 和 DB overview。
  - 文件较长，先用函数名定位，不要盲目大改。

## 主要 API

- `GET /api/health`：健康检查。
- `GET /api/db/overview`：A 股和美股 sample DB 覆盖概览。
- `GET /api/stocks`：按代码、名称、简称、行业搜索股票。
- `GET /api/stocks/screen`：返回股票基础信息加最新行情和估值；这里只是数据筛选。
- `GET/POST/DELETE /api/stock-pools...`：自选数据池 CRUD 和成员管理。
- `POST /api/tushare/sync-stock-basic`：同步 A 股基础列表。
- `POST /api/tushare/sync-daily`：同步单票日线。
- `POST /api/tushare/sync-market-daily`：按交易日补齐全市场日线。
- `POST /api/tushare/sync-fundamentals`：同步单票估值和财务指标。
- `POST /api/tushare/sync-market-daily-basic`：补齐全市场估值指标。
- `POST /api/tushare/sync-market-fundamentals`：补齐全市场财务指标。
- `GET /api/tushare/sync-progress`：查询日线或估值覆盖进度。
- `GET /api/daily-bars`：读取单票原始日线。
- `GET /api/stocks/{ts_code}/fundamentals`：读取单票基本面概览。
- `GET /api/us-research/overview`：读取美股 sample 文件预览。
- `GET /api/us-research/import-preview`：预览美股 sample 文件将 upsert 到哪些表。
- `POST /api/us-research/import-sample`：将 sample 数据 upsert 到 DB。
- `GET /api/us-research/db-overview`：从 DB 读取已持久化的美股 sample 数据。

## 前端地图

- `frontend/src/main.jsx`
  - 数据工作台主入口。
  - 读取 `/api/health`、`/api/db/overview`、`/api/tushare/sync-progress`、`/api/stocks/screen`、`/api/us-research/db-overview`。
  - 提供美股 sample 入库按钮，调用 `POST /api/us-research/import-sample`。

- `frontend/src/styles.css`
  - 工业化数据终端视觉，维护高信息密度和可扫描性。

- `frontend/package.json`
  - `npm run lint`、`npm run typecheck`、`npm run build`。

## my_quant 地图

- `my_quant/us_research/config/watchlist_symbols.csv`
  - 美股 sample 观察池配置。

- `my_quant/us_research/data/holdings_sample.csv`
  - sample 持仓结构，数量和成本是虚构示例。

- `my_quant/us_research/data/snapshots/us_snapshot_latest.{json,csv}`
  - 美股 sample 快照。

- `my_quant/us_research/scripts/refresh_us_snapshot.py`
  - 使用 yfinance 刷新 sample 快照。
  - 仅生成 sample 数据文件，不生成报告，不做回测。

## 常见改动路线

新增 Tushare 数据字段：

1. 查 Tushare 当前文档。
2. 改 `backend/app/models.py` 加字段或表。
3. 改 `backend/app/main.py` 的 record-to-row 与 upsert。
4. 改 `backend/app/schemas.py` 和前端展示。
5. 运行后端 py_compile 和 backend tests。

新增美股 sample 字段：

1. 改 `my_quant/us_research/` 下 sample CSV/JSON 结构。
2. 改 `backend/app/us_research.py` 的 preview 读取。
3. 改 `backend/app/models.py` 和 `backend/app/main.py` 的 upsert 映射。
4. 改前端展示和后端测试。

新增前端数据面板：

1. 先读 `.codex/skills/frontend-design/SKILL.md`。
2. 保持第一屏为数据工作台。
3. 不引入策略、回测、交易信号或真实账户入口。
4. 改完运行前端构建。

## 验证命令

后端最小检查：

```powershell
python -m py_compile backend\app\database.py backend\app\models.py backend\app\schemas.py backend\app\tushare_client.py backend\app\us_research.py backend\app\main.py
python -m unittest discover backend\tests -v
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
- 不要连接真实券商或导入真实持仓。
- 不要把数据筛选写成策略筛选。
- 不要恢复旧策略、旧回测、旧研究阶段或旧报告生成链路。
