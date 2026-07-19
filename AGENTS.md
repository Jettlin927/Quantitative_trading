# AGENTS.md

本文件是本仓库的项目主管规则。用户常用语言是中文，除非用户明确切换语言，说明、计划、提交说明和交互文案默认使用中文。

详细代码导航看 `docs/agent-code-map.md`。后续 Agent 接手时，先读 `docs/agent-handoff.md`。

## 当前目标

截至 2026-07-19，用户已明确重新开启“量化研究底座”，但仍保持研究模拟与真实交易隔离。可信工程的代码、隔离 PostgreSQL、远端 sandbox、独立反例审计以及经用户确认的生产 schema 迁移与发布均已完成；生产 revision 仍为 `0006_worker_heartbeats`。2026-07-13 起新增的六条静态研究策略、可信长历史报告和统一结果目录属于仓库研究能力，不能据此声称已经部署到生产。生产底座验收证据见 `docs/deployment/2026-07-12-production-trustworthiness-acceptance.md`。

当前主线包括：

- A 股 Tushare 数据同步、持久化、覆盖度查询和原始数据展示。
- 交易日历、复权因子、指数、ETF、申万行业及研究所需的历史可交易性数据。
- 美股 sample 观察池、sample 快照、sample 持仓结构入库和只读展示。
- PostgreSQL schema、幂等 upsert、同步日志、数据覆盖度和最小数据管理前端。
- 新建的 `backend/app/quant_research/` 纯研究协议层：point-in-time 数据集、研究组合模拟、基准指标、walk-forward、运行清单和 readiness 门禁。
- 六条源码静态登记策略，以及 `docs/research/strategy-results/` 下当前可信报告与旧档案分层的只读统一入口。
- `backend/app/data_quality/` 研究范围级数据质量、canonical 输入快照、可复现运行和显式中断续跑。
- PostgreSQL 持久任务、独立租约 worker、重启恢复和 worker 心跳。

当前主线仍不包括：

- 真实交易信号、盘中实时策略服务、自动调参/自动发布策略、策略收益承诺。
- AI 复盘、交易评级、真实持仓导入、券商连接或任何真实资金动作。

旧策略文件和旧回测证据不作为新底座实现来源。新增研究能力必须使用新的目录、合同、验证口径和运行清单。

## 默认架构

默认架构是四容器本地系统：

- `frontend`：React + Vite，目录 `frontend/`，宿主机端口默认 `15173`，容器内监听 `5173`。
- `api`：FastAPI + SQLAlchemy 2.0，目录 `backend/`，宿主机端口默认 `18000`，容器内监听 `8000`。
- `worker`：复用后端镜像，通过 PostgreSQL 租约执行持久同步任务，不对外暴露端口。
- `db`：PostgreSQL 16，使用 Docker volume 持久化本地数据。

`docker-compose.yml` 是启动入口。不要把项目退回到单文件静态 HTML 方案，除非用户明确要求。

## 目录边界

- `backend/app/models.py`：应用 schema 合同；生产 schema 演进必须通过 `backend/migrations/` 的 Alembic revision。
- `backend/app/main.py`：数据 API、持久任务入队、Tushare 适配、美股 sample 导入和 DB overview；长同步不能回到 API 进程内执行。
- `backend/app/sync_worker.py`：PostgreSQL 租约、抢占、心跳、退避和崩溃恢复。
- `backend/app/data_quality/`：研究范围级完整性规则与持久质量运行。
- `backend/app/tushare_client.py`：Tushare token、日期和 Decimal 清洗。
- `backend/app/us_research.py`：美股 sample 文件到 DB preview 的适配器。
- `backend/app/quant_research/`：无券商、无实盘副作用的量化研究协议层；执行、净值、指标和通用报告统计诊断的公式只能在这里定义。
- `backend/app/quant_research/reporting.py`：报告共用的收益序列、尾部风险、HAC alpha、DSR/PBO 统计口径；HTML 生成脚本不得复制一套同名算法。
- `backend/app/strategy_results.py`：把 `docs/research/strategy-results/manifest.json` 投影为只读 API；兼容当前 `summaryJson` 报告包和旧 phased/csv 档案。
- `scripts/research/render_*_report.py`：只负责从 canonical 工件组装表格、图形和中文叙事；不得重定义成交、成本、首日本金或核心绩效口径。
- `frontend/`：只展示数据覆盖、同步状态、A 股样本、美股 sample 入库状态。
- `my_quant/us_research/`：只保留美股 sample 文件、快照刷新脚本和配置；不保留报告生成或回测脚本。
- `docs/research/a-share-data/`：只记录 A 股数据源、DB 覆盖和同步事实。
- `docs/research/strategy-results/`：已完成研究的只读发布层；`index.html` 是统一入口，`manifest.json` 是机器清单，每个当前报告包至少含 `index.html` 与 `summary.json`。

