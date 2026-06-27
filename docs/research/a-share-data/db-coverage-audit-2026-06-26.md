# Docker DB 五年 A 股覆盖审计（2026-06-26）

## 结论

本次只读审计确认：Docker PostgreSQL 中的 A 股日线表和 daily_basic 表已覆盖过去 5 年主窗口。

- 审计时间：`2026-06-26 19:36 +08:00`
- DB 容器：`quant_trading_db`
- API 容器：`quant_trading_api`
- 补齐区间：`2021-06-26` 到 `2023-05-31`
- 已有区间：`2023-06-01` 到 `2026-05-29`
- 合并后有效交易日区间：`2021-06-28` 到 `2026-05-29`

`2021-06-26` 是周末，A 股窗口内首个实际交易日为 `2021-06-28`。

## 同步命令

先启动 API：

```bash
docker compose up -d api
```

补齐日线：

```bash
curl -sS -X POST http://localhost:18000/api/tushare/sync-market-daily \
  -H 'Content-Type: application/json' \
  -d '{"start_date":"2021-06-26","end_date":"2023-05-31","skip_existing":true,"min_existing_rows":5000}'
```

返回：

```json
{"status":"ok","target":"market:daily","trade_dates":468,"skipped_trade_dates":0,"rows_upserted":2269115,"failed_dates":[]}
```

补齐 daily_basic：

```bash
curl -sS -X POST http://localhost:18000/api/tushare/sync-market-daily-basic \
  -H 'Content-Type: application/json' \
  -d '{"start_date":"2021-06-26","end_date":"2023-05-31","skip_existing":true,"min_existing_rows":5000}'
```

返回：

```json
{"status":"ok","target":"market:daily_basic","trade_dates":468,"skipped_trade_dates":0,"rows_upserted":2248457,"failed_dates":[]}
```

## 只读 SQL 验证

命令：

```bash
docker compose exec -T db psql -U quant -d quant_trading -v ON_ERROR_STOP=1 \
  -c "select 'stock_daily_bars' as table_name, min(trade_date) as min_date, max(trade_date) as max_date, count(*) as rows, count(distinct ts_code) as symbols, count(distinct trade_date) as trade_dates from stock_daily_bars union all select 'stock_daily_basic', min(trade_date), max(trade_date), count(*), count(distinct ts_code), count(distinct trade_date) from stock_daily_basic;" \
  -c "select target, start_date, end_date, rows_upserted, status, message, created_at from data_sync_runs order by created_at desc limit 8;"
```

结果：

| table_name | min_date | max_date | rows | symbols | trade_dates |
| --- | --- | --- | ---: | ---: | ---: |
| stock_daily_bars | 2021-06-28 | 2026-05-29 | 6,155,284 | 5,713 | 1,192 |
| stock_daily_basic | 2021-06-28 | 2026-05-29 | 6,134,626 | 5,713 | 1,192 |

最近同步记录：

| target | start_date | end_date | rows_upserted | status | message |
| --- | --- | --- | ---: | --- | --- |
| market:daily_basic | 2021-06-26 | 2023-05-31 | 2,248,457 | ok | trade_dates=468, skipped_dates=0, failed_dates=0 |
| market:daily | 2021-06-26 | 2023-05-31 | 2,269,115 | ok | trade_dates=468, skipped_dates=0, failed_dates=0 |
| market:daily_basic | 2023-06-01 | 2026-06-01 | 3,886,169 | ok | trade_dates=725, skipped_dates=0, failed_dates=0 |
| market:daily | 2023-06-01 | 2026-06-01 | 3,886,169 | ok | trade_dates=725, skipped_dates=0, failed_dates=0 |

## 数据源结论

- Tushare `daily` 数据源可用：缺口同步返回 `status=ok`，失败日期为空。
- Tushare `daily_basic` 数据源可用：缺口同步返回 `status=ok`，失败日期为空。
- 本报告不验证逐股财务指标完整性；此前 `market:fundamentals` 同步记录仍是 `partial`，后续如果把财务指标作为硬验收，应单独补齐和审计。

## 边界

- 未执行 `docker compose down -v`。
- 未删除或重建 PostgreSQL volume。
- 未把 `.env`、Tushare token 或数据库密码写入报告。
