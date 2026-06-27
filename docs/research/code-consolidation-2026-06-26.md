# 代码收拢记录 2026-06-26

## 目标

把分散在三个 AI 写代码目录里的可复用研究代码收拢到 `/Users/jettlin/code/Quantitative_trading`，同时保留清晰边界：不删除源目录、不覆盖目标仓已有成果、不迁入凭据、虚拟环境、第三方上游仓库或大体积缓存。

## 源目录盘点

| 来源 | 实际路径 | 体量 | 本次判断 |
| --- | --- | ---: | --- |
| xquant `my_quant` | `/Users/jettlin/Documents/xquant-beginner/my_quant` | 63M | 与目标仓 `my_quant/` 高度重叠，只补入目标仓缺失成果，不整包覆盖。 |
| 投资分析 | `/Users/jettlin/code/投资分析` | 56M | 独立美股/主题/持仓研究工作区，含真实持仓 CSV 和本地环境文件；本次只登记，不迁入。 |
| Kronos 预测 | `/Users/jettlin/Documents/kronos-预测` | 1.1G | 包含第三方 `Kronos/` 上游仓和 `.venv/`；只收拢自有包装脚本，不迁入上游仓和环境。 |

## 已收拢到主仓的内容

- `my_quant/strategy_research/experiment/kronos_forecast_slope.py`
  - 来源：`/Users/jettlin/Documents/xquant-beginner/my_quant/strategy_research/experiment/kronos_forecast_slope.py`
  - 用途：把 Kronos 预测统计路径转成研究用 `buy` / `sell` / `hold` 信号。

- `my_quant/strategy_research/tests/test_kronos_forecast_slope.py`
  - 来源：`/Users/jettlin/Documents/xquant-beginner/my_quant/strategy_research/tests/test_kronos_forecast_slope.py`
  - 用途：覆盖正斜率买入、弱正斜率观望、负斜率卖出。

- `my_quant/strategy_research/strategies/12_kronos_forecast_slope/flow.md`
  - 来源：`/Users/jettlin/Documents/xquant-beginner/my_quant/strategy_research/strategies/12_kronos_forecast_slope/flow.md`
  - 用途：记录 Kronos 斜率信号的假设、规则和后续验证要求。

- `my_quant/strategy_research/run_kronos_hk_forecast.py`
  - 来源：`/Users/jettlin/Documents/kronos-预测/scripts/forecast_hk_kronos.py`
  - 改动：改成通过 `--kronos-dir` 或 `KRONOS_DIR` 指向外部 Kronos checkout，默认输出到 `my_quant/strategy_research/web_report/kronos_hk_forecast/`。
  - 边界：不把 `Kronos/` 第三方模型仓库、`.venv/` 或历史预测产物复制进主仓。

- `my_quant/strategy_research/experiment/b1_trend_pullback.py`
  - 改动：`fetch_tushare_index_bars()` 读取缓存前检查缓存最大日期；缓存没有覆盖请求结束日时刷新。
  - 原因：源侧已有回归测试证明半旧指数缓存会造成 B1 盘前日期漂移。

- `my_quant/strategy_research/tests/test_experiment_engine.py`
  - 改动：补入 Tushare 指数半旧缓存刷新回归测试。

## 本次明确不迁入的内容

- `/Users/jettlin/code/投资分析/.env.local`、任何 `.env*`、token、密码或本地凭据。
- `/Users/jettlin/code/投资分析/hsbc_current_holdings_2026.csv`、成交 CSV 和真实账户相关文件。
- `/Users/jettlin/code/投资分析/data/`、`reports/` 下的市场快照和 HTML 产物。
- `/Users/jettlin/Documents/kronos-预测/.venv/`。
- `/Users/jettlin/Documents/kronos-预测/Kronos/` 第三方上游仓库。
- `/Users/jettlin/Documents/xquant-beginner/my_quant/strategy_research/data_cache/`、`external_data_cache/`、`logs/` 和批量 CSV 结果。

## 后续建议

1. `投资分析` 仍建议作为独立工作区保留。若要并入主仓，应另起一次任务，只迁移 `scripts/`、`tests/`、`watchlist_symbols_2026.csv` 的非敏感结构，并把真实持仓/成交数据改成示例或本地忽略文件。
2. Kronos 若要成为长期依赖，优先用外部 checkout、submodule 或明确的安装说明，不建议把上游模型仓库直接复制进本仓。
3. `my_quant` 后续同步要以“逐项合并缺失成果”为准，不要用源目录整包覆盖目标仓，因为目标仓已有小本金主板口径和现实成交约束成果。
