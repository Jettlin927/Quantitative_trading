# Agent Handoff

这份文档给后续 Agent 接手当前仓库状态用。规则仍以根目录 `AGENTS.md` 为准。

## 当前接手状态

- 仓库：`E:\coding_things\Quantitative_trading`
- 当前分支：任务完成后已合并到 `main`；接手时仍应先用 `git status -sb` 和 `git log -1` 实时确认。
- 当前任务：异步同步任务、完整个股历史、2012 年起历史回补、覆盖快照、20:30 日更和远端真实 PostgreSQL 验收均已完成。
- 当前边界：不删除 PostgreSQL volume，不导入真实账户数据，不连接券商，不发布交易信号。
- 当前远端发布目录：`/opt/quantitative-trading-release-20260710-2330`；原 `/opt/quantitative-trading` 工作区未被覆盖。
- 当前服务数据库 volume：`quant_todo_p0_postgres_data_todo_p0`。切换前的旧 volume `quantitative-trading_postgres_data` 已于 2026-07-11 经用户明确确认后删除，两份已验证 dump 继续保留作为回滚点。
- 当前远端备份：`/opt/quantitative-trading-backups/pre-2012-history-volume-switch-20260711-0108.dump`，已通过 `pg_restore -l` 校验。

## 建议阅读顺序

1. `AGENTS.md`：项目主管规则、研究边界和数据安全红线。
2. `docs/research/quant-research-foundation-plan-2026-07-10.md`：当前审计、缺口、范围与验收标准。
3. `docs/agent-code-map.md`：从目标到代码位置的导航。
4. `backend/app/models.py`、`backend/app/main.py`：DB schema、同步与查询 API。
5. `backend/app/quant_research/`：新的统一研究协议层。
6. `backend/tests/test_research_data_contracts.py`、`test_quant_research_foundation.py`：新能力合同。
7. `操作日志.md`：实际操作和验证记录。

## 当前能力分层

- P0 数据：交易日历、股票原始日线/复权、估值、财务、指数、ETF、申万行业及历史成员。
- P1 本分支：历史上市状态、每日涨跌停、停复牌事件、ETF/基金复权因子。
- 研究协议：严格复权和 point-in-time、显式历史股票池、下一交易日开盘组合模拟、标准指标、walk-forward、manifest 和 readiness。
- 异步运维：`data_sync_jobs` 持久化任务状态；前端和日更脚本只提交任务并轮询，不把 Tushare token 保存到任务 payload。
- 覆盖性能：`data_overview_snapshots` 保存精确聚合快照，页面默认读取快照；日更和手动同步后通过 `refresh=true` 在后台重算。
- 历史展示：`GET /api/daily-bars` 的日期参数可省略；前端标的研究默认载入数据库全部历史并提供近 1/3/5 年与全部历史视图。
- 美股 sample：继续只允许 sample/脱敏结构入库和只读展示，不属于当前 A 股研究协议扩展范围。

## 仍未完成

- readiness 仍是表级门禁，不是逐标的逐日期的数据质量证明；正式研究仍需针对股票池和区间做缺口审计。
- 尚未补指数历史成分/权重、行业代理净值和数据质量日报。
- 尚未基于新协议建立教学 baseline；旧策略和旧结果不算新底座验收证据。
- 服务器端口仍只监听 loopback；需要在用户本机维持 SSH tunnel，当前验收入口为 `http://127.0.0.1:15174/`。
- TradingFlow 目前只做过产品/接入可行性评估，仓库未接入真实美股或期权流。购买前必须确认正式 API/数据库集成合同、历史深度、限流和数据许可，不能依赖浏览器抓取。
- 服务器删除未挂载旧 PostgreSQL volume 后，磁盘使用率约 `83%`、剩余约 `6.6G`；两份数据库 dump 仍保留，扩展大体量美股/期权数据前仍应评估扩容。
- Tushare `stk_limit` 会同时返回交易所基金。新同步已按股票主数据过滤，DB 中既有的非股票记录因未获删除授权而保留；覆盖快照和研究 loader 均只按股票范围使用。

## 验证命令

本机 PowerShell 使用仓库当前 Python 环境；测试显式切换到内存 SQLite，避免触碰真实 PG：

```powershell
$env:DATABASE_URL='sqlite+pysqlite:///:memory:'
python -m py_compile backend\app\database.py backend\app\models.py backend\app\schemas.py backend\app\tushare_client.py backend\app\us_research.py backend\app\main.py backend\app\quant_research\dataset.py backend\app\quant_research\repository.py backend\app\quant_research\portfolio.py backend\app\quant_research\metrics.py backend\app\quant_research\validation.py backend\app\quant_research\manifest.py backend\app\quant_research\readiness.py
python -m unittest discover backend\tests -v
docker compose config
git diff --check
git status -sb
```

## 常见坑点

- `Base.metadata.create_all()` 只会补建缺失表，不替代正式的生产迁移审计；不要因为内存 SQLite 测试通过就直接操作真实 PG。
- `stock_suspend_events` 在某一范围内为 0 行可能是合法结果；readiness 要求表存在，但不要求非空。
- A 股横截面研究必须显式提供历史股票池，并具备 `stock_listings`、`stock_adjust_factors` 和 `stock_limit_prices`；禁止拿当前 `stocks` 全表替代历史样本。
- 缺持仓价格、复权因子或基准重叠日期时应严格失败，不要用前值或原始价静默填充。
- PostgreSQL volume 是本地持久化来源；不要执行 `docker compose down -v` 或删除 volume。
- `outputs/research-runs/` 已被忽略，用于大型一次性研究结果；可复现配置和协议文档仍应进入 Git。
- Windows 的 `core.autocrlf=true` 会破坏 Linux 定时脚本；根目录 `.gitattributes` 已固定 `*.sh text eol=lf`，不要删除。

## 下一步建议

1. 增加按标的/日期检查覆盖连续性的质量报告，不只依赖表级 readiness。
2. 补指数历史成分/权重，再建立一个简单教学 baseline，只验证全链路，不作策略候选或投资建议。
3. 如用户确认开展美股/期权流研究，先做 TradingFlow 7 天试用和 1 周数据落盘 PoC，再决定是否新增正式美股 schema。
