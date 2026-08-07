# 系统流程

本页描述个人投资工作台当前控制面、运行面和隔离边界。代码职责见[代码地图](code-map.md)。

## 当前拓扑

```text
浏览器 / SSH 隧道
        │
        ▼
frontend ──同源 /api──► api ─────────► PostgreSQL
                          │               ├─ public：公共兼容 schema
                          │               └─ private_workbench：持仓/规则/分析
                          ▼
                  Alpaca 市场观察

personal-analysis-worker ──► DeepSeek + 受控只读工具
          │
          └───────────────► 活跃持仓规则周期评估 ──► 今日注意事项
```

公共 Compose 启动 PostgreSQL、API 和前端；私有 Compose 覆盖添加工作台配置与
`personal-analysis-worker`。旧研究 Worker 和数据同步 Worker 不属于当前拓扑。

## 持仓与权益

1. 用户通过 `/portfolio` 提交带持仓版本和幂等键的手工命令。
2. 私有 API 校验 gateway、Origin、Fetch Metadata、JSON、个人请求头和并发版本；
   配置不完整时 fail-closed。
3. `PortfolioBook` 保存持仓、现金和已实现交易事实；价格由 Alpaca adapter 按用途读取。
4. 权益投影保留价格时点和缺失语义，写入每日快照后供前端读取。

## 规则与今日事项

1. 用户为持仓标的启用确定性观察规则。
2. 私有 Worker 按 XNYS 日历在盘前、盘中和盘后评估“活跃持仓 ∩ 已启用规则标的”；
   休市日不运行，同一交易日与时段保持幂等。
3. 最新规则命中与数据缺口投影到 `/today`；移出持仓后不再自动评估。
4. 旧 `/rules` 地址只负责回到今日页，不是独立产品入口。

## AI 分析

1. 用户创建分析草稿，前端先展示目标、个人上下文和外发预览。
2. 用户显式确认后，任务进入 `personal-analysis-worker`；只有该进程可读取模型 secret。
3. 工具只读持仓、行情和获准来源，返回带 `evidence_id` 与时点的证据。
4. 前端展示运行事件和结果；模型输出只作个人参考，不触发交易或后台写入。

## 接到任务时去哪找

| 任务 | 先读 | 主要入口 |
| --- | --- | --- |
| 持仓、现金、权益或成交事实 | [产品范围](../product/) | `personal_workspace/portfolio.py`、`router.py` |
| 今日事项或规则 | ADR 0010 | `personal_workspace/rules.py`、`rule_automation.py`、`journey.py` |
| 美股行情与来源 | [美股数据边界](../data/us/) | `market_observation/`、`personal_workspace/market_runtime.py` |
| AI 分析 | ADR 0009 | `personal_workspace/analysis.py`、`personal_workspace/agent/`、`personal_analysis_worker.py` |
| schema/migration | `backend/migrations/` + 生产门禁 | `models.py`、Alembic、PostgreSQL 集成测试 |
| 部署/凭据 | [生产部署合同](../operations/production-deployment-and-home-access.md) | `scripts/ops/`、Compose 覆盖 |

表行数、提交、容器、数据新鲜度和部署状态必须现场核验。
