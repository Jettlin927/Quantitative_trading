# 美股本地私有持仓

本目录只提供本地 CSV 工具，用于把已经由 Gmail 正文确认的 HSBC `全部執行` / `全部执行` 成交整理成成交账本、FIFO/open-lot 持仓 CSV 和本地 HTML。

它与 `my_quant/us_research/` 的 sample/实验行情数据严格分离；不连接券商、不下单、不生成交易指令，也不把私人持仓写入 PostgreSQL、API 或前端。

## 使用

输入行必须按实际成交先后排列。`email_ts_utc` 可用于审计，但脚本不会用交易编号猜测缺失的成交顺序。要得到完整当前持仓，输入账本必须覆盖期初以来的完整成交；若卖出数量超过已知 open lots，脚本会在写文件前失败。

```bash
python3 -m my_quant.us_holdings.scripts.update_hsbc_ledger --input /path/to/confirmed_fills.jsonl
```

默认输出到 Git 忽略目录：

- `outputs/private/us_hsbc/hsbc_executed_trades.csv`
- `outputs/private/us_hsbc/hsbc_current_holdings.csv`
- `outputs/private/us_hsbc/holdings.html`

真实成交、持仓、Gmail 标识和券商导出不得提交到 Git。若未来需要入库或通过 API/前端展示私人持仓，必须先单独批准数据治理方案。
