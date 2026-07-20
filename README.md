# 量化研究系统

本仓库用于同步实际市场数据、执行可复现的离线量化研究，并把研究计划、运行事实、证据、评价和结论以只读方式提供给用户查看。

它不是交易系统：不连接券商或真实账户，不导入真实持仓与成交，不下单，也不把研究结果写成投资建议或收益承诺。

## 产品边界

系统允许：

- 同步、持久化、质检和展示 A 股实际市场数据。
- 明确隔离并展示美股 sample 数据，直到研究级数据合同另行批准。
- 使用 point-in-time、历史 universe、复权和下一交易日执行口径做离线研究。
- 保存冻结研究计划、运行、证据、结构化评价、强制结论和后续研究提案。
- 通过 GitHub Issues、API、研究驾驶舱和不可变工件发布研究过程与结果。

系统禁止：

- 连接真实券商、交易账户或资金账户。
- 自动下单、撤单、调仓、融资融券或执行其他真实资金动作。
- 把运行成功等同于研究通过，或把研究结论转成买入、卖出、持有与收益承诺。

统一术语见 [CONTEXT.md](CONTEXT.md)，稳定工程规则见 [AGENTS.md](AGENTS.md)。

## 系统组成

- `frontend`：React + Vite 构建、Nginx 静态服务的只读研究与数据界面。
- `api`：FastAPI + SQLAlchemy 2.0 的数据、研究和运维接口。
- `worker`：执行持久化数据同步任务，与 API 请求进程分离。
- `db`：PostgreSQL 16，保存市场数据、任务和结构化研究事实。
- `backend/app/quant_research/`：无券商副作用的研究协议、模拟、指标和复现能力。
- `outputs/research-runs/`：被 Git 忽略的 canonical 运行工件；可提交的报告投影继续位于 `docs/research/strategy-results/`。

模块职责和代码入口见 [架构文档](docs/architecture/)。

## 快速开始

复制环境变量模板，并只在本机填写真实值：

```powershell
Copy-Item .env.example .env
notepad .env
```

新建空开发库时，先显式建立 schema；API 不会自动迁移数据库：

```powershell
docker compose up -d db
docker compose build api worker
docker compose run --rm api alembic upgrade head
```

已有业务表但没有 `alembic_version` 时，不得直接执行 `upgrade head`。应先按 [运维文档](docs/operations/)核对 schema fingerprint 和人工门禁。

日常启动、重建和停止：

```powershell
.\启动数据工作台.cmd
.\重新构建并启动数据工作台.cmd
.\停止数据工作台.cmd
```

默认本机入口为前端 `http://localhost:15173`、API 文档 `http://localhost:18000/docs` 和 PostgreSQL `localhost:5432`。生产监听与访问方式必须以现场配置为准，不能从 README 推断。

当前远程访问只使用 SSH 隧道：通过本机 SSH 配置连接服务器，把远端 loopback 前端映射到本机后访问 `http://127.0.0.1:15173`。本项目不为此购买或申请域名，不部署 Cloudflare/Tailscale 入口，也不开放公网 IP 端口；完整决定见[远程访问决策](docs/operations/private-https-authentication-decision.md)。新电脑上的 Codex 在部署、迁移、切换或配置 Windows 常驻隧道前，必须遵守[生产部署与家庭电脑访问合同](docs/operations/production-deployment-and-home-access.md)。

## 开发验证

后端快速门禁：

```bash
DATABASE_URL=sqlite+pysqlite:///:memory: python -m unittest discover -s backend/tests -p 'test_*.py' -v
```

涉及 migration、数据质量、快照、runner 或 Worker 时，追加隔离 PostgreSQL 验证：

```bash
scripts/ops/test_postgres_integration.sh
```

前端与 Compose：

```bash
scripts/ops/test_frontend_production_image.sh
docker compose config
```

完整门禁和生产限制见 [AGENTS.md](AGENTS.md)。

## 文档导航

- [文档总入口](docs/index.md)
- [产品范围](docs/product/)
- [架构与代码地图](docs/architecture/)
- [A 股数据](docs/data/a-share/) / [美股数据边界](docs/data/us/)
- [研究合同](docs/research/contracts/) / [研究指南](docs/research/guides/)
- [运维手册](docs/operations/) / [不可变验收证据](docs/acceptance/)
- [当前与历史研究结果入口](docs/research/strategy-results/)
- [历史归档](docs/archive/)

活动路线图只存在于 GitHub Issues：[寻路地图](https://github.com/Jettlin927/Quantitative_trading/issues/3)与[第一阶段实施总览](https://github.com/Jettlin927/Quantitative_trading/issues/17)。README、dated 文档和 `操作日志.md` 不承担当前任务状态。

## 安全提醒

- 不提交 `.env`、Tushare token、数据库密码、SSH 私钥或其他凭据。
- 不提交真实账户、持仓、成交或券商导出。
- 未经用户明确批准，不执行生产 migration、生产切换、覆盖恢复、旧服务器清理或任何 volume 删除。
