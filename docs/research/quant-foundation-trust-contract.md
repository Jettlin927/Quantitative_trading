# 量化研究底座可信合同

状态：Phase 0 冻结版 1.0
适用范围：`backend/app/quant_research/` 下的新离线研究链路

## 目的与边界

本合同定义一次离线量化研究何时可被称为“数据完整、无未来函数、结果可复现”。任何新 loader、特征、组合模拟或 runner 都必须遵守本合同，不得在单个策略中另造更宽松的时点规则。

本合同只服务离线研究和组合模拟，不连接券商，不导入真实持仓或成交，不产生真实委托，不将回测输出表述为收益承诺。分钟线、Tick、期权和新付费数据源不是当前合同的必要输入。

## 规范术语

| 术语 | 定义 |
| --- | --- |
| `quality_scope` | 一次质量评估的完整边界：研究类型、日期区间、宇宙、所需表/字段、基准和允许的 warning。不能用“全库有数据”代替。 |
| `universe` | 每个研究日可能进入计算的标的集合，必须有明确来源和历史有效区间。 |
| `universe_provenance` | 宇宙的来源类型、源文件/表、成员与生效日期、版本及限制项。 |
| `universe_hash` | 对标准化 `universe_provenance` 和按日期排序的成员记录计算的 SHA-256。 |
| `observed_at` | 市场参与者在真实时间中最早可观测到该信息的时点。 |
| `available_from` | 数据允许进入特征或成交约束的最早时点；取经济上可得时间和数据源可得时间中更晚者。 |
| `signal_date` | 产生目标权重的研究日。一个信号只能使用 `available_from` 不晚于信号计算时点的信息。 |
| `execution_date` | 目标权重按执行协议首次尝试成交的交易日。默认收盘信号在下一交易日开盘执行。 |
| `data_snapshot` | 在一致性只读事务中冻结的精确输入切片，包含表、字段、行、自然键排序、行数和内容哈希。 |
| `data_snapshot_id` | 由所有输入 artifact 哈希、scope、日期和 `universe_hash` 合成的 SHA-256。 |
| `reproducibility_key` | 对标准化配置哈希、`data_snapshot_id`、代码提交、环境指纹和随机种子计算的 SHA-256。它标识“同一个可复现问题”，不等于每次尝试的 `run_id`。 |

## Quality scope 合同

每次研究级质量检查至少必须声明：

```json
{
  "scope": "a_share_cross_section",
  "startDate": "2026-01-05",
  "endDate": "2026-01-23",
  "warmupStart": "2026-01-05",
  "universeProvenance": {
    "type": "industry_membership",
    "source": "industry_members",
    "sourceKey": "SYNIND.SI"
  },
  "universeHash": "sha256:<64-lowercase-hex>",
  "requiredInputs": {
    "stock_daily_bars": ["open", "high", "low", "close", "vol", "amount"],
    "stock_adjust_factors": ["adj_factor"],
    "stock_limit_prices": ["up_limit", "down_limit"],
    "stock_suspend_events": ["suspend_type", "suspend_timing"],
    "stock_financial_indicators": ["end_date", "ann_date", "roe"]
  },
  "benchmark": "SYNIDX.SH",
  "allowedWarnings": []
}
```

评估结果只能是：

- `ready`：当前 scope 的所有阻断规则通过。
- `ready_with_warnings`：研究切片可用，但存在已明确排除、已列入 limitations 的非阻断问题。
- `blocked`：研究切片缺少关键数据，或违反时点、宇宙或基准合同。
- `failed`：质量检查本身超时或异常。它不能被降级成 warning，也不能被解释为数据缺失。

表存在、全表非空、全表最早/最晚日期或全表总行数，都不能单独产生研究级 `ready`。质量结果必须能定位到 `rule_id`、表、失败数量和有上限的样例键。

## Universe provenance 合同

新研究只允许下列宇宙来源：

1. `explicit_snapshot`：按代码排序的显式标的文件，保存文件哈希、生成时间、来源和用途。
2. `industry_membership`：根据 `industry_members.in_date/out_date` 逐日还原成员，同时受 `stock_listings.list_date/delist_date` 约束。
3. `index_membership`：只有在未来具备历史指数成分/权重表后才能使用；当前缺表时必须 `blocked`，不得用当前成分冒充历史成分。

成员资格默认按包含边界计算：

```text
list_date <= trade_date <= delist_date（delist_date 为空时无上界）
in_date   <= trade_date <= out_date（out_date 为空时无上界）
```

如数据源对 `out_date` 另有明确定义，必须在 provenance 中改写边界语义并加入 limitations。

`stocks`、`stock_listings` 或自选股池的当前静态截面可用于当日展示或 `explicit_snapshot` 研究，但不能被表述为无幸存者偏差的历史横截面。此类研究必须记录 `survivorshipRisk=true`，不能获得“无偏” readiness。

