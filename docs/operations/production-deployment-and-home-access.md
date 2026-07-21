# 生产部署、受控切换与家庭电脑访问

关联：[Issue #26：迁移生产数据并切换到新服务器](https://github.com/Jettlin927/Quantitative_trading/issues/26)、[ADR 0005：新服务器通过并行验收接替生产](../adr/0005-new-server-replaces-production-safely.md)、[远程访问决策：仅使用 SSH 隧道](private-https-authentication-decision.md)。

本文是稳定操作合同，供任意一台新电脑上的 Codex 在拉取 `main` 后使用。它不记录当前生产状态，也不构成生产授权；提交、CI、镜像、容器、数据库、前端和切换状态都必须现场读回。

## 授权边界

以下动作必须由用户针对本次目标、时间窗口和精确版本明确批准，历史 Issue、操作日志或其他会话中的旧批准不能自动沿用：

- 停止旧服务器写入或进入维护窗口。
- 复制生产备份、恢复 PostgreSQL、执行 Alembic upgrade 或 baseline stamp。
- 启动新服务器生产应用、把新服务器确认为当前生产入口或回切旧服务器。
- 新增或变更服务器登录密钥、生产凭据及权限。
- 删除或覆盖数据库、volume、备份、旧服务器数据和 canonical 研究工件。

无需生产授权即可做只读现场核验、在本地建立 SSH 隧道，以及不触碰生产运行态的本地测试。只要目标或影响范围不清楚，就停在只读核验并向用户报告。

## 新电脑上的 Codex 必须先做什么

1. 从干净工作区拉取 `main`，完整阅读根 `AGENTS.md`、`CONTEXT.md`、本页、ADR 0005、Issue #26、父 Issue 及其 GitHub 原生阻塞关系。
2. 分开核验本地 `HEAD`、`origin/main` 和目标发布提交；只有已进入 `main` 且精确提交的 GitHub CI 通过，才能成为生产候选。
3. 核验本机 SSH config 中的新旧服务器别名。服务器 IP、用户名、端口和私钥路径只保存在本机，不从仓库示例、脚本默认值或旧日志推断。
4. 核验本机与目标服务器各自的 `.env` 是否存在、是否被 Git 忽略、权限是否仅限当前运维身份；只检查必需键是否存在，不在终端、评论或日志中打印值。
5. 在执行前读回目标主机名、当前用户、磁盘和内存、Git 提交、工作区状态、Compose 合并配置、容器、镜像、数据库 revision、备份及工件目录。
6. 把准备执行的动作、目标主机、精确提交、维护窗口、回滚点和仍需批准项用中文复述给用户。没有当次授权就不能进入对应门禁。

本机 `.env` 至少按实际任务保存以下**键名**，真实值不得进入 Git：

| 范围 | 常用键名 | 规则 |
| --- | --- | --- |
| SSH 调用 | `REMOTE`、`REMOTE_SSH_PORT`、`REMOTE_SSH_KEY`、`PROJECT_DIR` | 必须与本机 SSH config 和现场目标交叉核对；脚本默认值不是目标证明 |
| 发布身份 | `REPO_URL`、`BRANCH` | 生产固定为仓库的 `main`，仍须核验精确提交 |
| PostgreSQL | `POSTGRES_DB`、`POSTGRES_USER`、`POSTGRES_PASSWORD` | 密码只保存在受保护的本机及服务器 `.env`，不得回显 |
| 应用凭据 | `TUSHARE_TOKEN`、`DEEPSEEK_TOKEN`、`RESEARCH_GITHUB_TOKEN` | 只在对应服务确实需要且已获授权时配置 |
| 监听与工件 | `POSTGRES_PORT`、`API_PORT`、`FRONTEND_PORT`、`POSTGRES_DATA_DIR`、`RESEARCH_ARTIFACTS_DIR` | 生产端口必须绑定 loopback；持久目录必须显式存在 |

不要直接把 `.env.example` 当成生产配置；其中的开发默认值或历史目标不能证明当前新服务器身份。不要用 `cat .env`、`Get-Content .env` 或开启 shell trace 的方式检查凭据。

## 受控迁移与切换状态机

每一步都要保留中文事实记录。前一步没有通过或下一步缺少授权时立即停止，不得为了“完成部署”跳过门禁。

### 0. 只读预检

- 固定发布候选的完整提交 SHA，并确认它属于 `origin/main`、工作区干净且对应 CI 通过。
- 分别读回旧服务器和新服务器的主机身份、监听端口、容器、镜像、数据库版本与 Alembic revision。
- 核验新服务器目录、资源限制、loopback 绑定和 Compose 合并配置符合[新服务器运行环境合同](new-server-runtime.md)。
- 核验最近一次可恢复逻辑备份、备份校验和、`pg_restore --list` 可读性及 canonical 工件目录；只读预检不得停止服务或创建恢复目标。
- 确认旧服务器仍可作为回滚源，并记录恢复旧服务所需的精确提交与配置位置。

### 1. 准备精确发布版本

取得目标发布授权后，目标服务器只能检出已确认的 `main` 提交。构建前再次确认 `.env` 和 `docker-compose.server.yml` 不在 Git 中，Compose 解析后的 PostgreSQL、API 和前端宿主端口均为 `127.0.0.1`。

仓库脚本 `scripts/ops/deploy_remote.sh` 和 `scripts/ops/deploy_server.sh` 只能在显式确认目标变量后使用。不得依赖脚本、`.env.example` 或历史 shell 中的默认主机；不得在迁移前用 `all` 目标提前启动新服务器写服务。

### 2. 建立停写点与生产备份

只有维护窗口与停写获得批准后才执行：

1. 停止旧服务器的同步 Worker、研究 Worker、API 写入口和其他可能写库的任务；前端是否保留只读由现场方案决定。
2. 证明没有活动写任务后，记录停写时间、旧库 revision、schema 指纹、关键表行数、最大业务日期、研究运行/评价/发布计数。
3. 使用 PostgreSQL 逻辑备份生成自包含归档并计算 SHA-256；不得复制运行中的 PostgreSQL volume。
4. 对归档运行列表检查并保留输出。备份或校验失败就恢复旧服务并停止迁移。
5. 对 canonical 研究工件生成相对路径、大小与 SHA-256 清单；不得修改源工件。

### 3. 恢复 PostgreSQL 与升级 schema

恢复目标必须是新服务器明确的数据目录和空目标库。恢复前再次读回主机身份与目标路径；禁止覆盖已有未知数据库，禁止删除 volume 来“重试”。

- 使用与源库兼容的 PostgreSQL 客户端恢复逻辑归档，并拒绝 owner/ACL 漂移。
- 恢复完成后先按旧库停写清单比较 revision、schema 指纹、关键行数、日期覆盖和研究事实计数。
- 只有用户明确批准生产 migration 且恢复前后 revision 路径已核对，才能在 API 镜像中运行一次 `alembic upgrade head`。
- `stamp`、手工 DDL、`DROP INDEX` 或修补生产行都不是普通恢复步骤，必须另行给出原因、影响和批准。
- migration 后重新生成 schema 指纹及计数；任何不一致都停止应用启动并进入回滚判断。

### 4. 同步 canonical 工件

把停写点对应的工件同步到新服务器显式目录，再用源端清单逐项核对路径、大小和 SHA-256。默认禁止带删除语义的同步，也不得把本地 sample、缓存或仓库报告投影冒充 canonical 工件。

数据库中的证据引用与新服务器工件必须能读回同一运行、评价版本和指纹；缺失、额外覆盖或指纹不一致时不能发布或切换。

### 5. 启动新服务器应用并验证

只有目标发布获得批准、数据库与工件验证通过后，才按依赖顺序启动 `db`、`api`、同步 `worker` 和 `frontend`。`research-worker` 属于正式研究自动化入口，除非其 GitHub 凭据、资源预算和正式研究门禁已单独确认，否则保持未启动。

启动后至少读回：

| 层 | 必须保存的事实 |
| --- | --- |
| 发布身份 | API/镜像报告的完整 Git SHA 与 `refs/heads/main`，镜像摘要 |
| PostgreSQL | Alembic revision、schema 指纹、关键表行数、最大业务日期、运行/评价/发布计数 |
| 工件 | 文件计数、总大小、SHA-256 清单及数据库证据引用抽查 |
| API | `/api/health`、结构化研究和市场数据读回，数值为 JSON-safe |
| Worker | 心跳新鲜、无遗留执行中租约、无意外重试或写任务 |
| 前端 | 新服务器 loopback 首页、SPA 深链、同源 `/api/health` 与实际数据页面 |
| 网络 | PostgreSQL、API、前端只监听 `127.0.0.1`，没有新增公网应用端口 |

镜像构建成功、容器显示 `Up`、API 健康、前端可打开和数据一致是不同事实，必须分别报告。

### 6. 切换、观察与回滚

第一阶段没有域名或公网流量入口。切换是把新服务器确认为当前生产服务，并让用户通过 `quant-trading-new` 的 SSH 隧道访问；不能靠改公网 DNS 或开放端口完成。

- 切换前由用户查看上述证据并明确确认。
- 切换成功后旧服务器保持完整、停止写入并至少保留 14 天；旧数据库、volume、备份、工件和源码都不清理。
- 新服务器验证失败时，先停止其 API、Worker 和前端，完整保留失败现场；再按已验证的旧提交和配置恢复旧服务器服务，读回 API、Worker、前端与数据库事实。
- 删除新服务器失败数据、清理旧服务器或改变回滚保留期都需要新的明确批准。

## 多台设备同时访问

新服务器可以同时接受多条 SSH 隧道。Mac 与 Windows 各自建立独立 SSH 连接，并都转发到服务器 loopback 的 `127.0.0.1:15173`；两台电脑可以同时使用本机 `25173`，因为本机端口空间彼此独立。任一电脑休眠、断网或关闭隧道，只影响该设备。

每台设备必须生成自己的 SSH 密钥，只把各自公钥加入服务器，不能在设备间复制私钥。这样设备丢失或停用时可以单独撤销对应公钥，不影响另一台设备。新增或撤销服务器公钥仍属于凭据变更，必须由用户明确批准。

## macOS 电脑：访问新服务器前端

### 一次性准备

1. 在终端运行 `ssh -V`，确认系统 OpenSSH 可用。
2. 如果这台 Mac 还没有独立密钥，生成带口令的 Ed25519 密钥；不要复制 Windows 或其他电脑的私钥。
3. 把 `.pub` 公钥交给获授权的运维会话加入服务器；私钥只保留在本机。
4. 在 `~/.ssh/config` 配置本机别名。真实服务器地址、用户、端口和私钥路径不写入仓库。
5. 首次连接时人工核对服务器主机密钥指纹；主机指纹意外变化时停止连接并先查明原因。

生成独立密钥：

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
ssh-keygen -t ed25519 -a 64 -f ~/.ssh/quant-trading-mac-tunnel-ed25519 -C "quant-trading-mac-tunnel"
chmod 600 ~/.ssh/quant-trading-mac-tunnel-ed25519
chmod 644 ~/.ssh/quant-trading-mac-tunnel-ed25519.pub
```

`~/.ssh/config` 示例中的占位值只能在本机替换：

```sshconfig
Host quant-trading-new-tunnel
  HostName <在本机填写新服务器地址>
  User <在本机填写登录用户>
  Port 22
  IdentityFile ~/.ssh/quant-trading-mac-tunnel-ed25519
  IdentitiesOnly yes
  ServerAliveInterval 30
  ServerAliveCountMax 3
```

配置后限制文件权限并测试 SSH 身份：

```bash
chmod 600 ~/.ssh/config
ssh -v quant-trading-new-tunnel
```

如果服务器为该公钥配置了“只允许端口转发、禁止 shell”，身份验证成功后 shell 被拒绝是预期结果；直接执行下面的隧道命令即可。

### 建立隧道并访问

```bash
ssh -NT \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -L 127.0.0.1:25173:127.0.0.1:15173 \
  quant-trading-new-tunnel
```

保持终端运行，然后打开前端并检查同源 API：

```bash
open http://127.0.0.1:25173
curl -fsS http://127.0.0.1:25173/api/health
```

结束访问时在 SSH 终端按 `Ctrl-C`。如果本机 `25173` 已被占用，只改第一个本机端口，例如映射为 `35173:127.0.0.1:15173`，随后访问 `http://127.0.0.1:35173`；远端端口仍固定为 `15173`。

## Windows 家庭电脑：常态访问新服务器前端

### 一次性准备

1. 在 PowerShell 运行 `ssh -V`，确认 Windows OpenSSH Client 已安装。
2. 为这台电脑新建独立的 Ed25519 密钥并设置口令；不要从其他电脑复制私钥。推荐把常驻隧道密钥与 Codex 运维密钥分开：前者只允许端口转发，后者仅在人工运维时使用。
3. 只把 `.pub` 公钥交给有权限的运维会话。把公钥加入服务器属于凭据变更，必须由用户明确批准；常驻隧道密钥应在 `authorized_keys` 中限制为仅允许转发到 `127.0.0.1:15173`，不得取得 shell。
4. 在 `%USERPROFILE%\.ssh\config` 本地配置 `quant-trading-new`（运维）与 `quant-trading-new-tunnel`（仅隧道）别名。`HostName`、`User`、`Port` 和 `IdentityFile` 的真实值不写入仓库。
5. 首次连接要人工核对服务器主机密钥指纹，确认后再写入 `known_hosts`；不得盲目接受变化后的主机密钥。

新建独立隧道密钥的本地示例：

```powershell
ssh-keygen -t ed25519 -a 64 -f "$env:USERPROFILE\.ssh\quant-trading-home-tunnel-ed25519" -C "quant-trading-home-tunnel"
```

`%USERPROFILE%\.ssh\config` 示例中的占位值只能在本机替换：

```sshconfig
Host quant-trading-new-tunnel
  HostName <在本机填写新服务器地址>
  User <在本机填写登录用户>
  Port 22
  IdentityFile ~/.ssh/quant-trading-home-tunnel-ed25519
  IdentitiesOnly yes
  ServerAliveInterval 30
  ServerAliveCountMax 3
```

如果本地需要保存部署 `.env`，先确认它被 Git 忽略，再限制为当前 Windows 用户可读写：

```powershell
git check-ignore .env
icacls .env /inheritance:r /grant:r "$($env:USERNAME):(R,W)"
```

不要在 Codex 对话、截图、Issue、PR 或命令输出中展示 `.env` 内容。

### 临时隧道

推荐统一使用本机 `25173`，避免与本机开发环境的 `15173` 冲突：

```powershell
ssh -NT -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:25173:127.0.0.1:15173 quant-trading-new-tunnel
```

如果本机 `25173` 已被占用，可改用 `35173`：

```powershell
ssh -NT -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:35173:127.0.0.1:15173 quant-trading-new-tunnel
```

隧道保持运行时访问 `http://127.0.0.1:25173`，或使用实际选择的本机端口。浏览器看到的是本机 loopback HTTP，电脑到服务器的链路由 SSH 加密；服务器应用端口仍未开放公网。

### 登录后自动建立隧道

先手工验证隧道、前端与数据读回，再在 Windows 任务计划程序创建当前用户登录时运行的任务：

- 程序：`C:\Windows\System32\OpenSSH\ssh.exe`
- 参数：`-NT -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:25173:127.0.0.1:15173 quant-trading-new-tunnel`
- 仅在当前用户登录时运行，不使用最高权限；失败后每 1 分钟重试，并禁止同一任务并发启动多个实例。
- 口令保护的隧道密钥应由当前用户的 Windows `ssh-agent` 管理；不要为了无人值守而移除口令或把私钥写进脚本。

本地检查：

```powershell
Test-NetConnection 127.0.0.1 -Port 25173
Invoke-WebRequest http://127.0.0.1:25173/api/health -UseBasicParsing
Start-Process http://127.0.0.1:25173
```

端口不通先检查计划任务与 SSH 进程；端口通但页面无数据，要检查新服务器 `frontend`、`api`、PostgreSQL 和 Worker 的现场状态。SSH 隧道只提供连接，不会部署应用、迁移数据库或生成数据。

## 家庭电脑上的 Codex 交接规则

用户在家中电脑拉取 `main` 后，可以让 Codex 按以下顺序协助：

1. 读取仓库规则与本页，只做本机和服务器只读预检。
2. 核验 SSH 别名可达，检查 `.env` 必需键存在但不输出值。
3. 核验新服务器前端的 loopback 服务和 Windows 隧道；前端必须从恢复后的 PostgreSQL/API 读回实际数据，不能用 sample 或静态页面冒充。
4. 需要部署、migration、切换、凭据变更或回滚时，把精确动作与风险列给用户并取得当次明确批准。
5. 操作后把 Git、CI、服务器、数据库、API、前端与回滚状态分开报告，并把无凭据的阶段事实追加到 `操作日志.md` 或对应不可变验收记录。

仓库让不同电脑共享规则，不共享权限或授权。没有本机 SSH 配置、私钥和受保护的 `.env`，Codex 只能给出缺失项，不能从 GitHub 仓库恢复这些秘密。

## 永久禁止

- `docker compose down -v`、`docker volume rm`、覆盖恢复或删除 canonical 工件。
- 复制运行中的 PostgreSQL volume，或跳过逻辑备份校验直接恢复。
- 把 `.env`、IP、用户名、密码、token、私钥、主机指纹或真实账户数据提交到 Git。
- 为方便访问而把 PostgreSQL、API 或前端绑定到公网，或擅自部署域名、Cloudflare/Tailscale 入口。
- 把旧批准当成新电脑、新版本或新维护窗口的持续授权。
