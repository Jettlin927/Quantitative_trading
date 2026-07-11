# 2026-07-11 生产 PostgreSQL 迁移确认单

> 状态：最终独立复审和最新全量备份校验均已通过；等待用户明确确认。本文不是执行授权。

## 生产现状

- 活动数据库：PostgreSQL 16，数据库约 19 GiB，持久 volume 为 `quant_todo_p0_postgres_data_todo_p0`。
- 活动 schema 仍无 `alembic_version`；截至本确认单生成时未执行 stamp、upgrade、DROP、DELETE 或历史数据清理。
- `data_sync_jobs` 只有 3 行，均为最终 `ok`，表总大小约 112 KiB，无 queued/running 任务。
- 13 个与唯一约束键序完全相同的普通索引合计约 3974 MiB；对应唯一约束索引均存在。
- 服务器根盘约 40 GiB，当前剩余约 6.6 GiB。全量备份直接流式保存到本机，不在服务器根盘落地。
- 活动 API/前端使用旧 release 目录；新代码可先在 `/opt/quantitative-trading` 构建，不会因更新该目录触发旧 API 的 `--reload`。

## 将执行的 schema 变更

1. 计算生产 schema fingerprint；只有与冻结的 25 表 baseline 精确一致时，才创建 `alembic_version` 并 stamp `0001_existing_schema_baseline`。不会重复创建现有表。
2. `0002_quality_snapshot_registry`：新增 `data_quality_runs`、`data_quality_results`、`data_snapshots` 及其约束/索引。
3. `0003_drop_duplicate_indexes`：使用 `DROP INDEX CONCURRENTLY IF EXISTS` 删除 13 个普通重复索引；不删除唯一约束索引：
   - `ix_stock_limit_prices_code_date`
   - `ix_stock_adjust_factors_code_date`
   - `ix_stock_daily_bars_code_date`
   - `ix_stock_daily_basic_code_date`
   - `ix_index_daily_bars_code_date`
   - `ix_fund_adjust_factors_code_date`
   - `ix_fund_daily_bars_code_date`
   - `ix_stock_financial_indicators_code_period`
   - `ix_trade_calendars_exchange_date`
   - `ix_asset_daily_prices_key_date`
   - `ix_assets_market_symbol`
   - `ix_stock_pool_members_pool_code`
   - `ix_watchlist_items_name_asset`
4. `0004_research_runs`：新增研究运行登记表和 2 个普通索引。
5. `0005_job_leases`：给 `data_sync_jobs` 新增 9 个租约、心跳、尝试、退避和更新时间字段，并新增 claim/lease expiry 两个索引。现有 3 行只接收声明的默认值。
6. `0006_worker_heartbeats`：新增 worker 心跳表及状态/时间索引。

上述 migration 不包含行情/基本面数据行的 `DELETE`、`UPDATE`、清洗或归档，也不删除 PostgreSQL volume。

## 锁、耗时和磁盘预期

