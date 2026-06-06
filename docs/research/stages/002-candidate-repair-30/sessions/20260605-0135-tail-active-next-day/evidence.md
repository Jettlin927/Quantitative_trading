# Evidence

## 已完成

- 新增 `tail-active-next-day` 单票策略入口。
- 单票回测已接入 `stock_daily_basic.volume_ratio`、`turnover_rate`、`turnover_rate_f`。
- 组合研究脚本 `scripts/research/run_portfolio_backtest.py` 已补齐尾盘模式的“次日未涨停退出”语义。
- 新增 `scripts/research/run_tail_active_grid.py`，可做参数网格：
  - 涨幅区间：`3%-5%`、`2.5%-5%`、`3%-6%`、`3.5%-5.5%`
  - 量比阈值：`1.5`、`2.0`
  - 换手率阈值：`5%`、`7%`
  - 涨停记忆窗口：`10`、`15` 个交易日
  - 历史主线代理：按本地行业同日平均涨幅、上涨比例和行业排名过滤。
- 新增 `scripts/research/sync_tail_minute_bars.py`，可重建尾盘候选日期并用 run-local 缓存探测 `14:30` 分钟入场价覆盖率；不写数据库。
- 新增实时筛选源：
  - 腾讯实时行情：涨幅、量比、换手、涨跌停价。
  - 同花顺热点：强势股题材归因。
  - 东财行业榜：行业主线快照，断连时空数据兜底。

## 验证

- `python -m py_compile backend\app\database.py backend\app\models.py backend\app\schemas.py backend\app\backtest_engine.py backend\app\tushare_client.py backend\app\ai_client.py backend\app\market_signal_sources.py backend\app\main.py scripts\research\run_portfolio_backtest.py scripts\research\run_tail_active_grid.py`：通过。
- 实时源冒烟：
  - 腾讯实时行情返回 `2` 条。
  - 同花顺热点返回 `89` 条。
  - 东财行业榜曾返回 `100` 个行业；复测遇到上游断连，已兜底为空行业榜。

## 数据恢复

本地 Docker Desktop 与 PostgreSQL 已恢复。当前历史样本覆盖：

- 股票数：`5525`
- `stock_daily_bars`：`3901653` 行
- `stock_daily_basic`：`3901653` 行
- 行情区间：`2023-05-29` 至 `2026-05-29`
- daily_basic 区间：`2023-05-29` 至 `2026-05-29`

## 历史回测结果

### `002-tail-active-grid-pilot-001`

命令：

```powershell
python scripts\research\run_tail_active_grid.py `
  --run-id 002-tail-active-grid-pilot-001 `
  --start-date 2023-01-01 `
  --end-date 2026-05-31 `
  --max-stocks 600 `
  --min-bars 120 `
  --exclude-st `
  --exclude-bj
```

当前最优参数：

```json
{
  "tailEntryMinPctChg": 0.025,
  "tailEntryMaxPctChg": 0.05,
  "tailMinVolumeRatio": 2.0,
  "tailMinTurnoverRatePct": 7.0,
  "tailPriorLimitUpLookback": 15
}
```

结果：

- 测试股票：`590`
- 有交易股票：`314`
- 完成交易：`613`
- 正收益率：`29.94%`
- 中位收益：`-0.57%`
- 平均收益：`-0.31%`
- 平均最大回撤：`-0.99%`
- 结论：交易数充足、回撤浅，但收益中枢为负，第一阶段未通过。

### `002-tail-active-risk-pilot-001`

命令：

```powershell
python scripts\research\run_tail_active_grid.py `
  --run-id 002-tail-active-risk-pilot-001 `
  --start-date 2023-01-01 `
  --end-date 2026-05-31 `
  --max-stocks 600 `
  --min-bars 120 `
  --exclude-st `
  --exclude-bj `
  --grid risk-refine
