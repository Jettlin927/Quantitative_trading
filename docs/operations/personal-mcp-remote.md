# 远端个人 MCP loopback 与 SSH 隧道运维合同

本页定义 `quant-trading-prod` 上远端个人 MCP 的装配与稳定入口。代码已经提供固定
`127.0.0.1:16174` 的独立进程、默认关闭的 `personal-mcp` Compose profile、本机
`127.0.0.1:26174` SSH 隧道脚本和无 secret 客户端模板；这不表示生产已经配置、部署或
启用。生产执行、secret 创建或轮换仍留给 #266，并须针对精确 SHA 和窗口重新授权。
本地装配通过不构成生产授权。

稳定安全决定见 [ADR 0014](../adr/0014-remote-loopback-personal-mcp.md)。HTTP adapter 只把
官方 Streamable HTTP 的单一 `/mcp` 翻译给既有 `PersonalMcpGateway`，再调用唯一
`DomainToolRegistry`。DeepSeek Worker 仍直接使用内部 client-tool adapter 和 registry，
不导入 MCP，也不接收 MCP token。

## 固定装配

- `personal-mcp` profile 默认不启动，且 `PERSONAL_MCP_ENABLED` 默认是 `false`。
- 进程和宿主使用 host network，只绑定 `127.0.0.1:16174`；没有可配置 host、普通 Docker
  port publish、公共 Nginx route、域名或外部网络入口。
- 只提供 `/mcp` Streamable HTTP，不提供 SSE、WebSocket 或其他 endpoint。
- actor 只从 `PERSONAL_MCP_ACTOR_ID` 启动配置读取；远端端口、Origin
  `http://127.0.0.1:26174`、channel `mcp_streamable_http` 和 purpose
  `mcp_remote_read` 均由代码固定。
- 数据库 URL、bearer token、keyring、Alpaca 凭据与授权、新闻目录均通过只读文件或目录
  挂载。数据库 URL 与 token 不进入环境、命令参数、Compose render 或日志；MCP 容器不
  接收 DeepSeek secret。
- 数据库 URL 文件和 token 文件必须是 root owner、普通文件、owner-readable 且 group /
  other 无权限；缺失、空值、符号链接或权限不符时 fail-closed。其他凭据文件继续服从各自
  的 owner-only 合同。

`docker-compose.personal.yml` 使用以下宿主路径键；值只保存在受保护的服务器 `.env`，
不得写入仓库或 Issue：

| 键 | 内容 |
| --- | --- |
| `PERSONAL_MCP_DATABASE_URL_HOST_FILE` | 仅一行 `postgresql+psycopg` 的 `quant_personal_mcp` 最小权限角色 URL |
| `PERSONAL_MCP_TOKEN_HOST_FILE` | 独立 MCP bearer token |
| `PERSONAL_DATA_KEYRING_HOST_FILE` | 既有 keyring JSON |
| `ALPACA_CREDENTIALS_HOST_FILE` | 既有 Alpaca 凭据 JSON |
| `ALPACA_AUTHORIZATION_HOST_FILE` | 允许 `mcp_remote_read` 的新策略版本 |
| `INVESTMENT_NEWS_HOST_DIR` | 固定结构化新闻 checkout |

## 服务器 start、readback 与 stop

以下命令是 #266 获得生产授权后的稳定形状，不得在本票执行。`<项目名>` 与活动 release
目录必须现场读回，不从文档猜测。先验证合并配置，再只构建和启动 MCP；`--no-deps`
保证不会顺带重启或降低 DB：

```bash
docker compose -p <项目名> \
  -f docker-compose.yml -f docker-compose.server.yml -f docker-compose.personal.yml \
  --profile personal-mcp config >/dev/null
docker compose -p <项目名> \
  -f docker-compose.yml -f docker-compose.server.yml -f docker-compose.personal.yml \
  --profile personal-mcp build personal-mcp
docker compose -p <项目名> \
  -f docker-compose.yml -f docker-compose.server.yml -f docker-compose.personal.yml \
  --profile personal-mcp up -d --no-deps personal-mcp
```

readback 不携带 token，也不输出文件内容；健康探针必须从服务器 loopback 的 `/mcp` 得到
`401`，而不是成功调用工具：

