# 系统流程导航

本页说明美股优先工作台的当前控制面、运行面和隔离边界。稳定代码职责见[代码地图](code-map.md)，退役决定见 [ADR 0010](../adr/0010-us-first-workbench-and-retired-legacy-data-paths.md)。

## 当前拓扑

```text
浏览器 / SSH 隧道
        │
        ▼
frontend ──同源 /api──► api ─────────► PostgreSQL
                          │               ├─ public：研究与历史 schema
                          │               └─ private_workbench：手工持仓/规则/分析
                          ▼
                 Alpaca 市场观察（显式授权）

GitHub Issues ──► research-worker ──► quant_research ──► canonical 工件
                           │                                   │
                           └──── PostgreSQL 编排/评价 ◄────────┘

personal-analysis-worker ──► DeepSeek + 受控工具
        （只读私有 secret，与正式研究隔离）
```

`docker-compose.yml` 定义 `db`、`api`、`research-worker` 和 `frontend`；`research-worker` 默认不随普通启动运行。`docker-compose.personal.yml` 添加私有 API 配置与 `personal-analysis-worker`。

公共数据同步 Worker 已退役。API 请求进程不承担 Tushare、AKShare 或 yfinance 的批量拉取。

## 个人工作台流程

1. 用户通过 `/today`、`/portfolio`、`/markets/us` 和 `/rules` 查看或维护手工美股投研上下文。
2. 私有写请求经过 gateway、Origin、Fetch Metadata、JSON、显式个人请求头和幂等校验；配置不完整时 fail-closed。
3. `PortfolioBook` 保存手工持仓和权益事实；市场价格与 K 线通过 Alpaca adapter 按用途授权读取。
4. 规则引擎生成确定性的 `triggered`、`not_triggered`、`unavailable` 或 `invalid` 结果，不把缺失数据伪造成零值。
5. 个人 AI 分析先生成外发预览，再显式确认并进入独立 worker；工具证据必须带可校验 `evidence_id`。
6. AI 输出、规则命中和 synthetic trace 都不是正式研究批准、运行、评价或结论。

个人不可变记录版本链路已退役；正式研究发布的不可覆盖合同不受影响。

## 正式研究生命周期

1. **提案与冻结计划**：`research_plan.py` 解析 `research-plan/v3`，冻结假设、范围、数据、基准、成本、门禁和试验预算。
2. **人工批准**：只有授权用户对精确 `plan_sha256` 的批准评论有效；标签不是授权。
3. **编排与队列**：`research_worker.py` 和 `research_orchestration.py` 校验 Issue、代码提交、资源与租约。
4. **运行与证据**：`quant_research/` 只执行已合并、CI 通过且静态登记的策略，输出 canonical 工件和账本。
5. **评价与结论**：结论只允许研究通过、有条件候选、证据不足、受阻或不通过；运行成功不等于研究通过。
6. **发布与读回**：数据库、工件、Issue、API 和前端必须读回同一评价版本与指纹；更正创建前向替代版本。
7. **监测与后续提案**：新增数据只形成观察；改变结论必须生成新提案并重新批准。

完整合同见[策略评价规范](../research/contracts/strategy-evaluation-standard.md)、[研究编排器](../operations/research-orchestrator.md)和[一致发布](../operations/research-publication.md)。

## 市场数据与历史 schema

- 当前在线市场观察：`market_observation/` 的 Alpaca adapter、来源授权和健康度。
- 当前研究质量：`data_quality/` 的质量运行与 readiness；活动默认范围由美股/ETF 主线决定。
- 已退役：A 股 Tushare 同步、免费美股实验、旧 sample/HSBC ledger 及其 API、cron 和页面。
- 继续保留：Alembic 历史 revision、旧表身份、历史研究工件和 dated 审计证据。

本地或 CI 验证可执行 `alembic upgrade head` 建立完整 schema，再插入合成夹具。schema 中存在退役表不表示可以恢复数据拉取、生产写入或产品入口。

## 接到任务时去哪找

| 任务类型 | 先读 | 主要入口 |
| --- | --- | --- |
| 个人工作台 | ADR 0007、0009、0010 | `personal_workspace/`、`personal_analysis_worker.py` |
| 美股市场数据 | [美股数据边界](../data/us/) | `market_observation/` |
| 策略研究/评价 | [策略评价规范](../research/contracts/strategy-evaluation-standard.md) | `quant_research/`、`scripts/research/` |
| 编排/Issue 自动化 | [研究编排器](../operations/research-orchestrator.md) | `research_plan.py`、`research_orchestration.py`、`research_worker.py` |
| 发布/读回 | [一致发布](../operations/research-publication.md) | `research_publication.py`、`research_catalog.py`、`research_analytics.py` |
| schema/migration | `backend/migrations/` + 生产门禁 | `models.py`、Alembic revision、PostgreSQL 集成测试 |
| 部署/凭据 | [生产部署合同](../operations/production-deployment-and-home-access.md) | `scripts/ops/`、Compose 覆盖 |

表行数、提交、容器、数据新鲜度和部署状态必须现场核验，不能引用本文档或历史记录代替。
