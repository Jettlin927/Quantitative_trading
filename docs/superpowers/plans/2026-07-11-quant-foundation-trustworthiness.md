# 量化底座可信工程实施计划

> 状态：待实施。按阶段执行，每个阶段独立验证、独立提交；前一阶段没有通过验收，不进入下一阶段。

## 目标

把当前“真实 PostgreSQL 数据工作台 + 研究协议原型”补成一个可审计的离线量化研究底座，优先完成四件事：

1. 数据完整性：针对明确研究范围、股票池和日期区间证明数据是否完整，而不是只证明表存在。
2. 无未来函数：任何特征、股票池和交易动作都只能使用当时已经可获得的信息。
3. 结果可复现：固定代码、配置、随机种子和输入快照后，可以重新得到相同的确定性结果。
4. 进程重启可靠：API 或 worker 重启后，任务不会永久卡在 queued/running，也不会因为重复执行破坏数据。

本计划不恢复旧策略主线，不承诺策略收益，不连接券商，不产生真实交易动作。

## 明确暂缓

- 分钟线、Tick、逐笔成交。
- ETF/个股期权和隐含波动率数据。
- 真实美股行情流、期权流和付费数据源。
- 真实持仓、真实成交、券商接口和自动交易。
- AI 选股、自动调参、自动发布策略。
- 资金流、两融、题材、研报等解释层数据；除非后续研究问题明确需要，否则不阻塞本计划。
- 大规模前端改版。前四个阶段先以数据库、CLI、API 和可验证产物为准。

## 当前基线与已知问题

- 当前真实 PostgreSQL 已有千万级股票日线、每日指标、复权因子、指数和基金数据，采集和持久化不是从零开始。
- backend/app/quant_research 已有复权、point-in-time、显式股票池、下一交易日执行、指标、walk-forward、manifest 和 readiness 骨架。
- 当前 readiness 只返回表存在/非空的 0/1 结果。线上接口会返回 ready，但 stock_limit_prices 中有 2,225,255 条记录找不到 stock_listings 对应项。
- 当前复权价格以请求区间最后一个复权因子为锚点；追加未来公司行动后，历史绝对复权价格会改变，不满足严格的前缀不变性。
- 财务数据允许 ann_date 与 trade_date 同日匹配；公告时间未知时存在收盘后公告被同日使用的风险。
- quant_research 函数目前除测试外没有完整生产调用链；线上没有 research_runs、data_snapshots 或 artifact registry。
- API 使用 FastAPI BackgroundTasks 执行同步任务。任务状态虽然入库，但 API 进程退出后没有租约、心跳、抢占恢复和自动重试。
- 当前 Base.metadata.create_all 在应用导入时执行；线上没有正式 schema revision。
- 多张大表同时保留相同键的唯一索引和普通索引，重复普通索引理论占用约 4 GB。
- 服务器当前剩余空间约 6.6 GiB。任何新备份、输入快照和研究产物必须先做容量预检。

## 核心架构选择

### 1. 数据质量按“研究范围”评估

质量检查必须携带：

- scope：例如 etf_time_series、a_share_cross_section。
- start_date、end_date。
- universe 定义及其哈希。
- 实际需要的字段/因子清单。
- benchmark。

只检查全库总行数不能产生 ready。状态统一为：

- ready：所有阻断规则通过。
- ready_with_warnings：研究切片可安全使用，但全库仍有被明确排除的问题。
- blocked：研究切片缺关键数据或违反 point-in-time 合同。
- failed：质量检查本身执行失败，不能被解释成数据不完整。

### 2. 可复现依赖“冻结精确输入切片”

数据库使用 upsert，会被后续供应商修订覆盖。只记录行数、最小日期和最大日期不足以复现历史运行。

本计划采用最小可行方案：

- 在 PostgreSQL REPEATABLE READ、READ ONLY 事务中读取本次研究需要的精确切片。
- 按自然键稳定排序，使用标准库写 canonical CSV.gz；本阶段不引入 PyArrow。
- 对未压缩 canonical bytes 计算 SHA-256。
- 快照只冻结本次研究所需标的、日期和字段，不复制整个 19 GB 数据库。
- 重现运行从冻结输入读取，不再查询可能已经变化的在线表。

### 3. 任务可靠性使用 PostgreSQL 租约队列

当前服务器规模不需要 Redis、Celery 或新的消息队列。

- API 只负责入队和查询，不在请求进程内执行任务。
- 独立 worker 使用 SELECT ... FOR UPDATE SKIP LOCKED 抢占任务。
- 任务带 lease_owner、lease_expires_at、heartbeat_at、attempt_count 和 next_attempt_at。
- 语义为 at-least-once；依靠现有自然键和幂等 upsert 保证重复执行安全。
- worker 重启后回收过期租约，任务不会永久停留在 running。

