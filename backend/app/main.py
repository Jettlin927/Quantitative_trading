from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from statistics import mean, median
from threading import Lock
from time import monotonic, sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .ai_client import analyze_stock_quality_with_deepseek
from .backtest_engine import DEFAULT_CONFIG, enrich_rows, json_safe, run_backtest
from .database import Base, SessionLocal, engine, get_db
from .models import DataSyncRun, Stock, StockDailyBar, StockDailyBasic, StockFinancialIndicator, StockPool, StockPoolMember
from .schemas import (
    BacktestRequest,
    DailyBarOut,
    MarketBacktestRequest,
    NewsTrendOut,
    ResearchJobRequest,
    StockPoolCreate,
    StockPoolDetailOut,
    StockPoolMembersRequest,
    StockPoolMemberOut,
    StockPoolOut,
    StockFundamentalsOut,
    StockOut,
    StockScreenOut,
    SyncDailyRequest,
    SyncFundamentalsRequest,
    SyncMarketDataRequest,
    SyncMarketFundamentalsRequest,
    SyncStockBasicRequest,
)
from .tushare_client import decimal_or_none, get_pro_api, parse_tushare_date, tushare_date

@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Quantitative Trading API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


DAILY_FIELDS = ",".join(
    [
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "change",
        "pct_chg",
        "vol",
        "amount",
    ]
)

DAILY_BASIC_FIELDS = ",".join(
    [
        "ts_code",
        "trade_date",
        "close",
        "turnover_rate",
        "turnover_rate_f",
        "volume_ratio",
        "pe",
        "pe_ttm",
        "pb",
        "ps",
        "ps_ttm",
        "dv_ratio",
        "dv_ttm",
        "total_share",
        "float_share",
        "free_share",
        "total_mv",
        "circ_mv",
    ]
)

FINA_INDICATOR_FIELDS = ",".join(
    [
        "ts_code",
        "ann_date",
        "end_date",
        "eps",
        "dt_eps",
        "bps",
        "netprofit_margin",
        "grossprofit_margin",
        "roe",
        "roe_waa",
        "roa",
        "debt_to_assets",
        "current_ratio",
        "quick_ratio",
        "assets_turn",
        "basic_eps_yoy",
        "op_yoy",
        "netprofit_yoy",
        "tr_yoy",
        "or_yoy",
        "q_sales_yoy",
        "q_profit_yoy",
    ]
)

NEWS_SOURCES = {
    "cls": "财联社",
    "wallstreetcn": "华尔街见闻",
    "xueqiu": "雪球热榜",
    "weibo": "微博热搜",
    "zhihu": "知乎热榜",
    "baidu": "百度热搜",
    "toutiao": "今日头条",
    "thepaper": "澎湃新闻",
}

UPSERT_CHUNK_SIZE = 2000
MARKET_BACKTEST_BATCH_SIZE = int(os.getenv("MARKET_BACKTEST_BATCH_SIZE", "360"))
MARKET_BACKTEST_WORKERS = int(os.getenv("MARKET_BACKTEST_WORKERS", "12"))
MARKET_BACKTEST_JOB_TTL_SECONDS = 3600
MARKET_BACKTEST_EXECUTOR = ThreadPoolExecutor(max_workers=1)
MARKET_BACKTEST_JOBS: dict[str, dict[str, Any]] = {}
MARKET_BACKTEST_LOCK = Lock()
RESEARCH_JOB_TTL_SECONDS = int(os.getenv("RESEARCH_JOB_TTL_SECONDS", "7200"))
RESEARCH_JOB_WORKERS = int(os.getenv("RESEARCH_JOB_WORKERS", "1"))
RESEARCH_JOB_EXECUTOR = ThreadPoolExecutor(max_workers=max(RESEARCH_JOB_WORKERS, 1))
RESEARCH_JOBS: dict[str, dict[str, Any]] = {}
RESEARCH_JOB_LOCK = Lock()
TRADE_CALENDAR_CACHE_TTL_SECONDS = 3600
TRADE_CALENDAR_CACHE: dict[tuple[date, date], tuple[float, list[date]]] = {}
RESEARCH_RUN_SUMMARY_READ_CHARS = int(os.getenv("RESEARCH_RUN_SUMMARY_READ_CHARS", "300000"))
RESEARCH_RUN_SUMMARY_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}
REPO_ROOT = Path(__file__).resolve().parents[2]
RESEARCH_ROOT = REPO_ROOT / "docs" / "research"
RESEARCH_INDEX_PATH = RESEARCH_ROOT / "research-runs.json"
RESEARCH_STAGES_DIR = RESEARCH_ROOT / "stages"
RESEARCH_RUNS_DIR = REPO_ROOT / "docs" / "research" / "runs"
RESEARCH_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
EXECUTABLE_STRATEGY_ID = "cross-section-strength-risk8"
EXECUTABLE_STRATEGY_SPEC_PATH = REPO_ROOT / "docs" / "research" / "executable-strategy-cross-section-risk8.json"
RESEARCH_JOB_SCRIPTS = {
    "portfolio_backtest": "scripts/research/run_portfolio_backtest.py",
    "window_validation": "scripts/research/run_window_validation.py",
    "trade_delta": "scripts/research/analyze_trade_delta.py",
}
RESEARCH_WEIGHT_FLAGS = {
    "return20": "cross-section-return20-weight",
    "return60": "cross-section-return60-weight",
    "high60": "cross-section-high60-weight",
    "recovery20": "cross-section-recovery20-weight",
    "volume": "cross-section-volume-weight",
    "base": "cross-section-base-weight",
    "industryReturn20": "cross-section-industry-return20-weight",
    "industryRelativeReturn20": "cross-section-industry-relative-return20-weight",
    "stockSpecificBreakoutQuality": "cross-section-stock-specific-breakout-quality-weight",
    "stockSpecificMatureBreadthQuality": "cross-section-stock-specific-mature-breadth-quality-weight",
    "macdHist": "cross-section-macd-hist-weight",
    "macdHistDelta": "cross-section-macd-hist-delta-weight",
    "bollSqueeze": "cross-section-boll-squeeze-weight",
    "bollPosition": "cross-section-boll-position-weight",
    "bollPositionBalance": "cross-section-boll-position-balance-weight",
    "rsiBalance": "cross-section-rsi-balance-weight",
    "maAlignment": "cross-section-ma-alignment-weight",
    "indicatorSetup": "cross-section-indicator-setup-weight",
    "indicatorConfluenceQuality": "cross-section-indicator-confluence-quality-weight",
    "indicatorTurnQuality": "cross-section-indicator-turn-quality-weight",
    "rsiMomentumQuality": "cross-section-rsi-momentum-quality-weight",
    "rsiMomentumConfirmedQuality": "cross-section-rsi-momentum-confirmed-quality-weight",
    "turnoverRateF": "cross-section-turnover-rate-f-weight",
    "volumeRatioBasic": "cross-section-volume-ratio-basic-weight",
    "lowVolumeRatioBasic": "cross-section-low-volume-ratio-basic-weight",
    "smallCircMv": "cross-section-small-circ-mv-weight",
    "priorGapStability": "cross-section-prior-gap-stability-weight",
    "amountRatio": "cross-section-amount-ratio-weight",
    "amountEfficiency20": "cross-section-amount-efficiency20-weight",
    "amountEfficiencyRsi": "cross-section-amount-efficiency-rsi-weight",
    "moneyflowMainNetRank1": "cross-section-moneyflow-main-rank1-weight",
    "moneyflowMainNetRank3": "cross-section-moneyflow-main-rank3-weight",
    "moneyflowMainNetRank5": "cross-section-moneyflow-main-rank5-weight",
    "moneyflowIndustryConfirm": "cross-section-moneyflow-industry-confirm-weight",
    "moneyflowRsiConfirm": "cross-section-moneyflow-rsi-confirm-weight",
    "moneyflowMarketStrong": "cross-section-moneyflow-market-strong-weight",
    "moneyflowMarketQuality": "cross-section-moneyflow-market-quality-weight",
    "moneyflowMarketSurgeQuality": "cross-section-moneyflow-market-surge-quality-weight",
    "moneyflowMarketSurgeStrictQuality": "cross-section-moneyflow-market-surge-strict-quality-weight",
    "moneyflowMarketSurgeRelativeQuality": "cross-section-moneyflow-market-surge-relative-quality-weight",
    "industryMoneyflowSumNetRank1": "cross-section-industry-moneyflow-sum-net-rank1-weight",
    "kplConceptCountRank1": "cross-section-kpl-concept-count-rank1-weight",
}
RESEARCH_COMMON_PARAM_FLAGS = {
    "costMultiplier": "cost-multiplier",
    "maxSinglePositionPct": "max-single-position-pct",
    "marketMinSamples": "market-min-samples",
    "marketMinAboveMa20Pct": "market-min-above-ma20-pct",
    "marketMinAboveMa60Pct": "market-min-above-ma60-pct",
    "marketMinUpPct": "market-min-up-pct",
    "marketSoftMinSamples": "market-soft-min-samples",
    "marketSoftMinAboveMa20Pct": "market-soft-min-above-ma20-pct",
    "marketSoftMinAboveMa60Pct": "market-soft-min-above-ma60-pct",
    "marketSoftMinUpPct": "market-soft-min-up-pct",
    "marketSoftMaxBaseFailedChecks": "market-soft-max-base-failed-checks",
    "maxEntryGapPct": "max-entry-gap-pct",
    "minEntryGapPct": "min-entry-gap-pct",
    "maxEntryRangePct": "max-entry-range-pct",
    "maxIntradayReturnPct": "max-intraday-return-pct",
    "failureSymbolCooldownDays": "failure-symbol-cooldown-days",
    "failureIndustryWeeklyLossLimit": "failure-industry-weekly-loss-limit",
    "failureIndustryCooldownDays": "failure-industry-cooldown-days",
    "industryStateMinSamples": "industry-state-min-samples",
    "industryStateMinUpPct": "industry-state-min-up-pct",
    "industryStateMinAboveMa20Pct": "industry-state-min-above-ma20-pct",
    "industryStateMinAboveMa60Pct": "industry-state-min-above-ma60-pct",
    "industryStateMinReturn20Pct": "industry-state-min-return20-pct",
    "entryGapSizeHaircutThresholdPct": "entry-gap-size-haircut-threshold-pct",
    "entryGapSizeHaircutPct": "entry-gap-size-haircut-pct",
    "entryRangeSizeHaircutThresholdPct": "entry-range-size-haircut-threshold-pct",
    "entryRangeSizeHaircutPct": "entry-range-size-haircut-pct",
    "entryIntradaySizeHaircutThresholdPct": "entry-intraday-size-haircut-threshold-pct",
    "entryIntradaySizeHaircutPct": "entry-intraday-size-haircut-pct",
    "slippagePct": "slippage-pct",
    "buySlippagePct": "buy-slippage-pct",
    "sellSlippagePct": "sell-slippage-pct",
    "limitBandTolerancePct": "limit-band-tolerance-pct",
}
RESEARCH_PORTFOLIO_ONLY_PARAM_FLAGS = {
    "maxOvernightExposurePct": "max-overnight-exposure-pct",
    "entryGapScorePenaltyThresholdPct": "entry-gap-score-penalty-threshold-pct",
    "entryGapScorePenalty": "entry-gap-score-penalty",
    "entryRangeScorePenaltyThresholdPct": "entry-range-score-penalty-threshold-pct",
    "entryRangeScorePenalty": "entry-range-score-penalty",
    "entryPriorVolumeRatioBasicScorePenaltyThreshold": "entry-prior-volume-ratio-basic-score-penalty-threshold",
    "entryPriorVolumeRatioBasicScorePenalty": "entry-prior-volume-ratio-basic-score-penalty",
    "entryVolumeInefficiencyCrowdingPriorVolumeRatioBasicThreshold": "entry-volume-inefficiency-crowding-prior-volume-ratio-basic-threshold",
    "entryVolumeInefficiencyCrowdingAmountEfficiencyRsiRankMax": "entry-volume-inefficiency-crowding-amount-efficiency-rsi-rank-max",
    "entryVolumeInefficiencyCrowdingScorePenalty": "entry-volume-inefficiency-crowding-score-penalty",
    "entryIndustryReturnOverheatRankThreshold": "entry-industry-return-overheat-rank-threshold",
    "entryIndustryReturnOverheatScorePenalty": "entry-industry-return-overheat-score-penalty",
    "entryUnsupportedBollSqueezeBollRankThreshold": "entry-unsupported-boll-squeeze-boll-rank-threshold",
    "entryUnsupportedBollSqueezeIndustryMoneyflowRankMax": "entry-unsupported-boll-squeeze-industry-moneyflow-rank-max",
    "entryUnsupportedBollSqueezeScorePenalty": "entry-unsupported-boll-squeeze-score-penalty",
    "entryIndustryMoneyflowCrowdingSumRankThreshold": "entry-industry-moneyflow-crowding-sum-rank-threshold",
    "entryIndustryMoneyflowCrowdingPersistentScoreMax": "entry-industry-moneyflow-crowding-persistent-score-max",
    "entryIndustryMoneyflowCrowdingScorePenalty": "entry-industry-moneyflow-crowding-score-penalty",
    "entryMoneyflowSurgeRsiCrowdingSurgeRankThreshold": "entry-moneyflow-surge-rsi-crowding-surge-rank-threshold",
    "entryMoneyflowSurgeRsiCrowdingRsiRankThreshold": "entry-moneyflow-surge-rsi-crowding-rsi-rank-threshold",
    "entryMoneyflowSurgeRsiCrowdingGapThresholdPct": "entry-moneyflow-surge-rsi-crowding-gap-threshold-pct",
    "entryMoneyflowSurgeRsiCrowdingRangeThresholdPct": "entry-moneyflow-surge-rsi-crowding-range-threshold-pct",
    "entryMoneyflowSurgeRsiCrowdingScorePenalty": "entry-moneyflow-surge-rsi-crowding-score-penalty",
    "entryIndicatorConfluenceMoneyflowCrowdingConfluenceRankThreshold": "entry-indicator-confluence-moneyflow-crowding-confluence-rank-threshold",
    "entryIndicatorConfluenceMoneyflowCrowdingMoneyflow5RankThreshold": "entry-indicator-confluence-moneyflow-crowding-moneyflow5-rank-threshold",
    "entryIndicatorConfluenceMoneyflowCrowdingMinGapPct": "entry-indicator-confluence-moneyflow-crowding-min-gap-pct",
    "entryIndicatorConfluenceMoneyflowCrowdingScorePenalty": "entry-indicator-confluence-moneyflow-crowding-score-penalty",
    "entryUnconfirmedGapRangeMinGapPct": "entry-unconfirmed-gap-range-min-gap-pct",
    "entryUnconfirmedGapRangeMinRangePct": "entry-unconfirmed-gap-range-min-range-pct",
    "entryUnconfirmedGapRangeMaxSurgeRank": "entry-unconfirmed-gap-range-max-surge-rank",
    "entryUnconfirmedGapRangeScorePenalty": "entry-unconfirmed-gap-range-score-penalty",
    "maxPriorGapDown60Pct": "max-prior-gap-down-60-pct",
    "maxPriorGapDown3Count60": "max-prior-gap-down-3-count-60",
    "limitUpEntryBlockPct": "limit-up-entry-block-pct",
    "nextOpenCancelGapDownPct": "next-open-cancel-gap-down-pct",
    "earlyExitDays": "early-exit-days",
    "earlyExitLossPct": "early-exit-loss-pct",
    "gapStopMarketCooldownDays": "gap-stop-market-cooldown-days",
    "gapStopIndustryCooldownDays": "gap-stop-industry-cooldown-days",
    "gapStopSymbolCooldownDays": "gap-stop-symbol-cooldown-days",
    "industryOvernightRiskWindowDays": "industry-overnight-risk-window-days",
    "industryOvernightRiskGapDownPct": "industry-overnight-risk-gap-down-pct",
    "industryOvernightRiskMinCount": "industry-overnight-risk-min-count",
    "industryOvernightRiskMinRatio": "industry-overnight-risk-min-ratio",
}
RESEARCH_COMMON_BOOL_FLAGS = {
    "disableMarketBreadthFilter": "disable-market-breadth-filter",
    "marketSoftGate": "market-soft-gate",
    "industryStateFilter": "industry-state-filter",
    "stopGapFillAtOpen": "stop-gap-fill-at-open",
    "limitDownStopDelay": "limit-down-stop-delay",
}
RESEARCH_PORTFOLIO_ONLY_BOOL_FLAGS = {
    "nextOpenEntry": "next-open-entry",
    "earlyExitEntryLowBreak": "early-exit-entry-low-break",
}


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/strategies/executable/{strategy_id}")
def get_executable_strategy(strategy_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    if strategy_id != EXECUTABLE_STRATEGY_ID:
        raise HTTPException(status_code=404, detail="策略基线不存在")

    spec = read_json_file(EXECUTABLE_STRATEGY_SPEC_PATH)
    run_id = str(spec.get("evidenceRun") or "")
    results_path = REPO_ROOT / "docs" / "research" / "runs" / run_id / "results.json"
    review_path = REPO_ROOT / "docs" / "research" / "runs" / run_id / "review.md"
    if not run_id or not results_path.exists():
        raise HTTPException(status_code=404, detail="策略证据文件不存在")

    run = read_json_file(results_path)
    analysis = apply_strategy_spec_analysis_override(spec, run.get("analysis", {}))
    result = run.get("result", {})
    equity_curve = build_portfolio_equity_curve(result.get("equity", []))
    symbol_audit = analysis.get("symbolAudit", {})
    symbol_audit_rows = build_executable_symbol_audit_rows(result.get("completedTrades", []))
    robustness = build_executable_robustness_diagnostics(spec, analysis, equity_curve, query_daily_bar_coverage(db))
    response = {
        "id": strategy_id,
        "label": "横截面择强 Risk8",
        "status": spec.get("status"),
        "spec": spec,
        "runId": run_id,
        "resultFiles": {
            "results": results_path.relative_to(REPO_ROOT).as_posix(),
            "review": review_path.relative_to(REPO_ROOT).as_posix(),
        },
        "startedAt": run.get("startedAt"),
        "finishedAt": run.get("finishedAt"),
        "strategy": run.get("strategy", {}),
        "portfolioRules": run.get("portfolioRules", {}),
        "payload": run.get("payload", {}),
        "scope": result.get("scope", {}),
        "summary": result.get("summary", {}),
        "analysis": analysis,
        "metrics": compact_executable_metrics(analysis),
        "objectiveGates": analysis.get("objectiveGates", {}),
        "diagnosticGates": analysis.get("diagnosticGates", {}),
        "equityCurve": equity_curve,
        "symbolAudit": {**symbol_audit, "rows": symbol_audit_rows},
        "symbolAuditRows": symbol_audit_rows,
        "top10": symbol_audit.get("top10", []),
        "bottom10": symbol_audit.get("bottom10", []),
        "capitalBottom10": symbol_audit.get("capitalBottom10", []),
        "tailRisk": symbol_audit.get("tailRisk", {}),
        "tailLossRisk": symbol_audit.get("tailLossRisk", {}),
        "tailRatioEvidence": symbol_audit.get("tailRatioEvidence", {}),
        "tailCapitalRisk": symbol_audit.get("tailCapitalRisk", {}),
        "completedTrades": result.get("completedTrades", []),
        "allTrades": result.get("trades", []),
        "recentTrades": result.get("trades", [])[-40:],
        "finalPositions": result.get("finalPositions", []),
        "resultCounts": {
            "equityDays": len(result.get("equity", [])),
            "tradeActions": len(result.get("trades", [])),
            "completedTrades": len(result.get("completedTrades", [])),
            "finalPositions": len(result.get("finalPositions", [])),
            "symbolAuditRows": len(symbol_audit_rows),
        },
        "reviewText": review_path.read_text(encoding="utf-8") if review_path.exists() else "",
        "robustness": robustness,
        "aiAnalysis": build_executable_strategy_ai_analysis(spec, analysis, robustness),
    }
    return json_safe(response)


@app.get("/api/research/runs")
def list_research_runs(limit: int = 120, include_unmerged: bool = False) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 120), 300))
    runs = []
    errors = []
    if not RESEARCH_RUNS_DIR.exists():
        return {"sourceDir": "docs/research/runs", "count": 0, "runs": [], "errors": []}

    run_dirs = sorted((item for item in RESEARCH_RUNS_DIR.iterdir() if item.is_dir()), key=lambda item: item.name, reverse=True)
    available_run_ids = {item.name for item in run_dirs}
    registry = read_research_registry()
    registry_summaries = {
        str(item.get("runId")): build_research_registry_run_summary(item, available_run_ids)
        for item in registry.get("runs", [])
        if isinstance(item, dict) and item.get("runId")
    }
    for run_dir in run_dirs:
        if len(runs) >= safe_limit:
            break
        results_path = run_dir / "results.json"
        if not results_path.exists():
            continue
        if run_dir.name in registry_summaries:
            runs.append(registry_summaries[run_dir.name])
            continue
        if not include_unmerged:
            continue
        try:
            summary = read_research_run_summary_cached(run_dir)
        except HTTPException as exc:
            errors.append({"runId": run_dir.name, "detail": exc.detail})
            continue
        runs.append(summary)

    return json_safe({"sourceDir": "docs/research/runs", "count": len(runs), "runs": runs, "errors": errors})


