# 032-portfolio-risk-on-pullback-confirm 组合回测复盘

- 开始时间：2026-05-30 04:33 +0800
- 结束时间：2026-05-30 04:34 +0800
- 策略：Risk-On趋势回踩确认 (`trend-pullback-confirm-market-breadth`)
- 结论：未达标

## 组合指标

- 总收益：3.53%
- 年化收益：1.16%
- 最大回撤：-11.77%
- 盈亏比：4.00:1
- Profit factor：1.22:1
- 胜率：19.72%
- 交易动作数：169
- 完成交易数：71
- 最大同时持仓：3
- 最大单票集中度：15.76%
- 最大行业集中度：30.24%
- 市场 Risk-On 天数：178
- 市场 Risk-Off 天数：548
- 市场过滤拦截天数：416
- 市场过滤拦截信号数：59339

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