### 4. 数据问题先检测，不自动删除

- 当前 2,225,255 条非股票涨跌停记录先报告为 out_of_domain。
- 研究 loader 必须通过 stock_listings 或明确 universe 排除这些记录。
- 本计划不删除历史行情记录。
- 任何 DELETE、批量清洗或物理归档必须另行列出影响范围并取得用户明确确认。

## 实施前决策门禁

### D0：数据库迁移工具

推荐引入 Alembic，原因是当前已经存在真实 PostgreSQL、大表和后续多阶段 schema 变更，继续依赖 create_all 无法审计或安全回滚。

仓库规则要求引入数据库迁移工具前先获得用户明确确认。因此实施 Phase 1 前必须确认：

- 允许把 Alembic 作为唯一 schema migration 入口，并在 backend/requirements.txt 固定版本。
- 现有生产库先校验 schema fingerprint，再 stamp 初始 baseline；禁止对现有表重复 CREATE。
- 应用启动只校验 revision，不自动执行生产迁移。

若用户不确认 Alembic，本计划停在 Phase 0，不临时发明自研迁移框架。

## 全局完成标准

只有以下条件全部满足，才能称为本计划完成：

1. 一个指定研究范围的 readiness 必须包含 universe、日期区间、规则结果和质量运行 ID。
2. 人为删除一个关键标的日的复权因子后，质量门禁必须 blocked，并能定位标的和日期。
3. 当前非股票涨跌停记录必须出现在质量报告中，不能继续无提示返回纯 ready。
4. 给输入追加未来行情、未来复权因子或未来财务公告后，截止原日期的特征和目标权重完全不变。
5. 未知公告时间的财务记录最早从公告日后的下一交易日可用。
6. 相同代码、配置、随机种子和 data_snapshot_id 的两次运行具有相同 reproducibility_key、输入哈希、净值哈希和指标哈希。
7. 在线数据库后续发生 upsert 后，旧 run 仍能从冻结输入重新得到相同结果。
8. API/worker 在 queued 和 running 两种状态下重启，任务均能恢复；不会出现重复自然键，也不会永久卡住。
9. 新 migration 能从空 PostgreSQL 建库，也能从当前 schema baseline 前向升级。
10. 后端单测、真实 PostgreSQL 集成测试、Compose config、Shell 语法和远端 smoke 全部通过。

---

## Phase 0：冻结合同和验收夹具

### Task 0.1：建立统一可信合同

**文件**

- 新增：docs/research/quant-foundation-trust-contract.md
- 修改：docs/research/README.md
- 修改：docs/agent-code-map.md

**实施**

- [ ] 写明 quality scope、universe provenance、available_from、signal_date、execution_date、data_snapshot 和 reproducibility_key 的定义。
- [ ] 为每张可用于研究的表建立“信息何时可得”矩阵：
  - stock_daily_bars：open 只能在开盘后观测；high/low/close/vol/amount 收盘后可得，基于收盘的信号只能下一交易日执行。
  - stock_daily_basic：默认收盘后可得，下一交易日进入特征。
  - stock_financial_indicators：只有日期没有时间时，ann_date 后下一交易日可得。
  - stock_adjust_factors / fund_adjust_factors：用于计算截至当日的总回报，不允许用未来因子重标历史前缀。
  - stock_limit_prices：开盘成交约束可使用，但必须属于股票 universe。
  - stock_suspend_events：区分全天/开盘停牌与盘中事件；未知时采用保守规则并写入 limitations。
  - stock_listings / industry_members：按 list_date、delist_date、in_date、out_date 做历史资格判断。
- [ ] 明确静态当前股票池不能被表述为无幸存者偏差的横截面研究；必须记录 universe 来源与哈希。
- [ ] 明确旧 ma/value 策略和旧结果只保留为 archive，不作为新底座验收证据。

**测试与验收**

- [ ] 文档包含至少一个 ETF 时序研究和一个 A 股横截面研究的输入合同示例。
- [ ] 每个字段都能回答“何时可见”和“最早何时可用于下单”。
- [ ] docs/research/README.md 能直接导航到本合同。

### Task 0.2：建立小型黄金数据集

**文件**

- 新增：backend/tests/fixtures/quant_research_golden/
- 新增：backend/tests/fixtures/quant_research_golden/README.md
- 新增：backend/tests/test_quant_trust_contract.py

**实施**

- [ ] 创建完全合成且可提交 Git 的小数据集：2 个股票、1 个 ETF、1 个指数、15 个交易日。
- [ ] 覆盖周末、停牌、涨停、复权因子跳变、同日财务公告、公告后下一交易日和退市边界。
- [ ] 固定预期的可用日期、目标执行日、净值序列和指标 JSON。
- [ ] 不复制真实 Tushare 数据，不写 token 或真实持仓。

**测试与验收**

