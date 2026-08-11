# 生产 PostgreSQL 运行角色与私有权限

本页定义 #159 的稳定角色合同。它只提供可审阅的 SQL、连接切换入口和验证方法，
不构成生产角色、密码、migration 或部署授权。

## 固定角色矩阵

| 角色 | LOGIN | public | private_workbench | DDL/管理能力 | 连接用途 |
| --- | --- | --- | --- | --- | --- |
| `quant_api_runtime` | 是 | 表 CRUD、序列使用 | 无 | 无 | 公共 API 的 `API_DATABASE_URL` |
| `quant_research_runtime` | 是 | 表 CRUD、序列使用 | 无 | 无 | 遗留角色；确认无连接与依赖前不得复用或删除 |
| `quant_personal_api` | 是 | 无表/序列权限 | 表 CRUD、序列使用 | 无 | 私有 API 的 `PRIVATE_DATABASE_URL` |
| `quant_personal_analysis` | 是 | 只读 | 表 CRUD、序列使用 | 无 | 个人分析与持仓规则 Worker 的 `PERSONAL_ANALYSIS_DATABASE_URL` |
| `quant_personal_mcp` | 是 | 无 | 所需事实/ledger 只读；仅两张 0023 ledger 表可 INSERT | 无 | 远端 MCP 的受保护数据库 URL 文件 |

五个定义角色固定为 `NOSUPERUSER`、`NOCREATEDB`、`NOCREATEROLE`、`NOREPLICATION`、
`NOBYPASSRLS` 和 `NOINHERIT`，不能在 `public` 创建对象。公共 API 与遗留研究角色对
`private_workbench` 没有 `USAGE`、表权限或序列权限。

当前公共运行路径没有完成逐表 command/read model 分离，因此 `quant_api_runtime` 仍需
对 `public` 表执行 CRUD。这比超级用户收敛很多，但不是最终的逐表最小权限；不得把
本票写成已经完成公共表级最小授权。个人角色与公开角色之间的私有 schema 隔离是本
阶段必须满足的安全边界。`quant_personal_mcp` 不继承私有 schema 的全表权限或未来表
default privileges；它只能读取 exact-five 当前需要的 workspace、holding、instrument
state、rule evaluation/revision 与两张 ledger 表，并只能向
`personal_tool_evidence_records`、`personal_capability_audit_events` 插入记录。它不能写
workspace、持仓、规则、分析、简报或其他业务表，也不复用个人 API/Worker 角色。
apply 会撤销它的全部既存 role membership；`NOINHERIT` 之外还必须证明它不能
`SET ROLE` 到个人 API/Worker。数据库 `TEMPORARY` 与 `public` schema 的 PUBLIC 默认
权限也由工件收敛，因此 MCP 不能通过临时表或 public 对象扩大存储面。

## 工件

- `scripts/ops/postgres_roles/apply.sql`：事务内创建或收紧角色、现有对象权限和默认
  权限。首次创建的角色没有密码；再次执行会保留已经受控配置的密码。
- `scripts/ops/postgres_roles/readback.sql`：读回角色属性、连接、schema、代表性表和
  default privileges。
- `scripts/ops/postgres_roles/rollback.sql`：撤销本工件授予的权限并删除五个角色。
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

应用前必须读回唯一 Alembic head 和数据库对象 owner。应用后使用五个独立 TCP 连接
验证 `current_user`，并验证允许路径与以下拒绝路径：

- 两个公共运行角色读取 `private_workbench` 必须得到权限拒绝；
- `quant_personal_api` 读取 `public` 必须得到权限拒绝；
- 五个角色在 `public` 创建表必须得到权限拒绝；
- `quant_personal_mcp` 创建临时表或 `SET ROLE` 到其他运行角色必须得到权限拒绝；
- 所有角色的超级用户、建库、建角色、复制和 bypass-RLS 属性均为 false；
- 新建测试表必须继承与现有表一致的 default privileges。

当前连接切换由 Compose 的 `API_DATABASE_URL` 和 `PRIVATE_DATABASE_URL` 承载。
`RESEARCH_WORKER_DATABASE_URL` 已随研究 Worker 从 Compose 与环境变量示例中移除；
不得重新用于启动服务。生产不得把公共数据库身份的兼容回退写成角色切换完成。

## 回滚边界

正常部署回滚优先恢复上一份受控 Compose/secret 配置，不自动删除角色。只有确认没有
活动连接、依赖对象或待恢复配置，并取得精确角色删除授权后，才能执行
`rollback.sql`。回滚后必须读回角色不存在、应用重新使用获批的旧连接身份，并验证
API、个人分析 Worker、队列和数据库健康。apply 对数据库 `TEMPORARY` 与 `public`
schema 的 PUBLIC 权限收紧不会由 rollback 猜测性放宽；若现场确需恢复不同 ACL，必须
以应用前读回为依据另行精确授权。
