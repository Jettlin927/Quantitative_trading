# Agent Code Map

这份文档给后续 AI Agent 快速定位代码用。项目主管规则仍以根目录 `AGENTS.md` 为准。

如果是接手当前分支、PR 或阶段性验收，先看 `docs/agent-handoff.md`。

## 一句话架构

本仓库是本地量化数据与离线研究工作台：React/Vite 前端负责只读展示，FastAPI 后端负责入队、查询和 Tushare 数据适配，独立 worker 执行持久同步任务，PostgreSQL 负责业务数据、质量运行、快照和任务状态，`backend/app/quant_research/` 负责无实盘副作用的统一研究协议与可复现运行。

```text
人类/AI Agent
  -> frontend/ React 数据工作台，或直接调用 http://localhost:18000 API
  -> backend/app/main.py FastAPI 数据路由与持久任务入队
  -> backend/app/sync_worker.py PostgreSQL 租约 worker
  -> backend/app/models.py SQLAlchemy schema
  -> PostgreSQL 本地数据
  -> backend/app/data_quality/ 研究范围级完整性门禁
  -> backend/app/quant_research/ 严格数据集、冻结快照、组合模拟、评估与复现协议
```

## 启动入口

- `docker-compose.yml`：四容器入口，服务名是 `db`、`api`、`worker`、`frontend`；研究产物使用独立命名 volume。
- `docker-compose.test.yml`：PostgreSQL 16 tmpfs 测试入口，不挂载日常数据库 volume。
- `启动数据工作台.cmd`：日常启动，只应 `docker compose up -d`。
- `重新构建并启动数据工作台.cmd`：改 Dockerfile、依赖或代码后使用。
- `停止数据工作台.cmd`：停止服务。
- `README.md`：面向人类的使用说明。
- `AGENTS.md`：面向 AI Agent 的项目主管规则。
- `docs/agent-handoff.md`：当前分支、PR、验证命令和本机坑点交接。
- 当前生产 release 为 `/opt/quantitative-trading-release-20260712-0101`，schema 为 `0006_worker_heartbeats`，运行时代码为 `c24ade495492f64ea82aa229827858cdef52cdf6`；验收 ID 与输入哈希见 `docs/deployment/2026-07-12-production-trustworthiness-acceptance.md`。

## 后端地图

- `backend/app/database.py`
  - SQLAlchemy engine、`SessionLocal`、`Base`、`get_db()`。
  - Alembic revision 检查、冻结 schema fingerprint 和既有库安全 `stamp-existing`；应用启动只校验 head，不自动迁移。
  - 默认本地 `DATABASE_URL` 只作开发兜底；容器内由 Compose 注入。

