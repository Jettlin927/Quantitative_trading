# 量化研究底座审计与实施方案

日期：2026-07-10

## 结论

最新 `main` 已经是合格的量化数据工作台，但还不是统一、可复现的量化研究底座。P0 数据层已经覆盖交易日历、A 股原始日线、复权因子、估值、财务指标、指数、ETF、申万行业和历史行业成员；当前主要缺口是历史上市股票池、每日可交易性数据，以及所有研究共同遵守的标准协议。

本阶段不以某个策略盈利为成功标准。成功标准是：相同代码、参数和数据快照可以复现相同研究；任何未来数据、缺失复权、缺失可交易性或缺失基准都会被发现或阻断。

## 专业量化研究底座需要的层级

| 层级 | 必需能力 | 当前仓库 | 本阶段 |
| --- | --- | --- | --- |
| 数据源与血缘 | 来源、自然键、幂等同步、覆盖日期、失败日志 | 已具备 | 保持 |
| 时间轴 | 正式交易日历、统一时区、严格信号/成交时点 | 已具备交易日历 | 纳入统一协议 |
| 价格口径 | 原始价格、股票/ETF 复权因子、可解释的调整后价格 | 股票因子已具备，ETF 因子缺失，研究脚本未统一 | 补 ETF 因子和统一构造器 |
| 历史股票池 | 上市、暂停上市、退市和历史行业成员 | 行业成员已具备；上市状态缺失 | 补 `stock_listings` |
| 可交易性 | 停复牌、涨跌停、无行情日、成交容量 | 仅旧脚本局部猜测 | 补事件/价格表和严格门禁 |
| Point-in-time | 财务数据只在公告后可见，禁止未来函数 | DB 有 `ann_date`，脚本各自实现 | 补统一 as-of 关联 |
| 研究组合 | 信号日与执行日分离、成本、现金、权重漂移 | 旧脚本各自模拟 | 补下一交易日开盘模拟器 |
| 评估 | 基准、年化、波动、Sharpe、回撤、超额、跟踪误差 | 不统一 | 补统一指标 |
| 验证 | 样本内/样本外、rolling/anchored walk-forward | 当前主线缺失 | 补窗口生成器 |
| 可复现性 | Git commit、参数哈希、数据快照、限制项、run id | 当前结果不完整 | 补 manifest |
| 工程门禁 | 单元测试、PostgreSQL 集成验证、ready/blocked | 有通用测试，无研究门禁 | 补 readiness |
| 交易隔离 | 无券商、无真实账户、无自动下单 | 已具备 | 保持 |

## 当前已有能力

- PostgreSQL 16、FastAPI、SQLAlchemy、React/Vite 和 Docker Compose 三容器架构。
- `stocks`、`stock_daily_bars`、`stock_daily_basic`、`stock_financial_indicators`。
- `trade_calendars`、`stock_adjust_factors`、`indices`、`index_daily_bars`。
- `funds`、`fund_daily_bars`、`industry_classifications`、`industry_members`。
- `data_sync_runs`、覆盖度 API、只读查询、远端 sandbox 和 24 个通过的后端测试。
- 两套历史探索脚本，但它们不是新研究底座的协议来源。

## 当前缺口

### 阻断 A 股横截面研究

- `stocks` 只同步当前 `list_status=L`，无法还原历史退市样本，存在幸存者偏差。
- 没有正式的每日涨跌停价格和停复牌事件表。
- 没有统一的缺价处理规则；旧脚本存在把缺价延用或跳过的不同做法。

### 阻断研究可复现

- 没有统一的调整后价格构造函数和 point-in-time 财务关联函数。
- 没有统一的下一交易日开盘组合模拟协议。
- 没有标准基准指标、walk-forward 窗口和 run manifest。
- 旧研究结果未完整记录代码版本、参数哈希和数据库快照。

## 本阶段实现范围

### 1. P1 数据可信性

- 新增 `stock_listings`、`stock_limit_prices`、`stock_suspend_events`、`fund_adjust_factors`。
- 新增 Tushare 幂等同步接口和只读查询。
- 不自动迁移真实 PostgreSQL；先完成代码、内存库测试和远端隔离验证。

### 2. `backend/app/quant_research/`

- `dataset.py`：复权 OHLC、财务公告日 as-of 关联、历史成员筛选。
- `repository.py`：只允许显式股票池，加载历史上市状态、复权、涨跌停和停复牌合同；禁止隐式使用当前全市场。
- `portfolio.py`：目标权重在下一交易日开盘生效，处理现金、权重漂移和买卖成本；缺少持仓价格或开盘不可买卖时严格失败。
- `metrics.py`：绝对与基准相对指标，所有输出 JSON-safe。
- `validation.py`：anchored/rolling walk-forward 窗口。
- `manifest.py`：run id、配置 SHA256、Git commit、数据快照、限制项和交易隔离标记。
- `readiness.py`：区分 ETF 时间序列和 A 股横截面研究；缺表或空关键表返回 `blocked`。

### 3. 明确不做

- 不恢复旧 Risk8/B1/RAM/Kronos 或旧 `strategy_research`。
- 不新增自动调参、策略排名、买卖评级或盘中信号。
- 不连接真实券商，不导入真实持仓，不执行真实交易。
- 不把大型逐事件结果继续提交到 Git。

## 验收标准

1. 复权构造缺少因子时失败，不静默回退到原始价格。
2. 财务记录满足 `ann_date <= trade_date`，未来公告永远不可见。
3. 信号日目标最早在下一交易日开盘生效。
4. 持仓缺少开盘/收盘价，或目标调仓违反停牌、涨停不可买、跌停不可卖约束时，组合模拟失败。
5. 指标包含总收益、年化、波动、Sharpe、最大回撤、Calmar，以及有基准时的超额收益、跟踪误差和信息比率。
6. walk-forward 窗口训练集严格早于测试集。
7. manifest 对相同参数生成相同配置哈希，并显式标记 `researchOnly=true`、`executionEnabled=false`。
8. readiness 能让 ETF 时间序列研究独立 ready，并在 A 股横截面关键数据缺失时 blocked。
9. 后端编译、全部单元测试、Compose config 和远端隔离验证通过。

## 后续阶段

- 全量回填 `stock_listings`、`stock_limit_prices`、`stock_suspend_events`。
- 增加指数成分与权重，支持正式历史基准股票池。
- 增加数据质量日报：缺口、重复、异常复权跳变、基准日期错位。
- 在底座验收后，再新建一个最简单的教学 baseline；baseline 只验证流程，不作为候选策略或投资建议。

## 本次实施状态

- 已完成 P1 四张表、同步/查询 API、统一研究协议和 40 个全量单元测试。
- 已通过后端编译、内存 SQLite 回归、Compose 配置和差异检查。
- 未修改前端，未迁移或回填真实 PostgreSQL，未重启本地或远端服务。
- 远端隔离验证因当前 Windows 环境缺少项目 SSH 私钥而未完成；恢复认证后仍需补做，因此“代码可用”不等于“真实数据已 ready”。
