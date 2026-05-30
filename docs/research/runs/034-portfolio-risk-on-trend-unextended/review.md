# 034-portfolio-risk-on-trend-unextended 组合回测复盘

- 开始时间：2026-05-30 04:36 +0800
- 结束时间：2026-05-30 04:37 +0800
- 策略：Risk-On趋势跟随-未过热 (`trend-follow-market-breadth-unextended`)
- 结论：未达标

## 组合指标

- 总收益：1.04%
- 年化收益：0.35%
- 最大回撤：-6.54%
- 盈亏比：4.00:1
- Profit factor：1.20:1
- 胜率：20.55%
- 交易动作数：180
- 完成交易数：73
- 最大同时持仓：3
- 最大单票集中度：15.74%
- 最大行业集中度：29.47%
- 市场 Risk-On 天数：178
- 市场 Risk-Off 天数：548
- 市场过滤拦截天数：425
- 市场过滤拦截信号数：61570

## 门槛

- singleSymbolTailRiskMet：False
- totalReturnMet：False
- profitLossRatioMet：True
- maxDrawdownMet：True
- minimumTradesMet：True
- singleConcentrationMet：False
- industryConcentrationMet：True

## 解释

本轮使用共享资金、买入排序、最大持仓数、单票上限和行业上限，把逐标的信号转换为组合诊断。若任一门槛未过，策略仍不能视为可执行落地组合策略。