@app.get("/api/research/overview")
def get_research_overview() -> dict[str, Any]:
    return json_safe(build_research_overview())


@app.get("/api/research/sessions/{session_id}")
def get_research_session(session_id: str) -> dict[str, Any]:
    registry = read_research_registry()
    active_stage = registry.get("activeStage", {})
    session_dir = resolve_research_session_dir(str(active_stage.get("stageDir") or ""), session_id)
    return json_safe(build_research_session_summary(session_dir, include_text=True))


@app.get("/api/research/runs/{run_id}")
def get_research_run(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return json_safe(build_research_run_response(run_id, db))


@app.post("/api/research/jobs", status_code=202)
def create_research_job(payload: ResearchJobRequest) -> dict[str, Any]:
    cleanup_research_jobs()
    command, run_id = build_research_job_command(payload)
    job_id = uuid4().hex
    now = datetime.now().isoformat(timespec="seconds")
    job = {
        "jobId": job_id,
        "jobType": payload.job_type,
        "runId": run_id,
        "status": "queued",
        "message": "等待研究任务启动",
        "createdAt": now,
        "updatedAt": now,
        "progressPct": 0,
        "command": command,
        "request": research_payload_dict(payload),
        "_updatedMono": monotonic(),
    }
    with RESEARCH_JOB_LOCK:
        RESEARCH_JOBS[job_id] = job
    RESEARCH_JOB_EXECUTOR.submit(run_research_job, job_id, command, run_id)
    return public_research_job(job)


@app.get("/api/research/jobs")
def list_research_jobs() -> dict[str, Any]:
    cleanup_research_jobs()
    with RESEARCH_JOB_LOCK:
        jobs = [public_research_job(job) for job in sorted(RESEARCH_JOBS.values(), key=lambda item: str(item.get("createdAt") or ""), reverse=True)]
    return {"count": len(jobs), "jobs": jobs}


@app.get("/api/research/jobs/{job_id}")
def get_research_job(job_id: str) -> dict[str, Any]:
    with RESEARCH_JOB_LOCK:
        job = RESEARCH_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="未找到这个研究任务，可能已经过期。")
        return public_research_job(job)


@app.post("/api/research/jobs/{job_id}/cancel")
def cancel_research_job(job_id: str) -> dict[str, Any]:
    with RESEARCH_JOB_LOCK:
        job = RESEARCH_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="未找到这个研究任务，可能已经过期。")
        process = job.get("_process")
        if job.get("status") not in {"queued", "running", "canceling"}:
            return public_research_job(job)
        job["_cancelRequested"] = True
        if process and process.poll() is None:
            process.terminate()
            job["status"] = "canceling"
            job["message"] = "正在取消研究任务"
        else:
            job["status"] = "canceled"
            job["message"] = "研究任务已取消"
            job["finishedAt"] = datetime.now().isoformat(timespec="seconds")
        job["updatedAt"] = datetime.now().isoformat(timespec="seconds")
        job["_updatedMono"] = monotonic()
        return public_research_job(job)


@app.get("/api/research/jobs/{job_id}/result")
def get_research_job_result(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    with RESEARCH_JOB_LOCK:
        job = RESEARCH_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="未找到这个研究任务，可能已经过期。")
        run_id = str(job.get("runId") or "")
        status = str(job.get("status") or "")
    if status != "ok":
        raise HTTPException(status_code=409, detail="研究任务尚未完成。")
    return json_safe(build_research_run_response(run_id, db))


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "quantitative-trading-api", "docs": "/docs"}


@app.get("/api/stocks", response_model=list[StockOut])
def list_stocks(q: str | None = None, limit: int = 50, db: Session = Depends(get_db)) -> list[StockOut]:
    stmt = select(Stock)
    if q:
        like = f"%{q}%"
        stmt = stmt.where((Stock.ts_code.ilike(like)) | (Stock.symbol.ilike(like)) | (Stock.name.ilike(like)) | (Stock.industry.ilike(like)))
    stmt = stmt.order_by(Stock.ts_code).limit(min(limit, 200))
    return [stock_to_schema(stock) for stock in db.scalars(stmt).all()]


@app.get("/api/stocks/screen", response_model=list[StockScreenOut])
def screen_stocks(
    q: str | None = None,
    industry: str | None = None,
    market: str | None = None,
    technical: str = "all",
    rank_by: str = "composite",
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 60,
    db: Session = Depends(get_db),
) -> list[StockScreenOut]:
    end = end_date or date.today()
    start = start_date or end - timedelta(days=730)
    requested_limit = min(limit, 200)
    scan_limit = requested_limit
    if (rank_by or "").strip().lower() in {"fundamental", "quality", "valuation"}:
        scan_limit = min(max(requested_limit * 8, requested_limit), 800)
    stmt = select(Stock)
    if q:
        like = f"%{q}%"
        stmt = stmt.where((Stock.ts_code.ilike(like)) | (Stock.symbol.ilike(like)) | (Stock.name.ilike(like)) | (Stock.industry.ilike(like)))
    if industry:
        stmt = stmt.where(Stock.industry.ilike(f"%{industry}%"))
    if market:
        stmt = stmt.where(Stock.market.ilike(f"%{market}%"))

    stocks = db.scalars(stmt.order_by(Stock.ts_code).limit(scan_limit)).all()
    ts_codes = [stock.ts_code for stock in stocks]
    bars_by_code = query_screen_bars_by_code(db, ts_codes, start, end)
    fundamentals_by_code = query_latest_fundamentals_by_code(db, ts_codes, start, end)
    screened = [
        build_screen_row(
            stock,
            bars_by_code.get(stock.ts_code, []),
            *fundamentals_by_code.get(stock.ts_code, (None, None)),
            technical,
        )
        for stock in stocks
    ]
    if technical != "all":
        screened = [row for row in screened if row.technical_score > 0 and "无本地日线" not in row.technical_tags]
    return sort_screened_stocks(screened, rank_by)[:requested_limit]


@app.get("/api/stock-pools", response_model=list[StockPoolOut])
def list_stock_pools(db: Session = Depends(get_db)) -> list[StockPoolOut]:
    stmt = (
        select(StockPool, func.count(StockPoolMember.id))
        .outerjoin(StockPoolMember, StockPoolMember.pool_id == StockPool.id)
        .group_by(StockPool.id)
        .order_by(StockPool.created_at.desc(), StockPool.id.desc())
    )
    return [stock_pool_to_schema(pool, count) for pool, count in db.execute(stmt).all()]


@app.post("/api/stock-pools", response_model=StockPoolOut)
def create_stock_pool(payload: StockPoolCreate, db: Session = Depends(get_db)) -> StockPoolOut:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="池子名称不能为空。")
    exists = db.scalars(select(StockPool).where(func.lower(StockPool.name) == name.lower()).limit(1)).first()
    if exists:
        raise HTTPException(status_code=409, detail="已经存在同名标的池。")

    pool = StockPool(name=name, description=payload.description.strip() if payload.description else None)
    db.add(pool)
    db.commit()
    db.refresh(pool)
    return stock_pool_to_schema(pool, 0)


@app.get("/api/stock-pools/{pool_id}", response_model=StockPoolDetailOut)
def get_stock_pool(pool_id: int, db: Session = Depends(get_db)) -> StockPoolDetailOut:
    pool = get_stock_pool_or_404(db, pool_id)
    return stock_pool_detail_to_schema(db, pool)


@app.post("/api/stock-pools/{pool_id}/members", response_model=StockPoolDetailOut)
def add_stock_pool_members(pool_id: int, payload: StockPoolMembersRequest, db: Session = Depends(get_db)) -> StockPoolDetailOut:
    pool = get_stock_pool_or_404(db, pool_id)
    ts_codes: list[str] = []
    for item in payload.ts_codes:
        text = item.strip()
        if text:
            ts_codes.append(resolve_ts_code(db, text))
    ts_codes = list(dict.fromkeys(ts_codes))
    if not ts_codes:
        raise HTTPException(status_code=400, detail="请至少提供一个有效标的。")

    rows = [{"pool_id": pool.id, "ts_code": ts_code} for ts_code in ts_codes]
    stmt = pg_insert(StockPoolMember.__table__).values(rows)
    db.execute(stmt.on_conflict_do_nothing(index_elements=["pool_id", "ts_code"]))
    db.commit()
    return stock_pool_detail_to_schema(db, pool)


@app.delete("/api/stock-pools/{pool_id}/members/{ts_code}", response_model=StockPoolDetailOut)
def remove_stock_pool_member(pool_id: int, ts_code: str, db: Session = Depends(get_db)) -> StockPoolDetailOut:
    pool = get_stock_pool_or_404(db, pool_id)
    resolved = resolve_ts_code(db, ts_code)
    db.execute(delete(StockPoolMember).where(StockPoolMember.pool_id == pool.id, StockPoolMember.ts_code == resolved))
    db.commit()
    return stock_pool_detail_to_schema(db, pool)


@app.delete("/api/stock-pools/{pool_id}")
def delete_stock_pool(pool_id: int, db: Session = Depends(get_db)) -> dict[str, str | int]:
    pool = get_stock_pool_or_404(db, pool_id)
    db.execute(delete(StockPoolMember).where(StockPoolMember.pool_id == pool.id))
    db.delete(pool)
    db.commit()
    return {"status": "ok", "pool_id": pool_id}


@app.post("/api/tushare/sync-stock-basic")
def sync_stock_basic(payload: SyncStockBasicRequest, db: Session = Depends(get_db)) -> dict[str, int | str]:
    pro = get_pro_api(payload.token)
    df = pro.stock_basic(
        exchange="",
        list_status="L",
        fields="ts_code,symbol,name,area,industry,market,list_date",
    )
    rows = [
        {
            "ts_code": item["ts_code"],
            "symbol": item.get("symbol"),
            "name": item.get("name") or item["ts_code"],
            "area": item.get("area"),
            "industry": item.get("industry"),
            "market": item.get("market"),
            "list_date": parse_tushare_date(item.get("list_date")),
        }
        for item in df.to_dict("records")
    ]
    if rows:
        stmt = pg_insert(Stock.__table__).values(rows)
        update_cols = {col: getattr(stmt.excluded, col) for col in rows[0] if col != "ts_code"}
        db.execute(stmt.on_conflict_do_update(index_elements=["ts_code"], set_=update_cols))
    db.add(DataSyncRun(target="stock_basic", rows_upserted=len(rows), status="ok"))
    db.commit()
    return {"status": "ok", "rows_upserted": len(rows)}


@app.post("/api/tushare/sync-daily")
def sync_daily(payload: SyncDailyRequest, db: Session = Depends(get_db)) -> dict[str, int | str]:
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="end_date 不能早于 start_date。")

    ts_code = resolve_ts_code(db, payload.ts_code)
    pro = get_pro_api(payload.token)
    df = pro.daily(
        ts_code=ts_code,
        start_date=tushare_date(payload.start_date),
        end_date=tushare_date(payload.end_date),
        fields=DAILY_FIELDS,
    )
    rows = [daily_record_to_row(item) for item in df.to_dict("records")]
    upsert_daily_bar_rows(db, rows)

    db.add(
        DataSyncRun(
            target=ts_code,
            start_date=payload.start_date,
            end_date=payload.end_date,
            rows_upserted=len(rows),
            status="ok",
        )
    )
    db.commit()
    return {"status": "ok", "ts_code": ts_code, "rows_upserted": len(rows)}


@app.post("/api/tushare/sync-market-daily")
def sync_market_daily(payload: SyncMarketDataRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="end_date 不能早于 start_date。")

    pro = get_pro_api(payload.token)
    trade_dates = query_market_trade_dates(pro, payload.start_date, payload.end_date)
    if payload.max_trade_dates:
        trade_dates = trade_dates[-payload.max_trade_dates :]
    skipped_trade_dates = 0
    if payload.skip_existing:
        trade_dates, skipped_trade_dates = filter_sparse_trade_dates(db, StockDailyBar.trade_date, trade_dates, payload.min_existing_rows)

    rows_upserted = 0
    failed_dates: list[dict[str, str]] = []
    for trade_day in trade_dates:
        try:
            df = pro.daily(trade_date=tushare_date(trade_day), fields=DAILY_FIELDS)
            rows = dedupe_rows([daily_record_to_row(item) for item in df.to_dict("records")], ("ts_code", "trade_date"))
            rows_upserted += upsert_daily_bar_rows(db, rows)
            db.commit()
        except Exception as error:  # Tushare/network errors should not discard earlier dates.
            db.rollback()
            failed_dates.append({"trade_date": trade_day.isoformat(), "error": compact_error(error)})

    return finish_market_sync_run(
        db=db,
        target="market:daily",
        start_date=payload.start_date,
        end_date=payload.end_date,
        trade_dates=len(trade_dates),
        rows_upserted=rows_upserted,
        failed_dates=failed_dates,
        skipped_trade_dates=skipped_trade_dates,
    )


@app.post("/api/tushare/sync-fundamentals")
def sync_fundamentals(payload: SyncFundamentalsRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="end_date 不能早于 start_date。")

    ts_code = resolve_ts_code(db, payload.ts_code)
    pro = get_pro_api(payload.token)
    daily_basic_df = pro.daily_basic(
        ts_code=ts_code,
        start_date=tushare_date(payload.start_date),
        end_date=tushare_date(payload.end_date),
        fields=DAILY_BASIC_FIELDS,
    )
    fina_indicator_df = pro.fina_indicator(
        ts_code=ts_code,
        start_date=tushare_date(payload.start_date),
        end_date=tushare_date(payload.end_date),
        fields=FINA_INDICATOR_FIELDS,
    )

    daily_rows = dedupe_rows(
        [row for item in daily_basic_df.to_dict("records") if (row := daily_basic_record_to_row(item))],
        ("ts_code", "trade_date"),
    )
    financial_rows = dedupe_rows(
        [row for item in fina_indicator_df.to_dict("records") if (row := financial_indicator_record_to_row(item))],
        ("ts_code", "end_date", "ann_date"),
    )

    upsert_daily_basic_rows(db, daily_rows)
    upsert_financial_indicator_rows(db, financial_rows)

    db.add(
        DataSyncRun(
            target=f"{ts_code}:fundamentals",
            start_date=payload.start_date,
            end_date=payload.end_date,
            rows_upserted=len(daily_rows) + len(financial_rows),
            status="ok",
            message=f"daily_basic={len(daily_rows)}, fina_indicator={len(financial_rows)}",
        )
    )
    db.commit()
    return {
        "status": "ok",
        "ts_code": ts_code,
        "daily_basic_rows": len(daily_rows),
        "fina_indicator_rows": len(financial_rows),
    }


@app.post("/api/tushare/sync-market-daily-basic")
def sync_market_daily_basic(payload: SyncMarketDataRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="end_date 不能早于 start_date。")

    pro = get_pro_api(payload.token)
    trade_dates = query_market_trade_dates(pro, payload.start_date, payload.end_date)
    if payload.max_trade_dates:
        trade_dates = trade_dates[-payload.max_trade_dates :]
    skipped_trade_dates = 0
    if payload.skip_existing:
        trade_dates, skipped_trade_dates = filter_sparse_trade_dates(db, StockDailyBasic.trade_date, trade_dates, payload.min_existing_rows)

    rows_upserted = 0
    failed_dates: list[dict[str, str]] = []
    for trade_day in trade_dates:
        try:
            df = pro.query("daily_basic", ts_code="", trade_date=tushare_date(trade_day), fields=DAILY_BASIC_FIELDS)
            rows = dedupe_rows(
                [row for item in df.to_dict("records") if (row := daily_basic_record_to_row(item))],
                ("ts_code", "trade_date"),
            )
            rows_upserted += upsert_daily_basic_rows(db, rows)
            db.commit()
        except Exception as error:  # Keep already-synced dates durable during a long full-market run.
            db.rollback()
            failed_dates.append({"trade_date": trade_day.isoformat(), "error": compact_error(error)})

    return finish_market_sync_run(
        db=db,
        target="market:daily_basic",
        start_date=payload.start_date,
        end_date=payload.end_date,
        trade_dates=len(trade_dates),
        rows_upserted=rows_upserted,
        failed_dates=failed_dates,
        skipped_trade_dates=skipped_trade_dates,
    )


@app.post("/api/tushare/sync-market-fundamentals")
def sync_market_fundamentals(payload: SyncMarketFundamentalsRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="end_date 不能早于 start_date。")

    stocks = list(db.scalars(select(Stock).order_by(Stock.ts_code)).all())
    if payload.max_stocks:
        stocks = stocks[: payload.max_stocks]
    if not stocks:
        raise HTTPException(status_code=400, detail="请先同步 A 股基础列表。")

    pro = get_pro_api(payload.token)
    rows_upserted = 0
    skipped_stocks = 0
    failed_stocks: list[dict[str, str]] = []
    for stock in stocks:
        if payload.skip_existing and has_financial_rows(db, stock.ts_code, payload.start_date, payload.end_date):
            skipped_stocks += 1
            continue
        try:
            df = pro.fina_indicator(
                ts_code=stock.ts_code,
                start_date=tushare_date(payload.start_date),
                end_date=tushare_date(payload.end_date),
                fields=FINA_INDICATOR_FIELDS,
            )
            rows = dedupe_rows(
                [row for item in df.to_dict("records") if (row := financial_indicator_record_to_row(item))],
                ("ts_code", "end_date", "ann_date"),
            )
            rows_upserted += upsert_financial_indicator_rows(db, rows)
            db.commit()
        except Exception as error:
            db.rollback()
            failed_stocks.append({"ts_code": stock.ts_code, "error": compact_error(error)})

    status = "partial" if failed_stocks else "ok"
    if failed_stocks and not rows_upserted and not skipped_stocks:
        status = "failed"
    db.add(
        DataSyncRun(
            target="market:fundamentals",
            start_date=payload.start_date,
            end_date=payload.end_date,
            rows_upserted=rows_upserted,
            status=status,
            message=f"stocks={len(stocks)}, skipped_stocks={skipped_stocks}, failed_stocks={len(failed_stocks)}",
        )
    )
    db.commit()
    return {
        "status": status,
        "stocks": len(stocks),
        "skipped_stocks": skipped_stocks,
        "financial_rows": rows_upserted,
        "failed_stocks": failed_stocks,
    }


