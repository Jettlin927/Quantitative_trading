# 策略生命周期索引接入报告（2026-06-27）

## 结论

本次已把“不要物理删除策略证据”从文档规则推进为机器可读索引、后端只读 API 和前端可见状态。

结论标签：`阶段通过`。2026-06-27 用户决定从头开始后，所有旧策略都已退出主策略评估视图；旧 ETF/RAM/Kronos/Risk8/B1 路线只保留为历史证据，不再被误认为当前有效策略。

## 关键修正

当前 worktree 中曾出现旧策略 `flow.md` 的 tracked 删除态：

- `00_baseline_china_permanent`
- `01_universe_diversification`
- `02_risk_parity_permanent`
- `03_ram_topn_switch`
- `04_rebalance_cost_control`
- `05_stoploss_trend_filter`
- `06_parameter_sensitivity`
- `07_oos_walk_forward`
- `08_factor_diagnostics`
- `09_final_candidate_ram_top2`
- `12_kronos_forecast_slope`

本次保留这些 `flow.md`，避免物理删除历史策略入口。当前没有任何旧策略通过生命周期索引进入主视图。

## 新增索引

```text
docs/research/strategy-lifecycle.json
```

当前计数：

| 状态 | 数量 | 含义 |
| --- | ---: | --- |
| `legacy_reset` | 14 | 旧策略全量退场，仅保留历史证据 |

## 新增 API

```text
GET /api/strategy-lifecycle
```

`GET /api/strategy-evaluations` 也已补充：

- `lifecycleStatus`
- `showInPrimaryDashboard`
- `evidenceRetention`

当前策略评估返回：

```text
evaluations []
resetStatus legacy_strategies_removed_from_primary
```

## 前端呈现

前端右栏新增 `策略档案` 面板，展示：

- `0 active`
- `0 frozen`
- `0 archived`
- `14 reset`
- 前几条退场策略名称与状态。
- 策略归档政策路径。

这能把“隐藏旧策略”和“删除旧证据”区分开。

## 修改文件

- `backend/app/strategy_lifecycle.py`
- `backend/app/main.py`
- `backend/tests/test_strategy_lifecycle.py`
- `docs/research/strategy-lifecycle.json`
- `frontend/src/main.jsx`
- `frontend/src/styles.css`
- `my_quant/strategy_research/README.md`

## 验证

已运行：

```bash
.venv/bin/python -m unittest backend.tests.test_strategy_lifecycle backend.tests.test_us_research backend.tests.test_strategy_evaluation backend.tests.test_research_engine_metrics -v
.venv/bin/python -m py_compile backend/app/main.py backend/app/strategy_lifecycle.py backend/tests/test_strategy_lifecycle.py
docker compose run --rm frontend npm run lint
docker compose run --rm frontend npm run build
git diff --check
curl -fsS http://localhost:18000/api/strategy-lifecycle
curl -fsS http://localhost:18000/api/strategy-evaluations
```

验证结果：

- 生命周期、US sample、三段评估和指标测试共 `7` 个测试通过。
- 前端 lint 和 build 通过。
- `git diff --check` 通过。
- `/api/strategy-lifecycle` 返回 `{'total': 14, 'legacy_reset': 14}`。
- `/api/strategy-evaluations` 返回空 `evaluations` 和 `resetStatus=legacy_strategies_removed_from_primary`。
- 前端主标题应显示 `新策略研究台`，右栏 `策略档案` 显示 `14 reset`。

## 后续事项

后续如果用户仍希望“删除无用策略”，建议流程是：

1. 先把策略标成 `archived_negative_evidence`。
2. 确认对应结果、run、HTML、CSV、JSON 已在索引中可检索。
3. 用户明确确认“物理删除这些文件并接受历史证据丢失风险”后，再执行删除。
