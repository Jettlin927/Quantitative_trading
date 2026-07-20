# 研究评价一致发布与恢复

研究发布只接受已经终态的研究运行和显式的五类中文结论。运行成功、Worker 成功或旧脚本 `status=ok` 都不能自行晋升为“研究通过”。发布服务复用既有评价、证据和发布表；唯一新增的 `0010_research_issue_mapping` schema 只保存历史导入研究与独立 GitHub Issue 的不可变一对一映射。

## 一致发布顺序

1. 在同一正式研究下冻结评价、全部终态运行关联与证据引用；计算不可变评价指纹。不得省略失败或中断尝试而只发布胜出运行。
2. 为本次尝试创建 `pending` 发布记录。相同评价的失败重试复用评价与工件，只新增后继发布尝试。
3. 成功运行必须通过既有 `validate_research_archive` 完整校验，并与数据库中的策略、代码提交、配置、数据快照、环境、复现键和结果指纹逐项一致。失败或中断运行只冻结数据库中的终态、阶段、错误、时间和运行身份；其可能残留的部分 manifest 或 checkpoint 不作为可信证据。随后从冻结身份确定性生成 `summary.json`、原始 `report.html` 与发布 `manifest.json`。这些文件原子写入评价 ID 对应目录；目标已存在时只校验，不覆盖。
4. 用评价指纹作为隐藏标记，创建中文 GitHub 终态评论。相同标记只接受完全相同的正文，禁止 PATCH 已发布评论。
5. 先以 `pending` 从数据库/API 和 `RESEARCH_READBACK_BASE_URL` 指向的前端同源入口读回；一致后提交数据库发布版本，再从同一前端入口读回 `published`。评价版本、结论、运行指纹、工件 URL 任一不一致都停止。
6. 只有 `published` 已经持久化并经前端读回，才把正式研究与编排状态收敛为已发布；全部数据库事务成功后，用一次 GitHub Issue 更新同时关闭研究 Issue 并设置 `研究:已发布` 标签。GitHub PATCH 是成功路径最后一个有副作用动作，关闭之后不再追加数据库尾写。动态状态页只承诺数据库、API 与前端已经读回，并明确说明 GitHub 终态仍由 Worker 持续核验和补偿。

发布记录已经变为 `published`、但核心状态或 GitHub 原子收敛失败时，不改写该不可变版本；正式研究与编排状态回到可审计的评价/阻塞态，并记录 `research_publication_failed`。Worker 优先恢复最新但尚未收敛的 published 版本，同时持续巡检当前生效版本，即使后面已有 pending/failed 更正也不会漏掉旧生效结论。巡检会按数据库事实重建并逐字节核对三份发布工件，再核对冻结评论 ID、正文、Issue 标题、状态和标签；任何漂移都会使动态状态页退出“当前生效”。可重试故障按最新失败事件退避；工件篡改、身份冲突、非法 URL 和读回内容漂移属于确定性失败，停止自动重试。评论仍存在时可恢复原正文后显式重放；评论已删除且 GitHub 无法恢复原 ID 时，只能创建前向更正版本和新评论，旧数据库身份继续保留。PostgreSQL advisory lock 覆盖整段外部发布流程，即使同时启动多个 Worker，也只有一个流程可创建评论和收敛状态。Worker 只消费已经显式冻结的评价，绝不根据运行成功或失败自动猜测五类结论。

## 显式评价入口

成功运行进入 `evaluating` 后，由评价者提交冻结 JSON 合同；这是创建新结构化评价的明确运行入口。合同必须列出该正式研究的全部运行，并显式给出五类中文结论、证据、限制和后续建议：

```json
{
  "schemaVersion": "research-evaluation-request/v1",
  "formalResearchId": "正式研究 UUID",
  "conclusion": "证据不足",
  "runIds": ["运行 UUID"],
  "supportingEvidence": [{"statement": "已完成 canonical 归档校验"}],
  "opposingEvidence": [],
  "missingEvidence": [{"statement": "缺少更长 OOS"}],
  "limitations": [{"statement": "仅用于离线量化研究"}],
  "followUpRecommendations": [{"statement": "按新计划补充 OOS"}],
  "evidenceRefs": [{
    "kind": "report",
    "uri": "artifacts://运行 UUID/manifest.json",
    "runId": "运行 UUID",
    "sha256": "64 位小写 SHA-256",
    "metadata": {"mediaType": "application/json"}
  }],
  "supersedesEvaluationId": null
}
```