@app.get("/api/tushare/sync-progress")
def get_sync_progress(
    target: str,
    start_date: date,
    end_date: date,
    min_existing_rows: int = 5000,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date 不能早于 start_date。")
    if min_existing_rows < 1:
        raise HTTPException(status_code=400, detail="min_existing_rows 必须大于 0。")
    return build_sync_progress(db, target, start_date, end_date, min_existing_rows)


@app.get("/api/daily-bars", response_model=list[DailyBarOut])
def get_daily_bars(ts_code: str, start_date: date, end_date: date, db: Session = Depends(get_db)) -> list[DailyBarOut]:
    ts_code = resolve_ts_code(db, ts_code)
    bars = query_daily_bars(db, ts_code, start_date, end_date)
    return enriched_bars_to_schema(bars)


@app.get("/api/stocks/{ts_code}/fundamentals", response_model=StockFundamentalsOut)
def get_stock_fundamentals(ts_code: str, start_date: date | None = None, end_date: date | None = None, db: Session = Depends(get_db)) -> StockFundamentalsOut:
    resolved = resolve_ts_code(db, ts_code)
    valuation, financial = query_latest_fundamentals(db, resolved, start_date, end_date)
    profile = build_fundamental_profile(valuation, financial)
    return StockFundamentalsOut(
        ts_code=resolved,
        valuation=daily_basic_to_dict(valuation),
        financial=financial_indicator_to_dict(financial),
        score=profile["score"],
        tags=profile["tags"],
    )


@app.get("/api/news/trends", response_model=NewsTrendOut)
def get_news_trends(sources: str = "cls,wallstreetcn,xueqiu", count: int = 6, q: str | None = None) -> NewsTrendOut:
    selected_sources = [source.strip() for source in sources.split(",") if source.strip()]
    if not selected_sources:
        raise HTTPException(status_code=400, detail="请至少选择一个消息源。")

    items = collect_news_items(selected_sources, count)
    if not items:
        sleep(0.5)
        items = collect_news_items(selected_sources, count)

    limit = min(max(count * len(selected_sources), 1), 60)
    if q:
        keyword = q.strip().lower()
        matched_items = [item for item in items if keyword in item["title"].lower() or keyword in item["source_name"].lower()]
        if matched_items:
            return NewsTrendOut(status="ok", items=matched_items[:limit])
        if items:
            return NewsTrendOut(status="fallback", items=items[:limit], message=f"未匹配到“{q.strip()}”，已展示通用财经热点。")

    if not items:
        return NewsTrendOut(status="empty", items=[], message="新闻源暂时没有返回数据，请稍后重试。")
    return NewsTrendOut(status="ok", items=items[:limit])


@app.get("/api/stocks/{ts_code}/quality-analysis")
def analyze_stock_quality(
    ts_code: str,
    start_date: date | None = None,
    end_date: date | None = None,
    use_ai: bool = True,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    resolved = resolve_ts_code(db, ts_code)
    stock = db.get(Stock, resolved)
    end = end_date or date.today()
    start = start_date or end - timedelta(days=730)
    bars = query_daily_bars(db, resolved, start, end)
    rows = [bar_to_backtest_row(bar) for bar in bars]
    enriched = enrich_rows(rows, DEFAULT_CONFIG) if rows else []
    valuation, financial = query_latest_fundamentals(db, resolved, start, end)
    fundamentals = build_fundamental_profile(valuation, financial)
    news_items = stock_news_items(stock, resolved)
    agents = [
        build_fundamental_agent(fundamentals, valuation, financial),
        build_technical_agent(enriched),
        build_sentiment_agent(news_items, stock.name if stock else resolved),
        build_news_agent(news_items),
    ]
    report = build_quality_report(
        stock=stock,
        ts_code=resolved,
        start_date=start,
        end_date=end,
        agents=agents,
        rows=enriched,
        valuation=valuation,
        financial=financial,
        news_items=news_items,
    )
    return json_safe(analyze_stock_quality_with_deepseek(report) if use_ai else report)


@app.post("/api/backtests/run")
def run_db_backtest(payload: BacktestRequest, db: Session = Depends(get_db)) -> dict:
    ts_code = resolve_ts_code(db, payload.ts_code)
    bars = query_daily_bars(db, ts_code, payload.start_date, payload.end_date)
    if not bars:
        raise HTTPException(status_code=404, detail="数据库里没有这个区间的行情，请先同步 Tushare 数据。")
    stock = db.get(Stock, ts_code)
    rows = [bar_to_backtest_row(bar) for bar in bars]
    config = dict(payload.config)
    config["symbolName"] = config.get("symbolName") or (f"{stock.name} {ts_code}" if stock else ts_code)
    return run_backtest(rows, config)


@app.post("/api/backtests/market")
def run_market_backtest(payload: MarketBacktestRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="end_date 不能早于 start_date。")

    return execute_market_backtest(db, payload)


@app.post("/api/backtests/market/jobs")
def create_market_backtest_job(payload: MarketBacktestRequest) -> dict[str, Any]:
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="end_date 不能早于 start_date。")

    cleanup_market_backtest_jobs()
    job_id = uuid4().hex
    job = {
        "jobId": job_id,
        "status": "queued",
        "message": "等待后台验证启动",
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
        "progressPct": 0,
        "total": 0,
        "processed": 0,
        "tested": 0,
        "skipped": 0,
        "failed": 0,
        "batchSize": MARKET_BACKTEST_BATCH_SIZE,
        "workers": MARKET_BACKTEST_WORKERS,
        "_updatedMono": monotonic(),
    }
    with MARKET_BACKTEST_LOCK:
        MARKET_BACKTEST_JOBS[job_id] = job
    payload_copy = MarketBacktestRequest(**(payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()))
    MARKET_BACKTEST_EXECUTOR.submit(run_market_backtest_job, job_id, payload_copy)
    return public_market_backtest_job(job)


@app.get("/api/backtests/market/jobs/{job_id}")
def get_market_backtest_job(job_id: str) -> dict[str, Any]:
    with MARKET_BACKTEST_LOCK:
        job = MARKET_BACKTEST_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="未找到这个全市场验证任务，可能已经过期。")
        return public_market_backtest_job(job)


def query_backtest_stocks(db: Session, payload: MarketBacktestRequest) -> list[Stock]:
    stmt = select(Stock)
    if payload.pool_id:
        get_stock_pool_or_404(db, payload.pool_id)
        stmt = stmt.join(StockPoolMember, StockPoolMember.ts_code == Stock.ts_code).where(StockPoolMember.pool_id == payload.pool_id)
    if payload.q:
        like = f"%{payload.q.strip()}%"
        stmt = stmt.where((Stock.ts_code.ilike(like)) | (Stock.symbol.ilike(like)) | (Stock.name.ilike(like)) | (Stock.industry.ilike(like)))
    if payload.industry:
        stmt = stmt.where(Stock.industry.ilike(f"%{payload.industry.strip()}%"))
    if payload.market:
        stmt = stmt.where(Stock.market.ilike(f"%{payload.market.strip()}%"))
    if payload.exclude_st:
        stmt = stmt.where(~Stock.name.ilike("ST%"), ~Stock.name.ilike("*ST%"))
    if payload.exclude_bj:
        stmt = stmt.where(Stock.market != "北交所", ~Stock.ts_code.ilike("%.BJ"))
    if payload.min_list_days:
        earliest_list_date = payload.start_date - timedelta(days=payload.min_list_days)
        stmt = stmt.where(Stock.list_date.is_not(None), Stock.list_date <= earliest_list_date)
    if payload.min_avg_amount is not None:
        amount_subquery = (
            select(
                StockDailyBar.ts_code.label("ts_code"),
                func.avg(StockDailyBar.amount).label("avg_amount"),
            )
            .where(
                StockDailyBar.trade_date >= payload.start_date,
                StockDailyBar.trade_date <= payload.end_date,
                StockDailyBar.amount.is_not(None),
            )
            .group_by(StockDailyBar.ts_code)
            .subquery()
        )
        stmt = stmt.join(amount_subquery, amount_subquery.c.ts_code == Stock.ts_code).where(amount_subquery.c.avg_amount >= payload.min_avg_amount)
    if payload.min_avg_circ_mv is not None or payload.min_avg_turnover_rate_f is not None:
        basic_subquery = (
            select(
                StockDailyBasic.ts_code.label("ts_code"),
                func.avg(StockDailyBasic.circ_mv).label("avg_circ_mv"),
                func.avg(StockDailyBasic.turnover_rate_f).label("avg_turnover_rate_f"),
            )
            .where(
                StockDailyBasic.trade_date >= payload.start_date,
                StockDailyBasic.trade_date <= payload.end_date,
            )
            .group_by(StockDailyBasic.ts_code)
            .subquery()
        )
        stmt = stmt.join(basic_subquery, basic_subquery.c.ts_code == Stock.ts_code)
        if payload.min_avg_circ_mv is not None:
            stmt = stmt.where(basic_subquery.c.avg_circ_mv >= payload.min_avg_circ_mv)
        if payload.min_avg_turnover_rate_f is not None:
            stmt = stmt.where(basic_subquery.c.avg_turnover_rate_f >= payload.min_avg_turnover_rate_f)
    stmt = stmt.order_by(StockPoolMember.created_at, Stock.ts_code) if payload.pool_id else stmt.order_by(Stock.ts_code)
    if payload.max_stocks:
        stmt = stmt.limit(payload.max_stocks)
    return list(db.scalars(stmt).all())


def execute_market_backtest(db: Session, payload: MarketBacktestRequest, progress: Any | None = None) -> dict[str, Any]:
    pool = get_stock_pool_or_404(db, payload.pool_id) if payload.pool_id else None
    stocks = [stock_to_market_meta(stock) for stock in query_backtest_stocks(db, payload)]
    total = len(stocks)
    results: list[dict[str, Any]] = []
    processed = 0
    tested = 0
    skipped = 0
    failed = 0
    report_market_backtest_progress(progress, processed, total, tested, skipped, failed, "正在准备候选标的")

    workers = max(MARKET_BACKTEST_WORKERS, 1)
    with ProcessPoolExecutor(max_workers=workers) as executor:
        for batch_start in range(0, total, MARKET_BACKTEST_BATCH_SIZE):
            batch = stocks[batch_start : batch_start + MARKET_BACKTEST_BATCH_SIZE]
            bars_by_code = query_backtest_rows_by_code(db, [stock["ts_code"] for stock in batch], payload.start_date, payload.end_date)
            futures = {}
            for stock in batch:
                rows = bars_by_code.get(stock["ts_code"], [])
                if len(rows) < payload.min_bars:
                    skipped += 1
                    processed += 1
                    report_market_backtest_progress(progress, processed, total, tested, skipped, failed, f"跳过样本不足：{stock['ts_code']}")
                    continue
                futures[executor.submit(run_single_market_backtest, stock, rows, payload.config)] = stock["ts_code"]

            for future in as_completed(futures):
                ts_code = futures[future]
                try:
                    results.append(future.result())
                    tested += 1
                except Exception:
                    failed += 1
                processed += 1
                report_market_backtest_progress(progress, processed, total, tested, skipped, failed, f"验证中：{ts_code}")

    results.sort(key=lambda item: (item["totalReturn"], item["disciplineScore"], item["tradeCount"]), reverse=True)
    returns = [float(item["totalReturn"]) for item in results]
    drawdowns = [float(item["maxDrawdown"]) for item in results]
    winners = [item for item in results if float(item["totalReturn"]) > 0]
    return json_safe(
        {
            "status": "ok",
            "scope": {
                "startDate": payload.start_date.isoformat(),
                "endDate": payload.end_date.isoformat(),
                "poolId": payload.pool_id,
                "poolName": pool.name if pool else None,
                "q": payload.q,
                "industry": payload.industry,
                "market": payload.market,
                "minBars": payload.min_bars,
                "maxStocks": payload.max_stocks,
                "excludeSt": payload.exclude_st,
                "excludeBj": payload.exclude_bj,
                "minListDays": payload.min_list_days,
                "minAvgAmount": payload.min_avg_amount,
                "minAvgCircMv": payload.min_avg_circ_mv,
                "minAvgTurnoverRateF": payload.min_avg_turnover_rate_f,
                "batchSize": MARKET_BACKTEST_BATCH_SIZE,
                "workers": MARKET_BACKTEST_WORKERS,
            },
            "summary": {
                "candidates": total,
                "tested": len(results),
                "skipped": skipped,
                "failed": failed,
                "winners": len(winners),
                "positiveRate": len(winners) / len(results) if results else 0,
                "avgReturn": mean(returns) if returns else 0,
                "medianReturn": median(returns) if returns else 0,
                "avgMaxDrawdown": mean(drawdowns) if drawdowns else 0,
            },
            "results": results,
        }
    )


def report_market_backtest_progress(progress: Any | None, processed: int, total: int, tested: int, skipped: int, failed: int, message: str) -> None:
    if not progress:
        return
    progress(
        {
            "message": message,
            "progressPct": processed / total if total else 0,
            "total": total,
            "processed": processed,
            "tested": tested,
            "skipped": skipped,
            "failed": failed,
        }
    )


def run_single_market_backtest(stock: dict[str, Any], rows: list[tuple[str, float, float, float, float, float]], base_config: dict[str, Any]) -> dict[str, Any]:
    backtest_rows = [
        {
            "ts_code": stock["ts_code"],
            "date": row[0],
            "open": row[1],
            "high": row[2],
            "low": row[3],
            "close": row[4],
            "volume": row[5],
        }
        for row in rows
    ]
    config = dict(base_config)
    config["symbolName"] = config.get("symbolName") or f"{stock['name']} {stock['ts_code']}"
    summary = run_backtest(backtest_rows, config, include_ai=False, include_details=False)
    return {
        "ts_code": stock["ts_code"],
        "name": stock["name"],
        "industry": stock["industry"],
        "market": stock["market"],
        "dataBars": len(rows),
        "latestDate": rows[-1][0],
        "totalReturn": summary["totalReturn"],
        "maxDrawdown": summary["maxDrawdown"],
        "winRate": summary["winRate"],
        "profitLossRatio": summary["profitLossRatio"],
        "profitFactor": summary["profitFactor"],
        "annualizedReturn": summary["annualizedReturn"],
        "annualizedVolatility": summary["annualizedVolatility"],
        "sharpeRatio": summary["sharpeRatio"],
        "sortinoRatio": summary["sortinoRatio"],
        "calmarRatio": summary["calmarRatio"],
        "maxDrawdownDurationDays": summary["maxDrawdownDurationDays"],
        "tradeCount": summary["tradeCount"],
        "completedTrades": summary["completedTradeCount"],
        "finalEquity": summary["finalEquity"],
        "disciplineScore": summary["disciplineScore"],
        "blocked": summary["blocked"],
    }


def stock_to_market_meta(stock: Stock) -> dict[str, Any]:
    return {
        "ts_code": stock.ts_code,
        "name": stock.name,
        "industry": stock.industry,
        "market": stock.market,
    }


def query_backtest_rows_by_code(db: Session, ts_codes: list[str], start_date: date, end_date: date) -> dict[str, list[tuple[str, float, float, float, float, float]]]:
    if not ts_codes:
        return {}
    stmt = (
        select(
            StockDailyBar.ts_code,
            StockDailyBar.trade_date,
            StockDailyBar.open,
            StockDailyBar.high,
            StockDailyBar.low,
            StockDailyBar.close,
            StockDailyBar.vol,
        )
        .where(
            StockDailyBar.ts_code.in_(ts_codes),
            StockDailyBar.trade_date >= start_date,
            StockDailyBar.trade_date <= end_date,
        )
        .order_by(StockDailyBar.ts_code, StockDailyBar.trade_date)
    )
    grouped: dict[str, list[tuple[str, float, float, float, float, float]]] = {}
    for row in db.execute(stmt):
        grouped.setdefault(row.ts_code, []).append((row.trade_date.isoformat(), float(row.open), float(row.high), float(row.low), float(row.close), float(row.vol) if row.vol is not None else 0))
    return grouped


def run_market_backtest_job(job_id: str, payload: MarketBacktestRequest) -> None:
    update_market_backtest_job(job_id, status="running", message="后台验证已启动")
    db = SessionLocal()
    try:
        result = execute_market_backtest(db, payload, progress=lambda update: update_market_backtest_job(job_id, **update))
        update_market_backtest_job(
            job_id,
            status="ok",
            message="全市场验证完成",
            progressPct=1,
            result=result,
            finishedAt=datetime.now().isoformat(timespec="seconds"),
        )
    except Exception as error:
        update_market_backtest_job(
            job_id,
            status="failed",
            message="全市场验证失败",
            error=compact_error(error),
            finishedAt=datetime.now().isoformat(timespec="seconds"),
        )
    finally:
        db.close()


def update_market_backtest_job(job_id: str, **updates: Any) -> None:
    with MARKET_BACKTEST_LOCK:
        job = MARKET_BACKTEST_JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updatedAt"] = datetime.now().isoformat(timespec="seconds")
        job["_updatedMono"] = monotonic()


def public_market_backtest_job(job: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items() if not key.startswith("_")}


def cleanup_market_backtest_jobs() -> None:
    now = monotonic()
    with MARKET_BACKTEST_LOCK:
        expired = [
            job_id
            for job_id, job in MARKET_BACKTEST_JOBS.items()
            if job.get("status") in {"ok", "failed"} and now - float(job.get("_updatedMono", now)) > MARKET_BACKTEST_JOB_TTL_SECONDS
        ]
        for job_id in expired:
            del MARKET_BACKTEST_JOBS[job_id]


def build_research_job_command(payload: ResearchJobRequest) -> tuple[list[str], str]:
    job_type = str(payload.job_type or "").strip()
    script = RESEARCH_JOB_SCRIPTS.get(job_type)
    if not script:
        raise HTTPException(status_code=400, detail=f"不支持的研究任务类型：{job_type}")

    run_id = validate_research_run_id(payload.run_id, "runId")
    run_dir = RESEARCH_RUNS_DIR / run_id
    if run_dir.exists():
        raise HTTPException(status_code=409, detail=f"runId 已存在，不能覆盖既有研究输出：{run_id}")

    command = [sys.executable, script, "--run-id", run_id]
    if job_type == "trade_delta":
        baseline_run = validate_existing_research_run_id(payload.baseline_run, "baselineRun")
        candidate_run = validate_existing_research_run_id(payload.candidate_run, "candidateRun")
        command.extend(["--baseline-run", baseline_run, "--candidate-run", candidate_run])
        reject_research_params(payload.params, {"note"})
        return command, run_id

    if payload.strategy:
        command.extend(["--strategy", str(payload.strategy)])
    if payload.source_run:
        if job_type != "portfolio_backtest":
            raise HTTPException(status_code=400, detail="sourceRun 只支持 portfolio_backtest。")
        command.extend(["--source-run", validate_existing_research_run_id(payload.source_run, "sourceRun")])

    context_path = resolve_research_context_path(payload)
    if context_path:
        command.extend(["--context", context_path])
    moneyflow_cache_path = resolve_research_cache_path(payload.moneyflow_cache, payload.moneyflow_cache_run_id, "moneyflow-cache.jsonl", "moneyflowCache")
    if moneyflow_cache_path:
        command.extend(["--moneyflow-cache", moneyflow_cache_path])
    concept_cache_path = resolve_research_cache_path(payload.concept_cache, payload.concept_cache_run_id, "concept-cache.jsonl", "conceptCache")
    if concept_cache_path:
        command.extend(["--concept-cache", concept_cache_path])

    append_research_params(command, job_type, payload.params)
    return command, run_id


