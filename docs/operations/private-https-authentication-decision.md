# 远程访问决策：仅使用 SSH 隧道

关联：[调研决策：私有 HTTPS 入口的身份认证方案](https://github.com/Jettlin927/Quantitative_trading/issues/15)、[运维：建设私有 HTTPS 研究前端入口](https://github.com/Jettlin927/Quantitative_trading/issues/25)。

## 当前生效决定

用户于 2026-07-20 明确选择：**SSH 隧道是研究系统唯一的远程访问入口**。

- PostgreSQL `5432`、API `18000` 与前端 `15173` 继续只监听 `127.0.0.1`。
- 不购买或申请域名，不部署 Cloudflare Tunnel、Cloudflare Access 或 Tailscale Serve。
- 不把前端、API、PostgreSQL 或其他应用端口绑定到公网 IP，也不增加对应的公网入站规则。
- GitHub 仓库是否私有与服务器端口是否暴露是两件事；私有仓库不能替代服务器网络隔离。
- 除非用户以后明确提出重新评估，否则后续任务不得再次询问域名、Cloudflare 账号、允许登录身份、MFA 或会话时长，也不得把 HTTPS 入口作为第一阶段完成条件。

## 使用方式

在需要访问时，从本机建立临时隧道：

```bash
ssh -N -L 15173:127.0.0.1:15173 quant-trading-new
```

隧道保持运行期间，在本机浏览器访问：

```text
http://127.0.0.1:15173
```

结束访问时在 SSH 命令所在终端按 `Ctrl-C`。该方式不改变服务器监听地址，也不需要域名、TLS 证书或外部身份平台。

Windows 家庭电脑的一次性密钥、SSH config、登录后自动隧道和本地验证步骤见[生产部署、受控切换与家庭电脑访问](production-deployment-and-home-access.md)。常驻隧道只提供连接，不会启动新服务器应用或迁移数据。

如需临时访问 API，必须同样通过显式 SSH 端口转发连接远端 `127.0.0.1:18000`；不得为了方便而开放公网监听。PostgreSQL 仍不作为普通浏览器访问路径。

## 历史决策的处理

Issue #15 曾基于当时“普通浏览器直接访问私有 HTTPS”这一目标，调研并推荐 Cloudflare Tunnel + Access。用户现已取消该目标，因此该结论只作为历史备选，不再构成实施授权：

- Issue #25 按“需求撤回、不计划实施”关闭。
- 准备 Cloudflare 入口的 Pull Request #35 关闭且不合并。
- 不创建域名、DNS、Cloudflare/Tailscale 资源，不向服务器写入相关 token 或配置。

若未来确实需要免 SSH 的浏览器入口，应由用户重新提出目标，另建 Issue 并重新评估网络暴露、身份认证、域名、成本和生产门禁；不能从旧调研结论直接恢复实施。
