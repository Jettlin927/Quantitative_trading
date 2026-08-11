# 远端个人 MCP loopback 与 SSH 隧道运维合同

本页定义 `quant-trading-prod` 上远端个人 MCP 的目标操作边界。受认证的 Streamable HTTP
ASGI adapter 已在代码中实现；当前仍不接 Compose、部署脚本或真实客户端配置。本文
不构成生产授权，也不表示远端 MCP 已配置、已部署或已启用。稳定安全决定见
[ADR 0014](../adr/0014-remote-loopback-personal-mcp.md)。

## 目标拓扑与职责

生产 MCP 只能在 `quant-trading-prod` 运行，由 Compose 后续以宿主 `127.0.0.1:<远端端口>`
发布官方 Streamable HTTP 的单一 `/mcp` endpoint。本机客户端只连接
`127.0.0.1:<本地端口>/mcp`，中间使用 SSH 隧道转发；不得开放公网端口、域名、公共反向
代理、Cloudflare 或 Tailscale。

HTTP adapter 只把官方协议请求翻译给既有 `PersonalMcpGateway`，再调用唯一
`DomainToolRegistry`。`stdio` 只保留为非生产/测试 adapter。服务器上的 DeepSeek Worker
继续走 Chat Completions `tool_calls` -> internal client-tool adapter ->
`DomainToolRegistry`，不导入或调用 MCP，也不接收 MCP bearer token。

## 后续实现必须保持的门禁

- 默认关闭；未显式启用时不得装配数据库、keyring、来源或 HTTP 服务。
- 宿主监听精确为 `127.0.0.1`，Compose 和进程都不能接受任意 host 或 `0.0.0.0`。
- bearer token 只来自 owner-only 宿主文件并只读挂载；缺失、权限错误或不匹配时拒绝。
- 带 `Origin` 的请求只接受精确 allowlist；通配符、`null`、空值、畸形或多值均拒绝。
  完全不带 `Origin` header 的非浏览器客户端仍必须通过 bearer、SSH、固定 actor 和领域门禁。
- gateway 的服务端入口上下文只能由 adapter 在构造时从封闭映射选择，客户端不能覆盖：
  HTTP 固定 `channel="mcp_streamable_http"` 与 `purpose="mcp_remote_read"`；stdio 仍固定
  `channel="mcp_stdio"` 与 `purpose="mcp_stdio"`。现有 stdio 硬编码必须先改为该构造边界，
  HTTP 才能复用 gateway。历史证据不会因新 purpose 自动扩权，新增授权必须使用新策略
  版本并保留原记录。
- exact-five、固定只读权限、20 秒 deadline、256 KiB、并发 2、30 rpm、全尝试 audit、
  audit 失败即停服、retention、时效、coverage 与 cost 合同保持不变。

## 分阶段状态与验证

后续交付必须分别读回，不得互相替代：

1. HTTP adapter：已由本地与隔离测试证明官方 ClientSession 的
   initialize/list/call/shutdown、401、403、限额、脱敏和删除边界；这不表示已装配或启用。
2. Compose 与本机入口：默认 profile 不启动；启用时只发布服务器 loopback；token、
   keyring 和来源文件只读挂载，容器不接收 DeepSeek secret；SSH 隧道与项目客户端模板
   不含真实 secret。
3. 生产执行：针对精确 main SHA、维护窗口、migration、token 创建或轮换、容器切换和
   回滚取得当次授权后，分别核验 checkout、镜像、schema、监听、隧道、真实 exact-five
   调用、audit 与原有工作台回归。

本页目前只冻结上述合同，不实现 start、tunnel、readback、stop 或 rollback 命令；这些
稳定入口由后续 Compose/运维 Issue 在隔离验证后补充。

## 紧急关闭与回滚不变量

kill switch 是把 `PERSONAL_MCP_ENABLED` 恢复为 `false` 并停止远端 MCP 服务，随后确认
本机隧道无法访问 `/mcp`，服务器没有残留监听。关闭不执行生产 migration、Alembic
downgrade 或数据库删除，不删除 evidence/audit，不改变 `DomainToolRegistry`，也不影响
Today、API、个人分析 Worker、自动简报或 `stdio` 测试 adapter。

停止服务、修改生产配置、创建或轮换 bearer token、容器切换和真实启用仍须后续票针对
精确目标单独授权；文档、代码或 CI 通过不能替代生产读回与业务验收。
