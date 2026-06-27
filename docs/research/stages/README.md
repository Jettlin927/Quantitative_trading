# 阶段推进目录

本目录按阶段保存长期策略目标的推进证据。

当前完整阶段路线见 `../long-term-goal.md`。每个阶段通过后，才创建下一个阶段目录；未通过的阶段保留为证据，不删除、不覆盖。

## 阶段索引

| 阶段 | 状态 | 目录 | 说明 |
| --- | --- | --- | --- |
| `000-goal-system-design` | `completed` | `000-goal-system-design/` | 本次完成长期目标和阶段推进制度设计。 |
| `001-observation-diagnosis` | `retired_legacy` | `001-observation-diagnosis/` | 旧 Risk8 观察诊断阶段；用户决定旧策略退场后停止推进。 |
| `001-research-reset` | `active` | `001-research-reset/` | 当前活跃阶段：从零开始建立新策略假设、数据口径和评估流程。 |
| `002-candidate-repair-30` | `planned` | 通过上一阶段后创建 | 修复观察级候选，阶段目标为年化 `>= 30%`、最大回撤 `< 10%`、盈亏比 `>= 2:1`、滚动窗口至少 `5/7` 通过。 |
| `003-research-qualified-50` | `planned` | 通过上一阶段后创建 | 推进到合格研究级，阶段目标为年化 `>= 50%`、最大回撤 `< 10%`、盈亏比 `>= 2.5:1`、滚动窗口 `7/7` 通过。 |
| `004-high-return-frontier-75` | `planned` | 通过上一阶段后创建 | 接近高收益边界，阶段目标为年化 `>= 75%`、最大回撤 `< 10%`、盈亏比 `>= 3:1`，并通过尾部后 10 审计。 |
| `005-ultimate-research-target-100` | `planned` | 通过上一阶段后创建 | 达成终极研究目标：年化 `>= 100%`、最大回撤 `< 10%`、盈亏比 `>= 3:1`。 |
| `006-paper-trading-readiness` | `planned` | 通过上一阶段后创建 | 不新增收益门槛；把研究结果落地成每日纸面作业、账本、数据质量报告、运行时风控和对账流程。 |
| `007-pre-live-validation` | `planned` | 用户确认后创建 | 实盘前验证准备；只有用户明确确认真实资金边界后才能进入。 |

当前活跃目录为 `001-research-reset/`。旧 `001-observation-diagnosis/` 保留为历史证据，不再作为当前策略推进入口。