- `backend/app/models.py`
  - `Stock`：A 股基础信息。
  - `StockDailyBar`：A 股日线 OHLCV，`ts_code + trade_date` 唯一。
  - `StockDailyBasic`：Tushare `daily_basic`，估值、换手率、市值等。
  - `StockFinancialIndicator`：Tushare `fina_indicator`，ROE、毛利率、负债率、增长率等。
  - `StockListing`：L/D/P/G 历史上市状态。
  - `StockLimitPrice` / `StockSuspendEvent`：每日涨跌停价格和停复牌事件。
  - `TradeCalendar` / `StockAdjustFactor`：交易日历和股票复权因子。
  - `Index` / `IndexDailyBar`：指数目录和日线。
  - `Fund` / `FundDailyBar` / `FundAdjustFactor`：ETF/基金目录、日线和复权因子。
  - `IndustryClassification` / `IndustryMember`：申万行业层级和历史成员。
  - `StockPool` / `StockPoolMember`：自选数据池和成员。
  - `Asset`：美股/ETF sample 资产主数据，`market + symbol` 与 `natural_key` 唯一。
  - `AssetDailyPrice`：美股 sample 快照，`asset_natural_key + trade_date` 唯一。
  - `WatchlistItem`：美股 sample 观察池，`watchlist_name + asset_natural_key` 唯一。
  - `PortfolioSnapshot`：美股 sample 持仓快照，`snapshot_id` 唯一，`holdings` 为 JSON。
  - `DataSyncRun`：单个同步接口的执行记录。
  - `DataSyncJob`：持久异步任务；含 `active_key`、尝试上限、退避、lease owner/expiry 和 heartbeat。
  - `SyncWorkerHeartbeat`：独立 worker 的代码提交、进程起点、当前任务和最近心跳。
  - `DataOverviewSnapshot`：千万级表精确覆盖矩阵的持久化快照，避免每次打开页面重复全表聚合。
  - `DataQualityRun` / `DataQualityResult`：研究范围、日期、universe、规则结果和质量状态的审计登记。
  - `DataSnapshot`：冻结输入切片及每张表的 canonical artifact/hash 登记。
  - `ResearchRun`：代码、配置、环境、快照、checkpoint、结果指纹和中断状态登记。

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
  - 包含 A 股/指数/ETF/行业 Tushare 同步适配、持久任务提交/轮询、DB overview、质量运行/readiness、股票池 CRUD、美股 sample import preview/import 和 DB overview。
  - API 只入队，不在请求进程里执行长同步；启动时 revision 落后会明确失败。
  - 文件较长，先用函数名定位，不要盲目大改。

- `backend/app/sync_worker.py`
  - 使用 `FOR UPDATE SKIP LOCKED` 领取任务，独立短事务续租/心跳，过期租约可由新 worker 接管。
  - 语义为 at-least-once；幂等依赖各表自然键 upsert，永久错误和重试耗尽会进入最终失败。

- `backend/app/data_quality/`
  - `contracts.py`：scope、universe、日期、数据集与状态合同。
  - `rules.py`：schema、唯一性、domain、引用、日历覆盖、复权、OHLCV、新鲜度和基准重叠规则。
  - `runner.py`：PostgreSQL `REPEATABLE READ READ ONLY` 执行、statement timeout、规则登记和 `ready/ready_with_warnings/blocked/failed` 分流。

- `backend/app/quant_research/`
  - 信任边界以 `docs/research/quant-foundation-trust-contract.md` 为准；新 loader、特征、模拟器和 runner 必须先满足其 quality scope、宇宙血缘和时点可得合同。
  - `dataset.py`：严格复权、公告日 point-in-time 关联、历史成员筛选。
  - `repository.py`：从 DB 加载显式历史股票池和基准；缺历史上市、复权或涨跌停数据时失败。
  - `strategy_registry.py`：六条源码静态登记策略的身份、scope、必需冻结表、示例配置和函数分发；禁止动态 import。
  - `features.py`：baseline 实际使用的因果时序/横截面特征；warmup 保持 null。
  - `etf_trend_baseline.py`：固定 120 日均线、月末 1/0 目标的 ETF baseline。
  - `etf_volatility_managed.py`：预登记的月度倒数方差/倒数波动率 ETF 暴露，以及校准期中位数固定门槛的低波动 0/100% 准入探索。
  - `a_share_price_baseline.py`：固定 120–20 动量、60 日波动和历史行业成员的 A 股价格 baseline。
  - `a_share_b1_trend_pullback.py`：公开 B1 趋势回调描述的事前固定近似复现；含 BBI/双重 EMA/KDJ、沪深300市场门、Top2 代理排序、分档止盈和现实成交账本。
  - `portfolio.py`：下一交易日开盘目标权重模拟，一次产生 NAV、请求、模拟执行和逐日持仓；含现金、成本及开盘可买卖硬约束。
  - `metrics.py`：绝对/基准相对、回撤持续期、成本、换手、持仓和集中度指标；期首已持有/执行的研究必须显式传入初始净值，不能漏掉首日。
  - `reporting.py`：报告共用收益序列、尾部风险、HAC alpha、DSR 和 PBO；避免各 HTML 脚本复制统计公式。
  - `validation.py`：固定参数 anchored/rolling walk-forward，仅输出 test/OOS 指标；每个 `StrategyDefinition` 必须显式声明 `walk_forward_benchmark_source`，ETF 趋势/波动/准入使用同一 ETF 因果复权基准，不能静默改用市场环境指数。
  - `risk.py`：从冻结收益、NAV、positions、历史成员和基准生成暴露与风险贡献 canonical 工件。
  - `allocation.py`：等权/逆波动率、单票/行业/现金/换手约束的确定性目标权重分配；不生成订单。
  - `manifest.py`：run id、参数哈希、Git commit、数据快照、artifact schema 和结果指纹。
  - `run_config.py`：canonical 配置、validation/risk policy、环境和可复现键。
  - `snapshot.py` / `artifacts.py`：只读一致性切片、canonical CSV.gz、容量门禁、SHA-256 和原子完成语义。
  - `runner.py`：质量门禁、快照、目标、模拟、指标、风险、manifest、finalize 的 hash-chain checkpoint；支持归档验证、断库 reproduce、stale `running`→`interrupted` 和严格 `--resume`。
  - `baselines.py`：仅用于管线验收、无参数搜索和无收益主张的单 ETF sentinel。
  - `readiness.py`：inventory、quality run 和风险能力 readiness；`index_weights` / `industry_proxy_daily` 缺失时明确 blocked。

