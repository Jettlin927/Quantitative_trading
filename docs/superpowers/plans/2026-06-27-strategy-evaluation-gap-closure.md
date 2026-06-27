# Strategy Evaluation Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐后端研究引擎边界、美股数据入库准备、三段策略评估 API 和前端统一读取闭环，同时保护历史策略证据不被物理删除。

**Architecture:** 先做只读聚合，不碰真实持仓、不新增会自动落库的美股表、不删除历史策略目录。第一阶段把三段评估窗口从前端常量收回后端；第二阶段把 `my_quant/strategy_research` 中可复用逻辑迁为 `backend/app/research_engine/`；第三阶段在用户确认后新增美股 sample DB 表；第四阶段做策略冻结/归档视图，而不是删除证据。

**Tech Stack:** FastAPI、SQLAlchemy 2.0、标准库 `unittest`、React + Vite、Docker Compose、现有 `docs/research/runs` 证据文件。

---

### Task 1: 后端三段策略评估聚合 API

**Files:**
- Create: `backend/app/strategy_evaluation.py`
- Create: `backend/tests/test_strategy_evaluation.py`
- Modify: `backend/app/main.py`
- Modify: `操作日志.md`

- [x] **Step 1: Write the failing test**

Create `backend/tests/test_strategy_evaluation.py`:

```python
from __future__ import annotations

from datetime import date
import unittest

from backend.app.strategy_evaluation import build_evaluation_windows


class StrategyEvaluationTest(unittest.TestCase):
    def test_builds_three_windows_with_only_first_window_covered(self):
        spec = {"window": {"startDate": "2020-01-01", "endDate": "2024-12-31"}}
        analysis = {"targetMet": True}

        windows = build_evaluation_windows(spec, analysis, today=date(2026, 6, 27))

        self.assertEqual([item["id"] for item in windows], ["train-2020-2024", "oos-2025-now", "bear-market-observe"])
        self.assertEqual(windows[0]["status"], "pass")
        self.assertEqual(windows[1]["status"], "missing")
        self.assertEqual(windows[2]["status"], "observation_pending")
        self.assertFalse(windows[2]["qualifiesStrategy"])

    def test_covered_window_fails_when_target_not_met(self):
        spec = {"window": {"startDate": "2020-01-01", "endDate": "2024-12-31"}}
        analysis = {"targetMet": False, "strictTargetMet": False}

        windows = build_evaluation_windows(spec, analysis, today=date(2026, 6, 27))

        self.assertEqual(windows[0]["status"], "fail")
        self.assertTrue(windows[0]["qualifiesStrategy"])
```

- [x] **Step 2: Run test to verify RED**

Run:

```bash
.venv/bin/python -m unittest backend.tests.test_strategy_evaluation -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.app.strategy_evaluation'`.

- [x] **Step 3: Write minimal implementation**

Create `backend/app/strategy_evaluation.py` with:

```python
from __future__ import annotations

from datetime import date
from typing import Any


def build_evaluation_windows(spec: dict[str, Any], analysis: dict[str, Any], today: date | None = None) -> list[dict[str, Any]]:
    current_date = today or date.today()
    windows = [
        {
            "id": "train-2020-2024",
            "label": "第一轮",
            "startDate": "2020-01-01",
            "endDate": "2024-12-31",
            "role": "qualification",
            "qualifiesStrategy": True,
            "objective": "策略定型与主评估窗口，收益、回撤、盈亏比必须同时达标。",
        },
        {
            "id": "oos-2025-now",
            "label": "第二轮",
            "startDate": "2025-01-01",
            "endDate": current_date.isoformat(),
            "role": "out_of_sample",
            "qualifiesStrategy": True,
            "objective": "样本外复核，通过后才允许进入当前适用性讨论。",
        },
        {
            "id": "bear-market-observe",
            "label": "最终观察",
            "startDate": None,
            "endDate": None,
            "role": "observation_only",
            "qualifiesStrategy": False,
            "objective": "只观察熊市韧性、流动性和交易纪律，不作为策略达标判定。",
        },
    ]
    for window in windows:
        window["status"] = classify_window_status(window, spec, analysis)
    return windows


def classify_window_status(window: dict[str, Any], spec: dict[str, Any], analysis: dict[str, Any]) -> str:
    if not window["qualifiesStrategy"]:
        return "observation_pending"
    spec_window = spec.get("window", {})
    if not covers_window(spec_window.get("startDate"), spec_window.get("endDate"), window["startDate"], window["endDate"]):
        return "missing"
    return "pass" if bool(analysis.get("targetMet") or analysis.get("strictTargetMet")) else "fail"


def covers_window(spec_start: str | None, spec_end: str | None, required_start: str | None, required_end: str | None) -> bool:
    if not spec_start or not spec_end or not required_start or not required_end:
        return False
    return spec_start <= required_start and spec_end >= required_end
```

