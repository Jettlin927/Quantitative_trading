# 运维手册

本模块保存稳定的部署、迁移、巡检、备份和恢复流程。主机、分支、提交、磁盘、schema 与运行状态必须现场核验，不从文档快照推断。

- [CI/CD 与部署边界](cicd.md)
- [远端 Docker 只读巡检](remote-docker-inspection.md)
- [生产服务器安全容器运行环境](new-server-runtime.md)
- [生产服务器部署、受控变更与家庭电脑访问](production-deployment-and-home-access.md)
- [生产 PostgreSQL 运行角色与私有权限](production-postgres-runtime-roles.md)
- [个人工作台生产 secret 与 Compose 覆盖](personal-workbench-secrets.md)
- [GitHub 研究计划与服务端编排器](research-orchestrator.md)
- [远程访问决策：仅使用 SSH 隧道](private-https-authentication-decision.md)
- [研究评价一致发布与恢复](research-publication.md)
- [不可变生产验收证据](../acceptance/)

生产 migration、baseline stamp、覆盖恢复、生产切换、持久数据清理、凭据变更和公网入口上线都需要用户单独批准。
