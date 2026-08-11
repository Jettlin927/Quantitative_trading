# 生产服务器部署、受控变更与家庭电脑访问

关联：[ADR 0008：单一生产服务器与旧服务器退役](../adr/0008-single-production-server-and-retired-legacy-host.md)、[远程访问决策：仅使用 SSH 隧道](private-https-authentication-decision.md)。历史双服务器迁移方法见 [ADR 0005](../adr/0005-new-server-replaces-production-safely.md)，不得据此恢复旧服务器角色。

本文是稳定操作合同，也是当前生产操作合同。它不记录易变化的提交、运行 ID、表行数
或部署状态，也不构成生产授权；所有生产事实必须在 `quant-trading-prod` 现场读回。

## 当前拓扑

- `quant-trading-prod` 是唯一生产服务器和唯一数据权威。
- PostgreSQL、API、个人分析 Worker 和前端均在该服务器运行；前端与后端不跨服务器拆分。
- 原旧服务器已经退出本系统，不是备机、灾备、回滚源、Staging 或计算节点。
- PostgreSQL `5432`、API `18000` 和前端 `15173` 只监听服务器 loopback；用户只
  通过 SSH 隧道访问。
- 生产代码固定来自仓库 `main` 的精确提交，但 GitHub `main`、服务器 checkout、
  镜像和运行容器必须分别核验，不能凭分支名推断一致。

## 授权边界

以下动作必须由用户针对目标、时间窗口和精确版本明确批准；历史 Issue、旧操作记录
或其他会话中的批准不能沿用：

- 停止或重启生产写服务、切换运行版本。
- 恢复 PostgreSQL、执行 Alembic upgrade、baseline stamp、手工 DDL 或数据修复。
- 删除或覆盖数据库、volume、备份和未知来源持久文件。
- 新增或变更服务器登录密钥、生产凭据及权限。
- 开放公网端口或新增域名、Cloudflare/Tailscale 等远程入口。

无需生产授权即可做只读现场核验、在本地建立 SSH 隧道，以及不触碰生产运行态的
本地或隔离测试。只要目标或影响范围不清楚，就停在只读核验并向用户报告。

## 新电脑上的 Codex 必须先做什么

1. 从干净工作区拉取 `main`，完整阅读根 `AGENTS.md`、`CONTEXT.md`、本页和 ADR
   0008。
2. 核验本机 SSH config 中的 `quant-trading-prod`；真实地址、用户名、端口和私钥
   路径只保存在本机，不从仓库示例、脚本默认值或旧日志推断。
3. 分开核验本地 `HEAD`、`origin/main`、目标发布提交及其精确 GitHub CI。
4. 核验本机与生产服务器 `.env` 存在、被 Git 忽略且权限受限；只检查必需键，不
   输出值。
5. 只读读回主机身份、磁盘和内存、checkout、工作区、Compose 合并配置、容器、
   镜像、数据库 revision、持久目录、端口和定时任务。
6. 执行前用中文复述精确动作、目标提交、维护窗口、失败停止点和仍需批准项。

本机 `.env` 至少按实际任务保存以下键名，真实值不得进入 Git：

| 范围 | 常用键名 | 规则 |
| --- | --- | --- |
| SSH 调用 | `REMOTE`、`REMOTE_SSH_PORT`、`REMOTE_SSH_KEY`、`PROJECT_DIR` | `REMOTE` 必须解析到 `quant-trading-prod` |
| 发布身份 | `REPO_URL`、`BRANCH` | 生产固定为仓库 `main`，仍须核验精确提交 |
| PostgreSQL | `POSTGRES_DB`、`POSTGRES_USER`、`POSTGRES_PASSWORD` | 密码不得回显 |
| 应用凭据 | `DEEPSEEK_TOKEN`；Alpaca 凭据使用受保护只读文件 | 只配置当前服务实际需要的权限 |
| 监听与数据 | `POSTGRES_PORT`、`API_PORT`、`FRONTEND_PORT`、`POSTGRES_DATA_DIR` | 持久目录必须显式存在，端口必须绑定 loopback |

不要把 `.env.example` 当成生产配置，也不要用 `cat .env`、PowerShell
`Get-Content .env` 或 shell trace 检查凭据。

## 生产部署门禁

### 1. 固定候选版本

- 刷新 `origin/main`，记录完整 SHA，确认工作区干净且该 SHA 的目标 CI 全部通过。
- 确认生产 migration 状态、Compose 合并配置和构建参数与该 SHA 匹配。
- 不从本地上传未提交源码；生产服务器只拉取并构建已确认的 `main` 提交。

### 2. 执行受控部署

- 只有取得当次授权后，才能在 `quant-trading-prod` 执行发布或 migration。
- 数据库、API、个人分析 Worker 和前端必须保持同一生产身份；不得只更新
  一个服务后笼统宣称发布完成。