- [x] **Step 4: Add FastAPI endpoint**

Modify `backend/app/main.py`:

```python
from .strategy_evaluation import build_evaluation_windows
```

Add route after `get_executable_strategy`:

```python
@app.get("/api/strategy-evaluations")
def list_strategy_evaluations(db: Session = Depends(get_db)) -> dict[str, Any]:
    strategy = get_executable_strategy(EXECUTABLE_STRATEGY_ID, db)
    overview = build_research_overview()
    return json_safe(
        {
            "source": "backend",
            "updatedAt": date.today().isoformat(),
            "activeStage": overview.get("stage", {}),
            "evaluations": [
                {
                    "strategyId": strategy["id"],
                    "label": strategy["label"],
                    "runId": strategy.get("runId"),
                    "status": strategy.get("status"),
                    "statusTier": strategy.get("spec", {}).get("statusTier"),
                    "metrics": strategy.get("metrics", {}),
                    "objectiveGates": strategy.get("objectiveGates", {}),
                    "diagnosticGates": strategy.get("diagnosticGates", {}),
                    "evaluationWindows": build_evaluation_windows(strategy.get("spec", {}), strategy.get("analysis", {})),
                    "resultFiles": strategy.get("resultFiles", {}),
                }
            ],
        }
    )
```

- [x] **Step 5: Run GREEN checks**

Run:

```bash
.venv/bin/python -m unittest backend.tests.test_strategy_evaluation -v
.venv/bin/python -m py_compile backend/app/main.py backend/app/strategy_evaluation.py
curl -fsS http://localhost:18000/api/strategy-evaluations
```

Expected: tests pass, compile passes, endpoint returns one evaluation with three `evaluationWindows`.

- [x] **Step 6: Add route contract regression tests**

Created `backend/tests/test_api_contracts.py` to lock the unified backend payloads consumed by the frontend: `/api/strategy-evaluations`, `/api/strategy-lifecycle` and `/api/us-research/import-preview`. The strategy evaluation contract intentionally keeps both qualifying windows as `missing` until formal 2020-2024 and 2025-current evidence exists.

- [x] **Step 7: Add frontend dashboard aggregation API**

Added `GET /api/research/dashboard` as the preferred frontend data contract. It returns health, research overview, executable baseline strategy, strategy evaluation windows, lifecycle index, US sample overview, US import preview and research run summaries in one backend payload. Added contract coverage in `backend/tests/test_api_contracts.py`.

### Task 2: 前端读取后端评估窗口

**Files:**
- Modify: `frontend/src/main.jsx`
- Modify: `frontend/src/styles.css` only if needed for new copy length
- Modify: `操作日志.md`

- [x] **Step 1: Load `/api/strategy-evaluations`**

Add the endpoint to the existing `Promise.allSettled` call and store `strategyEvaluation`.

- [x] **Step 2: Prefer backend windows**

Use `strategyEvaluation.evaluations[0].evaluationWindows` as the source for `PhaseRail`; fall back to local calculation only when the endpoint is unavailable.

- [x] **Step 3: Verify frontend**

Run:

```bash
docker compose run --rm frontend npm run build
```

Expected: build passes and the page still renders `三段验证闸门`.

- [x] **Step 4: Redesign data presentation to QuantConnect-style strategy page**

After the user supplied QuantConnect screenshots as the target presentation style, rewrite the frontend into a light research-terminal layout with left navigation, black topbar, strategy title/meta, KPI strip, chart grid, overview metrics table, rolling statistics table and right evidence rail. Keep backend `/api/strategy-evaluations` as the source of phase gates.

Verify:

```bash
docker compose run --rm frontend npm run lint
docker compose run --rm frontend npm run build
git diff --check -- frontend/src/main.jsx frontend/src/styles.css
```

Runtime Chrome check should confirm chart panels, overview rows, rolling rows and phase rows render without console errors.

- [x] **Step 5: Prefer `/api/research/dashboard` in frontend data flow**

Updated `frontend/src/main.jsx` so `refreshAll()` first loads `/api/research/dashboard?run_limit=160` and hydrates the whole strategy page from that single backend payload. The previous multi-endpoint loading path remains as a fallback when the aggregation endpoint is unavailable.

Verify:

```bash
docker compose run --rm frontend npm run lint
docker compose run --rm frontend npm run build
curl -fsS 'http://localhost:18000/api/research/dashboard?run_limit=5'
```

Expected: lint/build pass and the dashboard endpoint returns `source=backend`, baseline strategy `cross-section-strength-risk8`, first evaluation window `missing`, and `usImportPreview.writesEnabled=false`.

### Task 3: `research_engine` package migration without deleting `my_quant`

