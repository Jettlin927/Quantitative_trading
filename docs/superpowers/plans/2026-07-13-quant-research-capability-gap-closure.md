# 离线量化研究能力短板补齐计划

> 状态：Phase 0–5 实现与最终验收已完成；精确代码验收提交为 `891b2825b62c8e91576ee54d04fbafc738c95f69`，等待合并 `main` 与推送。实施基线为 `main@ca8495fccdecad53f10748eb9f0d408ed3b26d4e`。参考对象为 `goldmansachs/gs-quant@release-2.0.14`，只吸收适合本仓库的通用研究、组合模拟和风险分析概念，不复制其 Marquee、衍生品、实时定价或交易体系。

## 目标

在不突破“日频、离线研究、无券商、无真实账户、无自动发布”边界的前提下，把当前可信量化底座从“只能跑固定单 ETF sentinel”推进到：

1. 同一可信 runner 可以运行多个明确登记的研究策略，而不是硬编码一条 sentinel。
2. 特征、目标权重、模拟执行、组合状态和绩效指标都有稳定合同与 canonical 工件。
3. 至少具备一条 ETF 时序 baseline 和一条不依赖财务修订历史的 A 股横截面 baseline。
4. 组合层能解释换手、成本、未成交、持仓和基础风险贡献。
5. 任何新增结果仍绑定 quality run、冻结 snapshot、代码、环境、随机种子、checkpoint 和结果指纹，并可完全离线复现。

本计划不是“复制 GS Quant”。GS Quant 的公开仓库同时是高盛平台 SDK，大量数据、定价、风险和组合能力依赖机构凭据与远端服务；本仓库只实现当前研究目标真正需要的最小子集。

## 明确假设

- 数据频率继续只支持日频；分钟线、Tick 和实时流不进入本计划。
- 资产范围只包括现有 A 股、指数和 ETF 数据；美股继续仅保留 sample 只读边界。
- 新能力统一放在 `backend/app/quant_research/`，不恢复旧 `strategy_research`、`backtest-reports`、`strategy-lab` 或 `research_engine`。
- 正式运行只读取冻结输入；策略代码不能绕过 snapshot 查询在线行情表。
- 继续使用当前 pandas/标准库能力；本计划不引入 `gs-quant`、Zipline、Backtrader、Celery、Redis、PyArrow 或新的优化框架。
- 前端继续只做数据工作台，不增加策略选择、回测执行、交易评级或真实账户入口。
- `TODO.md` 继续只负责 PostgreSQL 数据维度路线；本计划负责研究与风险能力，不把两份路线混写。

## 明确不做

- 利率、外汇、信用、商品和复杂衍生品 instrument 模型。
- Greeks、收益率曲线、波动率曲面和远端定价服务。
- 真实订单、成交回报、券商连接、账户组合或自动调仓。
- 自动参数搜索、自动挑选最优策略、自动发布研究结果。
- MCP/Agent 研究执行入口；核心 CLI 和工件合同稳定前不增加新的调用表面。
- 严格财务横截面 baseline。当前供应商历史修订不可完整重建，正式 readiness 必须继续 blocked。

## 当前基线与真实短板

### 已完成，不重复建设

- 研究范围级数据质量与 `ready / ready_with_warnings / blocked / failed` 状态。
- PostgreSQL `REPEATABLE READ + READ ONLY` 精确输入快照。
- canonical CSV.gz、SHA-256、manifest、结果指纹和完全离线 reproduce。
- ResearchRun checkpoint、显式 resume、损坏检测和中断恢复。
- Alembic `0001` 至 `0006_worker_heartbeats`、隔离 PostgreSQL 集成测试和四容器 CI。
- 下一交易日开盘执行、停牌/涨跌停约束、复权前缀不变和财务公告保守可得日。

### 仍缺少

