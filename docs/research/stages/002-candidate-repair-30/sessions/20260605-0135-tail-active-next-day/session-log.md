# Session Log

## 2026-06-05 01:35 +08:00

- 建立尾盘活跃次日纪律策略研究 session。
- 明确第一阶段只做日线近似回测，不做严格 `14:30` 分钟级历史表。
- 本地 PostgreSQL 与 Docker Desktop 均不可用，真实回测暂时阻塞。
- 已准备参数网格脚本和恢复后命令。

## 2026-06-05 01:55 +08:00

- Docker Desktop 与本地 PostgreSQL 已恢复，确认日线和 daily_basic 样本覆盖 `2023-05-29` 至 `2026-05-29`。
- 运行基础网格 `002-tail-active-grid-pilot-001`：最佳组合完成交易 `613` 笔，正收益率 `29.94%`，中位收益 `-0.57%`，平均收益 `-0.31%`。
- 运行风险过滤细化 `002-tail-active-risk-pilot-001`：最佳组合完成交易 `109` 笔，正收益率 `43.48%`，中位收益 `-0.22%`，平均收益 `-0.11%`。
- 当前结论为观察：活跃度和入场风险过滤能降低回撤，但还不能形成正期望，不应推进为组合级候选。

## 2026-06-05 02:16 +08:00

- 补充历史可复现主线代理：按本地日线计算同日行业平均涨幅、上涨比例和行业排名，只在 `tailMainlineFilter.enabled=true` 时参与尾盘入场过滤。
- 运行小样本主线细化 `002-tail-active-mainline-pilot-001`：最佳组合完成交易 `9` 笔，正收益率 `44.44%`，中位收益 `-0.21%`，平均收益 `-0.10%`。
- 运行全市场最佳风险过滤验证 `002-tail-active-best-risk-full-001`：测试 `4912` 只，完成交易 `530` 笔，正收益率 `39.65%`，中位收益 `-0.21%`，平均收益 `-0.12%`。
- 运行全市场主线细化 `002-tail-active-mainline-full-001`：最佳组合完成交易 `76` 笔，正收益率 `38.03%`，中位收益 `-0.23%`，平均收益 `-0.13%`。
- 当前结论仍为观察/未通过：粗行业主线代理没有改善收益中枢，下一步应研究 `14:30` 分钟级入场价或题材持续性历史缓存。

## 2026-06-05 02:41 +08:00

- 新增 `scripts/research/sync_tail_minute_bars.py`，用本地日线重建尾盘候选日期，并用 run-local `minute_cache.jsonl` 探测 `14:30` 分钟入场价覆盖率，不写数据库。
- 运行 `002-tail-active-minute-best-risk-full-dryrun-001`：完整窗口重建候选日期 `534` 个，与全市场最佳风险过滤 run 的 `530` 笔完成交易规模接近。
- 运行 `002-tail-active-minute-sample-001`：Tushare `stk_mins` 请求失败，当前账号频率限制为 `1次/小时`，不适合批量补分钟价。
- 运行 `002-tail-active-minute-eastmoney-sample-001`：东财近端分钟价格接口本次断连；此前手工探测显示历史日期参数支持不稳定。
- 当前结论：分钟入场价探测脚本已具备，但可用数据源尚未通过，不能进入全量分钟回测。下一步若继续，需要确认是否引入 `mootdx` 或仅做极小样本人工复核级分钟验证。

## 2026-06-05 02:59 +08:00

- 通过 API 只补 `2026-06-02` 至 `2026-06-04` 最近三天日线与 daily_basic，各 upsert `16529` 行，本地覆盖延伸到 `2026-06-04`。
- 运行 `002-tail-active-minute-best-risk-latest-dryrun-001`：best-risk 候选仍为 `534` 个，最新候选停在 `2026-05-26`。
- 运行 `002-tail-active-minute-base-latest-dryrun-001`：base 候选为 `3749` 个，最新候选到 `2026-06-03`。
- 为匹配东财近端分钟源，新增 `--include-open-candidates`，允许最后交易日只做分钟覆盖/收盘价差验证，不计算次日收益。
- 运行 `002-tail-active-minute-eastmoney-open-base-001`：base 开放候选为 `3753` 个，含 `2026-06-04` 候选，但东财近端分钟源 `5` 次请求中 `3` 次断连，分钟匹配 `0`。
- 新增可选 `mootdx` provider，不改依赖；运行 `002-tail-active-minute-mootdx-diagnostic-001` 证明当前环境未安装 `mootdx`。
- 当前结论：最近日线已补齐，脚本结构支持三个 provider，但可用分钟源仍未通过，不能进入分钟级全量回测。

## 2026-06-05 03:06 +08:00

- 增强 `scripts/research/sync_tail_minute_bars.py` 输出，新增 `sourceStatus`、`sourceStatusReason` 和 `canPromoteToBacktest`，防止未通过分钟源被误推进全量回测。
- 运行 `002-tail-active-minute-source-status-dryrun-001`，确认 dry-run 输出 `candidate_rebuild_only` 且 `canPromoteToBacktest=false`。
- 运行 `002-tail-active-minute-source-status-mootdx-001`，确认当前未安装 `mootdx` 时输出 `source_failed` 且 `canPromoteToBacktest=false`。
- 新增 `minute-data-source-decision.md`，固化分钟源决策矩阵和晋级门槛。

