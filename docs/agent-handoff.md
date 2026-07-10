# Agent Handoff

这份文档给后续 Agent 接手当前仓库状态用。规则仍以根目录 `AGENTS.md` 为准。

## 当前接手状态

- 仓库：`E:\coding_things\Quantitative_trading`
- 当前分支：`codex/research-foundation`
- 基线：`origin/main` 的 `0e8dde0`。
- 当前任务：P1 历史可交易性、统一离线研究协议、四视图 React 数据终端和远端真实 PostgreSQL 验收均已完成。
- 当前边界：不删除 PostgreSQL volume，不导入真实账户数据，不连接券商，不发布交易信号。
- 当前远端发布目录：`/opt/quantitative-trading-release-20260710-2330`；原 `/opt/quantitative-trading` 脏工作区未被覆盖。
- 当前远端备份：`/opt/quantitative-trading-backups/pre-research-foundation-20260710-2330.dump`；数据库 volume 为 `quantitative-trading_postgres_data`。

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
- 美股 sample：继续只允许 sample/脱敏结构入库和只读展示，不属于当前 A 股研究协议扩展范围。

## 仍未完成

- 当前 readiness 是表级门禁，不是全市场全历史完整性证明：股票复权仅 5 个样本标的，涨跌停和停复牌仅 2026-06-26 至 2026-07-10；正式研究前按目标股票池和区间回填。
- 尚未补指数历史成分/权重、行业代理净值和数据质量日报。
- 尚未基于新协议建立教学 baseline；旧策略和旧结果不算新底座验收证据。
- 服务器端口仍只监听 loopback；需要在用户本机维持 SSH tunnel，当前验收入口为 `http://127.0.0.1:15174/`。

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

## 下一步建议

1. 根据具体研究目标确定股票池、区间和基准，批量回填对应复权、涨跌停、停复牌和指数成分历史。
2. 增加按标的/日期检查覆盖连续性的质量报告，不只依赖表级 readiness。
3. 数据完整性门禁通过后，新建一个简单教学 baseline，只验证全链路，不作策略候选或投资建议。
