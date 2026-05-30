# 029-portfolio-market-breadth-score32-tight-stop-position-3x15 组合回测复盘

- 开始时间：2026-05-30 04:26 +0800
- 结束时间：2026-05-30 04:27 +0800
- 策略：MA趋势跟随-高弹性止盈-紧止损 (`trend-follow-tight-stop-maximum-profit`)
- 结论：未达标

## 组合指标

- 总收益：9.22%
- 年化收益：2.99%
- 最大回撤：-10.34%
- 盈亏比：6.67:1
- Profit factor：1.26:1
- 胜率：14.06%
- 交易动作数：291
- 完成交易数：128
- 最大同时持仓：3
- 最大单票集中度：15.89%
- 最大行业集中度：39.91%
- 市场 Risk-On 天数：178
- 市场 Risk-Off 天数：548
- 市场过滤拦截天数：431
- 市场过滤拦截信号数：120172

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
