# 个人工作台使用私有存储并与正式研究隔离

个人美股 AI 投研工作台保存用户手工声明的持仓、规则、分析草稿和研究记录。这些对象属于单一用户的私有研究上下文，不是实际市场数据、正式研究输入或研究结论。冻结依据是 [个人美股 AI 投研工作台首期实现规格](https://github.com/Jettlin927/Quantitative_trading/issues/140#issuecomment-5159448603)。

系统采用以下稳定边界：

- 私有对象只写入 PostgreSQL 的 `private_workbench` schema，并由 `PRIVATE_DATABASE_URL` 对应的独立进程角色访问；私有 schema 与 public schema 不建立跨 schema 外键。
- ticker、名称、数量、均价、现金、备注、规则参数、问题正文、分析内容和记录正文使用 AES-256-GCM envelope 加密；密钥来自只读 keyring 文件，不进入数据库、日志、前端或研究工件。等值查询只保存独立 lookup key 生成的 HMAC。
- API 可以通过旅程投影只读公开市场、官方证据和已发布正式研究；公开数据及正式研究模块不能反向读取个人持仓、规则、分析或记录。
- `quant_research`、research worker 和发布路径不得导入个人工作台模块；research worker 不取得私有数据库连接、私有 keyring 或 OpenAI key，数据库角色也不具有私有 schema 的 `USAGE` 或 `SELECT`。
- 个人记录、AI 运行成功、规则命中和 synthetic tracer 均不能映射为正式研究批准、运行、评价或结论。

首期仍只通过 SSH 隧道访问 loopback 前端。受信前端代理向私有 API 注入浏览器不可见的 gateway 凭据，私有写请求同时校验精确 Origin、Fetch Metadata、JSON、显式个人请求头和幂等键。任一私有配置缺失时，私有路由 fail-closed，公开只读能力继续启动。

本决定不授权生产 migration、数据库角色或 secret 变更，不授权配置真实 provider key、写入真实持仓、部署或启动正式研究；这些动作必须经过对应人工门禁的精确授权。