**Files:**
- Create: `backend/app/research_engine/__init__.py`
- Create: `backend/app/research_engine/metrics.py`
- Create: `backend/app/research_engine/portfolio.py`
- Create: `backend/app/research_engine/reports.py`
- Create: `backend/tests/test_research_engine_metrics.py`
- Create: `backend/tests/test_research_engine_portfolio.py`
- Create: `backend/tests/test_research_engine_reports.py`
- Modify: `docs/research/repo-structure-evaluation-roadmap-2026-06-27.md`
- Modify: `操作日志.md`

- [x] **Step 1: Move one pure function first**

Start with metric helpers that do not read files, call network, or depend on caches. Keep `my_quant/strategy_research` import paths working until all callers are migrated.

- [x] **Step 2: Add compatibility tests**

Assert the backend helper returns the same annualized return, max drawdown and Sharpe values as the existing tested behavior.

- [x] **Step 3: Verify**

Run:

```bash
.venv/bin/python -m unittest backend.tests.test_research_engine_metrics -v
.venv/bin/python -m unittest discover my_quant/strategy_research/tests -v
```

Expected: both pass. No strategy evidence files are deleted.

- [x] **Step 4: Move portfolio weight helpers**

Moved `normalize_weights`, `make_equal_weight`, `make_risk_parity` and `make_ram_topn` into `backend/app/research_engine/portfolio.py`. Kept `my_quant/strategy_research/experiment/strategies.py` as a compatibility wrapper so historical experiment scripts and evidence paths keep importing from the old location while the reusable implementation lives under the backend boundary.

- [x] **Step 5: Add portfolio compatibility tests**

Added `backend/tests/test_research_engine_portfolio.py` for weight normalization and RAM top-N selection. Verified the old experiment suite still passes through the compatibility wrapper:

```bash
.venv/bin/python -m unittest backend.tests.test_research_engine_portfolio -v
.venv/bin/python -m unittest my_quant.strategy_research.tests.test_experiment_engine -v
```

Expected: backend portfolio tests pass and the old experiment suite remains green. No cache, result, report or strategy evidence files are moved or deleted.

- [x] **Step 6: Move pure report payload helpers**

Moved Markdown table fallback, `select_best_candidate`, `build_summary_payload` and `build_manifest_payload` into `backend/app/research_engine/reports.py`. Kept `my_quant/strategy_research/experiment/reports.py` as a compatibility wrapper for those pure functions while leaving file-writing functions (`write_summary`, `write_manifest`) in the old research workspace.

- [x] **Step 7: Add report compatibility tests**

Added `backend/tests/test_research_engine_reports.py` for manifest payloads, candidate fallback selection and Markdown fallback when `tabulate` is unavailable. Verified the old experiment suite remains green:

```bash
.venv/bin/python -m unittest backend.tests.test_research_engine_reports -v
.venv/bin/python -m unittest my_quant.strategy_research.tests.test_experiment_engine -v
```

Expected: backend report tests pass and the old experiment suite remains green. No report files are regenerated and no result artifacts are moved.

- [x] **Step 8: Move walk-forward window helper**

Moved the pure rolling/anchored walk-forward window generator into `backend/app/research_engine/validation.py` as `build_walk_forward_windows`. Kept `my_quant/strategy_research/experiment/validation.py` as the compatibility caller for historical `walk_forward_analysis`; the full backtest-dependent analysis function stays in `my_quant` because it still depends on experiment-local `run_config` and `StrategyConfig`.

- [x] **Step 9: Add validation compatibility tests**

Added `backend/tests/test_research_engine_validation.py` for rolling and anchored window boundaries. Verified the old walk-forward analysis test still passes through the compatibility caller:

```bash
.venv/bin/python -m unittest backend.tests.test_research_engine_validation -v
.venv/bin/python -m unittest my_quant.strategy_research.tests.test_experiment_engine.ExperimentEngineTest.test_walk_forward_analysis_returns_rolling_and_anchored_rows -v
.venv/bin/python -m py_compile backend/app/research_engine/validation.py my_quant/strategy_research/experiment/validation.py
```

Expected: backend validation tests pass, the old experiment walk-forward test remains green, and no result artifacts are moved or regenerated.

- [x] **Step 10: Document remaining migration boundary**

Added `docs/research/research-engine-migration-inventory-2026-06-27.md` and `.html` to classify what is now backend-owned versus intentionally retained in `my_quant`. The remaining experiment modules are kept because they depend on data fetching, cache paths, report writing, strategy-specific execution, or historical evidence contracts.

