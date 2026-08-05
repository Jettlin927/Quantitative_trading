# 美股数据边界

美股是当前产品主线。在线市场观察通过 Alpaca adapter 按用途授权读取，个人持仓只由用户手工维护；正式研究仍需独立满足 point-in-time、历史 universe、许可、质量、冻结快照和人工批准合同。

## 当前数据线

### Alpaca 市场观察

- `backend/app/market_observation/` 负责 Alpaca 适配、来源健康、用途授权和追加式授权注册。
- API、个人标的页、规则和 AI 工具必须显式声明用途；凭据或授权缺失时返回 unavailable/fail-closed，不伪造价格或零值。
- 在线观察只代表其 `as_of` 时点和授权范围，不能自动晋升为正式研究证据。

### 私有个人上下文

- 持仓、现金、成本、规则与分析存放在隔离的 `private_workbench` schema，并按私有工作台安全合同访问。
- 持仓只接受用户手工命令；不连接券商、不读取真实订单、不从 Gmail 或 broker export 自动同步。
- Alpaca/DeepSeek secret 只通过只读文件注入被授权进程，不进入源码、数据库、日志或浏览器。

### 正式研究

- `backend/app/quant_research/` 保存与券商无副作用的运行、评价和复现内核。
- 实际市场观察、个人持仓、合成夹具和正式研究证据必须保持 schema、权限和结论隔离。
- 研究运行成功不等于研究通过；正式结论只来自获批冻结计划的结构化评价与一致发布。

## 已退役数据线

- `us_experiment_*` 的 AKShare 目录、yfinance 主日线、双源校验、回填、checkpoint、cron、API 和前端入口已退役。
- 旧 `assets` / `asset_daily_prices` sample 预览、`my_quant/us_research` 观察池及 `my_quant/us_holdings` HSBC ledger 已退出当前代码路径。
- 对应历史表仍可存在于 Alembic schema；历史研究文档继续作为当时证据保留，但不能当作当前操作手册或研究级数据。

本地与 CI 测试只需建立完整 schema 并插入合成数据，不调用上述退役源。未来若要恢复任何第三方源，必须新建设计与授权，不得借历史表名绕过 [ADR 0010](../../adr/0010-us-first-workbench-and-retired-legacy-data-paths.md)。

## 运维与验证

- 私有 secret 与 Compose 覆盖：[个人工作台生产 secret](../../operations/personal-workbench-secrets.md)
- schema 与 PostgreSQL 集成验证：[变更验证](../../agents/validation.md)
- 正式研究资格：[量化研究可信合同](../../research/contracts/quant-foundation-trust-contract.md)
- 当前方向：[ADR 0010](../../adr/0010-us-first-workbench-and-retired-legacy-data-paths.md)
