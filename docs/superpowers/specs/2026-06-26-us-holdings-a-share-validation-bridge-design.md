# US Holdings Operation + A-Share Validation Bridge Design

## 背景

当前仓库是本地 A 股量化研究工作台，Docker 中的 PostgreSQL 主要沉淀 Tushare A 股基础信息、日线、估值、财务指标和研究池数据。用户现在的真实操作对象逐渐转向美股，尤其是 AI 算力扩张相关主线，包括存储、互联、供电、散热、先进封装、数据中心基础设施和 SpaceX/Starlink 上游。

本次转型不是把仓库硬改成美股数据库，也不是废弃 A 股研究资产。新的方向是：

- 美股层服务真实持仓、观察池和后续操作判断。
- A 股层继续利用 Tushare 数据密度做规则验证和大样本实验。
- 桥接层把 A 股验证出来的通用交易纪律反哺美股持仓动作。

## 目标

建立一个软转型架构，让仓库同时支持：

1. 观察美股持仓和观察池，并生成有针对性的后续操作分析。
2. 使用 A 股/Tushare 数据验证交易规则是否有效。
3. 把验证结论转成可复用的美股操作纪律，例如不追高、止跌后加仓、财报前降风险、同因子杠杆敞口上限。
4. 保留现有 Docker PostgreSQL 中的 A 股数据和表结构，不删除 volume，不破坏历史研究可复现性。
5. 保持 Windows/PowerShell 和 Docker Compose 运行体验。

## 非目标

- 不连接真实券商、真实交易账户或任何可触发资金变化的接口。
- 不把量化模型输出写成自动买卖指令。
- 第一版不迁移 Docker PostgreSQL 里的 A 股历史数据到新表。
- 第一版不引入 Alembic 或大型迁移框架。
- 第一版不把 `/Users/jettlin/code/投资分析` 的 `.env.local`、真实持仓/成交 CSV、历史报告和大体积行情缓存直接复制进主仓。
- 第一版不重写现有 React/FastAPI 工作台主页面。

## 核心假设

- 用户的真实收益机会主要来自美股主线，而不是继续在 A 股上寻找直接交易策略。
- Tushare 仍是当前最稳定、最方便、已付费的数据源，因此 A 股数据库适合作为策略规则验证沙盘。
- A 股验证只能证明通用交易规则和风控纪律是否有统计支持，不能证明某个美股主题或单票一定有效。
- 美股操作层应先轻量文件化，待闭环有效后再考虑是否入库。

## 总体架构

```text
美股操作问题
  -> US holdings / watchlist / thesis ledger
  -> 生成具体规则问题
  -> A 股 Tushare 大样本验证
  -> 规则结论与适用边界
  -> 美股持仓动作标签
  -> HTML/Markdown 操作报告
```

系统分三层：

1. **US 操作层**
   - 管理美股持仓、观察池、主题标签、催化事件和 thesis ledger。
   - 输出持仓动作标签：`继续持有`、`只等回调`、`止跌后小加`、`减仓降风险`、`观察不动`。

2. **A 股验证层**
   - 继续使用 Docker PostgreSQL、Tushare 和现有 A 股研究代码。
   - 验证通用规则：趋势跟随、回调站稳、追高风险、止盈止损、市场退潮过滤、同因子集中度。

3. **桥接层**
   - 把验证结果整理成规则卡片。
   - 每条规则必须说明：验证市场、样本窗口、核心指标、失败条件、能否映射到美股、映射限制。

## 目录结构

第一版新增轻量目录，不改变现有 Docker 架构：

```text
my_quant/
  us_research/
    README.md
    config/
      watchlist_symbols.csv
      theme_taxonomy.csv
      rule_mapping.yaml
    data/
      holdings_sample.csv
      snapshots/
    reports/
      latest_us_operations.html
      latest_us_operations.md
    scripts/
      build_us_operations_report.py
      refresh_us_snapshot.py
    tests/
      test_rule_mapping.py
      test_us_operations_report.py

docs/
  research/
    us-bridge/
      README.md
      rules/
        no_chase_after_extended_gap.md
        add_only_after_stop_confirmation.md
        leverage_same_factor_budget.md
      experiments/
        2026-06-26-no-chase-validation.md
```

