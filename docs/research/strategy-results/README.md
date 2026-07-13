# 策略结果只读档案

本目录只保存已经完成的研究回测结果，用于前端呈现和复盘。

本目录同时保存旧研究档案和新可信研究底座生成的只读报告。它们不包含回测执行入口，不连接券商，也不产生实盘买卖建议。

当前 B1 结论以 2026-07-13 的公开规则近似复现报告为准：`a-share-b1-trend-pullback-20260713/index.html`。该报告明确没有取得来源完整公式，因此只给出“近似复现不通过”，不能写成严格复制。

2026-06-27 的 B1 标准分阶段回测继续保留为旧档案，不作为当前可信长历史结论。

## 文件

- `manifest.json`：只读结果清单和结论标签。
- `b1_standard_phased_backtest_20260627_latest.json`：分阶段回测汇总。
- `b1_score_weight_scan_20260627_latest.csv`：评分权重扫描结果。
- `a-share-b1-trend-pullback-20260713/index.html`：2026-07-13 可信近似复现 HTML 报告。
- `a-share-b1-trend-pullback-20260713/summary.json`：上述报告的机器可读摘要。