```bash
docker compose -p <项目名> \
  -f docker-compose.yml -f docker-compose.server.yml -f docker-compose.personal.yml \
  --profile personal-mcp ps personal-mcp
test "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 \
  http://127.0.0.1:16174/mcp)" = 401
```

普通停止只停止 MCP，不执行 migration、downgrade、DB stop 或数据清理：

```bash
docker compose -p <项目名> \
  -f docker-compose.yml -f docker-compose.server.yml -f docker-compose.personal.yml \
  --profile personal-mcp stop personal-mcp
```

## 本机 SSH 隧道

脚本固定使用已配置的 `quant-trading-prod`、本地 `26174` 和远端 `16174`：

```bash
scripts/ops/personal_mcp_tunnel.sh start
scripts/ops/personal_mcp_tunnel.sh status
scripts/ops/personal_mcp_tunnel.sh stop
```

`start` 在连接前绑定探测本机端口；遇到端口占用直接失败，不静默改端口。SSH 使用
`ControlMaster=yes`、独立 ControlPath、`ControlPersist=no`、
`ExitOnForwardFailure=yes`、BatchMode 和 keepalive；启动后同时检查 ControlMaster 与
`/mcp` 的无 token `401`。`status` 任一检查失败即报告隧道断开或远端 MCP 不可达；
`stop` 只发送 `ssh -O exit`，重复执行安全，不停止服务器服务。

## Codex 与 Claude Code 无 secret 模板

Codex 模板是 `.codex/config.personal-mcp.example.toml`；把对应 table 合并到受信项目的
`.codex/config.toml`。模板使用 `bearer_token_env_var`，只记录环境变量名。Claude Code
模板是 `.mcp.json.example`；按需复制或合并到 `.mcp.json`，只使用 `type: http` 与
`Bearer ${PERSONAL_MCP_BEARER_TOKEN}`。两份模板都只指向
`http://127.0.0.1:26174/mcp`，不包含 token。

客户端 token 由获准渠道放入仓库已忽略的 owner-only 本机文件；不要把值粘贴到模板、
shell history 或命令参数：

```bash
chmod 600 .personal-mcp-token
PERSONAL_MCP_BEARER_TOKEN="$(<.personal-mcp-token)" codex
PERSONAL_MCP_BEARER_TOKEN="$(<.personal-mcp-token)" claude
```

Codex 当前的 Streamable HTTP 配置支持 `bearer_token_env_var`；Claude Code 的项目 JSON
支持在 HTTP `headers` 中展开环境变量。客户端启动前先运行 tunnel `status`，客户端内再
检查 exact-five；没有 token、隧道或远端服务时必须失败，不回退 stdio 或离线推断。

## token 轮换、kill switch 与回滚

HTTP app 在进程启动时一次读取 token。token 轮换必须原子替换 owner-only 宿主文件后，
强制重建 MCP 容器；只改挂载文件而不重启不会切换内存中的 token：

```bash
docker compose -p <项目名> \
  -f docker-compose.yml -f docker-compose.server.yml -f docker-compose.personal.yml \
  --profile personal-mcp up -d --no-deps --force-recreate personal-mcp
```

随后重新启动使用新本机 token 文件的 Codex/Claude 客户端。轮换不需要重建 SSH 隧道；
旧 token 必须以真实 401 读回，exact-five 和 audit 仍需重新验收。创建或轮换任何真实
token 都属于 #266 的明确授权范围。

kill switch 是先在受保护服务器配置中设置 `PERSONAL_MCP_ENABLED=false`，再执行
`stop personal-mcp`，最后确认服务器 `16174` 不再监听、本机 tunnel `status` 失败。紧急
关闭和版本回滚都只停止或恢复这一个 profile：不执行 migration，不停止或降低 DB，
不删除 evidence/audit，不改 registry、Today、API、个人分析 Worker、自动简报或 stdio
测试 adapter。

代码、Compose config、本机脚本测试、生产部署、容器启动、真实隧道、真实 exact-five、
audit 与业务验收必须分层报告；本票只覆盖前四项中的本地代码与静态合同。
