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
| `PERSONAL_ANALYSIS_DATABASE_URL` | `quant_personal_analysis` 的完整 PostgreSQL URL | 只供个人分析与持仓规则 Worker，不得与 API 连接复用 |
| `DEEPSEEK_CREDENTIALS_HOST_FILE` | 仅含 DeepSeek API key 的 JSON 文件绝对路径 | 只读挂载给个人分析 Worker，必须为 owner-only 权限 |
| `DEEPSEEK_MONTHLY_SOFT_BUDGET_USD` | DeepSeek 月度软预算 | 必须为正数，超预算 fail-closed |
| `PERSONAL_ANALYSIS_PROVIDER` | API 侧非 secret 能力开关 | 只有精确值 `deepseek` 才允许入队；默认 `disabled` |
| `OFFICIAL_ANALYSIS_QUERY_HOST_FILE` | 官方证据查询清单的宿主绝对路径 | 只读挂载给 API；含标的、固定查询类型、来源授权身份、检查时间、过期时间和内容哈希 |
| `OFFICIAL_ANALYSIS_AUTHORIZATION_HOST_FILE` | 官方证据用途授权快照的宿主绝对路径 | 只读挂载给 API；必须允许 display/internal_analysis/ai_context，禁止 redistribute/formal_research |
| `SEC_USER_AGENT` | SEC 要求的非空 User-Agent | 不得包含 secret；由 API 访问固定 SEC host 时使用 |

覆盖把个人访问文件只读挂载到 API：

- gateway token：`/run/secrets/personal-gateway-token`
- keyring：`/run/secrets/personal-keyring.json`

前端 Nginx 只额外接收同一个只读 gateway token 文件，用于同源反向代理注入
`X-Personal-Gateway`；浏览器源码和响应中不得出现 token。前端不得接收 keyring、
私库 URL 或个人 Origin。研究 Worker 不得接收任何个人 secret。容器内
路径由覆盖固定，不能在 `.env` 中改写。宿主文件不存在时 `create_host_path: false`
会拒绝启动，不能静默创建目录代替 secret。

`personal-ai` profile 默认关闭。只有后续精确授权启动时，个人分析 Worker 才接收：

- keyring：`/run/secrets/personal-keyring.json`；
- DeepSeek 凭据：`/run/secrets/deepseek-credentials.json`；
- 独立的 `PERSONAL_ANALYSIS_DATABASE_URL`。

API、前端、研究 Worker 和数据库均不得接收 DeepSeek 凭据。历史
`DEEPSEEK_TOKEN`、`DEEPSEEK_MODEL`、`DEEPSEEK_API_BASE` 环境入口不属于个人分析合同，
不得复用。模型固定为 `deepseek-v4-flash`，endpoint 固定为 DeepSeek 官方地址。
API 只接收非 secret 的 `PERSONAL_ANALYSIS_PROVIDER` 状态；缺省或其他值均保持
`provider_unavailable`，避免凭据尚未就绪时产生无人消费的真实任务。
未配置 #164 时，Compose 为 profile 内的数据库 URL 和凭据宿主路径保留不可用哨兵值，
使未启用该 profile 的日常配置检查不被阻断；误启 profile 会因无有效凭据而失败关闭。

## 官方证据查询与用途授权

API 的在线读取只支持两类固定、无凭据的首期查询：SEC `companyfacts` 与 BLS series。
查询清单按 `subject_id` 选择公司事实，`subject_id="*"` 选择所有问题共享的宏观事实；
不得配置任意 URL、任意请求方法或自定义 header。SEC 查询先读取固定 submissions
endpoint 取得 accession 的精确可得时间，再读取固定 companyfacts endpoint；BLS 只
读取固定公开 API。

当生产服务器无法直接访问上述官方端点时，查询清单也允许
`kind="frozen_official_fact"`。该类型只能保存从官方来源取得的最小事实摘录，并必须同时
固定 `source`、`dataset`、`evidence_id`、`field`、`excerpt`、`content_sha256`、`as_of`
和用途授权快照；`field` 只允许 `official_facts` 或 `macro_facts`。内容哈希、来源与授权
不一致，或 `as_of` 晚于当前时间时必须 fail-closed。冻结事实不包含 URL、请求头、凭据、
持仓或 provider payload，也不授权把非官方数据改名为官方证据。

查询文件与授权文件都是 schema version 1 的 JSON 对象，顶层必须包含 `checked_at`、
`expires_at` 和 `content_sha256`。哈希口径为：移除顶层 `content_sha256` 后，对剩余对象按
UTF-8、键排序、无多余空白的 canonical JSON 计算 SHA-256。任一文件缺失、过期、哈希
不符、用途授权不完整、标的无查询、来源失败或零合格事实时，预览返回明确 gap，API 与
前端均禁止入队。配置装配阶段不发送外部请求；只有用户生成预览时才执行有界只读 GET/
POST，且响应只保留最小结构化事实、证据身份、as-of 与内容哈希进入冻结包。

官方证据文件只挂载给 API。个人分析 Worker不接收这些文件，也不在执行阶段重新抓取；
它只能消费用户确认 preview SHA 后已写入私有数据库的冻结 evidence pack。查询/授权文件
不含 DeepSeek key、持仓数量、成本、市值、权重、行情或规则结果。

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

## DeepSeek 凭据格式

凭据文件只允许一个字段；不得写入 base URL、模型、预算或其他动态路由配置：

```json
{
  "api_key": "<DeepSeek API key>"
}
```

运行时拒绝 group/other 可读的凭据文件。DeepSeek 默认磁盘上下文缓存和输入/输出
处理政策必须出现在每次外发预览中；不得把 DeepSeek 描述为 `store=false` 或 ZDR。
真实 key 配置和首次请求分别受 #164、#166 的精确人工门禁控制。

## DeepSeek 模型、费用与数据政策快照

首期只允许 `deepseek-v4-flash`，固定调用
`https://api.deepseek.com/chat/completions`，单次最大输出为 4096 tokens。禁止旧别名、
任意 base URL、自动选模、跨供应商 fallback、流式调用和 hosted tools。
首期显式设置 `thinking.type=disabled`，避免默认思考模式产生未纳入产品合同的推理内容
和额外输出成本。

代码内价格快照生效日为 2026-04-24，币种 USD：缓存命中输入 `$0.0028`、缓存未命中
输入 `$0.14`、输出 `$0.28`，均按每百万 tokens 计。每次运行记录 input、output、
cache-hit、cache-miss token 和规范化价格快照 SHA；实际费用由真实 usage 计算。预览
估算使用“请求 UTF-8 bytes 作为保守输入 token 上界 + 最大输出 tokens”，不冒充真实
账单。价格来源为 [DeepSeek 官方模型与价格](https://api-docs.deepseek.com/quick_start/pricing)。

DeepSeek 官方说明磁盘上下文缓存默认启用；当前合同不具备可等同于 OpenAI
`store=false` 或 ZDR 的供应商保证。外发预览必须展示这一事实，并继续排除行情、派生
指标、个人收益及未经授权的私有字段。参考：[上下文缓存](https://api-docs.deepseek.com/guides/kv_cache)、
[隐私政策](https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html?locale=en_US)。

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
- 研究 Worker 看不到个人 secret，前端看不到 keyring、私库 URL 或个人 Origin；
- 缺少任一配置时个人路由继续 fail-closed；
- 错误 gateway token、错误 Origin 和公共数据库角色访问私库均被拒绝；
- 正确请求的最小成功路径只在 #162 目标镜像启动后验收。