```

当前最优参数：

```json
{
  "tailEntryMinPctChg": 0.025,
  "tailEntryMaxPctChg": 0.05,
  "tailMinVolumeRatio": 2.0,
  "tailMinTurnoverRatePct": 7.0,
  "tailPriorLimitUpLookback": 15,
  "entryRiskFilter": {
    "enabled": true,
    "maxEntryRangePct": 0.06
  }
}
```

结果：

- 测试股票：`590`
- 有交易股票：`92`
- 完成交易：`109`
- 正收益率：`43.48%`
- 中位收益：`-0.22%`
- 平均收益：`-0.11%`
- 平均最大回撤：`-0.41%`
- 结论：风险过滤明显减少交易并提高正收益率，但收益中枢仍为负，不能称为最优可用方案。

### `002-tail-active-mainline-pilot-001`

命令：

```powershell
python scripts\research\run_tail_active_grid.py `
  --run-id 002-tail-active-mainline-pilot-001 `
  --start-date 2023-01-01 `
  --end-date 2026-05-31 `
  --max-stocks 600 `
  --min-bars 120 `
  --exclude-st `
  --exclude-bj `
  --grid mainline-refine
```

当前最优参数：

```json
{
  "tailEntryMinPctChg": 0.025,
  "tailEntryMaxPctChg": 0.05,
  "tailMinVolumeRatio": 2.0,
  "tailMinTurnoverRatePct": 7.0,
  "tailPriorLimitUpLookback": 15,
  "entryRiskFilter": {
    "enabled": true,
    "maxEntryRangePct": 0.06
  },
  "tailMainlineFilter": {
    "enabled": true,
    "minSamples": 20,
    "maxRank": 5,
    "minAvgReturnPct": 0.005,
    "minUpPct": 0.55
  }
}
```

结果：

- 测试股票：`590`
- 有交易股票：`9`
- 完成交易：`9`
- 正收益率：`44.44%`
- 中位收益：`-0.21%`
- 平均收益：`-0.10%`
- 平均最大回撤：`-0.26%`
- 结论：小样本主线代理过窄，交易数严重不足，且收益仍为负。

### `002-tail-active-best-risk-full-001`

命令：

```powershell
python scripts\research\run_tail_active_grid.py `
  --run-id 002-tail-active-best-risk-full-001 `
  --start-date 2023-01-01 `
  --end-date 2026-05-31 `
  --min-bars 120 `
  --exclude-st `
  --exclude-bj `
  --grid best-risk
```

结果：

- 测试股票：`4912`
- 有交易股票：`459`
- 完成交易：`530`
- 正收益率：`39.65%`
- 中位收益：`-0.21%`
- 平均收益：`-0.12%`
- 平均最大回撤：`-0.39%`
- 结论：全市场验证确认小样本负期望不是抽样偶然；当前最佳风险过滤版本仍未通过第一阶段。

### `002-tail-active-mainline-full-001`

命令：

```powershell
python scripts\research\run_tail_active_grid.py `
  --run-id 002-tail-active-mainline-full-001 `
  --start-date 2023-01-01 `
  --end-date 2026-05-31 `
  --min-bars 120 `
  --exclude-st `
  --exclude-bj `
  --grid mainline-refine
```

当前最优参数：

```json
{
  "tailEntryMinPctChg": 0.025,
  "tailEntryMaxPctChg": 0.05,
  "tailMinVolumeRatio": 2.0,
  "tailMinTurnoverRatePct": 7.0,
  "tailPriorLimitUpLookback": 15,
  "entryRiskFilter": {
    "enabled": true,
    "maxEntryRangePct": 0.06
  },
  "tailMainlineFilter": {
    "enabled": true,
    "minSamples": 20,
    "maxRank": 10,
    "minAvgReturnPct": 0.01,
    "minUpPct": 0.55
  }
}
```

结果：

- 测试股票：`4912`
- 有交易股票：`71`
- 完成交易：`76`
- 正收益率：`38.03%`
- 中位收益：`-0.23%`
- 平均收益：`-0.13%`
- 平均最大回撤：`-0.29%`
- 结论：行业同日涨幅主线代理降低交易量和回撤，但没有改善收益中枢；继续收紧行业排名不是优先方向。

## 分钟数据源探测

### `002-tail-active-minute-best-risk-full-dryrun-001`

命令：

```powershell
python scripts\research\sync_tail_minute_bars.py `
  --run-id 002-tail-active-minute-best-risk-full-dryrun-001 `
  --start-date 2023-01-01 `
  --end-date 2026-05-31 `
  --min-bars 120 `
  --exclude-st `
  --exclude-bj `
  --max-candidates 20 `
  --profile best-risk `
  --dry-run
```

