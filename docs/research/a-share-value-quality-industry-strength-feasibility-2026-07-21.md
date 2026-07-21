# A 股价值质量、行业相对强度与低换手组合可行性审查

## 2026-07-22 工程实现状态

- Issue #56、#58 已进入 `main`；本策略按 `a_share_value_quality_industry_strength@1` 开始工程交付，但本节仍不是正式研究结论。
- 两个事前版本固定为“价值质量对照版”和“行业相对强度主版本”；120 日行业强度、Top 20/30 缓冲、60 开市日调仓、单票/行业/换手上限和 1 倍/2 倍成本均由静态配置校验，runner 不动态搜索。
- target builder 只读取冻结 `SW2021/L1` 逐日行业身份、当日估值和 `available_from <= signal_date` 的 `observed` 财务修订；未来财务修订、未来行业成员和未来价格有前缀不变回归。
- A 股账本以目标资金执行次日开盘、100 股整手和 T+1，分别记录佣金、卖出税费、滑点、现金不足、停牌、涨跌停、换手上限以及 20/60 日 ADV 容量部分成交或阻断。
- canonical 指标合同区分主基准 `H00985.CSI`、价值质量对照版和环境参考 `000985.CSI`，并输出主动收益、回撤、成本、换手、集中度和容量；缺基准、缺对照、缺环境或缺 ADV 时返回 `not_available + reason`，不填零。
- `legacy_value_sector_stopfall` 仍只保留为探索线索：它没有 point-in-time 行业、财务修订、canonical snapshot、整数手/阻塞/ADV 账本或匹配总收益基准，旧收益数字不能进入本策略评价。
- 生产遗留财务在 2026-07-21 以前仍是 `legacy_unverified`。因此代码、测试、配置和报告合同完成后也**不得启动历史正式研究**；须等待真实观测窗口满足另行冻结的最低覆盖门槛，再创建独立研究 Issue 并取得精确计划哈希批准。

- 审查日期：2026-07-21（Asia/Shanghai）
- 审查基线：`main@22747c9c5f41474894c8204f10659b8ee005cc74`
- 建议策略身份：`a_share_value_quality_industry_strength` / version `1`
- 审查性质：实现与数据可行性审查，不是正式研究，不产生研究批准，也没有运行回测

## 结论卡

- **判断：具备工程实现条件，但当前不具备启动正式研究条件；若此刻开跑，研究状态只能是 `受阻`。**
- 最强正面证据：生产已有 2010 年以来的大规模 A 股行情、每日估值、财务、上市/退市、复权、涨跌停、停牌、申万行业和指数数据；现有正式底座也已具备 canonical snapshot、下一交易日开盘、不可成交账本、walk-forward、风险暴露与复现身份。
- 最大阻断：财务修订历史被质量规则固定判为 `blocked`；正式 A 股 snapshot 冻结不了 `stock_daily_basic` 和 `stock_financial_indicators`；universe 只接受单一行业代码，无法表达全部申万一级行业横截面；新策略尚未静态登记。
- 数据层额外阻断：当前生产 31 个 SW2021 一级行业共 5,864 条成员记录全部 `outDate=null`，只能证明“当前库有一个开放归属”，不能证明可重建历史行业调整；历史 ST 状态也没有独立事件表。
- 因此下一项 Issue 应是**研究能力补齐与冻结计划准备**，不是直接执行研究。正式运行仍须等代码合并、CI 通过、生产数据修复、完整质量运行通过，并由 `Jettlin927` 对精确 `plan_sha256` 评论 `批准研究 <plan_sha256>`。

上述判断遵守仓库强制门禁：缺 point-in-time、历史成员、可交易性、匹配基准、成本/容量或复现身份时属于 `受阻`，不能用管线成功代替策略有效性（`docs/research/contracts/strategy-evaluation-standard.md:15-42,78-92`）。

## 1. 当前现场事实

### 1.1 代码与生产

