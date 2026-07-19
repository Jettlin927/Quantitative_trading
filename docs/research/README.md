# 数据与量化研究文档

`docs/research/` 记录数据源、DB schema、覆盖审计、sample 入库和 2026-07-10 重新开启的量化研究底座。

旧策略研究、旧回测报告和旧研究阶段仍不恢复。新的研究能力以 `backend/app/quant_research/` 为唯一协议层，并严格保持离线研究、下一交易日执行、point-in-time、基准对照和运行可复现边界。

## 当前研究底座

- `quant-foundation-trust-contract.md`：新研究链路的统一可信合同，定义 quality scope、宇宙血缘、信息可得时点、下一交易日执行、输入快照和可复现键。
- `strategy-evaluation-standard.md`：所有具体策略研究的强制画像与评价规范，固定结论状态、数据/执行/成本证据、指标字典、市场环境矩阵、稳健性门禁和复用报告模板。
- `quant-research-foundation-plan-2026-07-10.md`：专业研究底座能力矩阵、缺口、实施顺序和验收标准。
- `../superpowers/plans/2026-07-13-quant-research-capability-gap-closure.md`：基于当前可信底座补齐多策略分发、特征、模拟账本、A 股横截面、walk-forward 和基础风险层的分阶段计划。
- `backend/tests/fixtures/quant_research_golden/`：完全合成的最小黄金数据集，用于锁定周末、停牌、涨跌停、复权、公告可用日和退市边界。
- 一次性运行产物写入被 Git 忽略的 `outputs/research-runs/`，不再把大型逐事件 CSV 提交到主仓库。
- `docs/research/strategy-results/` 是统一只读发布层：同时登记当前可信报告与明确标记的旧档案；它不提供研究执行入口，也不把报告状态冒充生产部署或交易事实。

## 静态策略与统一入口

正式 runner 只允许源码静态登记的六条研究策略；不能动态安装、按模块路径导入或上传策略代码：

| strategy ID | 版本 | scope | 示例配置 | 用途 |
| --- | --- | --- | --- | --- |
| `sentinel_etf_baseline` | `1` | `etf_time_series` | `configs/research/sentinel_etf_baseline.json` | 验证质量门禁、冻结快照和离线复现 |
| `etf_trend_120d` | `1` | `etf_time_series` | `configs/research/etf_trend_baseline.json` | 固定 120 日均线、月末 1/0 目标的时序 baseline |
| `etf_volatility_managed` | `1` | `etf_time_series` | `configs/research/etf_volatility_managed_baseline.json` | Moreira–Muir 倒数已实现方差、月末无杠杆 ETF 暴露复现 |
| `etf_low_volatility_gate` | `1` | `etf_time_series` | `configs/research/etf_low_volatility_gate.json` | 校准期中位数固定门槛、月末低波动满仓/高波动空仓的事后探索 |
| `a_share_price_baseline` | `1` | `a_share_cross_section` | `configs/research/a_share_price_baseline.json` | 固定 120–20 动量、60 日波动、历史行业成员的价格型横截面 baseline |
| `a_share_b1_trend_pullback` | `1` | `a_share_cross_section` | `configs/research/a_share_b1_long_history.json` | 对公开 B1 趋势回调描述的事前固定近似复现；不宣称掌握来源完整公式 |

所有已发布结果统一从 [`strategy-results/index.html`](strategy-results/index.html) 进入，机器清单见 [`strategy-results/manifest.json`](strategy-results/manifest.json)。当前可信报告如下：

| 策略/报告 | 样本与关键事实 | 强制状态 |
| --- | --- | --- |
| `etf_volatility_managed@1` | OOS `2018-01-02..2026-06-29`；首个执行日已从初始本金计入；基准逆方差版 100,000 元期末约 152,552 元，最大回撤 -40.89%，Sharpe 改善与稳定性门禁失败 | `不通过` |
| `etf_low_volatility_gate@1` | 同一 OOS 上追加的第 5 个研究假设；100,000 元期末约 129,035 元，最大回撤 -52.82%，差于被动和固定半仓 | `不通过` |
| `etf_trend_120d@1` | `2012-11-19..2026-06-29`；基础成本期末约 100,683 元，最大回撤 -52.82%，长期财富显著差于被动与同暴露静态基准 | `不通过` |
| `a_share_b1_trend_pullback@1` | `2012-06-26..2026-07-10`；公开规则代理而非精确复制，长历史主版本期末约 26,649 元、最大回撤 -90.99% | `不通过` |

三组报告共 16 个最终 canonical 运行均绑定代码提交 `26da0d347d77de7ee03a95277fc4ad45bdaa983a` 和镜像 `sha256:5061ca1a590f626ae4bfff58c24a0c9f07a9b62be8cf6ef554abcf3748bdbb3d`。每个运行都在该镜像的 `--network none` 容器中连续复现 2 次，result fingerprint 16/16 × 2 全部匹配；完整两轮总账见 [`strategy-results/reproduction-evidence-20260719.json`](strategy-results/reproduction-evidence-20260719.json)，报告生成器会校验各自运行子集并在不一致时停止。波动率管理与低波动准入共享 snapshot `8d3be33191f476fe0c4fed39f1ae1e95467c24ff46d90532a4202e42284faffc`；趋势报告使用 snapshot `5552b240062a2d9f549770830aefe614f481e64f1e98df03f49357610670653e`；B1 报告分别保留长历史与来源周期冻结快照。各运行 ID、配置哈希、结果指纹和独立报告生成时间以对应 `summary.json` 为准。