结果：

- 候选日期：`534`
- 选中候选：`20`
- 结论：候选重建口径与全市场最佳风险过滤 run 的 `530` 笔交易规模接近，说明 `14:30` 数据源探测脚本能复现核心候选日期。

### `002-tail-active-minute-sample-001`

命令：

```powershell
python scripts\research\sync_tail_minute_bars.py `
  --run-id 002-tail-active-minute-sample-001 `
  --start-date 2023-01-01 `
  --end-date 2026-05-31 `
  --min-bars 120 `
  --exclude-st `
  --exclude-bj `
  --max-candidates 20 `
  --max-requests 1 `
  --profile best-risk
```

结果：

- 候选日期：`534`
- 选中候选：`20`
- 分钟匹配：`0`
- 请求错误：`1`
- 错误原因：Tushare `stk_mins` 当前账号频率限制为 `1次/小时`，不适合批量补尾盘分钟价。

### `002-tail-active-minute-eastmoney-sample-001`

命令：

```powershell
python scripts\research\sync_tail_minute_bars.py `
  --run-id 002-tail-active-minute-eastmoney-sample-001 `
  --start-date 2023-01-01 `
  --end-date 2026-05-31 `
  --min-bars 120 `
  --exclude-st `
  --exclude-bj `
  --max-candidates 20 `
  --max-requests 1 `
  --profile best-risk `
  --provider eastmoney-recent `
  --sleep-seconds 0
```

结果：

- 候选日期：`534`
- 选中候选：`20`
- 分钟匹配：`0`
- 请求错误：`1`
- 错误原因：东财近端分钟价格接口本次返回 `Remote end closed connection without response`；此前手工探测还发现该接口对历史日期参数不稳定，不能直接当作三年历史分钟源。

## 研究判断

当前证据支持四个判断：

1. 用户提出的三项活跃度条件能降低隔夜暴露和回撤，但不足以提供正期望。
2. 入场日振幅过滤 `maxEntryRangePct=0.06` 是目前最有价值的风险约束，但它只能把亏损收窄，不能反转期望。
3. 行业同日涨幅/上涨率/排名这种粗主线代理也不能修复信号；下一步应转向“14:30 分钟级入场价”和“题材持续性/热点归因历史缓存”两个更独立的信息源。只有日线近似或分钟级信号转正后，才进入共享资金组合和滚动窗口。
4. 分钟数据源不能直接进入全量回测：Tushare 当前频率限制过低，东财近端分钟接口不稳定且历史日期支持不足；`mootdx` 已预留 provider，但当前环境未安装依赖。下一步应优先确认是否引入并验证 `mootdx`，或限定极小候选样本做人工复核级分钟验证。

## 下一步候选实验

当前脚本支持 `tushare`、`eastmoney-recent` 和可选 `mootdx` 三个 provider。下一步若要继续分钟方向，需要先确认是否把 `mootdx` 加入依赖并验证连接质量；当前仓库没有该依赖，且分钟数据源覆盖会影响研究数据口径。

## 最新数据与 provider 诊断

### 最近日线补齐

命令：

```powershell
Invoke-RestMethod -Uri 'http://localhost:18000/api/tushare/sync-market-daily' `
  -Method Post `
  -ContentType 'application/json' `
  -Body '{"start_date":"2026-06-02","end_date":"2026-06-04","max_trade_dates":3,"skip_existing":true,"min_existing_rows":5000}'

Invoke-RestMethod -Uri 'http://localhost:18000/api/tushare/sync-market-daily-basic' `
  -Method Post `
  -ContentType 'application/json' `
  -Body '{"start_date":"2026-06-02","end_date":"2026-06-04","max_trade_dates":3,"skip_existing":true,"min_existing_rows":5000}'
```

结果：

- `stock_daily_bars` 新增/更新 `16529` 行。
- `stock_daily_basic` 新增/更新 `16529` 行。
- 本地行情和 daily_basic 覆盖已延伸到 `2026-06-04`。

