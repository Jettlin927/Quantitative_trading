# 021-portfolio-market-breadth-score-cap-30 组合回测假设

- 时间：2026-05-30 04:15 +0800
- 策略：MA趋势跟随-高弹性止盈-移除MACD过滤 (`trend-follow-maximum-profit-no-macd`)
- 前置证据：`010-filtered-universe-tail-audit`：targetMet=False，tailRiskMet=False
- 组合假设：若入池过滤、买入排序、仓位和行业集中度约束能把单票信号转换为共享资金组合，则组合收益/回撤/盈亏比应比逐标的尾部更稳定。
- 组合参数：最大持仓 5，单票上限 20%，行业上限 40%，每周最多新开仓 2。
- 信号阈值：min=None，max=3.0。
- 市场状态：启用，以前一交易日市场宽度决定是否允许新开仓。
- 注意：如果逐标的收益后 10 审计未通过，本轮只能作为组合诊断，不能判定策略落地完成。