```bash
python scripts/research/publish_research_evaluation.py \
  --contract /受控路径/evaluation.json
```

该 CLI 只冻结评价和 `pending` 发布记录，不访问 GitHub、不生成工件，也不与 Worker 竞争外部发布；research-worker 是唯一外部发布者。入口不从运行成功或失败推断结论。合同遗漏任一已关联运行时拒绝发布；前序失败或中断尝试必须保留，但只要至少存在一个成功运行，仍可按完整证据判断是否 `研究通过`。

五类结论都有最低内容合同：`有条件候选` 必须有支持证据、明确限制和后续建议；`证据不足` 必须有尚缺证据、限制和后续建议；`受阻` 必须有阻塞导致的缺失证据、阻塞事实和后续建议；`不通过` 必须有反对证据、限制和后续建议；`研究通过` 也必须保留限制和后续建议。原生研究存在成功运行时，还必须至少引用一项绑定该运行且可由 manifest 校验的 canonical 证据。

`研究通过` 另须在 `supportingEvidence` 中显式列出十项硬门禁：`identity_and_hypothesis`、`point_in_time_universe`、`execution_semantics`、`net_cost_and_liquidity`、`matched_benchmark`、`test_oos`、`market_regime`、`trial_history`、`risk_and_capacity`、`reproducibility`。每项都必须为 `status: "passed"`，并用 `evidenceRefs` 连接已声明的 canonical 证据；同时必须包含 input snapshot、代码、环境、参数、账本和统计六类输入证据。账本必须真实引用 `rebalance_requests.csv.gz`、`rebalance_executions.csv.gz` 和 `positions.csv.gz`；统计必须同时引用全周期 `metrics.json`、冻结测试段 `oos_metrics.json`、匹配基准 `benchmark_nav.csv.gz`、两份 walk-forward 窗口/指标 CSV、风险暴露与风险贡献 CSV。`oos_metrics.json` 中的市场环境覆盖从实际可用单元反推，不能相信自报数组；参数邻域与容量必须为 `complete`，并用策略哈希、每个邻域配置哈希、预期资金规模、ADV 参数、参与率与冲击阈值逐项绑定 `research-plan/v3`。Runner 会重跑每个冻结邻域配置，并用 OOS 调仓请求和请求日前成交额计算容量；发布器重新闭合汇总、阈值和风险贡献，不能接受事后另填的“通过”字段。多次试验的 DSR/PBO 必须是带有限概率、冻结试验数和组合身份的结构化对象，`null` 或 `not_available` 不构成证据。不能只给同一 manifest 贴上不同类型标签。发布 HTML 报告是上述冻结证据和机器摘要的确定性下游输出，不用它自证“研究通过”。所有 `artifacts://<run_id>/<path>` 引用都必须显式填写本评价中的 `runId`，URI、运行、路径与 SHA-256 会逐项对照 canonical manifest 和实际文件，不能用仓库链接、空运行或格式正确的占位哈希绕过。

GitHub 终态评论只发布有界中文摘要、各类证据数量和稳定链接；完整证据文字、运行身份与结果指纹保留在不可变机器摘要和 HTML 报告中，避免超长合同触发 GitHub 评论上限后形成无法更正的冻结版本。评论只写入一个稳定的“后续研究提案”只读入口，不把可变的提案标题、状态或转化计划 ID 嵌入 canonical 工件。因此发布后新增或更新提案不会改变已冻结的摘要、报告和评论字节。

## 冻结测试段与 canonical 工件

正式研究计划采用 `research-plan/v3`。`sampleSplits` 必须严格冻结且不重叠地覆盖 `train`、`validation` 和 `test_oos`；`runConfig.validationPolicy` 必须启用非 `none` 的 walk-forward；`reportContract.evaluationPolicy` 固定市场环境回看窗口、阈值与成本压力倍数，`reportContract.researchPassPolicy` 固定参数邻域和容量假设。任一边界、策略、邻域或容量阈值变化都会改变计划哈希并使既有批准失效。

运行归档采用 `research-run-artifact/v5`，其中 `oos_metrics.json` 为 `research-oos-metrics/v2`。`metrics.json` 继续保存完整研究区间统计；OOS 工件只从冻结 `test_oos` 计算，并包含显式期初净值、匹配基准、逐年统计、只使用前一交易日基准信息分类的市场环境矩阵、样本/warmup/开市日/调仓/请求/成交/阻塞/独立交易计数、期末与平均 gross/net 暴露、结构化 walk-forward、参数邻域重跑、成本压力、容量观察和由风险 CSV 汇总的组合波动与总风险贡献。`walk_forward_windows.csv.gz` 与 `walk_forward_metrics.csv.gz` 冻结逐窗口边界和 OOS 指标并原生展示在报告中。`benchmark_nav.csv.gz` 保存与策略完全对齐的主基准净值路径，首个研究日收益从 `pre_close` 或上一交易日收盘计算，不能从首个收盘重新归一化。

