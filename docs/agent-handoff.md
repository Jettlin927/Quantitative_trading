# Agent Handoff

这份文档给后续 Agent 接手可信量化研究底座用。规则仍以根目录 `AGENTS.md` 为准；所有运行状态都应现场复查，不能只信本文。

## 当前接手状态

- 本地仓库：`/Users/jettlin/code/Quantitative_trading`；用户常用 Windows 副本路径仍可能是 `E:\coding_things\Quantitative_trading`。接手必须现场运行 `git status -sb`、`git log -10 --oneline` 和 `git rev-list --left-right --count origin/main...HEAD`，不要从本文猜当前分支或远端提交。
- 2026-07-13 的量化研究能力补齐已实现静态策略分发、因果特征、可审计模拟账本、A 股历史行业成员 baseline、OOS-only walk-forward、透明风险工件、确定性约束分配和风险数据 readiness。该轮只授权验证后合并/推送代码，不包含生产部署、生产研究运行或数据库迁移；生产运行时代码仍须与仓库最新 `main` 分开核对。
- 新增能力已在精确代码提交 `891b2825b62c8e91576ee54d04fbafc738c95f69` 完成最终验收：本地隔离 PostgreSQL 16.14 全量 219/219、0 跳过，固定黑盒审计 12/12；远端 `/tmp` 隔离源码复用现有 API 镜像完成 SQLite 全量 219 项、10 项 PG-only 按设计跳过，未连接生产数据库或重启服务。黄金 A 股合成运行结果指纹为 `ca9243de1c5fb8599cc589710f63c8358e9f6c9e1c5e1e0b261a0e200df71806`，连续两次断库 reproduce 匹配。
- 上述实现与验收记录已 fast-forward 合并并推送至 `main@e51bbf37f528b4a9ea3df5ba2ae394d228ab2b6b`；GitHub CI run `29232720679` 的后端 SQLite、前端、PostgreSQL 16、Compose/Shell 四个 job 全部成功。该动作不代表生产服务器已部署新代码。
- 可信工程的既有生产运行时代码为 `c24ade495492f64ea82aa229827858cdef52cdf6`。GitHub `main` 后续已有文档提交；接手不能把“仓库最新提交”写成“生产已部署”。
- 2026-07-19 最终 canonical 研究代码身份为 `26da0d347d77de7ee03a95277fc4ad45bdaa983a`：统一 OOS 首日、被动基准、walk-forward、子区间净值/回撤、HAC 诊断与报告归档边界，并拒绝路径逃逸及已声明摘要损坏。该提交及其后续文档仍未部署到生产；接手必须现场核对当前分支、`main` 和 production runtime。
- 当前目标：数据完整性、无未来函数、结果可复现、进程重启可靠四条可信门禁。分钟线、期权、新付费源、券商和真实交易继续暂缓。
- Phase 0–5 已完成。首次独立审计发现的 4 个问题修复后，同一审计者在精确提交 `f506d0e58c303afe7ad561b37ceff27c6e5e681f` 重放 39/39 反例；PostgreSQL 16.14 全矩阵 162/162、0 跳过。生产发布后又在精确部署代码上执行 73 项定向门禁，72 项通过、1 项显式 PG URL 用例按设计跳过。
- 运行时代码对应的 GitHub Actions CI run `29158046019` 四个 job 全部成功；生产验收证据提交 `1fe3162f4953c08fa4ad5de160994565b320406c` 已 fast-forward 推送 `main`，对应 CI run `29161789513` 的四个 job 也全部成功。生产 quality、snapshot、sentinel、断库 reproduce 和 worker 重启恢复的完整证据见 `docs/deployment/2026-07-12-production-trustworthiness-acceptance.md`。
- 生产 PostgreSQL 已在用户明确确认后完成 fingerprint 门禁、baseline stamp、`0002→0006` 和 13 个普通重复索引清理。后续新的生产 DDL、数据删除、volume 操作或覆盖恢复仍必须再次确认。