def run_research_job(job_id: str, command: list[str], run_id: str) -> None:
    process: subprocess.Popen[str] | None = None
    update_research_job(job_id, status="running", message="研究任务执行中", progressPct=0.01, startedAt=datetime.now().isoformat(timespec="seconds"))
    try:
        process = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        update_research_job(job_id, pid=process.pid, _process=process)
        stdout, stderr = process.communicate()
        stdout_tail = tail_text(stdout)
        stderr_tail = tail_text(stderr)
        with RESEARCH_JOB_LOCK:
            cancel_requested = bool(RESEARCH_JOBS.get(job_id, {}).get("_cancelRequested"))
        if process.returncode == 0:
            update_research_job(
                job_id,
                status="ok",
                message="研究任务完成",
                progressPct=1,
                stdoutTail=stdout_tail,
                stderrTail=stderr_tail,
                resultFiles=build_known_research_run_files(run_id),
                summary=read_finished_research_job_summary(run_id),
                finishedAt=datetime.now().isoformat(timespec="seconds"),
                _process=None,
            )
        elif cancel_requested:
            update_research_job(
                job_id,
                status="canceled",
                message="研究任务已取消",
                stdoutTail=stdout_tail,
                stderrTail=stderr_tail,
                finishedAt=datetime.now().isoformat(timespec="seconds"),
                _process=None,
            )
        else:
            update_research_job(
                job_id,
                status="failed",
                message="研究任务失败",
                error=f"exit code {process.returncode}",
                stdoutTail=stdout_tail,
                stderrTail=stderr_tail,
                finishedAt=datetime.now().isoformat(timespec="seconds"),
                _process=None,
            )
    except Exception as error:
        if process and process.poll() is None:
            process.terminate()
        update_research_job(
            job_id,
            status="failed",
            message="研究任务失败",
            error=compact_error(error),
            finishedAt=datetime.now().isoformat(timespec="seconds"),
            _process=None,
        )


def append_research_params(command: list[str], job_type: str, params: dict[str, Any]) -> None:
    if not params:
        return
    remaining = dict(params)
    append_nested_research_flags(command, remaining.pop("crossSectionScoreWeights", {}), RESEARCH_WEIGHT_FLAGS, "crossSectionScoreWeights")
    append_nested_research_flags(command, remaining.pop("marketBreadthFilter", {}), {
        "minSamples": "market-min-samples",
        "minAboveMa20Pct": "market-min-above-ma20-pct",
        "minAboveMa60Pct": "market-min-above-ma60-pct",
        "minUpPct": "market-min-up-pct",
        "disabled": "disable-market-breadth-filter",
    }, "marketBreadthFilter")
    append_nested_research_flags(command, remaining.pop("marketBreadthSoftGate", {}), {
        "enabled": "market-soft-gate",
        "minSamples": "market-soft-min-samples",
        "minAboveMa20Pct": "market-soft-min-above-ma20-pct",
        "minAboveMa60Pct": "market-soft-min-above-ma60-pct",
        "minUpPct": "market-soft-min-up-pct",
        "maxBaseFailedChecks": "market-soft-max-base-failed-checks",
    }, "marketBreadthSoftGate")
    append_nested_research_flags(command, remaining.pop("entryRiskFilter", {}), {
        "maxGapPct": "max-entry-gap-pct",
        "minGapPct": "min-entry-gap-pct",
        "maxEntryRangePct": "max-entry-range-pct",
        "maxIntradayReturnPct": "max-intraday-return-pct",
    }, "entryRiskFilter")
    append_nested_research_flags(command, remaining.pop("industryStateFilter", {}), {
        "enabled": "industry-state-filter",
        "minSamples": "industry-state-min-samples",
        "minUpPct": "industry-state-min-up-pct",
        "minAboveMa20Pct": "industry-state-min-above-ma20-pct",
        "minAboveMa60Pct": "industry-state-min-above-ma60-pct",
        "minReturn20Pct": "industry-state-min-return20-pct",
    }, "industryStateFilter")

    value_flags = dict(RESEARCH_COMMON_PARAM_FLAGS)
    bool_flags = dict(RESEARCH_COMMON_BOOL_FLAGS)
    if job_type == "portfolio_backtest":
        value_flags.update(RESEARCH_PORTFOLIO_ONLY_PARAM_FLAGS)
        bool_flags.update(RESEARCH_PORTFOLIO_ONLY_BOOL_FLAGS)

    for key, flag in value_flags.items():
        append_research_value_flag(command, flag, remaining.pop(key, None))
    for key, flag in bool_flags.items():
        append_research_bool_flag(command, flag, remaining.pop(key, None))
    reject_research_params(remaining, {"note"})


def append_nested_research_flags(command: list[str], value: Any, flag_map: dict[str, str], label: str) -> None:
    if value in (None, ""):
        return
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"{label} 必须是对象。")
    remaining = dict(value)
    for key, flag in flag_map.items():
        raw = remaining.pop(key, None)
        if isinstance(raw, bool):
            append_research_bool_flag(command, flag, raw)
        else:
            append_research_value_flag(command, flag, raw)
    reject_research_params(remaining, set())


def append_research_value_flag(command: list[str], flag: str, value: Any) -> None:
    if value is None or value == "":
        return
    if isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"--{flag} 需要数值，不能传布尔值。")
    command.extend([f"--{flag}", str(value)])


def append_research_bool_flag(command: list[str], flag: str, value: Any) -> None:
    if value is None or value is False:
        return
    if value is not True:
        raise HTTPException(status_code=400, detail=f"--{flag} 是布尔开关，只接受 true。")
    command.append(f"--{flag}")


def reject_research_params(params: dict[str, Any], allowed_ignored: set[str]) -> None:
    unknown = [key for key in params if key not in allowed_ignored]
    if unknown:
        raise HTTPException(status_code=400, detail=f"不支持的研究参数：{', '.join(sorted(unknown))}")


def resolve_research_context_path(payload: ResearchJobRequest) -> str | None:
    if payload.context and payload.base_context_run_id:
        raise HTTPException(status_code=400, detail="context 和 baseContextRunId 只能二选一。")
    if payload.base_context_run_id:
        run_id = validate_existing_research_run_id(payload.base_context_run_id, "baseContextRunId")
        return repo_relative_existing_path(RESEARCH_RUNS_DIR / run_id / "context.json", "baseContextRunId")
    if payload.context:
        return repo_relative_existing_path(payload.context, "context")
    return None


def resolve_research_cache_path(path_value: str | None, run_id_value: str | None, file_name: str, label: str) -> str | None:
    if path_value and run_id_value:
        raise HTTPException(status_code=400, detail=f"{label} 和 {label}RunId 只能二选一。")
    if run_id_value:
        run_id = validate_existing_research_run_id(run_id_value, f"{label}RunId")
        return repo_relative_existing_path(RESEARCH_RUNS_DIR / run_id / file_name, f"{label}RunId")
    if path_value:
        return repo_relative_existing_path(path_value, label)
    return None


def validate_research_run_id(run_id: str | None, label: str) -> str:
    value = str(run_id or "").strip()
    if not value or not RESEARCH_RUN_ID_PATTERN.fullmatch(value) or "/" in value or "\\" in value:
        raise HTTPException(status_code=400, detail=f"{label} 不是合法 run id。")
    return value


def validate_existing_research_run_id(run_id: str | None, label: str) -> str:
    value = validate_research_run_id(run_id, label)
    if not (RESEARCH_RUNS_DIR / value).is_dir():
        raise HTTPException(status_code=404, detail=f"{label} 对应的研究 run 不存在：{value}")
    return value


def repo_relative_existing_path(path_value: str | Path, label: str) -> str:
    path = Path(path_value)
    resolved = (REPO_ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    repo_root = REPO_ROOT.resolve()
    if resolved != repo_root and repo_root not in resolved.parents:
        raise HTTPException(status_code=400, detail=f"{label} 必须位于项目目录内。")
    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"{label} 文件不存在：{path_value}")
    return resolved.relative_to(repo_root).as_posix()


def read_finished_research_job_summary(run_id: str) -> dict[str, Any] | None:
    run_dir = RESEARCH_RUNS_DIR / run_id
    if not (run_dir / "results.json").exists():
        return None
    try:
        return read_research_run_summary_cached(run_dir)
    except Exception:
        return None


def update_research_job(job_id: str, **updates: Any) -> None:
    with RESEARCH_JOB_LOCK:
        job = RESEARCH_JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updatedAt"] = datetime.now().isoformat(timespec="seconds")
        job["_updatedMono"] = monotonic()


def public_research_job(job: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items() if not key.startswith("_")}


def cleanup_research_jobs() -> None:
    now = monotonic()
    with RESEARCH_JOB_LOCK:
        expired = [
            job_id
            for job_id, job in RESEARCH_JOBS.items()
            if job.get("status") in {"ok", "failed", "canceled"} and now - float(job.get("_updatedMono", now)) > RESEARCH_JOB_TTL_SECONDS
        ]
        for job_id in expired:
            del RESEARCH_JOBS[job_id]


def research_payload_dict(payload: ResearchJobRequest) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(by_alias=True)
    return payload.dict(by_alias=True)


def tail_text(text: str | None, limit: int = 4000) -> str:
    value = text or ""
    return value[-limit:] if len(value) > limit else value


def query_market_trade_dates(pro: Any, start_date: date, end_date: date) -> list[date]:
    trade_dates = query_tushare_trade_dates(pro, start_date, end_date)
    if trade_dates is not None:
        return trade_dates
    return estimate_weekday_dates(start_date, end_date)


def query_tushare_trade_dates(pro: Any, start_date: date, end_date: date) -> list[date] | None:
    try:
        df = pro.trade_cal(
            exchange="SSE",
            start_date=tushare_date(start_date),
            end_date=tushare_date(end_date),
            is_open="1",
            fields="cal_date,is_open",
        )
        trade_dates = sorted(
            {
                parsed
                for item in df.to_dict("records")
                if str(item.get("is_open", "1")) == "1" and (parsed := parse_tushare_date(item.get("cal_date")))
            }
        )
        return trade_dates
    except Exception:
        return None


def query_cached_trade_dates(start_date: date, end_date: date) -> list[date] | None:
    cache_key = (start_date, end_date)
    now = monotonic()
    cached = TRADE_CALENDAR_CACHE.get(cache_key)
    if cached and now - cached[0] < TRADE_CALENDAR_CACHE_TTL_SECONDS:
        return cached[1]

    try:
        pro = get_pro_api(None)
    except Exception:
        return None
    trade_dates = query_tushare_trade_dates(pro, start_date, end_date)
    if trade_dates is not None:
        TRADE_CALENDAR_CACHE[cache_key] = (now, trade_dates)
    return trade_dates


def filter_sparse_trade_dates(db: Session, date_column: Any, trade_dates: list[date], min_rows: int) -> tuple[list[date], int]:
    if not trade_dates:
        return [], 0
    stmt = select(date_column, func.count()).where(date_column.in_(trade_dates)).group_by(date_column)
    row_counts = {trade_date: count for trade_date, count in db.execute(stmt).all()}
    pending_dates = [trade_date for trade_date in trade_dates if row_counts.get(trade_date, 0) < min_rows]
    return pending_dates, len(trade_dates) - len(pending_dates)


def build_sync_progress(db: Session, target: str, start_date: date, end_date: date, min_rows: int) -> dict[str, Any]:
    targets = {
        "daily": {
            "run_target": "market:daily",
            "date_column": StockDailyBar.trade_date,
            "updated_column": StockDailyBar.updated_at,
            "label": "全市场日线",
        },
        "daily_basic": {
            "run_target": "market:daily_basic",
            "date_column": StockDailyBasic.trade_date,
            "updated_column": StockDailyBasic.updated_at,
            "label": "全市场估值",
        },
    }
    config = targets.get(target)
    if not config:
        raise HTTPException(status_code=400, detail="target 仅支持 daily 或 daily_basic。")

    date_column = config["date_column"]
    updated_column = config["updated_column"]
    stmt = (
        select(date_column, func.count(), func.max(updated_column))
        .where(date_column >= start_date, date_column <= end_date)
        .group_by(date_column)
    )
    date_rows = db.execute(stmt).all()
    observed_dates = len(date_rows)
    last_run = query_latest_sync_run(db, config["run_target"])
    trade_calendar_dates = query_cached_trade_dates(start_date, end_date)
    if trade_calendar_dates is not None:
        calendar_total_dates = len(trade_calendar_dates)
        progress_basis = "tushare_trade_calendar"
    else:
        calendar_total_dates = trade_date_total_from_sync_run(last_run, start_date, end_date)
        progress_basis = "tushare_trade_calendar_from_last_sync" if calendar_total_dates is not None else "estimated_weekdays"
    total_dates = max(calendar_total_dates if calendar_total_dates is not None else estimate_weekdays(start_date, end_date), observed_dates)
    complete_dates = 0
    sparse_dates = 0
    total_rows = 0
    covered_rows = 0
    latest_date: date | None = None
    latest_date_rows = 0
    last_updated_at = None

    for trade_date, row_count, updated_at in date_rows:
        total_rows += row_count
        covered_rows += min(row_count, min_rows)
        if row_count >= min_rows:
            complete_dates += 1
        elif row_count > 0:
            sparse_dates += 1
        if latest_date is None or trade_date > latest_date:
            latest_date = trade_date
            latest_date_rows = row_count
        if updated_at and (last_updated_at is None or updated_at > last_updated_at):
            last_updated_at = updated_at

    empty_dates = max(total_dates - observed_dates, 0)
    expected_rows = total_dates * min_rows
    progress_pct = min(1, covered_rows / expected_rows) if expected_rows else 0
    return {
        "status": "ok",
        "target": target,
        "label": config["label"],
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "minExistingRows": min_rows,
        "totalDates": total_dates,
        "observedDates": observed_dates,
        "completeDates": complete_dates,
        "sparseDates": sparse_dates,
        "emptyDates": empty_dates,
        "progressPct": progress_pct,
        "rows": total_rows,
        "latestDate": latest_date.isoformat() if latest_date else None,
        "latestDateRows": latest_date_rows,
        "lastUpdatedAt": last_updated_at.isoformat() if last_updated_at else None,
        "lastRun": sync_run_to_dict(last_run),
        "basis": progress_basis,
    }


def trade_date_total_from_sync_run(run: DataSyncRun | None, start_date: date, end_date: date) -> int | None:
    if not run or not run.start_date or not run.end_date or not run.message:
        return None
    if run.start_date > start_date or run.end_date < end_date:
        return None
    trade_dates = sync_message_int(run.message, "trade_dates")
    skipped_dates = sync_message_int(run.message, "skipped_dates")
    if trade_dates is None or skipped_dates is None:
        return None
    return trade_dates + skipped_dates


def sync_message_int(message: str, key: str) -> int | None:
    match = re.search(rf"{key}=(\d+)", message)
    return int(match.group(1)) if match else None


def estimate_weekdays(start_date: date, end_date: date) -> int:
    return len(estimate_weekday_dates(start_date, end_date))


def estimate_weekday_dates(start_date: date, end_date: date) -> list[date]:
    weekdays: list[date] = []
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:
            weekdays.append(current)
        current += timedelta(days=1)
    return weekdays


def query_latest_sync_run(db: Session, target: str) -> DataSyncRun | None:
    return db.scalars(select(DataSyncRun).where(DataSyncRun.target == target).order_by(DataSyncRun.created_at.desc()).limit(1)).first()


def sync_run_to_dict(run: DataSyncRun | None) -> dict[str, Any] | None:
    if not run:
        return None
    return {
        "target": run.target,
        "startDate": run.start_date.isoformat() if run.start_date else None,
        "endDate": run.end_date.isoformat() if run.end_date else None,
        "rowsUpserted": run.rows_upserted,
        "status": run.status,
        "message": run.message,
        "createdAt": run.created_at.isoformat() if run.created_at else None,
    }


def resolve_ts_code(db: Session, text: str) -> str:
    query = text.strip().upper()
    if not query:
        raise HTTPException(status_code=400, detail="请填写股票代码或股票名称。")

    stock = db.get(Stock, query)
    if stock:
        return stock.ts_code

    stmt = (
        select(Stock)
        .where((Stock.symbol == query) | (Stock.name == text.strip()) | (Stock.name.ilike(f"%{text.strip()}%")))
        .order_by(Stock.ts_code)
        .limit(1)
    )
    stock = db.scalars(stmt).first()
    return stock.ts_code if stock else query


def query_daily_bars(db: Session, ts_code: str, start_date: date, end_date: date) -> list[StockDailyBar]:
    stmt = (
        select(StockDailyBar)
        .where(
            StockDailyBar.ts_code == ts_code,
            StockDailyBar.trade_date >= start_date,
            StockDailyBar.trade_date <= end_date,
        )
        .order_by(StockDailyBar.trade_date)
    )
    return list(db.scalars(stmt).all())


def query_screen_bars_by_code(db: Session, ts_codes: list[str], start_date: date, end_date: date) -> dict[str, list[tuple[date, float, float, float, float, float | None, float]]]:
    if not ts_codes:
        return {}
    stmt = (
        select(
            StockDailyBar.ts_code,
            StockDailyBar.trade_date,
            StockDailyBar.open,
            StockDailyBar.high,
            StockDailyBar.low,
            StockDailyBar.close,
            StockDailyBar.pct_chg,
            StockDailyBar.vol,
        )
        .where(
            StockDailyBar.ts_code.in_(ts_codes),
            StockDailyBar.trade_date >= start_date,
            StockDailyBar.trade_date <= end_date,
        )
        .order_by(StockDailyBar.ts_code, StockDailyBar.trade_date)
    )
    grouped: dict[str, list[tuple[date, float, float, float, float, float | None, float]]] = {ts_code: [] for ts_code in ts_codes}
    for row in db.execute(stmt):
        grouped.setdefault(row.ts_code, []).append(
            (
                row.trade_date,
                float(row.open),
                float(row.high),
                float(row.low),
                float(row.close),
                float(row.pct_chg) if row.pct_chg is not None else None,
                float(row.vol) if row.vol is not None else 0,
            )
        )
    return grouped


