# 2026-07-12 可信量化底座生产验收

> 状态：生产迁移、发布和验收已通过；独立只读反例审计在既定日频、固定 ETF sentinel 范围内给出 GO。

## 验收范围

本次只验收四项可信工程能力：研究范围级数据完整性、无未来函数、结果可复现、进程重启后可靠。分钟线、期权、新付费数据源、财务横截面严格修订历史、券商连接和真实交易继续不在范围内。

本次没有删除业务数据、历史脏数据、PostgreSQL volume 或备份，没有导入真实持仓/成交，也没有开放公网 PostgreSQL。

## 发布身份与迁移

- 本次发布时的 GitHub `main` / 生产运行时代码基线为 `c24ade495492f64ea82aa229827858cdef52cdf6`。最终验收文档提交后 `main` 可以领先该 SHA；只要领先部分仅为文档，生产运行时代码仍保持 `c24ade4...`，无需为文档提交重建容器。
- 非活动构建目录及当前活动 release：`/opt/quantitative-trading-release-20260712-0101`。
- 发布前冻结 schema fingerprint：`1956bc7ef21d73f504605089f3ec4a2a65d343ac7457c18c45b7be7828763785`，25 张表、18 个 sequence，与 baseline 精确一致。
- 生产库先显式 stamp `0001_existing_schema_baseline`，再分步升级 `0002` 至 `0006_worker_heartbeats`；重复 `upgrade head` 无操作，`alembic check` 返回无待生成变更。
- `0003` 使用 concurrent 路径删除 13 个已验证的重复普通索引；目标索引剩余 0，对应 13 个唯一守卫全部 `unique/valid/ready/live`，全库无 invalid/not-ready 索引。
- 13 个目标索引迁移前逻辑大小合计 `4,166,868,992` bytes，迁移后目标索引逻辑大小为 0。迁移窗口同时发生镜像构建，不能把这项逻辑大小直接当作 `df` 的精确净释放；根盘可用空间实测由约 `6.5 GiB` 变为约 `9.0 GiB`，当前约使用 77%。
- 迁移后 `pg_database_size` 为 `16,295,099,415` bytes，`public` schema 索引逻辑大小合计 `7,166,763,008` bytes；五张新增质量/快照/研究/worker registry 表总大小合计 `352,256` bytes。
- PostgreSQL 容器未重建，活动 volume 仍为 `quant_todo_p0_postgres_data_todo_p0`。
- 发布前全量 custom-format dump 为 `2,232,308,654` bytes，SHA-256 为 `7c13b7ec933fd0ec965f07cea57db8add43a29fc96ec9b3d53d544aed040dd14`；250 个 TOC、25 个 `TABLE DATA`，完整 `pg_restore --file=/dev/null` 读取成功。

## 核心数据迁移前后核对

迁移完成后重新执行精确 `count/min/max`；下表与迁移前预检逐项一致：

| 表 | 行数 | 最早日期 | 最晚日期 |
| --- | ---: | --- | --- |
| `stock_daily_bars` | 14,123,330 | 2010-01-04 | 2026-07-10 |
| `stock_daily_basic` | 14,033,551 | 2010-01-04 | 2026-07-10 |
| `stock_adjust_factors` | 14,051,784 | 2010-01-04 | 2026-07-10 |
| `stock_limit_prices` | 15,666,326 | 2012-01-04 | 2026-07-10 |
| `stock_financial_indicators` | 243,708 | 2010-03-31 | 2026-03-31 |
| `fund_daily_bars` | 2,910,819 | 2010-01-04 | 2026-06-29 |
| `fund_adjust_factors` | 3,026,797 | 2012-01-04 | 2026-06-29 |
| `index_daily_bars` | 12,392,117 | 2010-01-01 | 2026-07-10 |
| `trade_calendars` | 6,209 | 2010-01-01 | 2026-12-31 |

日期列口径：行情、daily basic、复权和涨跌停使用 `trade_date`；财务表使用 `end_date`；交易日历使用 `cal_date`。

## 数据完整性证据

