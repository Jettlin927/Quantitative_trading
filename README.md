# 美股个人投研工作台

本仓库以美股为主，提供手工持仓、观察规则、市场证据、个人 AI 分析和正式量化研究的只读工作台。市场观察主线使用 Alpaca；个人数据保存在隔离的私有 schema，正式研究继续遵守冻结计划、人工批准、可复现证据和一致发布合同。

它不是交易系统：不连接真实券商或资金账户，不自动下单，不从券商同步持仓，也不把研究结果写成买卖评级或收益承诺。

## 产品边界

| 能力 | 实现 |
| --- | --- |
| 今日与持仓 | 手工美股持仓、每日权益、确定性规则与关注事项 |
| 市场证据 | Alpaca 美股行情、来源授权、健康度与可追溯证据 |
| 个人分析 | 与正式研究隔离的 tool-use AI 分析队列 |
| 正式研究 | point-in-time 输入、冻结计划、人工批准、结构化评价和不可覆盖发布 |
| 工程底座 | React、FastAPI、PostgreSQL 16、Alembic、Docker Compose |

[架构文档](docs/architecture/) · [美股数据边界](docs/data/us/) · [研究合同](docs/research/contracts/) · [运维手册](docs/operations/)

## 已退役边界

- A 股产品入口、Tushare 拉取、公共数据同步 Worker 和相关策略执行入口已退役。
- `us_experiment_*` 的 AKShare/yfinance 免费实验链路和旧 `my_quant` sample/HSBC ledger 已退役。
- 个人工作台的不可变记录版本链路已退役；正式研究发布的不可覆盖合同不受影响。
- 既有 Alembic revision、历史表定义、已发布研究证据和 dated 审计文档继续保留。测试可用合成数据验证这些 schema，但不得重新接入退役数据源或把历史表当作当前产品能力。

决策见 [ADR 0010](docs/adr/0010-us-first-workbench-and-retired-legacy-data-paths.md)，实施路线图见 [GitHub Issue #214](https://github.com/Jettlin927/Quantitative_trading/issues/214)。

## 系统组成

- `frontend`：今日、持仓、美股市场、规则和系统健康的工作界面；不再暴露旧 A 股策略驾驶舱。
- `api`：美股个人工作台、市场观察、研究目录、评价、发布和数据质量接口。
- `db`：PostgreSQL 16，保存公开研究事实与隔离的私有工作台数据。
- `personal-analysis-worker`：个人 AI 分析队列，只通过私有 Compose 覆盖启用。
- `research-worker`：正式研究队列，只通过 `research-automation` profile 和人工门禁启用。
- `outputs/research-runs/`：被 Git 忽略的 canonical 研究工件。

模块职责见[代码地图](docs/architecture/code-map.md)，运行流程见[系统流程](docs/architecture/system-flow.md)。

## 快速开始

复制环境变量模板，只在本机填写需要的值：

```powershell
Copy-Item .env.example .env
notepad .env
```

新建空开发库时显式建立 schema；API 不会自动执行 migration：

```powershell
docker compose up -d db
docker compose build api frontend
docker compose run --rm api alembic upgrade head
docker compose up -d api frontend
```

这组命令只建立本地 schema，不拉取 A 股或旧美股实验数据。需要个人工作台写能力时，另按[个人工作台 secret 与 Compose 覆盖](docs/operations/personal-workbench-secrets.md)配置；缺少私有配置时相关路由应 fail-closed。

默认本机入口为前端 `http://localhost:15173`、API 文档 `http://localhost:18000/docs` 和 PostgreSQL `localhost:5432`。生产端口和版本必须现场核验。远程访问仅使用 SSH 隧道，见[生产部署与家庭电脑访问合同](docs/operations/production-deployment-and-home-access.md)。

## 开发验证

```bash
DATABASE_URL=sqlite+pysqlite:///:memory: python -m unittest discover -s backend/tests -p 'test_*.py' -v
scripts/ops/test_postgres_integration.sh
scripts/ops/test_frontend_production_image.sh
docker compose config
```

PostgreSQL 集成测试只建立 schema 并写入合成夹具，不需要任何市场数据 token。完整门禁与生产限制见 [AGENTS.md](AGENTS.md)。

## 文档导航

- [文档总入口](docs/index.md)
- [产品范围](docs/product/)
- [架构与代码地图](docs/architecture/)
- [美股数据边界](docs/data/us/) / [A 股退役 schema 边界](docs/data/a-share/)
- [研究合同](docs/research/contracts/) / [研究指南](docs/research/guides/)
- [运维手册](docs/operations/) / [历史验收证据](docs/acceptance/)
- [历史归档](docs/archive/)

活动路线图只存在于 [GitHub Issues](https://github.com/Jettlin927/Quantitative_trading/issues)。当前仓库整改由 [Issue #214](https://github.com/Jettlin927/Quantitative_trading/issues/214) 承接；README 和 dated 文档不承担当前任务状态。

## 安全提醒

- 不提交 `.env`、数据库密码、Alpaca/DeepSeek/GitHub token、SSH 私钥或其他凭据。
- 不从券商同步真实账户、成交或订单；持仓只允许用户手工维护。
- 未经明确批准，不执行生产 migration、部署、数据删除、volume 清理或凭据变更。