- `backend/app/strategy_results.py`
  - 读取 `docs/research/strategy-results/manifest.json`，兼容当前 `summaryJson` 报告包和旧 phased/csv 档案，只返回只读结果概览；`summary.status` 取 manifest，旧脚本状态单列为 `sourceExecutionStatus`。
  - manifest 工件路径必须是结果根目录内的普通相对文件；拒绝绝对路径、`..`、symlink 逃逸。已声明 `summaryJson` 缺失或不是 JSON 对象时显式失败，不能降级成空摘要。

- `scripts/research/render_etf_volatility_managed_report.py`
  - 只接受四个预登记波动率变体、两类成本压力场景和两个低波动准入成本场景；从 canonical 工件生成统一 HTML/JSON。
  - 被动 ETF 与策略都从 OOS 首日开盘前初始净值 1.0 起算；报告必须包含成本/换手、DSR/PBO、支持/反对/尚缺证据和复现身份。

- `scripts/research/report_evidence.py`
  - 从 canonical manifest 派生确定性的 `reportGeneratedAt`，并校验共享复现证据中的代码身份、运行 ID、镜像、断网条件及连续两轮 result fingerprint；任何不一致都停止报告发布。

- `scripts/research/render_etf_trend_120d_report.py`
  - 只接受固定120日趋势的基础、零、双倍成本三个 canonical 运行，重建同一ETF被动基准与同平均暴露静态组合，并生成长历史 HTML/JSON 报告。
  - 报告首屏明确完整周期与年度子区间，避免把逐年稳定性表误读为总回测只有一年。

- `scripts/research/render_a_share_b1_report.py`
  - 只接受五个事前登记 B1 场景，并用共享证据文件校验固定断网复现身份；重新计算来源对照、长历史、执行、风险、环境、压力期和 walk-forward 摘要。
  - 生成以 100,000 元为统一展示本金的 HTML/JSON 报告，并把“近似复现”与“原网页数值复现”明确分开。

- `docs/research/strategy-results/`
  - 根 `index.html` 是当前可信报告与旧档案的统一入口，`manifest.json` 是机器清单，`reproduction-evidence-20260719.json` 是当前三组报告的两轮断网复现总账；当前报告包至少包含 `index.html` 和 `summary.json`。
  - 这里只保存可提交的只读投影；canonical 输入、账本和运行 manifest 留在被 Git 忽略的 `outputs/research-runs/`。

