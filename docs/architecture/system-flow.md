# 系统流程导航

本页说明系统"怎么运转"：容器拓扑、研究生命周期与市场数据同步的每一步由哪些
模块负责、入口在哪、该读什么文档。它面向第一次接触仓库的智能体，把"这个系统
怎么工作"和"接到任务该去哪里"串起来。稳定代码职责见 [代码地图](code-map.md)；
不冻结易变化的生产事实。

## 一图总览：控制面与执行面

```
GitHub Issues（控制面）          PostgreSQL + 工件（执行面）
─────────────────────           ─────────────────────────────
研究提案 / 冻结计划 / 批准       db        artifacts
      │                          │            │
      │  轮询+标签收敛            ▼            │
      └──────────────► research-worker ──► quant_research/runner（正式研究）
                              │                │
                              ▼                ▼
                         research_orchestration  canonical 工件 + 账本
                              │                │
                              ▼                ▼
                         research_publication ──► 评价 → 结论 → 发布读回
```

- **控制面 = GitHub Issues**：提案、冻结计划、批准、可读进度、最终结论。
- **执行面 = 服务器**：队列、数据、计算、工件、评价与发布。
- 数据库、工件、Issue、API 与前端读回同一评价版本与指纹后才算发布完成。
- 运行成功 ≠ 研究通过；`status=ok` 只能表示源执行成功。

## 容器拓扑

`docker-compose.yml` 定义五个服务（另见 [架构 README](README.md)）：

| 容器 | 入口 | 职责 |
| --- | --- | --- |
| `db` | postgres:16-alpine | 市场数据、任务与结构化研究事实 |
| `api` | `backend/app/main.py` | 数据/研究/运维 API，入队持久任务，不在请求进程执行长同步 |
| `worker` | `backend/app/sync_worker.py` | 持久化数据同步任务（租约、心跳、退避） |
| `research-worker` | `backend/app/research_worker.py` | 正式研究队列，profile `research-automation`，需工程 Issue + 人工批准门 |
| `frontend` | React + Vite 构建、Nginx | 只读研究驾驶舱与数据界面 |

## 正式研究生命周期

每步标注【负责模块】与【必读文档】。完整口径见
[策略画像与评价规范](../research/contracts/strategy-evaluation-standard.md) 与
[量化研究可信合同](../research/contracts/quant-foundation-trust-contract.md)。

1. **提案与冻结计划**
   - 研究提案创建、排序，但不得据此启动正式研究。
   - 冻结计划是 `research-plan/v3` 的不可变 JSON（`<!-- research-plan-json:start -->`
     标记），含假设、规则、范围、基准、成本、门禁、试验预算、停止条件、参数邻域。
   - 【`research_plan.py`：解析与校验；`configs/research/`：入口清单】
   - 【文档：`../operations/research-orchestrator.md`、ADR 0001】

2. **人工批准**
   - 只有 GitHub 用户 `Jettlin927` 对冻结计划发布 `批准研究 <plan_sha256>` 才有效；
     标签不是授权来源。参数/门槛变化形成新哈希，必须重新批准。
   - 【`research_orchestration.py` 的 `AUTHORIZED_RESEARCH_APPROVER` 与批准校验】
   - 【ADR 0001：正式研究需要人工批准】

3. **编排与队列**
   - research-worker 轮询 GitHub Issue，把已批准计划放入租约队列；work item 状态机
     为 排队/已租用/运行中/成功/失败/中断；编排状态独立（待批准→…→已发布）。
   - 【`research_worker.py`（轮询、信号处理）；`research_orchestration.py`（队列、
     状态机、批准校验）】
   - 【文档：`../operations/research-orchestrator.md`；ADR 0002（运行≠评价）】

4. **运行与证据**
   - 只执行已合并、CI 通过并静态登记的策略代码（`quant_research/strategy_registry.py`）；
     自动优化限制在冻结搜索空间内。
   - 运行产物为 canonical 工件与账本（`outputs/research-runs/`，被 Git 忽略）。
   - 【`quant_research/`：`runner.py`（执行）、`snapshot.py`/`universe.py`/
     `dataset.py`（point-in-time 输入）、`execution.py`/`portfolio.py`/
     `metrics.py`/`risk.py`（下一交易日执行与统计）、`artifacts.py`/
     `manifest.py`/`repository.py`（工件）】
   - 【文档：`../operations/research-publication.md`；ADR 0004（发布不可变）】