1. `runner.py` 在 `features_targets`、`simulation` 和 `metrics` 三阶段直接调用 sentinel 函数，`strategyId` 还不是实际分发键。
2. 正式 snapshot 只开放 `etf_time_series`；A 股质量规则虽已存在，但 A 股正式冻结输入与 runner 尚未闭环。
3. 特征层只有复权、point-in-time 关联和少量基础函数，没有受合同约束的可复用时序/横截面特征集。
4. 组合模拟只输出每日 NAV 汇总；买卖请求、模拟执行、逐日持仓和阻断原因没有独立 canonical 工件。
5. 指标只有收益、波动、Sharpe、回撤、Calmar、胜率、跟踪误差和信息比率，缺少成本、换手、集中度、回撤持续期和基础风险归因。
6. `build_walk_forward_windows()` 已存在，但正式 runner 没有生成或评估 walk-forward 工件。
7. 指数历史权重、行业代理净值仍属于 `TODO.md` 的数据缺口；在补齐前不能宣称完整指数归因或行业基准复制能力。

## 核心设计选择

### 1. 使用静态白名单，不做插件框架

新增一个最小 `StrategyDefinition` 登记表。每个策略只声明：

- `strategy_id`、`strategy_version` 和允许的 `scope`。
- 必需的冻结输入表。
- 参数校验函数。
- 从冻结输入生成目标权重的函数。
- 从冻结输入准备公共模拟价格面板的函数。
- 固定 limitations。

runner 只允许从源码白名单按 `strategyId` 查找定义。禁止 import-by-string、entry point、动态文件扫描和用户上传代码。

组合模拟、指标、manifest、checkpoint 和归档验证仍由公共 runner 负责，策略不得各自复制一套流水线。

### 2. 特征函数只实现 baseline 实际使用的集合

第一批只实现：

- 简单收益率和区间收益率。
- 移动平均。
- 滚动波动率。
- 滚动 z-score。
- 横截面 percentile rank。
- 横截面 winsorize。
- 等权目标权重生成。

所有函数必须保留 warmup 产生的空值，不得 backfill，不得用未来窗口居中，不得静默删除日期或标的。后续策略需要新函数时再逐项增加，不预先复制 GS Quant 的大规模 timeseries API。

### 3. “模拟账本”不伪装真实订单

新增工件使用以下名称：

- `rebalance_requests.csv.gz`：目标变化和计划动作。
- `rebalance_executions.csv.gz`：实际模拟执行、未执行权重、成本和阻断原因。
- `positions.csv.gz`：每日收盘后模拟持仓权重。

不用 `orders`、`fills` 等可能被误解为真实交易的名称。manifest 继续固定 `executionEnabled=false`。

### 4. 先证明 ETF 通用化，再开放 A 股横截面

ETF 能复用当前正式 snapshot，适合作为最小增量验证。A 股横截面需要新增冻结切片、历史 universe 和更大的容量检查，应单独过 Phase Gate，不能和策略分发一起大改。

### 5. 风险层先做透明计算，不先引入求解器

第一版风险层只做确定性的暴露、协方差、beta、集中度和风险贡献；分配层只做逆波动率、单票上限、行业上限、现金下限和换手上限的可解释规则。若未来必须引入二次规划依赖，另行提交依赖、精度和运维影响并先询问用户。

### 6. 统一历史 universe 契约，不维护第二份股票池

当前质量层使用 `industry_membership`，研究辅助层使用 `historical_membership`，两者还没有正式闭环。Phase 3 统一使用以下 canonical 定义：

```json
{
  "mode": "industry_membership",
  "source": "industry_members",
  "sourceKey": "<申万行业代码>"
}
```

- 不再为 A 股 baseline 新建手工股票列表；配置只声明来源表和行业代码。
- 质量检查在同一个只读一致性事务内，根据 `industry_members.in_date/out_date`、`stock_listings.list_date/delist_date` 和开市日历解析逐日成员，按 `trade_date,ts_code` 排序后计算 canonical hash。
- `DataQualityRun.universe_hash` 绑定解析后的逐日成员工件，而不是绑定请求中的当前代码列表或本机文件路径；registry 只保存来源、日期、hash 和计数，不把大面板塞进 JSON。
- snapshot 在自己的 `REPEATABLE READ + READ ONLY` 事务中重新解析；结果必须与质量运行的日期、来源键、计数和 hash 完全一致，随后再冻结为 canonical `universe.csv.gz`。质量检查后若成员表发生变化，snapshot 必须要求重新跑质量检查，不能静默接受漂移。
- 现有 `explicit_snapshot` ETF 合同保持不变；旧的 `historical_membership` 辅助命名在 Phase 3 测试迁移后删除，不保留双别名。

## 全局完成标准

只有以下条件全部满足，才能称为本计划完成：

