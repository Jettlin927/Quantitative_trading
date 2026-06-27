# 数据源审计：Tushare 与 yfinance

审计时间：`2026-06-26 19:55 +08:00`

## 结论

- Tushare 数据源当前可用：A 股日线、全市场 daily_basic、单票 fundamentals 烟测均返回 `status=ok`。
- yfinance 数据源当前可用：美股 sample 观察池快照返回 `status=ok ok=4 stale=0`。
- 注意：历史记录中 `market:fundamentals` 全市场同步曾为 `partial`，当前财务指标表覆盖 `2023-06-30` 到 `2026-03-31`，不是五年全市场财务完整库。若后续策略使用财务因子，需要另做全量 fundamentals 补齐与失败股票复核。

## Tushare 本地库覆盖

```text
stock_daily_bars           2021-06-28 -> 2026-05-29   6,155,284 rows
stock_daily_basic          2021-06-28 -> 2026-05-29   6,134,626 rows
stock_financial_indicators 2023-06-30 -> 2026-03-31      31,025 rows
```

## Tushare 同步记录

最近关键记录：

```text
market:daily_basic     ok       2021-06-26 -> 2023-05-31   rows_upserted=2,248,457
market:daily           ok       2021-06-26 -> 2023-05-31   rows_upserted=2,269,115
600703.SH:fundamentals ok       2023-06-01 -> 2026-06-01   daily_basic=724, fina_indicator=12
market:fundamentals    partial  2023-06-01 -> 2026-06-01   stocks=5525, failed_stocks=2934
market:daily_basic     ok       2023-06-01 -> 2026-06-01   rows_upserted=3,886,169
market:daily           ok       2023-06-01 -> 2026-06-01   rows_upserted=3,886,169
stock_basic            ok                                  rows_upserted=5,525
```

## Tushare 活性烟测

单票 fundamentals：

```bash
curl -sS -X POST http://localhost:18000/api/tushare/sync-fundamentals \
  -H 'Content-Type: application/json' \
  -d '{"ts_code":"600703.SH","start_date":"2026-01-01","end_date":"2026-06-01"}'
```

返回：

```json
{"status":"ok","ts_code":"600703.SH","daily_basic_rows":96,"fina_indicator_rows":1}
```

单票日线：

```bash
curl -sS -X POST http://localhost:18000/api/tushare/sync-daily \
  -H 'Content-Type: application/json' \
  -d '{"ts_code":"600703.SH","start_date":"2026-05-01","end_date":"2026-06-01"}'
```

返回：

```json
{"status":"ok","ts_code":"600703.SH","rows_upserted":19}
```

## yfinance 活性烟测

命令：

```bash
.venv/bin/python -m my_quant.us_research.scripts.refresh_us_snapshot
```

返回：

```text
wrote /Users/jettlin/code/Quantitative_trading/my_quant/us_research/data/snapshots/us_snapshot_latest.json
wrote /Users/jettlin/code/Quantitative_trading/my_quant/us_research/data/snapshots/us_snapshot_latest.csv
status=ok ok=4 stale=0
```

当前 sample 标的来自 `my_quant/us_research/config/watchlist_symbols.csv`，不读取真实券商账户或真实持仓。

## 后续要求

1. 若策略只用价格、成交额、估值和 daily_basic，当前 Tushare 数据源可继续使用。
2. 若策略使用财务质量因子，必须先补全 `stock_financial_indicators` 全市场覆盖，并复核历史 `market:fundamentals partial` 的失败股票。
3. yfinance 仅作为美股观察池研究快照来源，不作为券商持仓或真实交易系统。