不要恢复 `strategy_research`、`backtest-reports`、`strategy-lab`、`research_engine` 等旧主线目录。新的研究底座统一放在 `backend/app/quant_research/`，一次性运行产物放在被 Git 忽略的 `outputs/research-runs/`。

## 数据安全红线

PostgreSQL volume 是本地行情、基本面、同步记录和 sample 入库数据的持久化来源。不要执行以下操作，除非用户明确确认会丢数据：

```powershell
docker compose down -v
docker volume rm ...
```

不要把 `.env`、Tushare token、数据库密码、真实持仓、真实成交、券商导出或任何凭据写入源码、前端、日志、README 或测试。

Tushare token 默认来自 `.env` 的 `TUSHARE_TOKEN`。请求体临时传 token 只作为调试兜底，不应成为前端常规交互。

## 交易边界

本仓库现在不是交易系统。允许离线研究和组合模拟，但禁止：

- 连接真实券商、交易账户、资金账户或下单接口。
- 自动下单、撤单、调仓、融资融券、申购赎回或任何真实资金动作。
- 把数据展示、样本持仓或观察池写成买入、卖出、持有、评级或收益承诺。
- 导入真实持仓、真实成交或券商导出，除非用户明确确认新的数据治理方案。

允许：

- 同步和展示 Tushare A 股数据。
- 导入和展示脱敏/sample 美股数据。
- 查询 DB 表、行数、日期覆盖、最新快照和同步日志。
- 使用复权、point-in-time 和下一交易日执行口径进行离线研究模拟。
- 输出带数据快照、代码版本、参数哈希、基准和限制项的研究运行清单。

## 策略研究交付规范

用户要求研究、分析、评估、回测或比较任何策略时，必须先完整阅读 `docs/research/strategy-evaluation-standard.md`，并按该文件的固定顺序交付。不得只凭累计收益、年化收益或 Sharpe 判断策略好坏。

每份策略研究至少必须包含：

- 强制结论状态：`研究通过`、`有条件候选`、`证据不足`、`blocked` 或 `不通过`。
- 策略画像、经济假设、适用与失效条件。
- point-in-time 数据、历史 universe、执行时点、成本和可复现证据。
- 样本外总体指标、基准对照、交易/风险/容量指标。
- 上涨/下跌/震荡、高/低波动、逐年及压力期的市场环境矩阵。
- walk-forward、参数邻域、成本压力和试验次数；多次筛选时增加 DSR/PBO。
- “支持证据、反对证据、尚缺证据”和明确限制项。

缺匹配基准、净成本、test/OOS、市场环境覆盖、关键可交易性、试验登记或复现身份时，不得输出 `研究通过`；应按规范标记 `证据不足` 或 `blocked`。baseline/sentinel 管线成功不等于策略具有 alpha。

HTML 和其他面向用户的策略报告必须优先使用可读的中文方案名称，不能用 `T0`、`V2`、`baseline_a` 等内部编号代替名称。确需保留内部编号时，必须在首屏结论、指标和图表之前先列出“编号—名称—具体规则”对照，后文采用“名称（编号）”格式；运行清单和复现字段可以保留原始编号。

研究期首存在由上一信号日触发的首个执行日时，总体收益、基准、成本和回撤必须从显式初始净值/本金计算，不能用首个收盘净值重新归一化而漏掉首日收益或费用。

完成面向用户的研究报告后：

- canonical 运行目录继续保存在被 Git 忽略的 `outputs/research-runs/`，不得把大型账本复制进文档目录。
- 当前可信报告包写入 `docs/research/strategy-results/<report-id>/`，并登记到 `manifest.json`；统一入口 `docs/research/strategy-results/index.html` 必须能直达报告和机器摘要。
- 断网复现次数、镜像和结果指纹必须来自可提交的 `reproduction-evidence-*.json` 并在渲染前逐项校验；不得在 HTML/JSON 生成器中硬编码“复现通过”。证据缺失、轮次不足或指纹不符时必须停止发布。
- `researchDate` 表示研究口径日期；`reportGeneratedAt` 必须从本报告最新 canonical manifest 的 `generatedAt` 确定性派生。API 和页面展示报告生成时间时不得回退冒用研究日期。
- 旧管线结果必须标记 `legacy`/“历史档案”；API 的 `summary.status` 统一取 manifest 的研究状态，旧脚本原始 `status=ok` 只能放在 `sourceExecutionStatus`，不得映射为 `研究通过`。
- HTML/JSON 是 canonical 运行的只读投影；如两者不一致，以冻结输入、代码/环境身份、manifest 和 result fingerprint 为准，并重新生成报告。