- [ ] 当前实现至少有一项测试先失败，证明夹具能捕获 end-date anchor 或同日公告问题。
- [ ] 黄金文件稳定排序，git diff 不因本机时区或浮点显示变化而漂移。

**Phase 0 Gate**

- [ ] 用户确认 D0 后才能进入 Phase 1。
- [ ] 合同和黄金数据集独立提交。

---

## Phase 1：正式 schema migration 与数据完整性门禁

### Task 1.1：建立 Alembic baseline

**文件**

- 修改：backend/requirements.txt
- 新增：alembic.ini
- 新增：backend/migrations/env.py
- 新增：backend/migrations/script.py.mako
- 新增：backend/migrations/versions/0001_existing_schema_baseline.py
- 新增：backend/tests/test_schema_migrations.py
- 修改：backend/app/database.py
- 修改：backend/app/main.py

**实施**

- [ ] 0001 migration 必须能在空 PostgreSQL 创建当前全部表、约束和索引。
- [ ] 为现有生产库生成 schema fingerprint 校验器；只有 fingerprint 与 baseline 一致时才允许 stamp 0001。
- [ ] 移除应用导入时的 Base.metadata.create_all。
- [ ] FastAPI lifespan 只检查数据库 revision 是否为 head；不一致时明确失败，不自动升级。
- [ ] SQLite 单测继续显式使用 Base.metadata.create_all，仅作为测试夹具，不作为生产迁移路径。

**测试与验收**

- [ ] 空 PostgreSQL：alembic upgrade head 成功。
- [ ] 当前 schema 临时副本：fingerprint 校验、stamp 0001、upgrade head 成功。
- [ ] 重复运行 upgrade head 不产生新变更。
- [ ] revision 落后时 API 启动失败并输出不含凭据的明确错误。

### Task 1.2：新增质量运行和快照登记表

**文件**

- 修改：backend/app/models.py
- 修改：backend/app/schemas.py
- 新增：backend/migrations/versions/0002_quality_and_snapshot_registry.py

**最小 schema**

data_quality_runs：

- id：UUID 字符串主键。
- scope、start_date、end_date、universe_hash。
- status：running / ready / ready_with_warnings / blocked / failed。
- config、summary：JSON。
- code_commit、started_at、finished_at。

data_quality_results：

- id：自增主键。
- run_id 外键。
- rule_id、table_name、severity、status。
- checked_rows、failed_rows。
- sample_issues：最多保存 20 个样例，不保存数百万条逐行问题。
- 唯一键：run_id + rule_id + table_name。

data_snapshots：

- snapshot_id：内容 SHA-256 主键。
- quality_run_id、scope、start_date、end_date、universe_hash。
- artifact_root、table_artifacts、row_counts。
- source_cutoff、status、created_at。

**测试与验收**

- [ ] 外键、状态字段、唯一键和 JSON-safe 输出测试通过。
- [ ] migration 只新增表，不改写或删除已有行情数据。

### Task 1.3：实现质量规则引擎

**文件**

- 新增：backend/app/data_quality/__init__.py
- 新增：backend/app/data_quality/contracts.py
- 新增：backend/app/data_quality/rules.py
- 新增：backend/app/data_quality/runner.py
- 新增：scripts/research/check_data_quality.py
- 新增：backend/tests/test_data_quality_rules.py
- 新增：backend/tests/test_data_quality_postgres.py

**规则**

- [ ] schema：必要表、字段、类型和自然键存在。
- [ ] uniqueness：自然键无重复。
- [ ] domain：股票研究数据只包含 universe 中的股票代码；额外代码计数并报告。
- [ ] referential：行情、复权、涨跌停、停牌事件能关联主数据。
- [ ] calendar coverage：
  - 上市且未退市的开市日应有日线，全天停牌日除外。
  - 有股票日线的日期必须有正数复权因子。
  - 有股票日线的日期必须有涨跌停价格；缺失即 blocked。
  - 只有策略声明需要 daily_basic 或 financial 时，它们才成为阻断项。
- [ ] value sanity：OHLC 正数且 high 不低于 open/close/low，low 不高于 open/close/high，成交量和金额非负。
- [ ] adjustment continuity：复权因子正数；跳变被记录，公司行动日以外的异常跳变产生 warning。
- [ ] freshness：研究结束日不得超过表的有效覆盖；日更监控单独报告最新开市日是否缺失。
- [ ] benchmark overlap：基准与研究区间有完整交易日重叠。

**实现约束**

- [ ] 质量查询使用只读事务和 statement timeout。
- [ ] 不构造全市场 × 全日期的巨大笛卡尔积；先按交易日、上市范围和停牌数聚合，再对缺口标的下钻。
- [ ] failed 与 blocked 分开：SQL 超时或程序异常不能被伪装成“数据缺失”。
- [ ] CLI 返回非零退出码：blocked=2，failed=3；ready 和 ready_with_warnings 为 0。
- [ ] 不提供自动修复或 DELETE。

