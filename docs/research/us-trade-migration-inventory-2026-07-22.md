# 美股研究与 HSBC 持仓账本迁移清单

> 历史清单：`my_quant/us_holdings`、`my_quant/us_research`、HSBC ledger 和对应测试已于
> 2026-08-05 按 [ADR 0010](../adr/0010-us-first-workbench-and-retired-legacy-data-paths.md)
> 退役；下列路径和命令不再存在。当前持仓只走私有工作台的手工维护合同。

日期：2026-07-22

## 背景

用户希望把 `/Users/jettlin/code/投资分析` 中的美股研究、持仓账本和快照能力迁移到 Quantitative_trading。当前可用目标仓库是 `/Users/jettlin/code/Quantitative_trading`；本机未发现 `~/Document/Quantitative_trading` 或 `~/Documents/Quantitative_trading` 作为有效 Git 仓库。

Quantitative_trading 的数据治理红线仍然有效：迁移 PR 不直接提交真实持仓、真实成交、Gmail 正文、券商导出或密钥。用户已于 2026-07-22 明确批准“本地 gitignore 私有 CSV”这一最小治理方案，用于从正文确认的成交计算个人持仓；该批准不覆盖 PostgreSQL、API、前端、共享服务器或 Git 提交。

## 迁移对象

### 第一优先级：账本与快照流水线

- `hsbc_executed_trades_2026.csv`：已成交交易事实，迁移时只作为本地私有数据源，不提交真实内容。
- `hsbc_current_holdings_2026.csv`：当前 open-lot 基线，不能简单从历史成交全量重放。
- `hsbc_non_executed_orders_2026.csv`：未成交、取消、失败订单，只用于核对，不进入已成交账本。
- `watchlist_symbols_2026.csv`：研究池、持仓角色和 recent_exit 状态。
- `scripts/finnhub_snapshot.py`：持仓快照、行情刷新和 HTML 输出逻辑。
- `tests/test_finnhub_snapshot_fast_refresh.py`：holdings-only 快路径验证。
- `reports/daily-notify/latest-holdings-snapshot.html` 与 `data/holdings_latest/`：最新持仓快照产物，迁移后应改为运行产物而不是长期源码事实。

### 第二优先级：研究池与预测台账

- `prediction_ledger_2026.csv`：B 轴 thesis 预测台账。
- `reports/ai-network-upstream/`：AI 网络/上游瓶颈研究、估值和预测台账展示。
- `reports/memory-sndk-mu-research/`：存储链研究报告。
- `reports/spacex-upstream/`：SpaceX/Starlink 上游专题。
- `reports/ai-upstream-other-modules/`、`reports/ai-downstream-rotation/`：其他产业链专题。

### 第三优先级：通知与可视化

- `scripts/daily_notify_report.py`：本地通知摘要。
- `reports/daily-notify/`：日常快照、K 线交易图和 Markdown 摘要。
- `hsbc-trade-overview-2026.html`、`finnhub-live-snapshot.html`：可读总览入口。

### 不迁移或需人工确认

- `.env.local`、`.env`、任何 API key、Gmail token、券商登录信息。
- Gmail 原始正文、截图、浏览器缓存、临时 canvas 状态。
- 真实 HSBC CSV 内容不得迁移或提交；已批准的本地私有文件只作为运行输入和输出。
- 旧报告截图可按需保留为工件，不默认塞入 Git。

## 目标结构建议

- `my_quant/us_holdings/broker_ledger.py`：本地私有成交账本的 CSV 归一化、去重和 FIFO/open-lot 计算。
- `my_quant/us_holdings/scripts/update_hsbc_ledger.py`：从 Gmail connector 已确认字段生成私有 CSV 和本地 HTML。
- `outputs/private/us_hsbc/`：真实账本默认输出目录，必须 gitignore。
- `my_quant/us_research/config/watchlist_symbols.csv`：未来承接非敏感研究池。
- `docs/research/us-trade-migration-inventory-2026-07-22.md`：本迁移清单。

## Gmail 到 CSV 的新边界

Gmail connector 只负责搜索和读取邮件正文；脚本只接收已确认的结构化成交行。

最小输入字段：

- `trade_id`：交易编号，必填，用于去重。
- `status`：只接受 `全部執行` / `全部执行`。
- `side`：`買入` / `买入` / `沽出`。
- `股票名稱/股票編號` 或 `ticker` + `security_name`。
- `quantity`：已成交数量。
- `price`：成交价，可接受 `USD10.00`。
- `email_ts_utc`、`email_id`：可选但建议保留，便于审计。

输入行必须按实际成交先后排列；缺失时间戳时保留输入顺序，不能用交易编号猜测 FIFO 顺序。只有覆盖期初以来完整成交的 ledger 才能生成完整当前持仓；卖出超过已知 open lots 时必须在写文件前停止。

`有待執行`、`全部取消`、`未能執行` 仍只作为核对信息，不写入已成交 ledger。

## 分阶段验收

1. 本 PR：建立私有 ledger 工具、迁移清单、gitignore 边界和单元测试；不提交真实交易数据。
2. 后续 PR：把 `finnhub_snapshot.py` 的 holdings-only 快路径迁入 `my_quant/us_research`，并改为读取私有 holdings CSV。
3. 后续 PR：迁移非敏感 watchlist / thesis ledger schema，并给前端只读展示入口。
4. 当前只批准本地私有文件模式；若要把私人持仓写入 PostgreSQL、API、前端或共享服务器，必须重新设计并单独批准数据治理方案。

## 验证命令

```bash
python3 -m unittest backend.tests.test_us_broker_ledger
python3 -m py_compile my_quant/us_holdings/broker_ledger.py my_quant/us_holdings/scripts/update_hsbc_ledger.py
```