1. 未登记或 scope 不匹配的 `strategyId` 在读取 snapshot 前失败。
2. 现有 sentinel 在同一黄金输入上的 targets、NAV、metrics 字节内容保持不变；既有 v1 完成归档仍可验证和 reproduce。
3. ETF 趋势 baseline 能走完整 quality → snapshot → features/targets → simulation → metrics → manifest → finalize，并在断开数据库后重复 reproduce 得到同一结果指纹。
4. 给输入追加原截止日之后的行情后，原截止日及之前的特征、目标权重和模拟账本逐字段不变。
5. 模拟执行、成本、现金和持仓工件能够逐日对账；阻断动作不会消失，也不会被算作已成交。
6. A 股 baseline 的 universe 按历史行业成员与上市边界逐日求交，质量运行与 snapshot 绑定同一逐日成员 hash，不使用当前股票列表冒充历史股票池。
7. A 股 baseline 不读取财务指标；缺复权、涨跌停、停牌、上市状态或基准重叠时必须 blocked 或失败。
8. walk-forward 只汇总 test 窗口，不把训练区间收益混入 OOS 结论，不执行自动调参。
9. 风险贡献之和与组合风险在数值容差内一致；同一冻结输入重复计算完全确定。
10. 后端全量单测、隔离 PostgreSQL 集成测试、Compose config、Shell 语法和 `git diff --check` 全部通过。

---

## Phase 0：冻结兼容合同与失败测试

### Task 0.1：锁定 sentinel 向后兼容

**文件**

- 新增：`backend/tests/test_research_strategy_dispatch.py`
- 修改：`backend/tests/test_sentinel_baseline.py`
- 修改：`backend/tests/test_research_reproduction.py`
- 修改：`backend/tests/test_research_resume.py`

**先写失败测试**

- [x] 未登记 `strategyId` 被拒绝，且拒绝发生在冻结 snapshot 前。
- [x] `strategyId` 与 scope 不匹配被拒绝。
- [x] 禁止把模块路径、文件路径或 Python 表达式当作策略 ID。
- [x] 现有 sentinel 黄金 targets、NAV、metrics 不因分发层改变。
- [x] v1 完成归档继续可验证并完全离线 reproduce。
- [x] checkpoint 中的策略身份改变时 resume 明确拒绝。

### Task 0.2：扩展可信合同

**文件**

- 修改：`docs/research/quant-foundation-trust-contract.md`
- 修改：`docs/research/README.md`
- 修改：`backend/tests/test_quant_trust_contract.py`

**实施**

- [x] 定义 strategy registry、feature availability、模拟账本、walk-forward OOS 和风险工件合同。
- [x] 明确策略只能读取冻结输入，不能自行连接 DB 或网络。
- [x] 定义 v1/v2 归档兼容边界和结果指纹包含范围。
- [x] 明确所有新增 baseline 都是研究管线示例，不是策略推荐或收益承诺。

**Phase 0 Gate**

- [x] 失败测试确实能在旧实现上失败。
- [x] 合同没有放宽 point-in-time、下一交易日执行、缺数据失败或 universe 血缘规则。

---

## Phase 1：最小策略分发与 ETF 趋势 baseline

### Task 1.1：建立静态策略登记表

**文件**

- 新增：`backend/app/quant_research/strategy_registry.py`
- 修改：`backend/app/quant_research/run_config.py`
- 修改：`backend/app/quant_research/runner.py`
- 修改：`backend/app/quant_research/baselines.py`
- 修改：`backend/app/quant_research/__init__.py`

**实施**

- [x] 用冻结 dataclass 加常量字典登记策略；不建立继承树。
- [x] 先把 `sentinel_etf_baseline` 适配到登记表，保持原有函数和输出合同。
- [x] runner 在进入 quality gate 前解析策略定义，并在三个现有阶段调用公共定义。
- [x] base config 校验与策略参数校验分开；未知参数必须拒绝，不能静默忽略。
- [x] checkpoint 身份继续绑定 `strategyId + strategyVersion + configSha256`。

### Task 1.2：增加最小特征函数

**文件**

- 新增：`backend/app/quant_research/features.py`
- 新增：`backend/tests/test_research_features.py`

**实施**

