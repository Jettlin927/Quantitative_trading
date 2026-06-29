# CI/CD 部署脚本

本仓库的服务器部署入口保持为 `/opt/quantitative-trading`，远端服务仍由 Docker Compose 管理。脚本只做拉取代码、构建/启动指定服务和健康检查，不删除 PostgreSQL volume。

本地 `.env` 是后续 Codex session 的远端连接入口，至少保留：

```bash
REMOTE=ubuntu@182.254.180.169
REMOTE_SSH_PORT=22
REMOTE_SSH_KEY=/Users/jettlin/.ssh/quantitative_trading_server_ed25519
PROJECT_DIR=/opt/quantitative-trading
REPO_URL=ssh://git@ssh.github.com:443/Jettlin927/Quantitative_trading.git
BRANCH=main
```

`scripts/ops/deploy_remote.sh` 和 `scripts/ops/bootstrap_remote_github_ssh.sh` 会自动读取仓库根目录 `.env`。不要把服务器登录密码写入 `.env`。

## 一次性配置 GitHub SSH

本机先确认私有仓库可访问：

```bash
git ls-remote ssh://git@ssh.github.com:443/Jettlin927/Quantitative_trading.git HEAD
```

把本机 GitHub SSH key 放到服务器，并验证服务器能读取私有仓库：

```bash
LOCAL_KEY="$HOME/.ssh/id_ed25519" ./scripts/ops/bootstrap_remote_github_ssh.sh
```

脚本会把 key 放到服务器 `~/.ssh/quantitative_trading_github`，并用 `git ls-remote` 验证，不会把私钥写入仓库。

## 本机快速部署

```bash
./scripts/ops/deploy_remote.sh all
./scripts/ops/deploy_remote.sh frontend
./scripts/ops/deploy_remote.sh backend
./scripts/ops/deploy_remote.sh pg
./scripts/ops/deploy_remote.sh verify
./scripts/ops/deploy_remote.sh status
```

目标含义：

- `frontend`：拉取 GitHub 最新代码，只构建并重启 `frontend`，不重启 `api/db`。
- `backend` / `api`：拉取 GitHub 最新代码，确保 `db` 已启动，只构建并重启 `api`。
- `pg` / `db`：拉取 GitHub 最新代码，只启动/校验 PostgreSQL，不删除 volume。
- `all`：按 `db -> api -> frontend` 顺序部署。
- `verify`：只做 `pg_isready`、后端 `/api/health` 和前端 HTTP 检查。
- `status`：只输出 `docker compose ps`。

## 服务器上直接执行

登录服务器后也可以直接运行：

```bash
cd /opt/quantitative-trading
./scripts/ops/deploy_server.sh backend
```

如果远端有 `docker-compose.server.yml`，脚本会自动叠加它；没有该文件时使用仓库默认 `docker-compose.yml`。

如果 `/opt/quantitative-trading` 已存在但不是 Git checkout，首次部署会把旧目录移动为 `/opt/quantitative-trading.pre-git.<时间戳>`，再从 GitHub clone。脚本会把旧目录里的 `.env`、`docker-compose.server.yml` 和 `logs` 拷回新目录；PostgreSQL 数据仍在 Docker volume 中，不会被删除。

## 可选环境变量

```bash
REMOTE=ubuntu@182.254.180.169
PROJECT_DIR=/opt/quantitative-trading
REPO_URL=ssh://git@ssh.github.com:443/Jettlin927/Quantitative_trading.git
BRANCH=main
SSH_KEY=$HOME/.ssh/quantitative_trading_github
COMPOSE_SERVER_FILE=docker-compose.server.yml
```

`SKIP_GIT_PULL=1 ./scripts/ops/deploy_server.sh backend` 可以跳过远端 Git 拉取，仅对服务器现有代码执行部署。
