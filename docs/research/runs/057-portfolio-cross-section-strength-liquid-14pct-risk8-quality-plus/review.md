# 057-portfolio-cross-section-strength-liquid-14pct-risk8-quality-plus 组合回测复盘

- 开始时间：2026-05-30 05:17 +0800
- 结束时间：2026-05-30 05:17 +0800
- 策略：MA趋势跟随-高弹性止盈-移除MACD过滤 (`trend-follow-maximum-profit-no-macd`)
- 结论：未达标

## 组合指标

- 总收益：0.00%
- 年化收益：0.00%
- 最大回撤：0.00%
- 盈亏比：n/a
- Profit factor：n/a
- 胜率：0.00%
- 交易动作数：0
- 完成交易数：0
- 最大同时持仓：0
- 最大单票集中度：0.00%
- 最大行业集中度：0.00%
- 市场 Risk-On 天数：0
- 市场 Risk-Off 天数：726
- 市场过滤拦截天数：178
- 市场过滤拦截信号数：29267
- 买入风险过滤拦截信号数：6827
- 买入排序：cross_section_strength
- 标的冷却事件：0
- 行业冷却事件：0
- 标的冷却拦截信号：0
- 行业冷却拦截信号：0

## 成交标的尾部审计

- 已成交标的数：0
- 收益后 10 严格审计：未通过
- 尾部亏损/回撤审计：未通过
- 尾部盈亏比证据：未通过
- 尾部最差收益：n/a
- 尾部最深回撤：n/a
- 尾部最低盈亏比：n/a
- 尾部违规：亏损 0，回撤 0，盈亏比 0
- 盈亏比样本：合格 0/0，稀疏 0，合格样本最低盈亏比 n/a

### 成交标的收益前 10

- 无

### 成交标的收益后 10

- 无

## 门槛

- sourceSingleSymbolTailRiskMet：False
- portfolioSymbolTailRiskMet：False
- portfolioSymbolTailLossMet：False
- portfolioSymbolTailRatioEvidenceMet：False
- totalReturnMet：False
- profitLossRatioMet：False
- maxDrawdownMet：True
- minimumTradesMet：False
- singleConcentrationMet：True
- industryConcentrationMet：True

## 解释

本轮使用共享资金、买入排序、最大持仓数、单票上限和行业上限，把逐标的信号转换为组合诊断。若任一门槛未过，策略仍不能视为可执行落地组合策略。