- [x] 只实现本计划“核心设计选择 2”列出的函数。
- [x] 每个滚动函数显式声明 window、min_periods 和排序要求。
- [x] 横截面函数按 `trade_date` 分组，并保持 `ts_code` 稳定排序。
- [x] 拒绝重复自然键、非有限数、混合时区和非单调输入。

**反例测试**

- [x] 追加未来行情不改变旧特征前缀。
- [x] warmup 不足保持空值，不用更短窗口冒充完整窗口。
- [x] 同值排名、空值和极端值在不同运行中结果一致。

### Task 1.3：新增 ETF 趋势 baseline

**文件**

- 新增：`backend/app/quant_research/etf_trend_baseline.py`
- 新增：`configs/research/etf_trend_baseline.json`
- 新增：`configs/research/etf_trend_universe.txt`
- 新增：`backend/tests/test_etf_trend_baseline.py`
- 修改：`scripts/research/run_quant_research.py`

**固定研究定义**

- [x] scope 为 `etf_time_series`，universe 为一只显式 ETF。
- [x] 使用收盘复权价和固定 120 个开市日移动平均。
- [x] 每月最后一个开市日产生目标；收盘价高于均线时目标权重为 1，否则为 0。
- [x] `available_date=signal_date`，最早在下一开市日开盘执行。
- [x] 参数只来自提交进 Git 的 config；不做扫描、搜索或事后挑选。
- [x] limitations 明确记录单资产、日频、固定规则、非 alpha 结论和风险利率假设。

**Phase 1 Gate**

- [x] sentinel 全部兼容测试通过。
- [x] ETF 趋势黄金测试覆盖周末、月末、warmup、均线交叉和下一日执行。
- [x] ETF 趋势正式小样本能离线 reproduce；数据库不可连接时仍匹配。
- [x] 本阶段不修改 DB schema、Compose 或前端。

---

## Phase 2：可审计模拟账本与指标

### Task 2.1：输出逐动作和逐持仓工件

**文件**

- 修改：`backend/app/quant_research/portfolio.py`
- 修改：`backend/app/quant_research/runner.py`
- 修改：`backend/app/quant_research/artifacts.py`
- 新增：`backend/tests/test_research_simulation_ledger.py`
- 修改：`backend/tests/test_quant_research_foundation.py`

**最小数据合同**

`rebalance_requests.csv.gz`：

- `execution_date, signal_date, ts_code, requested_change, side`

`rebalance_executions.csv.gz`：

- `execution_date, signal_date, ts_code, requested_change, executed_change, blocked_change, status, reason, transaction_cost_rate`

`positions.csv.gz`：

- `trade_date, ts_code, close_weight`

**实施**

- [x] 公共 simulator 一次计算同时返回 NAV 与三个账本 DataFrame，不能从 NAV 事后猜测成交。
- [x] `status` 只允许 `filled / partial / blocked`；`reason` 使用固定枚举。
- [x] 停牌、涨停、跌停、现金不足和估值沿用分别记录，不合并成自由文本。
- [x] 所有工件有固定列、自然键、排序、canonical hash 和 checkpoint 引用。
- [x] 每日 `positions + cash`、执行变化、成本和 NAV 可在容差内闭合。

### Task 2.2：补齐研究指标

**文件**

- 修改：`backend/app/quant_research/metrics.py`
- 新增：`backend/tests/test_research_metrics_extended.py`

**新增指标**

- [x] downside volatility、Sortino；无风险利率固定为 0 并写入 limitations。
- [x] 最大回撤持续期。
- [x] 平均/最大单边换手、累计成本率。
- [x] 最大/平均持仓数、最大单票权重、HHI 集中度。
- [x] blocked/partial 请求比例和累计未执行权重。
- [x] 对基准的 beta；样本不足或基准方差为 0 时返回 null。

### Task 2.3：归档 schema v2 与 v1 兼容

**文件**

- 修改：`backend/app/quant_research/manifest.py`
- 修改：`backend/app/quant_research/runner.py`
- 修改：`backend/tests/test_research_manifest.py`
- 修改：`backend/tests/test_research_reproduction.py`

**实施**

- [x] 新运行写 `artifactSchemaVersion=2`，结果指纹纳入三个账本工件。
- [x] v1 完成归档继续按原工件集合验证和 reproduce，不要求凭空出现 v2 文件。
- [x] v2 缺任一账本、自然键重复、状态非法或对账失败时，在重算前拒绝。
- [x] v1 未完成临时运行不跨版本续跑；给出明确错误并要求新建 run。

