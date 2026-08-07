# 美股数据边界

当前数据主线只有按用途授权的 Alpaca 市场观察和用户手工维护的个人事实。

## Alpaca 市场观察

- `backend/app/market_observation/` 负责 Alpaca 适配、来源健康、用途授权和追加式授权注册。
- API、标的页、规则和 AI 工具显式声明用途；凭据或授权缺失时返回
  `unavailable`/fail-closed，不伪造价格或零值。
- 每条观察保留来源与 `as_of`；超出授权用途或时效后不能冒充当前事实。

## 私有个人事实

- 持仓、现金、成本、成交、规则与分析存放在隔离的 `private_workbench` schema。
- 持仓和成交只接受用户手工命令；不连接券商，不读取真实订单，不从 Gmail 或 broker
  export 自动同步。
- Alpaca/DeepSeek secret 只通过受保护文件注入获准进程，不进入源码、数据库、日志或浏览器。
- 合成测试夹具与个人数据隔离，不得用于生成看似真实的用户持仓或行情。

## 遗留数据线

A 股、`us_experiment_*`、旧 sample/HSBC ledger 和量化研究数据均已退出当前产品。
对应 Alembic 历史表、代码或文档的存在只表示兼容或审计资产；不得恢复数据拉取、
生产写入、API、页面或定时任务。物理清理遵守
[ADR 0011](../../adr/0011-personal-investment-workbench-without-research.md)的独立审计与授权要求。

## 运维与验证

- 私有 secret 与 Compose 覆盖：[个人工作台生产 secret](../../operations/personal-workbench-secrets.md)
- schema 与 PostgreSQL 集成验证：[变更验证](../../agents/validation.md)
- 当前产品方向：[ADR 0011](../../adr/0011-personal-investment-workbench-without-research.md)
