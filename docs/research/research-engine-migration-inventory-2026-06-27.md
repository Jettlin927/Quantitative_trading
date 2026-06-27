# Research Engine Migration Inventory - 2026-06-27

## 结论

`阶段通过`：当前适合迁入后端边界的无文件副作用通用逻辑已形成 `backend/app/research_engine/` 基础层；剩余 `my_quant/strategy_research/experiment/` 模块要么依赖数据读取/结果写入，要么是具体策略执行脚本，不应在本阶段强行迁移。

## 已迁入后端边界

| 后端模块 | 来源 | 迁移内容 | 旧路径处理 |
| --- | --- | --- | --- |
| `metrics.py` | `experiment/metrics.py` | NAV 指标、回撤、Sharpe、Sortino、Calmar、beta | 旧路径保留兼容函数 |
| `portfolio.py` | `experiment/strategies.py` | 权重归一化、等权、风险平价、RAM Top-N | 旧路径保留兼容函数 |
| `reports.py` | `experiment/reports.py` | Markdown 表格 fallback、候选选择、summary/manifest payload | 写文件函数继续留在旧路径 |
| `validation.py` | `experiment/validation.py` | rolling/anchored walk-forward 窗口生成 | 完整 `walk_forward_analysis` 继续留在旧路径 |

## 本阶段故意不迁移

| 模块 | 保留原因 |
| --- | --- |
| `backtest.py` | 仍依赖旧 `StrategyConfig` 和实验区组合执行语义；迁移会改变回测执行边界。 |
| `config.py` | 包含旧实验路径、资产池、训练/评估窗口常量；直接迁入后端会混淆生产 API 配置与历史实验配置。 |
| `data.py` | 依赖 AkShare 和本地 cache；属于数据抓取/修复层，不是纯后端 API 逻辑。 |
| `pipeline.py` / `satellite_pipeline.py` | 会读取价格、写 CSV、生成 manifest 和报告；属于批处理实验执行入口。 |
| `b1_trend_pullback.py` | 含 Tushare/AkShare 数据读取、真实成交约束、报告写入和 A 股策略细节；需要单独设计后端执行契约。 |
| `satellite.py` / `external_probe.py` / `stoploss_trend_filter.py` | 策略级实验逻辑和报告逻辑混在一起；应先拆纯函数、再考虑迁移。 |
| `factor_diagnostics.py` | 纯度较高但与旧资产池常量耦合；可作为下一轮候选，不影响当前 API 闭环。 |
| `kronos_forecast_slope.py` / `kronos_archive_backtest.py` | Kronos 预测归档研究逻辑，证据来源外部且属于历史实验路线；保留为负证据/专项研究工具。 |
| `no_chase.py` | 包含正式验证报告 HTML/JSON 输出；属于专项策略研究，不纳入当前后端基础层。 |

## 当前后端基础层边界

- 后端可复用层只承接纯计算、纯权重、纯报告 payload、纯窗口生成。
- 文件读取、网络数据源、报告写入、回测执行和策略专项实验继续留在 `my_quant`。
- 旧路径继续可 import，历史证据和脚本不物理删除。
- 新后端 API 应优先从 `backend.app.research_engine` import，避免继续把通用逻辑散落到前端或旧实验脚本。

## 验证证据

```bash
.venv/bin/python -m unittest backend.tests.test_research_engine_validation backend.tests.test_research_engine_reports backend.tests.test_research_engine_portfolio backend.tests.test_research_engine_metrics backend.tests.test_api_contracts backend.tests.test_us_research_db backend.tests.test_us_research backend.tests.test_strategy_lifecycle backend.tests.test_strategy_evaluation -v
.venv/bin/python -m unittest my_quant.strategy_research.tests.test_experiment_engine.ExperimentEngineTest.test_walk_forward_analysis_returns_rolling_and_anchored_rows -v
.venv/bin/python -m py_compile backend/app/research_engine/validation.py backend/app/research_engine/reports.py backend/app/research_engine/portfolio.py backend/app/research_engine/metrics.py my_quant/strategy_research/experiment/validation.py my_quant/strategy_research/experiment/reports.py my_quant/strategy_research/experiment/strategies.py my_quant/strategy_research/experiment/metrics.py backend/app/main.py backend/app/models.py backend/app/us_research.py backend/app/strategy_lifecycle.py backend/app/strategy_evaluation.py
git diff --check
```

结果：后端/API 相关 `22` 个测试通过；旧 walk-forward 兼容测试 `1` 个通过；编译检查和 diff 空白检查通过。