- migration 与镜像构建不得并行；失败时停止在现场，不通过删除 volume、
  覆盖数据或临时手工 DDL 强行继续。

### 3. 分层读回

| 层 | 必须保存的事实 |
| --- | --- |
| 代码 | GitHub `main` SHA、目标 CI、服务器 checkout SHA |
| 镜像与进程 | 镜像摘要、容器实际 `APP_GIT_COMMIT`、健康和重启状态 |
| PostgreSQL | Alembic revision、schema 指纹、关键计数和最大业务日期 |
| Worker | 个人分析 Worker 心跳、队列和最近任务结果；未启用时记录为未启用 |
| API | `/api/health` 与目标业务读回，数值保持 JSON-safe |
| 前端 | 首页、SPA 深链、同源 `/api/health` 和实际数据页面 |
| 网络 | `5432`、`18000`、`15173` 只监听 `127.0.0.1` |

代码合并、CI 通过、镜像构建、容器启动、生产读回和业务验收是不同事实，报告时必须
分开。

## SSH 隧道访问

macOS、Linux 与 Windows 电脑可以同时建立独立隧道。每台设备必须使用自己的 SSH
密钥；不能复制私钥，新增或撤销公钥仍属于凭据变更。SSH 隧道只提供连接，不会启动
生产应用、部署代码或迁移数据。

运维别名统一为 `quant-trading-prod`。仅端口转发的独立密钥建议使用
`quant-trading-prod-tunnel`：

```sshconfig
Host quant-trading-prod-tunnel
  HostName <在本机填写生产服务器地址>
  User <在本机填写登录用户>
  Port 22
  IdentityFile <在本机填写仅用于隧道的私钥路径>
  IdentitiesOnly yes
  ServerAliveInterval 30
  ServerAliveCountMax 3
```

首次连接必须人工核对主机密钥指纹。建立隧道：

```bash
ssh -NT \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -L 127.0.0.1:25173:127.0.0.1:15173 \
  quant-trading-prod-tunnel
```

隧道保持运行时访问 `http://127.0.0.1:25173`，并检查
`http://127.0.0.1:25173/api/health`。如果本机端口冲突，只修改第一个 `25173`；远端
端口仍为 `15173`。

Windows 任务计划程序如需登录后自动启动，只运行当前用户权限下的
`C:\Windows\System32\OpenSSH\ssh.exe`，参数沿用上述隧道命令并增加
`-o BatchMode=yes`；失败可重试，但禁止并发启动多个实例。口令密钥交由当前用户的
`ssh-agent` 管理，不把私钥或口令写进脚本。

## 远端 MCP 目标合同（本地装配已实现，尚未部署）

远端个人 MCP 的生产目标同样位于 `quant-trading-prod`，只在宿主 `127.0.0.1` 发布单一
`/mcp` Streamable HTTP endpoint，由本机客户端经独立 SSH 隧道访问；不得开放公网端口、
域名或公共反向代理。协议 adapter 只调用唯一 `DomainToolRegistry`，审计 channel 固定为
`mcp_streamable_http`，领域 purpose 固定为 `mcp_remote_read`。DeepSeek Worker 继续通过
Chat Completions `tool_calls` 的内部 adapter 直接调用 registry，不导入 MCP。

该目标的 bearer token、Origin、历史证据不扩权、默认关闭和 kill switch 见
[远端 MCP 运维合同](personal-mcp-remote.md)。HTTP ASGI adapter、固定 loopback 的独立进程、
默认关闭的 Compose profile、本机隧道脚本和无 secret 客户端模板已在本地实现；当前仍不
授权创建凭据、修改服务器配置、启动容器或真实启用。后续生产切换仍须针对精确 SHA、维护
窗口和回滚动作取得当次授权并现场读回。

## 家庭电脑上的 Codex 交接规则

1. 读取仓库规则和本页，只做本机与 `quant-trading-prod` 的只读预检。
2. 核验 SSH 别名、`.env` 必需键、前端 loopback 服务和同源 API。
3. 需要部署、migration、凭据变更、数据修复或删除时，列出精确动作并取得当次
   授权。
4. 操作后分别报告 Git、CI、镜像、进程、数据库、Worker、API、前端与网络。

仓库共享规则，不共享权限。没有本机 SSH 配置、私钥和受保护的 `.env`，Codex 只能
报告缺失项，不能从 GitHub 恢复这些秘密。

## 永久禁止

- 未经精确授权执行 `docker compose down -v`、`docker volume rm`、覆盖恢复或删除
  未知来源持久文件。
- 不得复制运行中的 PostgreSQL volume，或跳过逻辑备份校验直接恢复。
- 把 `.env`、IP、用户名、密码、token、私钥、主机指纹或真实账户数据提交到 Git。
- 为方便访问而把 PostgreSQL、API 或前端绑定到公网，或擅自部署域名、
  Cloudflare/Tailscale 入口。
- 把旧批准、旧服务器或历史迁移记录当成当前生产授权或回滚能力。
