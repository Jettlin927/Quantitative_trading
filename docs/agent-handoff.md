# Agent Handoff

这份文档给后续 Agent 接手可信量化研究底座用。规则仍以根目录 `AGENTS.md` 为准；所有运行状态都应现场复查，不能只信本文。

## 当前接手状态

- 本地仓库：`/Users/jettlin/code/Quantitative_trading`；用户常用 Windows 副本路径仍可能是 `E:\coding_things\Quantitative_trading`。
- 当前开发分支：`codex/quant-foundation-trustworthiness`。接手先运行 `git status -sb`、`git log -3 --oneline` 和 `git rev-list --left-right --count origin/main...HEAD`。
- 当前目标：数据完整性、无未来函数、结果可复现、进程重启可靠四条可信门禁。分钟线、期权、新付费源、券商和真实交易继续暂缓。
- Phase 0–4 的代码和测试已完成；Phase 5 的 CI、远端 sandbox 和最终独立反例审计已经执行。首次审计发现 4 个问题，修复后同一审计者在精确提交 `f506d0e58c303afe7ad561b37ceff27c6e5e681f` 重放 39/39 反例并判定四项目标全部 PASS；PostgreSQL 16.14 全矩阵 162/162、0 跳过。
- 精确提交 `f506d0e` 的 GitHub Actions CI run `29154412670` 已全绿，四个 job 均成功；最终文档提交和 `main` 推送仍应以现场 Git 状态与后续 CI 为准。
- 生产 PostgreSQL 尚未迁移：无 `alembic_version`，未执行 stamp、upgrade、DROP INDEX、DELETE 或历史数据清理。正式 DDL 与发布必须先让用户确认 `docs/deployment/2026-07-11-production-migration-approval.md`。

## 当前服务器事实

- SSH：`ubuntu@182.254.180.169`；活动持久 volume 为 `quant_todo_p0_postgres_data_todo_p0`，严禁 `docker compose down -v` 或删除该 volume。
- 活动容器仍是旧版 `db/api/frontend`，API/前端 bind 到 `/opt/quantitative-trading-release-20260710-2330`；当前 API 仍带 `--reload`。新代码目录 `/opt/quantitative-trading` 可以在不触发旧 API reload 的情况下先构建。
- Compose project label 为 `quantitative-trading`；服务器覆盖文件位于 `/opt/quantitative-trading/docker-compose.server.yml`，继续把 DB/API/frontend 端口限制在 loopback，并指向上述 external PG volume。
- 生产库约 19 GiB，根盘约 40 GiB、剩余约 6.6 GiB；13 个已证明与唯一约束完全重复的普通索引约占 3974 MiB。
- `data_sync_jobs` 当前只有 3 行最终 `ok`，表约 112 KiB；正式迁移前仍需重新确认无 queued/running 和无异常长事务。
- 最新生产全量 custom-format dump 已流式保存到本机 `/Users/jettlin/backups/Quantitative_trading/quant_trading_2026-07-11_2138+0800.dump`，大小 `2,232,308,654` bytes、权限 `0600`、SHA-256 `7c13b7ec933fd0ec965f07cea57db8add43a29fc96ec9b3d53d544aed040dd14`。PostgreSQL 16 `pg_restore -l` 得到 250 个非注释 TOC 条目/25 个 `TABLE DATA` 段，整包 `pg_restore --file=/dev/null` 读取也成功。

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
- 临时 sandbox 已删除，生产容器保持健康且生产库仍无 `alembic_version`。

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

## 生产确认后的固定顺序

1. 复核最新备份、SHA-256、`pg_restore -l`、生产 fingerprint、active jobs、长事务、磁盘和当前 HTTP smoke。
2. 把最终审计提交同步到非活动 `/opt/quantitative-trading`，先构建镜像；不要先覆盖活动 release bind。
3. 用新镜像只读计算 fingerprint；精确匹配后才显式 `stamp-existing`，再单独 `alembic upgrade head`。
4. 核对 revision、13 个普通重复索引、唯一约束、关键表行数和查询计划，再切换 API/worker/frontend。
5. 运行 health、worker heartbeat、quality、sentinel、两次离线 reproduce、queued/running 重启恢复和 loopback 端口检查。
6. 更新本文、生产确认单、`操作日志.md`，然后才允许把 Goal 标记完成。

## 不要做

- 不要把表存在或 inventory 写成研究级 ready。
- 不要恢复旧策略目录或拿旧回测报告当可信底座证据。
- 不要自动删除非股票涨跌停历史、旧快照、数据库 volume 或任何备份。
- 不要导入真实持仓/成交、连接券商、开放公网 PostgreSQL 或把 sentinel 写成投资建议。
- 不要在未获用户明确确认时执行生产 stamp、Alembic upgrade、`DROP INDEX` 或发布新 API/worker。
