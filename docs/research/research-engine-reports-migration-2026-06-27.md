# Research Engine Reports Migration - 2026-06-27

## 结论

`阶段通过`：本阶段只迁移报告与 manifest 的纯构造逻辑，未写入、重生成或移动任何历史结果文件。

## 目标

继续把 `my_quant/strategy_research/experiment/` 中可复用研究逻辑迁入 `backend/app/research_engine/`，让后续 FastAPI 聚合 API 可以复用报告 payload 构造能力，同时保留旧实验脚本兼容入口。

## 已迁移内容

- 新增 `backend/app/research_engine/reports.py`。
- 新增 `backend/tests/test_research_engine_reports.py`。
- `my_quant/strategy_research/experiment/reports.py` 保留为兼容入口。

迁移函数：

- `percent`
- `markdown_table`
- `select_best_candidate`
- `build_summary_payload`
- `build_manifest_payload`

## 未迁移内容

- `write_summary`
- `write_manifest`
- 任何会写入 `my_quant/strategy_research/results/` 的逻辑
- 任何 HTML 报告生成脚本
- 任何 cache、result、report 或策略证据文件

## 验证命令

```bash
.venv/bin/python -m unittest backend.tests.test_research_engine_reports -v
.venv/bin/python -m unittest my_quant.strategy_research.tests.test_experiment_engine -v
.venv/bin/python -m unittest backend.tests.test_research_engine_reports backend.tests.test_research_engine_portfolio backend.tests.test_research_engine_metrics backend.tests.test_api_contracts backend.tests.test_us_research backend.tests.test_strategy_lifecycle backend.tests.test_strategy_evaluation -v
.venv/bin/python -m py_compile backend/app/research_engine/reports.py backend/app/research_engine/portfolio.py backend/app/research_engine/metrics.py my_quant/strategy_research/experiment/reports.py my_quant/strategy_research/experiment/strategies.py my_quant/strategy_research/experiment/metrics.py
```

## 验证结果

- 新增 reports 后端测试：`3` 个测试通过。
- 后端研究/API 相关测试：`19` 个测试通过。
- 旧 experiment suite：`70` 个测试通过。
- Python 编译检查通过。

## 后续事项

可以继续迁移无 IO 的诊断聚合逻辑。凡是涉及结果文件写入、HTML 重生成、数据库 schema、真实持仓或回测语义的变更，仍需单独确认。
