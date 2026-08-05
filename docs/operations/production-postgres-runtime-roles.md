# 生产 PostgreSQL 运行角色与私有权限

本页定义 #159 的稳定角色合同。它只提供可审阅的 SQL、连接切换入口和验证方法，
不构成生产角色、密码、migration 或部署授权。

## 固定角色矩阵

| 角色 | LOGIN | public | private_workbench | DDL/管理能力 | 连接用途 |
| --- | --- | --- | --- | --- | --- |
| `quant_api_runtime` | 是 | 表 CRUD、序列使用 | 无 | 无 | 公共 API 的 `API_DATABASE_URL` |
| `quant_research_runtime` | 是 | 表 CRUD、序列使用 | 无 | 无 | 研究 Worker 的 `RESEARCH_WORKER_DATABASE_URL` |
| `quant_personal_api` | 是 | 无表/序列权限 | 表 CRUD、序列使用 | 无 | 私有 API 的 `PRIVATE_DATABASE_URL` |
| `quant_personal_analysis` | 是 | 只读 | 表 CRUD、序列使用 | 无 | 个人分析 Worker 的 `PERSONAL_ANALYSIS_DATABASE_URL` |

四个定义角色固定为 `NOSUPERUSER`、`NOCREATEDB`、`NOCREATEROLE`、`NOREPLICATION`、
`NOBYPASSRLS` 和 `NOINHERIT`，不能在 `public` 创建对象。公共 API 和研究 Worker 对
`private_workbench` 没有 `USAGE`、表权限或序列权限。

当前公共运行路径没有完成逐表 command/read model 分离，因此两个公共运行角色仍需
对 `public` 表执行 CRUD。这比超级用户收敛很多，但不是最终的逐表最小权限；不得把
本票写成已经完成公共表级最小授权。个人角色与公开角色之间的私有 schema 隔离是本
阶段必须满足的安全边界。

## 工件

- `scripts/ops/postgres_roles/apply.sql`：事务内创建或收紧角色、现有对象权限和默认
  权限。首次创建的角色没有密码；再次执行会保留已经受控配置的密码。
- `scripts/ops/postgres_roles/readback.sql`：读回角色属性、连接、schema、代表性表和
  default privileges。
- `scripts/ops/postgres_roles/rollback.sql`：撤销本工件授予的权限并删除四个角色。
  这是生产角色删除操作，必须单独授权，不能作为自动失败处理。

SQL 要求 `private_workbench` 已由目标 Alembic migration 创建，因此生产顺序是：先
完成备份恢复演练，再执行获批的 migration，最后应用本角色工件。密码和完整数据库
URL 在后续 secret 门禁配置，不写入 SQL、Git、Issue、命令参数或 shell history。

## 受控执行形状

以下命令只说明输入和读回边界，不构成执行授权。必须在 `quant-trading-prod` 的精确
release 目录运行，并使用当次 Compose 文件和环境文件。不要开启 shell trace，也不
要输出 `.env`。

```bash
docker compose exec -T db sh -lc \
  'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < scripts/ops/postgres_roles/apply.sql

docker compose exec -T db sh -lc \
  'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < scripts/ops/postgres_roles/readback.sql
```

应用前必须读回唯一 Alembic head 和数据库对象 owner。应用后使用四个独立 TCP 连接
验证 `current_user`，并验证允许路径与以下拒绝路径：

- 两个公共运行角色读取 `private_workbench` 必须得到权限拒绝；
- `quant_personal_api` 读取 `public` 必须得到权限拒绝；
- 四个角色在 `public` 创建表必须得到权限拒绝；
- 所有角色的超级用户、建库、建角色、复制和 bypass-RLS 属性均为 false；
- 新建测试表必须继承与现有表一致的 default privileges。

当前连接切换由 Compose 的 `API_DATABASE_URL`、`RESEARCH_WORKER_DATABASE_URL` 和
`PRIVATE_DATABASE_URL` 分别承载。留空时仍使用
现有公共数据库身份，以保证本地开发兼容；生产不得把该兼容回退写成角色切换完成。

## 回滚边界

正常部署回滚优先恢复上一份受控 Compose/secret 配置，不自动删除角色。只有确认没有
活动连接、依赖对象或待恢复配置，并取得精确角色删除授权后，才能执行
`rollback.sql`。回滚后必须读回角色不存在、应用重新使用获批的旧连接身份，并验证
API、已启用 Worker、队列和数据库健康。