发布器只允许 artifact schema v5 及以上的运行晋升为 `研究通过`，并逐项核对 OOS 边界、评价策略、研究通过策略、非空 walk-forward、参数配置身份、容量观察、风险贡献和 canonical 工件。报告中的 OOS 指标、市场环境、稳健性、风险和成交章节只读取 `oos_metrics.json`；全区间 `metrics.json` 不能代替 OOS 证据。

## 更正与旧链接

已发布评价不可原地修改。更正必须创建新评价版本，并令 `supersedes_evaluation_id` 指向当前最新已发布评价；新发布同时以 `supersedes_publication_id` 指向上一发布尝试。旧目录、原始 HTML 和 manifest 哈希不修改，`/artifacts/report.html` 始终返回原始字节；`/report` 是读取当前发布投影的动态状态页，可显示“当前生效”“尚未完成一致发布”或“已被替代”，并链接回不可变原始报告。只读投影的 `superseded_by_evaluation_id` 不能反向写入 canonical HTML。

## 历史研究的一对一 Issue

历史导入的四项当前研究分别使用独立 Issue：[#37](https://github.com/Jettlin927/Quantitative_trading/issues/37)、[#38](https://github.com/Jettlin927/Quantitative_trading/issues/38)、[#39](https://github.com/Jettlin927/Quantitative_trading/issues/39) 与 [#40](https://github.com/Jettlin927/Quantitative_trading/issues/40)。每张票都必须保持 OPEN，并同时带 `类型:策略研究` 和 `来源:历史导入`；它们只承接既有“不通过”评价的结构化发布，不代表新研究批准或新运行。

冻结映射清单位于 `configs/research/historical_publication_issues_v1.json`。生产登记前必须先由用户单独批准并执行生产 Alembic upgrade，随后逐项运行：

```bash
python scripts/research/register_historical_issue_mapping.py \
  --strategy-id <冻结策略 ID> \
  --issue-number <对应 Issue 编号>
```

工具会先读取冻结清单，拒绝任何策略与 Issue 的交叉换绑，再读回精确仓库中 Issue 的编号、标题、类型、OPEN 状态和标签，最后才在数据库中写入不可变映射；重复登记同一映射幂等，换绑、复用或改名都会失败。发布 Worker 也会再次核对冻结 pair 和 GitHub Issue；pending 发布只接受 OPEN，已发布记录允许 OPEN/CLOSED 进入补偿，但仍严格核对标题与两枚历史标签。历史导入与原生评价共用五类结论的最低内容合同，不允许“不通过”省略后续建议。本阶段只准备 migration、清单和登记工具，不执行生产 migration 或映射写入。

## 配置与门禁

- `RESEARCH_PUBLIC_BASE_URL`：写入 GitHub 评论的前端根地址。第一阶段固定使用 `http://127.0.0.1:15173`，用户先建立 `ssh -N -L 15173:127.0.0.1:15173 quant-trading-new` 隧道再打开链接；无需域名、HTTPS 网关或公网端口。代码只额外接受完整 HTTPS 地址，拒绝非 loopback HTTP。
- `RESEARCH_READBACK_BASE_URL`：研究 Worker 实际读回的前端同源入口。Compose 默认使用 `http://frontend:5173`，以验证前端代理和 API/工件路由；不能改成绕过前端的数据库内部调用。
- `RESEARCH_PUBLICATION_RETRY_SECONDS`：发布失败后再次领取同一评价的最短间隔，默认 300 秒，防止外部故障时形成紧密重试。
- `RESEARCH_ARTIFACT_ROOT`：API 与研究 Worker 共享的持久工件根目录。

既有 pending/published 评价遇到非法 public/readback URL 时也会写入失败审计，不会在 Worker 循环外静默抛错。

本合同不授权生产部署、生产 Alembic upgrade、正式研究启动或历史迁移 apply。第一阶段也不购买或申请域名、不部署 Cloudflare/Tailscale、不开放公网端口；上述边界发生变化时仍需用户重新决定。
