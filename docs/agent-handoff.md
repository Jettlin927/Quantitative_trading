# Agent Handoff

这份文档给后续 Agent 接手当前仓库状态用。它不是项目规则，规则仍以根目录 `AGENTS.md` 为准；也不是完整代码地图，代码定位仍看 `docs/agent-code-map.md`。

## 当前接手状态

- 仓库：`/Users/jettlin/code/Quantitative_trading`
- 当前分支：`codex/my-quant-hardening`
- 当前 PR：`https://github.com/Jettlin927/Quantitative_trading/pull/1`
- PR 目标：`main`
- PR 状态：截至 2026-06-27 10:22 +08:00，`gh pr view 1` 显示 PR 为 `OPEN`，`isDraft=false`。
- 本交接页生成前的基线提交：`80ee144 chore: trim B1 report whitespace`。接手时以 `git log --oneline --decorate --max-count=5` 为准。
- 当前工作树要求：提交前先跑 `git status -sb`，不要把本仓外的脏树或本机自动化文件混进来。

## 建议阅读顺序

1. `AGENTS.md`：项目主管规则、交易行为边界、阶段推进红线。
2. `docs/agent-code-map.md`：从目标到代码位置的导航。
3. `docs/research/stages/README.md`：当前活跃研究阶段。
4. `docs/research/README.md`：策略研究协议和历史 run 结论。
5. `docs/research/backtest-reports/README.md`：策略目录和回测证据索引。
6. `my_quant/README.md`：独立研究工作区环境、Python 版本和常用命令。
7. `my_quant/strategy_research/README.md`：B1、小仓卫星、Risk8 和历史证据入口。

## 当前研究边界

- 当前活跃阶段是 `001-observation-diagnosis`，目标是解释 `105/106/110` 失败窗口为什么没有稳定正期望。
- 当前阶段未验收前，不要新建更高阶段目录，不要改 `docs/research/long-term-goal.md` 的阶段顺序。
- 策略输出仍是研究、复盘、模拟和风控辅助，不是实盘交易指令。
- 任何涉及真实券商、真实账户、真实持仓导入、资金读取、自动下单或交易提醒升级的事情，都必须先问用户。
- 修改核心回测语义、成交假设、风控硬线、端口、凭据或 PostgreSQL volume 前，也必须先问用户。

## 本仓主要工作区

- `backend/`：FastAPI、SQLAlchemy、Tushare 同步、A 股回测语义和 API 契约。
- `frontend/`：React + Vite 工作台，只负责参数提交和展示，不维护分叉策略逻辑。
- `docs/research/`：长期目标、阶段推进、研究 run、策略思想库和回测报告证据。
- `my_quant/strategy_research/`：独立 B1、小仓卫星、Risk8 证据、历史实验脚本、测试和报告生成。
- `my_quant/us_research/`：美股 sample 观察池、yfinance 快照和研究辅助报告；只提交 sample/脱敏结构，不提交真实券商导出。

## 验证命令

代码改动后按改动范围选择最小必要验证。当前仓库根 `.venv` 是 Python 3.12 环境，优先使用 `.venv/bin/python`，不要用 macOS 系统 `/usr/bin/python3`。

后端最小编译检查：

```bash
.venv/bin/python -m py_compile backend/app/database.py backend/app/models.py backend/app/schemas.py backend/app/backtest_engine.py backend/app/tushare_client.py backend/app/ai_client.py backend/app/main.py backend/app/b1_strategy.py
```

B1 后端测试：

```bash
.venv/bin/python -m unittest backend.tests.test_b1_strategy
```

A 股独立研究测试：

```bash
.venv/bin/python -m unittest discover my_quant/strategy_research/tests
```

美股 sample 研究测试：

```bash
.venv/bin/python -m unittest discover my_quant/us_research/tests
```

Compose 配置检查：

```bash
docker compose config
```

注意：`docker compose config` 可能展开本机环境变量。只记录通过/失败和必要错误摘要，不要把 token、密码或完整环境输出粘进日志、PR 或最终回复。

前端构建：

```bash
docker compose run --rm frontend npm run build
```

提交前 whitespace 检查：

```bash
git diff --check
git diff --check origin/main...HEAD
```

## 常见坑点

- 系统 `/usr/bin/python3` 可能是 Python 3.9，会在使用 `str | None` 等 3.10+ 语法时失败；本仓验证默认用 `.venv/bin/python`。
- PostgreSQL volume 是本地行情和同步记录来源；不要执行 `docker compose down -v` 或删除 volume，除非用户明确确认会丢数据。
- `docs/research/backtest-reports/` 和 `my_quant/strategy_research/web_report/` 中已有较多已落盘证据；优先引用已有 manifest/CSV/HTML，不要为了“看起来完整”手填指标。
- `/Users/jettlin/code/投资分析` 是另一个投资研究仓库，不属于本 PR 范围。
- `~/.codex/automations/` 是本机自动化状态，不属于本仓源码，也不要提交。
- 外部 Kronos checkout、`.venv/`、真实预测大文件和真实账户导出不要迁入本仓；本仓只保存包装脚本、sample、归档证据索引和脱敏输出。

## PR 维护流程

1. 先运行 `git status -sb`，确认是否有用户或其他 Agent 的未提交改动。
2. 用 `git diff --stat` 和必要的 `git diff -- <path>` 确认改动范围。
3. 只 stage 与本次任务相关的文件；工作树混杂时不要用 `git add -A`。
4. 运行对应验证命令，并把命令与结果写入 `操作日志.md`。
5. 用简短中文或英文提交信息提交，例如 `docs: add agent handoff guide`。
6. 推送当前分支，PR #1 会自动更新。
