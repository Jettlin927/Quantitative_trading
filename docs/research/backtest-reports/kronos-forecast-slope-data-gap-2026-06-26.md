# Kronos 预测斜率策略数据缺口报告

## 结论

`12_kronos_forecast_slope` 目前不能生成真实历史回测结果，因为仓库和已知本地相关仓库中没有历史 Kronos 预测输出文件。

这不是策略通过或淘汰结论，只是数据可用性结论。不能用合成预测、单次预测或事后价格路径替代历史预测回测。

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

未找到可用于滚动历史回测的 `*_forecast_stats.csv` 或同结构历史预测 DataFrame 落盘文件。

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
- 历史预测回测：缺数据，未完成。

因此最终验收第 2 项仍需用户提供或重新生成历史 Kronos 预测数据后才能关闭。
