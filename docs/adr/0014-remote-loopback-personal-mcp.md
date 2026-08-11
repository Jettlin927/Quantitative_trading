# 远端个人 MCP 运行在生产服务器 loopback 并通过 SSH 隧道访问

状态：已接受（2026-08-11）；本 ADR 只冻结目标拓扑和安全合同，不表示 Streamable HTTP
transport、Compose、生产凭据、部署或真实启用已经完成或获得授权。

## 背景

[ADR 0013](0013-local-read-only-personal-mcp.md)在首版未知网络身份与运维证据时，只允许
受保护本机上的 `stdio` 子进程。现有实现由此证明了单 actor、确定性五工具白名单、固定
只读权限、限额、审计和默认关闭，但本机子进程不能复用唯一生产服务器
`quant-trading-prod` 中的私有 PostgreSQL、keyring 与来源配置。把数据库或 secret 复制到
每台客户端会制造新的私有事实与凭据边界。

生产系统已经冻结为单服务器、服务只监听 loopback、用户通过 SSH 隧道访问。远端 MCP
应服从同一拓扑，同时不能让 DeepSeek 的内部工具调用绕经外部协议 adapter。

## 决定

### 生产拓扑

```text
本机 MCP 客户端
  -> 127.0.0.1:<本地端口>
  -> SSH -L
  -> quant-trading-prod:127.0.0.1:<远端端口>
  -> /mcp Streamable HTTP adapter
  -> PersonalMcpGateway
  -> 唯一 DomainToolRegistry

quant-trading-prod 上的 personal-analysis-worker
  -> DeepSeek Chat Completions tool_calls
  -> internal client-tool adapter
  -> 唯一 DomainToolRegistry
```

- 生产 MCP 运行于 `quant-trading-prod`，只允许宿主发布 `127.0.0.1`。禁止绑定
  `0.0.0.0`、开放公网安全组端口、配置公网域名、反向代理、Cloudflare 或 Tailscale
  入口；用户电脑只通过 SSH 隧道访问。
- 网络协议使用官方 MCP SDK 的 Streamable HTTP transport，并且只提供单一 `/mcp`
  endpoint。实现不得手写第二套 JSON-RPC、session 或 transport。
- `stdio` adapter 继续保留为非生产、测试或应急诊断入口，但不是生产运行拓扑。删除
  `stdio` 或 Streamable HTTP 任一 adapter 都不得改变 `PersonalMcpGateway`、
  `DomainToolRegistry`、来源 adapter、EvidenceLedger 或另一协议 adapter。
- DeepSeek 继续由个人分析 Worker 通过 Chat Completions `tool_calls`、内部 client-tool
  adapter 调用唯一 `DomainToolRegistry`。DeepSeek Runtime、provider 与 Worker 不得导入、
  启动或调用 MCP server、gateway 或 transport；MCP 也不得持有 DeepSeek secret 或调用模型。

### 网络身份、token 与 Origin

- SSH 的网络隔离不能替代应用认证。每个 HTTP 请求仍须携带独立 bearer token；token
  只从 `quant-trading-prod` 上 owner-only 的受保护宿主文件注入，使用常量时间比较，不能
  出现在 Git、Issue、命令参数、客户端可见工具参数、响应、审计或日志中。缺失、空值、
  文件权限错误或 token 不匹配时 fail-closed。
- 若请求带 `Origin`，必须与显式精确 allowlist 中的一项完全相同；通配符、前缀、后缀、
  `null`、空值、格式错误或多值均返回稳定的 403，且不得创建 session 或调用领域工具。
  请求完全不含 `Origin` header 时可以继续非浏览器 client 流程，但 bearer token、SSH
  隧道、固定 actor 与所有领域门禁仍然必需；缺少 `Origin` 不得被解释为匿名或低权限模式。
- actor 在服务启动时固定，不能来自 bearer token claim、URL、header、session、工具参数
  或提示文本。token 只证明访问获准入口，不授予 actor、purpose、工具或数据权限。

### channel、purpose 与历史证据

- `PersonalMcpGateway` 必须在构造时接收不可变的服务端入口上下文，由获准 adapter 从
  封闭映射中选择：`stdio` 对应 `channel="mcp_stdio"`、`purpose="mcp_stdio"`；远端
  HTTP 对应 `channel="mcp_streamable_http"`、`purpose="mcp_remote_read"`。客户端不能覆盖、
  追加或委托这两个值。现有 gateway 对 `mcp_stdio` 的硬编码只刻画 stdio 行为；后续 HTTP
  实现必须先把它收敛为上述服务端构造边界，不能原样复用后把 HTTP 尝试伪装成 stdio。
- 远端 HTTP 尝试在 capability audit 中真实记录 `channel="mcp_streamable_http"`，调用
  领域工具与 EvidenceLedger 时固定使用 `purpose="mcp_remote_read"`。确定性五工具白名单、
  `portfolio:read`、`market:read`、`news:read`、`evidence:read` 和 ADR 0013 的资源限制不变。
- 新 purpose 必须通过新的来源授权与证据策略版本逐项显式加入。历史 evidence、来源快照
  或 audit 即使曾允许 `domain_tool` 或 `mcp_stdio`，也不会因此自动允许
  `mcp_remote_read`；不能改写历史记录、扩大旧证据用途或用旧策略版本绕过当前检查。
- retention、字段投影、时效、coverage、cost、256 KiB 结果上限、20 秒 deadline、并发 2、
  30 rpm、全尝试审计与 audit 失败即停服继续沿用 ADR 0013；transport 不能改变这些结论。

### 默认关闭、紧急关闭与分阶段交付

- 远端 MCP 默认关闭。kill switch 仍是设置 `PERSONAL_MCP_ENABLED=false` 并停止远端 MCP
  服务；关闭后本机隧道应不可访问。关闭不修改 registry，不删除 evidence/audit，不执行
  migration 或 downgrade，也不影响 Today、API、个人分析 Worker、自动简报与 stdio 测试。
- Streamable HTTP adapter、Compose loopback 发布与 secret mount、生产部署和真实启用必须
  分别由后续 Issue 实施和验证。合并本 ADR 或测试通过不授权创建/轮换 token、修改生产
  配置、启动容器、执行 migration 或建立真实客户端会话。

## 被拒绝的方案

- 在用户电脑复制生产数据库、keyring 或来源 secret：会产生第二套私有事实和凭据边界。
- 让 DeepSeek 经 MCP 调用工具：会把内部 provider runtime 与外部协议身份、session 和
  transport 耦合，并产生第二条能力路径。
- 复用 `mcp_stdio` channel/purpose：会让审计无法区分网络入口，并把历史证据静默扩权。
- 只依赖 SSH、不校验 bearer token 或 Origin：不能形成独立的应用入口身份和浏览器边界。
- 公网监听、域名或公共反向代理：超出单一生产服务器的 loopback + SSH 访问决定。

## 后果与重决策门禁

ADR 0013 不再描述生产拓扑，但其领域与安全合同继续有效。后续实现必须以 deletion test
证明 DeepSeek 和两个 MCP transport 只共享唯一 `DomainToolRegistry`，并分层报告代码、
CI、合并、Compose、部署、Runtime、真实客户端调用与业务验收。

若要开放公网、多 actor、多租户、动态权限、写入、交易、AI/Hosted/Web 工具，改变
token/Origin、channel/purpose、历史证据用途、审计、限额、retention 或 kill switch，
必须新增 ADR；不能作为本决定的兼容扩展。