## 2026-07-13 至 2026-07-19 新增研究能力

- `scripts/research/run_quant_research.py --list-strategies` 不连接数据库，列出 `sentinel_etf_baseline@1`、`etf_trend_120d@1`、`etf_volatility_managed@1`、`etf_low_volatility_gate@1`、`a_share_price_baseline@1`、`a_share_b1_trend_pullback@1` 的 scope、必需冻结输入和示例配置。
- `etf_low_volatility_gate@1` 最终基础/双倍成本 run 为 `251662f5-def5-4228-9330-e68e13a47748`、`7e9ea891-45db-4885-9378-d27dadc58cb0`。基础成本 100,000 元期末约 129,035 元、CAGR 3.17%、最大回撤 -52.82%，结论仍为 `不通过`。
- `etf_volatility_managed@1` 最终六个 run 为：T0 `f24663b1-4160-465f-b9e8-ea295c2407a0`、T1 `854aa0e6-672f-4d7c-b330-1f9586c507dd`、T2 `5d082e36-6ce0-4e02-8d43-d1b2686cf9dd`、T3 `164e3704-8f42-4072-aea1-d5b1532a3049`、零成本 `b5cd6613-d822-434a-a913-983571708c78`、双倍成本 `14f79545-0ea6-474c-8faa-2e83a016283b`。T0 期末约 152,552 元、最大回撤 -40.89%；状态为 `不通过`。
- `etf_trend_120d@1` 最终基础、零、双倍成本 run 为 `73c82e27-754f-4f6a-bc85-4fc43c4b5be3`、`0e3af953-a064-4db2-beb3-0a84416f6ce8`、`7d5e9489-78dc-4b32-94a7-b264c16be486`，共享 snapshot `5552b240062a2d9f549770830aefe614f481e64f1e98df03f49357610670653e`。3303 个开市日对应 3303 个收益观察；基础成本 CAGR 0.05%、最大回撤 -52.82%，同期被动 ETF CAGR 8.32%，结论为 `不通过`。
- `a_share_b1_trend_pullback@1` 最终五个 run 为：网页机械口径 `fd68d6c7-1338-47ba-8bca-7ccaa9cc3713`、同周期现实成交 `74dd5a99-932b-4e00-8197-fe82419c8c15`、长历史主版本 `d13d510b-67df-4a97-97da-8ff387f357db`、页面参数一致性 `3d90dcc2-c14a-4af4-acf1-959e6cc4e683`、双倍成本 `36c194a7-3d45-47ae-9593-ecd46bf29a84`。长历史期末约 26,649 元、CAGR -9.31%、最大回撤 -90.99%，只能表述为“近似复现不通过”。
- 上述 16 个运行均绑定代码提交 `26da0d347d77de7ee03a95277fc4ad45bdaa983a` 和镜像 `sha256:5061ca1a590f626ae4bfff58c24a0c9f07a9b62be8cf6ef554abcf3748bdbb3d`；每个运行在 `--network none` 下连续复现 2 次，16/16 × 2 的 result fingerprint 全部匹配。完整两轮总账在 `docs/research/strategy-results/reproduction-evidence-20260719.json`；三个生成器会逐项校验各自运行子集，并从最新 canonical manifest 派生独立的 `reportGeneratedAt`。
- artifact schema v2 的公共 runner 同时生成 `targets/nav`、调仓请求、模拟执行和 positions；walk-forward 与风险工件按配置成对出现并进入 checkpoint、manifest 和结果指纹。已完成 v1 归档保持兼容，未完成 v1 不跨版本续跑。
- A 股价格 baseline 只使用逐日 `industry_members`、上市/退市、日线、复权、涨跌停、停牌和基准；固定 120–20 动量、60 日波动、月末 topN 等权和下一开市日开盘执行，不读取财务指标或当前成员列表。
- `risk.py` 从冻结输入计算 gross/net/cash、集中度、历史行业暴露、benchmark beta 和边际/总风险贡献；贡献之和必须等于组合波动。`allocation.py` 只输出受单票、行业、现金和换手约束的研究目标权重，不生成订单。
- `StrategyDefinition.walk_forward_benchmark_source` 是强制合同：ETF 趋势、波动率管理和低波动准入的 canonical walk-forward 使用同一 ETF 因果复权基准，市场指数只用于环境分类；窗口首个测试日从训练段末 NAV 接续，不能重新归一化丢失首日。
- 完整指数成分归因仍因缺 `index_weights` blocked；行业基准比较仍因缺可复现 `industry_proxy_daily` blocked。不要用 `index_daily_bars`、当前成分或现场临时代理冒充。
- 固定反例审计入口为 `python scripts/research/audit_quant_research.py`；完整数据库语义入口仍是 `PYTHON_BIN=.venv/bin/python scripts/ops/test_postgres_integration.sh`。
- `docs/research/strategy-results/index.html` 与 `manifest.json` 现在统一登记 3 组当前可信报告和 3 组旧档案；API `GET /api/strategy-results/overview` 同时兼容当前 `summaryJson` 和旧 phased/csv。统一 `summary.status` 只取 manifest，旧摘要 `status=ok` 仅保留为 `sourceExecutionStatus`，不得翻译为 `研究通过`。
- 结果清单拒绝绝对路径、`..` 与 symlink 逃逸；manifest 一旦声明 `summaryJson`，文件缺失或顶层不是 JSON 对象必须显式失败，不允许静默返回空摘要。

