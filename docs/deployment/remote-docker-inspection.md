# 远端 Docker 容器自动化巡检

本巡检只读远端 Docker 和 Compose 状态，用于发现 `db`、`api`、`frontend` 容器停止、健康检查失败、HTTP 不通或 Docker daemon 异常。它不执行构建、重启、删除容器、删除 volume 或数据库写入。

## 成功标准

1. 远端 `docker info` 可用。
2. `/opt/quantitative-trading` 中 `docker compose config --quiet` 通过。
3. `db`、`api`、`frontend` 三个服务都能解析到容器，且容器处于 running。
4. 有 Docker healthcheck 的容器必须是 healthy。
5. `db` 内 `pg_isready` 通过。
6. 后端 `GET /api/health` 通过。
7. 前端首页 HTTP 检查通过。
8. 输出一次 `docker stats --no-stream` 快照，用于排查 CPU、内存、网络和磁盘 IO 异常。

容器 restart count 大于 `0` 只记为 `WARN`，不让巡检失败；容器缺失、停止、unhealthy、数据库或 HTTP 不通记为 `FAIL`，脚本退出码为 `1`。

## 手动巡检

本机执行：

```bash
./scripts/ops/inspect_remote_docker.sh run
```

远端服务器上也可以直接执行：

```bash
cd /opt/quantitative-trading
./scripts/ops/inspect_server_docker.sh run
```

## 安装定时巡检

默认安装到远端 `ubuntu` 用户 crontab，每 `10` 分钟执行一次，日志写入：

```text
$HOME/quantitative-trading-logs/docker_container_inspection.log
```

安装：

```bash
./scripts/ops/inspect_remote_docker.sh install-cron
```

查看：

```bash
./scripts/ops/inspect_remote_docker.sh show-cron
```

移除：

```bash
./scripts/ops/inspect_remote_docker.sh remove-cron
```

如果要调整频率，先设置环境变量再安装：

```bash
INSPECTION_CRON_SCHEDULE="*/5 * * * *" ./scripts/ops/inspect_remote_docker.sh install-cron
```

## 边界

- 不运行 `docker compose up`、`docker compose down`、`docker compose down -v` 或 `docker volume rm`。
- 不读取或打印 `.env` 中的 token、密码和其他凭据。
- 不把 `5432`、`18000`、`15173` 暴露到公网。
- 不接入真实交易、券商、资金账户或下单接口。

后续如果需要通知能力，应在这个只读巡检通过后单独加 Feishu webhook 或邮件告警；告警内容只发送状态摘要，不发送日志全文和环境变量。
