# CI/CD 与部署边界

本仓库使用本地 Git、GitHub Actions 和目标服务器 Compose，但三者代表不同事实：代码已推送、CI 通过、镜像构建、容器启动、生产读回和业务验收不得混写。

## 配置入口

远端主机、端口、私钥和项目目录只保存在本机 `.env` 或 SSH config，不写入长期文档。运维脚本从这些入口读取配置：

- `scripts/ops/deploy_remote.sh`：从本机调用目标服务器。
- `scripts/ops/deploy_server.sh`：在目标服务器同步代码、解析 Compose、部署指定服务并读回健康状态。
- `docker-compose.yml`：统一基础 Compose。
- `docker-compose.server.yml`：目标服务器可选覆盖文件；不得包含提交到 Git 的真实凭据。

## 安全流程

1. 现场确认目标主机、分支、提交和工作区状态。
2. 运行与变更风险匹配的本地/隔离验证。
3. 推送代码并区分本地门禁与 GitHub CI。
4. 需要部署时单独确认生产授权，再由服务器拉取精确提交。
5. 读回镜像、容器、API、个人分析 Worker、前端、schema 与数据事实。
6. 保留回滚源码、数据库备份和未知来源持久文件；未经批准不清理。

## 禁止项

- GitHub Actions 不持有生产 SSH 私钥，也不执行依赖生产个人数据的任务。
- API 启动不自动执行 Alembic migration。
- 部署脚本不得删除 PostgreSQL volume、备份或未知来源持久文件。
- 数据库、API 和前端端口默认只监听 `127.0.0.1`；公网入口另走独立人工批准门。

旧部署说明及其时间点事实保存在[历史归档](../archive/operations/cicd-legacy-2026-07-19.md)。