不用连接数据库即可查看登记身份、必需冻结输入和示例配置：

```bash
python scripts/research/run_quant_research.py --list-strategies
```

六条策略都固定 `researchOnly=true`、`notInvestmentAdvice=true`、`executionEnabled=false`、`realBrokerConnected=false`。它们是研究协议示例，不是推荐、评级、收益承诺或真实交易入口。

新运行使用 artifact schema v2，公共产物包括 `targets.csv.gz`、`nav.csv.gz`、`rebalance_requests.csv.gz`、`rebalance_executions.csv.gz`、`positions.csv.gz`、`metrics.json`、`limitations.json`、`manifest.json` 和 hash-chain checkpoints。显式启用验证或风险策略时，还会成对写入：

- `walk_forward_windows.csv.gz` 与 `walk_forward_metrics.csv.gz`：只汇总 `test_oos`，训练区间不进入结论。
- `risk_exposures.csv.gz` 与 `risk_contributions.csv.gz`：只读取冻结收益、NAV、positions 和历史成员，并进入结果指纹。

已完成的 artifact schema v1 归档仍可验证和离线 reproduce；未完成的 v1 临时运行不能跨版本续跑。

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

## 固定 ETF 趋势 baseline

`configs/research/etf_trend_baseline.json` 登记为 `etf_trend_120d@1`。它只使用单只显式 ETF 的冻结日频复权收盘价，以固定 120 个开市日移动平均形成月末 1/0 目标，并在下一开市日开盘尝试执行。窗口、月末频率和权重均固定，不允许参数网格、自动搜索或事后挑选。

运行时先按配置的 `warmupStart..endDate`、`510300.SH` 和 `000300.SH` 创建匹配的 ETF quality run，再执行：

```bash
python scripts/research/run_quant_research.py \
  --config configs/research/etf_trend_baseline.json \
  --quality-run-id <QUALITY_RUN_ID>
```

该 baseline 只用于证明多策略分发、因果 rolling feature、月末目标与离线复现合同，不构成 alpha 结论、评级或交易建议。

## 固定 A 股价格型横截面 baseline

`configs/research/a_share_price_baseline.json` 登记为 `a_share_price_baseline@1`。它只使用 `industry_members` 的逐日历史成员、上市/退市边界、日线、复权、涨跌停、停牌和指数基准；不读取财务指标，也不使用当前股票列表冒充历史 universe。每个完整月末以固定 120–20 日动量和 60 日波动率做横截面排名，选择固定 topN 等权，并在下一开市日开盘尝试执行。

质量检查的日期必须精确覆盖配置的 `warmupStart..endDate`。industry universe 不接受 inline 股票代码、文件路径或 `asOfDate`：

```bash
python scripts/research/check_data_quality.py \
  --scope a_share_cross_section \
  --start-date 2025-06-02 \
  --end-date 2026-06-29 \
  --universe-type industry_membership \
  --universe-source industry_members \
  --universe-source-key 801080.SI \
  --benchmark 000300.SH

python scripts/research/run_quant_research.py \
  --config configs/research/a_share_price_baseline.json \
  --quality-run-id <QUALITY_RUN_ID>
```

该配置固定启用 anchored walk-forward 和 60/20 日滚动协方差风险工件。完整月末可用成员不足 topN、历史成员漂移、缺复权/涨跌停/停牌/上市边界、基准不重叠或风险数值非有限时都会失败，不做数据回退或参数搜索。

## 风险、约束分配与数据门禁

`backend/app/quant_research/risk.py` 输出逐日 gross/net/cash、最大权重、HHI、历史行业暴露、benchmark beta，以及标的边际/总风险贡献。窗口不足保持 null；有效窗口内总风险贡献之和必须在数值容差内等于组合年化波动。NaN、Infinity、重复键或不闭合会失败，不能自动填零。

`backend/app/quant_research/allocation.py` 是纯研究目标权重函数，支持等权和逆波动率起始分配、单票上限、行业上限、最低现金与单次单边换手上限。它采用固定顺序的裁剪、重分配和线性换手收缩，不宣称全局最优；不可行约束明确失败。当前登记 baseline 尚未自动调用该分配器，它也不会生成订单或连接券商。

能力边界保持显式：

- 基础历史行业暴露使用现有 `industry_members`/冻结 universe。
- 完整指数成分归因在非空 `index_weights` 落地前保持 blocked，不能用当前成分或 `index_daily_bars` 替代。
- 行业基准比较在非空、可复现的 `industry_proxy_daily` 落地前保持 blocked。
- 上述新表若未来实施，必须单独设计 Alembic、隔离 PostgreSQL 验收和生产迁移，并重新取得用户确认。

Phase 5 黑盒反例审计入口：

```bash
python scripts/research/audit_quant_research.py
```

该入口固定重放未来前缀、快照/账本/风险篡改、历史成员漂移、不可成交、约束不可行、OOS-only、resume 和断库 reproduce；完整 PostgreSQL 语义仍由 `scripts/ops/test_postgres_integration.sh` 验证。

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
