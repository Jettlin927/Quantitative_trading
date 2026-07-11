# 数据与量化研究文档

`docs/research/` 记录数据源、DB schema、覆盖审计、sample 入库和 2026-07-10 重新开启的量化研究底座。

旧策略研究、旧回测报告和旧研究阶段仍不恢复。新的研究能力以 `backend/app/quant_research/` 为唯一协议层，并严格保持离线研究、下一交易日执行、point-in-time、基准对照和运行可复现边界。

## 当前研究底座

- `quant-foundation-trust-contract.md`：新研究链路的统一可信合同，定义 quality scope、宇宙血缘、信息可得时点、下一交易日执行、输入快照和可复现键。
- `quant-research-foundation-plan-2026-07-10.md`：专业研究底座能力矩阵、缺口、实施顺序和验收标准。
- `backend/tests/fixtures/quant_research_golden/`：完全合成的最小黄金数据集，用于锁定周末、停牌、涨跌停、复权、公告可用日和退市边界。
- 一次性运行产物写入被 Git 忽略的 `outputs/research-runs/`，不再把大型逐事件 CSV 提交到主仓库。
- `docs/research/strategy-results/` 仅为历史只读档案，不代表当前策略候选或新底座验收结果。

## 可复现 ETF sentinel

`configs/research/sentinel_etf_baseline.json` 是第一条正式研究闭环配置。它只使用日频 ETF、基金复权因子、冻结的官方交易日历和指数基准，采用固定信号日、固定目标权重、下一交易日开盘执行；没有参数搜索，不依赖分钟线、期权或财务横截面。

正式运行前先执行同一 scope、日期、universe 和 benchmark 的数据质量检查。universe 必须来自实际存在的排序成员文件，不能只传一组内存代码：

```bash
python scripts/research/check_data_quality.py \
  --scope etf_time_series \
  --start-date 2025-12-01 \
  --end-date 2025-12-31 \
  --universe 510300.SH \
  --universe-type explicit_snapshot \
  --universe-source configs/research/sentinel_etf_universe.txt \
  --universe-as-of-date 2025-12-01 \
  --benchmark 000300.SH

python scripts/research/run_quant_research.py --quality-run-id <QUALITY_RUN_ID>
```

正式镜像必须注入真实 `APP_GIT_COMMIT`。运行顺序固定为 quality gate → input snapshot → features/targets → simulation → metrics → manifest → finalize，产物保存于 `outputs/research-runs/` 的独立持久卷。输入 CSV 使用稳定列、writer 强制的自然键单调去重、固定 null 语义和 `gzip mtime=0`；`\\N` 只允许表示 null，非 null 字符串若恰好等于该哨兵值会在写入和 hash 前被拒绝。文件登记 complete 前会 fsync 并原子 rename。universe 路径只作相对审计元数据，不进入 config/snapshot 身份；身份绑定实际来源 SHA、成员工件 SHA 和 `universeHash`。

每个阶段完成后会原子写入 checkpoint、输入/输出 hash 和前一 checkpoint hash，并同步 `stage` 与 `heartbeat_at`。真实进程中断不会被伪装成业务失败：运行记录与 `.RUN_ID.tmp` 保持 `running`，下一次 CLI 启动按阈值把陈旧运行转为 `interrupted`，并在 `checkpoints/recovery.json` 保留审计事件。之后只能显式续跑：

```bash
python scripts/research/run_quant_research.py \
  --resume <RUN_ID> \
  --stale-after-seconds 300
```

`--resume` 与 `--quality-run-id` 互斥。续跑会先校验 config/code/environment/snapshot/reproducibility key、完整 checkpoint 哈希链和已完成阶段的归档文件；校验通过后只执行最后有效 checkpoint 之后的阶段。归档验证与 reproduce 还会把 manifest `inputs/*`、snapshot table artifacts 和 checkpoint 阶段引用逐项交叉校验，任何一份元数据单独变化都会在重新计算前失败。身份变化要求新建 run，checkpoint 或归档损坏则直接停止，不能静默跳过或覆盖。

离线复现只读取运行目录内的冻结输入，不访问在线行情表：

```bash
python scripts/research/reproduce_quant_research.py outputs/research-runs/runs/<RUN_ID>
```

sentinel 仅验证研究管线，所有 manifest 都标记 `researchOnly=true`、`notInvestmentAdvice=true`、`executionEnabled=false`。它不是 alpha 研究、买卖评级或收益承诺。

## 生产验收基线

2026-07-12 已在生产 PostgreSQL 上完成固定 2025-12 `510300.SH` / `000300.SH` 验收：质量运行 `4930ff05-a332-4a62-b7a8-1c7479126bca` 的 30 条规则全部通过；冻结快照为 `cb9bac39488283a13e5d31604471841b7ac5311e0e5852f1d9ac8d0639152dab`；研究运行 `a22fb663-1b66-4579-ab58-e6d3236d1843` 的结果指纹为 `61aa690cc0f7ea6e1b090cbbdae359696a74ad5434266167c175b6453bbe5079`。数据库地址不可连接时连续两次 reproduce 均精确匹配。

这组 ID 是管线验收基线，不是可交易策略。基金全量数据当前最新到 2026-06-29；严格财务横截面仍受历史修订不可重建限制。完整输入哈希、生产迁移和重启恢复证据见 `docs/deployment/2026-07-12-production-trustworthiness-acceptance.md`。

## 当前保留文档

- `a-share-data/README.md`：A 股 DB 覆盖结论入口。
- `a-share-data/db-coverage-audit-2026-06-26.md`：A 股五年日线和 daily_basic 覆盖审计。
- `a-share-data/data-source-audit-2026-06-26.md`：A 股数据源和字段覆盖说明。
- `us-db-confirmation-checklist-2026-06-27.md`：美股 sample DB 表创建和入库确认记录。
- `us-sample-db-schema-implementation-2026-06-27.md`：美股 sample schema 与 API 实施记录。
- `us-sample-readonly-api-2026-06-27.md`：美股 sample 文件预览 API 记录。

## 当前 DB 主线

A 股：

- `stocks`
- `stock_daily_bars`
- `stock_daily_basic`
- `stock_financial_indicators`
- `stock_listings`
- `stock_limit_prices`
- `stock_suspend_events`
- `trade_calendars`
- `stock_adjust_factors`
- `indices`
- `index_daily_bars`
- `funds`
- `fund_daily_bars`
- `fund_adjust_factors`
- `industry_classifications`
- `industry_members`
- `stock_pools`
- `stock_pool_members`
- `data_sync_runs`
- `data_sync_jobs`

美股 sample：

- `assets`
- `asset_daily_prices`
- `watchlist_items`
- `portfolio_snapshots`

## 安全边界

- 不保存真实持仓、真实成交或券商导出。
- 不连接真实券商或真实账户。
- 不删除 PostgreSQL volume。
- 不把 sample 数据写成交易建议。
