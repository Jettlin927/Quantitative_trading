# 本机私有只读 MCP 使用固定 actor 与确定性工具白名单

状态：部分被取代（2026-08-11）；其中“生产只允许本机 `stdio`”的拓扑条款已由
[ADR 0014](0014-remote-loopback-personal-mcp.md)取代。单 actor、确定性五工具白名单、
固定只读权限、资源限制、审计、fail-stop 与 kill switch 合同继续有效；`stdio` 保留为
非生产/测试 adapter。本 ADR 不表示远端 MCP 已实现、已配置、已部署或已启用。

## 背景

个人投资工作台已经有受控的领域工具、Today 聚合、个人 AI 分析和自动简报。后续可以
让本机客户端通过 MCP 复用其中一小部分只读能力，但 MCP 会新增一个绕过浏览器交互的
调用入口。如果直接暴露现有 registry、允许客户端选择 actor 或复用 AI Runtime，可能
扩大私有持仓读取范围、恢复未经授权的 Web/Hosted 能力，甚至为写入或交易能力留下入口。

本决定延续 [ADR 0009](0009-personal-ai-analysis-agent-tools.md) 的个人 AI 工具隔离与
[ADR 0011](0011-personal-investment-workbench-without-research.md) 的个人工作台边界。
它不改变上述决定，也不改变当前生产运行路径。

## 决定

### 拓扑与身份

- 首版 MCP 只允许在受保护的本机以 `stdio` 子进程运行；不监听 TCP，不提供 HTTP、
  SSE、WebSocket、远程转发或多租户入口。
- 一个进程只服务一个 actor。`PERSONAL_MCP_ACTOR_ID` 在启动时固定，客户端请求、
  session、工具参数或提示文本都不能覆盖 actor。
- MCP 是 `DomainToolRegistry` 的出口 adapter，不是新的领域能力、证据或权限真相源；
  它不得直接访问来源 SDK、模型供应商或数据库表。

### 私有只读白名单

首版只发现并调用以下五个 canonical 工具：

1. `get_today_context`
2. `get_symbol_dossier`
3. `search_market_news`
4. `discover_related_candidates`
5. `get_evidence`

白名单是确定性的；legacy alias 和 registry 中的其他工具不会因为存在就自动暴露。
特别是 `search_web_evidence` 不属于 MCP 白名单。MCP 不提供持仓、现金、规则、分析、
配置或证据的写入，不提供交易、下单、撤单、调仓、admin、AI Completion、Hosted Tool、
Hosted Search 或任意 Web 搜索能力。

进程权限固定为 `portfolio:read`、`market:read`、`news:read`、`evidence:read`。客户端
不能申请、追加或委托权限；没有 `web_evidence:read`、write、admin 或 trading 权限。
所有读取继续受 actor、来源授权、purpose、字段投影、时效和证据读取策略约束。

### 默认关闭与配置门禁

- `PERSONAL_MCP_ENABLED=false` 是默认值；只有显式设为启用才允许进程进入服务循环。
- 启动时必须同时具备固定 actor、`PRIVATE_DATABASE_URL`、个人 keyring 和所需来源配置。
  任一配置缺失、无效或权限不足时拒绝启动或拒绝调用，不以空值、公共数据或其他 actor
  降级继续。
- 凭据只从现有受保护入口读取，不进入工具参数、协议响应、审计事件或日志。
- 实现、安装配置、启用和部署分别需要后续 Issue 与明确授权；合并代码本身不启用 MCP。

### 资源限制与稳定失败

每个 MCP 进程最多并发 2 个调用、每分钟最多接受 30 个调用；单次调用 deadline 为
20 秒，编码后的单次结果不得超过 256 KiB。并发、频率、deadline 或结果大小超限必须
返回稳定、可审计的失败，不得截断后伪装成成功，也不得绕过 registry 改走其他来源。

### 审计与紧急关闭

每次尝试调用都必须写入共享 capability audit：至少包含 request identity、workspace/
actor 范围、`mcp_stdio` channel、canonical 工具名、参数 SHA-256、状态或稳定错误、证据
引用、coverage/freshness/cost、策略版本及开始/完成时间。审计不得保存完整参数、完整
结果、私有持仓正文、凭据或供应商原始响应。

kill switch 是把 `PERSONAL_MCP_ENABLED` 关闭并停止 MCP 子进程。关闭 MCP 不修改
registry，不删除证据或审计记录，也不影响 Today、个人 AI 分析或自动简报等内部入口。

## 被拒绝的方案

- 远程或网络 MCP：首版没有足够的网络身份、会话隔离、传输认证和运维证据。
- 客户端传入 actor 或动态权限：会把私有数据边界交给不可信调用方。
- 自动投影 registry 全量工具：新增内部能力会静默扩大外部攻击面。
- 经 MCP 调用 AI、Hosted Tool 或 Web Search：会混合证据读取与模型/外网成本、授权和
  审计边界。
- 在 MCP 内复制 handler 或直连来源：会产生第二套能力与授权真相源。

## 后果与重决策门禁

未来实现必须证明默认关闭、五工具白名单、单 actor、固定权限、配置 fail-closed、限流、
deadline、结果大小、审计最小化和 kill switch；测试或文档不能替代显式启用授权。

若要支持远程传输、多 actor、多用户、动态授权、写入、交易、AI/Hosted 工具、Web 搜索，
或改变审计/限流/kill switch，必须新增 ADR，重新完成威胁模型、身份与权限、secret、
网络暴露、数据隔离、成本预算、审计保留、运维和回滚设计；不得把这些变化视为本 ADR
的兼容扩展。