### Task 4: 美股 DB schema and sample persistence

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/us_research.py`
- Create: `backend/tests/test_us_research_db.py`
- Modify: `docs/research/us-db-confirmation-checklist-2026-06-27.md`
- Modify: `操作日志.md`

- [x] **Preparation: Write confirmation checklist**

Created `docs/research/us-db-confirmation-checklist-2026-06-27.md` with the sample-only data boundary, proposed tables, read-only API scope and explicit confirmation questions. No model/schema changes were made.

- [x] **Preparation: Add file-backed sample read API before DB confirmation**

Created `backend/app/us_research.py`, `backend/tests/test_us_research.py` and `GET /api/us-research/overview`. The endpoint reads only sample files under `my_quant/us_research/`, returns `isSample=true`, `brokerConnected=false`, `realHoldingsImported=false` and `dbPersistence=pending_confirmation`, and does not add SQLAlchemy models or create tables. The frontend right rail now displays this backend-provided sample overview.

- [x] **Preparation: Add DB import preview without writes**

Added `GET /api/us-research/import-preview`, target table summaries and normalized records for `assets`, `asset_daily_prices`, `watchlist_items` and `portfolio_snapshots`. Before DB confirmation it returned `writesEnabled=false` and schema blockers. After confirmation it remains preview-only (`writesEnabled=false`) but reports `dbSchema=ready`, `canExecute=true` and the import endpoint.

- [x] **Preparation: Lock import preview API contract**

Added route contract coverage in `backend/tests/test_api_contracts.py` so the import preview remains sample-only and read-only until the DB schema boundary is explicitly confirmed.

- [x] **Step 1: Confirm DB boundary**

User confirmed: “新增持久化DB的schema啊”. This authorizes non-destructive creation of sample persistence tables. Real holdings and broker integrations remain out of scope.

- [x] **Step 2: Add sample-only models after confirmation**

Added `Asset`, `AssetDailyPrice`, `WatchlistItem`, `PortfolioSnapshot`, `GET /api/us-research/db-overview`, and `POST /api/us-research/import-sample`. The import endpoint only reads `my_quant/us_research/` sample files and upserts into DB.

- [x] **Step 3: Verify**

Run:

```bash
.venv/bin/python -m unittest backend.tests.test_us_research_db backend.tests.test_us_research backend.tests.test_api_contracts backend.tests.test_strategy_evaluation backend.tests.test_strategy_lifecycle backend.tests.test_research_engine_metrics backend.tests.test_research_engine_portfolio backend.tests.test_research_engine_reports -v
.venv/bin/python -m py_compile backend/app/database.py backend/app/models.py backend/app/schemas.py backend/app/backtest_engine.py backend/app/tushare_client.py backend/app/ai_client.py backend/app/main.py backend/app/us_research.py backend/app/strategy_lifecycle.py backend/app/strategy_evaluation.py backend/app/research_engine/metrics.py backend/app/research_engine/portfolio.py backend/app/research_engine/reports.py my_quant/strategy_research/experiment/reports.py
docker compose up -d --build api
curl -fsS -X POST http://localhost:18000/api/us-research/import-sample
curl -fsS http://localhost:18000/api/us-research/db-overview
```

Expected: tests and compile pass; API starts; sample import returns `sample_persisted`; Postgres contains `assets=4`, `asset_daily_prices=4`, `watchlist_items=4`, `portfolio_snapshots=1`. Real holdings remain untouched.

### Task 5: 策略冻结/归档 instead of physical deletion

**Files:**
- Modify: `docs/research/backtest-reports/README.md`
- Modify: `docs/research/research-runs.json` only in an integration session
- Create: `docs/research/strategy-archive-policy-2026-06-27.md`
- Modify: `操作日志.md`

- [x] **Step 1: Define archive states**

Use `active`, `frozen`, `archived_negative_evidence`, `deleted_only_after_user_confirmation`.

- [x] **Step 2: Keep evidence searchable**

Archived strategies stay in reports and run indexes but are hidden from the primary frontend strategy list.

- [x] **Step 3: Verify**

Run:

```bash
rg -n "active|frozen|archived_negative_evidence" docs/research/strategy-archive-policy-2026-06-27.md
git diff --check -- docs/research/strategy-archive-policy-2026-06-27.md docs/research/backtest-reports/README.md 操作日志.md
```

Expected: archive policy exists and no physical deletion occurs.

- [x] **Step 4: Add lifecycle index, backend API, and frontend archive panel**

Created `docs/research/strategy-lifecycle.json`, `backend/app/strategy_lifecycle.py`, `backend/tests/test_strategy_lifecycle.py`, `GET /api/strategy-lifecycle`, and lifecycle fields in `GET /api/strategy-evaluations`. Restored tracked strategy `flow.md` evidence entries that were in deleted state, then marked old strategies as `archived_negative_evidence` or `frozen`. Frontend right rail now shows `策略档案` counts.

- [x] **Step 5: Lock lifecycle API contract**

Added route contract coverage in `backend/tests/test_api_contracts.py` to ensure only `cross-section-strength-risk8` appears in the primary dashboard while frozen and archived strategies remain retained as evidence.
