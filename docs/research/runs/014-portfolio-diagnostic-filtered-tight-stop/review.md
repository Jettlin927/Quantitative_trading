# 014-portfolio-diagnostic-filtered-tight-stop 组合回测复盘

- 开始时间：2026-05-30 04:02 +0800
- 结束时间：2026-05-30 04:03 +0800
- 策略：MA趋势跟随-高弹性止盈-紧止损 (`trend-follow-tight-stop-maximum-profit`)
- 结论：未达标

## 组合指标

- 总收益：-35.35%
- 年化收益：-13.54%
- 最大回撤：-37.20%
- 盈亏比：6.67:1
- Profit factor：0.83:1
- 胜率：10.67%
- 交易动作数：631
- 完成交易数：300
- 最大同时持仓：4
- 最大单票集中度：21.48%
- 最大行业集中度：39.82%

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
