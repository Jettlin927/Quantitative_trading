# Agent Handoff

这份文档给后续 Agent 接手可信量化研究底座用。规则仍以根目录 `AGENTS.md` 为准；所有运行状态都应现场复查，不能只信本文。

## 当前接手状态

- 本地仓库：`/Users/jettlin/code/Quantitative_trading`；用户常用 Windows 副本路径仍可能是 `E:\coding_things\Quantitative_trading`。
- 可信工程已于 2026-07-11 快进合入 GitHub `main`，生产运行时代码为 `c24ade495492f64ea82aa229827858cdef52cdf6`。本地任务分支仍为 `codex/quant-foundation-trustworthiness`；文档收口后 `main` 可能领先运行时代码，接手必须现场运行 `git status -sb`、`git log -3 --oneline` 和 `git rev-list --left-right --count origin/main...HEAD`，并把“仓库最新提交”与“生产运行时提交”分开核对。
- 当前目标：数据完整性、无未来函数、结果可复现、进程重启可靠四条可信门禁。分钟线、期权、新付费源、券商和真实交易继续暂缓。
- Phase 0–5 已完成。首次独立审计发现的 4 个问题修复后，同一审计者在精确提交 `f506d0e58c303afe7ad561b37ceff27c6e5e681f` 重放 39/39 反例；PostgreSQL 16.14 全矩阵 162/162、0 跳过。生产发布后又在精确部署代码上执行 73 项定向门禁，72 项通过、1 项显式 PG URL 用例按设计跳过。
- 运行时代码对应的 GitHub Actions CI run `29158046019` 四个 job 全部成功；生产验收证据提交 `1fe3162f4953c08fa4ad5de160994565b320406c` 已 fast-forward 推送 `main`，对应 CI run `29161789513` 的四个 job 也全部成功。生产 quality、snapshot、sentinel、断库 reproduce 和 worker 重启恢复的完整证据见 `docs/deployment/2026-07-12-production-trustworthiness-acceptance.md`。
- 生产 PostgreSQL 已在用户明确确认后完成 fingerprint 门禁、baseline stamp、`0002→0006` 和 13 个普通重复索引清理。后续新的生产 DDL、数据删除、volume 操作或覆盖恢复仍必须再次确认。

## 当前服务器事实

- SSH：`ubuntu@182.254.180.169`；活动持久 volume 为 `quant_todo_p0_postgres_data_todo_p0`，严禁 `docker compose down -v` 或删除该 volume。
- 当前四容器为 `db/api/worker/frontend`；API/worker/frontend bind 到 `/opt/quantitative-trading-release-20260712-0101`，API 使用无 `--reload` 的生产命令。API/worker 的 `APP_GIT_COMMIT` 均为 `c24ade495492f64ea82aa229827858cdef52cdf6`。
- Compose project label 为 `quantitative-trading`；当前 release 的服务器覆盖文件继续把 DB/API/frontend 端口限制在 loopback，并指向上述 external PG volume。PostgreSQL 只监听 `127.0.0.1:5432`，worker 不对宿主机暴露端口。
- 生产 schema revision 为 `0006_worker_heartbeats`。13 个重复普通索引迁移前逻辑大小合计 `4,166,868,992` bytes、迁移后为 0，唯一守卫完整；迁移后 `pg_database_size=16,295,099,415` bytes，`public` 索引逻辑大小合计 `7,166,763,008` bytes，根盘约 40 GiB、剩余约 9.0 GiB。镜像构建与索引迁移发生在同一窗口，不能把索引逻辑大小表述为 `df` 的精确净释放。
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
- `ResearchRun` 绑定 canonical config、代码提交、依赖/环境、随机种子和 snapshot；结果指纹只含确定性 targets/nav/metrics。
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
