# Permanent Portfolio Alpha Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible research pipeline that decides whether any TODO strategy can stably outperform the China permanent portfolio.

**Architecture:** Keep strategy intent in one folder per route under `my_quant/strategy_research/strategies/`, keep shared evaluation rules in `evaluation_framework.md`, and keep reproducible evidence in `results/`. The executable engine is split into `experiment/config.py`, `data.py`, `strategies.py`, `backtest.py`, `metrics.py`, `reports.py`, and `pipeline.py`; `run_full_experiment.py` is the main entry point and `run_candidate_backtest.py` is a compatibility wrapper.

**Tech Stack:** Python 3.12, AkShare `fund_etf_hist_sina`, pandas, numpy, Markdown artifacts.

---

## Goal

把 `my_quant/TODO.md` 从待办清单变成一套可执行的策略研究系统。完成后，任何人都可以打开策略文件夹，看到该策略如何执行、如何和永久组合比较、什么情况下通过、什么情况下淘汰。

## Success Criteria

- 每条 TODO 路线都有独立策略文件夹和 `flow.md`。
- 所有策略共用同一套基准、指标和淘汰规则。
- 完整实验脚本能生成基础策略对比、RAM 参数扫描、训练期参数扫描、训练期最优样本外结果、摘要和 manifest。
- 核心回测行为有标准库 `unittest` 覆盖，不依赖额外测试包。
- 当前最优研究候选写入 `final_candidate.md`，并明确说明它还需要哪些验证才能成为稳定候选。
- 后续正式实现能迁移到 `xquant-learning` 的 q5/q6/q9 notebooks。

### Task 1: Experiment Engine

**Files:**
- Create or modify: `my_quant/strategy_research/experiment/config.py`
- Create or modify: `my_quant/strategy_research/experiment/data.py`
- Create or modify: `my_quant/strategy_research/experiment/strategies.py`
- Create or modify: `my_quant/strategy_research/experiment/backtest.py`
- Create or modify: `my_quant/strategy_research/experiment/metrics.py`
- Create or modify: `my_quant/strategy_research/experiment/reports.py`
- Create or modify: `my_quant/strategy_research/experiment/pipeline.py`
- Create or modify: `my_quant/strategy_research/tests/test_experiment_engine.py`

- [ ] **Step 1: Run unit tests**

```bash
.venv/bin/python -m unittest discover my_quant/strategy_research/tests -v
```

Expected: all tests pass. Tests cover weight normalization, RAM TopN selection, defense fallback, turnover cost, metrics, and manifest generation.

- [ ] **Step 2: Run the full experiment**

```bash
.venv/bin/python my_quant/strategy_research/run_full_experiment.py
```

Expected: command prints `Candidate Backtest Summary` and writes the result artifacts.

### Task 2: Result Artifacts

**Files:**
- Create: `my_quant/strategy_research/results/base_strategy_comparison.csv`
- Create: `my_quant/strategy_research/results/ram_parameter_scan.csv`
- Create: `my_quant/strategy_research/results/train_parameter_scan.csv`
- Create: `my_quant/strategy_research/results/train_best_oos_result.csv`
- Create: `my_quant/strategy_research/results/walk_forward_shortlist_summary.csv`
- Create: `my_quant/strategy_research/results/factor_ic_summary.csv`
- Create: `my_quant/strategy_research/results/latest_summary.md`
- Create: `my_quant/strategy_research/results/latest_summary.json`
- Create: `my_quant/strategy_research/results/experiment_manifest.json`

- [ ] **Step 1: Verify artifact manifest**

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
payload = json.loads(Path("my_quant/strategy_research/results/experiment_manifest.json").read_text())
assert payload["base_strategy_rows"] == 8
assert payload["ram_scan_rows"] == 144
assert payload["train_scan_rows"] == 144
assert payload["best_research_candidate"] == "ram_top2_m20_v120_f21_cost"
assert "walk_forward_shortlist_summary.csv" in payload["artifacts"]
assert "factor_ic_summary.csv" in payload["artifacts"]
print(payload)
PY
```

Expected: manifest records all result files and the current best research candidate.

### Task 3: Strategy Flow Review

**Files:**
- Modify: `my_quant/strategy_research/strategies/*/flow.md`
- Modify: `my_quant/strategy_research/evaluation_framework.md`

- [ ] **Step 1: Check each strategy has TODO mapping**

```bash
for f in my_quant/strategy_research/strategies/*/flow.md; do
  grep -q "TODO 对应" "$f" || { echo "missing TODO mapping: $f"; exit 1; }
done
echo "all strategy flows contain TODO mapping"
```

Expected: all flow files contain TODO mapping.

- [ ] **Step 2: Check each strategy has acceptance and rejection rules**

```bash
for f in my_quant/strategy_research/strategies/*/flow.md; do
  grep -q "通过条件" "$f" || { echo "missing pass rules: $f"; exit 1; }
  grep -q "淘汰条件" "$f" || { echo "missing reject rules: $f"; exit 1; }
done
echo "all strategy flows contain pass and reject rules"
```

Expected: all flow files contain explicit pass and reject rules.

### Task 4: Candidate Decision Update

**Files:**
- Modify: `my_quant/strategy_research/final_candidate.md`
- Read: `my_quant/strategy_research/results/latest_summary.md`

- [ ] **Step 1: Read the generated summary**

```bash
sed -n '1,120p' my_quant/strategy_research/results/latest_summary.md
```

Expected: summary includes the current best research candidate, baseline metrics, and train/test warning.

- [ ] **Step 2: Update the final candidate note**

Write the strategy name and the key metric deltas into `final_candidate.md` only if the generated summary proves them. Keep the warning that this is a research candidate until Walk-Forward and factor IC pass.

### Task 5: Formal Notebook Migration

**Files:**
- Modify later: `xquant-learning/q5-how-to-validate/notebooks/q5-how-to-validate.ipynb`
- Modify later: `xquant-learning/q6-avoid-overfitting/notebooks/q6-avoid-overfitting.ipynb`
- Modify later: `xquant-learning/q9-daily-work/notebooks/q9-daily-work.ipynb`

- [ ] **Step 1: Add China permanent portfolio to q5**

Add the China permanent portfolio as the second benchmark beside 沪深300 buy-and-hold. Every strategy table must include annual excess return, max drawdown difference, and Calmar difference versus the permanent portfolio.

- [ ] **Step 2: Add RAM TopN scan to q6**

Scan momentum windows `20, 60, 120, 180`, volatility windows `20, 60, 120`, TopN `1, 2, 3`, and rebalance intervals `10, 21, 42, 63`.

- [ ] **Step 3: Add factor IC diagnosis to q9**

Compute IC for momentum, RAM, low volatility, and trend strength by market regime. A strategy that has no positive IC regime remains a backtest artifact, not a strategy candidate.
