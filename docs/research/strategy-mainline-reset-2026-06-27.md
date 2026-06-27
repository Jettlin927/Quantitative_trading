# Strategy Mainline Reset - 2026-06-27

## 结论

`阶段通过`：旧策略已全部从当前主线退场。前端和后端聚合 API 不再把 `cross-section-strength-risk8`、B1、RAM、Kronos 或其他历史策略作为 active/frozen/archived 候选展示。

## 用户指令

用户明确表示：

> 策略可以全部都滚蛋。以前的那些已经没有参考价值了，我准备从头开始。

本次按“非物理删除、主线归零”执行：

- 旧策略不再作为候选或 baseline。
- 旧证据文件仍保留，避免误删历史 CSV/JSON/HTML/review。
- 新策略必须从假设、数据口径、失败条件和三段评估开始。

## 已完成

- `docs/research/strategy-lifecycle.json`
  - 版本升级到 `2`。
  - `14` 条旧策略全部标记为 `legacy_reset`。
  - `primaryDashboardStrategies=[]`。

- `backend/app/main.py`
  - `/api/research/dashboard` 返回 `baseline=null`。
  - `/api/strategy-evaluations` 返回空 `evaluations` 和 `resetStatus=legacy_strategies_removed_from_primary`。
  - 直接请求 `/api/strategies/executable/cross-section-strength-risk8` 返回 `410`。

- `frontend/src/main.jsx`
  - 主标题回退为 `新策略研究台`。
  - 旧策略退场文案替代旧 Risk8 描述。
  - 策略档案显示 `legacy_reset` 计数。

- 阶段文档
  - `docs/research/research-runs.json` 当前主线改为 `null`。
  - 活跃阶段切换为 `001-research-reset`。
  - 新增 `docs/research/stages/001-research-reset/README.md`。
  - 旧 `001-observation-diagnosis` 标为 `retired_legacy`。

## 验证命令

```bash
.venv/bin/python -m unittest backend.tests.test_api_contracts backend.tests.test_strategy_lifecycle backend.tests.test_strategy_evaluation -v
.venv/bin/python -m py_compile backend/app/main.py backend/app/strategy_lifecycle.py backend/tests/test_api_contracts.py
docker compose run --rm frontend npm run lint
docker compose run --rm frontend npm run build
```

## 验证结果

- 后端合同和生命周期相关测试：`9` 个测试通过。
- Python 编译检查通过。
- 前端 lint 通过。
- 前端 build 通过。

## 边界

本次没有物理删除：

- `docs/research/runs/`
- `docs/research/backtest-reports/`
- `my_quant/strategy_research/strategies/`
- `my_quant/strategy_research/results/`
- `docs/research/executable-strategy-cross-section-risk8.*`

这些文件只作为历史材料存在，不再进入当前主线。
