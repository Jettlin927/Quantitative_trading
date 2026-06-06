# Tail Active Next-Day Completion Audit

## Audit Time

`2026-06-05 03:52 +08:00`

## Objective

把尾盘活跃次日纪律策略完善为可复现研究目标：补齐可用数据源口径，设计阶段性验证计划，运行小样本/全市场回测，比较参数方案并沉淀下一步优化路径。

## Requirement Audit

| Requirement | Evidence | Status | Notes |
| --- | --- | --- | --- |
| 补齐可用数据源口径 | `minute-data-source-decision.md`、`minute-mootdx-probe-report.md` | Done with boundary | 日线/daily_basic 可用于候选和近似回测；Tushare 分钟受限；东财近端分钟不稳定；`mootdx` 分页 1 分钟源可用于近端严格 `14:30` 验证，但不能完整覆盖三年分钟历史。 |
| 实现策略原型 | `backend/app/backtest_engine.py`、`backend/app/main.py`、`frontend/src/main.jsx` | Done | 已新增 `tail-active-next-day` 入场模式、量比/换手/近涨停/涨幅过滤、次日未涨停退出纪律和前端参数入口。 |
| 设计阶段性验证计划 | `tail-active-validation-plan.md` | Done | 已定义日线候选、分钟源准入、三个月对照、扩大窗口、组合级验证和阶段结论门槛。 |
| 运行小样本回测 | `002-tail-active-grid-pilot-001`、`002-tail-active-risk-pilot-001`、`002-tail-active-minute-mootdx-best-risk-paged-002` | Done | 小样本显示风险过滤有边际改善，但不是稳定正期望。 |
| 运行全市场/扩大样本验证 | `002-tail-active-best-risk-full-001`、`002-tail-active-minute-mootdx-best-risk-jan-jun-001` | Done with source limit | 日线全市场三年近似验证完成；严格分钟验证受 `mootdx` 在线历史深度限制，只能覆盖约 `2026-01-07` 至 `2026-06-04`。 |
| 比较参数方案 | `run_tail_active_grid.py`、`minute-mootdx-probe-report.md`、`tail-active-interim-conclusion.md` | Done | 当前相对最优为 `best-risk`，但长窗口严格分钟均值 `-0.08%`、中位数 `-0.67%`、profit factor `0.951`，未达组合级门槛。 |
| 沉淀下一步优化路径 | `tail-active-interim-conclusion.md` | Done | 已明确不继续调同一组日线阈值，下一步转向题材持续性、涨停结构、市场退潮过滤、次日开盘处置新口径和分钟缓存工程。 |
| 验证工程可运行 | 操作日志、最近验证命令 | Partially done | Python 编译、本机/容器脚本编译、前端 build 已通过；`api` 镜像正式重建因 Docker 镜像代理 `429 Too Many Requests` 未完成。当前运行容器已临时安装 `mootdx` 并跑通 provider，但这不是持久镜像验证。 |

## Final Research State

- 结论类型：`观察`
- 当前最优观察预设：`best-risk`
- 是否阶段通过：否
- 是否进入组合级候选：否
- 是否继续同一组尾盘日线阈值调参：否

## Current Best Preset

```json
{
  "entryMode": "tail-active-next-day",
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

## Remaining Engineering Caveat

`backend/requirements.txt` 已加入 `mootdx==0.11.7`，但 `docker compose build api` 仍因 Docker 镜像代理请求 `python:3.12-slim` 返回 `429 Too Many Requests` 而无法完成正式镜像重建。该问题不改变当前研究结论，但影响从零重建环境的一键复现。待镜像代理恢复后，应重跑：

```powershell
docker compose build api
docker compose run --rm api python scripts/research/sync_tail_minute_bars.py --help
```

## Completion Judgment

研究目标已形成可复现的 session 级闭环：策略假设、数据源口径、阶段计划、参数比较、日线全市场回测、严格分钟近端验证、失败门槛和下一步路径均已沉淀。剩余的 Docker 镜像重建问题是外部依赖拉取限制，应作为工程复现 caveat 跟踪，而不是继续扩大当前策略同口径参数搜索。
