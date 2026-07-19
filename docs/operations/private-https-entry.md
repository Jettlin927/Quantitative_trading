# 私有 HTTPS 研究前端入口

关联：[Issue #25](https://github.com/Jettlin927/Quantitative_trading/issues/25)、[身份认证决策](private-https-authentication-decision.md)、[新服务器运行环境](new-server-runtime.md)。

本手册将 Cloudflare Tunnel + Access 决策落为可审查的 Compose 与验收合同。它不是上线授权：域名、Cloudflare 控制面、允许身份、MFA、会话时长、失败尝试限制和生产配置未经用户批准时，不得执行本文的任何控制面或容器变更。

## 仓库合同

- `docker-compose.private-https.example.yml` 是独立、显式加载的覆盖文件；日常 Compose 不会自动启动 Tunnel。
- `cloudflared` 镜像锁定到 `2026.7.2` 的多架构 digest，以非 root `65532:65532` 运行，只读根文件系统，不发布宿主端口。
- Tunnel token 只从 `/run/secrets/cloudflared-tunnel-token` 读取；宿主源文件固定为 `/srv/quantitative-trading/secrets/cloudflared-tunnel-token`，权限必须是 `0600` / `65532:65532`。不接受环境变量改写目标。
- `cloudflared` 仅与前端共享内部 `private_https_origin` 网络，另使用独立出站网络连接 Cloudflare；API、PostgreSQL 和 Worker 不加入两个 Tunnel 网络。
- PostgreSQL `5432`、API `18000` 和前端 `15173` 仍只发布到 `127.0.0.1`。Tunnel 的唯一 origin 必须是 `http://frontend:5173`。
- `cloudflared` 预置上限为 `0.15 CPU / 192 MiB / 64 PID`，日志为 `10 MiB × 3`。这是防护上限，不是资源验收结论；真实空闲、登录、持续访问和重连仍须实测。

仓库静态门禁：

```bash
scripts/ops/test_private_https_config.sh
bash -n scripts/ops/install_cloudflared_token.sh \
  scripts/ops/inspect_private_https_entry.sh \
  scripts/ops/test_private_https_config.sh
```

## 上线前人工批准单

用户必须在同一次上线批准中明确以下值，不能由仓库默认值或智能体推断：

1. 唯一精确 HTTPS 域名，不使用通配域名。
2. Cloudflare Account / Zero Trust 组织与对应 zone。
3. 唯一允许的精确邮箱；禁止 `Everyone`、整个邮箱域和 `Bypass`。
4. 独立 MFA 方法，且至少登记两种可恢复验证器。
5. Access 应用/策略会话不超过 8 小时，MFA 会话不超过 1 小时。
6. 真实租户能提供的重复猜测限速/锁定和失败认证日志证据。无法证明时验收失败，停止上线并重新评估 Caddy + Authelia。
7. 当次容器重建、公网入口启用、失败关闭演练和回滚窗口。

## 控制面与容器准备

以下步骤只能在上述批准后执行。顺序用于避免主机名出现短暂的无 Access 保护窗口：

1. 创建 self-hosted Access application，精确绑定获批域名，先启用未配置 Access 主机名默认拒绝。
2. 只创建一条 `Allow` 策略：`Include` 限定当前 Cloudflare Account Member，`Require` 为获批精确邮箱和独立 MFA；设置获批会话时长。
3. 创建一个 remotely-managed Tunnel，只配置一个精确 public hostname，origin 为 `http://frontend:5173`，并把该路由的 **HTTP Host Header** 固定为 `localhost`。这使公网 Host 不会被 Vite 开发服务器的 Host 校验拒绝；不得使用 `allowedHosts: true`。不配置 API、PostgreSQL、SSH、管理端、通配 hostname 或任何通配代理 origin；末尾只允许 fail-closed `http_status:404`。
4. 在服务器 checkout 中复制被 Git 忽略的实例覆盖：

   ```bash
   cp docker-compose.private-https.example.yml docker-compose.private-https.yml
   ```

5. 不要把 token 放进 shell 参数、环境变量、`.env` 或 Compose。使用交互式脚本写入只读文件：

   ```bash
   sudo scripts/ops/install_cloudflared_token.sh
   ```

6. 用真实 `.env` 解析三层 Compose，但不向终端输出展开结果：

   ```bash
   docker compose --env-file .env \
     -f docker-compose.yml \
     -f docker-compose.server.yml \
     -f docker-compose.private-https.yml \
     config --quiet
   ```

7. 在容器变更批准窗口内拉取精确镜像，并重建前端以接入隔离 origin 网络。不得因此执行 Alembic、恢复数据或重建 PostgreSQL：

   ```bash
   docker compose --env-file .env \
     -f docker-compose.yml \
     -f docker-compose.server.yml \
     -f docker-compose.private-https.yml \
     pull cloudflared

   docker compose --env-file .env \
     -f docker-compose.yml \
     -f docker-compose.server.yml \
     -f docker-compose.private-https.yml \
     up -d --no-deps frontend cloudflared
   ```

## 真实验收

先在 Cloudflare 控制面完成脱敏读回，不得只因“一个 URL 能显示 Access”就判定策略安全：

1. Tunnel 的全部 public hostname / ingress 清单只有获批精确域名 `→ http://frontend:5173`，HTTP Host Header 为 `localhost`，末尾仅为 `http_status:404`；没有额外域名、路径、通配代理或 API/DB/SSH origin。
2. Access application 的全部 hostname 只有获批域名；全部 policy 只有获批的精确单用户 `Allow`，其 `Include`/`Require`/MFA/会话时长与批准单一致，且没有 `Bypass`、`Everyone`或整个邮箱域。
3. 账户级“未配置 Access 的主机名必须受保护”为开启；DNS 记录只指向该 Tunnel。
4. 把上述全量清单以截图或脱敏导出作为验收证据，包含读回时间与操作人；不保存 Tunnel token、Access API token、cookie、MFA 种子或完整邮箱。这一读回由用户会话在控制面完成，服务器不保存 Cloudflare 管理 API token。

控制面全量读回通过后，再在服务器运行只读基础验收。脚本检查 TLS 验证、未登录跳转、三个 loopback 端口、token 文件权限、容器镜像/用户/命令/环境/健康/资源/安全选项/日志/端口、实际网络隔离和日志不含 token；它不会修改容器或 Cloudflare 状态。

```bash
sudo PRIVATE_HTTPS_URL=https://<获批精确域名>/ \
  HTTPS_TEST_SCENARIO=空闲 \
  scripts/ops/inspect_private_https_entry.sh
```

然后按下表进行浏览器与 Cloudflare 审计日志验收。每一项都要记录时间、执行身份、预期、实际结果和对应日志事件；不写入 token、cookie、MFA 种子或完整个人信息。

| 验收项 | 必须读回的事实 | 失败处理 |
| --- | --- | --- |
| TLS | 公网域名证书链可验证，无 HTTP 明文入口 | 停止上线 |
| 未登录 | 无 cookie 请求只到 Access，未抵达前端 | 停止上线 |
| 正常登录 | 只有精确邮箱 + 独立 MFA 成功，成功/失败日志均可读 | 停止上线 |
| 登录后应用 | 首页、JS/CSS 资源和 `/api/health` 均成功，Vite 无 Host 拒绝，页面与 API 都经同一 Access 入口 | 停止上线 |
| 错误身份/MFA | 错误邮箱和错误 MFA 不能抵达源站，日志为失败 | 停止上线 |
| 显式退出 | 访问 `https://<域名>/cdn-cgi/access/logout` 后原 cookie 不再可用 | 停止上线 |
| 会话超时 | 测试时长到期后必须重新登录/MFA，最终配置回读不超过 8h/1h | 停止上线 |
| 管理员撤销 | 撤销后旧会话在 Cloudflare 传播窗口后不再被接受 | 停止上线 |
| 重复猜测 | 连续失败全部无法达到源站，有可审计限速/锁定证据 | 无证据则拒绝上线，转评 Caddy + Authelia |
| MFA 恢复 | 两种验证器可用；模拟一种丢失后可恢复，不新增 `Bypass` | 停止上线 |
| 失败关闭 | 经批准停止 `cloudflared` 后公网入口不可用，没有绕过 Access 的直连路径 | 保持停止并调查 |
| SSH 恢复 | Cloudflare 入口不可用时，SSH 隧道仍能独立访问 `127.0.0.1:15173/18000` | 不得改为公网开端口 |
| 资源 | 空闲、首次登录、持续访问和重连各有 `docker stats --no-stream` 证据，无 OOM/swap 抖动/超时 | 停止上线或重做资源决策 |

只有全部通过，且源站访问日志与 Access 日志的时间线能证明失败请求未到达前端，才能把 Issue #25 记为完成。

## 回滚与恢复

- Access、DNS 或 Tunnel 异常时，先使用 SSH 隧道读回 loopback 前端/API；不开放 `15173`、`18000` 或 `5432`。
- token 疑似泄露时，先在 Cloudflare 轮换并强制断开旧 token，再通过交互式脚本原子替换文件。文件 bind mount 仍会指向旧 inode，因此必须使用三层 Compose 执行 `up -d --no-deps --force-recreate cloudflared`；普通 `docker restart` 或未触发 recreate 的 `compose up` 不能完成 token 轮换。重建后重跑只读验收，确认宿主文件与容器 mount 为同一 inode。
- MFA 恢复只通过账户管理员移除失效验证器并重新登记；不创建公开临时 `Bypass`。
- 需要停用入口时，先禁用 public hostname / Tunnel，再在获批窗口停止 `cloudflared`。保留 token 文件与 Compose 证据，直到回滚完成且用户确认可删除；不删除 volume、PostgreSQL 数据、备份或 canonical 研究工件。
