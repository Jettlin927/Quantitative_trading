# 044-portfolio-cross-section-strength-liquid-14pct-exposure15 组合回测复盘

- 开始时间：2026-05-30 04:49 +0800
- 结束时间：2026-05-30 04:50 +0800
- 策略：MA趋势跟随-高弹性止盈-移除MACD过滤 (`trend-follow-maximum-profit-no-macd`)
- 结论：未达标

## 组合指标

- 总收益：32.68%
- 年化收益：9.89%
- 最大回撤：-9.95%
- 盈亏比：4.00:1
- Profit factor：1.60:1
- 胜率：25.00%
- 交易动作数：316
- 完成交易数：136
- 最大同时持仓：3
- 最大单票集中度：14.93%
- 最大行业集中度：34.01%
- 市场 Risk-On 天数：183
- 市场 Risk-Off 天数：543
- 市场过滤拦截天数：7
- 市场过滤拦截信号数：1227
- 买入排序：cross_section_strength
- 标的冷却事件：85
- 行业冷却事件：7
- 标的冷却拦截信号：617
- 行业冷却拦截信号：854

## 门槛

- singleSymbolTailRiskMet：False
- totalReturnMet：True
- profitLossRatioMet：True
- maxDrawdownMet：True
- minimumTradesMet：True
- singleConcentrationMet：True
- industryConcentrationMet：True

## 解释

本轮使用共享资金、买入排序、最大持仓数、单票上限和行业上限，把逐标的信号转换为组合诊断。若任一门槛未过，策略仍不能视为可执行落地组合策略。
