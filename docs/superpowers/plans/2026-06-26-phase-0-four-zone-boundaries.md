# Phase 0 Four-Zone Boundaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 `TODO.md` Phase 0，让后续 Agent 能从根目录文档理解四区架构、数据边界、迁移风险和下一阶段入口。

**Architecture:** Phase 0 只做文档和盘点，不移动真实持仓、不迁移敏感文件、不改 Docker DB、不改策略语义。README 面向人类说明四区闭环，AGENTS 面向 Agent 固化边界，code map 面向执行者给导航，迁移盘点文档记录 `/Users/jettlin/code/投资分析` 的可迁移资产与禁止搬运内容。

**Tech Stack:** Markdown 文档、Git diff 校验、现有 `TODO.md` / `AGENTS.md` / `docs/agent-code-map.md` / `操作日志.md`。

---

### Task 1: Root README Four-Zone Summary

**Files:**
- Modify: `README.md`

- [x] **Step 1: 在 README 的项目介绍后新增“四区闭环”小节**

Insert after the opening paragraph and screenshots stay unchanged below the introduction:

```markdown
## 四区闭环

本仓库后续按四个区域收拢，目标是：用 A 股数据验证交易纪律，再把验证过的规则反哺美股持仓和观察池分析。

- `my_quant/us_research/`：美股操作层，保存 sample 持仓、观察池、快照、操作报告和规则证据；真实持仓和成交记录默认只读本地脱敏 CSV，不提交。
- `backend/`、`docker-compose.yml`、`docs/research/a-share-data/`：A 股数据沙盘，继续使用 Docker PostgreSQL 和 Tushare 做大样本验证。
- `docs/research/strategy-lab/`：策略思想库，沉淀规则卡片、假设、失败条件和负证据。
- `docs/research/backtest-reports/`、`my_quant/strategy_research/results/`、`my_quant/strategy_research/web_report/`：回测证据档案，保存 run 索引、HTML、CSV、manifest 和阶段结论。

边界不变：本仓库只做研究、复盘、模拟和人工辅助分析，不连接券商，不自动下单，不处理真实资金。
```

- [x] **Step 2: 检查 README 中没有真实持仓、token 或绝对敏感路径**

Run:

```bash
rg -n "TUSHARE_TOKEN=|DEEPSEEK_TOKEN=|hsbc_|executed_trades|current_holdings|/Users/jettlin/code/投资分析" README.md
```

Expected: only placeholder token examples may appear; no real holdings file path should be introduced by this task.

### Task 2: AGENTS Four-Zone Rule

**Files:**
- Modify: `AGENTS.md`

- [x] **Step 1: 在“默认架构”后新增“四区职责”小节**

Add a short Agent rule section:

```markdown
## 四区职责

后续改造按四区推进：

- 美股操作层：默认落在 `my_quant/us_research/`，只保存 sample、脱敏结构、观察池配置、数据快照和研究辅助报告；不要提交真实持仓、成交明细或券商导出。
- A 股数据沙盘：默认仍由 `backend/`、`docker-compose.yml` 和 PostgreSQL volume 承载，Tushare 数据用于大样本验证；不要为了美股第一版改 A 股表结构或删除 volume。
- 策略思想库：默认落在 `docs/research/strategy-lab/`，每条规则必须写清假设、A 股验证口径、美股映射边界、失败条件和负证据。
- 回测证据档案：默认落在 `docs/research/backtest-reports/`、`my_quant/strategy_research/results/` 和 `my_quant/strategy_research/web_report/`，指标应来自已落盘 CSV/JSON/manifest 或数据库结果。

Phase 0 只立边界和盘点，不搬大文件、不迁移真实持仓、不改 Docker DB、不改变回测语义。
```

- [x] **Step 2: 确认 AGENTS 没有把美股报告升级为交易指令**

Run:

```bash
rg -n "自动下单|连接真实券商|真实资金|研究辅助" AGENTS.md
```

Expected: red-line language remains present and unchanged in meaning.

### Task 3: Agent Code Map Navigation

