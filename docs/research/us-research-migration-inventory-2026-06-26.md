# 美股研究资产迁移盘点（2026-06-26）

## 结论

Phase 0 不迁移 `/Users/jettlin/code/投资分析` 的真实数据，只记录未来可复用结构。后续 Phase 1 如需迁移，只迁移非敏感脚本、测试和 sample 配置。

## 可迁移或可参考

- `scripts/finnhub_snapshot.py`
- `scripts/daily_notify_report.py`
- `scripts/generate_prediction_dashboard.py`
- `tests/test_finnhub_snapshot_fast_refresh.py`
- `tests/test_history_metrics.py`
- `watchlist_symbols_2026.csv` 的字段结构

## 需脱敏或 sample 化

- `watchlist_symbols_2026.csv` 的真实观察池内容
- `prediction_ledger_2026.csv`
- HTML 报告的展示结构

## 禁止直接迁移

- `hsbc_current_holdings_2026.csv`
- `hsbc_executed_trades_2026.csv`
- `hsbc_non_executed_orders_2026.csv`
- `.env`、`.env.local`、token、真实账户或券商导出

## 下一步

Phase 1 创建 `my_quant/us_research/`，只使用 sample 持仓和 sample 观察池，脚本复用前先写测试。