**Phase 2 Gate**

- [x] sentinel 与 ETF 趋势均生成可对账的 v2 工件。
- [x] 注入 blocked/partial 动作后，NAV、成本、现金和持仓仍闭合。
- [x] 任意篡改账本文件或 manifest 引用都会在 reproduce 前失败。

---

## Phase 3：A 股正式快照、价格型横截面 baseline 与 walk-forward

### Task 3.1：开放 A 股正式冻结输入

**文件**

- 修改：`backend/app/schemas.py`
- 修改：`backend/app/data_quality/contracts.py`
- 修改：`backend/app/data_quality/rules.py`
- 修改：`backend/app/data_quality/runner.py`
- 修改：`backend/app/quant_research/run_config.py`
- 修改：`backend/app/quant_research/snapshot.py`
- 修改：`backend/app/quant_research/repository.py`
- 修改：`backend/app/quant_research/universe.py`
- 修改：`backend/tests/test_data_api_contracts.py`
- 修改：`backend/tests/test_data_quality_rules.py`
- 修改：`backend/tests/test_quant_research_foundation.py`
- 新增：`backend/tests/test_a_share_research_snapshot.py`
- 修改：`backend/tests/test_research_snapshot_postgres.py`

**冻结切片**

- `trade_calendars`
- `stock_listings`
- `stock_daily_bars`
- `stock_adjust_factors`
- `stock_limit_prices`
- `stock_suspend_events`
- `industry_members`
- `indices`
- `index_daily_bars`
- canonical `universe`

**实施**

- [x] 只允许 `industry_membership + source=industry_members + sourceKey=<index_code>` 作为第一条正式 A 股历史 universe；拒绝本机路径、inline 当前成员和 `asOfDate`。
- [x] `DataQualityRunRequest` 对 `explicit_snapshot` 保持兼容；对 `industry_membership` 不再要求伪造当前股票列表，改为要求唯一 `sourceKey`。
- [x] 质量 runner 在同一只读事务内先解析逐日成员，再用解析出的代码集合执行行情、复权、涨跌停、停牌和基准完整性规则；空行业、区间缺口、重复/重叠成员区间或缺上市边界都保持 blocked。
- [x] 第一版要求 A 股 quality 日期精确等于 `warmupStart..endDate`；registry 记录 canonical 成员 hash、逐日记录数和唯一标的数，避免宽区间与切片 hash 产生歧义。
- [x] 删除质量规则中无条件的 `historical_membership_not_verified_in_quality_slice` 阻断，只在上述数据库解析和 hash 门禁全部通过时放行。
- [x] 研究配置统一使用 `industry_membership`；删除内部 `historical_membership` 双命名并更新辅助函数与测试。
- [x] 成员按每个研究日与上市/退市边界求交；不能只冻结开始日或当前成员。
- [x] snapshot 重新解析出的逐日成员与 quality registry 的来源键、日期、计数和 hash 必须精确一致，并将其写入 canonical `universe.csv.gz`。
- [x] `verify_materialized_inputs()` 按 scope 验证不同冻结表集，不再把 ETF 表集硬编码为唯一合法集合。
- [x] 容量预检按 universe × 日期和实际行数双重限制；超限在写文件前失败。
- [x] 不冻结 `stock_financial_indicators` 和 `stock_daily_basic`，防止 baseline 偷用尚未解决的修订历史。

### Task 3.2：新增价格型 A 股横截面 baseline

**文件**

- 新增：`backend/app/quant_research/a_share_price_baseline.py`
- 新增：`configs/research/a_share_price_baseline.json`
- 新增：`backend/tests/test_a_share_price_baseline.py`
- 修改：`backend/app/quant_research/strategy_registry.py`

**固定研究定义**

- [x] config 只声明 `industry_membership / industry_members / sourceKey`，不提交一份会与数据库历史成员漂移的股票池文本。
- [x] 每月最后一个开市日，在当日有效历史行业成员中计算价格特征。
- [x] 动量使用过去 120 个开市日至过去 20 个开市日的复权收益；波动使用过去 60 个开市日收益标准差。
- [x] 对两个特征分别做当日横截面 percentile rank，等权合成为分数。
- [x] 选择前 N 名并等权；`N` 和单票上限在 config 固定，不自动搜索。
- [x] 使用当日收盘后可得信息，下一开市日开盘尝试执行。
- [x] 停牌、涨跌停和缺价沿用公共 simulator 规则。
- [x] limitations 明确记录单行业 universe、价格型特征和无财务因子。