说明：

- `my_quant/us_research/` 负责可运行代码和本地文件产物。
- `docs/research/us-bridge/` 负责长期可读规则、实验证据和结论。
- 第一版使用 sample holdings，真实持仓文件留在本地或由用户明确指定，不默认提交。

## 数据边界

### 保留在 Docker PostgreSQL 的数据

- A 股基础列表。
- A 股日线 OHLCV。
- A 股 daily_basic 和财务指标。
- A 股研究池、同步记录和现有回测产物。

这些数据继续由现有 Tushare 流程维护。第一版不修改 `docker-compose.yml`、数据库 volume 名称或现有 A 股表结构。

### 美股第一版数据形态

美股第一版使用 CSV/JSON 文件，不入 Docker DB：

- `watchlist_symbols.csv`：ticker、name、theme、subtheme、role、instrument_type、risk_tag。
- `holdings_sample.csv`：ticker、instrument、quantity、cost_basis、theme、leverage_factor、notes。
- `snapshot JSON/CSV`：quote、52 周高低、MA20、MA50、MA200、距高点、20 日动量、60 日动量、波动率、数据时间戳。
- `prediction ledger`：后续从 `/Users/jettlin/code/投资分析/prediction_ledger_2026.csv` 迁移结构，不直接搬真实历史文件。

### 后续可选入库

当文件化闭环稳定后，再考虑新增通用资产表：

- `assets`
- `asset_daily_prices`
- `asset_snapshots`
- `theme_watchlists`
- `portfolio_positions`
- `prediction_ledger_entries`
- `rule_validation_runs`

这属于第二阶段，需要单独设计数据库迁移和备份流程。

## 数据源策略

第一版采用“免费优先、可降级、标记新鲜度”的策略：

- `yfinance`：历史 K 线、均线、动量、52 周高低。
- Finnhub：quote、profile 和基础公司信息；复用现有 `/Users/jettlin/code/投资分析` 的脚本经验。
- 手动 CSV：真实持仓、观察池、thesis ledger，避免连接券商。
- 后续可评估 Polygon、Financial Modeling Prep、Alpha Vantage，但第一版不绑定付费源。

每个快照产物必须记录：

- `source`
- `fetched_at`
- `market_session`
- `is_stale`
- `errors`

如果数据源失败，报告必须显示 `stale` 或 `partial`，不能把旧数据当作当前判断。

## 第一批规则验证主题

第一版只验证能直接反哺用户美股操作的问题：

1. **不追高规则**
   - 问题：强趋势票大幅跳涨后，直接追入是否显著增加回撤？
   - A 股验证：高开/突破后 N 日买入 vs 等回调确认。
   - 美股映射：对 `SNXX`、`MVLL`、`CRDU` 等高 beta 或杠杆表达标记 `只等回调`。

2. **止跌确认后加仓规则**
   - 问题：下跌途中买入 vs 站回关键均线/放量止跌后买入，哪种更稳？
   - A 股验证：跌破 MA20/BBI 后重新站回、成交量改善、KDJ/动量修复。
   - 美股映射：对回调后的 AI 主线票标记 `止跌后小加`。

3. **同因子杠杆预算规则**
   - 问题：同一 AI 硬件因子下多个 2x/高 beta 持仓叠加时，组合回撤如何放大？
   - A 股验证：同一风格或行业高相关组合的回撤与集中度关系。
   - 美股映射：限制 `SNXX + MVLL + CRDU + BEX` 等同因子风险预算。

4. **财报/催化前仓位规则**
   - 问题：财报前加仓是否值得，还是应等待确认？
   - A 股验证：公告/高波动事件日前后的追入和减仓效果，若数据不足则只做结构化复盘，不强行量化。
   - 美股映射：财报前默认 `不加仓` 或 `减风险`，除非已有明确盈利垫和低集中度。

## 输出报告

第一版报告为 HTML + Markdown 双产物：

```text
my_quant/us_research/reports/latest_us_operations.html
my_quant/us_research/reports/latest_us_operations.md
```

报告必须包含：