## 当前服务器事实

- SSH：`ubuntu@182.254.180.169`；活动持久 volume 为 `quant_todo_p0_postgres_data_todo_p0`，严禁 `docker compose down -v` 或删除该 volume。
- 当前四容器为 `db/api/worker/frontend`；API/worker/frontend bind 到 `/opt/quantitative-trading-release-20260712-0101`，API 使用无 `--reload` 的生产命令。API/worker 的 `APP_GIT_COMMIT` 均为 `c24ade495492f64ea82aa229827858cdef52cdf6`。
- Compose project label 为 `quantitative-trading`；当前 release 的服务器覆盖文件继续把 DB/API/frontend 端口限制在 loopback，并指向上述 external PG volume。PostgreSQL 只监听 `127.0.0.1:5432`，worker 不对宿主机暴露端口。
- 生产 schema revision 为 `0006_worker_heartbeats`。13 个重复普通索引迁移前逻辑大小合计 `4,166,868,992` bytes、迁移后为 0，唯一守卫完整；迁移后 `pg_database_size=16,295,099,415` bytes，`public` 索引逻辑大小合计 `7,166,763,008` bytes。2026-07-19 现场复查根盘使用约 76%、可用 9.1 GiB；当前不需扩容，低于 5 GiB 时应先通知用户。镜像构建与索引迁移发生在同一窗口，不能把索引逻辑大小表述为 `df` 的精确净释放。
- 2026-07-19 首次用错误临时 artifact root 复用上述 snapshot 时，完整性门禁留下两条失败 ResearchRun（`f654fda4-4b89-4359-b6fe-b3f32839e3b2`、`60f89724-da53-47f6-8303-8ad6bfb4ecd9`）并把 snapshot registry 标成 `failed`；磁盘全量哈希、表工件、行数和 registry 路径全部对账一致后，已在单事务恢复为 `complete` 并读回确认。两条失败运行作为审计记录保留，未删除；后续 8 条修正运行均 `succeeded/finalized`。
- `data_sync_jobs` 当前 6 行均为最终 `ok`，其中 2 行是生产 queued/expired-running 恢复演练；queued/running/failed/expired lease 均为 0，worker 心跳新鲜。
- 最新生产全量 custom-format dump 已流式保存到本机 `/Users/jettlin/backups/Quantitative_trading/quant_trading_2026-07-11_2138+0800.dump`，大小 `2,232,308,654` bytes、权限 `0600`、SHA-256 `7c13b7ec933fd0ec965f07cea57db8add43a29fc96ec9b3d53d544aed040dd14`。PostgreSQL 16 `pg_restore -l` 得到 250 个非注释 TOC 条目/25 个 `TABLE DATA` 段，整包 `pg_restore --file=/dev/null` 读取也成功。该备份是迁移前恢复点，迁移后新增 registry 记录不在其中。