**测试与验收**

- [ ] 黄金数据集的已知缺口均能命中正确 rule_id。
- [ ] 当前真实 PG 的非股票涨跌停行被报告为 out_of_domain，结果至少为 ready_with_warnings。
- [ ] 对一个小范围临时删除复权因子后，结果 blocked；事务回滚后原库不变。
- [ ] 同一输入重复检查得到相同规则计数。

### Task 1.4：升级 readiness 合同

**文件**

- 修改：backend/app/quant_research/readiness.py
- 修改：backend/app/main.py
- 修改：backend/app/schemas.py
- 修改：backend/tests/test_quant_research_foundation.py
- 修改：frontend/src/main.jsx（只改文案和状态展示，若本阶段决定展示）

**实施**

- [ ] 保留 GET /api/research/readiness 作为 inventory 检查，但它不得再返回研究级 ready；返回 level=inventory。
- [ ] 新增按 quality_run_id 查询的研究 readiness。
- [ ] readiness 输出包含 scope、universe_hash、start/end、quality_run_id、blockers、warnings 和 limitations。
- [ ] 任何研究 runner 必须接收一个 ready 或允许范围内的 ready_with_warnings 质量运行 ID。
- [ ] 前端不得把“表存在”显示成“研究已就绪”。

**验收**

- [ ] 当前线上同类数据不能再只凭 tableCounts=1 返回研究级 ready。
- [ ] 缺因子、缺基准、静态当前股票池风险能分别给出明确 blocker/warning。

### Task 1.5：重复索引安全治理

**文件**

- 修改：backend/app/models.py
- 新增：backend/migrations/versions/0003_remove_verified_duplicate_indexes.py
- 新增：scripts/ops/audit_postgres_indexes.sql
- 修改：docs/deployment/cicd.md

**实施**

- [ ] 只审计“唯一索引与普通索引字段顺序完全相同”的索引。
- [ ] 先在临时 PG 比较关键查询 EXPLAIN (ANALYZE, BUFFERS)。
- [ ] 仅删除经过验证的普通重复索引，永不删除唯一约束索引。
- [ ] 大索引使用 PostgreSQL concurrent 路径，避免长时间锁表。
- [ ] 本任务不删除任何数据行。

**验收**

- [ ] 关键股票池/日期查询计划不退化。
- [ ] 唯一约束仍能阻止重复写入。
- [ ] 实际释放空间和 migration 结果记录到操作日志。

**Phase 1 Gate**

- [ ] migration fresh/upgrade/idempotency 全部通过。
- [ ] 真实 PG 只读质量审计通过，且历史脏数据被显式报告。
- [ ] 未获得独立确认前，不清理 2,225,255 条历史记录。

---

## Phase 2：消除未来函数和幸存者偏差入口

### Task 2.1：改为因果总回报序列

**文件**

- 修改：backend/app/quant_research/dataset.py
- 修改：backend/app/quant_research/repository.py
- 修改：backend/tests/test_quant_research_foundation.py
- 修改：backend/tests/test_quant_trust_contract.py

**实施**

- [ ] 删除“用区间最后一个 adj_factor 重标整个历史”的研究语义。
- [ ] 生成以每个标的首个输入日为 1 的 causal total-return index；任意 t 的值只依赖不晚于 t 的价格和复权因子。
- [ ] 原始 open/high/low/close 保留用于成交约束和审计。
- [ ] 技术特征只使用收益、比例或 causal index，不允许跨标的比较任意锚点下的绝对复权价格。
- [ ] 明确 warmup_start 和 research_start；快照必须包含最大 lookback 所需的预热数据。

**测试**

- [ ] 在截止日后追加未来复权因子跳变，截止日前 causal index、收益和特征完全不变。
- [ ] 改变查询 end_date 不改变共同历史前缀。
- [ ] 缺失/非正复权因子继续严格失败，不回退原始价格。

### Task 2.2：财务信息使用 available_date

**文件**

- 修改：backend/app/quant_research/dataset.py
- 修改：backend/app/quant_research/repository.py
- 修改：backend/app/models.py（仅在需要保存 source update 字段时）
- 新增 migration（若新增字段）
- 修改：backend/tests/test_quant_trust_contract.py

**实施**

- [ ] 不再按 ts_code + ann_date 丢弃不同 end_date 的记录；保留数据库自然键 ts_code + end_date + ann_date。
- [ ] 使用交易日历把公告记录映射到 available_date。
- [ ] 没有公告时间时采用下一交易日，禁止 allow_exact_matches=True。
- [ ] 特征层显式选择报告期，不能在通用 dataset 层静默保留“同日最后一条”。
- [ ] 从现在开始保存可获得的 update_flag / source ingestion 时间；无法重建的历史修订在 manifest 中记录 limitation。
- [ ] 不使用财务因子的研究不因历史修订信息不足而 blocked；使用财务因子的严格研究必须声明 revision policy。

