# 私有 HTTPS 入口身份认证决策

关联：[调研决策：私有 HTTPS 入口的身份认证方案](https://github.com/Jettlin927/Quantitative_trading/issues/15)、[运维：建设私有 HTTPS 研究前端入口](https://github.com/Jettlin927/Quantitative_trading/issues/25)。

实施入口：[私有 HTTPS 研究前端入口手册](private-https-entry.md)。

## 一句话结论

采用 **Cloudflare Tunnel + Cloudflare Access**：新服务器只运行一个出站连接的 `cloudflared`，Access 仅允许一个明确身份并强制独立 MFA；前端、API 与 PostgreSQL 继续不暴露公网，SSH 隧道保留为独立恢复通道，上线仍须用户批准域名、Cloudflare 控制面和生产配置。

## 决策边界

本决策基于以下已确认约束：

- 只有一名用户，需要从普通浏览器访问只读研究前端。
- PostgreSQL `5432`、API `18000` 与前端 `15173` 不得直接暴露公网。
- 新服务器只有 2 核、3.6 GiB 内存与 2 GiB swap，认证入口不能引入重量级身份平台。
- 凭据不能进入仓库、镜像、Compose 展开结果、日志或前端资源。
- SSH 隧道继续可用，且必须能在外部身份服务或隧道故障时独立恢复访问。
- 后续实施必须真实验证 TLS、登录、退出、会话失效、失败尝试限制和恢复，而不是只验证容器启动。

GitHub 原生关系现场读回显示：本票父 Issue 是[寻路地图](https://github.com/Jettlin927/Quantitative_trading/issues/3)，没有未完成的 `blocked_by`，并直接阻塞[私有 HTTPS 研究前端入口](https://github.com/Jettlin927/Quantitative_trading/issues/25)。

## 为什么选择 Cloudflare Tunnel + Access

### 1. 网络边界最小

Cloudflare 官方说明，`cloudflared` 从源站发起出站连接，防火墙可以阻断全部入站流量；私有 Web 应用指南也明确说明 Tunnel 无需在源网络开放端口。这样无需把宿主机 `80/443` 或任何应用端口直接开放到公网，只把明确的前端源站路由交给隧道。

- [Cloudflare Tunnel：出站连接模型](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/)
- [Cloudflare 私有 Web 应用：Tunnel + Access 工作方式](https://developers.cloudflare.com/cloudflare-one/setup/secure-private-apps/private-web-app/)
- [Tunnel 防火墙要求：仅允许必要的出站连接](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/tunnel-with-firewall/)

`cloudflared` 可以在宿主机访问 loopback，也可以作为容器只加入前端所在的私有 Compose 网络；具体实现可二选一，但不变量是源站没有公网路由，API 与 PostgreSQL 不加入 Tunnel ingress。

### 2. 身份与会话能力完整

Access 的 self-hosted application 原生提供身份策略、应用令牌与强制重新认证。策略默认拒绝，没有显式 Allow 就不能访问；本项目只设置一条 Allow：`Include` 为当前 Cloudflare Account Member，`Require` 为唯一精确邮箱并强制独立 MFA，禁止 `Everyone`、整个邮箱域和 `Bypass`。新 Zero Trust 组织可直接用限制为当前 Cloudflare 账户成员的 Cloudflare IdP，避免在服务器保存 OAuth client secret。

- [Access self-hosted application 与会话能力](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/choose-application-type/)
- [Access 策略：默认拒绝及常见误配置](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/)
- [Cloudflare 作为 IdP：限制到 Cloudflare 账户成员](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/cloudflare/)
- [对未配置 Access 的主机名强制默认拒绝](https://developers.cloudflare.com/cloudflare-one/access-controls/access-settings/require-access-protection/)

Access 会签发全局会话令牌和应用令牌，可分别设置持续时间。用户访问应用域名下的 `/cdn-cgi/access/logout` 可以清除应用 cookie；管理员可以撤销用户会话，官方说明既有令牌会在短暂传播窗口后停止接受。

- [Access 会话时长、用户退出与管理员撤销](https://developers.cloudflare.com/cloudflare-one/access-controls/access-settings/session-management/)

实施默认值采用短会话而非“永久登录”：应用/策略会话不超过 8 小时，MFA 会话不超过 1 小时，不启用任何 Bypass。具体时长属于生产配置，仍需用户在上线前确认。

### 3. 不在服务器维护口令数据库

服务器只持有运行指定 Tunnel 所需的窄权限 token，不保存用户密码、TOTP 种子、OAuth client secret、Access API 管理 token 或本地认证数据库。`cloudflared` 2025.4.0 及以上支持 `--token-file`；token 必须通过宿主机权限为 `0600` 的独立 secret 文件只读挂载，不能写进命令、普通环境变量或 Compose 文件。

- [cloudflared `--token-file` 参数](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/run-parameters/)
- [Tunnel token 泄露后的轮换与强制断开](https://developers.cloudflare.com/tunnel/advanced/tunnel-tokens/)

Cloudflare Access 独立 MFA 支持 TOTP、WebAuthn 安全密钥和设备生物识别。本项目必须启用独立 MFA，并至少登记两种可恢复的验证器，避免单设备丢失造成锁死。

- [Access 独立 MFA 与锁定恢复](https://developers.cloudflare.com/cloudflare-one/access-controls/access-settings/independent-mfa/)
- [在应用或策略层强制 MFA](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/mfa-requirements/)

### 4. 本机资源与恢复面较小

认证、策略和会话判断由 Access 承担，服务器只增加一个官方称为 lightweight daemon 的 `cloudflared`，不增加反向代理、用户库、Redis、LDAP 或本地认证数据库。但 Cloudflare 同一份系统要求仍建议生产环境每个位置运行两台独立 connector 主机，且每台至少 4 GiB 内存、4 核；当前 2 核、3.6 GiB 主机既低于该基线，也无法提供两机高可用。因此本决策只批准“单用户、单 connector、非高可用”的受限验证，不得把它描述成符合 Cloudflare 生产基线。

- [cloudflared 系统要求与生产基线](https://developers.cloudflare.com/tunnel/downloads/system-requirements/)

Cloudflare 官方没有给出可作为本项目承诺的固定 RSS 上限，因此“轻量”不能只凭产品描述验收；实施必须用 `docker stats --no-stream` 分别记录空闲、首次登录、持续访问和重连时的 CPU/RSS，并设置与实测相容的容器资源上限和日志轮转。任一场景造成 swap 抖动、OOM、前端超时或 Tunnel 不稳定即阻断上线。

恢复路径与 Access 控制面分离：

1. Access、DNS 或 Tunnel 故障时，使用既有 SSH 别名和 SSH 隧道直连 loopback 前端/API。
2. 在 SSH 通道内检查 `cloudflared` 健康、Tunnel 状态和源站 loopback 读回。
3. token 疑似泄露时先在 Cloudflare 轮换并强制断开旧连接，再替换服务器 secret 文件并重启单个 connector。
4. MFA 全部丢失时由账户管理员删除失效验证器，再重新登记；不能通过公开临时 Bypass 恢复。
5. Cloudflare 控制面不可接受或长期不可用时，保持 SSH 隧道，不临时开放前端、API 或 PostgreSQL 端口。

## “暴力尝试限制”的诚实边界

推荐配置不在服务器暴露静态用户名/密码端点，而是使用“精确单用户 Allow + Cloudflare 账户身份 + Access 独立 MFA”。Access 官方认证日志会记录成功和失败登录，可用于验收与审计。

- [Access 成功/失败认证日志](https://developers.cloudflare.com/cloudflare-one/insights/logs/dashboard-logs/access-authentication-logs/)

如果用户改选邮件一次性 PIN，官方只明确保证 PIN 在 10 分钟后过期、单次使用，申请新 PIN 会使旧 PIN 失效；官方页面没有承诺一个可由本项目配置的数值化猜测次数上限。因此不得把“用了 OTP”写成“已验证限速”。

- [Access One-time PIN 的有效期与单次使用语义](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/one-time-pin/)

[私有 HTTPS 研究前端入口](https://github.com/Jettlin927/Quantitative_trading/issues/25) 的上线门禁必须包含：

- 连续失败认证全部无法抵达源站，并能在 Access 认证日志中读回。
- 独立 MFA 的错误验证、会话超时、用户退出和管理员撤销均能阻断旧会话。
- 若实际租户无法证明身份提供方对重复猜测的限速/锁定，或无法配置可审计的等价控制，则验收失败、不得上线；此时转用下文的 Caddy + Authelia 备选，而不是弱化 Issue 验收。

这是一项明确的实施验证门，不是已被官方资料证明的 Cloudflare 数值承诺。

## 淘汰方案

### Tailscale Serve：不选

Tailscale Serve 很适合把 localhost 服务只提供给 tailnet，并会附加身份头；但访问者必须先成为 tailnet 客户端。它提供的是网络成员身份与 ACL，不是本票要求的浏览器应用登录门户，也没有为本应用单独定义退出、cookie 会话过期和失败登录次数控制。不能把“非 tailnet 不可达”冒充“应用会话已验收”。SSH 隧道已经承担客户端依赖型的兜底职责，因此不再增加第二条同类路径。

- [Tailscale Serve：tailnet 可达性、HTTPS 与身份头](https://tailscale.com/docs/features/tailscale-serve)
- [Tailscale 设备撤销](https://tailscale.com/kb/1260/device-remove)

### Caddy `basic_auth`：不选

Caddy 能自动管理 HTTPS，`basic_auth` 也要求配置哈希密码；但 HTTP Basic Auth 是浏览器反复发送 `Authorization` header 的挑战响应机制，不提供本票要求的独立应用会话、标准退出和服务端会话撤销。它不能满足 #25 的完整验收。

- [Caddy 自动 HTTPS](https://caddyserver.com/docs/automatic-https)
- [Caddy `basic_auth`](https://caddyserver.com/docs/caddyfile/directives/basic_auth)

### Caddy + Authelia：仅作条件备选

Authelia 与 Caddy 的官方 ForwardAuth 集成能够提供会话过期、口令暴力尝试封禁和 secret 文件加载，功能上可以满足验收；但它需要公网 HTTPS 反向代理、两个本地服务、本地用户文件、加密存储与恢复状态，增加了 3.6 GiB 主机的常驻资源和备份责任。因此只有用户明确拒绝 Cloudflare 域名/控制面，或 Cloudflare 无法通过重复猜测限制门禁时，才重新批准该备选的生产配置。

- [Authelia 与 Caddy ForwardAuth](https://www.authelia.com/integration/proxies/caddy/)
- [Authelia 会话失效配置](https://www.authelia.com/configuration/session/introduction/)
- [Authelia 暴力尝试封禁](https://www.authelia.com/configuration/security/regulation/)
- [Authelia 文件 secret](https://www.authelia.com/configuration/methods/secrets/)

### oauth2-proxy：不选

oauth2-proxy 仍需单独的 TLS 入口、OAuth/OIDC 客户端与本地 cookie secret；其官方文档还明确说明 `/oauth2/sign_out` 只清除自身 cookie，用户可能仍登录身份提供方并自动重新登录。相对 Access 没有减少本机组件，也没有独立解决失败尝试限制。

- [oauth2-proxy 退出端点的会话边界](https://oauth2-proxy.github.io/oauth2-proxy/features/endpoints/)
- [oauth2-proxy 会话存储与 cookie 生命周期](https://oauth2-proxy.github.io/oauth2-proxy/configuration/session_storage/)

## #25 可直接采用的验收清单

- [ ] 用户批准域名、Cloudflare 控制面、唯一允许身份、MFA 方法和会话时长。
- [ ] 外网只看到 Access 登录；未认证、错误身份、无 MFA 和已撤销会话均不能抵达源站。
- [ ] `5432`、`18000`、`15173` 的宿主机监听继续为 loopback，云安全组与主机防火墙没有对应公网入站规则。
- [ ] Tunnel ingress 只有前端；没有 API、PostgreSQL、SSH、管理端或通配兜底路由。
- [ ] `cloudflared` token 只来自只读 secret 文件，仓库、镜像、Compose 展开结果和日志搜索均无凭据。
- [ ] HTTPS 证书链、登录、显式退出、会话超时、管理员撤销、MFA 错误/恢复和失败认证审计逐项读回。
- [ ] 重复猜测限制有实际证据；无法证明则停止上线并回到 Caddy + Authelia 备选。
- [ ] 停止 `cloudflared` 后入口失败关闭，SSH 隧道仍能独立访问 loopback 服务。
- [ ] 空闲、登录、访问和重连的 CPU/RSS、重启策略与日志上限有服务器实测记录。

## 可直接发布到 Issue 的结论

> 结论：选择 Cloudflare Tunnel + Access 作为单用户私有 HTTPS 入口，使用精确单用户策略、独立 MFA、短会话和文件化 Tunnel token，在不开放服务器入站应用端口的前提下把认证与会话移到 Access；SSH 隧道继续作为独立恢复通道，域名/Cloudflare 控制面/生产配置及重复猜测限制实测均是上线前人工门禁，任一不通过则不部署并回退评估 Caddy + Authelia。
