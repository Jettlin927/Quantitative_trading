# Mootdx 14:30 Minute Probe Report

## Context

尾盘活跃次日纪律策略的关键缺口是严格 `14:30` 入场价。此前日线回测使用收盘价近似，Tushare `stk_mins` 因账号频率限制无法批量补样本，东财近端分钟源多次断连。

## Provider Fix

`mootdx` 在线 K 线接口应使用 `frequency=KLINE_1MIN`。此前脚本使用 `category=7`，实际返回了日级聚合，导致分钟匹配为 `0`。修正后新增 `--mootdx-pages`，按 `start=page*800` 分页向前取 1 分钟 K 线。

## Source Probe

| Run | Window | Profile | Selected | Matches | Coverage | Status |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `002-tail-active-minute-mootdx-best-risk-jan-jun-001` | 2026-01-07 to 2026-06-04 | best-risk | 99 | 98 | 98.99% | `probe_passed` |
| `002-tail-active-minute-mootdx-base-jan-jun-n99-001` | 2026-01-07 to 2026-06-04 | base | 99 | 99 | 100.00% | `probe_passed` |
| `002-tail-active-minute-mootdx-best-risk-mar-jun-001` | 2026-03-01 to 2026-06-04 | best-risk | 71 | 71 | 100.00% | `probe_passed` |
| `002-tail-active-minute-mootdx-base-mar-jun-n71-001` | 2026-03-01 to 2026-06-04 | base | 71 | 71 | 100.00% | `probe_passed` |
| `002-tail-active-minute-mootdx-best-risk-paged-002` | 2026-04-01 to 2026-06-04 | best-risk | 25 | 25 | 100.00% | `probe_passed` |
| `002-tail-active-minute-mootdx-best-risk-apr-jun-001` | 2026-04-01 to 2026-06-04 | best-risk | 52 | 52 | 100.00% | `probe_passed` |
| `002-tail-active-minute-mootdx-base-apr-jun-001` | 2026-04-01 to 2026-06-04 | base | 52 | 52 | 100.00% | `probe_passed` |
| `002-tail-active-minute-mootdx-container-probe-001` | 2026-06-01 to 2026-06-04 | base | 3 | 3 | 100.00% | `insufficient_coverage` |

`002-tail-active-minute-mootdx-container-probe-001` 只用于证明后端容器内脚本可运行；样本数不足，不作为晋级样本。

## Return Comparison

| Run | Avg minute return | Median minute return | Minute win rate | Avg daily return matched | Median daily return matched | Avg daily close vs 14:30 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `best-risk-jan-jun-001` | -0.08% | -0.67% | 44.90% | -0.26% | -1.09% | 0.17% |
| `base-jan-jun-n99-001` | -0.63% | -0.84% | 40.40% | -0.69% | -0.83% | 0.06% |
| `best-risk-mar-jun-001` | 0.23% | -0.17% | 47.89% | 0.05% | -0.67% | 0.17% |
| `base-mar-jun-n71-001` | -0.39% | -0.07% | 46.48% | -0.57% | -0.56% | 0.19% |
| `best-risk-apr-jun-001` | 0.29% | -0.33% | 48.08% | 0.17% | 0.23% | 0.12% |
| `base-apr-jun-001` | -0.05% | 0.09% | 50.00% | -0.34% | -0.64% | 0.30% |

## Three-Month Detail

| Run | Profit factor | Best 5 | Worst 5 | Month split |
| --- | ---: | --- | --- | --- |
| `best-risk-jan-jun-001` | 0.951 | 10.87%, 9.99%, 9.93%, 9.63%, 7.36% | -12.64%, -10.99%, -7.88%, -6.75%, -6.62% | 2026-01: -1.14% avg / -2.41% med; 2026-02: -0.36% avg / -1.51% med; 2026-03: -0.98% avg / -1.60% med; 2026-04: 0.98% avg / 0.37% med; 2026-05: -0.18% avg / -1.11% med |
| `base-jan-jun-n99-001` | 0.696 | 10.13%, 9.99%, 9.43%, 7.08%, 7.03% | -15.12%, -13.29%, -12.64%, -12.14%, -11.21% | 2026-05: -0.55% avg / -0.89% med; 2026-06: -1.54% avg / -0.32% med |
| `best-risk-mar-jun-001` | 1.167 | 10.87%, 9.99%, 9.63%, 7.36%, 6.55% | -12.64%, -10.99%, -5.33%, -3.83%, -3.82% | 2026-03: -0.78% avg / -1.36% med; 2026-04: 0.98% avg / 0.37% med; 2026-05: -0.18% avg / -1.11% med |
| `base-mar-jun-n71-001` | 0.814 | 10.13%, 9.99%, 9.43%, 7.08%, 7.03% | -15.12%, -13.29%, -12.64%, -12.14%, -11.21% | 2026-05: -0.24% avg / -0.07% med; 2026-06: -1.54% avg / -0.32% med |

## Interpretation

- `mootdx` 分页源已通过小样本分钟覆盖门槛，可以作为下一步近端分钟验证 provider。
- `14:30` 入场价相对收盘价没有稳定改善收益中枢：best-risk 最大在线窗口均值和中位数均为负。
- 风险过滤仍有相对价值：best-risk 弱于门槛但强于 base，同规模 base 的 profit factor 只有 `0.696`。
- `mootdx` 在线源不能完整覆盖从 `2025-12-01` 开始的 6 个月样本；当前最大在线验证窗口约从 `2026-01-07` 开始。
- 下一步不应继续靠收紧日线参数追收益；应转向题材持续性、涨停结构和市场退潮过滤，或先建立分钟缓存工程后再做更长历史验证。

## Engineering Notes

- 已把 `mootdx==0.11.7` 加入 `backend/requirements.txt`，后续应通过后端容器复现。
- 本机 Anaconda 曾临时安装 `mootdx` 做探测；随后已卸载，并恢复 `httpx==0.28.1`，`python -m pip check` 通过。
- `docker compose build api` 因镜像代理返回 `429 Too Many Requests` 未完成正式镜像重建。
- 为验证容器口径，已在当前运行中的 `api` 容器临时安装 `mootdx==0.11.7` 并跑通 `002-tail-active-minute-mootdx-container-probe-001`。该临时安装不是持久镜像，正式复现仍需待 Docker 基础镜像拉取恢复后重建。
