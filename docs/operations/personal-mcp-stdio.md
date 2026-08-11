# 非生产只读 MCP stdio 启用与关闭

本页只定义非生产/测试 `stdio` adapter 的稳定操作边界，不构成真实 MCP 启用授权，也不授权
生产部署、生产配置变更或生产数据操作。仓库不保存 MCP 客户端配置、actor、数据库连接串、
keyring、来源凭据或其他 secret。

共同安全合同以 [ADR 0013](../adr/0013-local-read-only-personal-mcp.md) 为准；生产拓扑已由
[ADR 0014](../adr/0014-remote-loopback-personal-mcp.md)改为 `quant-trading-prod` loopback
Streamable HTTP + SSH 隧道。`stdio` 仍只运行本机子进程 stdin/stdout，不监听 TCP，
不提供 HTTP、SSE 或远程转发，也不能冒充生产验收。

## 默认关闭验收

`PERSONAL_MCP_ENABLED=false` 是唯一默认值。缺省或设为 `false` 时，进程必须在装配
数据库、keyring、来源或 stdio 服务前退出，返回码为 `2`，stdout 为空，stderr 只包含
稳定错误 `personal_mcp_disabled`。

可在仓库根目录使用依赖已安装的 Python 做无 secret 检查：

```bash
PYTHON_BIN=/absolute/path/to/python
env -u PERSONAL_MCP_ENABLED \
  "$PYTHON_BIN" -m backend.app.personal_workspace.mcp_server
test "$?" -eq 2
```

官方 SDK 集成检查不连接真实数据库或来源：

```bash
PYTHON_BIN=/absolute/path/to/python
"$PYTHON_BIN" -m unittest -v \
  backend.tests.test_personal_mcp_server.PersonalMcpStdioIntegrationTest
```

## 获批后的本机启动模板

真实启用前必须另行确认本机、actor、客户端、配置入口和验收窗口。运行环境必须通过受保护
入口提供以下键，不把值写进命令参数、仓库、Issue、日志或客户端可见的工具参数：

- `PERSONAL_MCP_ENABLED=true`
- `PERSONAL_MCP_ACTOR_ID`
- `PRIVATE_DATABASE_URL`
- `PERSONAL_DATA_KEYRING_FILE`
- `ALPACA_CREDENTIALS_FILE`
- `ALPACA_AUTHORIZATION_FILE`
- `INVESTMENT_NEWS_DIR`

数据库必须是 PostgreSQL，actor 必须已有私有 workspace，keyring 与来源文件必须使用既有
受保护入口。任一条件缺失时保持 fail-closed，不创建 workspace、不回退到公共数据，也不
临时放宽权限。

获批的 MCP 客户端或本机进程 supervisor 只运行以下程序；不要增加端口、网络 transport
或远程转发：

```bash
python -m backend.app.personal_workspace.mcp_server
```

启动读回必须分别确认：官方 SDK 版本为 `1.29.0`；发现列表恰好是 ADR 0013 的五个
canonical 只读工具；最小调用返回 actor 范围内证据；capability audit 只保存 canonical
工具名、参数 SHA-256 和证据引用，不保存完整参数、结果或 secret。安装客户端配置、启动
真实子进程和执行真实调用仍是三个独立状态，不能用代码或测试通过代替。

## 紧急关闭与回滚

1. 停止 MCP 子进程，等待 supervisor 确认进程已退出。
2. 从获批的本机配置入口移除 `PERSONAL_MCP_ENABLED=true`，恢复
   `PERSONAL_MCP_ENABLED=false`，并撤销客户端中的该本机进程注册。
3. 重跑“默认关闭验收”，确认没有进入 stdio 服务循环；如曾由 supervisor 托管，还要
   读回没有残留子进程。
4. 保留领域注册表、EvidenceLedger 和 capability audit；关闭或回滚 adapter
   不删除证据或审计记录，也不影响 Today、个人 AI 分析和自动简报。

关闭 MCP 不执行生产 migration，不运行 Alembic downgrade，不删除
`private_workbench` 表或持久数据。若未来要回滚代码、schema、客户端安装或生产运行版本，
必须按各自 Issue 和生产运维门禁重新取得精确授权。
