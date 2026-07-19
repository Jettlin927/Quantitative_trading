# 运维手册

本模块保存稳定的部署、迁移、巡检、备份和恢复流程。主机、分支、提交、磁盘、schema 与运行状态必须现场核验，不从文档快照推断。

- [CI/CD 与部署边界](cicd.md)
- [远端 Docker 只读巡检](remote-docker-inspection.md)
- [私有 HTTPS 入口身份认证决策](private-https-authentication-decision.md)
- [不可变生产验收证据](../acceptance/)

生产 migration、baseline stamp、覆盖恢复、生产切换、旧服务器清理、凭据变更和公网入口上线都需要用户单独批准。