- 本工作树 HEAD 为 `22747c9c5f41474894c8204f10659b8ee005cc74`，无本任务产生的预存修改。
- 由主代理完成生产只读读回：[`GET /api/health`](http://127.0.0.1:25173/api/health) 返回 `ok`，生产 Worker 与 `origin/main` 都是同一提交 `22747c9c5f41474894c8204f10659b8ee005cc74`。
- 由主代理完成生产只读读回：[`GET /api/db/overview`](http://127.0.0.1:25173/api/db/overview) 的缓存时间为 `2026-07-20T13:08:57.911751+00:00`，主要库存如下。

| 数据 | 生产覆盖 |
| --- | --- |
| 股票日线 | 14,156,473 行，2010-01-04 至 2026-07-20 |
| 每日估值 | 14,066,772 行，2010-01-04 至 2026-07-20 |
| 财务指标 | 243,708 行、5,531 个代码，公告日 2010-04-12 至 2026-06-04 |
| 上市边界 | 5,866 个代码，其中上市 5,528、退市 338 |
| 涨跌停 | 13,474,242 行，2012-01-04 至 2026-07-20 |
| 停牌事件 | 430,887 行，2012-01-04 至 2026-07-20 |
| 股票复权因子 | 14,134,231 行，2010-01-04 至 2026-07-20 |
| 行业分类/成员 | SW2021 分类 511 条；成员 17,579 条 |

这些数量说明原料规模足以支撑工程开发，但 [`GET /api/research/readiness?scope=a_share_cross_section`](http://127.0.0.1:25173/api/research/readiness?scope=a_share_cross_section) 明确返回 `level=inventory`、`researchReady=false`；它只检查“表是否存在/非空”，不代表特定策略质量门禁通过（`backend/app/quant_research/readiness.py:44-94`）。

### 1.2 模型与迁移已具备的基础

- `stock_daily_basic` 已有 PE/PB、市值、流通市值和换手字段；`stock_financial_indicators` 已有 `ann_date/end_date`、ROE、利润率、负债率和增长字段（`backend/app/models.py:95-152`；基线 migration 见 `backend/migrations/versions/0001_existing_schema_baseline.py:231-290`）。
- `stock_listings` 保存当前一行式 `list_status/list_date/delist_date`；涨跌停、停牌和复权因子有独立日频/事件表（`backend/app/models.py:155-224`）。这足以复用现有成交约束，但当前 schema 没有历史 ST/风险警示状态事件。
- 行业主数据有 `level/src`，成员有 `in_date/out_date`（`backend/app/models.py:305-332`；migration 见 `backend/migrations/versions/0001_existing_schema_baseline.py:407-426`）。字段合同是合理的，但生产内容还没有证明历史版本完整。

## 2. #37—#40 对新方向的约束

四个 Issue 当前均已关闭并发布为 `不通过`，而且都是**历史研究导入**，不是新的正式研究批准：[#37](https://github.com/Jettlin927/Quantitative_trading/issues/37)、[#38](https://github.com/Jettlin927/Quantitative_trading/issues/38)、[#39](https://github.com/Jettlin927/Quantitative_trading/issues/39)、[#40](https://github.com/Jettlin927/Quantitative_trading/issues/40)。生产冻结摘要可从对应评价端点读回：

- #37 波动率管理降低了年化波动，但最大回撤约 `-40.89%`、PBO `77.1%`，只适合作为以后独立验证的风险层，不能作为本次 alpha 主体（[`评价摘要`](http://127.0.0.1:25173/api/research/evaluations/372a554c-3b3d-5a65-8645-440eff2c3e5d/artifacts/summary.json)）。
- #38 低波动准入最大回撤 `-52.82%`，同时差于被动和半仓；“低波”不是下一期方向信号（[`评价摘要`](http://127.0.0.1:25173/api/research/evaluations/11ba00fd-8126-5ac7-9790-d7a57d19391c/artifacts/summary.json)）。
- #39 单 ETF 120 日均线基础成本 CAGR 约 `0.05%`，生产评价明确建议改做 point-in-time A 股横截面相对强度，而不是继续搜索同一 ETF 的均线窗口（[`评价摘要`](http://127.0.0.1:25173/api/research/evaluations/5090dc62-bf14-5263-87fa-7414541f04ae/artifacts/summary.json)）。
- #40 B1 长历史主版本累计亏损 `73.35%`、最大回撤 `90.99%`，单边换手约 `583.3` 倍；评价建议把低换手和行业相对趋势设为新研究的事前结构，而不是继续调整代理参数（[`评价摘要`](http://127.0.0.1:25173/api/research/evaluations/dd8bcc38-75b3-54d8-83df-9285908fe190/artifacts/summary.json)）。

所以新方向应回答三个互相独立的问题：价值质量能否提供横截面 alpha；行业相对强度能否减少行业级价值陷阱；低换手约束能否保住扣费后主动收益。不得再让一个价格门同时承担选股、择时和风控。

## 3. 当前硬缺口

### 3.1 代码级硬缺口

1. **基本面无法进入正式 snapshot。** `A_SHARE_SNAPSHOT_TABLES` 和 snapshot 完整性校验不含 `stock_daily_basic`、`stock_financial_indicators`，`_build_a_share_slices()` 也没有构造这两张表的冻结切片（`backend/app/quant_research/snapshot.py:54-65,331-356,772-940`）。PG 有数据不等于 runner 能使用。
2. **财务修订历史被固定阻断。** 质量规则只要声明使用 `stock_financial_indicators` 就固定产生 `point_in_time.financial_revision_history=blocked`，原因是自然键 upsert 覆盖供应商修订（`backend/app/data_quality/rules.py:321-337`）。模型/采集字段只有 `ann_date/end_date`，没有 `update_flag`、修订序列或供应商版本（`backend/app/main.py:134-138,605-619`）。
3. **历史 universe 只能绑定一个行业。** `build_industry_membership_universe()` 和 `resolve_industry_membership()` 只接受唯一 `sourceKey`（`backend/app/quant_research/universe.py:62-112`）；现有正式 A 股配置也只研究一个行业（`configs/research/a_share_price_baseline.json:37-41`）。这不能生成 31 个 SW2021 一级行业之间的横截面；snapshot 又不冻结 `industry_classifications`，无法把 `L1/src=SW2021` 分类身份绑定进结果。
4. **策略没有进入静态 registry。** 当前登记表只有 ETF、A 股价格基线和 B1，没有 `a_share_value_quality_industry_strength`；未知策略会在 quality gate 前拒绝（`backend/app/quant_research/strategy_registry.py:68-220`）。

### 3.2 数据与语义硬缺口

1. **严格 PIT 财务缺修订版本。** 正式合同要求财务只有日期时从 `ann_date` 后下一交易日可用，并保留报告期、修订版本和 `update_flag`（`docs/research/contracts/quant-foundation-trust-contract.md:90-123`）；当前表不能重建“当时看到的版本”。必须先迁移 schema 并重采/回填可区分修订的来源记录，或把该策略明确维持 `受阻`。
2. **申万历史行业有效期未被生产数据证明。** 由主代理完成生产只读读回：[`GET /api/industries?src=SW2021&limit=1000`](http://127.0.0.1:25173/api/industries?src=SW2021&limit=1000) 有 31 个 L1、134 个 L2、346 个 L3。审查进一步逐个读取 31 个 [`/api/industries/{index_code}/members`](http://127.0.0.1:25173/api/industries/801010.SI/members)：L1 共 5,864 行、5,864 个唯一股票、跨 L1 重复为 0，但 **5,864 行全部 `outDate=null`**。这与“每个股票只有一个当前开放归属”一致，却不能证明历史调入调出/改分类可复原；在修复前不能把它写成无偏历史行业映射。
3. **历史 ST 状态缺失。** `stock_listings` 只有当前名称、当前 `list_status` 和上市/退市日期（`backend/app/models.py:155-169`），没有逐日 ST、*ST、退市整理期或名称变更有效期。正式计划若要求排除 ST，必须新增历史状态来源与快照；若暂不新增，则必须明确“不按 ST 身份排除”，只用当日真实涨跌停和停牌约束，不能声称历史已排除 ST。
4. **容量和冲击未闭合。** 日线已有 `amount`，可以计算 20/60 日 ADV，但公共 `CostModel` 只有固定 buy/sell/slippage rate，执行器没有目标资金规模、ADV 参与率或冲击函数（`backend/app/quant_research/portfolio.py:12-20,48-60,247-258`）。必须输出订单金额、ADV 中位/P95/最大参与率和超限阻塞；至少预登记基础成本与双倍成本。
5. **匹配总收益基准需锁定截止日。** 生产 `H00985.CSI`（中证全指全收益）有 4,000 行、2010-01-04 至 2026-06-29，适合全市场人民币总收益主基准；若不先补到研究截止日，计划 `endDate` 最晚只能取 2026-06-29。`000300.SH` 不匹配全市场股票宇宙，不能继续作为唯一基准。生产证据：[`指数目录`](http://127.0.0.1:25173/api/indices?q=%E4%B8%AD%E8%AF%81%E5%85%A8%E6%8C%87&limit=50)、[`H00985.CSI 日线`](http://127.0.0.1:25173/api/indices/h00985.CSI/daily-bars?start_date=2010-01-01&end_date=2026-07-20)。

## 4. 最小可信策略 v1

建议先冻结一个简单、不可事后扩张的版本，并在同一计划中把“价值质量基线”作为必要对照，而不是搜索多个阈值。

| 模块 | v1 固定规则 |
| --- | --- |
| 经济假设 | 低估值且盈利/资产负债质量较好的公司可能获得长期均值回归；行业相对强度只用于减少行业级价值陷阱；低换手用于减少成本，不被解释为新增 alpha。 |
| 历史宇宙 | SSE/SZSE 普通 A 股，按每日有效上市/退市与可信 SW2021 L1 有效期构造；上市至少 252 个开市日；ST 是否排除按上一节先解决，不能用当前名称回填历史。 |
| 信号时点 | 每 60 个开市日调仓；使用 `t` 日收盘后的行情/估值及 `available_from<=t` 的财务，最早 `t+1` 开盘执行。财务 `ann_date` 当日不可见，下一开市日才可见。 |
| 价值质量分数 | 行业内分别计算正 PE_TTM、PB 的低分位和 ROE、净利率、低负债率的高分位；价值与质量各占 50%，字段缺失不填零，不用增长率和更多因子扩张 v1。 |
| 行业相对强度 | 对每个 L1 的当日有效成员，用因果复权收益合成过去 120 个开市日的等权行业收益并跨行业排名；只保留排名前 50% 的行业。它只是信号，不称为可投资行业基准，避免冒充缺失的 `industry_proxy_daily`（合同边界见 `docs/research/contracts/quant-foundation-trust-contract.md:170-174`）。 |
| 选股与缓冲 | 基线：不加行业强度门、价值质量 Top 20；主版本：行业门后 Top 20。已有持仓只在总排名跌出 Top 30 时退出，新入选必须进入 Top 20。试验登记固定为这两个事前版本。 |
| 组合 | 20 只、初始等权，单票上限 5%，单一 L1 上限 20%，不加杠杆；单次单边换手上限 25%。现有确定性 allocation 已支持等权、单票/行业/现金/换手约束（`backend/app/quant_research/allocation.py:14-80,152-219`）。 |
| 执行与成本 | 下一开市日开盘、100 股整手、T+1；停牌/开盘涨停买入阻塞、停牌/开盘跌停卖出阻塞并写入 ledger。佣金、卖出税费、滑点分列，另做双倍成本；在事前目标资金规模下加入 ADV/冲击。现有正式价格装配已能因果复权并标记涨跌停、停牌和估值延续（`backend/app/quant_research/a_share_price_baseline.py:220-321`）。 |
| 基准 | 主基准 `H00985.CSI` 中证全指全收益；必要对照为同历史 universe、同调仓日、同平均暴露的价值质量基线。不能只与沪深300比较。 |
| 验证 | 2012-01-04 起（由涨跌停/停牌共同覆盖决定）至基准完整截止日；固定 anchored walk-forward；OOS、逐年、方向×波动率、流动性与行业留一拆分；两个版本计入试验登记，报告 DSR/PBO、双倍成本和参数邻域，但不在同一 OOS 搜窗口。 |
| 停止条件 | 扣费后 OOS 不超过价值质量基线和总收益基准；优势集中于少数年份/行业；双倍成本翻负；行业留一系统性失败；或财务/行业 PIT、ADV 容量任一未闭合，则停止或保持 `受阻`。 |

## 5. 旧 `value_quality_sector_stopfall` 能复用什么

生产 [`GET /api/research/strategies`](http://127.0.0.1:25173/api/research/strategies) 显示 `legacy_value_sector_stopfall` 只是 `已归档` 历史档案、`formal_research_count=0`、没有发布结论。仓库 README 也明确历史 `status=ok` 只表示旧脚本执行成功（`docs/research/strategy-results/README.md:17-25`）。

可以复用为单元级参考的只有：价值持续天数、质量过滤、行业聚合特征、20/60/120/180/360 日观察窗口，以及“60 日再平衡、最多 20 只”的探索假设（`backend/app/value_sector_strategy.py:37-153,156-223,226-281`）。旧摘要中的 `42.14%` 总收益和 `-20.18%` 最大回撤只能作为提出新假设的线索（`docs/research/strategy-results/value-sector-stopfall-20260629/summary.json:1-83`）。

不能复用为正式证据或执行核心，原因包括：

- SQL 直接使用当前静态 `stocks.industry`，没有历史成员/分类版本（`scripts/research/run_value_sector_strategy.py:107-152`）。
- 财务在 `ann_date` 当日向后 merge，违反“公告日后下一开市日可用”（`scripts/research/run_value_sector_strategy.py:181-202`；正式可用日函数见 `backend/app/quant_research/dataset.py:54-126`）。
- 使用原始开收盘价，不冻结复权因子；不读取上市/退市、涨跌停、停牌；没有匹配基准、walk-forward、canonical snapshot、运行身份或结果指纹。
- 组合按任意股数买入、整仓卖出，用固定往返成本；缺整手、T+1、阻塞账本、税费分解、ADV/冲击和容量（`backend/app/value_sector_strategy.py:226-304`）。
- 每次再平衡会从所有历史信号中为每只股票保留最后一条，并无信号过期规则（`backend/app/value_sector_strategy.py:296-304`）。

## 6. 建议推进依赖

1. **P0 数据治理**：为财务修订历史设计 migration、采集字段和回填；修复/重新同步 SW2021 L1 历史成员有效期并证明调入调出；决定并实现历史 ST 政策。
2. **P0 正式输入**：增加全市场历史 universe 模式；把 `industry_classifications`、全部 L1 `industry_members`、`stock_daily_basic`、版本化财务及需要的历史状态冻入 A 股 snapshot；补齐对应质量规则和前缀不变黄金测试。
3. **P1 策略实现**：静态登记 `a_share_value_quality_industry_strength`，只实现上面的两个事前版本；复用公共复权、执行账本、allocation、risk、walk-forward 和复现链路，不从旧脚本直接连 PG。
4. **P1 成本容量**：冻结目标资金规模、ADV 窗口、参与率上限和冲击函数；完成基础/双倍成本与行业留一验证合同。
5. **P2 发布门禁**：代码合并、CI、生产数据迁移/同步和生产 quality run 全部通过后，才创建冻结计划并请求精确哈希批准。批准前不得启动正式研究；任何参数、范围、基准或门槛变化都要生成新计划版本。

## 7. 一手来源清单

- 仓库强制规范：`docs/research/contracts/strategy-evaluation-standard.md:15-120`、`docs/research/contracts/quant-foundation-trust-contract.md:90-174`
- 模型与迁移：`backend/app/models.py:62-224,305-332`、`backend/migrations/versions/0001_existing_schema_baseline.py:231-329,407-426`
- 正式快照/universe/registry：`backend/app/quant_research/snapshot.py:54-65,331-356,772-940`、`backend/app/quant_research/universe.py:62-239`、`backend/app/quant_research/strategy_registry.py:68-220`
- 质量与执行：`backend/app/data_quality/rules.py:258-337,843-877`、`backend/app/quant_research/dataset.py:13-150`、`backend/app/quant_research/portfolio.py:12-223`
- 历史探索：`backend/app/value_sector_strategy.py`、`scripts/research/run_value_sector_strategy.py`、`docs/research/strategy-results/value-sector-stopfall-20260629/summary.json`
- GitHub 当前 Issue：[#37](https://github.com/Jettlin927/Quantitative_trading/issues/37)、[#38](https://github.com/Jettlin927/Quantitative_trading/issues/38)、[#39](https://github.com/Jettlin927/Quantitative_trading/issues/39)、[#40](https://github.com/Jettlin927/Quantitative_trading/issues/40)
- 生产只读 API：[`health`](http://127.0.0.1:25173/api/health)、[`db/overview`](http://127.0.0.1:25173/api/db/overview)、[`research/strategies`](http://127.0.0.1:25173/api/research/strategies)、[`SW2021 industries`](http://127.0.0.1:25173/api/industries?src=SW2021&limit=1000)