- 固定切片：`etf_time_series`，`510300.SH`，2025-12-01 至 2025-12-31，基准 `000300.SH`。
- 质量运行 ID：`4930ff05-a332-4a62-b7a8-1c7479126bca`。
- 状态：`ready`；30 条规则全部通过，blocker/warning/failed 均为 0。
- 23 个开市日中，ETF 日线、ETF 复权因子和基准指数日线各 23 条；缺口、非法 OHLC 和自然键重复均为 0。
- inventory 接口仍明确返回 `researchReady=false`；只有绑定上述质量运行的 research readiness 才返回 `researchReady=true`，没有把“表存在”误写为研究就绪。

## 冻结输入与可复现证据

- 输入快照 ID：`cb9bac39488283a13e5d31604471841b7ac5311e0e5852f1d9ac8d0639152dab`。
- 快照事务：PostgreSQL `REPEATABLE READ + READ ONLY`。
- 研究运行 ID：`a22fb663-1b66-4579-ab58-e6d3236d1843`。
- 可复现键：`ddbaa1b1c19793c3bb55db107d634935de03025cdf69c14334994ddad694d9b3`。
- 结果指纹：`61aa690cc0f7ea6e1b090cbbdae359696a74ad5434266167c175b6453bbe5079`。
- `metrics.json` 内容 SHA-256：`9cb69b045c02cf52bcf4b65d705a93d1d03e4d49c5c3a30e1e5896baad7a733f`。
- 将 `DATABASE_URL` 刻意指向不可连接的 `127.0.0.1:1` 后连续执行两次 reproduce；两次实际结果指纹均与期望完全相同，`matches=true`、`mismatches=[]`。

冻结输入登记如下；`content SHA` 是未压缩 canonical 内容哈希，`file SHA` 是实际 gzip 文件哈希：

| 输入 | 行数 | content SHA-256 | file SHA-256 |
| --- | ---: | --- | --- |
| `fund_adjust_factors` | 23 | `50aad79ef64d5cee3ec9e6036a750b8ddd5bfb1a544b10f6a95ee02f3bb9f2e2` | `ccbb1004ef96fa1fd29e9cc60194421b7aeaa61ae423e3cd6dbabc4c89182c4a` |
| `fund_daily_bars` | 23 | `d12b23274acc9a36e2716686c7086d284b39c060be328b74975a559118e83b1c` | `d9821077e76a17e5ee47472b56e0e31e5922cb425355b8bfc4a1e0c005abb6e9` |
| `funds` | 1 | `8c4bddb1e3be44f83ae85eaea18b700e0f201fe816f38494ffa7a6c79b9a5ee4` | `930dff202625818eaac068717fa5e668b53a42ea4299bb5a4d1577962814fde0` |
| `index_daily_bars` | 23 | `98a0c0aee515f72a38fe933265fc467a02e9c50afabd0dd84d5c4b7be8d8b849` | `1b89867d7159d1ecc6a0dfbdf1cd5362994579478eed067c35c2c2edd6d76c1d` |
| `indices` | 1 | `d0af6162d3cef777b1bfae4ad2702570f016d4685db942b0d671adb85ca509dc` | `4abd03f408373a4c54ccfab1da875b8a97dcaad0cb08ff711be573bf6b9c867d` |
| `trade_calendars` | 31 | `18f4a1c14d5009ffccfed57139ee599d0f4a3649dabe68b45a0c5a51c5453257` | `4e832d3e2f493b8b1836f591eba156a4ac4513ae8998339bbb1edf99abd25d0e` |
| `universe` | 1 | `1927deef4dced0f7fc4c2f03c575fa4ae5ac4b1da758fd66b85ad4d520f41781` | `7f19b8fd617ca9f8d8351e24c68fae7079b5562170593afa99a3b0e5282fb73d` |

## 无未来函数证据

