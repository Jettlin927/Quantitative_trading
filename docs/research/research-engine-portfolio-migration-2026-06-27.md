# Research Engine Portfolio Migration - 2026-06-27

## 结论

`阶段通过`：本阶段只迁移了纯组合权重逻辑，未移动 cache、result、HTML report 或策略证据目录。

## 目标

把 `my_quant/strategy_research/experiment/strategies.py` 中可复用、无文件副作用的组合权重函数迁入后端边界，作为后续 FastAPI 研究聚合和策略评估 API 的共享实现。

## 已迁移内容

- 新增 `backend/app/research_engine/portfolio.py`。
- 新增后端测试 `backend/tests/test_research_engine_portfolio.py`。
- `my_quant/strategy_research/experiment/strategies.py` 改为兼容层，继续暴露旧 import 路径。

迁移函数：

- `normalize_weights`
- `make_equal_weight`
- `make_risk_parity`
- `make_ram_topn`

## 未迁移内容

- 未移动 `my_quant/strategy_research/data_cache/`。
- 未移动 `my_quant/strategy_research/results/`。
- 未移动 `my_quant/strategy_research/web_report/`。
- 未删除任何历史策略 `flow.md` 或负证据。
- 未改变回测执行语义、交易成本、持仓规则或三段评估门槛。

## 验证命令

```bash
.venv/bin/python -m unittest backend.tests.test_research_engine_portfolio -v
.venv/bin/python -m unittest my_quant.strategy_research.tests.test_experiment_engine -v
.venv/bin/python -m unittest backend.tests.test_research_engine_portfolio backend.tests.test_research_engine_metrics backend.tests.test_api_contracts backend.tests.test_us_research backend.tests.test_strategy_lifecycle backend.tests.test_strategy_evaluation -v
.venv/bin/python -m py_compile backend/app/research_engine/portfolio.py backend/app/research_engine/metrics.py backend/app/main.py my_quant/strategy_research/experiment/strategies.py my_quant/strategy_research/experiment/metrics.py
```

## 验证结果

- 新增 portfolio 后端测试：`4` 个测试通过。
- 旧实验兼容测试：`70` 个测试通过。
- 后端研究/API 相关测试：`15` 个测试通过。
- Python 编译检查通过。

## 后续事项

下一步可继续迁移更纯的研究逻辑，例如报告 manifest 解析或无 IO 的诊断聚合。涉及缓存读取、真实数据、数据库 schema、回测规则语义或历史结果可比口径的迁移，需要单独确认边界。
