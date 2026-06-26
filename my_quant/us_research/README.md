# 美股操作层研究工作区

`my_quant/us_research/` 是文件化的美股操作层。它服务于“用 A 股验证规则，再反哺美股持仓和观察池”的闭环，但本目录第一版只使用 sample 数据，不读取真实券商导出，不连接真实账户，不自动下单。

## 目录

- `config/watchlist_symbols.csv`：sample 观察池配置，保留 `ticker`、`role`、`notes` 这三个从现有观察池结构迁来的核心列，并补充主题、工具类型、杠杆系数和风险标签。
- `data/holdings_sample.csv`：sample 持仓结构，数量和成本为虚构示例，不代表真实持仓。
- `data/snapshots/`：yfinance 快照 JSON/CSV 输出目录。
- `reports/`：HTML + Markdown 美股操作报告输出目录。
- `scripts/refresh_us_snapshot.py`：使用 yfinance 拉取历史行情并计算趋势、新鲜度和风险字段。
- `scripts/build_us_operations_report.py`：从快照和 sample 持仓生成研究辅助报告。
- `tests/`：标准库 `unittest` 测试，不依赖网络。

## 生成 sample 报告

在仓库根目录执行：

```bash
.venv/bin/python -m my_quant.us_research.scripts.refresh_us_snapshot
.venv/bin/python -m my_quant.us_research.scripts.build_us_operations_report
```

输出：

- `my_quant/us_research/data/snapshots/us_snapshot_latest.json`
- `my_quant/us_research/data/snapshots/us_snapshot_latest.csv`
- `my_quant/us_research/reports/latest_us_operations.html`
- `my_quant/us_research/reports/latest_us_operations.md`

## 数据状态

快照和报告都会写入：

- `source`：当前为 `yfinance`。
- `fetched_at`：抓取时间。
- `status`：`ok`、`partial` 或 `stale`。
- `is_stale` / `stale_reason`：单个 ticker 的数据失败或陈旧原因。

如果 yfinance 失败，报告仍会生成，但会显示 `partial` 或 `stale`，动作标签默认转为 `观察不动`。

## 边界

- 不提交真实 `.env`、token、券商导出、真实持仓或真实成交。
- 不连接 HSBC、券商 API 或任何可触发资金变化的接口。
- 报告中的 `继续持有`、`只等回调`、`止跌后小加`、`减仓降风险`、`观察不动` 都是研究辅助标签，不是交易指令。