**测试**

- [ ] 周五公告在下周一首次可用。
- [ ] 交易日收盘后公告不能影响当日信号。
- [ ] 同一公告日的两个报告期不会被通用清洗丢弃。
- [ ] 追加未来公告不改变历史特征前缀。

### Task 2.3：统一信号日、可用日和执行日

**文件**

- 修改：backend/app/quant_research/repository.py
- 修改：backend/app/quant_research/portfolio.py
- 修改：backend/app/quant_research/validation.py
- 修改：backend/tests/test_quant_research_foundation.py

**实施**

- [ ] 数据面板增加 feature_available_date 或等价合同。
- [ ] 收盘特征只能生成 signal_date=t 的信号，并在下一交易日 open 执行。
- [ ] 同一执行日映射多个信号时明确选择规则或直接报错，禁止字典静默覆盖。
- [ ] 停牌判断使用 suspend_timing；全天/开盘停牌阻断开盘成交，盘中事件不得被当成开盘前已知事实。
- [ ] 被涨跌停或停牌冻结的已有持仓保持原仓位并记录 unfilled，不把整个组合运行伪装成成功调仓。
- [ ] 数据缺失仍严格失败；“不可成交”与“数据缺失”必须是不同状态。

**测试**

- [ ] t 日 close 信号只在 t+1 开盘执行。
- [ ] 周末和节假日正确映射到下一开市日。
- [ ] 多信号同执行日不会静默覆盖。
- [ ] 涨停买不入、跌停卖不出、停牌持仓冻结均有确定结果。

### Task 2.4：历史 universe 来源门禁

**文件**

- 修改：backend/app/quant_research/repository.py
- 新增：backend/app/quant_research/universe.py
- 修改：backend/tests/test_quant_trust_contract.py

**实施**

- [ ] universe 配置必须是 explicit_snapshot、industry_membership 或后续可用的 index_membership 之一。
- [ ] explicit_snapshot 保存排序后的代码文件和 SHA-256。
- [ ] 静态当前股票池用于横截面历史回测时标记 survivorshipRisk；不能给出无偏 readiness。
- [ ] industry_membership 使用 in_date/out_date 逐日恢复成员。
- [ ] 上市日、退市日和成员区间按历史日期过滤。
- [ ] 本阶段不因缺少指数权重伪造权重；需要指数历史成分的研究保持 blocked 或明确换用已有行业成员。

**Phase 2 Gate**

- [ ] 所有“追加未来数据不改变历史前缀”测试通过。
- [ ] 黄金数据集的公告、复权、执行和 universe 边界全部通过。
- [ ] 旧 archive 代码不得成为新 runner 的依赖。

---

## Phase 3：建立可复现研究运行闭环

### Task 3.1：定义 canonical 运行配置和运行登记

**文件**

- 新增：backend/app/quant_research/run_config.py
- 新增：backend/app/quant_research/runner.py
- 新增：backend/migrations/versions/0004_research_runs.py
- 修改：backend/app/models.py
- 修改：backend/app/schemas.py
- 新增：backend/tests/test_research_run_registry.py

**ResearchRun 最小字段**

- run_id：每次尝试的 UUID。
- reproducibility_key：config + snapshot + code + environment + seed 的 SHA-256。
- strategy_id、status、stage。
- config、config_sha256、data_snapshot_id。
- code_commit、environment_sha256、random_seed。
- metrics、result_fingerprint、artifact_root。
- started_at、heartbeat_at、finished_at、error。

**配置必须包含**

- strategy_id 和版本。
- scope、universe 定义、start/end、warmup_start。
- benchmark。
- 特征参数和目标权重参数。
- execution policy、cost model。
- random_seed、timezone=Asia/Shanghai。
- quality_run_id 和允许的 warning 白名单。

**验收**

- [ ] 配置做 canonical JSON 序列化；键顺序和本机环境不影响 config_sha256。
- [ ] 缺 code_commit、snapshot 或 seed 时拒绝正式运行。
- [ ] run_id 允许不同尝试；reproducibility_key 对相同输入保持一致。

### Task 3.2：冻结一致性输入快照

**文件**

- 新增：backend/app/quant_research/snapshot.py
- 新增：backend/app/quant_research/artifacts.py
- 新增：backend/tests/test_research_snapshot.py

**实施**

