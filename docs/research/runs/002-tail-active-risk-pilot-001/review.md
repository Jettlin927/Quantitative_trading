# 尾盘活跃网格复盘

- Run: `002-tail-active-risk-pilot-001`
- 覆盖: `2023-01-01` 至 `2026-05-31`
- 参数组合: `9`

## 当前最优

- 参数: `{"tailEntryMinPctChg": 0.025, "tailEntryMaxPctChg": 0.05, "tailMinVolumeRatio": 2.0, "tailMinTurnoverRatePct": 7.0, "tailPriorLimitUpLookback": 15, "entryRiskFilter": {"enabled": true, "maxEntryRangePct": 0.06}}`
- 测试股票: `590`，有交易股票: `92`，完成交易: `109`
- 中位收益: `-0.22%`，平均收益: `-0.11%`，正收益率: `43.48%`
- 平均最大回撤: `-0.41%`，中位盈亏比: `0.00:1`

## 结论

第一阶段信号可行性未通过：当前最优组合仍未取得正的中位收益和平均收益，暂不应推进为组合级候选。
