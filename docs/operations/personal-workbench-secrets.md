# 个人工作台生产 secret 与 Compose 覆盖

本页定义 #161 的稳定配置合同。`docker-compose.personal.yml` 是可选覆盖；默认部署不
加载它，个人工作台继续 fail-closed。本文和 Compose 工件不构成生产密码、secret、
配置或容器切换授权。

## 配置边界

启用覆盖前必须在受保护的服务器 `.env` 中配置以下键：

| 键 | 内容 | 规则 |
| --- | --- | --- |
| `PRIVATE_DATABASE_URL` | `quant_personal_api` 的完整 PostgreSQL URL | 不得使用数据库 owner 或超级用户 |
| `PERSONAL_GATEWAY_TOKEN_HOST_FILE` | gateway token 的宿主绝对路径 | 普通文件，最小权限，不得放进 release 目录 |
| `PERSONAL_DATA_KEYRING_HOST_FILE` | keyring JSON 的宿主绝对路径 | 普通文件，最小权限，不得放进 release 目录 |
| `PERSONAL_ALLOWED_ORIGINS` | 逗号分隔的精确浏览器 Origin | 不允许 `*`，按当次 SSH 隧道入口冻结 |

覆盖把两个文件只读挂载到 API：

- gateway token：`/run/secrets/personal-gateway-token`
- keyring：`/run/secrets/personal-keyring.json`

前端 Nginx 只额外接收同一个只读 gateway token 文件，用于同源反向代理注入
`X-Personal-Gateway`；浏览器源码和响应中不得出现 token。前端不得接收 keyring、
私库 URL 或个人 Origin。同步 Worker和研究 Worker不得接收任何个人 secret。容器内
路径由覆盖固定，不能在 `.env` 中改写。宿主文件不存在时 `create_host_path: false`
会拒绝启动，不能静默创建目录代替 secret。

## Keyring 格式

keyring 是 JSON 对象，包含活动数据密钥、按 key identity 索引的数据密钥集合和
独立 lookup key。所有密钥解码后必须符合 AES-256 长度；真实值不得进入 Git、Issue、
PR、日志、终端回显或截图。

```json
{
  "active_key_id": "<受控 key identity>",
  "data_keys": {
    "<受控 key identity>": "<32-byte key 的 base64>"
  },
  "lookup_key": "<独立 32-byte key 的 base64>"
}
```

gateway token 与 keyring 的生成、宿主路径、owner、mode、轮换和备份范围都必须在
#161 获得精确授权。不得在 shell history 中放置生成后的值，也不得用 `cat`、shell
trace 或 Compose 渲染输出读取 secret。

## 受控验证

没有生产授权时只验证合并配置，使用合成路径和值：

```bash
scripts/ops/test_new_server_runtime.sh
```

生产配置获批后，先检查键和文件是否存在、owner/mode 是否精确，不输出内容；再将
`COMPOSE_PERSONAL_FILE=docker-compose.personal.yml` 传给部署入口，使覆盖作为最后一个
Compose 文件渲染。默认留空时部署入口不加载该覆盖，既有部署行为不变。渲染输出
包含数据库 URL，因此不得写入日志、Issue 或 PR。容器启动与应用版本切换属于 #162，
不能在 #161 的文件准备阶段提前执行。

生产读回必须证明：

- API 看到固定的两个容器内路径，两个 mount 均为只读；
- 前端只看到只读 gateway token 文件，浏览器看不到 token；
- Worker和研究 Worker看不到个人 secret，前端看不到 keyring、私库 URL 或个人 Origin；
- 缺少任一配置时个人路由继续 fail-closed；
- 错误 gateway token、错误 Origin 和公共数据库角色访问私库均被拒绝；
- 正确请求的最小成功路径只在 #162 目标镜像启动后验收。
