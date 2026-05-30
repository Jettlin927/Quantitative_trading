# 015-portfolio-diagnostic-filtered-maximum-profit 组合回测复盘

- 开始时间：2026-05-30 04:03 +0800
- 结束时间：2026-05-30 04:04 +0800
- 策略：MA趋势跟随-高弹性止盈-移除MACD过滤 (`trend-follow-maximum-profit-no-macd`)
- 结论：未达标

## 组合指标

- 总收益：-38.07%
- 年化收益：-14.77%
- 最大回撤：-39.41%
- 盈亏比：4.00:1
- Profit factor：0.84:1
- 胜率：15.72%
- 交易动作数：657
- 完成交易数：299
- 最大同时持仓：5
- 最大单票集中度：22.32%
- 最大行业集中度：40.55%

## 门槛

- singleSymbolTailRiskMet：False
- totalReturnMet：False
- profitLossRatioMet：True
- maxDrawdownMet：False
- minimumTradesMet：True
- singleConcentrationMet：False
- industryConcentrationMet：False

## 解释

本轮使用共享资金、买入排序、最大持仓数、单票上限和行业上限，把逐标的信号转换为组合诊断。若任一门槛未过，策略仍不能视为可执行落地组合策略。
