# 040-portfolio-risk-on-trend-liquid 组合回测复盘

- 开始时间：2026-05-30 04:41 +0800
- 结束时间：2026-05-30 04:41 +0800
- 策略：MA趋势跟随-高弹性止盈-移除MACD过滤 (`trend-follow-maximum-profit-no-macd`)
- 结论：未达标

## 组合指标

- 总收益：0.90%
- 年化收益：0.30%
- 最大回撤：-13.96%
- 盈亏比：4.00:1
- Profit factor：1.02:1
- 胜率：16.13%
- 交易动作数：221
- 完成交易数：93
- 最大同时持仓：3
- 最大单票集中度：15.94%
- 最大行业集中度：29.57%
- 市场 Risk-On 天数：183
- 市场 Risk-Off 天数：543
- 市场过滤拦截天数：5
- 市场过滤拦截信号数：1041

## 门槛

- singleSymbolTailRiskMet：False
- totalReturnMet：False
- profitLossRatioMet：True
- maxDrawdownMet：False
- minimumTradesMet：True
- singleConcentrationMet：False
- industryConcentrationMet：True

## 解释

本轮使用共享资金、买入排序、最大持仓数、单票上限和行业上限，把逐标的信号转换为组合诊断。若任一门槛未过，策略仍不能视为可执行落地组合策略。
