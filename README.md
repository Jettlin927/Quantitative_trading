# 美股个人投资工作台

本仓库提供手工持仓、市场观察、确定性规则提醒、个人 AI 分析和系统健康检查。市场
数据通过 Alpaca 按用途只读访问，个人数据保存在隔离的私有 schema。

它不是量化研究平台或交易系统：不连接券商或资金账户，不自动同步持仓，不执行
交易，也不把行情、规则或 AI 输出写成评级、收益承诺或自动执行指令。

## 当前能力

| 能力 | 实现 |
| --- | --- |
| 今日事项 | 汇总活跃持仓的规则命中与数据缺口 |
| 手工组合 | 持仓、现金、买卖事实、每日权益与已实现盈亏 |
| 市场观察 | Alpaca 美股行情、来源授权、健康度与时点 |
| 个人分析 | 显式确认后由私有 Worker 执行的 tool-use AI 分析 |
| 工程底座 | React、FastAPI、PostgreSQL 16、Alembic、Docker Compose |

[产品范围](docs/product/) · [系统流程](docs/architecture/system-flow.md) ·
[代码地图](docs/architecture/code-map.md) · [运维手册](docs/operations/)

## 遗留边界

A 股数据链、旧美股实验、旧 sample/HSBC ledger 和量化研究系统均已退出当前产品。
仓库内仍可能存在相应代码、脚本、配置、API、migration、表、测试、工件和历史文档；
这些是兼容或审计资产，不是当前能力。物理清理必须经过独立依赖审计和明确授权，见
[ADR 0011](docs/adr/0011-personal-investment-workbench-without-research.md)。

## 系统组成

- `frontend`：今日、持仓、美股标的和系统健康界面。
- `api`：私有工作台、市场观察和健康 API。
- `db`：PostgreSQL 公共 schema 与隔离的 `private_workbench` schema。
- `personal-analysis-worker`：AI 分析队列与持仓规则周期评估，只通过私有 Compose 覆盖启用。

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

需要私有写能力时，按[个人工作台 secret 与 Compose 覆盖](docs/operations/personal-workbench-secrets.md)
配置；缺少私有配置时相关路由应 fail-closed。

默认本机入口为前端 `http://localhost:15173`、API 文档
`http://localhost:18000/docs` 和 PostgreSQL `localhost:5432`。生产端口和版本现场
核验；远程访问只使用 [SSH 隧道](docs/operations/production-deployment-and-home-access.md)。

## 开发验证

```bash
DATABASE_URL=sqlite+pysqlite:///:memory: python -m unittest discover -s backend/tests -p 'test_*.py' -v
scripts/ops/test_postgres_integration.sh
scripts/ops/test_frontend_production_image.sh
docker compose config
```

测试只使用合成数据，不需要市场 token 或真实个人数据。按改动选择验证范围，见
[变更验证](docs/agents/validation.md)。

## 安全提醒

- 不提交 `.env`、数据库密码、Alpaca/DeepSeek/GitHub token、SSH 私钥或其他凭据。
- 不从券商、邮箱或导出文件自动同步真实账户、成交或订单；组合只由用户手工维护。
- 未经明确批准，不执行生产 migration、部署、数据删除、volume 清理或凭据变更。