- 持仓总览：ticker、主题、工具类型、杠杆系数、风险标签。
- 主线状态：AI 算力、存储、互联、供电、散热、封装等主题热度与趋势。
- 个股动作标签：`继续持有`、`只等回调`、`止跌后小加`、`减仓降风险`、`观察不动`。
- 规则证据：每个动作标签引用哪条 A 股验证规则或美股本地证据。
- 风险区：同因子集中、杠杆损耗、财报/催化、价格过热、数据陈旧。
- 后续观察：下一次需要复核的 price level、signal、thesis 或 catalyst。

报告结论不能写成真实交易指令，只能写成人工研究辅助。

## Windows 与 Docker 兼容

第一版必须满足：

- 保留现有 `.cmd` 启停脚本语义。
- PowerShell 脚本使用 UTF-8 with BOM。
- 示例命令同时给 macOS/Linux 与 Windows PowerShell 版本。
- 不把 `/Users/jettlin/...` 写进容器内路径或默认配置。
- Docker 里的 `postgres_data` volume 不删除、不重建。
- 新增脚本能在仓库根目录运行，路径使用 `pathlib.Path`。
- 真实 token 只来自 `.env`、`.env.local` 或环境变量，不进入源码、报告或测试。

## 实施阶段

### Phase 1: 设计和文件化闭环

目标：不动 Docker DB，不改主前端，先跑通 US 操作报告。

交付：

- `my_quant/us_research/` 目录。
- sample watchlist / holdings。
- 美股快照脚本。
- 操作报告生成脚本。
- 规则映射配置。
- 最小测试。

验证：

- 报告可生成。
- 无真实凭据泄漏。
- 缺数据时报告显示 `partial`。

### Phase 2: A 股规则验证桥接

目标：把 1-2 条规则用 A 股数据验证，并形成规则卡片。

交付：

- `docs/research/us-bridge/rules/*.md`。
- A 股验证 run 或复用现有 run 的证据引用。
- 报告中能引用规则卡片。

验证：

- 每条规则有数据窗口、指标、失败条件。
- 明确“可映射”和“不可映射”的边界。

### Phase 3: Docker DB 可选扩展

目标：文件化闭环稳定后，考虑新增跨市场资产表。

交付：

- 数据库备份脚本。
- 新表设计。
- 非破坏性 schema 初始化脚本。
- Windows PowerShell 初始化命令。

验证：

- `docker compose config` 通过。
- 现有 A 股接口仍可用。
- 新表创建不会删除旧表或 volume。

## 成功标准

第一版成功的标准不是收益率，而是工作流闭环：

- 用户能看到当前美股持仓和观察池的动作标签。
- 每个动作标签有可追溯理由。
- 至少一条动作规则来自 A 股大样本验证，而不是纯主观判断。
- 报告明确标出数据新鲜度和风险。
- 不破坏现有 A 股 Docker 数据库和回测能力。

## 风险与对策

- 风险：A 股规则强行映射到美股，造成伪科学。
  - 对策：每条规则必须写明映射限制；主题判断仍以美股基本面、催化和趋势为主。

- 风险：真实持仓数据误提交。
  - 对策：默认只提交 sample；真实持仓路径走本地配置和 `.gitignore`。

- 风险：数据源不稳定。
  - 对策：报告内显示 `stale` / `partial`，不隐藏失败。

- 风险：过早改 Docker DB。
  - 对策：第一版文件化；DB 扩展进入 Phase 3 并要求备份。

- 风险：仓库同时承担 A 股和美股导致混乱。
  - 对策：A 股是验证沙盘，美股是操作层；目录、文档和报告中明确分工。

## 需要用户确认的点

1. 第一版真实持仓是否只读本地 CSV，不提交到 Git。
2. 美股观察池初始来源是否从 `/Users/jettlin/code/投资分析/watchlist_symbols_2026.csv` 结构化迁移。
3. 第一条优先验证规则是否选择“不追高规则”。
4. Phase 1 是否暂时不改 React 前端，只生成 HTML/Markdown 报告。

## 自检

- 本 spec 没有要求删除 Docker volume。
- 本 spec 没有要求连接券商或自动下单。
- 本 spec 没有把 A 股验证结论等同于美股单票收益保证。
- 本 spec 第一版没有引入数据库迁移工具。
- 本 spec 明确保留 Windows/PowerShell 兼容要求。