5. **评价与结论**
   - 依据冻结计划的评价策略（DSR/PBO、walk-forward、成本压力）做结构化判断；
     结论只允许 研究通过 / 有条件候选 / 证据不足 / 受阻 / 不通过。
   - 【`quant_research/evaluation.py`、`research_evaluation.py`；`run_config.py`
     （评价/通过策略）】
   - 【ADR 0002；`../research/contracts/`】

6. **发布与读回**
   - 数据库、工件、Issue、API 与前端读回同一评价版本与指纹后才算发布完成；
     已发布研究不可原地覆盖，修正必须创建替代版本。
   - 【`research_publication.py`（可恢复发布步骤、前端同源读回验证）；
     `research_catalog.py`（只读目录投影）；`research_analytics.py`（指标投影）】
   - 【文档：`../operations/research-publication.md`；ADR 0004】

7. **监测与后续提案**
   - 新增数据只形成观察（`market_observation/`、`official_evidence/`），不自动改
     结论；需要改变结论时必须产生新的研究提案。
   - 历史迁移只映射已有运行/证据，不构成批准、不补跑（`research_history_migration.py`、
     `historical_publication_issues.py`、`configs/research/`）。【ADR 0006】

## 市场数据同步流

```
前端/API 入队 ──► worker 租约 ──► 摄取 ──► PostgreSQL ──► 质量门
```

- API 入队：`main.py` 的 `/api/sync-jobs`（202 异步）与 `/api/tushare/sync-*`。
- worker 执行：`sync_worker.py`（租约、心跳、退避、恢复）。
- 摄取实现：`market_data_ingestion.py`（A 股：行情/基本面/列表/交易日历/复权/
  指数/基金/行业）与 `tushare_client.py`。
- 质量门：`data_quality/`（研究范围质量、canonical 输入快照、质量运行），
  API 暴露 `/api/data-quality/runs` 与 `/api/research/readiness`。
- 美股实验（非正式研究资格）：`us_experiment.py` + 定时脚本
  （`scripts/ops/`），`researchEligible=false` 固定。

## 个人工作台与市场观察（与正式研究隔离）

- 真实美股持仓、观察规则、AI 分析走 `personal_workspace/` +
  `personal_analysis_worker.py`（唯一可读 DeepSeek secret 的进程，
  `PERSONAL_ANALYSIS_MODE=agent` 启用 tool-use agent 运行时）。
- 市场观察（Alpaca 来源、健康度、追加式授权）走 `market_observation/`；
  官方证据资格走 `official_evidence/`。
- 【ADR 0007（个人工作台私密隔离）、ADR 0009（个人 AI 分析 agent 工具）】

## 接到任务时去哪找

| 任务类型 | 先读 | 主要代码入口 |
| --- | --- | --- |
| 策略研究/回测/评价 | [策略评价规范](../research/contracts/strategy-evaluation-standard.md) + 本页第 4-6 步 | `quant_research/`、`scripts/research/` |
| 研究编排/Issue 自动化 | `../operations/research-orchestrator.md` + `../agents/issue-tracker.md` | `research_plan.py`、`research_orchestration.py`、`github_research.py`、`research_worker.py` |
| 数据同步/质量 | [A 股数据](../data/a-share/)、[美股边界](../data/us/) | `market_data_ingestion.py`、`sync_worker.py`、`data_quality/` |
| 发布/读回一致性 | `../operations/research-publication.md` | `research_publication.py`、`research_catalog.py`、`research_analytics.py` |
| 前端页面/视觉 | `../../.codex/skills/frontend-design/SKILL.md` | `frontend/src/`（视图表见 code-map） |
| 生产部署/迁移/凭据 | `../operations/production-deployment-and-home-access.md` | `scripts/ops/` |
| schema/migration | `../../backend/migrations/` + 本仓库 AGENTS.md 生产门禁 | `models.py` + Alembic revision |
| 验证 | `../agents/validation.md` | `../../backend/tests/`、`../../scripts/ops/test_*.sh` |

易变化的事实（表行数、磁盘、部署状态、运行 ID、Issue 状态）必须现场核验，不能
引用本文档或历史记录代替。
