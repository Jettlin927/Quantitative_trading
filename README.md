# Local Quant Data Workspace

一个本地运行的量化数据与离线研究工作台：把 A 股 Tushare 数据和美股 sample 数据统一落到 PostgreSQL，由后端和前端只读展示覆盖度，并用独立研究协议层约束复权、point-in-time、组合模拟、评估和复现口径。

它不是实盘策略或交易系统：不连接券商，不自动下单，不处理真实资金。研究底座只提供通用数据与模拟协议，不把历史探索脚本或单次结果当成有效策略。

## 当前能做什么

- 同步 Tushare A 股历史上市状态、交易日历、日线、复权因子、每日涨跌停、停复牌事件、估值和财务指标。
- 同步指数及指数日线、ETF 及 ETF 日线/复权因子、申万行业分类和历史成员。
- 用 PostgreSQL 持久化行情、基本面、研究主数据、自选数据池和同步记录，并保证自然键幂等 upsert。
- 查询 A 股数据覆盖度、最新交易日、表行数和同步历史。
- 读取股票列表、原始日线、复权/可交易性数据、最新估值和最新财务指标。
- 将 `my_quant/us_research/` 下的美股 sample 观察池、sample 快照和 sample 持仓结构 upsert 到 DB。
- 在前端查看 API/DB 状态、A 股覆盖、美股 sample 入库状态和近期同步记录。
- 用 `backend/app/quant_research/` 构造严格复权和公告日可见的数据集，执行受停牌/涨跌停约束的下一交易日开盘研究组合模拟，输出基准指标、walk-forward 窗口和可复现 manifest。
- 用 `GET /api/research/readiness` 区分 ETF 时间序列与 A 股横截面研究是否满足数据门槛。

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

## 当前服务器部署

- 服务器部署目录：`/opt/quantitative-trading-release-20260710-2330`。
- 服务器上的 PostgreSQL、API 和前端端口继续只绑定 `127.0.0.1`，不直接暴露数据库或 API 到公网。
- 本机建立 SSH tunnel 后访问前端；当前验收入口为 `http://127.0.0.1:15174/`，隧道断开后需要重新建立。
- 部署前备份位于 `/opt/quantitative-trading-backups/pre-research-foundation-20260710-2330.dump`；现有 `quantitative-trading_postgres_data` volume 已原位保留。
- 当前 A 股横截面和 ETF 时序 readiness 均为 `ready`，但这是表级门禁。股票复权只回填了 5 个样本标的，涨跌停与停复牌只回填了 2026-06-26 至 2026-07-10；正式研究前仍需按目标股票池和研究区间补齐历史数据。

## 推荐使用流程

1. 打开前端工作台，确认 API、DB 和两类研究门禁状态正常。
2. 先同步交易日历和资产目录，再同步日线、复权因子及对应研究数据；具体接口见 API 文档。
3. A 股横截面研究还必须同步历史上市状态、每日涨跌停和停复牌事件。
4. 用 `GET /api/db/overview`、`GET /api/tushare/sync-progress` 和 `GET /api/research/readiness` 检查覆盖度与研究门槛。
5. readiness 为 `blocked` 时先补数据，不允许研究代码静默回退到不完整口径。
6. 如需美股 sample 数据，调用 `POST /api/us-research/import-sample` 入库。

## 主要 API