高频、期权、做市或其他非日频股票/ETF策略必须声明本规范中的不适用项并补充专项指标，不能静默省略。

## 启动与构建

日常启动使用：

```powershell
.\启动数据工作台.cmd
```

它应该只执行 `docker compose up -d`。不要在日常启动脚本里加入 `--build`。

修改 Dockerfile、依赖文件、后端代码或前端代码后，使用：

```powershell
.\重新构建并启动数据工作台.cmd
```

停止服务使用：

```powershell
.\停止数据工作台.cmd
```

`.cmd` 启动脚本必须保留：

```bat
chcp 65001 >nul
cd /d "%~dp0"
```

## 后端规则

`stock_daily_bars` 按 `ts_code + trade_date` 去重 upsert。

`stock_daily_basic` 按 `ts_code + trade_date` 去重 upsert。

`stock_financial_indicators` 按 `ts_code + end_date + ann_date` 去重 upsert。

`stock_listings` 按 `ts_code` 去重 upsert；`stock_limit_prices`、`stock_adjust_factors` 和 `fund_adjust_factors` 按 `ts_code + trade_date` 去重 upsert。

`stock_suspend_events` 按 `ts_code + trade_date + suspend_type + suspend_timing` 去重 upsert。

美股 sample 表使用自然键去重：

- `assets.natural_key`
- `asset_daily_prices.natural_key`
- `watchlist_items.natural_key`
- `portfolio_snapshots.snapshot_id`

API 返回必须 JSON-safe。指标和数值中的 `NaN`、`Infinity` 必须转成 `None/null` 或可展示兜底值。

## 前端规则

前端使用 React + Vite，目录 `frontend/`。涉及页面、组件、布局或视觉优化时，必须先阅读 `.codex/skills/frontend-design/SKILL.md`。

审美方向是工业化数据终端：高信息密度、克制、网格感、纪律感。第一屏必须是可操作的数据工作台，不做营销落地页。

前端只能展示：

- API/DB 状态。
- A 股表覆盖、同步日志、股票样本、估值和财务覆盖。
- 美股 sample 入库状态、sample 资产、sample 观察池和 sample 持仓快照。

前端不能出现策略评估、回测执行、交易信号、买卖评级、AI 复盘或真实账户入口。

## 简单与手术改变

用最少代码解决当前问题。不要为一次性需求做抽象，不要加入未要求的灵活性，不要顺手重构无关代码。

每一行修改都应该能追溯到用户请求或验证需要。发现无关死代码可以指出，但不要擅自删除，除非用户明确要求清理该类内容。

## Windows 与编码

用户在 Windows/PowerShell 环境工作。处理包含中文或特殊字符的路径时必须正确引用。

创建或编辑 PowerShell 脚本使用 UTF-8 with BOM。读取或输出中文文档时显式使用 UTF-8，避免乱码。

不要把 Windows 路径硬编码到容器内部。容器内路径默认以 `/app` 为准。

## 必须先问用户

以下情况必须先停下来问用户：

- 会删除数据库 volume 或持久化数据。
- 会改变 Tushare token、数据库密码或端口约定。
- 会引入新的大型依赖、框架替换或数据库迁移工具。
- 会对生产 PostgreSQL 执行 baseline stamp、Alembic upgrade、`DROP INDEX` 或覆盖性恢复。
- 会导入真实持仓、真实成交、券商导出或连接真实账户。
- 会把当前离线研究底座升级为实时信号、自动研究发布或真实交易系统。

## 阶段操作日志

仓库根目录维护 `操作日志.md`。每个阶段性任务开始或结束时，Codex 应追加一条日志，至少包含：

- 时间：使用本机时间，格式尽量为 `YYYY-MM-DD HH:mm +08:00`。
- 阶段目标：本阶段打算解决什么问题。
- 实际操作：列出改过的文件、关键命令和重要决策。
- 验证结果：写明已运行的检查；如果没验证或验证失败，如实写原因。
- 后续事项：仍未完成、需要用户确认或下一阶段要接着做的事。

日志只记录事实和工程判断，不写入 `.env`、token、密码或真实凭据。

## 验证清单

