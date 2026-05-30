# 022-portfolio-market-breadth-score-cap-32-position-4x18 组合回测复盘

- 开始时间：2026-05-30 04:16 +0800
- 结束时间：2026-05-30 04:17 +0800
- 策略：MA趋势跟随-高弹性止盈-移除MACD过滤 (`trend-follow-maximum-profit-no-macd`)
- 结论：未达标

## 组合指标

- 总收益：22.08%
- 年化收益：6.88%
- 最大回撤：-14.04%
- 盈亏比：4.00:1
- Profit factor：1.28:1
- 胜率：18.80%
- 交易动作数：279
- 完成交易数：117
- 最大同时持仓：4
- 最大单票集中度：19.18%
- 最大行业集中度：35.79%
- 市场 Risk-On 天数：178
- 市场 Risk-Off 天数：548
- 市场过滤拦截天数：428
- 市场过滤拦截信号数：116968

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
