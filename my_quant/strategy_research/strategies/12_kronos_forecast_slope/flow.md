# 12 Kronos 预测斜率策略

## 目标

把 Kronos 未来价格预测摘要转换成一个可测试、可回测的交易信号。这个策略只负责生成 `buy` / `sell` / `hold` 信号，不自动下单。

## 输入

- `*_forecast_stats.csv` 或同结构 `DataFrame`
- 当前真实收盘价 `last_close`
- 默认使用预测统计列 `median`
- 如果存在 `p10`，用它做下行情景过滤

## 核心规则

1. 取预测 `median` 路径。
2. 计算 `log(median / last_close)`。
3. 对上述序列按预测天数做线性回归，得到日均 log 斜率。
4. 默认买入条件：
   - 日均 log 斜率 `> 0.1%`
   - 20 日预测中位数收益 `>= 3%`
   - 20 日 `p10` 收益不差于 `-3%`
5. 默认卖出条件：
   - 日均 log 斜率 `< -0.1%`
6. 其他情况保持观望。

## 为什么不裸用零斜率

`斜率 > 0 买入，斜率 < 0 卖出` 可以作为最小原型，但太容易被噪声、采样路径偏移和交易成本击穿。当前实现把它收紧为“斜率 + 预测收益安全垫 + 下行分位过滤”，避免轻微信号触发交易。

## 代码位置

- 策略模块：`my_quant/strategy_research/experiment/kronos_forecast_slope.py`
- 测试：`my_quant/strategy_research/tests/test_kronos_forecast_slope.py`

## 后续验证

这个信号进入候选前，至少要完成：

- 滚动历史预测回测，而不是只看单次预测。
- 和中国永久组合比较年化收益、波动、最大回撤和风险调整收益。
- 和现有 B1 盘前预案分开评估，不能把模型预测信号直接混入 B1 规则。
- 明确交易成本、滑点、持仓上限和人工确认流程。

## HK 股票预测数据缺口

缺口报告：`docs/research/backtest-reports/kronos-forecast-slope-data-gap-2026-06-26.md`

当前仓库和已知本地相关仓库中未找到 HK 股票口径的 `*_forecast_stats.csv` 或同结构历史 Kronos 预测文件。因此，本策略仍不能生成 HK 股票滚动历史回测结果。

后续若要做 HK 股票专项验证，必须先提供或重新生成 HK 股票历史预测数据；不得用未来真实价格、合成预测或单次当前预测冒充历史回测。

## 当前归档预测回测

报告：`docs/research/backtest-reports/kronos-archive-backtest-2026-06-26/index.html`

产物：

- `docs/research/backtest-reports/kronos-archive-backtest-2026-06-26/rows.csv`
- `docs/research/backtest-reports/kronos-archive-backtest-2026-06-26/summary.json`

说明：

- 使用外部 Kronos webui 已落盘的 `29` 份预测 JSON。
- 每份 JSON 包含 `prediction_results` 和对应 `actual_data`，不是合成预测。
- 样本是加密期货 `5m` 预测归档，不等价于 HK 股票或 A 股策略验证。
- 结果：方向命中率 `93.10%`，long-only 复利收益 `-0.39%`，大多数信号为 `hold`。

因此本策略目录已有对应信号级回测证据；若要升级为 HK 股票策略证据，仍需另行生成 HK 股票滚动预测归档。

所有结论只用于课程研究和回测学习，不构成投资建议。