### `002-tail-active-minute-best-risk-latest-dryrun-001`

结果：

- `best-risk` 口径候选日期仍为 `534`。
- 最新候选仍停在 `2026-05-26`，说明 `2026-06-02` 至 `2026-06-04` 没有触发最佳风险过滤尾盘候选。

### `002-tail-active-minute-base-latest-dryrun-001`

结果：

- `base` 口径候选日期为 `3749`。
- 最新候选包含 `2026-06-03`，但默认不包含 `2026-06-04`，因为需要次日收盘收益而最后交易日没有次日数据。

### `002-tail-active-minute-eastmoney-open-base-001`

命令：

```powershell
python scripts\research\sync_tail_minute_bars.py `
  --run-id 002-tail-active-minute-eastmoney-open-base-001 `
  --start-date 2023-01-01 `
  --end-date 2026-06-04 `
  --min-bars 120 `
  --exclude-st `
  --exclude-bj `
  --max-candidates 20 `
  --max-requests 5 `
  --profile base `
  --provider eastmoney-recent `
  --sleep-seconds 1 `
  --include-open-candidates
```

结果：

- 候选日期：`3753`
- 选中候选：`20`
- 分钟匹配：`0`
- 请求错误：`3`
- 结论：开放最后交易日后，base 口径能产生 `2026-06-04` 候选，但东财近端分钟源仍无法形成可用覆盖。

### `002-tail-active-minute-eastmoney-open-base-diagnostic-001`

结果：

- 候选日期：`3753`
- 选中候选：`5`
- 请求 `1` 只最新候选，返回 `Remote end closed connection without response`。
- 结论：东财近端分钟价格 provider 当前不稳定，不宜作为历史分钟回测数据源。

### `002-tail-active-minute-mootdx-diagnostic-001`

结果：

- 候选日期：`3753`
- 选中候选：`5`
- 请求 `1` 只最新候选，返回 `mootdx is not installed; install and verify it before using provider=mootdx`。
- 结论：脚本已预留 `mootdx` provider，但当前环境缺少依赖；是否加入依赖需单独确认。

## 数据源晋级门槛

已新增 `minute-data-source-decision.md`，把当前数据源状态和晋级门槛固化为本 session 决策：

- 日线和 daily_basic：可用于候选重建和对照组。
- 腾讯实时、同花顺热点、东财行业榜：只用于实时观察，不用于历史回放。
- 本地行业主线代理：已形成负证据，不继续收紧行业排名。
- Tushare `stk_mins`：当前账号 `1次/小时`，不能批量补三年分钟样本。
- 东财近端分钟价：断连且历史日期支持不稳定，不进入全量回测。
- `mootdx`：脚本已预留 provider，但当前环境未安装，是否引入依赖需要单独确认。

### `002-tail-active-minute-source-status-dryrun-001`

结果：

- 候选日期：`7`
- 选中候选：`5`
- `sourceStatus`：`candidate_rebuild_only`
- `canPromoteToBacktest`：`false`
- 结论：dry-run 只证明候选重建，不验证分钟源。

### `002-tail-active-minute-source-status-mootdx-001`

结果：

- 候选日期：`7`
- 选中候选：`1`
- 请求错误：`1`
- `sourceStatus`：`source_failed`
- `canPromoteToBacktest`：`false`
- 结论：当前环境未安装 `mootdx`，不能晋级分钟回测。

## 人工复核 Worklist

新增 `scripts/research/export_tail_minute_review_worklist.py`，从已经生成的 `candidate_dates.json` 固定抽取样本，不重新请求外部数据源，并输出 `worklist.json`、`worklist.csv` 和 `review.md`。CSV 预留 `manualMatchedTime`、`manual1430Price`、`manualSource`、`manualCheckedAt`、`manualNotes`，用于人工或半自动核对 `14:30` 入场价。

### `002-tail-active-minute-manual-worklist-best-risk-001`

来源：`002-tail-active-minute-best-risk-full-dryrun-001`

结果：

- 抽取样本：`20`
- 目标时间：`14:30:00`
- 口径：`best-risk` 严格候选。
- 结论：固定严格候选样本，供后续核对分钟价覆盖率；不作为收益回测结论。