`universe_hash` 的输入必须是 UTF-8 的 canonical JSON：键字典序、日期为 `YYYY-MM-DD`、标的和成员记录按自然键排序、无本机绝对路径和生成时间。

正式 `explicit_snapshot` 必须能读取实际成员文件，文件内容经去空行、去注释、代码大写和排序后必须与 members 完全一致；仅提供任意非空 source 文本不能通过。`universe_hash` 绑定成员文件内容哈希而不是本机路径，因此同一工件跨 worktree/容器保持一致。`as_of_date` 必须是有效 ISO 日期，`NaT`、数字和无效字符串均拒绝。

正式交易日历必须由完整 `trade_calendars` 源记录构造 `OpenTradeCalendar`，同时绑定真实存在的 CSV/CSV.GZ 工件及其 canonical CSV 内容 SHA-256。构造和每次使用时都必须重新读取工件，并验证工件实际规范化记录、传入记录和内容哈希三者一致。组合模拟和财务可用日映射拒绝裸日期列表；伪造路径、修改工件，或从源记录删掉一个真实开市日，都必须在计算前失败。

## 信息可得时间矩阵

下表的“最早用途”是保守上限。数据行的 `created_at/updated_at` 只表示入库时间，不得反向证明它在历史当时已经可得。

| 表 | 关键信息 | `observed_at` / `available_from` | 最早可用于下单 | 必须记录的限制 |
| --- | --- | --- | --- | --- |
| `trade_calendars` | `is_open`、`pretrade_date` | 官方日历确认后可用；临时休市以最终状态为准 | 只用于映射下一交易日，不产生信号 | 交易所、时区和日历版本 |
| `stocks` | 当前基础信息 | 只是入库时的当前截面 | 不能单独决定历史买入资格 | 静态截面和幸存者偏差 |
| `stock_listings` | `list_date`、`delist_date`、`list_status` | 官方生效日起可用；未知公告时间时不得假设提前知道未来退市 | 上市生效后可进入当日资格；退市边界按 provenance 声明 | 当前单行状态不等于完整历史事件流 |
| `stock_daily_bars` | `open` | 当日首笔成交后才可观测 | 不得用当日 `open` 生成并假设成交在同一 `open` | 成交时点、缺价和停牌语义 |
| `stock_daily_bars` | `high/low/close/vol/amount` | 当日收盘后可得 | 基于它们的 `signal_date=t` 只能在下一交易日开盘首次执行 | 行情修订和入库截止时间 |
| `stock_daily_basic` | 估值、市值、换手 | 默认当日收盘后可得 | 下一交易日开盘 | 供应商发布延迟和修订政策 |
| `stock_financial_indicators` | `end_date`、`ann_date`、指标 | 有公告时间时按真实时间；只有日期时，`available_from` 固定为 `ann_date` 后的下一交易日 | `available_from` 当日开盘；不允许在 `ann_date` 当日使用 | 报告期、修订版本、`update_flag` 和未知公告时间 |
| `stock_adjust_factors` | `adj_factor` | 只能用截至当日的因子构造总回报序列 | 当日总回报信息用于下一交易日开盘 | 未来因子不得重标历史前缀 |
| `stock_limit_prices` | `up_limit`、`down_limit` | 当日盘前已知的官方涨跌停边界 | 可用于限制当日开盘成交 | `ts_code` 必须属于当日股票 universe；域外代码必须报告 |
| `stock_suspend_events` | `suspend_type`、`suspend_timing` | “全天/开盘”事件可在开盘前约束；盘中事件只在发生后可得 | 全天/开盘停牌阻断开盘成交；盘中事件不得伪装为盘前已知 | `suspend_timing` 未知时保守阻断并写入 limitations |
| `funds` | 基金主数据、`list_date` | 官方生效日起可用 | 只在已上市日期参与研究 | 退市/清盘信息不完整时写入 limitations |
| `fund_daily_bars` | OHLCV | 与 `stock_daily_bars` 相同：open 开盘后，其余收盘后 | 收盘信号在下一交易日开盘 | 必须配套有效基金复权因子 |
| `fund_adjust_factors` | `adj_factor` | 与股票复权因子相同，只允许因果前缀 | 当日总回报信息用于下一交易日开盘 | 分红或份额变化日及数据修订 |
| `indices` | 指数主数据 | 官方发布后可用 | 不直接产生成交；用于标识基准 | 基准代码、发布方和收益口径 |
| `index_daily_bars` | 指数 OHLCV | 当日收盘后可得 | 不直接作为成交价；用于同日收盘后评估 | 必须与研究日期有完整重叠 |
| `industry_classifications` | 行业主数据 | 分类版本发布后可用 | 不单独决定成员 | 分类版本与来源 |
| `industry_members` | `in_date`、`out_date` | 按官方生效日还原；公告时间未知时不允许提前使用未来成员 | 只有在当日已生效的成员才可进入宇宙 | 边界包含规则、公告时间缺失和历史修订 |

