# 策略冻结与归档规则（2026-06-27）

## 结论

策略代码、策略目录和前端入口可以冻结、隐藏或停止维护；已经落盘的回测数据、失败窗口、CSV、JSON、HTML 和 review 文档不应直接删除。

本仓库后续默认采用“数据优先”的策略治理方式：

1. 主界面只展示仍在评估的策略。
2. 不再有研究价值的策略进入归档状态。
3. 负证据继续保留，避免未来重复踩同一个坑。
4. 物理删除只在用户明确确认后执行。

## 状态定义

| 状态 | 含义 | 是否在前端主视图展示 | 是否保留证据 |
| --- | --- | --- | --- |
| `active` | 当前仍在评估或推进的策略 | 是 | 是 |
| `frozen` | 暂停推进，但仍可能复查 | 否 | 是 |
| `archived_negative_evidence` | 已被淘汰，作为负证据保存 | 否 | 是 |
| `legacy_reset` | 用户决定从头开始后退场的旧策略 | 否 | 仅作历史材料 |
| `deleted_only_after_user_confirmation` | 用户明确确认后才物理删除 | 否 | 删除前先备份索引 |

## 什么可以删

- 明确无调用方的临时脚本副本。
- 重复生成且可由 manifest 重新生成的中间缓存。
- 不含唯一证据的旧前端展示壳。
- 用户明确确认要删除的策略目录。

## 什么不应该删

- `docs/research/runs/*/results.json`
- `docs/research/runs/*/review.md`
- `docs/research/backtest-reports/` 下的 HTML、CSV、JSON、README 索引。
- `my_quant/strategy_research/results/` 中无法从当前代码稳定再生成的历史结果。
- 记录失败窗口、尾部后 10、成本压力、样本外失败的负证据。

## 前端规则

前端主策略列表只读后端聚合 API。后端应该默认过滤掉 `frozen` 和 `archived_negative_evidence`，但保留一个“证据档案”入口用于复查。

第一版不需要做删除按钮。若未来加入删除能力，必须显示：

- 删除对象。
- 删除原因。
- 受影响文件列表。
- 是否已有备份。
- 用户二次确认。

## 后端规则

策略状态应来自后端证据索引，而不是前端硬编码。推荐在后续 `strategy_evaluations` 聚合中增加：

```json
{
  "strategyId": "cross-section-strength-risk8",
  "lifecycleStatus": "legacy_reset",
  "showInPrimaryDashboard": false,
  "evidenceRetention": "keep_legacy_evidence"
}
```

2026-06-27 已落地：

- `docs/research/strategy-lifecycle.json`
- `backend/app/strategy_lifecycle.py`
- `GET /api/strategy-lifecycle`
- `GET /api/strategy-evaluations` 中的 `lifecycleStatus`、`showInPrimaryDashboard`、`evidenceRetention`
- 前端右栏 `策略档案` 面板

## 执行口径

当用户说“这些策略没用”时，默认理解为：

- 从主视图移除。
- 停止继续自动推进。
- 保留已有证据和负样本。

只有当用户明确说“物理删除这些文件，并确认可以丢失这些历史证据”时，才进入删除流程。

2026-06-27 用户明确表达“策略可以全部都滚蛋，准备从头开始”后，本仓库把所有旧策略统一标为 `legacy_reset`，前端和后端聚合 API 不再返回任何旧 baseline。