- 生产 sentinel 的唯一目标在 2025-12-05 形成，实际只在下一交易日 2025-12-08 开盘执行，没有同日收盘信号同日成交。
- 精确生产代码在隔离环境定向执行 73 项可信/复现/恢复测试：72 项通过，1 项显式 PostgreSQL URL 的续跑集成测试按设计跳过；其中未来复权因子、未来公告、公告日同日不可见、特征晚于信号、错误日历和下一交易日执行反例全部通过。
- 同一运行时代码此前已经在 PostgreSQL 16.14 全矩阵执行 162/162、0 跳过，并由非实现者重放 39/39 个自定义反例。
- 上述证据只证明当前日频研究协议与固定 sentinel 没有已知未来函数，不证明任意未来新增策略天然安全；新增 loader、特征或执行规则仍必须通过同类前缀不变测试。

## 进程重启与租约恢复证据

生产演练只使用仓库内 `isSample=true`、`brokerConnected=false`、`realHoldingsImported=false` 的 `us_sample` 幂等任务。

1. queued 演练：停止 worker 后提交任务 `f1360532-9227-408c-8f19-716768c8cce6`；重启 API 后任务仍为 `queued`、`attemptCount=0`。重新启动 worker 后任务变为 `ok`、`attemptCount=1`，租约已释放。
2. expired-running 演练：任务 `5f5dc534-b757-42e4-8193-418c17d8b62d` 由 `production-crash-rehearsal` 领取 5 秒租约后模拟进程消失；租约过期并重启 API/worker 后，新 worker 以 `attemptCount=2` 接管并完成，旧租约已释放。
3. 最终队列状态：queued/running/failed/expired lease 均为 0，worker 心跳新鲜，代码提交为 `c24ade4...`。
4. 两次 at-least-once 执行后，sample 表为 `assets=4`、`asset_daily_prices=4`、`watchlist_items=4`、`portfolio_snapshots=1`；四类自然键重复组均为 0。

## 运行状态

- `db`、`api`、`worker`、`frontend` 四容器均运行；API health、数据库连接和 frontend HTTP 均正常。
- API/worker 注入的 `APP_GIT_COMMIT` 均为 `c24ade495492f64ea82aa229827858cdef52cdf6`，工作目录挂载当前 release；研究产物位于独立 `quant_research_artifacts` volume。
- PostgreSQL、API 和前端端口只绑定 `127.0.0.1`；worker 不对宿主机暴露端口。
- 每日 20:30 定时任务已更新为从当前 release 提交并轮询持久任务。
- 回滚保留旧 release 和旧 API/frontend 镜像标签；未执行 image prune。

## 仍然存在的限制

- 基金日线和基金复权全量最新覆盖仅到 2026-06-29；不能宣称所有市场数据均新鲜到 2026-07-10。本次固定 2025-12 sentinel 不受影响。
- 当前 sentinel 是固定单 ETF、无参数搜索的管线验收，不是 alpha 研究、买卖评级或收益承诺。
- 分钟线、期权、真实美股流、券商和真实资金动作继续缺席。
- 严格使用财务因子的横截面研究，在供应商历史修订不可重建时仍必须 blocked。
- 生产恢复演练首次写入 4/4/4/1 条明确标记为 sample 的美股示例记录；没有写入真实持仓或真实成交。
- 前端容器当前仍运行 Vite `npm run dev -- --host 0.0.0.0`；宿主机端口虽只绑定 loopback，但这不等于静态生产服务器硬化已经完成。
- worker 目前在任务行聚合保存 `attempt_count`，并由容器日志记录逐次 claim/finish；尚无每次 attempt 独立持久审计表。

## 结论门禁

独立审计已确认：数据完整性、无未来函数、结果可复现和进程重启可靠四项目标，在本次明确的日频、固定 ETF sentinel 范围内均为 GO。该结论不等于分钟/期权/财务横截面、前端静态服务或逐 attempt 审计等完整生产硬化已经完成。验收证据提交 `1fe3162f4953c08fa4ad5de160994565b320406c` 已 fast-forward 推送到 GitHub `main`，对应 CI run `29161789513` 的 SQLite/Python、PostgreSQL 16、前端和仓库门禁四个 job 全部成功；本 Goal 的完成条件已经满足。