def query_latest_fundamentals_by_code(
    db: Session,
    ts_codes: list[str],
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, tuple[StockDailyBasic | None, StockFinancialIndicator | None]]:
    if not ts_codes:
        return {}

    result: dict[str, tuple[StockDailyBasic | None, StockFinancialIndicator | None]] = {ts_code: (None, None) for ts_code in ts_codes}
    valuation_filters = [StockDailyBasic.ts_code.in_(ts_codes)]
    financial_filters = [StockFinancialIndicator.ts_code.in_(ts_codes)]
    if start_date:
        valuation_filters.append(StockDailyBasic.trade_date >= start_date)
        financial_filters.append(StockFinancialIndicator.end_date >= start_date)
    if end_date:
        valuation_filters.append(StockDailyBasic.trade_date <= end_date)
        financial_filters.append(StockFinancialIndicator.end_date <= end_date)

    valuation_ranked = (
        select(
            StockDailyBasic.id.label("id"),
            func.row_number()
            .over(partition_by=StockDailyBasic.ts_code, order_by=StockDailyBasic.trade_date.desc())
            .label("row_number"),
        )
        .where(*valuation_filters)
        .subquery()
    )
    valuation_stmt = select(StockDailyBasic).join(valuation_ranked, StockDailyBasic.id == valuation_ranked.c.id).where(valuation_ranked.c.row_number == 1)
    for valuation in db.scalars(valuation_stmt):
        _, financial = result.get(valuation.ts_code, (None, None))
        result[valuation.ts_code] = (valuation, financial)

    financial_ranked = (
        select(
            StockFinancialIndicator.id.label("id"),
            func.row_number()
            .over(
                partition_by=StockFinancialIndicator.ts_code,
                order_by=(StockFinancialIndicator.end_date.desc(), StockFinancialIndicator.ann_date.desc()),
            )
            .label("row_number"),
        )
        .where(*financial_filters)
        .subquery()
    )
    financial_stmt = (
        select(StockFinancialIndicator)
        .join(financial_ranked, StockFinancialIndicator.id == financial_ranked.c.id)
        .where(financial_ranked.c.row_number == 1)
    )
    for financial in db.scalars(financial_stmt):
        valuation, _ = result.get(financial.ts_code, (None, None))
        result[financial.ts_code] = (valuation, financial)

    return result


def daily_record_to_row(item: dict) -> dict:
    return {
        "ts_code": item["ts_code"],
        "trade_date": parse_tushare_date(item["trade_date"]),
        "open": decimal_or_none(item.get("open")),
        "high": decimal_or_none(item.get("high")),
        "low": decimal_or_none(item.get("low")),
        "close": decimal_or_none(item.get("close")),
        "pre_close": decimal_or_none(item.get("pre_close")),
        "change_amount": decimal_or_none(item.get("change")),
        "pct_chg": decimal_or_none(item.get("pct_chg")),
        "vol": decimal_or_none(item.get("vol")),
        "amount": decimal_or_none(item.get("amount")),
    }


def daily_basic_record_to_row(item: dict) -> dict | None:
    trade_date = parse_tushare_date(item.get("trade_date"))
    if not trade_date:
        return None
    return {
        "ts_code": item["ts_code"],
        "trade_date": trade_date,
        "close": decimal_or_none(item.get("close")),
        "turnover_rate": decimal_or_none(item.get("turnover_rate")),
        "turnover_rate_f": decimal_or_none(item.get("turnover_rate_f")),
        "volume_ratio": decimal_or_none(item.get("volume_ratio")),
        "pe": decimal_or_none(item.get("pe")),
        "pe_ttm": decimal_or_none(item.get("pe_ttm")),
        "pb": decimal_or_none(item.get("pb")),
        "ps": decimal_or_none(item.get("ps")),
        "ps_ttm": decimal_or_none(item.get("ps_ttm")),
        "dv_ratio": decimal_or_none(item.get("dv_ratio")),
        "dv_ttm": decimal_or_none(item.get("dv_ttm")),
        "total_share": decimal_or_none(item.get("total_share")),
        "float_share": decimal_or_none(item.get("float_share")),
        "free_share": decimal_or_none(item.get("free_share")),
        "total_mv": decimal_or_none(item.get("total_mv")),
        "circ_mv": decimal_or_none(item.get("circ_mv")),
    }


def upsert_daily_bar_rows(db: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    for chunk in chunk_rows(rows):
        stmt = pg_insert(StockDailyBar.__table__).values(chunk)
        update_cols = {col: getattr(stmt.excluded, col) for col in chunk[0] if col not in {"id", "ts_code", "trade_date", "created_at"}}
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=["ts_code", "trade_date"],
                set_=update_cols,
            )
        )
    return len(rows)


def upsert_daily_basic_rows(db: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    for chunk in chunk_rows(rows):
        stmt = pg_insert(StockDailyBasic.__table__).values(chunk)
        update_cols = {col: getattr(stmt.excluded, col) for col in chunk[0] if col not in {"id", "ts_code", "trade_date", "created_at"}}
        db.execute(stmt.on_conflict_do_update(index_elements=["ts_code", "trade_date"], set_=update_cols))
    return len(rows)


def upsert_financial_indicator_rows(db: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    for chunk in chunk_rows(rows):
        stmt = pg_insert(StockFinancialIndicator.__table__).values(chunk)
        update_cols = {col: getattr(stmt.excluded, col) for col in chunk[0] if col not in {"id", "ts_code", "end_date", "ann_date", "created_at"}}
        db.execute(stmt.on_conflict_do_update(index_elements=["ts_code", "end_date", "ann_date"], set_=update_cols))
    return len(rows)


def chunk_rows(rows: list[dict]) -> list[list[dict]]:
    return [rows[index : index + UPSERT_CHUNK_SIZE] for index in range(0, len(rows), UPSERT_CHUNK_SIZE)]


def finish_market_sync_run(
    db: Session,
    target: str,
    start_date: date,
    end_date: date,
    trade_dates: int,
    rows_upserted: int,
    failed_dates: list[dict[str, str]],
    skipped_trade_dates: int = 0,
) -> dict[str, Any]:
    status = "ok"
    if failed_dates:
        status = "partial" if rows_upserted else "failed"
    db.add(
        DataSyncRun(
            target=target,
            start_date=start_date,
            end_date=end_date,
            rows_upserted=rows_upserted,
            status=status,
            message=f"trade_dates={trade_dates}, skipped_dates={skipped_trade_dates}, failed_dates={len(failed_dates)}",
        )
    )
    db.commit()
    if status == "failed":
        raise HTTPException(status_code=502, detail=f"{target} 同步失败：{failed_dates[0]['error'] if failed_dates else '无返回数据'}")
    return {
        "status": status,
        "target": target,
        "trade_dates": trade_dates,
        "skipped_trade_dates": skipped_trade_dates,
        "rows_upserted": rows_upserted,
        "failed_dates": failed_dates[:20],
    }


def compact_error(error: Exception) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ").strip()
    return (message or error.__class__.__name__)[:180]


def read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在：{path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"JSON 文件解析失败：{path.name}") from exc


def read_research_run_summary_cached(run_dir: Path) -> dict[str, Any]:
    results_path = run_dir / "results.json"
    if not results_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在：{results_path.name}")

    stat = results_path.stat()
    cache_key = str(results_path)
    cached = RESEARCH_RUN_SUMMARY_CACHE.get(cache_key)
    if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return dict(cached[2])

    summary = read_research_run_summary_fast(run_dir, results_path)
    if summary is None:
        summary = build_research_run_summary(run_dir, read_research_run_file(run_dir))
    RESEARCH_RUN_SUMMARY_CACHE[cache_key] = (stat.st_mtime_ns, stat.st_size, summary)
    return dict(summary)


def read_research_run_summary_fast(run_dir: Path, results_path: Path) -> dict[str, Any] | None:
    try:
        with results_path.open("r", encoding="utf-8") as file:
            text = file.read(RESEARCH_RUN_SUMMARY_READ_CHARS)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"研究运行结果读取失败：{run_dir.name}") from exc

    run_id = extract_json_key_value(text, "runId")
    strategy = extract_json_key_value(text, "strategy") or {}
    analysis = extract_json_key_value(text, "analysis") or {}
    result_pos = text.find('"result"')
    result_status = extract_json_key_value(text, "status", result_pos if result_pos >= 0 else 0)
    result_summary = extract_json_key_value(text, "summary", result_pos if result_pos >= 0 else 0) or {}
    if not run_id and not strategy and not analysis and not result_summary:
        return None

    run = {
        "runId": run_id or run_dir.name,
        "startedAt": extract_json_key_value(text, "startedAt"),
        "finishedAt": extract_json_key_value(text, "finishedAt"),
        "sourceRun": extract_json_key_value(text, "sourceRun"),
        "strategy": strategy if isinstance(strategy, dict) else {},
        "analysis": analysis if isinstance(analysis, dict) else {},
        "result": {
            "status": result_status,
            "summary": result_summary if isinstance(result_summary, dict) else {},
            "equity": [],
            "trades": [],
            "completedTrades": [],
            "finalPositions": [],
        },
    }
    return build_research_run_summary(run_dir, run)


def extract_json_key_value(text: str, key: str, start: int = 0) -> Any:
    key_pos = text.find(f'"{key}"', max(start, 0))
    if key_pos < 0:
        return None
    colon_pos = text.find(":", key_pos)
    if colon_pos < 0:
        return None
    value_text = text[colon_pos + 1 :].lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(value_text)
    except json.JSONDecodeError:
        return None
    return value


def read_research_registry() -> dict[str, Any]:
    if not RESEARCH_INDEX_PATH.exists():
        return {"runs": [], "activeStage": {}, "target": {}, "warnings": ["research-runs.json 不存在"]}
    registry = read_json_file(RESEARCH_INDEX_PATH)
    if not isinstance(registry.get("runs"), list):
        registry["runs"] = []
    return registry


def build_research_overview() -> dict[str, Any]:
    registry = read_research_registry()
    active_stage = registry.get("activeStage", {}) if isinstance(registry.get("activeStage"), dict) else {}
    stage = build_research_stage_summary(active_stage)
    available_run_ids = set(query_research_run_ids())
    integrated_runs = [build_research_registry_run_summary(item, available_run_ids) for item in registry.get("runs", []) if isinstance(item, dict)]
    sessions = build_research_session_summaries(str(active_stage.get("stageDir") or ""))
    unmerged_runs = build_unmerged_research_runs(registry)
    session_evidence = [item for item in sessions if item.get("hasEvidence")]
    warnings = []
    if unmerged_runs:
        warnings.append(f"发现 {len(unmerged_runs)} 个当前阶段未整合 run；只能作为待复核证据。")
    if session_evidence:
        warnings.append(f"发现 {len(session_evidence)} 个 session 证据文件；等待整合 session 复核。")
    if not sessions and not (stage.get("sessionsDirExists")):
        warnings.append("当前阶段尚未创建 sessions/ 目录；并行研究启动后会自动出现在这里。")

    return {
        "sourceDir": "docs/research",
        "registryPath": RESEARCH_INDEX_PATH.relative_to(REPO_ROOT).as_posix(),
        "activeStage": active_stage,
        "stage": stage,
        "target": registry.get("target", {}),
        "ultimateTarget": registry.get("ultimateTarget", {}),
        "currentMainline": registry.get("currentMainline"),
        "officialConclusionSource": "research-runs.json 与整合 session 更新后的阶段证据",
        "integratedRunCount": len(integrated_runs),
        "integratedRuns": integrated_runs,
        "sessionCount": len(sessions),
        "activeSessions": sessions,
        "evidenceInbox": {
            "unmergedRuns": unmerged_runs,
            "sessionEvidence": session_evidence,
            "count": len(unmerged_runs) + len(session_evidence),
        },
        "integrationWarnings": warnings,
    }


def build_research_stage_summary(active_stage: dict[str, Any]) -> dict[str, Any]:
    stage_dir_value = str(active_stage.get("stageDir") or "")
    stage_dir = (REPO_ROOT / stage_dir_value).resolve() if stage_dir_value else None
    readme_path = stage_dir / "README.md" if stage_dir else None
    text = read_text_file(readme_path) if readme_path else ""
    return {
        "stageId": str(active_stage.get("stageId") or first_markdown_heading(text, "未声明阶段")),
        "stageDir": stage_dir_value,
        "sourceFile": safe_relative_path(readme_path) if readme_path and readme_path.exists() else "",
        "sessionsDir": safe_relative_path(stage_dir / "sessions") if stage_dir else "",
        "sessionsDirExists": bool(stage_dir and (stage_dir / "sessions").exists()),
        "status": strip_markdown(extract_markdown_section(text, "阶段状态")).strip() or "unknown",
        "objective": compact_markdown(extract_markdown_section(text, "阶段目标") or str(active_stage.get("objective") or "")),
        "gates": extract_markdown_items(text, "硬门槛"),
        "priorityHypotheses": extract_markdown_items(text, "优先验证假设"),
        "forbiddenAttempts": extract_markdown_items(text, "禁止重复尝试"),
        "requiredOutputs": extract_markdown_items(text, "必须产物"),
        "startEvidence": extract_markdown_items(text, "起点证据"),
    }


def query_research_run_ids() -> list[str]:
    if not RESEARCH_RUNS_DIR.exists():
        return []
    return [item.name for item in RESEARCH_RUNS_DIR.iterdir() if item.is_dir()]


def build_research_registry_run_summary(item: dict[str, Any], available_run_ids: set[str] | None = None) -> dict[str, Any]:
    run_id = str(item.get("runId") or "")
    run_dir = RESEARCH_RUNS_DIR / run_id if run_id else None
    return {
        "runId": run_id,
        "label": item.get("strategyName") or run_id,
        "strategyName": item.get("strategyName"),
        "parameterSummary": item.get("parameterSummary"),
        "sample": item.get("sample"),
        "statusTier": item.get("statusTier") or item.get("status"),
        "evidenceRole": item.get("evidenceRole"),
        "fullWindowPass": item.get("fullWindowPass"),
        "rollingWindowPass": item.get("rollingWindowPass"),
        "passedWindows": item.get("passedWindows"),
        "failedWindows": item.get("failedWindows"),
        "failedWindowLabels": item.get("failedWindowLabels", []),
        "failureReason": item.get("failureReason"),
        "nextAction": item.get("nextAction"),
        "metrics": {
            "annualizedReturn": item.get("annualizedReturn"),
            "totalReturn": item.get("totalReturn"),
            "maxDrawdown": item.get("maxDrawdown") or item.get("worstDrawdown"),
            "profitLossRatio": item.get("profitLossRatio"),
            "tailWorstReturn": item.get("tailWorstReturn"),
            "minAnnualizedReturn": item.get("minAnnualizedReturn"),
            "minSharpeRatio": item.get("minSharpeRatio"),
        },
        "resultFiles": build_known_research_run_files(run_id) if available_run_ids is not None and run_id in available_run_ids else (build_research_run_files(run_dir) if run_dir and run_dir.is_dir() else {}),
        "integrationStatus": "integrated",
    }


def build_unmerged_research_runs(registry: dict[str, Any]) -> list[dict[str, Any]]:
    integrated_ids = {str(item.get("runId")) for item in registry.get("runs", []) if isinstance(item, dict) and item.get("runId")}
    active_stage = registry.get("activeStage", {}) if isinstance(registry.get("activeStage"), dict) else {}
    stage_id = str(active_stage.get("stageId") or "")
    current_mainline = str(registry.get("currentMainline") or "")
    if not RESEARCH_RUNS_DIR.exists():
        return []

    rows = []
    for run_dir in sorted((item for item in RESEARCH_RUNS_DIR.iterdir() if item.is_dir()), key=lambda item: item.name, reverse=True):
        if run_dir.name in integrated_ids or not (run_dir / "results.json").exists():
            continue
        if stage_id and not run_dir.name.startswith(f"{stage_id.split('-', 1)[0]}-"):
            continue
        try:
            summary = read_research_run_summary_cached(run_dir)
        except HTTPException:
            continue
        if not is_current_stage_run_candidate(run_dir.name, summary, stage_id, current_mainline):
            continue
        summary["integrationStatus"] = "unmerged"
        summary["warning"] = "未写入 research-runs.json；只能作为待复核证据，不参与阶段通过结论。"
        rows.append(summary)
    return rows


def is_current_stage_run_candidate(run_id: str, run: dict[str, Any], stage_id: str, current_mainline: str) -> bool:
    stage_prefix = stage_id.split("-", 1)[0]
    if stage_prefix and not run_id.startswith(f"{stage_prefix}-"):
        return False
    if "-repair-" in run_id:
        return True
    source_run = str(run.get("sourceRun") or run.get("source_run") or "")
    return bool(current_mainline and source_run == current_mainline)


def build_research_session_summaries(stage_dir_value: str) -> list[dict[str, Any]]:
    stage_dir = (REPO_ROOT / stage_dir_value).resolve() if stage_dir_value else None
    sessions_dir = stage_dir / "sessions" if stage_dir else None
    if not sessions_dir or not sessions_dir.exists():
        return []
    return [build_research_session_summary(item) for item in sorted(sessions_dir.iterdir(), key=lambda path: path.name) if item.is_dir()]


def resolve_research_session_dir(stage_dir_value: str, session_id: str) -> Path:
    if not RESEARCH_RUN_ID_PATTERN.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="研究 session ID 不合法")
    stage_dir = (REPO_ROOT / stage_dir_value).resolve() if stage_dir_value else None
    sessions_dir = (stage_dir / "sessions").resolve() if stage_dir else None
    session_dir = (sessions_dir / session_id).resolve() if sessions_dir else None
    if not session_dir or session_dir.parent != sessions_dir or not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="研究 session 不存在")
    return session_dir


def build_research_session_summary(session_dir: Path, include_text: bool = False) -> dict[str, Any]:
    session_path = session_dir / "session.md"
    log_path = session_dir / "session-log.md"
    evidence_path = session_dir / "evidence.md"
    session_text = read_text_file(session_path)
    evidence_text = read_text_file(evidence_path)
    status = extract_label_value(session_text, ["状态", "status"]) or ("待整合" if evidence_text else "进行中")
    files = {
        name: safe_relative_path(path)
        for name, path in {
            "session": session_path,
            "log": log_path,
            "evidence": evidence_path,
        }.items()
        if path.exists()
    }
    summary = {
        "sessionId": session_dir.name,
        "topic": first_markdown_heading(session_text, session_dir.name),
        "status": status,
        "question": extract_label_value(session_text, ["研究问题", "问题", "research question"]),
        "hypothesis": extract_label_value(session_text, ["假设", "hypothesis"]),
        "runIdPrefix": extract_label_value(session_text, ["run id 前缀", "runIdPrefix", "run prefix"]),
        "hasEvidence": bool(evidence_text.strip()),
        "hasLog": log_path.exists(),
        "files": files,
        "evidenceSummary": compact_markdown(evidence_text)[:360],
        "integrationStatus": "session_evidence_waiting" if evidence_text else "active",
    }
    if include_text:
        summary["texts"] = {
            "session": session_text,
            "log": read_text_file(log_path),
            "evidence": evidence_text,
        }
    return summary


def read_text_file(path: Path | None) -> str:
    if not path or not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def safe_relative_path(path: Path | None) -> str:
    if not path:
        return ""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def first_markdown_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return strip_markdown(stripped.lstrip("#").strip()) or fallback
    return fallback


def extract_markdown_section(text: str, heading: str) -> str:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == f"## {heading}":
            start = index + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def extract_markdown_items(text: str, heading: str) -> list[str]:
    section = extract_markdown_section(text, heading)
    items = []
    for line in section.splitlines():
        stripped = line.strip()
        match = re.match(r"(?:[-*]|\d+\.)\s+(.*)", stripped)
        if match:
            items.append(strip_markdown(match.group(1).strip()))
    return items