- 新建质量、快照、研究和 worker 表不会扫描现有千万级行情表。
- PostgreSQL 官方说明，`DROP INDEX CONCURRENTLY` 不会阻断目标表上的并发查询、插入、更新和删除，但会等待冲突事务结束；migration 每次只删除一个索引。参见 [PostgreSQL 16 DROP INDEX](https://www.postgresql.org/docs/16/sql-dropindex.html)。
- `data_sync_jobs` 的 `ADD COLUMN` 会取得短暂表级 DDL 锁；该表当前仅 3 行/112 KiB。PostgreSQL 16 对带非易失默认值的新增列不要求表重写。参见 [PostgreSQL 16 ALTER TABLE](https://www.postgresql.org/docs/16/sql-altertable.html)。
- 远端 schema-only + 真实小样本 sandbox 中 baseline stamp 加 `0001→0006` 约 4 秒，重复 upgrade 幂等。生产主要不确定性是 13 个 concurrent drop 等待旧事务，预留 30 分钟维护窗口；若出现长事务或锁等待则停止并保留旧服务，不强杀数据库会话。
- 预计删除普通重复索引后根盘释放约 3.9 GiB；新登记表和小表索引初始占用很小。实际迁移后必须重新记录数据库、索引和根盘体积。

## 已完成的 sandbox 证据

- 使用生产 schema-only 和真实 `510300.SH` / `000300.SH` / SSE 日历小样本，在独立 PostgreSQL 16 tmpfs 容器完成 fingerprint、baseline stamp、`0001→0006`、重复 upgrade 和 `alembic check`。
- 最终修复提交 `f506d0e58c303afe7ad561b37ceff27c6e5e681f` 重新演练：30 条 ETF 数据质量规则全部通过，quality run ID 为 `e35c0289-544a-47b3-8781-b6190c845aae`。
- 最终真实小样本 snapshot ID 为 `cb9bac39488283a13e5d31604471841b7ac5311e0e5852f1d9ac8d0639152dab`，明确绑定 PostgreSQL `REPEATABLE READ + READ ONLY`；research run ID 为 `be5ef206-9291-4e24-82ad-7b01c0cb7b94`，reproducibility key 为 `561c3e0be6d1a2b644a7bfdf531e20d70328f6b85c81df87a1f0220def74e2f7`，result fingerprint 为 `61aa690cc0f7ea6e1b090cbbdae359696a74ad5434266167c175b6453bbe5079`。
- 将数据库 URL 刻意设为不可连接后连续两次 reproduce，实际结果指纹均与上述期望值完全一致。
- 最终代码在远端 PostgreSQL 16 再次通过 ResearchRun simulation 中断/显式 resume，以及双 worker 抢占、过期租约接管和自然键零重复。
- 非实现者在精确提交 `f506d0e58c303afe7ad561b37ceff27c6e5e681f` 重放 39 个自定义反例，39/39 通过；隔离 PostgreSQL 16.14 全矩阵 162/162 通过、0 跳过。数据完整性、无未来函数、结果可复现、进程重启可靠四项均判定 PASS。
- 同一提交的 GitHub Actions [CI run 29154412670](https://github.com/Jettlin927/Quantitative_trading/actions/runs/29154412670) 全绿：SQLite/Python、PostgreSQL 16 集成矩阵、前端 TypeScript/ESLint/build、Compose/Shell/差异检查四个 job 均成功。
- 临时 sandbox 已删除；活动数据库仍无 `alembic_version`。

## 备份证据

- 本机归档：`/Users/jettlin/backups/Quantitative_trading/quant_trading_2026-07-11_2138+0800.dump`，2026-07-11 21:38–23:23 +08:00 从生产容器直接流式保存；未在服务器根盘落地。文件大小 `2,232,308,654` bytes，权限 `0600`。
- SHA-256：`7c13b7ec933fd0ec965f07cea57db8add43a29fc96ec9b3d53d544aed040dd14`；`shasum -a 256 -c` 返回 `OK`。
- PostgreSQL 16 `pg_restore -l`：成功，250 个非注释 TOC 条目，包含当前 25 张表的 25 个 `TABLE DATA` 段。
- PostgreSQL 16 完整归档读取：`pg_restore --file=/dev/null --no-owner --no-acl` 成功，2026-07-11 23:23:54–23:24:00 +08:00，耗时 6 秒；不是只读取归档头。
- 备份格式为 `pg_dump --format=custom`，可由 `pg_restore` 选择、排序和恢复对象；官方说明见 [pg_dump](https://www.postgresql.org/docs/16/app-pgdump.html) 与 [pg_restore](https://www.postgresql.org/docs/16/app-pgrestore.html)。

## 获得确认后的执行顺序

1. 再次确认备份文件、SHA-256、`pg_restore -l`、服务器空间、无 active sync job、无异常长事务，暂停会与维护窗口重叠的日更入口。
2. 把最终已审计提交同步到服务器的非活动代码目录，运行 Compose config，并先构建 API、worker、frontend 镜像；活动旧容器继续服务。
3. 用新 API 镜像对活动数据库只读计算 fingerprint；值不完全一致则立即停止。
4. 用显式 fingerprint 执行 `stamp-existing`，随后单独执行 `alembic upgrade head`；migration 不绑定到 API 启动。
5. 验证 revision=`0006_worker_heartbeats`、13 个普通重复索引消失、唯一约束仍存在、数据库行数和关键范围查询不变。
6. 依次切换 API、worker、frontend；注入可验证 `APP_GIT_COMMIT`，保留现有外部 PG volume、loopback 端口覆盖和服务器日志目录。
7. 验证 PostgreSQL、HTTP health、worker heartbeat、前端、inventory/readiness、真实小范围 quality、sentinel 和两次离线 reproduce。
8. 分别演练 queued 和过期 running 的 worker 恢复，确认自然键重复为 0；记录磁盘、数据库和索引前后值。

## 回滚策略

- stamp 或 migration 前失败：不切换活动容器，数据库保持原状。
- 新表或新增列失败：停止发布并保留旧容器；这些变更是 schema 超集，旧应用不会依赖它们。
- 普通重复索引删除后出现查询计划退化：保留唯一约束，按 `0003` 的 downgrade 定义逐个 `CREATE INDEX CONCURRENTLY` 前向修复；不执行会删除审计表的自动降级。
- 新应用 smoke 失败：切回旧 release 容器；数据库的新增表/列保持不动，随后做前向修复。
- 只有发生无法前向修复的灾难性问题时，才从已验证 dump 恢复到新的隔离 volume；任何覆盖活动 volume 的恢复仍需用户再次明确确认。

## 所需确认

只有用户在看到填写完整的备份与最终审计证据后，明确回复同意生产 migration/发布，才允许执行 Task 5.3/5.4。仅同意代码、sandbox 或备份，不等同于授权生产 DDL。
