# 063-portfolio-cross-section-strength-risk8-cost-2x 组合回测假设

- 时间：2026-05-30 20:20 +0800
- 策略：MA趋势跟随-高弹性止盈-移除MACD过滤 (`trend-follow-maximum-profit-no-macd`)
- 前置证据：`060-single-tail-risk8-synced-source`：targetMet=False，tailRiskMet=False
- 组合假设：若入池过滤、买入排序、仓位和行业集中度约束能把单票信号转换为共享资金组合，则组合收益/回撤/盈亏比应比逐标的尾部更稳定。
- 组合参数：最大持仓 3，单票建仓上限 14%，单票观测暴露上限 15%，行业上限 40%，每周最多新开仓 2。
- 买入排序：cross_section_strength。
- 活跃标的上限：None，老标的加分 0.0。
- 信号阈值：min=None，max=None。
- 成本压力：2.0x。
- 失败节流：启用，标的冷却 20 天，行业周亏损阈值 2。
- 市场状态：启用，以前一交易日市场宽度决定是否允许新开仓。
- 注意：目标完成门槛以组合收益、组合盈亏比、组合回撤、交易次数、集中度和成交标的尾部亏损/回撤为准；逐标的源审计和尾部盈亏比样本充分性作为诊断项单独报告。