## 无未来函数不变式

新研究链路必须同时满足：

1. **前缀不变**：在原截止日之后追加行情、复权因子、公告或成员变化，原截止日及之前的特征和目标权重逐字段不变。
2. **公告日保守**：财务数据只有日期、没有时间时，`ann_date` 当日不可见，下一交易日才可见。
3. **收盘信号与开盘执行分离**：使用 t 日收盘信息生成的信号，最早在 t 后的下一交易日开盘尝试执行。
4. **不可成交不等于缺数据**：停牌或涨跌停导致的未成交必须显式记录；缺必要价格或复权因子必须失败，不得用旧价或原始价静默回退。
5. **静态宇宙不伪装历史宇宙**：任何当前成员列表用于历史横截面时都必须暴露幸存者风险。
6. **先规范化再校验**：`signal_date`、`available_date` 和标的代码必须先转成统一形式，再做重复键和单日权重合计；数字日期和字符串布尔值不得利用隐式类型转换绕过门禁。

## 数据快照与可复现键

正式研究不得直接把“当前在线表”当作可复现输入。每次运行必须从同一个 `REPEATABLE READ, READ ONLY` 事务读取精确切片，按各表自然键稳定排序，生成 canonical artifact 并计算 SHA-256。供应商后续 upsert 不得改变已完成 run 的输入。

```text
config_sha256 = sha256(canonical_json(config))
data_snapshot_id = sha256(canonical_json(scope + universe_hash + table_artifact_hashes))
reproducibility_key = sha256(
  canonical_json(config_sha256 + data_snapshot_id + code_commit + environment_sha256 + random_seed)
)
```

canonical 序列化不得包含 `run_id`、生成时间、临时目录、本机绝对路径或未排序字典。正式运行缺少真实 `code_commit`、`data_snapshot_id`、环境指纹或随机种子时必须拒绝。

## 输入合同示例

### ETF 时序研究

- scope：`etf_time_series`。
- universe：`explicit_snapshot`，标的为合成夹具中的 `SYNETF.SZ`，字符串按升序写入快照后计算 `universe_hash`。
- 日期：`2026-01-05` 至 `2026-01-23`，共 15 个开市日。
- 必需输入：`trade_calendars`、`funds`、`fund_daily_bars`、`fund_adjust_factors`、`indices`、`index_daily_bars`。
- 基准：`SYNIDX.SH`。
- 时点：`2026-01-09` 收盘后产生目标权重，最早在周末后的 `2026-01-12` 开盘执行。
- 失败口径：缺任一开市日基金日线、正数复权因子或基准收盘价时 `blocked`。

### A 股横截面研究

- scope：`a_share_cross_section`。
- universe：`industry_membership`，使用合成夹具 `SYNIND.SI` 的历史成员记录，再与 `stock_listings` 的上市/退市边界求交集。
- 日期：`2026-01-05` 至 `2026-01-23`；`SYN002.SH` 只有资格至包含 `2026-01-20` 的退市边界。
- 必需输入：股票日线、复权因子、涨跌停价、停牌事件、历史成员、上市边界和指数基准；本示例额外声明使用财务指标。
- 时点：`SYN001.SZ` 在周五 `2026-01-09` 发布且时间未知的记录，最早于 `2026-01-12` 可用；`2026-01-09` 当日特征必须为空。
- 可交易性：`SYN001.SZ` 在 `2026-01-12` 全天停牌、`2026-01-14` 开盘涨停；`SYN002.SH` 在 `2026-01-15` 开盘跌停。这些是未成交约束，不能被伪装成数据缺失。

## 黄金验收夹具

`backend/tests/fixtures/quant_research_golden/` 是本合同的最小、完全合成验收数据集。它固定 2 只股票、1 只 ETF、1 个指数和 15 个交易日，并显式覆盖周末、停牌、涨跌停、复权因子跳变、公告日/下一交易日和退市边界。

夹具文件必须按 README 声明的自然键稳定排序，日期固定为 ISO-8601，数字不使用本机 locale，不包含真实 Tushare 数据、token、真实持仓或券商信息。

## 旧策略 archive 边界

`docs/research/strategy-results/`、`backend/app/ma_strategy_stats.py`、`backend/app/value_sector_strategy.py` 及相关旧回测产物只是历史只读 archive：

- 可保留作为历史展示或过往决策的记录。
- 不是本合同的代码来源、黄金夹具、baseline 或验收证据。
- 新 runner 不得 import 它们，新 readiness 不得因它们存在而放行。
- 旧结果不得被重新标注为新底座可复现运行。

## 变更规则

本合同变更时，必须同步更新黄金夹具说明和 `backend/tests/test_quant_trust_contract.py`。任何放宽 `available_from`、执行时点、宇宙边界或缺数据失败口径的变更，都必须给出不会引入未来函数的新黄金测试证据。