- `backend/tests/fixtures/quant_research_golden/`
  - 完全合成的 2 股票 + 1 ETF + 1 指数、15 交易日黄金夹具。
  - 固定时点可得日、信号/执行日、净值和指标预期；不包含真实 Tushare 数据、持仓或凭据。

- `backend/tests/test_quant_trust_contract.py`
  - 验证黄金夹具的稳定排序、边界事件、下一交易日执行和固定产物。
  - 区间末复权锚定和公告日同日可见问题已修复，当前都是必须正常通过的硬断言。

- `backend/tests/test_research_snapshot*.py` / `test_research_reproduction.py` / `test_research_resume.py`
  - 验证一致性快照、跨产物根复用、在线库变更后的离线重现、归档篡改拒绝，以及 snapshot/simulation/finalize 中断续跑。

- `backend/tests/test_sync_worker*.py`
  - 验证双 worker 排他领取、租约过期接管、重试上限、旧 owner 失效和 PostgreSQL 自然键零重复。

## 主要 API

- `GET /api/health`：健康检查。
- `GET /api/db/overview`：A 股和美股 sample DB 覆盖概览。
- `POST /api/sync-jobs`、`GET /api/sync-jobs...`：提交并轮询持久化异步同步任务。
- `GET /api/stocks`：按代码、名称、简称、行业搜索股票。
- `GET /api/stocks/screen`：返回股票基础信息加最新行情和估值；这里只是数据筛选。
- `GET/POST/DELETE /api/stock-pools...`：自选数据池 CRUD 和成员管理。
- `POST /api/tushare/sync-stock-basic`：同步 A 股基础列表。
- `POST /api/tushare/sync-stock-listings`：同步历史上市状态。
- `POST /api/tushare/sync-daily`：同步单票日线。
- `POST /api/tushare/sync-market-daily`：按交易日补齐全市场日线。
- `POST /api/tushare/sync-market-limit-prices`：按交易日同步涨跌停价格。
- `POST /api/tushare/sync-market-suspend-events`：按日期同步停复牌事件。
- `POST /api/tushare/sync-fundamentals`：同步单票估值和财务指标。
- `POST /api/tushare/sync-market-daily-basic`：补齐全市场估值指标。
- `POST /api/tushare/sync-market-fundamentals`：补齐全市场财务指标。
- `GET /api/tushare/sync-progress`：查询日线或估值覆盖进度。
- `POST /api/tushare/sync-trade-calendar`、`sync-adjust-factors`：同步交易日历和股票复权因子。
- `POST /api/tushare/sync-index-basic`、`sync-index-daily`：同步指数目录和日线。
- `POST /api/tushare/sync-fund-basic`、`sync-fund-daily`、`sync-fund-adjust-factors`：同步 ETF/基金数据。
- `POST /api/tushare/sync-industry-classifications`：同步申万行业与历史成员。
- `GET /api/daily-bars`：读取单票原始日线。
- `GET /api/stocks/{ts_code}/fundamentals`：读取单票基本面概览。
- `GET /api/stock-listings`、`/api/stocks/{ts_code}/limit-prices`、`suspend-events`：读取研究所需历史状态。
- `GET /api/research/readiness`：只返回 inventory 级状态，不能代表某个研究切片 ready。
- `GET /api/strategy-results/overview`：读取统一只读研究结果清单，不执行回测。
- `POST /api/data-quality/runs`、`GET /api/data-quality/runs/{id}`：执行/读取研究范围级质量门禁。
- `GET /api/research/readiness/{quality_run_id}`：读取绑定 quality run 的研究级 readiness。
- `GET /api/us-research/overview`：读取美股 sample 文件预览。
- `GET /api/us-research/import-preview`：预览美股 sample 文件将 upsert 到哪些表。
- `POST /api/us-research/import-sample`：将 sample 数据 upsert 到 DB。
- `GET /api/us-research/db-overview`：从 DB 读取已持久化的美股 sample 数据。