## 已实现的可信闭环

### 数据完整性

- `backend/app/data_quality/` 按 scope、universe、日期、所需数据集和 benchmark 检查 schema、自然键、domain、引用、交易日覆盖、复权、涨跌停、OHLCV、新鲜度和基准重叠。
- 状态严格区分 `ready`、`ready_with_warnings`、`blocked`、`failed`；CLI 退出码为 0/2/3。
- formal snapshot gate 会重新读取全部 `DataQualityResult`，从明细重建状态、计数和规则引用，再与 `DataQualityRun.status/summary` 交叉核对；伪造主记录不能放行。
- 真实生产只读审计：2025-12 的 A 股切片为 `ready_with_warnings`，明确报告非股票涨跌停历史；ETF 小切片 30 条规则为 `ready`。未删除这些历史行。

### 无未来函数

- 复权改为因果 total-return index，未来复权因子不会重标历史前缀。
- 公告时间未知的财务数据从公告后的下一开市日可用；严格财务研究在缺供应商修订历史时 blocked。
- 收盘信号只能在下一交易日开盘执行；官方交易日历、显式/历史 universe、上市/退市、停牌和涨跌停边界都有硬校验。
- 伪日历路径、日历文件篡改、伪 universe 来源、混合日期/布尔值和追加未来行情/公告均有反例测试。

### 可复现

- PostgreSQL 输入在 `REPEATABLE READ + READ ONLY` 事务内按精确切片冻结；transaction 合同进入 `snapshot_id`。
- canonical CSV.gz 固定列、排序、ISO 日期、gzip mtime=0 和 SHA-256；`\N` 只表示 null，非 null 值若恰等于该哨兵会在写入/hash 前严格拒绝。
- `ResearchRun` 绑定 canonical config、代码提交、依赖/环境、随机种子和 snapshot；结果指纹包含确定性 targets/nav/metrics、v2 模拟账本，以及启用时成对出现的 walk-forward/风险工件。
- manifest 的输入副本、`dataSnapshot.tableArtifacts`、实际文件和 checkpoint hash 链交叉验证；reproduce 在任何计算前拒绝篡改。
- `scripts/research/reproduce_quant_research.py` 只读取冻结输入；在线数据库后续改变不影响旧运行。

### 重启可靠

- API 只入队；独立 `worker` 使用 PostgreSQL `FOR UPDATE SKIP LOCKED`、租约、短事务心跳、有限退避和永久错误分流。
- 语义为 at-least-once；自然键 upsert 保证崩溃重放不产生重复行，过期 owner 不能完成已被新 worker 接管的任务。
- ResearchRun 每阶段写原子 hash-chain checkpoint；stale `running` 转为 `interrupted`，`--resume RUN_ID` 严格核对身份和归档后只执行未完成阶段。
- snapshot、simulation、finalize 三个中断点均有恢复测试；损坏 checkpoint、归档、代码、环境、快照或可复现键会停止。

## 关键远端 sandbox 证据

