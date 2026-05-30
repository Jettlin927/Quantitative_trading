# 016-portfolio-market-breadth-filter 组合回测复盘

- 开始时间：2026-05-30 04:08 +0800
- 结束时间：2026-05-30 04:09 +0800
- 策略：MA趋势跟随-高弹性止盈-移除MACD过滤 (`trend-follow-maximum-profit-no-macd`)
- 结论：未达标

## 组合指标

- 总收益：-20.21%
- 年化收益：-7.25%
- 最大回撤：-22.72%
- 盈亏比：4.00:1
- Profit factor：0.79:1
- 胜率：14.18%
- 交易动作数：318
- 完成交易数：141
- 最大同时持仓：5
- 最大单票集中度：21.97%
- 最大行业集中度：39.38%
- 市场 Risk-On 天数：178
- 市场 Risk-Off 天数：548
- 市场过滤拦截天数：434
- 市场过滤拦截信号数：138207

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