- [ ] 在一个 PostgreSQL REPEATABLE READ、READ ONLY 事务中读取所有输入表。
- [ ] 查询必须限定 universe、日期、字段，禁止默认导出全库。
- [ ] 日期 ISO-8601、Decimal 固定字符串、null 固定空值语义、列顺序和行顺序固定。
- [ ] 使用 gzip mtime=0；SHA-256 基于未压缩 canonical bytes。
- [ ] table_artifacts 记录文件名、字段、自然键、行数、内容哈希。
- [ ] snapshot_id 由所有 table artifact hash、scope、universe_hash 和日期配置合成。
- [ ] 只有所有文件 fsync 并完成哈希后，临时目录才能原子 rename 为完成目录。
- [ ] 失败快照保持 failed 状态；不得出现数据库登记 complete 但文件不完整。

**容量门禁**

- [ ] 导出前估算行数和磁盘体积。
- [ ] 预计完成后剩余空间必须大于 max(5 GiB, 2 × 本次预计产物大小)，否则 blocked。
- [ ] 不自动删除旧快照；只输出候选清单并等待用户确认。

**测试**

- [ ] 相同事务切片重复导出得到相同 hash。
- [ ] 改动一行输入后得到不同 snapshot_id。
- [ ] 在线 DB 改动后，旧 snapshot 文件仍可读取并通过 hash 校验。
- [ ] 中途异常不会留下可被 runner 误认的 complete 快照。

### Task 3.3：强化 manifest 和环境指纹

**文件**

- 修改：backend/app/quant_research/manifest.py
- 修改：backend/Dockerfile
- 修改：docker-compose.yml
- 修改：scripts/ops/deploy_server.sh
- 新增：backend/tests/test_research_manifest.py

**实施**

- [ ] 部署时注入 APP_GIT_COMMIT；release 目录没有 .git 时也必须得到真实提交。
- [ ] 记录 schema revision、Python 版本、依赖锁文件哈希、容器/构建标识和时区。
- [ ] 记录 quality_run、snapshot、universe、配置、随机种子、limitations 和 artifact hashes。
- [ ] APP_GIT_COMMIT 缺失时只能运行 test 模式，不能产生正式 manifest。
- [ ] result_fingerprint 排除 run_id、generated_at 等运行元数据，只包含确定性结果。

**验收**

- [ ] 服务器 release 的 manifest 能对应到 origin/main 或明确部署提交。
- [ ] 相同输入两次运行的 result_fingerprint 相同。
- [ ] 修改配置、代码提交、输入快照或 seed 中任一项，reproducibility_key 必须变化。

### Task 3.4：提供 run 和 reproduce CLI

**文件**

- 新增：scripts/research/run_quant_research.py
- 新增：scripts/research/reproduce_quant_research.py
- 修改：backend/app/quant_research/__init__.py
- 新增：backend/tests/test_research_reproduction.py

**运行目录**