- 生产 schema-only + `510300.SH`、`000300.SH`、SSE 日历真实小样本在独立 PostgreSQL 16 tmpfs 容器中完成 baseline fingerprint/stamp、`0001→0006` 和重复 upgrade；stamp 加 upgrade 约 4 秒。
- 质量运行 ID：`731346b9-e1d9-46b4-8063-8a6c7fc09911`；当时 30/30 规则通过。
- 最终修复提交重新生成：quality run `e35c0289-544a-47b3-8781-b6190c845aae`；snapshot `cb9bac39488283a13e5d31604471841b7ac5311e0e5852f1d9ac8d0639152dab`；research run `be5ef206-9291-4e24-82ad-7b01c0cb7b94`；reproducibility key `561c3e0be6d1a2b644a7bfdf531e20d70328f6b85c81df87a1f0220def74e2f7`；result fingerprint `61aa690cc0f7ea6e1b090cbbdae359696a74ad5434266167c175b6453bbe5079`。
- 该 snapshot 明确绑定 PostgreSQL `REPEATABLE READ + READ ONLY`；数据库地址不可连接时连续两次 reproduce 都精确匹配结果指纹。
- worker 双抢、锁行跳过、租约过期接管和自然键零重复已在最终代码的远端 PostgreSQL 16 通过；ResearchRun simulation resume 也已再次通过。
- 临时 sandbox 已删除；上述 sandbox ID 只保留为迁移前证据。当前生产 registry 应使用下文“当前生产验收身份”的 ID，不要混用。

## 当前验证入口

快速 SQLite 门禁：

```bash
DATABASE_URL='sqlite+pysqlite:///:memory:' .venv/bin/python -m unittest discover -s backend/tests -p 'test_*.py'
```

完整 PostgreSQL 16 tmpfs 门禁：

```bash
PYTHON_BIN=.venv/bin/python scripts/ops/test_postgres_integration.sh
```

其余必检：

```bash
.venv/bin/python -m py_compile backend/app/database.py backend/app/models.py backend/app/schemas.py backend/app/tushare_client.py backend/app/us_research.py backend/app/main.py backend/app/sync_worker.py backend/app/quant_research/*.py scripts/research/*.py
docker compose config --quiet
docker compose -f docker-compose.test.yml config --quiet
for file in scripts/ops/*.sh; do bash -n "$file"; done
git diff --check
```

前端依赖如果本机 `node_modules` 不完整，使用正式 `node:22-alpine` Dockerfile 构建后在镜像内运行 `npm run typecheck`、`npm run lint`、`npm run build`，不要把本机缺失 `tsc` 误报成源码失败。

## 当前生产验收身份

- quality run：`4930ff05-a332-4a62-b7a8-1c7479126bca`，固定 2025-12 ETF 切片 30/30 规则通过。
- snapshot：`cb9bac39488283a13e5d31604471841b7ac5311e0e5852f1d9ac8d0639152dab`，PostgreSQL `REPEATABLE READ + READ ONLY`。
- research run：`a22fb663-1b66-4579-ab58-e6d3236d1843`。
- reproducibility key：`ddbaa1b1c19793c3bb55db107d634935de03025cdf69c14334994ddad694d9b3`。
- result fingerprint：`61aa690cc0f7ea6e1b090cbbdae359696a74ad5434266167c175b6453bbe5079`；数据库不可连接时两次 reproduce 均精确匹配。
- queued 恢复任务：`f1360532-9227-408c-8f19-716768c8cce6`，API 重启后仍 queued，worker 恢复后第 1 次尝试成功。
- expired-running 恢复任务：`5f5dc534-b757-42e4-8193-418c17d8b62d`，5 秒租约过期后由新 worker 第 2 次尝试接管成功。

完整表行数、输入文件哈希、时间线、限制和回滚证据统一见 `docs/deployment/2026-07-12-production-trustworthiness-acceptance.md`，不要只复制其中一个 ID 就宣称整个闭环通过。

## 不要做

- 不要把表存在或 inventory 写成研究级 ready。
- 不要恢复旧策略目录或拿旧回测报告当可信底座证据。
- 不要自动删除非股票涨跌停历史、旧快照、数据库 volume 或任何备份。
- 不要导入真实持仓/成交、连接券商、开放公网 PostgreSQL 或把 sentinel 写成投资建议。
- 不要把本次授权扩展为未来生产变更的长期授权；新的 stamp、Alembic upgrade、`DROP INDEX`、数据删除、volume 操作或覆盖恢复仍需单独确认。
