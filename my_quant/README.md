# my_quant

`my_quant/` 当前只保留与主仓数据工作台相关的 sample 数据源。

历史策略研究、回测、报告和盘前自动化已从当前主线移除。不要在这里恢复 `strategy_research`，除非用户明确要求重新开启策略研究。

## 当前保留内容

- `us_research/config/watchlist_symbols.csv`：美股 sample 观察池配置。
- `us_research/data/holdings_sample.csv`：美股 sample 持仓结构，数量和成本为虚构示例。
- `us_research/data/snapshots/us_snapshot_latest.json`：美股 sample 快照。
- `us_research/data/snapshots/us_snapshot_latest.csv`：美股 sample 快照表格。
- `us_research/scripts/refresh_us_snapshot.py`：刷新 sample 快照。

## 边界

- 不提交真实持仓、真实成交或券商导出。
- 不连接券商或真实账户。
- 不生成策略报告、操作报告或回测报告。
- 不把 sample 数据写成交易建议。

## 可选环境

如果需要刷新 sample 快照，可在仓库根目录 Python 3.12 环境中安装 `my_quant/requirements.txt`：

```bash
.venv/bin/python -m pip install -r my_quant/requirements.txt
```

刷新 sample 快照：

```bash
.venv/bin/python -m my_quant.us_research.scripts.refresh_us_snapshot
```