### Task 3.3：把 walk-forward 接入正式工件

**文件**

- 修改：`backend/app/quant_research/validation.py`
- 修改：`backend/app/quant_research/runner.py`
- 新增：`backend/tests/test_research_walk_forward.py`

**实施**

- [x] config 增加可选且结构固定的 `validationPolicy`；旧 config 缺省为 `none`。
- [x] 第一版只允许固定参数的 rolling/anchored 窗口评估，不允许窗口内调参。
- [x] 输出 `walk_forward_windows.csv.gz` 和 `walk_forward_metrics.csv.gz`。
- [x] 汇总指标只标记 test/OOS 区间；训练区间只用于形成窗口边界和未来可能的拟合接口。
- [x] 窗口、指标和汇总进入结果指纹与 checkpoint。

**Phase 3 Gate**

- [x] `industry_membership` 在旧实现上仍因未验证而 blocked；新实现只有在真实历史面板、上市边界和 hash 全部验证后才变为 ready。
- [x] quality 完成后修改任一成员区间，旧 qualityRunId 无法创建 snapshot；重跑质量后才能继续。
- [x] 合成黄金数据同时证明历史成员进入/退出、退市、停牌、涨跌停和月末执行。
- [x] 追加未来成员、行情或复权因子不改变旧 targets 与账本前缀。
- [x] 缺任一必需表、字段或基准日期时 quality/snapshot 明确 blocked 或失败。
- [x] A 股小样本在隔离 PostgreSQL 16 完成 run、resume、archive validate 和两次断库 reproduce。

---

## Phase 4：透明风险分析与受约束分配

### Task 4.1：增加基础风险工件

**文件**

- 新增：`backend/app/quant_research/risk.py`
- 新增：`backend/tests/test_research_risk.py`
- 修改：`backend/app/quant_research/runner.py`
- 修改：`backend/app/quant_research/manifest.py`

**实施**

- [x] 从冻结收益和 positions 计算滚动协方差，不查询在线数据。
- [x] 输出每日 gross/net/cash、最大权重、HHI、行业暴露和基准 beta。
- [x] 计算标的边际风险贡献与总风险贡献；缺足够窗口时保持 null。
- [x] 输出 `risk_exposures.csv.gz` 和 `risk_contributions.csv.gz`，进入结果指纹。
- [x] 协方差、权重或风险贡献出现 NaN/Infinity 时明确失败，不自动填零。

### Task 4.2：增加最小受约束分配器

**文件**

- 新增：`backend/app/quant_research/allocation.py`
- 新增：`backend/tests/test_research_allocation.py`

**实施**

- [x] 只支持等权和逆波动率两种起始分配。
- [x] 支持单票权重上限、行业权重上限、现金下限和单次换手上限。
- [x] 使用固定顺序的裁剪与再归一化，保证跨运行确定性；不宣称全局最优。
- [x] 约束无法同时满足时明确失败，不静默放宽。
- [x] 分配结果仍只是 target weights，不生成真实订单。

### Task 4.3：数据前置门禁

- [x] 基础行业暴露可以使用现有 `industry_members`，不要求新增 schema。
- [x] 完整指数成分归因必须等 `TODO.md` 的 `index_weights` 完成。
- [x] 行业基准比较必须等 `industry_proxy_daily` 或等价可复现工件完成。
- [x] 若实施上述新表，必须单独设计 Alembic revision、隔离 PG 验收和生产迁移方案，并在任何生产 upgrade 前再次取得用户确认。

**Phase 4 Gate**

- [x] 风险贡献之和与组合波动在固定容差内一致。
- [x] 所有约束在黄金样本逐日通过；不可行输入明确失败。
- [x] 追加未来收益不改变旧风险前缀。
- [x] 本阶段仍不出现券商、实盘或自动发布入口。

---

## Phase 5：文档、CLI 与收口

### Task 5.1：形成稳定研究入口

**文件**

