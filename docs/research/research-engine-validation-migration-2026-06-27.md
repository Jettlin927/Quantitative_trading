# Research Engine Validation Migration - 2026-06-27

## 结论

`阶段通过`：本阶段只迁移 rolling/anchored walk-forward 窗口生成逻辑，未移动、重生成或删除任何历史回测结果。

## 目标

继续把 `my_quant/strategy_research/experiment/` 中可复用且无文件副作用的研究逻辑迁入后端边界。本次选择 `validation.py` 中的窗口生成逻辑，因为它服务样本内/样本外验证语义，并且不依赖本地 cache、结果文件或 DB。

## 已迁移内容

- 新增 `backend/app/research_engine/validation.py`。
- 新增 `backend/tests/test_research_engine_validation.py`。
- `my_quant/strategy_research/experiment/validation.py` 保留为兼容调用入口。

迁移函数：

- `build_walk_forward_windows`

## 未迁移内容

- `walk_forward_analysis`
- `run_config`
- `StrategyConfig`
- 任何会执行回测、读取行情、写入 CSV 或生成报告的逻辑

这些仍留在 `my_quant/strategy_research/experiment/`，因为它们依赖旧实验区的执行上下文和历史证据路径。

## 验证命令

```bash
.venv/bin/python -m unittest backend.tests.test_research_engine_validation -v
.venv/bin/python -m unittest my_quant.strategy_research.tests.test_experiment_engine.ExperimentEngineTest.test_walk_forward_analysis_returns_rolling_and_anchored_rows -v
.venv/bin/python -m py_compile backend/app/research_engine/validation.py my_quant/strategy_research/experiment/validation.py
```

## 验证结果

- 后端 validation 测试：`2` 个测试通过。
- 旧 walk-forward 分析兼容测试：`1` 个测试通过。
- Python 编译检查通过。

## 后续事项

如果后续要迁移完整 `walk_forward_analysis`，需要先把回测执行函数、策略配置契约和输入数据边界一起设计清楚，不能直接让后端反向依赖旧实验区。