### `002-tail-active-minute-manual-worklist-latest-base-001`

来源：`002-tail-active-minute-base-latest-dryrun-001`

结果：

- 抽取样本：`20`
- 目标时间：`14:30:00`
- 口径：`base` 近端候选，包含 `2026-06-03` 样本。
- 结论：固定最新近端样本，便于验证行情页面或后续 provider 是否能稳定取到最近历史分钟价；不作为收益回测结论。

## Mootdx 分页分钟源验证

新增 `minute-mootdx-probe-report.md`，记录 `mootdx` 在线 1 分钟 K 线 provider 修正与近端收益对照。

实现修正：

- `mootdx` 在线 K 线使用 `frequency=KLINE_1MIN`，不是旧的 `category=7`。
- 新增 `--mootdx-pages`，按 `start=page*800` 分页向前取分钟线。
- 新增 `backend/requirements.txt` 依赖 `mootdx==0.11.7`；正式镜像重建因 Docker 镜像代理 `429 Too Many Requests` 暂未完成。

### `002-tail-active-minute-mootdx-best-risk-paged-002`

结果：

- 窗口：`2026-04-01` 至 `2026-06-04`
- 口径：`best-risk`
- 选中候选：`25`
- 分钟匹配：`25`
- 覆盖率：`100.00%`
- `sourceStatus`：`probe_passed`
- `canPromoteToBacktest`：`true`

### `002-tail-active-minute-mootdx-best-risk-apr-jun-001`

结果：

- 窗口：`2026-04-01` 至 `2026-06-04`
- 口径：`best-risk`
- 选中候选：`52`
- 分钟匹配：`52`
- 覆盖率：`100.00%`
- 分钟入场至次日收盘：均值 `0.29%`，中位数 `-0.33%`
- 匹配样本日线收盘入场至次日收盘：均值 `0.17%`，中位数 `0.23%`
- 分钟胜率：`25/52 = 48.08%`
- `canPromoteToBacktest`：`true`

### `002-tail-active-minute-mootdx-base-apr-jun-001`

结果：

- 窗口：`2026-04-01` 至 `2026-06-04`
- 口径：`base`
- 选中候选：`52`
- 分钟匹配：`52`
- 覆盖率：`100.00%`
- 分钟入场至次日收盘：均值 `-0.05%`，中位数 `0.09%`
- 匹配样本日线收盘入场至次日收盘：均值 `-0.34%`，中位数 `-0.64%`
- 分钟胜率：`26/52 = 50.00%`
- `canPromoteToBacktest`：`true`

### `002-tail-active-minute-mootdx-container-probe-001`

结果：

- 在当前运行中的 `api` 容器临时安装 `mootdx==0.11.7` 后执行。
- 选中候选：`3`
- 分钟匹配：`3`
- 覆盖率：`100.00%`
- 结论：容器运行口径可走通，但样本数不足，不作为晋级样本。

当前结论：

- `mootdx` 已从“未安装”推进为“近端小样本分钟源通过”。
- 严格 `14:30` 价没有把策略直接变成合格候选；best-risk 近端均值略正但中位数仍负，base 中位数略正但均值受尾部亏损拖累。
- 下一步应扩大分钟样本窗口和缓存口径，而不是继续收紧日线参数。

## 三个月分钟参数对照

新增 `tail-active-validation-plan.md`，把尾盘活跃次日纪律策略拆成阶段门槛：日线候选、分钟源准入、三个月分钟对照、扩大分钟窗口、组合级验证、阶段结论。

### `002-tail-active-minute-best-risk-mar-jun-dryrun-001`

结果：

- 窗口：`2026-03-01` 至 `2026-06-04`
- 口径：`best-risk`
- 候选日期：`71`
- 结论：三个月窗口候选规模可控，可跑全量分钟验证。

### `002-tail-active-minute-base-mar-jun-dryrun-001`

结果：

- 窗口：`2026-03-01` 至 `2026-06-04`
- 口径：`base`
- 候选日期：`297`
- 结论：base 全量分钟验证成本更高；本轮先取最新 `71` 条与 best-risk 同规模对照。