- 修改：`scripts/research/run_quant_research.py`
- 修改：`scripts/research/reproduce_quant_research.py`
- 修改：`docs/research/README.md`
- 修改：`docs/agent-code-map.md`
- 修改：`docs/agent-handoff.md`

**实施**

- [x] CLI 可以列出允许的 strategy ID、版本、scope、必需输入和示例 config，但不能动态安装策略。
- [x] README 给出 sentinel、ETF 趋势、A 股横截面三条端到端命令和产物目录。
- [x] 每条示例都标明 `researchOnly=true`、`executionEnabled=false`、limitations 和数据门禁。
- [x] 不提交真实运行的大型 CSV；正式产物继续写入忽略目录/独立 volume。

### Task 5.2：最终独立反例审计

- [x] 通过独立黑盒审计入口重放未来数据、篡改 snapshot、篡改账本/风险、历史成员漂移、不可成交、约束不可行、OOS-only 和断库 reproduce；当前任务未启用第二实现者，不虚构人员独立性。
- [x] 在精确 commit 上记录测试数、PG 版本、结果指纹和残留限制。
- [x] 只有黑盒审计与最终矩阵通过后，才更新计划状态为完成。

### 最终验收记录

- 精确代码提交：`891b2825b62c8e91576ee54d04fbafc738c95f69`。
- 本地仓库门禁：全部已跟踪 Python 文件 `py_compile`、两份 Compose config、`scripts/ops/*.sh` 语法和 `git diff --check` 通过；固定黑盒审计 12/12 通过。
- 本地数据库门禁：隔离 tmpfs PostgreSQL `16.14` 全量运行 219 项，全部通过、0 跳过，退出码为 0。
- 黄金运行证据：合成 A 股正式运行的 `resultFingerprint=ca9243de1c5fb8599cc589710f63c8358e9f6c9e1c5e1e0b261a0e200df71806`，`reproducibilityKey=ba779af6f6aac548c31aa18146dd5498f2fc9cd685abe3eeeaf82e0ad6423f23`；断开数据库依赖后连续两次 reproduce 均匹配。
- 远端隔离复核：当前源码只同步到 `/tmp`，复用现有生产 API 镜像但不连接生产 PostgreSQL、不重启生产服务；Compose、Shell、Python 编译、黑盒 12/12 和 SQLite 全量 219 项均通过，其中 10 项 PostgreSQL 专用用例按设计跳过。临时目录已删除。
- 服务器磁盘：根盘约 40 GiB、已用 29 GiB、可用 9.0 GiB、77%；当前不需要扩容，5 GiB 继续作为停止构建并通知用户的安全门槛。
- 残留限制：完整指数成分归因仍因缺 `index_weights` blocked，行业基准比较仍因缺 `industry_proxy_daily` blocked；本轮未引入财务修订历史、实时数据、券商、实盘、生产部署或数据库迁移。

## 每阶段统一验证

后端快速门禁：

```bash
DATABASE_URL=sqlite+pysqlite:///:memory: \
python -m unittest discover -s backend/tests -p 'test_*.py' -v
```

涉及 snapshot、runner、resume、归档或 PostgreSQL 语义时：

```bash
scripts/ops/test_postgres_integration.sh
```

仓库门禁：

```bash
git ls-files -z -- '*.py' | xargs -0 python -m py_compile
docker compose config
docker compose --file docker-compose.test.yml config
git diff --check
```

本计划默认不改前端；若未来确需改前端，必须先取得范围确认并运行 typecheck、lint 和 build。

## 推荐实施顺序与提交边界

1. Phase 0：合同与失败测试，单独提交。
2. Phase 1.1：sentinel 策略分发兼容，单独提交。
3. Phase 1.2–1.3：特征函数与 ETF 趋势 baseline，单独提交。
4. Phase 2：模拟账本、扩展指标、v2 归档，单独提交。
5. Phase 3.1：A 股 snapshot，单独提交并先过隔离 PG。
6. Phase 3.2–3.3：A 股 baseline 与 walk-forward，单独提交。
7. Phase 4：风险与分配，至少拆成风险、分配、数据前置三个提交。
8. Phase 5：文档、CLI 和独立审计收口。

每个 Phase Gate 未通过时停止，不把后续阶段叠加到未验证基线上。计划文件只描述实施顺序，不构成生产迁移、服务器发布或新增依赖的自动授权。
