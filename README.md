# Local Quant Data Workspace

一个本地运行的量化数据与离线研究工作台：把 A 股 Tushare 数据和美股 sample 数据统一落到 PostgreSQL，由后端和前端只读展示覆盖度，并用独立研究协议层约束复权、point-in-time、组合模拟、评估和复现口径。

它不是实盘策略或交易系统：不连接券商，不自动下单，不处理真实资金。研究底座只提供通用数据与模拟协议，不把历史探索脚本或单次结果当成有效策略。

## 当前能做什么

- 同步 Tushare A 股历史上市状态、交易日历、日线、复权因子、每日涨跌停、停复牌事件、估值和财务指标。
- 同步指数及指数日线、ETF 及 ETF 日线/复权因子、申万行业分类和历史成员。
- 用 PostgreSQL 持久化行情、基本面、研究主数据、自选数据池和同步记录，并保证自然键幂等 upsert。
- 查询 A 股数据覆盖度、最新交易日、表行数和同步历史。
- 读取股票列表、完整原始日线历史、复权/可交易性数据、最新估值和最新财务指标；标的研究图表支持近 1/3/5 年及全部历史切换。
- 将 `my_quant/us_research/` 下的美股 sample 观察池、sample 快照和 sample 持仓结构 upsert 到 DB。
- 在前端查看 API/DB 状态、A 股覆盖、美股 sample 入库状态和近期同步记录；所有写入型刷新操作通过持久化异步任务执行和轮询，不阻塞页面请求。
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
- 当前服务挂载历史数据卷 `quant_todo_p0_postgres_data_todo_p0`；切换前的 `quantitative-trading_postgres_data` 仍完整保留，可作为回滚来源，未删除任何 volume。
- 历史卷切换前备份为 `/opt/quantitative-trading-backups/pre-2012-history-volume-switch-20260711-0108.dump`，已通过 `pg_restore -l` 校验。
- A 股日线、估值、股票复权、指数日线和 ETF 日线的主体历史已覆盖 2012 年和 2015 年股灾区间；`scripts/ops/backfill_a_share_history.py` 用于续跑并补齐涨跌停、停复牌和 ETF 复权等 P1 数据。
- 服务器 `crontab` 使用 `CRON_TZ=Asia/Shanghai`，每天 20:30 调用 `scripts/ops/sync_today_market_data.sh`，提交异步日更任务并轮询结果。
- 历史回补后服务器磁盘约使用 `94%`、剩余约 `2.5G`；新增美股或期权逐笔数据前必须先扩容或经用户确认清理旧回滚数据。

## 推荐使用流程

1. 打开前端工作台，确认 API、DB 和两类研究门禁状态正常。
2. 前端刷新按钮会提交 `/api/sync-jobs` 异步任务；可离开当前页面，任务状态会持久化，重新打开页面后继续显示。
3. A 股横截面研究还必须同步历史上市状态、每日涨跌停和停复牌事件。
4. 用 `GET /api/db/overview`、`GET /api/tushare/sync-progress` 和 `GET /api/research/readiness` 检查覆盖度与研究门槛。
5. readiness 为 `blocked` 时先补数据，不允许研究代码静默回退到不完整口径。
6. 如需美股 sample 数据，调用 `POST /api/us-research/import-sample` 入库。

## 主要 API

- `GET /api/health`：默认只做轻量 DB 连通检查；排障时可用 `include_counts=true` 读取全表行数。
- `GET /api/db/overview`：A 股和美股 sample 的 DB 覆盖概览。
- `POST /api/sync-jobs`：提交 `stock_listings`、`trade_calendar`、`market_bundle`、`daily_market` 或 `us_sample` 异步任务。
- `GET /api/sync-jobs`、`GET /api/sync-jobs/{id}`：读取任务列表或轮询单个任务状态。
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
- `GET /api/tushare/sync-progress`：查询同步覆盖进度；`include_coverage=false` 只读取近期运行记录。
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
- `data_sync_jobs`：持久化异步任务，记录排队、运行、完成或失败状态；请求中的临时 token 不入库。
- `data_overview_snapshots`：缓存经精确聚合得到的覆盖矩阵；页面默认读取快照，日更或手动同步完成后用 `refresh=true` 重算。

## 2012 年起历史回补

默认回补区间为 `2012-01-01` 到当天，脚本会检查现有覆盖并跳过已完整项目，失败后可以使用同一命令续跑：

```bash
docker exec quant_trading_api python scripts/ops/backfill_a_share_history.py \
  --start-date 2012-01-01 \
  --end-date 2026-07-10 \
  --rate 120 \
  --resume
```

先用 `--dry-run` 查看计划；`--max-items` 可做小批量验证。脚本覆盖股票目录、交易日历、全市场日线/估值、股票复权、股票范围涨跌停、停复牌、指数日线和真实 ETF 复权（排除 LOF/分级基金与 ETF 联接）。不要通过删除或重建 PostgreSQL volume 来“重新同步”。

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

## 测试与 CI

后端快速门禁使用内存 SQLite，不会连接本地或服务器 PostgreSQL：

```bash
DATABASE_URL=sqlite+pysqlite:///:memory: python -m unittest discover -s backend/tests -p 'test_*.py' -v
```

真实 PostgreSQL 语义使用隔离的 PostgreSQL 16 tmpfs 容器验证：

```bash
scripts/ops/test_postgres_integration.sh
```

该脚本使用独立 Compose project、随机本机端口和专用的 `quant_migration_test` / `quant_worker_test` 数据库，自动发现 migration、数据质量、快照、runner 与 worker lease 集成测试。退出时只清理本次测试容器；`docker-compose.test.yml` 不声明命名卷，不会读取或删除日常 `postgres_data`。

GitHub Actions 还会固定运行 Python `py_compile`、黄金数据前缀不变/重现门禁、前端 typecheck/lint/build、主/测试 Compose 配置、全部运维 Shell 语法和 Git 空白差异检查。

## 发布前检查

```powershell
git status --short --ignored
```

确认 `.env` 显示为 ignored，而源码、README 和 docs 正常进入提交。