### `002-tail-active-minute-mootdx-best-risk-mar-jun-001`

结果：

- 窗口：`2026-03-01` 至 `2026-06-04`
- 口径：`best-risk`
- 选中候选：`71`
- 分钟匹配：`71`
- 覆盖率：`100.00%`
- 分钟收益均值：`0.23%`
- 分钟收益中位数：`-0.17%`
- 分钟胜率：`47.89%`
- profit factor：`1.167`
- 月度分段：`2026-03` 均值 `-0.78%`，`2026-04` 均值 `0.98%`，`2026-05` 均值 `-0.18%`
- 结论：风险过滤改善了均值和尾部，但中位数仍为负，不能进入组合级候选。

### `002-tail-active-minute-mootdx-base-mar-jun-n71-001`

结果：

- 窗口：`2026-03-01` 至 `2026-06-04`
- 口径：`base`
- 候选日期：`297`
- 选中候选：`71`
- 分钟匹配：`71`
- 覆盖率：`100.00%`
- 分钟收益均值：`-0.39%`
- 分钟收益中位数：`-0.07%`
- 分钟胜率：`46.48%`
- profit factor：`0.814`
- 月度分段：`2026-05` 均值 `-0.24%`，`2026-06` 均值 `-1.54%`
- 结论：base 同规模对照弱于 best-risk，说明入场风险过滤有边际价值，但不足以让策略达标。

## 最大在线窗口分钟验证

新增 `tail-active-interim-conclusion.md`，给出当前阶段中期结论：`观察，不进入组合级候选`。

### `002-tail-active-minute-best-risk-dec-jun-dryrun-001`

结果：

- 窗口：`2025-12-01` 至 `2026-06-04`
- 口径：`best-risk`
- 候选日期：`122`
- 结论：6 个月候选规模可控，但 `mootdx` 在线分钟源无法完整覆盖 2025-12。

### `002-tail-active-minute-base-dec-jun-dryrun-001`

结果：

- 窗口：`2025-12-01` 至 `2026-06-04`
- 口径：`base`
- 候选日期：`626`
- 结论：base 全量规模明显高于 best-risk，仍只适合作为等量对照。

### `mootdx` 在线深度探测

以 `603890` 为例，`mootdx` 1 分钟在线翻页：

- `start=21600`：`2026-01-12 13:41` 至 `2026-01-15 15:00`
- `start=22400`：`2026-01-07 10:51` 至 `2026-01-12 13:40`
- `start=23200`：`2026-01-07 09:31` 至 `2026-01-07 10:50`
- `start=24000`：无数据

结论：当前在线源可支撑约 `2026-01-07` 之后的近端验证，不足以完成从 `2025-12-01` 开始的完整 6 个月分钟回测。

### `002-tail-active-minute-best-risk-jan-jun-dryrun-001`

结果：

- 窗口：`2026-01-07` 至 `2026-06-04`
- 口径：`best-risk`
- 候选日期：`99`

### `002-tail-active-minute-base-jan-jun-dryrun-001`

结果：

- 窗口：`2026-01-07` 至 `2026-06-04`
- 口径：`base`
- 候选日期：`491`

### `002-tail-active-minute-mootdx-best-risk-jan-jun-001`

结果：

- 选中候选：`99`
- 分钟匹配：`98`
- 覆盖率：`98.99%`
- 分钟收益均值：`-0.08%`
- 分钟收益中位数：`-0.67%`
- 分钟胜率：`44.90%`
- profit factor：`0.951`
- 月度分段：仅 `2026-04` 为正；`2026-01`、`2026-02`、`2026-03`、`2026-05` 均未确认稳定优势。
- 结论：扩大窗口后触发停止条件，不能进入组合级候选。

### `002-tail-active-minute-mootdx-base-jan-jun-n99-001`

结果：

- 选中候选：`99`
- 分钟匹配：`99`
- 覆盖率：`100.00%`
- 分钟收益均值：`-0.63%`
- 分钟收益中位数：`-0.84%`
- 分钟胜率：`40.40%`
- profit factor：`0.696`
- 结论：base 明显弱于 best-risk；风险过滤有相对价值，但不能让策略达标。
