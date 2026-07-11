# 量化研究可信合同黄金夹具

这是一个完全合成、可提交 Git 的最小研究数据集。所有代码都以 `SYN` 开头，价格、成交量、财务指标和事件均为人工构造，不来自 Tushare，不含 token、真实持仓、成交或券商信息。

## 固定范围

- 15 个交易日：`2026-01-05` 至 `2026-01-23`。
- 4 个周末休市日：`2026-01-10/11`、`2026-01-17/18`。
- 2 只合成股票：`SYN001.SZ`、`SYN002.SH`。
- 1 只合成 ETF：`SYNETF.SZ`。
- 1 个合成市场指数：`SYNIDX.SH`。
- 1 个合成行业分类：`SYNIND.SI`，只用于表达历史 universe，不是额外的市场基准。

## 边界事件

| 事件 | 夹具记录 | 预期合同 |
| --- | --- | --- |
| 周末 | `trade_calendars.csv` 的 4 个 `is_open=0` 日 | 周五收盘信号映射到下周一开盘 |
| 全天停牌 | `SYN001.SZ` 在 `2026-01-12` 有停牌事件且无日线 | 该日行情缺口有可解释事件；开盘不可成交 |
| 开盘涨停 | `SYN001.SZ` 在 `2026-01-14` 的 `open=up_limit=12.32` | 不可买入 |
| 开盘跌停 | `SYN002.SH` 在 `2026-01-15` 的 `open=down_limit=18.90` | 不可卖出 |
| 复权因子跳变 | `SYN001.SZ` 在 `2026-01-16` 从 `1.0` 变为 `1.2` | 追加该未来因子不应改变 `2026-01-15` 及之前的因果前缀 |
| 同日公告 | `SYN001.SZ` 在周五 `2026-01-09` 只有 `ann_date` | 当日不可见，最早于 `2026-01-12` 可见 |
| 退市边界 | `SYN002.SH` 的 `delist_date=2026-01-20` | 当日包含在资格内，`2026-01-21` 起排除，且无后续日线 |

## 文件与排序

| 文件 | 稳定排序键 |
| --- | --- |
| `trade_calendars.csv` | `exchange, cal_date` |
| `stock_listings.csv` | `ts_code` |
| `stock_daily_bars.csv` | `ts_code, trade_date` |
| `stock_adjust_factors.csv` | `ts_code, trade_date` |
| `stock_limit_prices.csv` | `ts_code, trade_date` |
| `stock_suspend_events.csv` | `ts_code, trade_date, suspend_type, suspend_timing` |
| `stock_financial_indicators.csv` | `ts_code, end_date, ann_date` |
| `industry_members.csv` | `index_code, con_code, in_date` |
| `funds.csv` | `ts_code` |
| `fund_daily_bars.csv` | `ts_code, trade_date` |
| `fund_adjust_factors.csv` | `ts_code, trade_date` |
| `indices.csv` | `ts_code` |
| `index_daily_bars.csv` | `ts_code, trade_date` |
| `target_weights.csv` | `signal_date, ts_code` |
| `expected_fundamental_availability.csv` | `ts_code, end_date, ann_date` |
| `expected_execution_dates.csv` | `signal_date, ts_code` |
| `expected_nav.csv` | `trade_date` |
| `expected_metrics.json` | JSON 键字典序 |

日期一律使用 `YYYY-MM-DD`，数字使用 `.` 作小数点。测试不读本机时区，也不把文件修改时间纳入预期结果。

## 固定基线

`target_weights.csv` 表达一个无参数搜索的管线验收基线：`2026-01-09` 收盘后产生 `SYNETF.SZ=100%` 的目标权重，零成本下于 `2026-01-12` 开盘执行。`expected_nav.csv` 和 `expected_metrics.json` 是该固定输入的黄金产物，只验证管线语义，不代表策略收益主张或投资建议。

Phase 0 曾用两个预期失败锁定区间末复权锚定和公告日同日可见问题。Phase 2 实现因果复权和 `available_from` 后，这两项已升级为永久正常断言，任何回归都会直接使测试失败。