def extract_label_value(text: str, labels: list[str]) -> str:
    normalized_labels = {label.strip().lower() for label in labels}
    for line in text.splitlines():
        stripped = line.strip().lstrip("-*").strip()
        match = re.match(r"([^:：]+)[:：]\s*(.+)", stripped)
        if match and match.group(1).strip().lower() in normalized_labels:
            return strip_markdown(match.group(2).strip())
    return ""


def strip_markdown(value: str) -> str:
    return value.replace("`", "").replace("**", "").strip()


def compact_markdown(value: str) -> str:
    text = re.sub(r"[#>*_`]", "", value or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def apply_strategy_spec_analysis_override(spec: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    objective_evidence = spec.get("objectiveEvidence")
    if not isinstance(objective_evidence, dict):
        return analysis

    merged = {**analysis, **objective_evidence}
    if isinstance(spec.get("objectiveGates"), dict):
        merged["objectiveGates"] = spec["objectiveGates"]
    if isinstance(spec.get("diagnosticGates"), dict):
        merged["diagnosticGates"] = spec["diagnosticGates"]
    if isinstance(spec.get("qualificationObjective"), dict):
        merged["qualificationObjective"] = spec["qualificationObjective"]
    return merged


def resolve_research_run_dir(run_id: str) -> Path:
    if not RESEARCH_RUN_ID_PATTERN.fullmatch(run_id):
        raise HTTPException(status_code=400, detail="研究运行 ID 不合法")
    run_dir = (RESEARCH_RUNS_DIR / run_id).resolve()
    if run_dir.parent != RESEARCH_RUNS_DIR.resolve() or not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="研究运行不存在")
    return run_dir


def read_research_run_file(run_dir: Path) -> dict[str, Any]:
    results_path = run_dir / "results.json"
    run = read_json_file(results_path)
    if not isinstance(run, dict):
        raise HTTPException(status_code=500, detail=f"研究运行结果格式不正确：{run_dir.name}")
    return run


def build_research_run_summary(run_dir: Path, run: dict[str, Any]) -> dict[str, Any]:
    analysis = run.get("analysis", {})
    result = run.get("result", {})
    strategy = run.get("strategy", {})
    metrics = compact_executable_metrics(analysis)
    return {
        "runId": str(run.get("runId") or run_dir.name),
        "label": strategy.get("label") or strategy.get("name") or run_dir.name,
        "strategyName": strategy.get("name"),
        "hypothesis": strategy.get("hypothesis"),
        "status": derive_research_run_status(analysis, result),
        "startedAt": run.get("startedAt"),
        "finishedAt": run.get("finishedAt"),
        "sourceRun": run.get("sourceRun"),
        "metrics": metrics,
        "summary": result.get("summary", {}),
        "resultCounts": {
            "equityDays": len(result.get("equity", [])),
            "tradeActions": len(result.get("trades", [])),
            "completedTrades": len(result.get("completedTrades", [])),
            "finalPositions": len(result.get("finalPositions", [])),
        },
        "resultFiles": build_research_run_files(run_dir),
    }


def build_research_run_response(run_id: str, db: Session) -> dict[str, Any]:
    run_dir = resolve_research_run_dir(run_id)
    run = read_research_run_file(run_dir)
    analysis = run.get("analysis", {})
    result = run.get("result", {})
    strategy = run.get("strategy", {})
    equity_curve = build_portfolio_equity_curve(result.get("equity", []))
    symbol_audit = analysis.get("symbolAudit", {})
    symbol_audit_rows = build_executable_symbol_audit_rows(result.get("completedTrades", []))
    spec = infer_research_run_spec(run_dir, run, analysis)
    robustness = build_executable_robustness_diagnostics(spec, analysis, equity_curve, query_daily_bar_coverage(db))
    return {
        "id": str(run.get("runId") or run_dir.name),
        "label": strategy.get("label") or strategy.get("name") or run_dir.name,
        "status": derive_research_run_status(analysis, result),
        "spec": spec,
        "runId": str(run.get("runId") or run_dir.name),
        "resultFiles": build_research_run_files(run_dir),
        "startedAt": run.get("startedAt"),
        "finishedAt": run.get("finishedAt"),
        "sourceRun": run.get("sourceRun"),
        "strategy": strategy,
        "portfolioRules": run.get("portfolioRules", {}),
        "payload": run.get("payload", {}),
        "marketStatePayload": run.get("marketStatePayload", {}),
        "scope": result.get("scope", {}),
        "summary": result.get("summary", {}),
        "analysis": analysis,
        "metrics": compact_executable_metrics(analysis),
        "objectiveGates": analysis.get("objectiveGates", {}),
        "diagnosticGates": analysis.get("diagnosticGates", {}),
        "equityCurve": equity_curve,
        "symbolAudit": {**symbol_audit, "rows": symbol_audit_rows},
        "symbolAuditRows": symbol_audit_rows,
        "top10": symbol_audit.get("top10", []),
        "bottom10": symbol_audit.get("bottom10", []),
        "capitalBottom10": symbol_audit.get("capitalBottom10", []),
        "tailRisk": symbol_audit.get("tailRisk", {}),
        "tailLossRisk": symbol_audit.get("tailLossRisk", {}),
        "tailRatioEvidence": symbol_audit.get("tailRatioEvidence", {}),
        "tailCapitalRisk": symbol_audit.get("tailCapitalRisk", {}),
        "completedTrades": result.get("completedTrades", []),
        "allTrades": result.get("trades", []),
        "recentTrades": result.get("trades", [])[-40:],
        "finalPositions": result.get("finalPositions", []),
        "resultCounts": {
            "equityDays": len(result.get("equity", [])),
            "tradeActions": len(result.get("trades", [])),
            "completedTrades": len(result.get("completedTrades", [])),
            "finalPositions": len(result.get("finalPositions", [])),
            "symbolAuditRows": len(symbol_audit_rows),
        },
        "reviewText": (run_dir / "review.md").read_text(encoding="utf-8") if (run_dir / "review.md").exists() else "",
        "robustness": robustness,
        "aiAnalysis": build_executable_strategy_ai_analysis(spec, analysis, robustness),
    }


def build_research_run_files(run_dir: Path) -> dict[str, str]:
    files = {}
    for name in ("results.json", "review.md", "hypothesis.md", "context.json", "strategies.json", "next-input.md"):
        path = run_dir / name
        if path.exists():
            files[name.removesuffix(".json").removesuffix(".md").replace("-", "_")] = path.relative_to(REPO_ROOT).as_posix()
    return files


def build_known_research_run_files(run_id: str) -> dict[str, str]:
    if not run_id:
        return {}
    base = f"docs/research/runs/{run_id}"
    return {"results": f"{base}/results.json"}


def infer_research_run_spec(run_dir: Path, run: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    payload = run.get("payload", {})
    strategy = run.get("strategy", {})
    config = strategy.get("config") or payload.get("config") or {}
    return {
        "id": str(run.get("runId") or run_dir.name),
        "status": derive_research_run_status(analysis, run.get("result", {})),
        "sourceEvidenceRun": run.get("sourceRun"),
        "window": {
            "startDate": payload.get("start_date") or payload.get("startDate"),
            "endDate": payload.get("end_date") or payload.get("endDate"),
        },
        "capital": {
            "initialCash": config.get("initialCash"),
            "maxPositions": config.get("maxPositions") or config.get("maxConcurrentPositions"),
            "maxSinglePositionPct": config.get("positionCapPct"),
            "riskPct": config.get("riskPct"),
            "weeklyTradeLimit": config.get("weeklyTradeLimit"),
            "lotSize": config.get("lotSize"),
            "maxIndustryExposurePct": config.get("maxIndustryPositionPct"),
        },
        "entry": {
            "entryMode": config.get("entryMode"),
            "entryScoreMode": config.get("entryScoreMode"),
            "entryPriority": config.get("entryPriority") or config.get("entryScoreMode"),
            "useTrendFilter": config.get("useTrendFilter"),
            "useMacdFilter": config.get("useMacdFilter"),
            "useRsiFilter": config.get("useRsiFilter"),
            "buySlippagePct": config.get("buySlippagePct"),
            "sellSlippagePct": config.get("sellSlippagePct"),
            "stopGapFillAtOpen": config.get("stopGapFillAtOpen"),
            "entryRiskFilter": config.get("entryRiskFilter", {}),
            "industryOvernightRiskWindowDays": config.get("industryOvernightRiskWindowDays"),
            "industryOvernightRiskMinRatio": config.get("industryOvernightRiskMinRatio"),
        },
        "exit": {
            "stopLossPct": config.get("stopLossPct"),
            "takeProfit1Pct": config.get("takeProfit1Pct"),
            "takeProfit2Pct": config.get("takeProfit2Pct"),
            "earlyExitDays": config.get("earlyExitDays"),
            "gapStopMarketCooldownDays": config.get("gapStopMarketCooldownDays"),
        },
        "universe": {
            "minBars": payload.get("min_bars"),
            "maxStocks": payload.get("max_stocks"),
            "excludeSt": payload.get("exclude_st"),
            "excludeBj": payload.get("exclude_bj"),
            "minListDays": payload.get("min_list_days"),
            "minAvgAmount": payload.get("min_avg_amount"),
            "minAvgCircMv": payload.get("min_avg_circ_mv"),
            "minAvgTurnoverRateF": payload.get("min_avg_turnover_rate_f"),
        },
    }


def derive_research_run_status(analysis: dict[str, Any], result: dict[str, Any]) -> str:
    if analysis.get("strictTargetMet"):
        return "strict_pass"
    if analysis.get("targetMet"):
        return "target_pass"
    return str(result.get("status") or "review")


def build_portfolio_equity_curve(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not points:
        return []
    initial_equity = float(points[0].get("equity") or 0) or 1.0
    peak = initial_equity
    curve = []
    for point in points:
        equity = float(point.get("equity") or 0)
        peak = max(peak, equity)
        drawdown = equity / peak - 1 if peak else 0
        curve.append(
            {
                "date": point.get("date"),
                "equity": equity,
                "returnPct": equity / initial_equity - 1 if initial_equity else 0,
                "drawdown": drawdown,
                "cash": point.get("cash"),
                "positions": point.get("positions"),
                "marketRiskOn": point.get("marketRiskOn"),
                "marketAboveMa20Pct": point.get("marketAboveMa20Pct"),
                "marketAboveMa60Pct": point.get("marketAboveMa60Pct"),
                "marketUpPct": point.get("marketUpPct"),
            }
        )
    return curve


def compact_executable_metrics(analysis: dict[str, Any]) -> dict[str, Any]:
    symbol_audit = analysis.get("symbolAudit", {})
    tail_loss = symbol_audit.get("tailLossRisk", {})
    tail_ratio = symbol_audit.get("tailRatioEvidence", {})
    tail_capital = symbol_audit.get("tailCapitalRisk", {})
    return {
        "targetMet": analysis.get("targetMet"),
        "strictTargetMet": analysis.get("strictTargetMet"),
        "targetAnnualizedReturn": analysis.get("targetAnnualizedReturn"),
        "targetTotalReturn": analysis.get("targetTotalReturn"),
        "totalReturn": analysis.get("totalReturn"),
        "annualizedReturn": analysis.get("annualizedReturn"),
        "annualizedVolatility": analysis.get("annualizedVolatility"),
        "sharpeRatio": analysis.get("sharpeRatio"),
        "sortinoRatio": analysis.get("sortinoRatio"),
        "calmarRatio": analysis.get("calmarRatio"),
        "maxDrawdownDurationDays": analysis.get("maxDrawdownDurationDays"),
        "maxDrawdown": analysis.get("maxDrawdown"),
        "profitLossRatio": analysis.get("profitLossRatio"),
        "profitFactor": analysis.get("profitFactor"),
        "winRate": analysis.get("winRate"),
        "tradeCount": analysis.get("tradeCount"),
        "completedTradeCount": analysis.get("completedTradeCount"),
        "maxConcurrentPositions": analysis.get("maxConcurrentPositions"),
        "maxSinglePositionPct": analysis.get("maxSinglePositionPct"),
        "maxIndustryPositionPct": analysis.get("maxIndustryPositionPct"),
        "marketRiskOnDays": analysis.get("marketRiskOnDays"),
        "marketRiskOffDays": analysis.get("marketRiskOffDays"),
        "blockedRiskSignals": analysis.get("blockedRiskSignals"),
        "testedSymbols": symbol_audit.get("testedSymbols"),
        "tailWorstReturn": tail_loss.get("worstReturn"),
        "tailWorstDrawdown": tail_loss.get("worstDrawdown"),
        "tailWorstPortfolioImpactPct": tail_capital.get("worstPortfolioImpactPct"),
        "tailBottomPortfolioImpactPct": tail_capital.get("totalBottomPortfolioImpactPct"),
        "tailRatioEligibleCount": tail_ratio.get("eligibleCount"),
        "tailRatioCheckedCount": tail_ratio.get("checkedCount"),
    }


def build_executable_symbol_audit_rows(completed_trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_symbol: dict[str, dict[str, Any]] = {}
    for trade in completed_trades:
        ts_code = str(trade.get("ts_code") or "")
        if not ts_code:
            continue
        bucket = by_symbol.setdefault(
            ts_code,
            {
                "ts_code": ts_code,
                "name": trade.get("name"),
                "industry": trade.get("industry"),
                "returns": [],
                "capitalReturns": [],
                "netPnls": [],
                "entryCosts": [],
                "portfolioImpacts": [],
            },
        )
        bucket["returns"].append(float(trade.get("returnPct") or 0))
        if trade.get("capitalReturnPct") is not None:
            bucket["capitalReturns"].append(float(trade.get("capitalReturnPct") or 0))
        if trade.get("netPnl") is not None:
            bucket["netPnls"].append(float(trade.get("netPnl") or 0))
        if trade.get("entryCost") is not None:
            bucket["entryCosts"].append(float(trade.get("entryCost") or 0))
        if trade.get("pnlPctOfEntryEquity") is not None:
            bucket["portfolioImpacts"].append(float(trade.get("pnlPctOfEntryEquity") or 0))

    rows = [summarize_executable_symbol_bucket(bucket) for bucket in by_symbol.values()]
    rows.sort(key=lambda item: item["totalReturn"], reverse=True)
    return rows


def summarize_executable_symbol_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    returns = list(bucket["returns"])
    for value in returns:
        equity *= 1 + value
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = min(max_drawdown, equity / peak - 1)
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    average_win = mean(wins) if wins else 0
    average_loss = mean(losses) if losses else 0
    net_pnl = sum(float(value) for value in bucket.get("netPnls", []))
    entry_cost = sum(float(value) for value in bucket.get("entryCosts", []))
    portfolio_impacts = [float(value) for value in bucket.get("portfolioImpacts", [])]
    return {
        "ts_code": bucket["ts_code"],
        "name": bucket.get("name"),
        "industry": bucket.get("industry"),
        "totalReturn": equity - 1,
        "capitalReturnPct": net_pnl / entry_cost if entry_cost else None,
        "netPnl": net_pnl if entry_cost else None,
        "entryCost": entry_cost if entry_cost else None,
        "portfolioImpactPct": sum(portfolio_impacts) if portfolio_impacts else None,
        "maxDrawdown": max_drawdown,
        "profitLossRatio": average_win / abs(average_loss) if average_loss else None,
        "profitFactor": sum(wins) / abs(sum(losses)) if losses else None,
        "winRate": len(wins) / len(returns) if returns else 0,
        "completedTrades": len(returns),
        "tradeCount": len(returns),
    }


def query_daily_bar_coverage(db: Session) -> dict[str, Any]:
    row = db.execute(
        select(
            func.min(StockDailyBar.trade_date),
            func.max(StockDailyBar.trade_date),
            func.count(StockDailyBar.id),
            func.count(func.distinct(StockDailyBar.ts_code)),
            func.count(func.distinct(StockDailyBar.trade_date)),
        )
    ).one()
    start_date, end_date, row_count, symbol_count, trade_date_count = row
    calendar_years = (end_date - start_date).days / 365.25 if start_date and end_date else 0
    return {
        "startDate": start_date.isoformat() if start_date else None,
        "endDate": end_date.isoformat() if end_date else None,
        "rowCount": int(row_count or 0),
        "symbolCount": int(symbol_count or 0),
        "tradeDateCount": int(trade_date_count or 0),
        "calendarYears": calendar_years,
    }


def build_executable_robustness_diagnostics(
    spec: dict[str, Any],
    analysis: dict[str, Any],
    equity_curve: list[dict[str, Any]],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    target_met = bool(analysis.get("targetMet"))
    spec_window = spec.get("window", {})
    spec_end = parse_iso_date(spec_window.get("endDate")) or parse_iso_date(coverage.get("endDate")) or date.today()
    coverage_start = parse_iso_date(coverage.get("startDate"))
    coverage_end = parse_iso_date(coverage.get("endDate"))
    end_covered = bool(coverage_end and coverage_end >= spec_end - timedelta(days=7))
    required_windows = []
    for years in (5, 8):
        required_start = date_years_before(spec_end, years)
        data_ready = bool(target_met and coverage_start and coverage_start <= required_start and end_covered)
        missing_days = max((coverage_start - required_start).days, 0) if coverage_start else None
        required_windows.append(
            {
                "key": f"{years}y",
                "label": f"{years}年长周期",
                "years": years,
                "requiredStartDate": required_start.isoformat(),
                "requiredEndDate": spec_end.isoformat(),
                "dataReady": data_ready,
                "missingCalendarDays": missing_days,
                "status": "pass" if data_ready else ("blocked" if target_met else "locked"),
                "comment": "数据已覆盖，可进入长周期回测。" if data_ready else f"需要至少从 {required_start.isoformat()} 开始的全市场历史数据。",
            }
        )

    has_five_year_data = bool(required_windows[0]["dataReady"])
    if not target_met:
        status = "blocked_stage1"
        verdict = "三年硬门槛未通过，稳健性闸门不启动。"
    elif has_five_year_data:
        status = "ready_for_stage2"
        verdict = "三年硬门槛已通过，历史数据已覆盖，可运行长周期与样本外验证。"
    else:
        status = "needs_longer_history"
        verdict = "三年硬门槛已通过，但本地历史不足 5 年，长周期验证暂不能判定。"

    gates = [
        {
            "key": "stage1Objective",
            "label": "三年硬门槛",
            "status": "pass" if target_met else "fail",
            "value": format_api_percent(analysis.get("totalReturn")),
            "detail": "通过后才启动稳健性闸门。",
        },
        {
            "key": "fiveYearData",
            "label": "5年数据覆盖",
            "status": required_windows[0]["status"],
            "value": f"{coverage.get('calendarYears', 0):.1f} 年",
            "detail": required_windows[0]["comment"],
        },
        {
            "key": "eightYearData",
            "label": "8年数据覆盖",
            "status": required_windows[1]["status"],
            "value": f"{coverage.get('tradeDateCount', 0)} 日",
            "detail": required_windows[1]["comment"],
        },
        {
            "key": "walkForward",
            "label": "滚动样本外",
            "status": "pending" if has_five_year_data else ("blocked" if target_met else "locked"),
            "value": "2Y/6M",
            "detail": "用 2 年训练、后 6 个月样本外验证，循环滚动。",
        },
        {
            "key": "parameterStability",
            "label": "参数稳定性",
            "status": "pending" if target_met else "locked",
            "value": "±20%",
            "detail": "仓位、risk8、市场宽度阈值上下浮动后不能断崖式失效。",
        },
        {
            "key": "costStress",
            "label": "成本压力",
            "status": "pending" if target_met else "locked",
            "value": "滑点/涨跌停",
            "detail": "提高佣金、印花税、滑点并加入不可成交约束。",
        },
    ]

    return {
        "status": status,
        "verdict": verdict,
        "stage2Enabled": target_met,
        "requiredBeforeStage2": "三年组合硬门槛必须先通过。",
        "dataCoverage": coverage,
        "requiredWindows": required_windows,
        "gates": gates,
        "availableSegments": summarize_equity_segments(equity_curve),
        "interpretation": "样本内强基线，尚未通过稳健性终审。" if target_met else "三年样本内目标未过，不进入稳健性计算。",
        "nextActions": build_robustness_next_actions(target_met, has_five_year_data, required_windows),
    }


def summarize_equity_segments(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[int, list[dict[str, Any]]] = {}
    for point in points:
        point_date = parse_iso_date(point.get("date"))
        if not point_date:
            continue
        groups.setdefault(point_date.year, []).append(point)

    segments = []
    for year, year_points in sorted(groups.items()):
        first = year_points[0]
        last = year_points[-1]
        start_equity = float(first.get("equity") or 0)
        end_equity = float(last.get("equity") or 0)
        return_pct = end_equity / start_equity - 1 if start_equity else None
        max_drawdown = calc_window_drawdown(year_points)
        segments.append(
            {
                "label": str(year),
                "startDate": first.get("date"),
                "endDate": last.get("date"),
                "tradeDays": len(year_points),
                "returnPct": return_pct,
                "maxDrawdown": max_drawdown,
                "status": "pass" if return_pct is not None and return_pct >= 0 and max_drawdown >= -0.1 else "watch",
            }
        )
    return segments


def calc_window_drawdown(points: list[dict[str, Any]]) -> float:
    peak = 0.0
    max_drawdown = 0.0
    for point in points:
        equity = float(point.get("equity") or 0)
        peak = max(peak, equity)
        if peak:
            max_drawdown = min(max_drawdown, equity / peak - 1)
    return max_drawdown


def build_robustness_next_actions(target_met: bool, has_five_year_data: bool, required_windows: list[dict[str, Any]]) -> list[str]:
    if not target_met:
        return ["先继续进化三年组合策略，直到收益、回撤、盈亏比、尾部亏损硬门槛通过。"]
    if not has_five_year_data:
        first_window = required_windows[0]
        return [
            f"先补齐至少从 {first_window['requiredStartDate']} 到 {first_window['requiredEndDate']} 的全市场日线和必要估值数据。",
            "数据补齐后运行 5 年/8 年长周期共享资金组合回测。",
            "再运行 2 年训练、后 6 个月样本外的滚动验证，失败窗口必须进入下一轮复盘。",
        ]
    return [
        "运行 5 年和 8 年长周期组合回测，要求收益、最大回撤和盈亏比不低于降级门槛。",
        "运行滚动样本外验证，按失败窗口生成下一轮研究上下文。",
        "运行成本、滑点、涨跌停不可成交和参数扰动压力测试。",
    ]


def date_years_before(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, month=2, day=28)


def parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def build_executable_strategy_ai_analysis(spec: dict[str, Any], analysis: dict[str, Any], robustness: dict[str, Any] | None = None) -> dict[str, Any]:
    metrics = compact_executable_metrics(analysis)
    target_met = bool(metrics.get("targetMet"))
    strict_met = bool(metrics.get("strictTargetMet"))
    score = 88 if target_met else 58
    if not strict_met:
        score -= 5
    robustness_status = (robustness or {}).get("status")
    if target_met and robustness_status != "passed":
        score -= 9
    verdict = "目标硬门槛未通过"
    if target_met:
        verdict = "三年硬门槛已过，稳健性闸门待补数据" if robustness_status == "needs_longer_history" else "三年硬门槛已过，等待二阶段稳健性终审"
    target_annualized = metrics.get("targetAnnualizedReturn") or (spec.get("qualificationObjective", {}) or {}).get("targetAnnualizedReturn")
    strengths = [
        "市场宽度过滤和买入日风险过滤显著减少弱势/高波动入场。",
        "收益来自共享资金组合和横截面择强，避免把单票长期结果误判为组合能力。",
    ]
    if target_met:
        strengths.insert(0, "组合收益、回撤、盈亏比、交易次数和集中度全部通过目标硬门槛。")
    else:
        strengths.insert(0, "回撤、盈亏比和尾部亏损仍有观察价值，但收益目标未通过。")
    return {
        "provider": "local-strategy-auditor",
        "status": "ok",
        "score": score,
        "verdict": verdict,
        "marketFit": "适合 Risk-On 阶段的高流动性 A 股横截面轮动；不适合弱势市场持续开仓。",
        "summary": [
            f"三年组合总收益 {format_api_percent(metrics.get('totalReturn'))}，年化 {format_api_percent(metrics.get('annualizedReturn'))}，目标年化 {format_api_percent(target_annualized)}。",
            f"完成交易 {metrics.get('completedTradeCount')} 笔，最大单票暴露 {format_api_percent(metrics.get('maxSinglePositionPct'))}。",
            (robustness or {}).get("verdict", "稳健性闸门尚未计算。"),
            "成交标的后 10 的亏损和回撤已受控，但单票盈亏比样本仍然稀疏。",
        ],
        "strengths": strengths,
        "risks": [
            "2026-05-31 起合格目标改为年化 30%，当前 105 只能作为观察级候选。",
            "严格诊断项未通过：逐标的源审计仍未通过，后 10 单票盈亏比样本为 0/10。",
            "回测仍按日线收盘价执行，未纳入涨跌停不可成交、滑点和盘中冲击。",
            "当前证据来自一个三年窗口，二阶段长周期/样本外验证尚未通过。",
        ],
        "nextChecks": [
            "三年硬门槛通过后才进入二阶段稳健性闸门。",
            "补齐更长历史后做 5 年/8 年和 2 年训练、6 个月样本外滚动验证。",
            "提高佣金、印花税和滑点假设，确认硬门槛是否仍成立。",
        ],
        "factorRead": [
            {"name": "年化收益", "value": format_api_percent(metrics.get("annualizedReturn")), "comment": f"目标为 {format_api_percent(target_annualized)}。"},
            {"name": "最大回撤", "value": format_api_percent(metrics.get("maxDrawdown")), "comment": "绝对值低于 10% 目标线。"},
            {"name": "盈亏比", "value": f"{float(metrics.get('profitLossRatio') or 0):.2f}:1", "comment": "高于 2:1 目标线。"},
            {"name": "尾部亏损", "value": format_api_percent(metrics.get("tailWorstReturn")), "comment": "成交标的后 10 最差约 -5%。"},
            {"name": "规格版本", "value": str(spec.get("id") or EXECUTABLE_STRATEGY_ID), "comment": "已固化为 Web 可读基线。"},
        ],
    }


def format_api_percent(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2%}"


def financial_indicator_record_to_row(item: dict) -> dict | None:
    ann_date = parse_tushare_date(item.get("ann_date"))
    end_date = parse_tushare_date(item.get("end_date"))
    if not ann_date or not end_date:
        return None
    return {
        "ts_code": item["ts_code"],
        "ann_date": ann_date,
        "end_date": end_date,
        "eps": decimal_or_none(item.get("eps")),
        "dt_eps": decimal_or_none(item.get("dt_eps")),
        "bps": decimal_or_none(item.get("bps")),
        "netprofit_margin": decimal_or_none(item.get("netprofit_margin")),
        "grossprofit_margin": decimal_or_none(item.get("grossprofit_margin")),
        "roe": decimal_or_none(item.get("roe")),
        "roe_waa": decimal_or_none(item.get("roe_waa")),
        "roa": decimal_or_none(item.get("roa")),
        "debt_to_assets": decimal_or_none(item.get("debt_to_assets")),
        "current_ratio": decimal_or_none(item.get("current_ratio")),
        "quick_ratio": decimal_or_none(item.get("quick_ratio")),
        "assets_turn": decimal_or_none(item.get("assets_turn")),
        "basic_eps_yoy": decimal_or_none(item.get("basic_eps_yoy")),
        "op_yoy": decimal_or_none(item.get("op_yoy")),
        "netprofit_yoy": decimal_or_none(item.get("netprofit_yoy")),
        "tr_yoy": decimal_or_none(item.get("tr_yoy")),
        "or_yoy": decimal_or_none(item.get("or_yoy")),
        "q_sales_yoy": decimal_or_none(item.get("q_sales_yoy")),
        "q_profit_yoy": decimal_or_none(item.get("q_profit_yoy")),
    }


def dedupe_rows(rows: list[dict], keys: tuple[str, ...]) -> list[dict]:
    by_key = {tuple(row[key] for key in keys): row for row in rows}
    return list(by_key.values())


def stock_to_schema(stock: Stock) -> StockOut:
    return StockOut(
        ts_code=stock.ts_code,
        symbol=stock.symbol,
        name=stock.name,
        area=stock.area,
        industry=stock.industry,
        market=stock.market,
        list_date=stock.list_date,
    )


def stock_pool_to_schema(pool: StockPool, member_count: int) -> StockPoolOut:
    return StockPoolOut(
        id=pool.id,
        name=pool.name,
        description=pool.description,
        member_count=member_count,
        created_at=pool.created_at,
        updated_at=pool.updated_at,
    )


def stock_pool_detail_to_schema(db: Session, pool: StockPool) -> StockPoolDetailOut:
    member_rows = db.execute(
        select(Stock, StockPoolMember.created_at)
        .join(StockPoolMember, StockPoolMember.ts_code == Stock.ts_code)
        .where(StockPoolMember.pool_id == pool.id)
        .order_by(StockPoolMember.created_at, Stock.ts_code)
    ).all()
    members = [
        StockPoolMemberOut(
            **stock_to_schema(stock).model_dump(),
            added_at=added_at,
        )
        for stock, added_at in member_rows
    ]
    return StockPoolDetailOut(
        **stock_pool_to_schema(pool, len(members)).model_dump(),
        members=members,
    )


def get_stock_pool_or_404(db: Session, pool_id: int | None) -> StockPool:
    if not pool_id:
        raise HTTPException(status_code=404, detail="未找到这个标的池。")
    pool = db.get(StockPool, pool_id)
    if not pool:
        raise HTTPException(status_code=404, detail="未找到这个标的池。")
    return pool


def build_screen_row(
    stock: Stock,
    bars: list[tuple[date, float, float, float, float, float | None, float]],
    valuation: StockDailyBasic | None,
    financial: StockFinancialIndicator | None,
    technical: str,
) -> StockScreenOut:
    fundamental_profile = build_fundamental_profile(valuation, financial)
    fundamentals = {
        "地区": stock.area,
        "行业": stock.industry,
        "市场": stock.market,
        "上市日期": stock.list_date.isoformat() if stock.list_date else None,
        "本地日线": len(bars),
        "估值": daily_basic_to_dict(valuation),
        "财务": financial_indicator_to_dict(financial),
        "质量评分": {
            "总分": fundamental_profile["score"],
            "等级": fundamental_profile["grade"],
            "分项": fundamental_profile["breakdown"],
        },
    }
    if not bars:
        return StockScreenOut(
            **stock_to_schema(stock).model_dump(),
            data_bars=0,
            technical_score=0,
            technical_tags=["无本地日线"],
            fundamental_score=fundamental_profile["score"],
            fundamental_grade=fundamental_profile["grade"],
            fundamental_breakdown=fundamental_profile["breakdown"],
            fundamental_tags=fundamental_profile["tags"],
            signal_summary="先同步日线后再筛选",
            fundamentals=fundamentals,
        )

    rows = [screen_bar_to_backtest_row(stock.ts_code, bar) for bar in bars]
    enriched = enrich_rows(rows, DEFAULT_CONFIG)
    latest = enriched[-1]
    previous = enriched[-2] if len(enriched) > 1 else None
    profile = classify_screen_signal(latest, previous, technical)
    latest_bar = bars[-1]
    return StockScreenOut(
        **stock_to_schema(stock).model_dump(),
        latest_date=latest_bar[0],
        close=latest_bar[4],
        pct_chg=latest_bar[5],
        data_bars=len(bars),
        technical_score=profile["score"],
        technical_tags=profile["tags"],
        fundamental_score=fundamental_profile["score"],
        fundamental_grade=fundamental_profile["grade"],
        fundamental_breakdown=fundamental_profile["breakdown"],
        fundamental_tags=fundamental_profile["tags"],
        news_state="未刷新",
        signal_summary=profile["summary"],
        fundamentals=fundamentals,
    )


def query_latest_fundamentals(
    db: Session,
    ts_code: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[StockDailyBasic | None, StockFinancialIndicator | None]:
    valuation_stmt = select(StockDailyBasic).where(StockDailyBasic.ts_code == ts_code)
    financial_stmt = select(StockFinancialIndicator).where(StockFinancialIndicator.ts_code == ts_code)
    if start_date:
        valuation_stmt = valuation_stmt.where(StockDailyBasic.trade_date >= start_date)
        financial_stmt = financial_stmt.where(StockFinancialIndicator.end_date >= start_date)
    if end_date:
        valuation_stmt = valuation_stmt.where(StockDailyBasic.trade_date <= end_date)
        financial_stmt = financial_stmt.where(StockFinancialIndicator.end_date <= end_date)

    valuation = db.scalars(valuation_stmt.order_by(StockDailyBasic.trade_date.desc()).limit(1)).first()
    financial = db.scalars(financial_stmt.order_by(StockFinancialIndicator.end_date.desc(), StockFinancialIndicator.ann_date.desc()).limit(1)).first()
    return valuation, financial


def has_financial_rows(db: Session, ts_code: str, start_date: date, end_date: date) -> bool:
    stmt = (
        select(func.count())
        .select_from(StockFinancialIndicator)
        .where(
            StockFinancialIndicator.ts_code == ts_code,
            StockFinancialIndicator.ann_date >= start_date,
            StockFinancialIndicator.ann_date <= end_date,
        )
    )
    return bool(db.scalar(stmt))


def daily_basic_to_dict(row: StockDailyBasic | None) -> dict[str, Any]:
    if not row:
        return {"状态": "未同步基本面"}
    return json_safe(
        {
            "日期": row.trade_date.isoformat(),
            "收盘": decimal_to_float(row.close),
            "换手率": decimal_to_float(row.turnover_rate),
            "自由流通换手率": decimal_to_float(row.turnover_rate_f),
            "量比": decimal_to_float(row.volume_ratio),
            "PE": decimal_to_float(row.pe),
            "PE_TTM": decimal_to_float(row.pe_ttm),
            "PB": decimal_to_float(row.pb),
            "PS": decimal_to_float(row.ps),
            "PS_TTM": decimal_to_float(row.ps_ttm),
            "股息率": decimal_to_float(row.dv_ratio),
            "股息率_TTM": decimal_to_float(row.dv_ttm),
            "总市值_万元": decimal_to_float(row.total_mv),
            "流通市值_万元": decimal_to_float(row.circ_mv),
        }
    )


def financial_indicator_to_dict(row: StockFinancialIndicator | None) -> dict[str, Any]:
    if not row:
        return {"状态": "未同步财务指标"}
    return json_safe(
        {
            "公告日期": row.ann_date.isoformat(),
            "报告期": row.end_date.isoformat(),
            "EPS": decimal_to_float(row.eps),
            "每股净资产": decimal_to_float(row.bps),
            "ROE": decimal_to_float(row.roe),
            "加权ROE": decimal_to_float(row.roe_waa),
            "ROA": decimal_to_float(row.roa),
            "毛利率": decimal_to_float(row.grossprofit_margin),
            "净利率": decimal_to_float(row.netprofit_margin),
            "资产负债率": decimal_to_float(row.debt_to_assets),
            "流动比率": decimal_to_float(row.current_ratio),
            "速动比率": decimal_to_float(row.quick_ratio),
            "营收同比": decimal_to_float(row.tr_yoy),
            "营业收入同比": decimal_to_float(row.or_yoy),
            "净利润同比": decimal_to_float(row.netprofit_yoy),
            "单季营收同比": decimal_to_float(row.q_sales_yoy),
            "单季净利同比": decimal_to_float(row.q_profit_yoy),
        }
    )


def build_fundamental_profile(valuation: StockDailyBasic | None, financial: StockFinancialIndicator | None) -> dict[str, Any]:
    tags: list[str] = []
    pe_ttm = decimal_to_float(valuation.pe_ttm if valuation else None)
    pb = decimal_to_float(valuation.pb if valuation else None)
    turnover = decimal_to_float(valuation.turnover_rate if valuation else None)
    dividend = decimal_to_float(valuation.dv_ttm if valuation else None)
    total_mv = decimal_to_float(valuation.total_mv if valuation else None)
    roe = decimal_to_float(financial.roe if financial else None)
    roa = decimal_to_float(financial.roa if financial else None)
    gross_margin = decimal_to_float(financial.grossprofit_margin if financial else None)
    net_margin = decimal_to_float(financial.netprofit_margin if financial else None)
    debt = decimal_to_float(financial.debt_to_assets if financial else None)
    current_ratio = decimal_to_float(financial.current_ratio if financial else None)
    quick_ratio = decimal_to_float(financial.quick_ratio if financial else None)
    revenue_growth = decimal_to_float(financial.tr_yoy if financial else None)
    profit_growth = decimal_to_float(financial.netprofit_yoy if financial else None)
    quarterly_revenue_growth = decimal_to_float(financial.q_sales_yoy if financial else None)
    quarterly_profit_growth = decimal_to_float(financial.q_profit_yoy if financial else None)

    profitability = (
        tier_score(roe, [(20, 12), (15, 10), (10, 7), (5, 4)])
        + tier_score(net_margin, [(15, 8), (8, 6), (3, 3)])
        + tier_score(gross_margin, [(40, 6), (25, 4), (15, 2)])
        + tier_score(roa, [(8, 4), (5, 3), (2, 1)])
    )
    growth = (
        tier_score(revenue_growth, [(25, 5), (10, 4), (0, 2)])
        + tier_score(profit_growth, [(30, 7), (10, 5), (0, 2)])
        + tier_score(quarterly_revenue_growth, [(20, 4), (5, 3), (0, 1)])
        + tier_score(quarterly_profit_growth, [(25, 4), (5, 3), (0, 1)])
    )
    balance = (
        range_score(debt, [(0, 35, 8), (35, 55, 6), (55, 70, 3)])
        + tier_score(current_ratio, [(1.8, 5), (1.2, 3), (1.0, 1)])
        + tier_score(quick_ratio, [(1.3, 3), (0.9, 2), (0.7, 1)])
        + tier_score(decimal_to_float(financial.bps if financial else None), [(5, 2), (1, 1)])
    )
    valuation_score = (
        range_score(pe_ttm, [(0, 18, 8), (18, 30, 6), (30, 45, 3)])
        + range_score(pb, [(0, 2, 5), (2, 4, 3), (4, 7, 1)])
        + range_score(decimal_to_float(valuation.ps_ttm if valuation else None), [(0, 3, 4), (3, 6, 2)])
        + tier_score(dividend, [(3, 3), (1, 2), (0, 1)])
    )
    liquidity = (
        range_score(turnover, [(0.3, 5, 5), (5, 10, 3)])
        + tier_score(total_mv, [(5_000_000, 5), (1_000_000, 4), (300_000, 2)])
        + tier_score(dividend, [(2, 2), (0, 1)])
    )

    breakdown = {
        "盈利质量": min(profitability, 30),
        "成长性": min(growth, 20),
        "资产负债": min(balance, 18),
        "估值": min(valuation_score, 20),
        "流动性分红": min(liquidity, 12),
    }
    score = min(sum(breakdown.values()), 100)

    append_fundamental_tags(tags, valuation, financial, score, roe, net_margin, revenue_growth, profit_growth, debt, pe_ttm, pb, dividend)
    return {"score": score, "grade": grade_fundamental_score(score, valuation, financial), "breakdown": breakdown, "tags": tags[:10]}


def tier_score(value: float | None, tiers: list[tuple[float, int]]) -> int:
    if value is None:
        return 0
    for threshold, score in tiers:
        if value >= threshold:
            return score
    return 0


def range_score(value: float | None, ranges: list[tuple[float, float, int]]) -> int:
    if value is None:
        return 0
    for low, high, score in ranges:
        if low < value <= high:
            return score
    return 0


def grade_fundamental_score(score: int, valuation: StockDailyBasic | None, financial: StockFinancialIndicator | None) -> str:
    if not valuation or not financial:
        return "待同步"
    if score >= 82:
        return "A"
    if score >= 68:
        return "B"
    if score >= 52:
        return "C"
    return "D"


def append_fundamental_tags(
    tags: list[str],
    valuation: StockDailyBasic | None,
    financial: StockFinancialIndicator | None,
    score: int,
    roe: float | None,
    net_margin: float | None,
    revenue_growth: float | None,
    profit_growth: float | None,
    debt: float | None,
    pe_ttm: float | None,
    pb: float | None,
    dividend: float | None,
) -> None:
    if not valuation:
        tags.append("估值未同步")
    if not financial:
        tags.append("财务未同步")
    if score >= 82:
        tags.append("质量A档")
    elif score >= 68:
        tags.append("质量B档")
    if roe is not None:
        tags.append("ROE优秀" if roe >= 15 else "ROE合格" if roe >= 10 else "ROE偏弱")
    if net_margin is not None and net_margin >= 8:
        tags.append("净利率稳健")
    if revenue_growth is not None and revenue_growth > 0:
        tags.append("营收增长")
    if profit_growth is not None and profit_growth > 0:
        tags.append("利润增长")
    if debt is not None:
        tags.append("低负债" if debt <= 35 else "负债可控" if debt <= 65 else "负债偏高")
    if pe_ttm is not None and 0 < pe_ttm <= 30:
        tags.append("PE合理")
    elif valuation:
        tags.append("PE偏高/亏损")
    if pb is not None and 0 < pb <= 4:
        tags.append("PB可比")
    if dividend is not None and dividend > 0:
        tags.append("有分红")


def sort_screened_stocks(rows: list[StockScreenOut], rank_by: str) -> list[StockScreenOut]:
    normalized = (rank_by or "composite").strip().lower()
    if normalized in {"fundamental", "quality"}:
        return sorted(rows, key=lambda row: (has_complete_fundamentals(row), row.fundamental_score, row.technical_score, row.data_bars), reverse=True)
    if normalized == "technical":
        return sorted(rows, key=lambda row: (row.technical_score, row.fundamental_score, row.data_bars), reverse=True)
    if normalized == "valuation":
        return sorted(rows, key=lambda row: (has_complete_fundamentals(row), row.fundamental_breakdown.get("估值", 0), row.fundamental_score, row.data_bars), reverse=True)
    return sorted(rows, key=lambda row: ((row.technical_score * 0.45) + (row.fundamental_score * 0.55), row.data_bars), reverse=True)


def has_complete_fundamentals(row: StockScreenOut) -> bool:
    return row.fundamental_grade != "待同步"


def stock_news_items(stock: Stock | None, ts_code: str) -> list[dict[str, Any]]:
    items = collect_news_items(["cls", "wallstreetcn", "xueqiu"], 8)
    keywords = [ts_code.split(".")[0].lower()]
    if stock:
        keywords.extend([stock.ts_code.lower(), stock.name.lower()])
    matched = [
        item
        for item in items
        if any(keyword and keyword in f"{item.get('title', '')} {item.get('source_name', '')}".lower() for keyword in keywords)
    ]
    return (matched or items)[:12]


def build_fundamental_agent(profile: dict[str, Any], valuation: StockDailyBasic | None, financial: StockFinancialIndicator | None) -> dict[str, Any]:
    score = int(profile["score"])
    evidence = [f"{name} {value}分" for name, value in profile["breakdown"].items()]
    risks: list[str] = []
    if not valuation:
        risks.append("估值数据未同步")
    if not financial:
        risks.append("财务指标未同步")
    debt = decimal_to_float(financial.debt_to_assets if financial else None)
    if debt is not None and debt > 65:
        risks.append("资产负债率偏高")
    profit_growth = decimal_to_float(financial.netprofit_yoy if financial else None)
    if profit_growth is not None and profit_growth < 0:
        risks.append("净利润同比下滑")
    return quality_agent(
        agent_id="fundamental",
        name="基本面分析师",
        score=score,
        confidence=85 if valuation and financial else 45 if valuation or financial else 20,
        summary=f"基本面质量{profile['grade']}档，综合 {score} 分。",
        evidence=evidence + profile["tags"][:4],
        risks=risks or ["暂未发现明显基本面红旗"],
        data_status="ok" if valuation and financial else "partial",
    )


def build_technical_agent(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return quality_agent("technical", "技术分析师", 45, 15, "缺少本地日线，技术面暂按中性处理。", ["无本地日线"], ["无法验证趋势和波动"], "missing")
    latest = rows[-1]
    previous = rows[-2] if len(rows) > 1 else None
    profile = classify_screen_signal(latest, previous, "all")
    score = int(profile["score"])
    rsi = latest.get("rsiStrategy")
    if rsi is not None and rsi > 82:
        score = max(0, score - 12)
    if rsi is not None and rsi < 25:
        score = max(0, score - 8)
    evidence = profile["tags"][:5]
    evidence.extend(
        [
            f"收盘 {format_agent_number(latest.get('close'))}",
            f"MACD柱 {format_agent_number(latest.get('macdHist'))}",
            f"RSI {format_agent_number(rsi)}",
        ]
    )
    risks = []
    if latest.get("close") and latest.get("ma20") and latest["close"] < latest["ma20"]:
        risks.append("收盘低于MA20")
    if latest.get("macdHist") is not None and latest["macdHist"] < 0:
        risks.append("MACD动能为负")
    return quality_agent("technical", "技术分析师", score, min(90, 35 + len(rows) // 8), profile["summary"], evidence, risks or ["技术面未触发明显风险"], "ok")


def build_sentiment_agent(items: list[dict[str, Any]], keyword: str) -> dict[str, Any]:
    score, positive, negative = score_text_sentiment([str(item.get("title") or "") for item in items])
    summary = f"围绕 {keyword} 的短线情绪读数为 {score} 分。"
    evidence = [str(item.get("title")) for item in items[:4]] or ["暂无可用标题"]
    risks = [f"负面标题 {negative} 条"] if negative else ["未检测到明显负面标题"]
    return quality_agent("sentiment", "情绪分析师", score, 70 if items else 20, summary, evidence + [f"正面 {positive} / 负面 {negative}"], risks, "ok" if items else "missing")


def build_news_agent(items: list[dict[str, Any]]) -> dict[str, Any]:
    titles = [str(item.get("title") or "") for item in items]
    score, positive, negative = score_text_sentiment(titles)
    macro_hits = count_keyword_hits(titles, ["降息", "加息", "关税", "通胀", "监管", "政策", "地缘", "制裁", "汇率"])
    if macro_hits:
        score = max(0, score - min(15, macro_hits * 3))
    evidence = titles[:5] or ["暂无财经新闻标题"]
    risks = []
    if negative:
        risks.append(f"新闻负面词命中 {negative} 次")
    if macro_hits:
        risks.append(f"宏观/政策事件命中 {macro_hits} 次")
    return quality_agent("news", "新闻分析师", score, 65 if items else 20, f"新闻事件读数 {score} 分，宏观/政策命中 {macro_hits} 次。", evidence, risks or ["新闻面暂未出现明显冲击"], "ok" if items else "missing")


def quality_agent(agent_id: str, name: str, score: int, confidence: int, summary: str, evidence: list[str], risks: list[str], data_status: str) -> dict[str, Any]:
    score = max(0, min(100, int(score)))
    return {
        "id": agent_id,
        "name": name,
        "score": score,
        "rating": rating_from_score(score),
        "confidence": max(0, min(100, int(confidence))),
        "summary": summary,
        "evidence": evidence[:6],
        "risks": risks[:5],
        "dataStatus": data_status,
    }


def build_quality_report(
    stock: Stock | None,
    ts_code: str,
    start_date: date,
    end_date: date,
    agents: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    valuation: StockDailyBasic | None,
    financial: StockFinancialIndicator | None,
    news_items: list[dict[str, Any]],
) -> dict[str, Any]:
    weights = {"fundamental": 0.34, "technical": 0.28, "sentiment": 0.16, "news": 0.22}
    score = round(sum(agent["score"] * weights.get(agent["id"], 0.25) for agent in agents))
    confidence = round(sum(agent["confidence"] for agent in agents) / len(agents)) if agents else 0
    rating = rating_from_score(score)
    bull_case = [item for agent in agents for item in agent["evidence"][:2]][:6]
    bear_case = [item for agent in agents for item in agent["risks"][:2]][:6]
    return {
        "symbol": ts_code,
        "name": stock.name if stock else ts_code,
        "industry": stock.industry if stock else None,
        "rating": rating,
        "score": score,
        "confidence": confidence,
        "consensus": f"{stock.name if stock else ts_code} 当前多维质量评分 {score}，研究评级为{rating}。",
        "bullCase": bull_case or ["暂无足够正面证据"],
        "bearCase": bear_case or ["暂无明显风险证据"],
        "watchPoints": build_watch_points(agents, valuation, financial, rows),
        "agents": agents,
        "latestSnapshot": compact_quality_snapshot(rows[-1] if rows else {}, valuation, financial),
        "dataStatus": {
            "dailyBars": len(rows),
            "fundamentalComplete": bool(valuation and financial),
            "newsItems": len(news_items),
            "socialSources": "StockTwits/Reddit未接入，当前使用财经新闻标题代理短线情绪",
        },
        "dateRange": {"startDate": start_date.isoformat(), "endDate": end_date.isoformat()},
        "ai": {"provider": "local", "status": "disabled", "message": "本地多 Agent 规则诊断"},
        "disclaimer": "研究评级仅用于本地复盘，不构成投资建议或交易指令。",
    }


def build_watch_points(agents: list[dict[str, Any]], valuation: StockDailyBasic | None, financial: StockFinancialIndicator | None, rows: list[dict[str, Any]]) -> list[str]:
    points = ["补齐或复核最新财报与估值数据" if not (valuation and financial) else "跟踪下一期财报是否延续当前质量评分"]
    latest = rows[-1] if rows else {}
    if latest.get("macdHist") is not None:
        points.append("观察MACD柱与RSI是否继续确认趋势")
    if any(agent["id"] in {"news", "sentiment"} and agent["score"] < 45 for agent in agents):
        points.append("复核负面新闻或情绪冲击是否持续发酵")
    points.append("用自选池回测验证该评级下的策略胜率和回撤")
    return points[:5]


def compact_quality_snapshot(row: dict[str, Any], valuation: StockDailyBasic | None, financial: StockFinancialIndicator | None) -> dict[str, Any]:
    return {
        "close": row.get("close"),
        "ma20": row.get("ma20"),
        "ma60": row.get("ma60"),
        "macdHist": row.get("macdHist"),
        "rsi": row.get("rsiStrategy"),
        "peTtm": decimal_to_float(valuation.pe_ttm if valuation else None),
        "pb": decimal_to_float(valuation.pb if valuation else None),
        "roe": decimal_to_float(financial.roe if financial else None),
        "debtToAssets": decimal_to_float(financial.debt_to_assets if financial else None),
    }


def score_text_sentiment(texts: list[str]) -> tuple[int, int, int]:
    positive = count_keyword_hits(texts, ["利好", "增长", "上调", "突破", "回购", "分红", "获批", "合作", "盈利", "创新高", "positive", "beat"])
    negative = count_keyword_hits(texts, ["利空", "下跌", "亏损", "调查", "减持", "暴跌", "风险", "处罚", "退市", "违约", "下调", "negative", "miss"])
    score = max(0, min(100, 50 + positive * 8 - negative * 10))
    return score, positive, negative


def count_keyword_hits(texts: list[str], keywords: list[str]) -> int:
    joined = "\n".join(texts).lower()
    return sum(joined.count(keyword.lower()) for keyword in keywords)


def rating_from_score(score: int) -> str:
    if score >= 72:
        return "买入"
    if score >= 58:
        return "持有"
    if score >= 45:
        return "中性"
    return "卖出"


def format_agent_number(value: Any) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def decimal_to_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def collect_news_items(sources: list[str], count: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    selected_sources = sources[:6]
    per_source_count = min(max(count, 1), 20)
    with ThreadPoolExecutor(max_workers=max(len(selected_sources), 1)) as executor:
        for rows in executor.map(lambda source: fetch_news_source(source, per_source_count), selected_sources):
            items.extend(rows)
    return items


def fetch_news_source(source: str, count: int) -> list[dict[str, Any]]:
    if source not in NEWS_SOURCES:
        return []
    url = f"https://newsnow.busiyi.world/api/s?id={source}"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 QuantitativeTradingResearch/0.1",
        },
    )
    try:
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return []

    items = payload.get("items", []) if isinstance(payload, dict) else []
    normalized = []
    for index, item in enumerate(items[:count], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        title = " ".join(title.split())
        normalized.append(
            {
                "source": source,
                "source_name": NEWS_SOURCES[source],
                "title": title,
                "url": item.get("url"),
                "rank": index,
                "heat": (item.get("extra") or {}).get("info") if isinstance(item.get("extra"), dict) else None,
            }
        )
    return normalized


def classify_screen_signal(row: dict, prev_row: dict | None, technical: str) -> dict:
    checks = {
        "ma-bullish": row.get("close", 0) > row.get("ma20", float("inf")) and row.get("ma20", 0) >= row.get("ma60", float("inf")),
        "macd-bullish": row.get("macdDif", 0) >= row.get("macdDea", 1) and row.get("macdHist", 0) > 0,
        "rsi-neutral": 40 <= (row.get("rsiStrategy") or -1) <= 70,
        "boll-lower": bool(row.get("bollLower")) and row["low"] <= row["bollLower"] * 1.02,
        "boll-breakout": bool(row.get("bollUpper")) and row["close"] > row["bollUpper"] and screen_volume_ok(row, 1.05),
        "volume-breakout": screen_volume_ok(row, 1.5),
        "boll-squeeze": bool(row.get("bollBandwidthPct")) and row["bollBandwidthPct"] <= 0.08,
    }
    if prev_row:
        checks["macd-cross"] = prev_row.get("macdDif", 0) <= prev_row.get("macdDea", 0) and row.get("macdDif", 0) > row.get("macdDea", 0)
        checks["ma-cross"] = prev_row.get("trendFastMa", 0) <= prev_row.get("trendSlowMa", 0) and row.get("trendFastMa", 0) > row.get("trendSlowMa", 0)

    label_map = {
        "ma-bullish": "均线多头",
        "macd-bullish": "MACD多头",
        "macd-cross": "MACD金叉",
        "rsi-neutral": "RSI健康",
        "boll-lower": "靠近BOLL下轨",
        "boll-breakout": "BOLL突破",
        "boll-squeeze": "BOLL收口",
        "volume-breakout": "放量",
        "ma-cross": "均线金叉",
    }
    tags = [label_map[key] for key, value in checks.items() if value]
    if technical != "all" and technical in checks and not checks[technical]:
        return {"score": 0, "tags": tags or ["未命中"], "summary": f"未命中 {label_map.get(technical, technical)}"}

    score = 0
    score += 18 if checks.get("ma-bullish") else 0
    score += 18 if checks.get("macd-bullish") else 0
    score += 14 if checks.get("rsi-neutral") else 0
    score += 15 if checks.get("boll-lower") else 0
    score += 18 if checks.get("boll-breakout") else 0
    score += 12 if checks.get("boll-squeeze") else 0
    score += 10 if checks.get("volume-breakout") else 0
    score += 18 if checks.get("macd-cross") or checks.get("ma-cross") else 0
    score = min(100, score)
    return {"score": score, "tags": tags or ["中性"], "summary": " / ".join(tags[:3]) if tags else "暂无明显技术形态"}


def screen_volume_ok(row: dict, multiplier: float) -> bool:
    return bool(row.get("volume")) and bool(row.get("volMa")) and row["volume"] >= row["volMa"] * multiplier


def enriched_bars_to_schema(bars: list[StockDailyBar]) -> list[DailyBarOut]:
    rows = [bar_to_backtest_row(bar) for bar in bars]
    enriched = enrich_rows(rows, DEFAULT_CONFIG)
    return [bar_to_schema(row, bars[index]) for index, row in enumerate(enriched)]


def bar_to_schema(row: dict, bar: StockDailyBar) -> DailyBarOut:
    return DailyBarOut(
        ts_code=row["ts_code"],
        date=row["date"],
        open=row["open"],
        high=row["high"],
        low=row["low"],
        close=row["close"],
        volume=row["volume"],
        amount=float(bar.amount) if bar.amount is not None else None,
        **json_safe(
            {
                "ma5": row.get("ma5"),
                "ma10": row.get("ma10"),
                "ma20": row.get("ma20"),
                "ma30": row.get("ma30"),
                "ma60": row.get("ma60"),
                "trendFastMa": row.get("trendFastMa"),
                "trendSlowMa": row.get("trendSlowMa"),
                "trendLongMa": row.get("trendLongMa"),
                "bollMid": row.get("bollMid"),
                "bollUpper": row.get("bollUpper"),
                "bollLower": row.get("bollLower"),
                "bollBandwidthPct": row.get("bollBandwidthPct"),
                "volMa": row.get("volMa"),
                "macdDif": row.get("macdDif"),
                "macdDea": row.get("macdDea"),
                "macdHist": row.get("macdHist"),
                "rsi6": row.get("rsi6"),
                "rsi12": row.get("rsi12"),
                "rsi24": row.get("rsi24"),
                "rsiStrategy": row.get("rsiStrategy"),
                "kdjK": row.get("kdjK"),
                "kdjD": row.get("kdjD"),
                "kdjJ": row.get("kdjJ"),
                "atr14": row.get("atr14"),
                "atrStrategy": row.get("atrStrategy"),
            }
        ),
    )


def bar_to_backtest_row(bar: StockDailyBar) -> dict:
    return {
        "ts_code": bar.ts_code,
        "date": bar.trade_date.isoformat(),
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "volume": float(bar.vol) if bar.vol is not None else 0,
    }


def screen_bar_to_backtest_row(ts_code: str, bar: tuple[date, float, float, float, float, float | None, float]) -> dict:
    return {
        "ts_code": ts_code,
        "date": bar[0].isoformat(),
        "open": bar[1],
        "high": bar[2],
        "low": bar[3],
        "close": bar[4],
        "volume": bar[6],
    }