改后端时至少运行：

```powershell
python -m py_compile backend\app\database.py backend\app\models.py backend\app\schemas.py backend\app\tushare_client.py backend\app\us_research.py backend\app\main.py backend\app\sync_worker.py backend\app\quant_research\metrics.py backend\app\quant_research\reporting.py backend\app\strategy_results.py
python -m unittest discover backend\tests -v
```

涉及 migration、质量、快照、runner 或 worker 时，还必须在 Git Bash/WSL/macOS/Linux 运行：

```bash
scripts/ops/test_postgres_integration.sh
```

改前端时优先运行：

```powershell
docker compose run --rm frontend npm run build
```

改 Compose 时运行：

```powershell
docker compose config
```

如果 Docker daemon 权限不足或 Docker Desktop 未运行，不要假装已验证。

## 服务器 CI/CD 优先

云服务器运行入口为 `ubuntu@182.254.180.169`，默认部署目录为 `/opt/quantitative-trading`。后续 Agent 需要构建、验证、重启或排查本仓库服务时，优先通过 SSH 在服务器执行 Docker/CI/CD 流程，减少用户本地电脑资源消耗。

本地 `.env` 维护远端连接变量：`REMOTE`、`REMOTE_SSH_PORT`、`REMOTE_SSH_KEY`、`PROJECT_DIR`、`REPO_URL`、`BRANCH`。优先使用 `scripts/ops/deploy_remote.sh` 和 `.env` 中的 SSH key；不要依赖交互式密码登录。

### SSH 直连方式

- 服务器：`ubuntu@182.254.180.169:22`
- 默认私钥路径：`~/.ssh/quantitative_trading_server_ed25519`
- 默认部署目录：`/opt/quantitative-trading`

本机可直接连接：

```bash
ssh -i ~/.ssh/quantitative_trading_server_ed25519 -p 22 ubuntu@182.254.180.169
```

推荐在本机 `~/.ssh/config` 配置别名，便于复用部署、巡检和端口转发命令：

```sshconfig
Host quant-trading-server
  HostName 182.254.180.169
  User ubuntu
  Port 22
  IdentityFile ~/.ssh/quantitative_trading_server_ed25519
  IdentitiesOnly yes
```

配置后使用 `ssh quant-trading-server` 登录。私钥内容、密码和 token 不得写入本仓库；如果本机密钥位置不同，仅在本地 `.env` 或 `~/.ssh/config` 中调整。

默认远端操作顺序：

1. 在本地确认工作区状态和将要同步的文件，不把 `.env`、token、密码、真实持仓、真实成交或其他凭据打包上传。
2. 通过 SSH 登录服务器，在 `/opt/quantitative-trading` 更新代码或接收当前源码包。
3. 在服务器运行 `docker compose config`。
4. 修改 Dockerfile、依赖、后端或前端后，在服务器运行 `docker compose up -d --build`；只重启已有服务时运行 `docker compose up -d`。
5. 在服务器运行 `docker compose ps`，并用 `curl` 验证 `http://127.0.0.1:18000/api/health` 和 `http://127.0.0.1:15173`。
6. 若需查看 PostgreSQL，只在服务器内网或容器网络访问；不要把 `5432` 暴露给公网。

## 服务器访问隧道

服务器上的 `5432`、`18000`、`15173` 默认只监听 `127.0.0.1`，不是公网反代。用户从本机访问远端工作台时，优先使用 SSH tunnel：

```bash
ssh -M -S /tmp/quant-trading-tunnel-ctl -fN \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -L 15173:127.0.0.1:15173 \
  -L 18000:127.0.0.1:18000 \
  quant-trading-server
```

隧道建立后，本机打开：

```text
http://localhost:15173
```

这条隧道运行在用户本机上；本机关机、重启、睡眠或断网后隧道会消失，但服务器上的 Docker 服务不会因此停止。重开电脑后需要重新执行上面的 tunnel 命令。

关闭隧道使用：

```bash
ssh -S /tmp/quant-trading-tunnel-ctl -O exit quant-trading-server
```

服务器 `.env` 可设置 `PIP_INDEX_URL`、`PIP_TRUSTED_HOST` 和 `NPM_CONFIG_REGISTRY`，用于让远端 Docker build 走更稳定的 pip/npm 镜像源；本地不配置这些变量时仍使用默认官方源。

服务器部署仍必须遵守数据安全红线。禁止执行会删除远端 PostgreSQL volume 的命令，例如 `docker compose down -v` 或 `docker volume rm`，除非用户明确确认接受远端数据丢失。
