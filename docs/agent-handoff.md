# Agent Handoff

这份文档给后续 Agent 接手当前仓库状态用。规则仍以根目录 `AGENTS.md` 为准。

## 当前接手状态

- 仓库：`/Users/jettlin/code/Quantitative_trading`
- 当前分支：`codex/strategy-evaluation-gap-closure`
- 当前 PR：`https://github.com/Jettlin927/Quantitative_trading/pull/2`
- PR 目标：`main`
- 当前任务：把仓库重置为纯“数据源 -> PostgreSQL DB”工作台。
- 当前边界：不删除 PostgreSQL volume，不处理真实账户，不恢复旧策略。

## 建议阅读顺序

1. `AGENTS.md`：项目主管规则和数据边界。
2. `docs/agent-code-map.md`：从目标到代码位置的导航。
3. `README.md`：人类使用说明。
4. `backend/app/models.py`：当前 DB schema。
5. `backend/app/main.py`：当前数据 API。

## 当前主线

- A 股数据：Tushare -> `stocks`、`stock_daily_bars`、`stock_daily_basic`、`stock_financial_indicators`。
- A 股辅助：`stock_pools`、`stock_pool_members`、`data_sync_runs`。
- 美股 sample 数据：`my_quant/us_research/` sample 文件 -> `assets`、`asset_daily_prices`、`watchlist_items`、`portfolio_snapshots`。
- 前端：只展示 API/DB 状态、覆盖度、同步记录、A 股样本和美股 sample 状态。

## 已移除主线

- 后端策略、回测、策略评估、策略生命周期和研究引擎。
- `my_quant/strategy_research/` 历史策略工作区。
- 策略研究阶段、回测报告、策略思想库和旧研究计划文档。
- 前端策略/回测/评估展示。

## 验证命令

当前仓库根 `.venv` 是 Python 3.12 环境，优先使用 `.venv/bin/python`。

后端最小编译检查：

```bash
.venv/bin/python -m py_compile backend/app/database.py backend/app/models.py backend/app/schemas.py backend/app/tushare_client.py backend/app/us_research.py backend/app/main.py
```

后端测试：

```bash
.venv/bin/python -m unittest discover backend/tests -v
```

Compose 配置检查：

```bash
docker compose config
```

前端构建：

```bash
docker compose run --rm frontend npm run build
```

提交前检查：

```bash
git diff --check
git status -sb
```

## 常见坑点

- 系统 `/usr/bin/python3` 可能是 Python 3.9；本仓验证默认用 `.venv/bin/python`。
- PostgreSQL volume 是本地持久化来源；不要执行 `docker compose down -v` 或删除 volume。
- `my_quant/us_research/` 只允许 sample/脱敏结构，不提交真实券商导出。
- `frontend/dist/` 是构建产物，不作为源码主线。
- `/Users/jettlin/code/投资分析` 是另一个投资研究仓库，不属于本 PR 范围。

## PR 维护流程

1. 运行 `git status -sb`，确认工作树。
2. 用 `git diff --stat` 和必要的 `git diff -- <path>` 确认改动范围。
3. 只 stage 与本次任务相关的文件。
4. 运行对应验证命令，并把命令与结果写入 `操作日志.md`。
5. 提交并推送当前分支，PR #2 会自动更新。
