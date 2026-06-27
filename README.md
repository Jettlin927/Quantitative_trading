# Local Quant Data Workspace

一个本地运行的数据工作台：把 A 股 Tushare 数据和美股 sample 数据统一落到 PostgreSQL，再由后端和前端只读展示覆盖度、表状态和样本数据。

它现在不是策略系统，不保留回测引擎，不连接券商，不自动下单，不处理真实资金。

## 当前能做什么

- 同步 Tushare A 股基础列表、日线行情、`daily_basic` 估值指标和 `fina_indicator` 财务指标。
- 用 PostgreSQL 持久化本地行情、基本面、财务指标、股票池和同步记录。
- 查询 A 股数据覆盖度、最新交易日、表行数和同步历史。
- 读取股票列表、原始日线、最新估值和最新财务指标。
- 将 `my_quant/us_research/` 下的美股 sample 观察池、sample 快照和 sample 持仓结构 upsert 到 DB。
- 在前端查看 API/DB 状态、A 股覆盖、美股 sample 入库状态和近期同步记录。

## 技术栈

- 前端：React + Vite
- 后端：FastAPI + SQLAlchemy 2.0
- 数据库：PostgreSQL 16
- 数据源：Tushare Pro；美股侧当前只使用本仓 sample 文件
- 运行方式：Docker Compose

## 快速启动

先复制环境变量模板，不要把真实 `.env` 提交到 Git：

```powershell
Copy-Item .env.example .env
notepad .env
```

至少填写：

```dotenv
TUSHARE_TOKEN=你的_tushare_token
```

然后启动：

```powershell
.\启动数据工作台.cmd
```

访问：

- 前端工作台：http://localhost:15173
- API 文档：http://localhost:18000/docs
- PostgreSQL：localhost:5432

修改依赖、Dockerfile、前端或后端代码后，使用：

```powershell
.\重新构建并启动数据工作台.cmd
```

停止服务：

```powershell
.\停止数据工作台.cmd
```

## 推荐使用流程

1. 打开前端工作台，确认 API 和 DB 状态正常。
2. 调用 `POST /api/tushare/sync-stock-basic` 同步 A 股基础列表。
3. 调用 `POST /api/tushare/sync-market-daily` 补齐全市场日线。
4. 调用 `POST /api/tushare/sync-market-daily-basic` 补齐估值指标。
5. 调用 `POST /api/tushare/sync-market-fundamentals` 补齐财务指标。
6. 用 `GET /api/db/overview` 和 `GET /api/tushare/sync-progress` 检查覆盖度。
7. 如需美股 sample 数据，调用 `POST /api/us-research/import-sample` 入库。

## 主要 API

- `GET /api/health`：健康检查和表行数。
- `GET /api/db/overview`：A 股和美股 sample 的 DB 覆盖概览。
- `GET /api/stocks`：按代码、名称、行业和市场查询 A 股基础信息。
- `GET /api/stocks/screen`：返回股票基础信息加最新行情和估值；这里只是数据筛选，不是策略筛选。
- `GET/POST/DELETE /api/stock-pools...`：自选数据池 CRUD。
- `POST /api/tushare/sync-stock-basic`：同步 A 股基础列表。
- `POST /api/tushare/sync-daily`：同步单票日线。
- `POST /api/tushare/sync-market-daily`：按交易日补齐全市场日线。
- `POST /api/tushare/sync-fundamentals`：同步单票估值和财务指标。
- `POST /api/tushare/sync-market-daily-basic`：补齐全市场估值指标。
- `POST /api/tushare/sync-market-fundamentals`：补齐全市场财务指标。
- `GET /api/tushare/sync-progress`：查询同步覆盖进度。
- `GET /api/daily-bars`：读取单票原始日线。
- `GET /api/stocks/{ts_code}/fundamentals`：读取单票最新估值和财务概览。
- `GET /api/us-research/overview`：读取美股 sample 文件，只读预览。
- `GET /api/us-research/import-preview`：预览 sample 文件将写入哪些 DB 表。
- `POST /api/us-research/import-sample`：将美股 sample 数据 upsert 到 DB。
- `GET /api/us-research/db-overview`：从 DB 读取美股 sample 入库状态。

## 数据表

- `stocks`：A 股基础信息。
- `stock_daily_bars`：A 股日线 OHLCV，按 `ts_code + trade_date` 去重。
- `stock_daily_basic`：Tushare `daily_basic`，包含 PE、PB、PS、换手率、市值等。
- `stock_financial_indicators`：Tushare `fina_indicator`，包含 ROE、毛利率、净利率、资产负债率、增长率等。
- `stock_pools`：自选数据池。
- `stock_pool_members`：自选数据池成员。
- `assets`：美股/ETF sample 资产主数据。
- `asset_daily_prices`：美股 sample 快照行情。
- `watchlist_items`：美股 sample 观察池。
- `portfolio_snapshots`：美股 sample 持仓快照，`holdings` 为 JSON。
- `data_sync_runs`：同步记录。

## 数据库软件连接

用 TablePlus、DBeaver、DataGrip、pgAdmin 等工具连接本机 PostgreSQL：

- Host：`localhost`
- Port：`.env` 中的 `POSTGRES_PORT`，默认 `5432`
- Database：`.env` 中的 `POSTGRES_DB`
- User：`.env` 中的 `POSTGRES_USER`
- Password：`.env` 中的 `POSTGRES_PASSWORD`
- SSL：本地开发通常关闭

容器内服务连接 DB 时使用 `db:5432`，宿主机数据库软件连接时使用 `localhost:<POSTGRES_PORT>`。

## 环境变量

`.env.example` 只保留占位值，可以提交；真实 `.env` 已被 `.gitignore` 忽略。

| 变量 | 说明 |
| --- | --- |
| `POSTGRES_DB` | PostgreSQL 数据库名 |
| `POSTGRES_USER` | PostgreSQL 用户名 |
| `POSTGRES_PASSWORD` | PostgreSQL 密码，本地默认值仅用于开发 |
| `POSTGRES_PORT` | 数据库宿主机端口，默认 `5432` |
| `API_PORT` | FastAPI 宿主机端口，默认 `18000` |
| `FRONTEND_PORT` | 前端宿主机端口，默认 `15173` |
| `TUSHARE_TOKEN` | Tushare Pro token，用于同步行情和财务数据 |

## 安全边界

- 本项目只做本地数据同步、入库和展示。
- 不接入券商，不自动下单，不处理真实账户资金。
- 不要提交 `.env`、真实 token、数据库密码或任何凭据。
- 不要提交真实持仓、真实成交或券商导出。
- 不要执行 `docker compose down -v`，除非明确接受会删除本地 PostgreSQL volume 数据。

## 发布前检查

```powershell
git status --short --ignored
```

确认 `.env` 显示为 ignored，而源码、README 和 docs 正常进入提交。