- config.json
- manifest.json
- quality.json
- inputs/*.csv.gz
- targets.csv.gz
- nav.csv.gz
- metrics.json
- limitations.json
- checkpoints/*.json

**实施**

- [ ] run 顺序固定为 quality gate → input snapshot → features/targets → simulation → metrics → manifest → finalize。
- [ ] 每个 stage 写原子 checkpoint 和输入/输出 hash。
- [ ] reproduce 只读取旧 snapshot，不访问在线行情表。
- [ ] reproduce 比较 targets、nav、metrics 和 result fingerprint；不比较时间戳字段。
- [ ] 失败时 ResearchRun 标记 failed 或 interrupted，并保留可审计错误；不得写 succeeded。

**测试与验收**

- [ ] 黄金数据集连续运行两次，确定性产物逐字节相同。
- [ ] 修改在线 DB 后重现旧 run，结果仍相同。
- [ ] 损坏一个输入文件后，reproduce 在计算前因 hash 不匹配而失败。

### Task 3.5：建立无收益主张的 sentinel baseline

**文件**

- 新增：backend/app/quant_research/baselines.py
- 新增：configs/research/sentinel_etf_baseline.json
- 新增：backend/tests/test_sentinel_baseline.py
- 修改：docs/research/README.md

**实施**

- [ ] 第一条真实闭环只使用已有 ETF、基金复权和指数基准，不依赖分钟线、期权或新数据源。
- [ ] 采用简单、可手算、无参数搜索的持有/固定再平衡基线，目标是验证管线而非寻找 alpha。
- [ ] 输出显著标注 research-only、not-investment-advice 和 limitations。
- [ ] 财务横截面 baseline 在 Phase 2 的 revision policy 未满足前不进入正式验收。

**Phase 3 Gate**

- [ ] sentinel 在黄金数据和真实小范围数据各成功运行一次。
- [ ] 真实运行可以离线 reproduce。
- [ ] 研究产物跨容器重启和 release 切换仍存在。

---

## Phase 4：任务租约、重启恢复和运行可靠性

### Task 4.1：扩展同步任务状态机

**文件**

- 修改：backend/app/models.py
- 新增：backend/migrations/versions/0005_job_leases.py
- 修改：backend/app/main.py
- 新增：backend/app/sync_worker.py
- 新增：backend/tests/test_job_leases.py
- 新增：backend/tests/test_sync_worker_postgres.py

**DataSyncJob 新增字段**

- attempt_count、max_attempts。
- lease_owner、lease_expires_at、heartbeat_at。
- next_attempt_at、last_error、updated_at。

**状态机**

- queued → running → ok / partial / failed。
- running 且 lease 过期 → queued（仍可重试）。
- attempt_count 达上限 → failed。
- SIGTERM → 停止抢新任务；当前任务安全退出或等待租约过期。

**实施**

- [ ] 使用 FOR UPDATE SKIP LOCKED 原子抢占。
- [ ] 每个 worker 有唯一 owner id。
- [ ] 心跳使用独立短事务，不能与长同步事务共用 session。
- [ ] 重试采用有上限的退避；参数错误、未知 action 等永久错误不重试。
- [ ] active_key 在最终状态或明确中断后释放。
- [ ] 原有自然键 upsert 保证重复执行不产生重复行。

**测试**

- [ ] 两个 worker 同时抢任务，只有一个获得执行权。
- [ ] 模拟 worker 崩溃和租约过期，新 worker 能恢复。
- [ ] 重复执行同一同步任务后自然键重复数为 0。
- [ ] 达到 max_attempts 后不会无限循环。

### Task 4.2：API 与 worker 解耦

**文件**

- 修改：backend/app/main.py
- 修改：docker-compose.yml
- 修改：backend/Dockerfile
- 修改：backend/tests/test_sync_jobs.py
- 修改：README.md

**实施**

- [ ] POST /api/sync-jobs 只提交数据库记录，不再调用 BackgroundTasks.add_task。
- [ ] 新增 worker service，复用后端镜像，命令为 python -m backend.app.sync_worker。
- [ ] worker 使用 restart: unless-stopped。
- [ ] API 重启不影响已入队任务。
- [ ] 后端生产 CMD 移除 --reload；开发热更新另走明确的本地命令，不带入服务器默认进程。

**验收**

- [ ] 停止 API 时 worker 不丢 queued 任务；API 恢复后查询状态正常。
- [ ] 停止 worker 时 API 仍能入队；worker 恢复后继续执行。
- [ ] API 和 worker 同时重启后，过期 running 任务可恢复。

### Task 4.3：研究运行可中断和续跑

**文件**

- 修改：backend/app/quant_research/runner.py
- 修改：scripts/research/run_quant_research.py
- 修改：scripts/research/reproduce_quant_research.py
- 新增：backend/tests/test_research_resume.py

**实施**

- [ ] runner 每完成一个阶段更新 heartbeat、stage 和 checkpoint hash。
- [ ] 进程重启后 stale running 转为 interrupted，而不是永远 running。
- [ ] --resume RUN_ID 逐阶段校验 checkpoint，已完成且 hash 正确的阶段不重复计算。
- [ ] 任何输入或代码指纹不一致时拒绝原 run 续跑，只能新建 run。
- [ ] 不要求本阶段自动重启所有研究；要求安全恢复、显式续跑且不产生半成品成功记录。

**验收**

- [ ] 在 snapshot、simulation 和 finalize 三个阶段分别模拟中断，均可从最后有效 checkpoint 继续。
- [ ] 损坏 checkpoint 时停止，不静默跳过。

### Task 4.4：修复日更并发和刷新顺序

**文件**

- 修改：scripts/ops/sync_today_market_data.sh
- 修改：scripts/ops/install_daily_sync_cron.sh
- 修改：scripts/ops/tests/

**实施**

- [ ] 使用 flock 防止 20:30 日更重叠。
- [ ] cron 只负责提交并轮询 durable job。
- [ ] 同步和财务更新完成后再 refresh db overview。
- [ ] 日更完成后运行“小范围最新交易日质量检查”；blocked/failed 写日志并返回非零。
- [ ] 非交易日明确输出 skipped，不把 0 行写入当成成功更新。

**验收**

- [ ] 同时启动两个日更脚本，只有一个执行。
- [ ] 任务完成后的 overview updated_at 晚于本次同步完成时间。
- [ ] 失败质量门禁能在 cron 日志中定位 rule_id。

### Task 4.5：worker 健康和观测

**文件**

- 新增：backend/migrations/versions/0006_worker_heartbeats.py
- 修改：backend/app/models.py
- 修改：backend/app/main.py
- 修改：frontend/src/main.jsx（仅只读状态）

**实施**

- [ ] worker heartbeat 持久化 worker_id、code_commit、started_at、last_seen 和 current_job_id。
- [ ] /api/health 返回 worker age 和 stale 状态，但不暴露凭据或 payload。
- [ ] 前端只展示 worker 在线/过期、队列长度、失败数和最近完成时间。
- [ ] write API 继续只监听 loopback；本阶段不扩展公网访问。

**Phase 4 Gate**

- [ ] 真实 PostgreSQL 集成环境完成 API/worker/research 三类重启测试。
- [ ] 没有任务永久停在 running。
- [ ] worker 重复执行不会破坏幂等性。

---

## Phase 5：CI、远端迁移和最终验收

### Task 5.1：建立真实 PostgreSQL 测试矩阵

**文件**

- 新增：docker-compose.test.yml
- 新增：scripts/ops/test_postgres_integration.sh
- 新增：.github/workflows/ci.yml
- 修改：README.md

**测试矩阵**

- [ ] Python py_compile。
- [ ] SQLite 快速单测。
- [ ] PostgreSQL 16：空库 migration 到 head。
- [ ] PostgreSQL 16：baseline schema stamp 后 migration 到 head。
- [ ] 数据质量、快照、runner、worker lease 集成测试。
- [ ] 黄金数据未来前缀不变和重现测试。
- [ ] docker compose config。
- [ ] 前端 typecheck/build（仅当前端有改动时仍作为 CI 常规门禁）。
- [ ] bash -n 所有运维脚本。
- [ ] git diff --check。

### Task 5.2：远端 sandbox 迁移演练

**安全约束**

- [ ] 不在当前活动 PG 直接试错。
- [ ] 不执行 docker compose down -v 或 docker volume rm。
- [ ] 服务器只有约 6.6 GiB 空间，不在同机复制完整 19 GB 数据库。
- [ ] 使用当前 PG 的 schema-only dump + 代表性小样本创建隔离临时 PG。
- [ ] 若生产迁移需要新全量备份，先验证空间；优先流式保存到本机/外部安全位置。空间不足时阻断，不挤满根盘。

**演练**

- [ ] 临时 PG 恢复 current schema。
- [ ] fingerprint 校验与 stamp baseline。
- [ ] upgrade head。
- [ ] 运行质量规则、sentinel、reproduce 和 worker crash recovery。
- [ ] 重复 migration 和重复同步验证幂等。
- [ ] 记录 EXPLAIN、索引空间和迁移耗时。

### Task 5.3：生产迁移确认门禁

正式 PG 迁移前必须再次取得用户确认，并提供：

- 将新增/修改的表、列和索引清单。
- 预计锁表方式、耗时和磁盘变化。
- 最新备份位置及 pg_restore -l 验证结果。
- sandbox 演练结果。
- 回滚策略。

未经确认，不执行生产 alembic upgrade、DROP INDEX 或历史数据清理。

### Task 5.4：生产发布与验收

**实施**

- [ ] 使用服务器 CI/CD 流程构建新 release，不覆盖活动 volume。
- [ ] 注入可验证 APP_GIT_COMMIT。
- [ ] 依次启动 db、api、worker、frontend。
- [ ] migration 单独执行，不绑在 API 自动启动中。
- [ ] 运行 health、readiness、quality、sentinel 和 reproduce。
- [ ] 执行 queued/running 重启恢复演练。
- [ ] 确认 PostgreSQL 5432 仍不暴露公网。

**最终证据**

- [ ] quality run ID 与规则汇总。
- [ ] data snapshot ID 与每个输入文件 SHA-256。
- [ ] research run ID、reproducibility key、result fingerprint。
- [ ] 两次重现结果对比。
- [ ] worker crash/recovery 时间线。
- [ ] migration revision、Git commit、Docker 状态和 HTTP smoke。
- [ ] 磁盘前后、数据库和索引体积。

**文档**

- [ ] 更新 docs/agent-handoff.md。
- [ ] 更新 docs/agent-code-map.md。
- [ ] 更新 README.md 和 docs/research/README.md。
- [ ] 每个阶段开始和结束追加操作日志.md。

---

## 推荐提交边界

1. trust-contract-and-golden-fixtures
2. alembic-baseline-and-data-quality
3. point-in-time-and-prefix-invariance
4. reproducible-snapshot-and-runner
5. durable-postgres-worker
6. ci-deploy-and-acceptance-docs

每个提交前至少运行本阶段相关测试和 git diff --check。不要把所有阶段堆成一个不可回滚的大提交。

## 不得隐式改变的边界

- 不删除 PostgreSQL volume。
- 不删除当前历史脏数据。
- 不把 token、密码或私钥写入 manifest、artifact、日志或测试。
- 不导入真实持仓、成交或券商数据。
- 不把 sentinel baseline 展示成买入/卖出建议或收益承诺。
- 不因为缺少分钟线、期权或新数据源而阻塞本计划。

## 第一执行入口

用户确认 D0 后，从 Phase 0 的合同/黄金夹具开始，然后执行 Phase 1 的 migration baseline。第一次触碰真实 PostgreSQL 前，必须停在 Task 5.3 再次提供迁移清单并请求确认。
