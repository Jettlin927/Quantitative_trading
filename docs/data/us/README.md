# 美股数据边界

仓库同时保留两条严格隔离的数据线：旧 `assets` / `asset_daily_prices` 是 sample 开发夹具；`us_experiment_*` 是免费公开源驱动的实际市场实验数据。两者都不能冒充研究级数据，也不能单独支持正式研究结论。

## 免费实验合同

- 当前标的目录通过 AKShare `stock_us_spot_em` 获取 Eastmoney `m:105,m:106,m:107` 全量快照，不设置人工票数上限。2026-07-21 现场发现 13,672 个代码；该数值会随目录变化，不是固定承诺。
- 主日线通过 yfinance 分批获取，显式使用 `interval=1d`、`auto_adjust=false`，保存 raw OHLCV、Adj Close、现金分红与拆股比例。
- 目标回填起点为 `2010-01-01`；上市较晚或源端历史不足的标的，以实际最早可得日留痕。
- AKShare `stock_us_hist(adjust="")` 只对每日确定性轮换样本和 yfinance 失败代码做同日校验；结果写入 `us_experiment_daily_checks`，绝不覆盖 yfinance 主表。
- API 与前端固定返回 `isExperimental=true`、`researchEligible=false`、`executionEnabled=false`。
- 当前目录不是历史 point-in-time universe，退市代码、历史成分、数据许可和长期可复现性仍是正式研究门禁。

## 表与只读接口

- `us_experiment_instruments`：当前目录、数据源代码、Yahoo 映射和逐标的同步状态。
- `us_experiment_daily_bars`：yfinance 主日线。
- `us_experiment_daily_checks`：AKShare 独立对照。
- `GET /api/us-experiment/overview`
- `GET /api/us-experiment/instruments`
- `GET /api/us-experiment/instruments/{source_code}/daily-bars`

## 回填与每日同步

首次或断点回填通过持久 Worker 分批执行：

```bash
python3 scripts/ops/backfill_us_experiment.py --start-date 2010-01-01 --end-date 2026-07-21
```

checkpoint 位于被 Git 忽略的 `outputs/us-experiment-checkpoints/`。每日任务使用最近 10 个日历日的短窗口，以覆盖周末、节假日和短暂停机：

```bash
scripts/ops/sync_us_experiment_daily.sh
scripts/ops/install_us_experiment_cron.sh
```

cron 固定为 `CRON_TZ=Asia/Shanghai` 的每日 `10:00`。安装 cron、执行 migration、全量回填和部署生产是不同操作，不能因代码合入而自动发生。

## Sample 保留边界

- 只读预览 `my_quant/us_research/` 的 sample 资产、快照和观察池。
- 将 sample 数据幂等写入明确标注的 sample schema。
- 在 API 和前端继续显著展示 sample 身份与限制。

## 历史实施记录

- [数据库确认清单（2026-06-27）](../../archive/data/us/us-db-confirmation-checklist-2026-06-27.md)
- [sample schema 实施记录（2026-06-27）](../../archive/data/us/us-sample-db-schema-implementation-2026-06-27.md)
- [sample 只读 API 记录（2026-06-27）](../../archive/data/us/us-sample-readonly-api-2026-06-27.md)