**Files:**
- Modify: `docs/agent-code-map.md`

- [x] **Step 1: 在“一句话架构”后新增“四区导航”**

Add:

```markdown
## 四区导航

- 美股操作层：`my_quant/us_research/`，后续放 sample 持仓、观察池、yfinance 快照脚本、HTML/Markdown 操作报告和规则证据引用。
- A 股数据沙盘：`backend/app/`、`docker-compose.yml`、PostgreSQL volume 和 `docs/research/a-share-data/`，继续服务 Tushare 同步、A 股研究池和大样本验证。
- 策略思想库：`docs/research/strategy-lab/`，后续放“不追高”“止跌后加仓”“同因子杠杆预算”等规则卡片和负证据。
- 回测证据档案：`docs/research/backtest-reports/`、`docs/research/runs/`、`my_quant/strategy_research/results/`、`my_quant/strategy_research/web_report/`。
```

- [x] **Step 2: 检查导航与 TODO Phase 0 一致**

Run:

```bash
rg -n "四区导航|us_research|strategy-lab|backtest-reports|a-share-data" docs/agent-code-map.md TODO.md
```

Expected: both files expose the same core directories.

### Task 4: 投资分析迁移盘点

**Files:**
- Create: `docs/research/us-research-migration-inventory-2026-06-26.md`

- [x] **Step 1: 写明可迁移、需脱敏、禁止迁移三类资产**

The document must include:

```markdown
# 美股研究资产迁移盘点（2026-06-26）

## 结论

Phase 0 不迁移 `/Users/jettlin/code/投资分析` 的真实数据，只记录未来可复用结构。后续 Phase 1 如需迁移，只迁移非敏感脚本、测试和 sample 配置。

## 可迁移或可参考

- `scripts/finnhub_snapshot.py`
- `scripts/daily_notify_report.py`
- `scripts/generate_prediction_dashboard.py`
- `tests/test_finnhub_snapshot_fast_refresh.py`
- `tests/test_history_metrics.py`
- `watchlist_symbols_2026.csv` 的字段结构

## 需脱敏或 sample 化

- `watchlist_symbols_2026.csv` 的真实观察池内容
- `prediction_ledger_2026.csv`
- HTML 报告的展示结构

## 禁止直接迁移

- `hsbc_current_holdings_2026.csv`
- `hsbc_executed_trades_2026.csv`
- `hsbc_non_executed_orders_2026.csv`
- `.env`、`.env.local`、token、真实账户或券商导出

## 下一步

Phase 1 创建 `my_quant/us_research/`，只使用 sample 持仓和 sample 观察池，脚本复用前先写测试。
```

- [x] **Step 2: 确认没有误包含真实持仓数据**

Run:

```bash
rg -n "quantity|cost|account|hsbc_current_holdings_2026.csv|hsbc_executed_trades_2026.csv" docs/research/us-research-migration-inventory-2026-06-26.md
```

Expected: only filenames in the "禁止直接迁移" section appear; no row-level holdings data.

### Task 5: 操作日志与验证

**Files:**
- Modify: `操作日志.md`

- [x] **Step 1: 追加 Phase 0 事实日志**

Append a dated entry with:

- 阶段目标：完成 TODO Phase 0 四区边界。
- 实际操作：README、AGENTS、code map、迁移盘点、计划文件。
- 验证结果：`git diff --check` and required `rg` checks.
- 后续事项：Phase 1 创建 `my_quant/us_research/` 文件化闭环。

- [x] **Step 2: 运行文档验证**

Run:

```bash
git diff --check -- README.md AGENTS.md docs/agent-code-map.md docs/research/us-research-migration-inventory-2026-06-26.md docs/superpowers/plans/2026-06-26-phase-0-four-zone-boundaries.md 操作日志.md
rg -n "四区闭环|四区职责|四区导航|美股研究资产迁移盘点" README.md AGENTS.md docs/agent-code-map.md docs/research/us-research-migration-inventory-2026-06-26.md
```

Expected: `git diff --check` exits 0; `rg` finds the four required sections.
