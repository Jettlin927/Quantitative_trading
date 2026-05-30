# 036-portfolio-risk-on-trend-ma60 组合回测复盘

- 开始时间：2026-05-30 04:38 +0800
- 结束时间：2026-05-30 04:39 +0800
- 策略：Risk-On趋势跟随-MA60结构 (`trend-follow-market-breadth-ma60`)
- 结论：未达标

## 组合指标

- 总收益：4.74%
- 年化收益：1.56%
- 最大回撤：-10.69%
- 盈亏比：4.00:1
- Profit factor：1.17:1
- 胜率：18.63%
- 交易动作数：243
- 完成交易数：102
- 最大同时持仓：3
- 最大单票集中度：15.83%
- 最大行业集中度：29.64%
- 市场 Risk-On 天数：178
- 市场 Risk-Off 天数：548
- 市场过滤拦截天数：392
- 市场过滤拦截信号数：51084

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
