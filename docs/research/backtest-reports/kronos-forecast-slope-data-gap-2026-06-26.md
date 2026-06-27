# Kronos 预测斜率策略数据缺口报告

## 结论

本报告记录最初的数据缺口：当前仓库和最初核对的相关仓库中没有 `*_forecast_stats.csv` 或同结构历史 Kronos 预测输出文件。

后续补充核对发现，外部 Kronos checkout 的 `webui/prediction_results/` 下有真实落盘预测 JSON，且每份 JSON 包含 `prediction_results` 和对应 `actual_data`。已基于这些归档生成信号级回测：

- `docs/research/backtest-reports/kronos-archive-backtest-2026-06-26/index.html`
- `docs/research/backtest-reports/kronos-archive-backtest-2026-06-26/rows.csv`
- `docs/research/backtest-reports/kronos-archive-backtest-2026-06-26/summary.json`

因此 `12_kronos_forecast_slope` 已有对应回测证据；但该证据的样本是外部加密期货 5 分钟预测归档，不等价于 HK 股票滚动预测验证。

不能用合成预测、单次预测或事后价格路径替代历史预测回测。

## 已核对范围

- 当前仓库：`/Users/jettlin/code/Quantitative_trading`
- 迁移来源仓库：`/Users/jettlin/Documents/xquant-beginner`
- 美股/主题研究仓库：`/Users/jettlin/code/投资分析`

搜索关键词：

- `*kronos*`
- `*forecast*`
- `*forecast_stats*`

当前只找到：

- `my_quant/strategy_research/experiment/kronos_forecast_slope.py`
- `my_quant/strategy_research/run_kronos_hk_forecast.py`
- `my_quant/strategy_research/tests/test_kronos_forecast_slope.py`
- `my_quant/strategy_research/strategies/12_kronos_forecast_slope/flow.md`

未找到可用于 HK 股票滚动历史回测的 `*_forecast_stats.csv` 或同结构历史预测 DataFrame 落盘文件。

## 最低补齐要求

后续若要关闭该策略目录的“对应回测结果”验收，至少需要一组按时间落盘的预测文件，每条样本必须包含：

- 预测生成日期；
- 标的代码；
- `last_close`；
- 预测天数；
- `median` 路径；
- 可选但推荐的 `p10` 路径；
- 预测生成时可见的真实历史价格窗口；
- 预测之后真实可交易价格，用于评估信号收益。

## 禁止替代方案

- 不得用未来真实价格反推预测路径。
- 不得用单次当前预测冒充历史滚动预测。
- 不得用合成随机预测文件写成真实回测结果。
- 不得把信号单测写成回测通过。

## 当前可验收状态

- 信号函数：已有。
- 单元测试：已有。
- 归档预测信号级回测：已有，使用外部 Kronos webui `prediction_results/*.json`。
- HK 股票滚动历史预测回测：缺数据，未完成。

因此最终验收第 2 项可以按“原策略目录均有对应证据”关闭；若后续目标升级为 HK 股票 Kronos 专项验证，仍需用户提供或重新生成 HK 股票历史预测数据。
