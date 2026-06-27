# 美股 sample 数据源

`my_quant/us_research/` 是美股 sample 数据源目录。它只服务于把 sample 观察池、sample 快照和 sample 持仓结构导入 PostgreSQL。

## 目录

- `config/watchlist_symbols.csv`：sample 观察池配置。
- `data/holdings_sample.csv`：sample 持仓结构，数量和成本为虚构示例。
- `data/snapshots/us_snapshot_latest.json`：sample 快照 JSON。
- `data/snapshots/us_snapshot_latest.csv`：sample 快照 CSV。
- `scripts/refresh_us_snapshot.py`：使用 yfinance 刷新 sample 快照。

## 刷新 sample 快照

在仓库根目录执行：

```bash
.venv/bin/python -m my_quant.us_research.scripts.refresh_us_snapshot
```

输出：

- `my_quant/us_research/data/snapshots/us_snapshot_latest.json`
- `my_quant/us_research/data/snapshots/us_snapshot_latest.csv`

## 入库

后端预览：

```text
GET /api/us-research/import-preview
```

写入 sample 数据：

```text
POST /api/us-research/import-sample
```

DB 概览：

```text
GET /api/us-research/db-overview
```

## 边界

- 当前数据全部视为 sample。
- 不提交真实持仓、真实成交或券商导出。
- 不连接 HSBC、券商 API 或任何真实账户。
- 不生成操作建议、策略报告或回测报告。