## 前端地图

- `frontend/src/main.jsx`
  - 数据工作台主入口。
  - 轻量状态、股票列表和任务状态先加载，千万级表覆盖矩阵渐进加载。
  - 写入型刷新统一调用 `POST /api/sync-jobs` 并轮询；标的研究一次读取数据库中的完整日线历史，可切换近 1/3/5 年和全部历史。

- `frontend/src/styles.css`
  - 工业化数据终端视觉，维护高信息密度和可扫描性。

- `frontend/package.json`
  - `npm run lint`、`npm run typecheck`、`npm run build`。

## 运维与回补

- `scripts/ops/backfill_a_share_history.py`：默认从 2012 年开始的可续跑历史回补；具备覆盖检查、checkpoint、重试、限速、dry-run 和小批量验证参数。
- `scripts/ops/sync_today_market_data.sh`：`flock` 互斥；先持久同步交易日历，再提交/轮询 `daily_market` 和财务任务，刷新 overview 后执行最新交易日质量门禁。
- `scripts/ops/install_daily_sync_cron.sh`：安装 `CRON_TZ=Asia/Shanghai` 的 20:30 日更任务。
- `scripts/ops/test_postgres_integration.sh`：启动 PostgreSQL 16 tmpfs，自动发现全部后端测试并在退出时清理容器/网络。
- `scripts/research/check_data_quality.py`：研究范围质量 CLI，`blocked=2`、`failed=3`。
- `scripts/research/run_quant_research.py`：`--list-strategies`、新运行或 `--resume RUN_ID`；正式运行要求 `APP_GIT_COMMIT`。
- `scripts/research/reproduce_quant_research.py`：只读冻结输入离线重现，不访问在线行情库。
- `scripts/research/audit_quant_research.py`：Phase 5 固定黑盒反例矩阵；PostgreSQL 语义仍使用完整隔离矩阵验证。

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
3. 不引入回测执行、交易信号、买卖评级或真实账户入口。
4. 改完运行前端构建。

新增离线研究能力：

1. 研究、分析、评估、回测或比较具体策略时，先阅读 `docs/research/strategy-evaluation-standard.md`，按其中的策略画像、指标和市场环境矩阵交付。
2. 实现新的研究协议前阅读 `docs/research/quant-foundation-trust-contract.md`，再在 `docs/research/` 写明数据、时点、成交、成本、基准和失败口径。
3. 共用协议放入 `backend/app/quant_research/`，不得复制旧探索脚本逻辑。
4. 先用黄金夹具表达新语义，并验证追加未来数据不改变历史前缀。
5. 数据不完整时严格失败或让 readiness 返回 `blocked`，不允许静默回退。
6. 新增单元测试，并让每次运行记录 manifest；大型结果只写入被忽略的 `outputs/research-runs/`。

## 验证命令

后端最小检查：

```powershell
python -m py_compile backend\app\database.py backend\app\models.py backend\app\schemas.py backend\app\tushare_client.py backend\app\us_research.py backend\app\main.py backend\app\quant_research\dataset.py backend\app\quant_research\repository.py backend\app\quant_research\features.py backend\app\quant_research\portfolio.py backend\app\quant_research\metrics.py backend\app\quant_research\validation.py backend\app\quant_research\risk.py backend\app\quant_research\allocation.py backend\app\quant_research\manifest.py backend\app\quant_research\readiness.py backend\app\quant_research\runner.py
python -m unittest discover backend\tests -v
```

在 Git Bash、WSL、macOS 或 Linux 运行完整 PostgreSQL 16 隔离矩阵：

```bash
scripts/ops/test_postgres_integration.sh
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
- 不要把数据筛选或 readiness 写成买卖建议。
- 不要恢复旧策略、旧回测、旧研究阶段或旧报告生成链路；新研究只使用新的协议层和验收口径。
