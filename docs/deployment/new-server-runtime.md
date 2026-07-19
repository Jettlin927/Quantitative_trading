# 新服务器安全容器运行环境

本文只覆盖 `quant-trading-new` 的 Docker/Compose 基础，不覆盖生产数据恢复、Alembic、应用启动、流量切换或旧服务器清理。

## 固定边界

- Docker Engine 只从 [Docker 官方 Ubuntu apt 仓库](https://docs.docker.com/engine/install/ubuntu/)安装；不使用 convenience script。
- PostgreSQL、API 与前端宿主端口只绑定 `127.0.0.1`。Docker 发布端口可能绕过 UFW，因此不能用防火墙替代 Compose 的 host IP 约束。
- Docker daemon 只使用本机 Unix socket，不监听 `2375/2376` TCP。
- 服务器 `.env` 必须显式提供非默认 `POSTGRES_PASSWORD`；模板拒绝空值。真实凭据不得进入 Git、文档或命令输出。
- 本阶段不创建数据库、不拉取生产备份、不运行 migration、不启动应用容器。

## 目录合同

| 主机路径 | 用途 | 初始权限 |
| --- | --- | --- |
| `/srv/quantitative-trading/postgres` | PostgreSQL 数据根 | `root:root 0700`，未来由受控恢复流程初始化 |
| `/srv/quantitative-trading/research-artifacts` | canonical 研究工件 | 运行用户 `0750` |
| `/srv/quantitative-trading/backups/{daily,weekly,monthly}` | 本地备份分层 | `root:root 0700` |
| `/opt/quantitative-trading` | 未来应用 checkout | 运行用户 `0755` |
| `/opt/quantitative-trading-releases` | 不可变发布版本 | 运行用户 `0755` |
| `/opt/quantitative-trading-bootstrap` | 仅用于 Compose 解析验收 | 运行用户 `0755` |

critical 数据使用显式 bind mount，并设置 `create_host_path: false`；路径缺失时应失败，不能由 Docker 静默创建到未知位置。前端依赖缓存等可重建内容不作为持久资产。

## 资源合同

服务器为 2 核、3.6GiB 内存和 2GiB swap。当前四个服务的上限合计为 1.8 CPU、2816MiB 内存，给宿主机和未来的单并发 `research-worker` 留出余量：

| 服务 | CPU 上限 | 内存上限 | PID 上限 |
| --- | ---: | ---: | ---: |
| PostgreSQL | 0.75 | 1280MiB | 256 |
| API | 0.45 | 640MiB | 256 |
| 数据 Worker | 0.35 | 512MiB | 256 |
| 前端 | 0.25 | 384MiB | 128 |

镜像构建、数据恢复、migration 和正式研究不得并行。Compose 的 `cpus`、`mem_limit` 和 `pids_limit` 语义以 [Compose services 规范](https://docs.docker.com/reference/compose-file/services/)为准。容器与 daemon 日志均使用 `json-file`，单文件 10MiB、最多 3 份；配置依据 [Docker 日志轮转说明](https://docs.docker.com/engine/logging/drivers/json-file/)。

## 初始化与配置验证

在目标服务器有完整仓库文件时执行：

```bash
sudo RUNTIME_USER=ubuntu scripts/ops/bootstrap_new_server_runtime.sh
```

从独立工作区执行时，可先把以下三个文件复制到服务器临时目录，再显式传入路径：

- `docker-compose.yml`
- `docker-compose.server.example.yml`
- `scripts/ops/bootstrap_new_server_runtime.sh`

脚本会安装 Docker Engine、Compose 与 Buildx，设置 daemon 日志轮转和 [live restore](https://docs.docker.com/engine/daemon/live-restore/)，创建空目录并运行：

```bash
POSTGRES_PASSWORD=compose-config-only docker compose \
  --env-file /dev/null \
  -f /opt/quantitative-trading-bootstrap/docker-compose.yml \
  -f /opt/quantitative-trading-bootstrap/docker-compose.server.yml \
  config --quiet
```

`compose-config-only` 只用于解析配置，不写入 `.env`，也不启动容器。实际部署前把模板复制为项目目录内被 Git 忽略的 `docker-compose.server.yml`，再由人工设置真实 `.env` 并重新读回配置。

仓库内的静态门禁为：

```bash
scripts/ops/test_new_server_runtime.sh
bash -n scripts/ops/bootstrap_new_server_runtime.sh
```

## 回滚边界

本阶段没有应用容器、生产数据或流量，因此保留已安装软件和空目录是最安全的回滚状态。若 Docker daemon 本身影响主机，可先 `systemctl disable --now docker containerd`；卸载软件、删除目录或清理 Docker 数据根必须另行确认，不能由本脚本自动执行。未来迁移失败时仍按 #14 切回旧服务器并完整保留旧环境至少 14 天。

## 现场验收

2026-07-19 18:15 +08:00 在 `quant-trading-new` 完成只读回查：

- Ubuntu 26.04 `resolute` / amd64；Docker Engine 客户端与服务端均为 `29.6.2`，Compose `v5.3.1`，Buildx `v0.35.0`。安装包来自 `https://download.docker.com/linux/ubuntu` 的 `resolute/stable`。
- Docker 与 containerd 为 `active`，Docker 已设为开机启动；存储驱动为 `overlayfs`、cgroup driver 为 `systemd`、日志驱动为 `json-file`。`daemon.json` 已启用 live restore 与 `10m × 3` 日志轮转。
- 新 SSH 会话中 `ubuntu` 已进入 `docker` 组。该组按 [Docker 官方说明](https://docs.docker.com/engine/install/linux-postinstall/)具有 root 级权限，只授予现有的密码外 sudo 运维身份，未新增用户。
- 目录与权限逐项匹配上表；根盘约 492G、可用 466G，内存可用约 3.0GiB，2GiB swap 未使用。
- 合并配置读回为 PostgreSQL `127.0.0.1:5432`、API `127.0.0.1:18000`、前端 `127.0.0.1:15173`；两处持久挂载分别指向显式 PostgreSQL 与研究工件目录，worker 和 frontend 没有主机源码挂载或隐式 volume。
- 主机实际监听仍只有 SSH `22` 与本机 DNS；Docker 未监听 `2375/2376`。现场为 0 个容器、0 个 volume、0 个镜像，证明本阶段没有启动应用或初始化数据。
- 用于传输安装文件的精确临时目录和脚本内部临时目录均已删除并确认不存在；保留 `/opt/quantitative-trading-bootstrap` 中两份无凭据 Compose 文件作为后续解析证据。