- `GET /api/health`：健康检查和表行数。
- `GET /api/db/overview`：A 股和美股 sample 的 DB 覆盖概览。
- `GET /api/stocks`：按代码、名称、行业和市场查询 A 股基础信息。
- `GET /api/stocks/screen`：返回股票基础信息加最新行情和估值；这里只是数据筛选，不是策略筛选。
- `GET/POST/DELETE /api/stock-pools...`：自选数据池 CRUD。
- `POST /api/tushare/sync-stock-basic`：同步 A 股基础列表。
- `POST /api/tushare/sync-stock-listings`：同步 L/D/P/G 历史上市状态。
- `POST /api/tushare/sync-daily`：同步单票日线。
- `POST /api/tushare/sync-market-daily`：按交易日补齐全市场日线。
- `POST /api/tushare/sync-market-limit-prices`：按交易日同步每日涨跌停价格。
- `POST /api/tushare/sync-market-suspend-events`：按日期同步停复牌事件。
- `POST /api/tushare/sync-fundamentals`：同步单票估值和财务指标。
- `POST /api/tushare/sync-market-daily-basic`：补齐全市场估值指标。
- `POST /api/tushare/sync-market-fundamentals`：补齐全市场财务指标。
- `GET /api/tushare/sync-progress`：查询同步覆盖进度。
- `POST /api/tushare/sync-trade-calendar`：同步正式交易日历。
- `POST /api/tushare/sync-adjust-factors`：同步股票复权因子。
- `POST /api/tushare/sync-index-basic`、`sync-index-daily`：同步指数目录和日线。
- `POST /api/tushare/sync-fund-basic`、`sync-fund-daily`、`sync-fund-adjust-factors`：同步 ETF/基金目录、日线和复权因子。
- `POST /api/tushare/sync-industry-classifications`：同步申万行业分类及历史成员。
- `GET /api/daily-bars`：读取单票原始日线。
- `GET /api/stocks/{ts_code}/fundamentals`：读取单票最新估值和财务概览。
- `GET /api/stock-listings`：读取历史上市状态。
- `GET /api/stocks/{ts_code}/limit-prices`：读取每日涨跌停价格。
- `GET /api/stocks/{ts_code}/suspend-events`：读取停复牌事件。
- `GET /api/funds/{ts_code}/adjust-factors`：读取 ETF/基金复权因子。
- `GET /api/research/readiness`：读取不同研究类型的 `ready/blocked` 门禁结果。
- `GET /api/us-research/overview`：读取美股 sample 文件，只读预览。
- `GET /api/us-research/import-preview`：预览 sample 文件将写入哪些 DB 表。
- `POST /api/us-research/import-sample`：将美股 sample 数据 upsert 到 DB。
- `GET /api/us-research/db-overview`：从 DB 读取美股 sample 入库状态。

## 数据表

- `stocks`：A 股基础信息。
- `stock_listings`：L/D/P/G 历史上市状态、上市日和退市日。
- `stock_daily_bars`：A 股日线 OHLCV，按 `ts_code + trade_date` 去重。
- `stock_adjust_factors`：A 股复权因子。
- `stock_limit_prices`：A 股每日涨跌停价格。
- `stock_suspend_events`：A 股停复牌事件。
- `stock_daily_basic`：Tushare `daily_basic`，包含 PE、PB、PS、换手率、市值等。
- `stock_financial_indicators`：Tushare `fina_indicator`，包含 ROE、毛利率、净利率、资产负债率、增长率等。
- `trade_calendars`：市场交易日历。
- `indices`、`index_daily_bars`：指数目录和指数日线。
- `funds`、`fund_daily_bars`、`fund_adjust_factors`：ETF/基金目录、日线和复权因子。
- `industry_classifications`、`industry_members`：申万行业层级与历史成员。
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

- 本项目只做本地数据同步、入库、展示和离线研究模拟。
- 不接入券商，不自动下单，不处理真实账户资金。
- 研究结果必须保留数据快照、代码版本、参数哈希、基准和限制项；不得写成投资建议或收益承诺。
- 不要提交 `.env`、真实 token、数据库密码或任何凭据。
- 不要提交真实持仓、真实成交或券商导出。
- 不要执行 `docker compose down -v`，除非明确接受会删除本地 PostgreSQL volume 数据。

## 发布前检查

```powershell
git status --short --ignored
```

确认 `.env` 显示为 ignored，而源码、README 和 docs 正常进入提交。