## 2026-06-05 03:11 +08:00

- 新增 `scripts/research/export_tail_minute_review_worklist.py`，从已有候选 dry-run 固定抽取人工复核样本，不重新请求外部分钟源。
- 生成 `002-tail-active-minute-manual-worklist-best-risk-001`：来源为严格 `best-risk` 候选，抽取 `20` 条。
- 生成 `002-tail-active-minute-manual-worklist-latest-base-001`：来源为近端 `base` 候选，抽取 `20` 条，包含 `2026-06-03` 样本。
- 当前结论不变：人工复核清单只能验证数据源覆盖与取价口径，不能替代 `canPromoteToBacktest=true` 的分钟级全量回测。

## 2026-06-05 03:26 +08:00

- 使用 Context7 核对 `mootdx` 当前文档：在线 K 线应使用 `frequency` 参数，1 分钟常量为 `KLINE_1MIN`。
- 修正 `scripts/research/sync_tail_minute_bars.py` 的 `mootdx` provider，从 `category=7` 改为 `frequency=KLINE_1MIN`，并新增 `--mootdx-pages` 分页取数。
- 运行 `002-tail-active-minute-mootdx-best-risk-paged-002`：`25/25` 匹配，`sourceStatus=probe_passed`，`canPromoteToBacktest=true`。
- 运行 `002-tail-active-minute-mootdx-best-risk-apr-jun-001`：`52/52` 匹配，分钟收益均值 `0.29%`，中位数 `-0.33%`。
- 运行 `002-tail-active-minute-mootdx-base-apr-jun-001`：`52/52` 匹配，分钟收益均值 `-0.05%`，中位数 `0.09%`。
- 新增 `backend/requirements.txt` 依赖 `mootdx==0.11.7`；`docker compose build api` 因 Docker 镜像代理返回 `429 Too Many Requests` 未完成。
- 本机临时安装的 `mootdx` 已卸载，`httpx` 恢复到 `0.28.1`，`python -m pip check` 通过。
- 当前运行中的 `api` 容器临时安装 `mootdx==0.11.7` 后跑通 `002-tail-active-minute-mootdx-container-probe-001`，证明容器口径脚本可运行；正式复现仍需镜像重建成功。

## 2026-06-05 03:38 +08:00

- 运行 `002-tail-active-minute-best-risk-mar-jun-dryrun-001`：`2026-03-01` 至 `2026-06-04` 的 `best-risk` 候选为 `71` 条。
- 运行 `002-tail-active-minute-base-mar-jun-dryrun-001`：同窗口 `base` 候选为 `297` 条。
- 运行 `002-tail-active-minute-mootdx-best-risk-mar-jun-001`：`71/71` 分钟匹配，分钟收益均值 `0.23%`，中位数 `-0.17%`，profit factor `1.167`。
- 运行 `002-tail-active-minute-mootdx-base-mar-jun-n71-001`：最新 `71` 条 `base` 候选 `71/71` 分钟匹配，分钟收益均值 `-0.39%`，中位数 `-0.07%`，profit factor `0.814`。
- 新增 `tail-active-validation-plan.md`，明确当前策略的阶段门槛、停止条件和下一步 run 顺序。
- 验证 `python -m py_compile scripts\research\sync_tail_minute_bars.py scripts\research\export_tail_minute_review_worklist.py`、容器内同路径编译和 `git diff --check` 通过；`docker compose build api` 仍因镜像代理 `429 Too Many Requests` 未完成。
- 当前结论：`best-risk` 明显优于 `base` 同规模对照，但中位数仍为负，只能继续观察，不能进入组合级候选。

## 2026-06-05 03:49 +08:00

- 运行 6 个月 dry-run：`002-tail-active-minute-best-risk-dec-jun-dryrun-001` 候选 `122` 条，`002-tail-active-minute-base-dec-jun-dryrun-001` 候选 `626` 条。
- 探测 `mootdx` 在线 1 分钟翻页深度：以 `603890` 为例，最早可取到约 `2026-01-07 09:31`，`start=24000` 无数据，无法完整覆盖 `2025-12`。
- 运行最大在线窗口 dry-run：`002-tail-active-minute-best-risk-jan-jun-dryrun-001` 候选 `99` 条，`002-tail-active-minute-base-jan-jun-dryrun-001` 候选 `491` 条。
- 运行 `002-tail-active-minute-mootdx-best-risk-jan-jun-001`：`98/99` 匹配，均值 `-0.08%`，中位数 `-0.67%`，profit factor `0.951`。
- 运行 `002-tail-active-minute-mootdx-base-jan-jun-n99-001`：`99/99` 匹配，均值 `-0.63%`，中位数 `-0.84%`，profit factor `0.696`。
- 新增 `tail-active-interim-conclusion.md`，结论为观察且不进入组合级候选；下一步不再继续调同一组尾盘日线阈值。

## 2026-06-05 03:52 +08:00

- 新增 `completion-audit.md`，逐项核对用户目标：数据源口径、策略原型、阶段计划、小样本/全市场验证、参数比较、下一步路径和工程验证状态。
- 审计结论：session 级研究闭环已完成，策略结论为观察且不进入组合级候选；唯一剩余 caveat 是 Docker 镜像代理 `429 Too Many Requests` 导致 `api` 镜像正式重建未完成。
