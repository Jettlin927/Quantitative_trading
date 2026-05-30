from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from math import floor
from pathlib import Path
from statistics import mean
from typing import Any

from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import calc_equity_performance_stats, calc_stop_price, enrich_rows, finite, json_safe, round_to_lot, should_enter
from backend.app.database import SessionLocal
from backend.app.main import query_backtest_rows_by_code, query_backtest_stocks, stock_to_market_meta
from backend.app.schemas import MarketBacktestRequest
from scripts.research.run_research_round import DEFAULT_CONTEXT_PATH, NEXT_BRIEF_PATH, RUNS_ROOT, build_market_payload, build_strategy, format_optional_percent, format_optional_ratio, now_text, read_json, summarize_tail_risk, write_json, write_text


CN_TZ = timezone(timedelta(hours=8))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a shared-capital portfolio diagnostic backtest.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--strategy", default="trend-follow-maximum-profit-no-macd", help="Strategy preset from run_research_round.py.")
    parser.add_argument("--context", default=str(DEFAULT_CONTEXT_PATH), help="Research context JSON path.")
    parser.add_argument("--source-run", default="", help="Optional previous single-symbol run id used as precondition evidence.")
    parser.add_argument("--cost-multiplier", type=float, default=1.0, help="Multiply commission and stamp duty for transaction-cost stress tests.")
    parser.add_argument("--max-single-position-pct", type=float, default=None, help="Override portfolio_target.maxSinglePositionPct for concentration stress tests.")
    parser.add_argument("--max-overnight-exposure-pct", type=float, default=None, help="Override portfolio_target.maxOvernightExposurePct for overnight exposure budget tests.")
    parser.add_argument("--disable-market-breadth-filter", action="store_true", help="Disable the portfolio market breadth gate while keeping allowed entry dates.")
    parser.add_argument("--market-min-samples", type=int, default=None, help="Override portfolio_target.marketBreadthFilter.minSamples.")
    parser.add_argument("--market-min-above-ma20-pct", type=float, default=None, help="Override portfolio_target.marketBreadthFilter.minAboveMa20Pct.")
    parser.add_argument("--market-min-above-ma60-pct", type=float, default=None, help="Override portfolio_target.marketBreadthFilter.minAboveMa60Pct.")
    parser.add_argument("--market-min-up-pct", type=float, default=None, help="Override portfolio_target.marketBreadthFilter.minUpPct.")
    parser.add_argument("--cross-section-volume-weight", type=float, default=None, help="Override the volume-rank weight in cross-section strength scoring.")
    parser.add_argument("--max-entry-gap-pct", type=float, default=None, help="Override portfolio_target.entryRiskFilter.maxGapPct.")
    parser.add_argument("--max-entry-range-pct", type=float, default=None, help="Override portfolio_target.entryRiskFilter.maxEntryRangePct.")
    parser.add_argument("--max-intraday-return-pct", type=float, default=None, help="Override portfolio_target.entryRiskFilter.maxIntradayReturnPct.")
    parser.add_argument("--entry-gap-score-penalty-threshold-pct", type=float, default=None, help="Score-penalize entries whose entry gap is above this threshold.")
    parser.add_argument("--entry-gap-score-penalty", type=float, default=0.0, help="Fixed score penalty applied above the entry gap threshold.")
    parser.add_argument("--entry-range-score-penalty-threshold-pct", type=float, default=None, help="Score-penalize entries whose entry-day range is above this threshold.")
    parser.add_argument("--entry-range-score-penalty", type=float, default=0.0, help="Fixed score penalty applied above the entry range threshold.")
    parser.add_argument("--entry-gap-size-haircut-threshold-pct", type=float, default=None, help="Reduce entry size when the entry gap is above this threshold.")
    parser.add_argument("--entry-gap-size-haircut-pct", type=float, default=0.0, help="Position-size haircut applied above the entry gap threshold.")
    parser.add_argument("--entry-range-size-haircut-threshold-pct", type=float, default=None, help="Reduce entry size when the entry-day range is above this threshold.")
    parser.add_argument("--entry-range-size-haircut-pct", type=float, default=0.0, help="Position-size haircut applied above the entry range threshold.")
    parser.add_argument("--entry-intraday-size-haircut-threshold-pct", type=float, default=None, help="Reduce entry size when the intraday return is above this threshold.")
    parser.add_argument("--entry-intraday-size-haircut-pct", type=float, default=0.0, help="Position-size haircut applied above the intraday return threshold.")
    parser.add_argument("--max-prior-gap-down-60-pct", type=float, default=None, help="Block entries if the worst prior 60-day overnight down gap exceeds this absolute threshold.")
    parser.add_argument("--max-prior-gap-down-3-count-60", type=int, default=None, help="Block entries if prior 60-day overnight gaps <= -3% exceed this count.")
    parser.add_argument("--slippage-pct", type=float, default=0.0, help="Apply the same buy/sell slippage to portfolio executions.")
    parser.add_argument("--buy-slippage-pct", type=float, default=None, help="Override buy-side slippage for execution stress tests.")
    parser.add_argument("--sell-slippage-pct", type=float, default=None, help="Override sell-side slippage for execution stress tests.")
    parser.add_argument("--stop-gap-fill-at-open", action="store_true", help="Fill stop exits at the open when the open gaps below the stop.")
    parser.add_argument("--limit-down-stop-delay", action="store_true", help="Delay gap-stop exits when the open is near the stock's limit-down price.")
    parser.add_argument("--limit-band-tolerance-pct", type=float, default=0.002, help="Tolerance around inferred daily price-limit bands.")
    parser.add_argument("--limit-up-entry-block-pct", type=float, default=None, help="Block entries when close-to-previous-close return is at or above this threshold.")
    parser.add_argument("--next-open-entry", action="store_true", help="Queue entry signals and execute them at the next trading day's open.")
    parser.add_argument("--next-open-cancel-gap-down-pct", type=float, default=None, help="Cancel queued entries if next open is this much below signal close.")
    parser.add_argument("--early-exit-days", type=int, default=0, help="Exit weak new positions during the first N holding days.")
    parser.add_argument("--early-exit-loss-pct", type=float, default=None, help="Early-exit positions whose close is this much below entry during the early window.")
    parser.add_argument("--early-exit-entry-low-break", action="store_true", help="Early-exit positions that close below the entry day's low during the early window.")
    parser.add_argument("--gap-stop-market-cooldown-days", type=int, default=0, help="Block all new entries for N calendar days after a gap-stop exit.")
    parser.add_argument("--gap-stop-industry-cooldown-days", type=int, default=0, help="Block new entries in the same industry for N calendar days after a gap-stop exit.")
    parser.add_argument("--gap-stop-symbol-cooldown-days", type=int, default=0, help="Block re-entry in the same symbol for N calendar days after a gap-stop exit.")
    parser.add_argument("--industry-overnight-risk-window-days", type=int, default=0, help="Block new entries in industries with recent broad overnight gap-down stress.")
    parser.add_argument("--industry-overnight-risk-gap-down-pct", type=float, default=0.03, help="Industry overnight risk gap-down threshold.")
    parser.add_argument("--industry-overnight-risk-min-count", type=int, default=0, help="Minimum recent industry gap-down events required to block new entries.")
    parser.add_argument("--industry-overnight-risk-min-ratio", type=float, default=0.0, help="Minimum recent industry gap-down event ratio required to block new entries.")
    args = parser.parse_args()

    context = read_json(Path(args.context))
    apply_cost_multiplier(context, args.cost_multiplier)
    apply_portfolio_override(context, args.max_single_position_pct)
    apply_overnight_exposure_override(context, args.max_overnight_exposure_pct)
    apply_market_breadth_override(
        context,
        args.disable_market_breadth_filter,
        args.market_min_samples,
        args.market_min_above_ma20_pct,
        args.market_min_above_ma60_pct,
        args.market_min_up_pct,
    )
    apply_cross_section_weight_override(context, args.cross_section_volume_weight)
    apply_entry_risk_override(context, args.max_entry_gap_pct, args.max_entry_range_pct, args.max_intraday_return_pct)
    apply_entry_score_penalty_override(
        context,
        args.entry_gap_score_penalty_threshold_pct,
        args.entry_gap_score_penalty,
        args.entry_range_score_penalty_threshold_pct,
        args.entry_range_score_penalty,
    )
    apply_entry_size_haircut_override(
        context,
        args.entry_gap_size_haircut_threshold_pct,
        args.entry_gap_size_haircut_pct,
        args.entry_range_size_haircut_threshold_pct,
        args.entry_range_size_haircut_pct,
        args.entry_intraday_size_haircut_threshold_pct,
        args.entry_intraday_size_haircut_pct,
    )
    apply_prior_gap_risk_override(context, args.max_prior_gap_down_60_pct, args.max_prior_gap_down_3_count_60)
    apply_execution_stress(
        context,
        args.slippage_pct,
        args.buy_slippage_pct,
        args.sell_slippage_pct,
        args.stop_gap_fill_at_open,
        args.limit_down_stop_delay,
        args.limit_band_tolerance_pct,
        args.limit_up_entry_block_pct,
        args.next_open_entry,
        args.next_open_cancel_gap_down_pct,
        args.early_exit_days,
        args.early_exit_loss_pct,
        args.early_exit_entry_low_break,
        args.gap_stop_market_cooldown_days,
        args.gap_stop_industry_cooldown_days,
        args.gap_stop_symbol_cooldown_days,
        args.industry_overnight_risk_window_days,
        args.industry_overnight_risk_gap_down_pct,
        args.industry_overnight_risk_min_count,
        args.industry_overnight_risk_min_ratio,
    )
    strategy = build_strategy(args.strategy, context)
    portfolio_rules = build_portfolio_rules(context, strategy["config"])
    payload = MarketBacktestRequest(**build_market_payload(context, strategy, max_stocks=None))
    market_state_payload = build_market_breadth_payload(context, strategy)
    run_dir = RUNS_ROOT / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    started_at = now_text()
    source_analysis = read_source_analysis(args.source_run)
    with SessionLocal() as db:
        result = run_portfolio_backtest(db, payload, strategy["config"], portfolio_rules, market_state_payload=market_state_payload)

    analysis = summarize_portfolio(result, context, source_analysis)
    payload_dict = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload.dict()
    market_state_payload_dict = payload_to_dict(market_state_payload)
    output = {
        "runId": args.run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "sourceRun": args.source_run or None,
        "payload": json_safe(payload_dict),
        "marketStatePayload": json_safe(market_state_payload_dict),
        "strategy": strategy,
        "portfolioRules": portfolio_rules,
        "analysis": analysis,
        "result": result,
    }
    write_json(run_dir / "context.json", context)
    write_json(run_dir / "strategies.json", {"selected": args.strategy, "strategy": strategy, "portfolioRules": portfolio_rules})
    write_json(run_dir / "results.json", output)
    write_text(run_dir / "hypothesis.md", render_hypothesis(args.run_id, started_at, strategy, context, portfolio_rules, args.source_run, source_analysis))
    write_text(run_dir / "review.md", render_review(args.run_id, started_at, strategy, analysis, result))
    next_input = render_next_input(args.run_id, strategy, analysis)
    write_text(run_dir / "next-input.md", next_input)
    write_text(NEXT_BRIEF_PATH, next_input)

    print(json.dumps({"runId": args.run_id, "analysis": analysis, "runDir": str(run_dir)}, ensure_ascii=False, indent=2))


def build_portfolio_rules(context: dict[str, Any], strategy_config: dict[str, Any]) -> dict[str, Any]:
    target = deepcopy(context.get("portfolio_target", {}))
    market_filter = target.get("marketBreadthFilter", {})
    failure_throttle = target.get("failureThrottle", {})
    entry_risk_filter = target.get("entryRiskFilter", {})
    entry_score_penalty = target.get("entryScorePenalty", {})
    entry_size_haircut = target.get("entrySizeHaircut", {})
    cross_section_weights = target.get("crossSectionScoreWeights", {})
    return {
        "initialCash": float(target.get("initialCash", strategy_config.get("initialCash", 100000))),
        "maxPositions": int(target.get("maxPositions", 5)),
        "maxSinglePositionPct": float(target.get("maxSinglePositionPct", strategy_config.get("positionCapPct", 0.2))),
        "maxSingleExposurePct": float(target.get("maxSingleExposurePct", target.get("maxSinglePositionPct", strategy_config.get("positionCapPct", 0.2)))),
        "maxIndustryPositionPct": float(target.get("maxIndustryPositionPct", 0.4)),
        "maxIndustryExposurePct": float(target.get("maxIndustryExposurePct", target.get("maxIndustryPositionPct", 0.4))),
        "maxOvernightExposurePct": None if target.get("maxOvernightExposurePct") is None else float(target["maxOvernightExposurePct"]),
        "weeklyBuyLimit": int(target.get("weeklyBuyLimit", strategy_config.get("weeklyTradeLimit", 2))),
        "minimumCompletedTrades": int(target.get("minimumCompletedTrades", 20)),
        "entryPriority": str(target.get("entryPriority", "signal_quality")),
        "minSignalScore": target.get("minSignalScore"),
        "maxSignalScore": target.get("maxSignalScore"),
        "maxDistinctSymbols": target.get("maxDistinctSymbols"),
        "knownSymbolScoreBonus": float(target.get("knownSymbolScoreBonus", 0)),
        "repeatFailureScorePenalty": float(target.get("repeatFailureScorePenalty", 0.75)),
        "entryRiskFilter": {
            "enabled": bool(entry_risk_filter.get("enabled", False)),
            "maxEntryRangePct": entry_risk_filter.get("maxEntryRangePct"),
            "maxIntradayReturnPct": entry_risk_filter.get("maxIntradayReturnPct"),
            "maxGapPct": entry_risk_filter.get("maxGapPct"),
            "maxUpperShadowPct": entry_risk_filter.get("maxUpperShadowPct"),
            "maxLowerShadowPct": entry_risk_filter.get("maxLowerShadowPct"),
            "maxPriorGapDown60Pct": entry_risk_filter.get("maxPriorGapDown60Pct"),
            "maxPriorGapDown3Count60": entry_risk_filter.get("maxPriorGapDown3Count60"),
            "maxPriorGapDown5Count60": entry_risk_filter.get("maxPriorGapDown5Count60"),
        },
        "entryScorePenalty": {
            "gapThresholdPct": entry_score_penalty.get("gapThresholdPct"),
            "gapPenalty": float(entry_score_penalty.get("gapPenalty", 0) or 0),
            "rangeThresholdPct": entry_score_penalty.get("rangeThresholdPct"),
            "rangePenalty": float(entry_score_penalty.get("rangePenalty", 0) or 0),
        },
        "entrySizeHaircut": {
            "gapThresholdPct": entry_size_haircut.get("gapThresholdPct"),
            "gapHaircutPct": float(entry_size_haircut.get("gapHaircutPct", 0) or 0),
            "rangeThresholdPct": entry_size_haircut.get("rangeThresholdPct"),
            "rangeHaircutPct": float(entry_size_haircut.get("rangeHaircutPct", 0) or 0),
            "intradayThresholdPct": entry_size_haircut.get("intradayThresholdPct"),
            "intradayHaircutPct": float(entry_size_haircut.get("intradayHaircutPct", 0) or 0),
        },
        "crossSectionScoreWeights": {
            "return20": float(cross_section_weights.get("return20", 3.0)),
            "return60": float(cross_section_weights.get("return60", 2.0)),
            "high60": float(cross_section_weights.get("high60", 1.5)),
            "recovery20": float(cross_section_weights.get("recovery20", 1.0)),
            "volume": float(cross_section_weights.get("volume", 0.5)),
            "base": float(cross_section_weights.get("base", 0.25)),
        },
        "failureThrottle": {
            "enabled": bool(failure_throttle.get("enabled", False)),
            "lossReturnThreshold": float(failure_throttle.get("lossReturnThreshold", 0)),
            "symbolCooldownDays": int(failure_throttle.get("symbolCooldownDays", 20)),
            "industryWeeklyLossLimit": int(failure_throttle.get("industryWeeklyLossLimit", 2)),
            "industryCooldownDays": int(failure_throttle.get("industryCooldownDays", 10)),
        },
        "marketBreadthFilter": {
            "enabled": bool(market_filter.get("enabled", False)),
            "usePreviousTradingDay": bool(market_filter.get("usePreviousTradingDay", True)),
            "minSamples": int(market_filter.get("minSamples", 1000)),
            "minAboveMa20Pct": float(market_filter.get("minAboveMa20Pct", 0.45)),
            "minAboveMa60Pct": float(market_filter.get("minAboveMa60Pct", 0.35)),
            "minUpPct": float(market_filter.get("minUpPct", 0.45)),
        },
    }


def apply_cost_multiplier(context: dict[str, Any], multiplier: float) -> None:
    if multiplier <= 0:
        raise SystemExit("--cost-multiplier must be positive.")
    if multiplier == 1.0:
        return
    costs = context.setdefault("costs", {})
    for key in ["commissionPct", "stampDutyPct"]:
        if key in costs and costs[key] is not None:
            costs[key] = float(costs[key]) * multiplier
    context["costStress"] = {
        "costMultiplier": multiplier,
        "note": "commissionPct and stampDutyPct were multiplied before building the strategy config.",
    }


def apply_portfolio_override(context: dict[str, Any], max_single_position_pct: float | None) -> None:
    if max_single_position_pct is None:
        return
    if not 0 < max_single_position_pct <= 1:
        raise SystemExit("--max-single-position-pct must be in (0, 1].")
    target = context.setdefault("portfolio_target", {})
    target["maxSinglePositionPct"] = max_single_position_pct
    context["portfolioOverride"] = {
        "maxSinglePositionPct": max_single_position_pct,
        "note": "portfolio_target.maxSinglePositionPct was overridden before building portfolio rules.",
    }


def apply_overnight_exposure_override(context: dict[str, Any], max_overnight_exposure_pct: float | None) -> None:
    if max_overnight_exposure_pct is None:
        return
    if not 0 < max_overnight_exposure_pct <= 1:
        raise SystemExit("--max-overnight-exposure-pct must be in (0, 1].")
    target = context.setdefault("portfolio_target", {})
    target["maxOvernightExposurePct"] = max_overnight_exposure_pct
    context["overnightExposureOverride"] = {
        "maxOvernightExposurePct": max_overnight_exposure_pct,
        "note": "portfolio_target.maxOvernightExposurePct was overridden before building portfolio rules.",
    }


def apply_market_breadth_override(
    context: dict[str, Any],
    disable_filter: bool,
    min_samples: int | None,
    min_above_ma20_pct: float | None,
    min_above_ma60_pct: float | None,
    min_up_pct: float | None,
) -> None:
    if min_samples is not None and min_samples < 0:
        raise SystemExit("--market-min-samples must be >= 0.")
    pct_values = {
        "--market-min-above-ma20-pct": min_above_ma20_pct,
        "--market-min-above-ma60-pct": min_above_ma60_pct,
        "--market-min-up-pct": min_up_pct,
    }
    for name, value in pct_values.items():
        if value is not None and not 0 <= value <= 1:
            raise SystemExit(f"{name} must be in [0, 1].")
    if not disable_filter and min_samples is None and all(value is None for value in pct_values.values()):
        return

    target = context.setdefault("portfolio_target", {})
    market_filter = target.setdefault("marketBreadthFilter", {})
    if disable_filter:
        market_filter["enabled"] = False
    if min_samples is not None:
        market_filter["minSamples"] = min_samples
    if min_above_ma20_pct is not None:
        market_filter["minAboveMa20Pct"] = min_above_ma20_pct
    if min_above_ma60_pct is not None:
        market_filter["minAboveMa60Pct"] = min_above_ma60_pct
    if min_up_pct is not None:
        market_filter["minUpPct"] = min_up_pct
    context["marketBreadthOverride"] = {
        "disabled": disable_filter,
        "minSamples": min_samples,
        "minAboveMa20Pct": min_above_ma20_pct,
        "minAboveMa60Pct": min_above_ma60_pct,
        "minUpPct": min_up_pct,
        "note": "portfolio_target.marketBreadthFilter was overridden before building portfolio rules.",
    }


def apply_cross_section_weight_override(context: dict[str, Any], volume_weight: float | None) -> None:
    if volume_weight is None:
        return
    if volume_weight < 0:
        raise SystemExit("--cross-section-volume-weight must be >= 0.")
    target = context.setdefault("portfolio_target", {})
    weights = target.setdefault("crossSectionScoreWeights", {})
    weights["volume"] = volume_weight
    context["crossSectionScoreWeightOverride"] = {
        "volume": volume_weight,
        "note": "Only the cross-section volume rank weight was overridden before building portfolio rules.",
    }


def apply_entry_risk_override(
    context: dict[str, Any],
    max_entry_gap_pct: float | None,
    max_entry_range_pct: float | None,
    max_intraday_return_pct: float | None,
) -> None:
    if max_entry_gap_pct is None and max_entry_range_pct is None and max_intraday_return_pct is None:
        return
    if max_entry_gap_pct is not None and not 0 <= max_entry_gap_pct < 1:
        raise SystemExit("--max-entry-gap-pct must be in [0, 1).")
    if max_entry_range_pct is not None and not 0 <= max_entry_range_pct < 1:
        raise SystemExit("--max-entry-range-pct must be in [0, 1).")
    if max_intraday_return_pct is not None and not 0 <= max_intraday_return_pct < 1:
        raise SystemExit("--max-intraday-return-pct must be in [0, 1).")
    target = context.setdefault("portfolio_target", {})
    entry_risk_filter = target.setdefault("entryRiskFilter", {})
    entry_risk_filter["enabled"] = True
    if max_entry_gap_pct is not None:
        entry_risk_filter["maxGapPct"] = max_entry_gap_pct
    if max_entry_range_pct is not None:
        entry_risk_filter["maxEntryRangePct"] = max_entry_range_pct
    if max_intraday_return_pct is not None:
        entry_risk_filter["maxIntradayReturnPct"] = max_intraday_return_pct
    context["entryRiskOverride"] = {
        "maxGapPct": max_entry_gap_pct,
        "maxEntryRangePct": max_entry_range_pct,
        "maxIntradayReturnPct": max_intraday_return_pct,
        "note": "portfolio_target.entryRiskFilter was overridden before building portfolio rules.",
    }


def apply_entry_score_penalty_override(
    context: dict[str, Any],
    gap_threshold_pct: float | None,
    gap_penalty: float,
    range_threshold_pct: float | None,
    range_penalty: float,
) -> None:
    if gap_threshold_pct is not None and not 0 <= gap_threshold_pct < 1:
        raise SystemExit("--entry-gap-score-penalty-threshold-pct must be in [0, 1).")
    if range_threshold_pct is not None and not 0 <= range_threshold_pct < 1:
        raise SystemExit("--entry-range-score-penalty-threshold-pct must be in [0, 1).")
    if gap_penalty < 0:
        raise SystemExit("--entry-gap-score-penalty must be >= 0.")
    if range_penalty < 0:
        raise SystemExit("--entry-range-score-penalty must be >= 0.")
    if (gap_threshold_pct is None or gap_penalty == 0) and (range_threshold_pct is None or range_penalty == 0):
        return

    target = context.setdefault("portfolio_target", {})
    score_penalty = target.setdefault("entryScorePenalty", {})
    if gap_threshold_pct is not None and gap_penalty > 0:
        score_penalty["gapThresholdPct"] = gap_threshold_pct
        score_penalty["gapPenalty"] = gap_penalty
    if range_threshold_pct is not None and range_penalty > 0:
        score_penalty["rangeThresholdPct"] = range_threshold_pct
        score_penalty["rangePenalty"] = range_penalty
    context["entryScorePenaltyOverride"] = {
        "gapThresholdPct": gap_threshold_pct,
        "gapPenalty": gap_penalty,
        "rangeThresholdPct": range_threshold_pct,
        "rangePenalty": range_penalty,
        "note": "portfolio_target.entryScorePenalty was overridden before building portfolio rules.",
    }


def apply_entry_size_haircut_override(
    context: dict[str, Any],
    gap_threshold_pct: float | None,
    gap_haircut_pct: float,
    range_threshold_pct: float | None,
    range_haircut_pct: float,
    intraday_threshold_pct: float | None,
    intraday_haircut_pct: float,
) -> None:
    thresholds = {
        "--entry-gap-size-haircut-threshold-pct": gap_threshold_pct,
        "--entry-range-size-haircut-threshold-pct": range_threshold_pct,
        "--entry-intraday-size-haircut-threshold-pct": intraday_threshold_pct,
    }
    haircuts = {
        "--entry-gap-size-haircut-pct": gap_haircut_pct,
        "--entry-range-size-haircut-pct": range_haircut_pct,
        "--entry-intraday-size-haircut-pct": intraday_haircut_pct,
    }
    for name, value in thresholds.items():
        if value is not None and not 0 <= value < 1:
            raise SystemExit(f"{name} must be in [0, 1).")
    for name, value in haircuts.items():
        if not 0 <= value < 1:
            raise SystemExit(f"{name} must be in [0, 1).")
    if (
        (gap_threshold_pct is None or gap_haircut_pct == 0)
        and (range_threshold_pct is None or range_haircut_pct == 0)
        and (intraday_threshold_pct is None or intraday_haircut_pct == 0)
    ):
        return

    target = context.setdefault("portfolio_target", {})
    size_haircut = target.setdefault("entrySizeHaircut", {})
    if gap_threshold_pct is not None and gap_haircut_pct > 0:
        size_haircut["gapThresholdPct"] = gap_threshold_pct
        size_haircut["gapHaircutPct"] = gap_haircut_pct
    if range_threshold_pct is not None and range_haircut_pct > 0:
        size_haircut["rangeThresholdPct"] = range_threshold_pct
        size_haircut["rangeHaircutPct"] = range_haircut_pct
    if intraday_threshold_pct is not None and intraday_haircut_pct > 0:
        size_haircut["intradayThresholdPct"] = intraday_threshold_pct
        size_haircut["intradayHaircutPct"] = intraday_haircut_pct
    context["entrySizeHaircutOverride"] = {
        "gapThresholdPct": gap_threshold_pct,
        "gapHaircutPct": gap_haircut_pct,
        "rangeThresholdPct": range_threshold_pct,
        "rangeHaircutPct": range_haircut_pct,
        "intradayThresholdPct": intraday_threshold_pct,
        "intradayHaircutPct": intraday_haircut_pct,
        "note": "portfolio_target.entrySizeHaircut was overridden before building portfolio rules.",
    }


def apply_prior_gap_risk_override(context: dict[str, Any], max_prior_gap_down_60_pct: float | None, max_prior_gap_down_3_count_60: int | None) -> None:
    if max_prior_gap_down_60_pct is not None and not 0 <= max_prior_gap_down_60_pct < 1:
        raise SystemExit("--max-prior-gap-down-60-pct must be in [0, 1).")
    if max_prior_gap_down_3_count_60 is not None and max_prior_gap_down_3_count_60 < 0:
        raise SystemExit("--max-prior-gap-down-3-count-60 must be >= 0.")
    if max_prior_gap_down_60_pct is None and max_prior_gap_down_3_count_60 is None:
        return
    target = context.setdefault("portfolio_target", {})
    entry_risk_filter = target.setdefault("entryRiskFilter", {})
    entry_risk_filter["enabled"] = True
    if max_prior_gap_down_60_pct is not None:
        entry_risk_filter["maxPriorGapDown60Pct"] = max_prior_gap_down_60_pct
    if max_prior_gap_down_3_count_60 is not None:
        entry_risk_filter["maxPriorGapDown3Count60"] = max_prior_gap_down_3_count_60
    context["priorGapRiskOverride"] = {
        "maxPriorGapDown60Pct": max_prior_gap_down_60_pct,
        "maxPriorGapDown3Count60": max_prior_gap_down_3_count_60,
        "note": "Prior overnight gap risk filters were overridden before building portfolio rules.",
    }


def apply_execution_stress(
    context: dict[str, Any],
    slippage_pct: float,
    buy_slippage_pct: float | None,
    sell_slippage_pct: float | None,
    stop_gap_fill_at_open: bool,
    limit_down_stop_delay: bool,
    limit_band_tolerance_pct: float,
    limit_up_entry_block_pct: float | None,
    next_open_entry: bool,
    next_open_cancel_gap_down_pct: float | None,
    early_exit_days: int,
    early_exit_loss_pct: float | None,
    early_exit_entry_low_break: bool,
    gap_stop_market_cooldown_days: int,
    gap_stop_industry_cooldown_days: int,
    gap_stop_symbol_cooldown_days: int,
    industry_overnight_risk_window_days: int,
    industry_overnight_risk_gap_down_pct: float,
    industry_overnight_risk_min_count: int,
    industry_overnight_risk_min_ratio: float,
) -> None:
    if slippage_pct < 0:
        raise SystemExit("--slippage-pct must be >= 0.")
    buy_slippage = slippage_pct if buy_slippage_pct is None else buy_slippage_pct
    sell_slippage = slippage_pct if sell_slippage_pct is None else sell_slippage_pct
    if buy_slippage < 0 or sell_slippage < 0:
        raise SystemExit("--buy-slippage-pct and --sell-slippage-pct must be >= 0.")
    if not 0 <= limit_band_tolerance_pct < 0.05:
        raise SystemExit("--limit-band-tolerance-pct must be in [0, 0.05).")
    if limit_up_entry_block_pct is not None and not 0 < limit_up_entry_block_pct < 1:
        raise SystemExit("--limit-up-entry-block-pct must be in (0, 1).")
    if next_open_cancel_gap_down_pct is not None and not 0 <= next_open_cancel_gap_down_pct < 1:
        raise SystemExit("--next-open-cancel-gap-down-pct must be in [0, 1).")
    if early_exit_days < 0:
        raise SystemExit("--early-exit-days must be >= 0.")
    if early_exit_loss_pct is not None and not 0 < early_exit_loss_pct < 1:
        raise SystemExit("--early-exit-loss-pct must be in (0, 1).")
    if (early_exit_loss_pct is not None or early_exit_entry_low_break) and early_exit_days <= 0:
        raise SystemExit("--early-exit-days must be > 0 when early-exit triggers are enabled.")
    if gap_stop_market_cooldown_days < 0 or gap_stop_industry_cooldown_days < 0 or gap_stop_symbol_cooldown_days < 0:
        raise SystemExit("--gap-stop-*-cooldown-days must be >= 0.")
    if industry_overnight_risk_window_days < 0:
        raise SystemExit("--industry-overnight-risk-window-days must be >= 0.")
    if not 0 < industry_overnight_risk_gap_down_pct < 1:
        raise SystemExit("--industry-overnight-risk-gap-down-pct must be in (0, 1).")
    if industry_overnight_risk_min_count < 0:
        raise SystemExit("--industry-overnight-risk-min-count must be >= 0.")
    if not 0 <= industry_overnight_risk_min_ratio <= 1:
        raise SystemExit("--industry-overnight-risk-min-ratio must be in [0, 1].")
    if (
        not buy_slippage
        and not sell_slippage
        and not stop_gap_fill_at_open
        and not limit_down_stop_delay
        and limit_up_entry_block_pct is None
        and not next_open_entry
        and next_open_cancel_gap_down_pct is None
        and early_exit_days <= 0
        and gap_stop_market_cooldown_days <= 0
        and gap_stop_industry_cooldown_days <= 0
        and gap_stop_symbol_cooldown_days <= 0
        and industry_overnight_risk_window_days <= 0
    ):
        return
    stress = {
        "buySlippagePct": buy_slippage,
        "sellSlippagePct": sell_slippage,
        "stopGapFillAtOpen": stop_gap_fill_at_open,
        "limitDownStopDelay": limit_down_stop_delay,
        "limitBandTolerancePct": limit_band_tolerance_pct,
        "limitUpEntryBlockPct": limit_up_entry_block_pct,
        "nextOpenEntry": next_open_entry,
        "nextOpenCancelGapDownPct": next_open_cancel_gap_down_pct,
        "earlyExitDays": early_exit_days,
        "earlyExitLossPct": early_exit_loss_pct,
        "earlyExitEntryLowBreak": early_exit_entry_low_break,
        "gapStopMarketCooldownDays": gap_stop_market_cooldown_days,
        "gapStopIndustryCooldownDays": gap_stop_industry_cooldown_days,
        "gapStopSymbolCooldownDays": gap_stop_symbol_cooldown_days,
        "industryOvernightRiskWindowDays": industry_overnight_risk_window_days,
        "industryOvernightRiskGapDownPct": industry_overnight_risk_gap_down_pct,
        "industryOvernightRiskMinCount": industry_overnight_risk_min_count,
        "industryOvernightRiskMinRatio": industry_overnight_risk_min_ratio,
    }
    context["execution_stress"] = stress
    context["executionStress"] = {
        **stress,
        "note": "Execution stress was applied before building the strategy config. It changes fill prices and optional entry blocking, not entry signal semantics.",
    }


def build_market_breadth_payload(context: dict[str, Any], strategy: dict[str, Any]) -> MarketBacktestRequest | None:
    scope_override = context.get("market_breadth_scope")
    if not scope_override:
        return None
    breadth_context = deepcopy(context)
    scope = deepcopy(context["scope"])
    scope.update(scope_override)
    breadth_context["scope"] = scope
    return MarketBacktestRequest(**build_market_payload(breadth_context, strategy, max_stocks=None))


def payload_to_dict(payload: MarketBacktestRequest | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    return payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload.dict()


def run_portfolio_backtest(
    db: Session,
    payload: MarketBacktestRequest,
    cfg: dict[str, Any],
    portfolio_rules: dict[str, Any],
    evaluation_start_date: date | None = None,
    market_state_payload: MarketBacktestRequest | None = None,
) -> dict[str, Any]:
    stocks = [stock_to_market_meta(stock) for stock in query_backtest_stocks(db, payload)]
    by_date, skipped = load_signal_rows(db, stocks, payload, cfg)
    market_state_source = "trade_candidates"
    market_state_scope: dict[str, Any] | None = None
    market_state_by_date = by_date
    if market_state_payload is not None:
        market_state_source = "independent_breadth_scope"
        market_state_stocks = [stock_to_market_meta(stock) for stock in query_backtest_stocks(db, market_state_payload)]
        market_state_by_date, market_state_skipped = load_signal_rows(db, market_state_stocks, market_state_payload, cfg)
        market_state_scope = {
            "startDate": market_state_payload.start_date.isoformat(),
            "endDate": market_state_payload.end_date.isoformat(),
            "candidates": len(market_state_stocks),
            "tested": len(market_state_stocks) - market_state_skipped,
            "skipped": market_state_skipped,
            "filters": payload_filters(market_state_payload),
        }
    cash = float(portfolio_rules["initialCash"])
    initial_cash = cash
    positions: dict[str, dict[str, Any]] = {}
    weekly_buys: dict[str, int] = {}
    trades: list[dict[str, Any]] = []
    completed_trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    pending_entries: list[dict[str, Any]] = []
    symbol_cooldowns: dict[str, date] = {}
    gap_stop_symbol_cooldowns: dict[str, date] = {}
    symbol_failure_counts: dict[str, int] = {}
    traded_symbols: set[str] = set()
    industry_cooldowns: dict[str, date] = {}
    gap_stop_industry_cooldowns: dict[str, date] = {}
    industry_weekly_losses: dict[str, int] = {}
    industry_overnight_gap_history: dict[str, list[tuple[int, int]]] = defaultdict(list)
    gap_stop_market_cooldown_until: date | None = None
    max_single_position_pct = 0.0
    max_industry_position_pct = 0.0
    market_states = build_market_states(market_state_by_date, portfolio_rules["marketBreadthFilter"])
    market_stats = {
        "blockedMarketDays": 0,
        "blockedMarketSignals": 0,
        "blockedRiskSignals": 0,
        "blockedLimitUpSignals": 0,
        "stopGapFillEvents": 0,
        "estimatedSlippageCost": 0.0,
        "nextOpenEntryOrders": 0,
        "nextOpenEntryCancels": 0,
        "earlyExitEvents": 0,
        "gapStopMarketCooldownEvents": 0,
        "gapStopIndustryCooldownEvents": 0,
        "gapStopSymbolCooldownEvents": 0,
        "blockedGapStopMarketCooldownSignals": 0,
        "blockedGapStopIndustryCooldownSignals": 0,
        "blockedGapStopSymbolCooldownSignals": 0,
        "industryOvernightRiskActiveDays": 0,
        "industryOvernightRiskActiveIndustryDays": 0,
        "blockedIndustryOvernightRiskSignals": 0,
        "blockedOvernightBudgetSignals": 0,
        "overnightBudgetReducedEntries": 0,
        "overnightBudgetReductionShares": 0,
        "entrySizeHaircutReducedEntries": 0,
        "entrySizeHaircutReductionShares": 0,
        "limitDownStopDelayEvents": 0,
        "blockedRiskReasons": {},
    }
    throttle_stats = {
        "symbolCooldownEvents": 0,
        "industryCooldownEvents": 0,
        "blockedSymbolCooldownSignals": 0,
        "blockedIndustryCooldownSignals": 0,
    }

    previous_market_state: dict[str, Any] | None = None
    for trade_date in sorted(by_date):
        current_date = date.fromisoformat(trade_date)
        day_items = by_date[trade_date]
        current_market_state = market_states.get(trade_date, default_market_state(trade_date))
        entry_market_state = previous_market_state if portfolio_rules["marketBreadthFilter"]["usePreviousTradingDay"] and previous_market_state else current_market_state
        if evaluation_start_date and current_date < evaluation_start_date:
            previous_market_state = current_market_state
            continue
        rows_by_code = {item["stock"]["ts_code"]: item for item in day_items}
        industry_overnight_risk = update_industry_overnight_risk(industry_overnight_gap_history, day_items, cfg)
        if industry_overnight_risk:
            market_stats["industryOvernightRiskActiveDays"] += 1
            market_stats["industryOvernightRiskActiveIndustryDays"] += len(industry_overnight_risk)
        profitable_exits: set[str] = set()

        for ts_code in list(positions):
            item = rows_by_code.get(ts_code)
            if not item:
                continue
            row = item["row"]
            position = positions[ts_code]
            position["lastPrice"] = row["close"]
            exit_action = decide_exit(row, position, cfg)
            if exit_action:
                if exit_action["kind"] == "defer":
                    market_stats["limitDownStopDelayEvents"] += 1
                    trades.append(
                        {
                            "date": row["date"],
                            "ts_code": position["ts_code"],
                            "name": position["name"],
                            "action": "止损延迟",
                            "price": row["open"],
                            "quantity": int(position["shares"]),
                            "cash": cash,
                            "reason": exit_action["reason"],
                            "fee": 0.0,
                            "basePrice": row["open"],
                            "priceRule": exit_action.get("priceRule"),
                            "executionStatus": "deferred",
                            "stopPrice": position["stopPrice"],
                            "limitDownPrice": row.get("limitDownPrice"),
                            "dailyLimitPct": row.get("dailyLimitPct"),
                            "entryDate": position["entryDate"],
                            "entryPrice": position["entryPrice"],
                            "industry": position.get("industry"),
                            "score": position.get("entryScore"),
                            "scoreParts": position.get("entryScoreParts"),
                            "riskMetrics": position.get("entryRiskMetrics"),
                        }
                    )
                    continue
                cash, completed_trade = execute_sell(cash, position, row, exit_action, trades, completed_trades, cfg, market_stats)
                if position["shares"] == 0:
                    if exit_action["price"] > position["entryPrice"]:
                        profitable_exits.add(ts_code)
                    if completed_trade:
                        update_failure_throttle(
                            completed_trade,
                            current_date,
                            symbol_cooldowns,
                            symbol_failure_counts,
                            industry_cooldowns,
                            industry_weekly_losses,
                            portfolio_rules,
                            throttle_stats,
                        )
                        gap_stop_market_cooldown_until = update_gap_stop_cooldowns(
                            completed_trade,
                            current_date,
                            gap_stop_symbol_cooldowns,
                            gap_stop_industry_cooldowns,
                            gap_stop_market_cooldown_until,
                            cfg,
                            market_stats,
                        )
                    del positions[ts_code]

        cash = enforce_position_caps(cash, positions, rows_by_code, trades, portfolio_rules, cfg, market_stats)
        if bool(cfg.get("nextOpenEntry", False)) and pending_entries:
            cash = execute_pending_entries(
                cash,
                pending_entries,
                rows_by_code,
                positions,
                trades,
                portfolio_rules,
                cfg,
                market_stats,
                weekly_buys,
                get_week_key(trade_date),
            )
            pending_entries = []
        equity_before_entries = portfolio_equity(cash, positions)
        buy_slots = int(portfolio_rules["maxPositions"]) - len(positions)
        week_key = get_week_key(trade_date)
        weekly_remaining = int(portfolio_rules["weeklyBuyLimit"]) - weekly_buys.get(week_key, 0)
        if buy_slots > 0 and weekly_remaining > 0:
            signals = build_entry_signals(
                day_items,
                positions,
                profitable_exits,
                cfg,
                portfolio_rules,
                current_date,
                symbol_cooldowns,
                symbol_failure_counts,
                industry_cooldowns,
                gap_stop_symbol_cooldowns,
                gap_stop_industry_cooldowns,
                industry_overnight_risk,
                traded_symbols,
                throttle_stats,
                market_stats,
            )
            if entry_market_state["riskOn"]:
                if gap_stop_market_cooldown_until and current_date <= gap_stop_market_cooldown_until:
                    market_stats["blockedGapStopMarketCooldownSignals"] += len(signals)
                    signals = []
                elif bool(cfg.get("nextOpenEntry", False)):
                    pending_entries = prepare_pending_entries(signals[: max(buy_slots * 4, 4)], cfg)
                    market_stats["nextOpenEntryOrders"] += len(pending_entries)
                    signals = []
                for signal in signals[: max(buy_slots * 4, 4)]:
                    if buy_slots <= 0 or weekly_remaining <= 0:
                        break
                    quantity, stop = size_position(signal["row"], cash, equity_before_entries, positions, signal["stock"], portfolio_rules, cfg)
                    if quantity <= 0:
                        continue
                    quantity = cap_quantity_for_entry_size_haircut(quantity, signal.get("riskMetrics") or {}, portfolio_rules, cfg, market_stats)
                    if quantity <= 0:
                        continue
                    quantity = cap_quantity_for_overnight_budget(
                        quantity,
                        execution_buy_price(signal["row"]["close"], cfg),
                        equity_before_entries,
                        positions,
                        portfolio_rules,
                        cfg,
                        market_stats,
                    )
                    if quantity <= 0:
                        continue
                    cash = execute_buy(cash, signal, quantity, stop, trades, positions, cfg, market_stats, portfolio_equity_before_entry=equity_before_entries)
                    traded_symbols.add(signal["stock"]["ts_code"])
                    weekly_buys[week_key] = weekly_buys.get(week_key, 0) + 1
                    weekly_remaining -= 1
                    buy_slots -= 1
            elif signals:
                market_stats["blockedMarketDays"] += 1
                market_stats["blockedMarketSignals"] += len(signals)

        equity = portfolio_equity(cash, positions)
        single_pct, industry_pct = concentration_metrics(equity, positions)
        max_single_position_pct = max(max_single_position_pct, single_pct)
        max_industry_position_pct = max(max_industry_position_pct, industry_pct)
        equity_curve.append(
            {
                "date": trade_date,
                "equity": equity,
                "cash": cash,
                "positions": len(positions),
                "marketRiskOn": bool(entry_market_state["riskOn"]),
                "marketAboveMa20Pct": entry_market_state.get("aboveMa20Pct"),
                "marketAboveMa60Pct": entry_market_state.get("aboveMa60Pct"),
                "marketUpPct": entry_market_state.get("upPct"),
            }
        )
        for position in positions.values():
            position["barsHeld"] = int(position.get("barsHeld", 0)) + 1
        previous_market_state = current_market_state

    evaluated_market_states = {
        trade_date: state
        for trade_date, state in market_states.items()
        if not evaluation_start_date or date.fromisoformat(trade_date) >= evaluation_start_date
    }
    summary = build_summary(
        initial_cash,
        cash,
        positions,
        trades,
        completed_trades,
        equity_curve,
        max_single_position_pct,
        max_industry_position_pct,
        evaluated_market_states,
        market_stats,
        throttle_stats,
        portfolio_rules,
    )
    return json_safe(
        {
            "status": "ok",
            "scope": {
                "startDate": payload.start_date.isoformat(),
                "evaluationStartDate": evaluation_start_date.isoformat() if evaluation_start_date else payload.start_date.isoformat(),
                "endDate": payload.end_date.isoformat(),
                "candidates": len(stocks),
                "tested": len(stocks) - skipped,
                "skipped": skipped,
                "filters": {
                    **payload_filters(payload),
                },
                "marketStateSource": market_state_source,
                "marketStateScope": market_state_scope,
            },
            "summary": summary,
            "equity": equity_curve,
            "trades": trades,
            "completedTrades": completed_trades,
            "finalPositions": list(positions.values()),
        }
    )


def payload_filters(payload: MarketBacktestRequest) -> dict[str, Any]:
    return {
        "excludeSt": payload.exclude_st,
        "excludeBj": payload.exclude_bj,
        "minListDays": payload.min_list_days,
        "minAvgAmount": payload.min_avg_amount,
        "minAvgCircMv": payload.min_avg_circ_mv,
        "minAvgTurnoverRateF": payload.min_avg_turnover_rate_f,
    }


def load_signal_rows(db: Session, stocks: list[dict[str, Any]], payload: MarketBacktestRequest, cfg: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], int]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skipped = 0
    batch_size = 240
    for batch_start in range(0, len(stocks), batch_size):
        batch = stocks[batch_start : batch_start + batch_size]
        bars_by_code = query_backtest_rows_by_code(db, [stock["ts_code"] for stock in batch], payload.start_date, payload.end_date)
        for stock in batch:
            rows = bars_by_code.get(stock["ts_code"], [])
            if len(rows) < payload.min_bars:
                skipped += 1
                continue
            daily_limit_pct = infer_daily_limit_pct(stock["ts_code"])
            raw_rows = [
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
            enriched = enrich_rows(raw_rows, cfg)
            for index, row in enumerate(enriched):
                prev = enriched[index - 1] if index else None
                row["dailyLimitPct"] = daily_limit_pct
                if prev and prev.get("close"):
                    row["prevClose"] = prev["close"]
                    row["limitDownPrice"] = float(prev["close"]) * (1 - daily_limit_pct)
                    row["limitUpPrice"] = float(prev["close"]) * (1 + daily_limit_pct)
                by_date[row["date"]].append({"stock": stock, "row": row, "prev": prev})
    return by_date, skipped


def infer_daily_limit_pct(ts_code: str) -> float:
    code = ts_code.split(".", 1)[0]
    if code.startswith(("300", "301", "688")):
        return 0.20
    return 0.10


def build_market_states(by_date: dict[str, list[dict[str, Any]]], rules: dict[str, Any]) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    for trade_date, items in by_date.items():
        samples = 0
        above_ma20 = 0
        above_ma60 = 0
        up_count = 0
        for item in items:
            row = item["row"]
            prev = item["prev"]
            if not finite(row.get("ma20")) or not finite(row.get("ma60")):
                continue
            samples += 1
            if row["close"] >= row["ma20"]:
                above_ma20 += 1
            if row["close"] >= row["ma60"]:
                above_ma60 += 1
            if prev and row["close"] > prev["close"]:
                up_count += 1
        above_ma20_pct = above_ma20 / samples if samples else 0
        above_ma60_pct = above_ma60 / samples if samples else 0
        up_pct = up_count / samples if samples else 0
        risk_on = (
            not bool(rules.get("enabled", False))
            or (
                samples >= int(rules["minSamples"])
                and above_ma20_pct >= float(rules["minAboveMa20Pct"])
                and above_ma60_pct >= float(rules["minAboveMa60Pct"])
                and up_pct >= float(rules["minUpPct"])
            )
        )
        states[trade_date] = {
            "date": trade_date,
            "samples": samples,
            "riskOn": risk_on,
            "aboveMa20Pct": above_ma20_pct,
            "aboveMa60Pct": above_ma60_pct,
            "upPct": up_pct,
        }
    return states


def default_market_state(trade_date: str) -> dict[str, Any]:
    return {
        "date": trade_date,
        "samples": 0,
        "riskOn": True,
        "aboveMa20Pct": None,
        "aboveMa60Pct": None,
        "upPct": None,
    }


def update_industry_overnight_risk(
    industry_gap_history: dict[str, list[tuple[int, int]]],
    day_items: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> set[str]:
    window = int(cfg.get("industryOvernightRiskWindowDays") or 0)
    if window <= 0:
        return set()

    gap_down_pct = float(cfg.get("industryOvernightRiskGapDownPct") or 0.03)
    min_count = int(cfg.get("industryOvernightRiskMinCount") or 0)
    min_ratio = float(cfg.get("industryOvernightRiskMinRatio") or 0)
    daily: dict[str, dict[str, int]] = defaultdict(lambda: {"down": 0, "samples": 0})
    for item in day_items:
        prev = item.get("prev")
        if not prev or not prev.get("close"):
            continue
        industry = str(item["stock"].get("industry") or "未知")
        gap = float(item["row"]["open"]) / float(prev["close"]) - 1
        daily[industry]["samples"] += 1
        if gap <= -gap_down_pct:
            daily[industry]["down"] += 1

    for industry, stats in daily.items():
        history = industry_gap_history[industry]
        history.append((stats["down"], stats["samples"]))
        if len(history) > window:
            del history[:-window]

    active: set[str] = set()
    for industry, history in industry_gap_history.items():
        down_count = sum(item[0] for item in history)
        sample_count = sum(item[1] for item in history)
        if sample_count and down_count >= min_count and down_count / sample_count >= min_ratio:
            active.add(industry)
    return active


def decide_exit(row: dict[str, Any], position: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any] | None:
    entry_price = float(position["entryPrice"])
    stop_price = float(position["stopPrice"])
    if row["low"] <= stop_price:
        if bool(cfg.get("stopGapFillAtOpen", False)) and row["open"] < stop_price:
            if limit_down_stop_delayed(row, cfg):
                return {
                    "kind": "defer",
                    "reason": "组合硬止损/保本线跳空遇跌停附近，保守延迟成交",
                    "priceRule": "limit_down_stop_delay",
                }
            return {"kind": "full", "price": row["open"], "reason": "组合硬止损/保本线跳空开盘成交", "priceRule": "gap_open_stop"}
        return {"kind": "full", "price": stop_price, "reason": "组合硬止损/保本线触发", "priceRule": "stop"}
    if row["high"] >= entry_price * (1 + float(cfg["takeProfit2Pct"])):
        return {"kind": "full", "price": entry_price * (1 + float(cfg["takeProfit2Pct"])), "reason": "组合第二止盈清仓", "priceRule": "take_profit"}
    if row["high"] >= entry_price * (1 + float(cfg["takeProfit1Pct"])) and not position["partialTaken"]:
        quantity = round_to_lot(floor(int(position["shares"]) / 2), int(cfg["lotSize"]))
        if quantity > 0:
            return {"kind": "partial", "quantity": quantity, "price": entry_price * (1 + float(cfg["takeProfit1Pct"])), "reason": "组合第一止盈减半", "priceRule": "take_profit"}
    early_exit = decide_early_exit(row, position, cfg)
    if early_exit:
        return early_exit
    if cfg["marketState"] == "weak" and row["close"] < row["trendSlowMa"]:
        return {"kind": "full", "price": row["close"], "reason": "组合弱势跌破慢线防守", "priceRule": "close"}
    return None


def limit_down_stop_delayed(row: dict[str, Any], cfg: dict[str, Any]) -> bool:
    if not bool(cfg.get("limitDownStopDelay", False)):
        return False
    limit_down_price = row.get("limitDownPrice")
    if limit_down_price is None:
        return False
    tolerance = float(cfg.get("limitBandTolerancePct", 0) or 0)
    return float(row["open"]) <= float(limit_down_price) * (1 + tolerance)


def decide_early_exit(row: dict[str, Any], position: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any] | None:
    early_days = int(cfg.get("earlyExitDays") or 0)
    if early_days <= 0 or bool(position.get("partialTaken", False)):
        return None
    bars_held = int(position.get("barsHeld", 0))
    if bars_held <= 0 or bars_held > early_days:
        return None

    entry_price = float(position["entryPrice"])
    triggers: list[str] = []
    early_loss_pct = cfg.get("earlyExitLossPct")
    if early_loss_pct is not None and row["close"] <= entry_price * (1 - float(early_loss_pct)):
        triggers.append(f"收盘较入场亏损达到 {float(early_loss_pct):.2%}")
    if bool(cfg.get("earlyExitEntryLowBreak", False)):
        entry_low = position.get("entryLow")
        if entry_low is not None and row["close"] < float(entry_low):
            triggers.append("收盘跌破开仓日低点")
    if not triggers:
        return None
    return {
        "kind": "full",
        "price": row["close"],
        "reason": f"开仓后早期弱势保护（持有 {bars_held} 日，{'；'.join(triggers)}）",
        "priceRule": "early_exit_close",
    }


def execute_sell(
    cash: float,
    position: dict[str, Any],
    row: dict[str, Any],
    exit_action: dict[str, Any],
    trades: list[dict[str, Any]],
    completed_trades: list[dict[str, Any]],
    cfg: dict[str, Any],
    market_stats: dict[str, int | float],
) -> tuple[float, dict[str, Any] | None]:
    quantity = int(position["shares"]) if exit_action["kind"] == "full" else min(int(exit_action["quantity"]), int(position["shares"]))
    base_price = float(exit_action["price"])
    price = execution_sell_price(base_price, cfg)
    market_stats["estimatedSlippageCost"] += max(0.0, base_price - price) * quantity
    if exit_action.get("priceRule") == "gap_open_stop":
        market_stats["stopGapFillEvents"] += 1
    if exit_action.get("priceRule") == "early_exit_close":
        market_stats["earlyExitEvents"] += 1
    gross = price * quantity
    fee = gross * (float(position["commissionPct"]) + float(position["stampDutyPct"]))
    cash += gross - fee
    position["realizedGross"] = float(position.get("realizedGross", 0.0)) + gross
    position["realizedFees"] = float(position.get("realizedFees", 0.0)) + fee
    position["shares"] -= quantity
    position["lastPrice"] = row["close"]
    trades.append(
        {
            "date": row["date"],
            "ts_code": position["ts_code"],
            "name": position["name"],
            "action": "卖出" if position["shares"] == 0 else "减仓",
            "price": price,
            "quantity": quantity,
            "cash": cash,
            "reason": exit_action["reason"],
            "fee": fee,
            "basePrice": base_price,
            "priceRule": exit_action.get("priceRule"),
        }
    )
    completed_trade = None
    if position["shares"] == 0:
        entry_cost = float(
            position.get("entryCost")
            or (float(position["entryPrice"]) * int(position.get("initialShares") or quantity))
        )
        net_proceeds = float(position.get("realizedGross", 0.0)) - float(position.get("realizedFees", 0.0))
        net_pnl = net_proceeds - entry_cost
        entry_equity = position.get("entryEquity")
        capital_return_pct = net_pnl / entry_cost if entry_cost else 0
        completed_trade = {
            "ts_code": position["ts_code"],
            "name": position["name"],
            "industry": position["industry"],
            "entryDate": position["entryDate"],
            "exitDate": row["date"],
            "entryPrice": position["entryPrice"],
            "exitPrice": price,
            "returnPct": capital_return_pct,
            "exitPriceReturnPct": (price - position["entryPrice"]) / position["entryPrice"] if position["entryPrice"] else 0,
            "entryCost": entry_cost,
            "netProceeds": net_proceeds,
            "netPnl": net_pnl,
            "capitalReturnPct": capital_return_pct,
            "pnlPctOfEntryEquity": net_pnl / float(entry_equity) if entry_equity else None,
            "exitReason": exit_action["reason"],
            "exitPriceRule": exit_action.get("priceRule"),
            "entryScore": position.get("entryScore"),
            "entryScoreParts": position.get("entryScoreParts"),
            "entryRiskMetrics": position.get("entryRiskMetrics"),
        }
        completed_trades.append(completed_trade)
    else:
        position["partialTaken"] = True
        position["stopPrice"] = position["entryPrice"]
    return cash, completed_trade


def enforce_position_caps(
    cash: float,
    positions: dict[str, dict[str, Any]],
    rows_by_code: dict[str, dict[str, Any]],
    trades: list[dict[str, Any]],
    portfolio_rules: dict[str, Any],
    cfg: dict[str, Any],
    market_stats: dict[str, int | float],
) -> float:
    equity = portfolio_equity(cash, positions)
    if equity <= 0:
        return cash
    cap_value = equity * float(portfolio_rules["maxSinglePositionPct"])
    for ts_code, position in sorted(positions.items(), key=lambda item: float(item[1]["shares"]) * float(item[1]["lastPrice"]), reverse=True):
        item = rows_by_code.get(ts_code)
        if item:
            position["lastPrice"] = item["row"]["close"]
        base_price = float(position["lastPrice"])
        price = execution_sell_price(base_price, cfg)
        value = int(position["shares"]) * base_price
        if value <= cap_value:
            continue
        excess_value = value - cap_value
        quantity = round_to_lot(floor(excess_value / base_price), int(cfg["lotSize"]))
        if quantity <= 0:
            continue
        quantity = min(quantity, int(position["shares"]))
        market_stats["estimatedSlippageCost"] += max(0.0, base_price - price) * quantity
        gross = price * quantity
        fee = gross * (float(position["commissionPct"]) + float(position["stampDutyPct"]))
        cash += gross - fee
        position["realizedGross"] = float(position.get("realizedGross", 0.0)) + gross
        position["realizedFees"] = float(position.get("realizedFees", 0.0)) + fee
        position["shares"] -= quantity
        trades.append(
            {
                "date": item["row"]["date"] if item else position["entryDate"],
                "ts_code": position["ts_code"],
                "name": position["name"],
                "action": "集中度减仓",
                "price": price,
                "quantity": quantity,
                "cash": cash,
                "reason": "单票集中度超过组合上限",
                "fee": fee,
                "basePrice": base_price,
                "priceRule": "concentration_rebalance",
            }
        )
        if position["shares"] <= 0:
            del positions[ts_code]
    return cash


def update_failure_throttle(
    completed_trade: dict[str, Any],
    trade_date: date,
    symbol_cooldowns: dict[str, date],
    symbol_failure_counts: dict[str, int],
    industry_cooldowns: dict[str, date],
    industry_weekly_losses: dict[str, int],
    portfolio_rules: dict[str, Any],
    throttle_stats: dict[str, int],
) -> None:
    rules = portfolio_rules["failureThrottle"]
    ts_code = completed_trade["ts_code"]
    industry = str(completed_trade.get("industry") or "未知")
    if float(completed_trade["returnPct"]) >= float(rules["lossReturnThreshold"]):
        symbol_failure_counts[ts_code] = 0
        return
    symbol_failure_counts[ts_code] = symbol_failure_counts.get(ts_code, 0) + 1
    if not bool(rules["enabled"]):
        return
    cooldown_days = int(rules["symbolCooldownDays"]) * min(symbol_failure_counts[ts_code], 3)
    symbol_cooldowns[ts_code] = trade_date + timedelta(days=cooldown_days)
    throttle_stats["symbolCooldownEvents"] += 1

    industry_week_key = f"{get_week_key(trade_date.isoformat())}:{industry}"
    industry_weekly_losses[industry_week_key] = industry_weekly_losses.get(industry_week_key, 0) + 1
    if industry_weekly_losses[industry_week_key] >= int(rules["industryWeeklyLossLimit"]):
        industry_cooldowns[industry] = trade_date + timedelta(days=int(rules["industryCooldownDays"]))
        throttle_stats["industryCooldownEvents"] += 1


def update_gap_stop_cooldowns(
    completed_trade: dict[str, Any],
    trade_date: date,
    symbol_cooldowns: dict[str, date],
    industry_cooldowns: dict[str, date],
    market_cooldown_until: date | None,
    cfg: dict[str, Any],
    market_stats: dict[str, int | float],
) -> date | None:
    if completed_trade.get("exitPriceRule") != "gap_open_stop":
        return market_cooldown_until

    symbol_days = int(cfg.get("gapStopSymbolCooldownDays") or 0)
    industry_days = int(cfg.get("gapStopIndustryCooldownDays") or 0)
    market_days = int(cfg.get("gapStopMarketCooldownDays") or 0)

    if symbol_days > 0:
        symbol_cooldowns[completed_trade["ts_code"]] = trade_date + timedelta(days=symbol_days)
        market_stats["gapStopSymbolCooldownEvents"] += 1
    if industry_days > 0:
        industry = str(completed_trade.get("industry") or "未知")
        industry_cooldowns[industry] = trade_date + timedelta(days=industry_days)
        market_stats["gapStopIndustryCooldownEvents"] += 1
    if market_days <= 0:
        return market_cooldown_until

    cooldown_until = trade_date + timedelta(days=market_days)
    market_stats["gapStopMarketCooldownEvents"] += 1
    if market_cooldown_until is None or cooldown_until > market_cooldown_until:
        return cooldown_until
    return market_cooldown_until


def build_entry_signals(
    day_items: list[dict[str, Any]],
    positions: dict[str, dict[str, Any]],
    profitable_exits: set[str],
    cfg: dict[str, Any],
    portfolio_rules: dict[str, Any],
    trade_date: date,
    symbol_cooldowns: dict[str, date],
    symbol_failure_counts: dict[str, int],
    industry_cooldowns: dict[str, date],
    gap_stop_symbol_cooldowns: dict[str, date],
    gap_stop_industry_cooldowns: dict[str, date],
    industry_overnight_risk: set[str],
    traded_symbols: set[str],
    throttle_stats: dict[str, int],
    market_stats: dict[str, int | float],
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    max_distinct = portfolio_rules.get("maxDistinctSymbols")
    for item in day_items:
        stock = item["stock"]
        ts_code = stock["ts_code"]
        if ts_code in positions or ts_code in profitable_exits:
            continue
        if max_distinct is not None and ts_code not in traded_symbols and len(traded_symbols) >= int(max_distinct):
            continue
        if gap_stop_symbol_cooldowns.get(ts_code) and trade_date <= gap_stop_symbol_cooldowns[ts_code]:
            market_stats["blockedGapStopSymbolCooldownSignals"] += 1
            continue
        if symbol_cooldowns.get(ts_code) and trade_date <= symbol_cooldowns[ts_code]:
            throttle_stats["blockedSymbolCooldownSignals"] += 1
            continue
        industry = str(stock.get("industry") or "未知")
        if gap_stop_industry_cooldowns.get(industry) and trade_date <= gap_stop_industry_cooldowns[industry]:
            market_stats["blockedGapStopIndustryCooldownSignals"] += 1
            continue
        if industry in industry_overnight_risk:
            market_stats["blockedIndustryOvernightRiskSignals"] += 1
            continue
        if industry_cooldowns.get(industry) and trade_date <= industry_cooldowns[industry]:
            throttle_stats["blockedIndustryCooldownSignals"] += 1
            continue
        if limit_up_entry_blocked(item["row"], item["prev"], cfg):
            market_stats["blockedLimitUpSignals"] += 1
            continue
        signal = should_enter(item["row"], item["prev"], cfg)
        if not signal["ok"]:
            if signal.get("blockedByRisk"):
                market_stats["blockedRiskSignals"] += 1
                record_blocked_risk_reason(market_stats, signal.get("reason") or "strategy_risk")
            continue
        risk_ok, risk_metrics, risk_reason = entry_risk_filter_ok(item["row"], item["prev"], portfolio_rules)
        if not risk_ok:
            market_stats["blockedRiskSignals"] += 1
            record_blocked_risk_reason(market_stats, risk_reason or "entry_risk_filter")
            continue
        score = signal_score(item["row"], portfolio_rules) - symbol_failure_counts.get(ts_code, 0) * float(portfolio_rules["repeatFailureScorePenalty"])
        if ts_code in traded_symbols:
            score += float(portfolio_rules["knownSymbolScoreBonus"])
        min_score = portfolio_rules.get("minSignalScore")
        max_score = portfolio_rules.get("maxSignalScore")
        if min_score is not None and score < float(min_score):
            continue
        if max_score is not None and score > float(max_score):
            continue
        signals.append({**item, "score": score, "reason": signal["reason"], "knownSymbol": ts_code in traded_symbols, "riskMetrics": risk_metrics})
    if portfolio_rules.get("entryPriority") == "cross_section_strength":
        apply_cross_section_strength_scores(signals, symbol_failure_counts, portfolio_rules)
    signals.sort(key=lambda item: item["score"], reverse=True)
    return signals


def record_blocked_risk_reason(market_stats: dict[str, Any], reason: str) -> None:
    reasons = market_stats.setdefault("blockedRiskReasons", {})
    reasons[reason] = int(reasons.get(reason, 0)) + 1


def entry_risk_filter_ok(row: dict[str, Any], prev: dict[str, Any] | None, portfolio_rules: dict[str, Any]) -> tuple[bool, dict[str, float], str | None]:
    rules = portfolio_rules["entryRiskFilter"]
    metrics = entry_risk_metrics(row, prev)
    if not bool(rules["enabled"]):
        return True, metrics, None
    limits = {
        "entryRangePct": rules.get("maxEntryRangePct"),
        "intradayReturnPct": rules.get("maxIntradayReturnPct"),
        "gapPct": rules.get("maxGapPct"),
        "upperShadowPct": rules.get("maxUpperShadowPct"),
        "lowerShadowPct": rules.get("maxLowerShadowPct"),
        "priorGapDown60Pct": rules.get("maxPriorGapDown60Pct"),
        "priorGapDown3Count60": rules.get("maxPriorGapDown3Count60"),
        "priorGapDown5Count60": rules.get("maxPriorGapDown5Count60"),
    }
    for key, limit in limits.items():
        if limit is not None and metrics[key] > float(limit):
            return False, metrics, key
    return True, metrics, None


def limit_up_entry_blocked(row: dict[str, Any], prev: dict[str, Any] | None, cfg: dict[str, Any]) -> bool:
    threshold = cfg.get("limitUpEntryBlockPct")
    if threshold is None or not prev or not prev.get("close"):
        return False
    return row["close"] / prev["close"] - 1 >= float(threshold)


def entry_risk_metrics(row: dict[str, Any], prev: dict[str, Any] | None) -> dict[str, float]:
    close = float(row["close"])
    open_price = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    entry_range = (high - low) / close if close else 0.0
    upper_shadow = (high - max(open_price, close)) / close if close else 0.0
    lower_shadow = (min(open_price, close) - low) / close if close else 0.0
    gap = open_price / prev["close"] - 1 if prev and prev.get("close") else 0.0
    intraday = close / open_price - 1 if open_price else 0.0
    return {
        "entryRangePct": entry_range,
        "upperShadowPct": upper_shadow,
        "lowerShadowPct": lower_shadow,
        "gapPct": gap,
        "intradayReturnPct": intraday,
        "priorGapDown60Pct": abs(float(row["minPriorGap60Pct"])) if finite(row.get("minPriorGap60Pct")) and row["minPriorGap60Pct"] < 0 else 0.0,
        "priorGapDown3Count60": float(row.get("priorGapDown3Count60") or 0),
        "priorGapDown5Count60": float(row.get("priorGapDown5Count60") or 0),
    }


def signal_score(row: dict[str, Any], portfolio_rules: dict[str, Any]) -> float:
    volume_ratio = row["volume"] / row["volMa"] if row.get("volume") and finite(row.get("volMa")) and row["volMa"] else 0
    fast_gap = row["close"] / row["trendFastMa"] - 1 if finite(row.get("trendFastMa")) and row["trendFastMa"] else 0
    slow_gap = row["trendFastMa"] / row["trendSlowMa"] - 1 if finite(row.get("trendSlowMa")) and row["trendSlowMa"] else 0
    if portfolio_rules.get("entryPriority") == "market_breadth_then_trend_quality":
        long_gap = row["close"] / row["trendLongMa"] - 1 if finite(row.get("trendLongMa")) and row["trendLongMa"] else 0
        ma20_gap = row["close"] / row["ma20"] - 1 if finite(row.get("ma20")) and row["ma20"] else 0
        overextension_penalty = max(0, ma20_gap - 0.08) * 20
        return min(volume_ratio, 2.5) + max(0, fast_gap) * 8 + max(0, slow_gap) * 12 + max(0, long_gap) * 6 - overextension_penalty
    return volume_ratio + fast_gap * 10 + slow_gap * 8


def apply_cross_section_strength_scores(signals: list[dict[str, Any]], symbol_failure_counts: dict[str, int], portfolio_rules: dict[str, Any]) -> None:
    if not signals:
        return
    ranks = {
        "return20": percentile_ranks(signals, lambda item: item["row"].get("return20")),
        "return60": percentile_ranks(signals, lambda item: item["row"].get("return60")),
        "high60": percentile_ranks(signals, lambda item: item["row"].get("distanceFromHigh60Pct")),
        "recovery20": percentile_ranks(signals, lambda item: item["row"].get("recoveryFromLow20Pct")),
        "volume": percentile_ranks(signals, lambda item: volume_ratio(item["row"])),
    }
    weights = portfolio_rules.get("crossSectionScoreWeights") or {}
    for index, signal in enumerate(signals):
        base = float(signal["score"])
        penalty = symbol_failure_counts.get(signal["stock"]["ts_code"], 0) * float(portfolio_rules["repeatFailureScorePenalty"])
        score = (
            ranks["return20"][index] * float(weights.get("return20", 3.0))
            + ranks["return60"][index] * float(weights.get("return60", 2.0))
            + ranks["high60"][index] * float(weights.get("high60", 1.5))
            + ranks["recovery20"][index] * float(weights.get("recovery20", 1.0))
            + ranks["volume"][index] * float(weights.get("volume", 0.5))
            + base * float(weights.get("base", 0.25))
            - penalty
        )
        entry_risk_penalty, entry_risk_penalty_parts = calc_entry_score_penalty(signal.get("riskMetrics") or {}, portfolio_rules)
        score -= entry_risk_penalty
        signal["score"] = score
        signal["scoreParts"] = {
            "return20Rank": ranks["return20"][index],
            "return60Rank": ranks["return60"][index],
            "high60Rank": ranks["high60"][index],
            "recovery20Rank": ranks["recovery20"][index],
            "volumeRank": ranks["volume"][index],
            "baseScore": base,
            "failurePenalty": penalty,
            "entryRiskPenalty": entry_risk_penalty,
            **entry_risk_penalty_parts,
            "knownSymbolBonus": float(portfolio_rules["knownSymbolScoreBonus"]) if signal.get("knownSymbol") else 0.0,
        }


def calc_entry_score_penalty(risk_metrics: dict[str, float], portfolio_rules: dict[str, Any]) -> tuple[float, dict[str, float]]:
    rules = portfolio_rules.get("entryScorePenalty") or {}
    penalty = 0.0
    parts = {
        "entryGapScorePenalty": 0.0,
        "entryRangeScorePenalty": 0.0,
    }
    gap_threshold = rules.get("gapThresholdPct")
    if gap_threshold is not None and float(risk_metrics.get("gapPct") or 0) > float(gap_threshold):
        parts["entryGapScorePenalty"] = float(rules.get("gapPenalty") or 0)
        penalty += parts["entryGapScorePenalty"]
    range_threshold = rules.get("rangeThresholdPct")
    if range_threshold is not None and float(risk_metrics.get("entryRangePct") or 0) > float(range_threshold):
        parts["entryRangeScorePenalty"] = float(rules.get("rangePenalty") or 0)
        penalty += parts["entryRangeScorePenalty"]
    return penalty, parts


def percentile_ranks(items: list[dict[str, Any]], value_fn: Any) -> list[float]:
    values = [float(value_fn(item)) if finite(value_fn(item)) else float("-inf") for item in items]
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    if len(values) == 1:
        ranks[0] = 1.0 if values[0] != float("-inf") else 0.0
        return ranks
    for rank, index in enumerate(order):
        ranks[index] = rank / (len(values) - 1) if values[index] != float("-inf") else 0.0
    return ranks


def volume_ratio(row: dict[str, Any]) -> float:
    return row["volume"] / row["volMa"] if row.get("volume") and finite(row.get("volMa")) and row["volMa"] else 0.0


def prepare_pending_entries(signals: list[dict[str, Any]], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = []
    for signal in signals:
        pending.append(
            {
                **signal,
                "signalDate": signal["row"]["date"],
                "signalClose": signal["row"]["close"],
                "signalStop": calc_stop_price(signal["row"], cfg),
            }
        )
    return pending


def execute_pending_entries(
    cash: float,
    pending_entries: list[dict[str, Any]],
    rows_by_code: dict[str, dict[str, Any]],
    positions: dict[str, dict[str, Any]],
    trades: list[dict[str, Any]],
    portfolio_rules: dict[str, Any],
    cfg: dict[str, Any],
    market_stats: dict[str, int | float],
    weekly_buys: dict[str, int],
    week_key: str,
) -> float:
    buy_slots = int(portfolio_rules["maxPositions"]) - len(positions)
    weekly_remaining = int(portfolio_rules["weeklyBuyLimit"]) - weekly_buys.get(week_key, 0)
    for pending in pending_entries:
        if buy_slots <= 0 or weekly_remaining <= 0:
            break
        ts_code = pending["stock"]["ts_code"]
        if ts_code in positions:
            continue
        item = rows_by_code.get(ts_code)
        if not item:
            market_stats["nextOpenEntryCancels"] += 1
            continue
        open_price = float(item["row"]["open"])
        if next_open_entry_cancelled(open_price, pending, cfg):
            market_stats["nextOpenEntryCancels"] += 1
            continue
        equity_before_entry = portfolio_equity(cash, positions)
        quantity, stop = size_position(
            item["row"],
            cash,
            equity_before_entry,
            positions,
            pending["stock"],
            portfolio_rules,
            cfg,
            base_entry_price=open_price,
            stop_override=float(pending["signalStop"]),
        )
        if quantity <= 0:
            continue
        quantity = cap_quantity_for_entry_size_haircut(quantity, pending.get("riskMetrics") or {}, portfolio_rules, cfg, market_stats)
        if quantity <= 0:
            continue
        quantity = cap_quantity_for_overnight_budget(
            quantity,
            execution_buy_price(open_price, cfg),
            equity_before_entry,
            positions,
            portfolio_rules,
            cfg,
            market_stats,
        )
        if quantity <= 0:
            continue
        execution_signal = {
            **pending,
            "row": item["row"],
            "signalDate": pending["signalDate"],
            "reason": f"{pending['reason']}；次日开盘执行",
        }
        cash = execute_buy(
            cash,
            execution_signal,
            quantity,
            stop,
            trades,
            positions,
            cfg,
            market_stats,
            base_price_override=open_price,
            price_rule="next_open_with_buy_slippage",
            portfolio_equity_before_entry=equity_before_entry,
        )
        weekly_buys[week_key] = weekly_buys.get(week_key, 0) + 1
        weekly_remaining -= 1
        buy_slots -= 1
    return cash


def next_open_entry_cancelled(open_price: float, pending: dict[str, Any], cfg: dict[str, Any]) -> bool:
    signal_close = float(pending["signalClose"])
    signal_stop = float(pending["signalStop"])
    if open_price <= signal_stop:
        return True
    cancel_gap = cfg.get("nextOpenCancelGapDownPct")
    return cancel_gap is not None and open_price / signal_close - 1 <= -float(cancel_gap)


def cap_quantity_for_entry_size_haircut(
    quantity: int,
    risk_metrics: dict[str, float],
    portfolio_rules: dict[str, Any],
    cfg: dict[str, Any],
    market_stats: dict[str, int | float],
) -> int:
    rules = portfolio_rules.get("entrySizeHaircut") or {}
    haircut = 0.0
    checks = [
        ("gapPct", "gapThresholdPct", "gapHaircutPct"),
        ("entryRangePct", "rangeThresholdPct", "rangeHaircutPct"),
        ("intradayReturnPct", "intradayThresholdPct", "intradayHaircutPct"),
    ]
    for metric_key, threshold_key, haircut_key in checks:
        threshold = rules.get(threshold_key)
        if threshold is not None and float(risk_metrics.get(metric_key) or 0) > float(threshold):
            haircut = max(haircut, float(rules.get(haircut_key) or 0))
    if haircut <= 0:
        return quantity

    capped_quantity = round_to_lot(floor(quantity * (1 - haircut)), int(cfg["lotSize"]))
    capped_quantity = min(quantity, capped_quantity)
    if capped_quantity >= quantity:
        return quantity
    market_stats["entrySizeHaircutReducedEntries"] += 1
    market_stats["entrySizeHaircutReductionShares"] += quantity - max(capped_quantity, 0)
    return max(capped_quantity, 0)


def cap_quantity_for_overnight_budget(
    quantity: int,
    entry_price: float,
    equity: float,
    positions: dict[str, dict[str, Any]],
    portfolio_rules: dict[str, Any],
    cfg: dict[str, Any],
    market_stats: dict[str, int | float],
) -> int:
    cap = portfolio_rules.get("maxOvernightExposurePct")
    if cap is None:
        return quantity
    remaining_value = equity * float(cap) - position_exposure(positions)
    max_quantity = round_to_lot(floor(max(remaining_value, 0) / entry_price), int(cfg["lotSize"]))
    capped_quantity = min(quantity, max_quantity)
    if capped_quantity >= quantity:
        return quantity
    if capped_quantity <= 0:
        market_stats["blockedOvernightBudgetSignals"] += 1
        return 0
    market_stats["overnightBudgetReducedEntries"] += 1
    market_stats["overnightBudgetReductionShares"] += quantity - capped_quantity
    return capped_quantity


def size_position(
    row: dict[str, Any],
    cash: float,
    equity: float,
    positions: dict[str, dict[str, Any]],
    stock: dict[str, Any],
    portfolio_rules: dict[str, Any],
    cfg: dict[str, Any],
    base_entry_price: float | None = None,
    stop_override: float | None = None,
) -> tuple[int, float]:
    stop = stop_override if stop_override is not None else calc_stop_price(row, cfg)
    entry_price = execution_buy_price(base_entry_price if base_entry_price is not None else row["close"], cfg)
    risk_per_share = max(entry_price - stop, 0.01)
    risk_sized = floor((equity * float(cfg["riskPct"])) / risk_per_share)
    cap_sized = floor((equity * float(portfolio_rules["maxSinglePositionPct"])) / entry_price)
    affordable = floor(cash / (entry_price * (1 + float(cfg["commissionPct"]))))
    industry_budget = equity * float(portfolio_rules["maxIndustryPositionPct"]) - industry_exposure(positions, stock.get("industry"))
    industry_sized = floor(max(industry_budget, 0) / entry_price)
    quantity = round_to_lot(min(risk_sized, cap_sized, affordable, industry_sized), int(cfg["lotSize"]))
    return quantity, stop


def execute_buy(
    cash: float,
    signal: dict[str, Any],
    quantity: int,
    stop: float,
    trades: list[dict[str, Any]],
    positions: dict[str, dict[str, Any]],
    cfg: dict[str, Any],
    market_stats: dict[str, int | float],
    base_price_override: float | None = None,
    price_rule: str = "close_with_buy_slippage",
    portfolio_equity_before_entry: float | None = None,
) -> float:
    row = signal["row"]
    stock = signal["stock"]
    base_price = row["close"] if base_price_override is None else base_price_override
    price = execution_buy_price(base_price, cfg)
    market_stats["estimatedSlippageCost"] += max(0.0, price - base_price) * quantity
    gross = price * quantity
    fee = gross * float(cfg["commissionPct"])
    cash -= gross + fee
    positions[stock["ts_code"]] = {
        "ts_code": stock["ts_code"],
        "name": stock["name"],
        "industry": stock.get("industry"),
        "entryDate": row["date"],
        "entryPrice": price,
        "stopPrice": stop,
        "shares": quantity,
        "initialShares": quantity,
        "entryGross": gross,
        "entryFee": fee,
        "entryCost": gross + fee,
        "realizedGross": 0.0,
        "realizedFees": 0.0,
        "entryEquity": portfolio_equity_before_entry,
        "partialTaken": False,
        "lastPrice": row["close"],
        "entryLow": row["low"],
        "entryScore": signal["score"],
        "entryScoreParts": signal.get("scoreParts"),
        "entryRiskMetrics": signal.get("riskMetrics"),
        "barsHeld": 0,
        "commissionPct": float(cfg["commissionPct"]),
        "stampDutyPct": float(cfg["stampDutyPct"]),
    }
    trades.append(
        {
            "date": row["date"],
            "ts_code": stock["ts_code"],
            "name": stock["name"],
            "action": "买入",
            "price": price,
            "quantity": quantity,
            "cash": cash,
            "reason": signal["reason"],
            "fee": fee,
            "basePrice": base_price,
            "priceRule": price_rule,
            "signalDate": signal.get("signalDate"),
            "score": signal["score"],
            "scoreParts": signal.get("scoreParts"),
            "riskMetrics": signal.get("riskMetrics"),
        }
    )
    return cash


def execution_buy_price(base_price: float, cfg: dict[str, Any]) -> float:
    return max(0.01, float(base_price) * (1 + float(cfg.get("buySlippagePct", 0) or 0)))


def execution_sell_price(base_price: float, cfg: dict[str, Any]) -> float:
    return max(0.01, float(base_price) * (1 - float(cfg.get("sellSlippagePct", 0) or 0)))


def portfolio_equity(cash: float, positions: dict[str, dict[str, Any]]) -> float:
    return cash + sum(float(position["shares"]) * float(position["lastPrice"]) for position in positions.values())


def position_exposure(positions: dict[str, dict[str, Any]]) -> float:
    return sum(float(position["shares"]) * float(position["lastPrice"]) for position in positions.values())


def industry_exposure(positions: dict[str, dict[str, Any]], industry: str | None) -> float:
    return sum(float(position["shares"]) * float(position["lastPrice"]) for position in positions.values() if position.get("industry") == industry)


def concentration_metrics(equity: float, positions: dict[str, dict[str, Any]]) -> tuple[float, float]:
    if equity <= 0 or not positions:
        return 0.0, 0.0
    values = [float(position["shares"]) * float(position["lastPrice"]) for position in positions.values()]
    industry_values: dict[str, float] = defaultdict(float)
    for position in positions.values():
        industry_values[str(position.get("industry") or "未知")] += float(position["shares"]) * float(position["lastPrice"])
    return max(values) / equity, max(industry_values.values()) / equity


def build_summary(
    initial_cash: float,
    cash: float,
    positions: dict[str, dict[str, Any]],
    trades: list[dict[str, Any]],
    completed_trades: list[dict[str, Any]],
    equity_curve: list[dict[str, Any]],
    max_single_position_pct: float,
    max_industry_position_pct: float,
    market_states: dict[str, dict[str, Any]],
    market_stats: dict[str, int],
    throttle_stats: dict[str, int],
    portfolio_rules: dict[str, Any],
) -> dict[str, Any]:
    final_equity = portfolio_equity(cash, positions)
    total_return = final_equity / initial_cash - 1 if initial_cash else 0
    peak = initial_cash
    max_drawdown = 0.0
    for point in equity_curve:
        equity = float(point["equity"])
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = min(max_drawdown, equity / peak - 1)
    returns = [float(trade["returnPct"]) for trade in completed_trades]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    average_win = mean(wins) if wins else 0
    average_loss = mean(losses) if losses else 0
    risk_stats = calc_equity_performance_stats(equity_curve, initial_cash)
    risk_on_days = sum(1 for state in market_states.values() if state["riskOn"])
    risk_off_days = len(market_states) - risk_on_days
    return {
        "initialCash": initial_cash,
        "finalEquity": final_equity,
        "cash": cash,
        "totalReturn": total_return,
        "annualizedReturn": risk_stats["annualizedReturn"],
        "annualizedVolatility": risk_stats["annualizedVolatility"],
        "sharpeRatio": risk_stats["sharpeRatio"],
        "sortinoRatio": risk_stats["sortinoRatio"],
        "calmarRatio": risk_stats["calmarRatio"],
        "maxDrawdownDurationDays": risk_stats["maxDrawdownDurationDays"],
        "maxDrawdown": max_drawdown,
        "tradeCount": len(trades),
        "completedTradeCount": len(completed_trades),
        "winRate": len(wins) / len(completed_trades) if completed_trades else 0,
        "averageWin": average_win,
        "averageLoss": average_loss,
        "profitLossRatio": average_win / abs(average_loss) if average_loss else None,
        "profitFactor": sum(wins) / abs(sum(losses)) if losses else None,
        "maxConcurrentPositions": max((int(point["positions"]) for point in equity_curve), default=0),
        "maxSinglePositionPct": max_single_position_pct,
        "maxIndustryPositionPct": max_industry_position_pct,
        "maxOvernightExposurePct": portfolio_rules.get("maxOvernightExposurePct"),
        "marketRiskOnDays": risk_on_days,
        "marketRiskOffDays": risk_off_days,
        "blockedMarketDays": int(market_stats["blockedMarketDays"]),
        "blockedMarketSignals": int(market_stats["blockedMarketSignals"]),
        "blockedRiskSignals": int(market_stats["blockedRiskSignals"]),
        "blockedRiskReasons": {key: int(value) for key, value in dict(market_stats.get("blockedRiskReasons", {})).items()},
        "blockedLimitUpSignals": int(market_stats["blockedLimitUpSignals"]),
        "stopGapFillEvents": int(market_stats["stopGapFillEvents"]),
        "estimatedSlippageCost": float(market_stats["estimatedSlippageCost"]),
        "nextOpenEntryOrders": int(market_stats["nextOpenEntryOrders"]),
        "nextOpenEntryCancels": int(market_stats["nextOpenEntryCancels"]),
        "earlyExitEvents": int(market_stats["earlyExitEvents"]),
        "gapStopMarketCooldownEvents": int(market_stats["gapStopMarketCooldownEvents"]),
        "gapStopIndustryCooldownEvents": int(market_stats["gapStopIndustryCooldownEvents"]),
        "gapStopSymbolCooldownEvents": int(market_stats["gapStopSymbolCooldownEvents"]),
        "blockedGapStopMarketCooldownSignals": int(market_stats["blockedGapStopMarketCooldownSignals"]),
        "blockedGapStopIndustryCooldownSignals": int(market_stats["blockedGapStopIndustryCooldownSignals"]),
        "blockedGapStopSymbolCooldownSignals": int(market_stats["blockedGapStopSymbolCooldownSignals"]),
        "industryOvernightRiskActiveDays": int(market_stats["industryOvernightRiskActiveDays"]),
        "industryOvernightRiskActiveIndustryDays": int(market_stats["industryOvernightRiskActiveIndustryDays"]),
        "blockedIndustryOvernightRiskSignals": int(market_stats["blockedIndustryOvernightRiskSignals"]),
        "blockedOvernightBudgetSignals": int(market_stats["blockedOvernightBudgetSignals"]),
        "overnightBudgetReducedEntries": int(market_stats["overnightBudgetReducedEntries"]),
        "overnightBudgetReductionShares": int(market_stats["overnightBudgetReductionShares"]),
        "entrySizeHaircutReducedEntries": int(market_stats["entrySizeHaircutReducedEntries"]),
        "entrySizeHaircutReductionShares": int(market_stats["entrySizeHaircutReductionShares"]),
        "limitDownStopDelayEvents": int(market_stats["limitDownStopDelayEvents"]),
        "entryPriority": portfolio_rules["entryPriority"],
        "symbolCooldownEvents": int(throttle_stats["symbolCooldownEvents"]),
        "industryCooldownEvents": int(throttle_stats["industryCooldownEvents"]),
        "blockedSymbolCooldownSignals": int(throttle_stats["blockedSymbolCooldownSignals"]),
        "blockedIndustryCooldownSignals": int(throttle_stats["blockedIndustryCooldownSignals"]),
    }


def summarize_portfolio(result: dict[str, Any], context: dict[str, Any], source_analysis: dict[str, Any] | None) -> dict[str, Any]:
    summary = result["summary"]
    objective = context["objective"]
    portfolio_target = context["portfolio_target"]
    target_annualized_return = objective_target_annualized_return(objective)
    target_total_return = objective_target_total_return(objective)
    source_tail_met = source_analysis.get("tailRisk", {}).get("tailRiskMet") if source_analysis else None
    symbol_audit = summarize_portfolio_symbol_audit(result.get("completedTrades", []), context)
    diagnostic_gates = {
        "sourceSingleSymbolTailRiskMet": source_tail_met,
        "portfolioSymbolTailRiskMet": symbol_audit["tailRisk"]["tailRiskMet"],
        "portfolioSymbolTailRatioEvidenceMet": symbol_audit["tailRatioEvidence"]["ratioEvidenceMet"],
    }
    objective_gates = {
        "portfolioSymbolTailLossMet": symbol_audit["tailLossRisk"]["tailLossRiskMet"],
        "annualizedReturnMet": float(summary["annualizedReturn"]) >= target_annualized_return,
        "totalReturnMet": float(summary["totalReturn"]) >= target_total_return,
        "profitLossRatioMet": float(summary.get("profitLossRatio") or 0) >= float(objective["target_profit_loss_ratio"]),
        "maxDrawdownMet": abs(float(summary["maxDrawdown"])) <= float(objective["max_abs_drawdown"]),
        "minimumTradesMet": int(summary["completedTradeCount"]) >= int(portfolio_target.get("minimumCompletedTrades", 20)),
        "singleConcentrationMet": float(summary["maxSinglePositionPct"]) <= float(portfolio_target.get("maxSingleExposurePct", portfolio_target["maxSinglePositionPct"])) + 0.001,
        "industryConcentrationMet": float(summary["maxIndustryPositionPct"]) <= float(portfolio_target.get("maxIndustryExposurePct", portfolio_target["maxIndustryPositionPct"])) + 0.001,
    }
    gates = {**diagnostic_gates, **objective_gates}
    return {
        "targetMet": all(value is True for value in objective_gates.values()),
        "strictTargetMet": all(value is True for value in gates.values()),
        "objectiveGates": objective_gates,
        "diagnosticGates": diagnostic_gates,
        "targetCoreMetWithoutSparseTailRatio": all(value is True for value in objective_gates.values()),
        "gates": gates,
        "targetAnnualizedReturn": target_annualized_return,
        "targetTotalReturn": target_total_return,
        "totalReturn": summary["totalReturn"],
        "annualizedReturn": summary["annualizedReturn"],
        "annualizedVolatility": summary["annualizedVolatility"],
        "sharpeRatio": summary["sharpeRatio"],
        "sortinoRatio": summary["sortinoRatio"],
        "calmarRatio": summary["calmarRatio"],
        "maxDrawdownDurationDays": summary["maxDrawdownDurationDays"],
        "maxDrawdown": summary["maxDrawdown"],
        "profitLossRatio": summary["profitLossRatio"],
        "profitFactor": summary["profitFactor"],
        "winRate": summary["winRate"],
        "tradeCount": summary["tradeCount"],
        "completedTradeCount": summary["completedTradeCount"],
        "maxConcurrentPositions": summary["maxConcurrentPositions"],
        "maxSinglePositionPct": summary["maxSinglePositionPct"],
        "maxIndustryPositionPct": summary["maxIndustryPositionPct"],
        "maxOvernightExposurePct": summary["maxOvernightExposurePct"],
        "marketRiskOnDays": summary["marketRiskOnDays"],
        "marketRiskOffDays": summary["marketRiskOffDays"],
        "blockedMarketDays": summary["blockedMarketDays"],
        "blockedMarketSignals": summary["blockedMarketSignals"],
        "blockedRiskSignals": summary["blockedRiskSignals"],
        "blockedRiskReasons": summary.get("blockedRiskReasons", {}),
        "blockedLimitUpSignals": summary["blockedLimitUpSignals"],
        "stopGapFillEvents": summary["stopGapFillEvents"],
        "estimatedSlippageCost": summary["estimatedSlippageCost"],
        "nextOpenEntryOrders": summary["nextOpenEntryOrders"],
        "nextOpenEntryCancels": summary["nextOpenEntryCancels"],
        "earlyExitEvents": summary["earlyExitEvents"],
        "gapStopMarketCooldownEvents": summary["gapStopMarketCooldownEvents"],
        "gapStopIndustryCooldownEvents": summary["gapStopIndustryCooldownEvents"],
        "gapStopSymbolCooldownEvents": summary["gapStopSymbolCooldownEvents"],
        "blockedGapStopMarketCooldownSignals": summary["blockedGapStopMarketCooldownSignals"],
        "blockedGapStopIndustryCooldownSignals": summary["blockedGapStopIndustryCooldownSignals"],
        "blockedGapStopSymbolCooldownSignals": summary["blockedGapStopSymbolCooldownSignals"],
        "industryOvernightRiskActiveDays": summary["industryOvernightRiskActiveDays"],
        "industryOvernightRiskActiveIndustryDays": summary["industryOvernightRiskActiveIndustryDays"],
        "blockedIndustryOvernightRiskSignals": summary["blockedIndustryOvernightRiskSignals"],
        "blockedOvernightBudgetSignals": summary["blockedOvernightBudgetSignals"],
        "overnightBudgetReducedEntries": summary["overnightBudgetReducedEntries"],
        "overnightBudgetReductionShares": summary["overnightBudgetReductionShares"],
        "entrySizeHaircutReducedEntries": summary["entrySizeHaircutReducedEntries"],
        "entrySizeHaircutReductionShares": summary["entrySizeHaircutReductionShares"],
        "limitDownStopDelayEvents": summary["limitDownStopDelayEvents"],
        "entryPriority": summary["entryPriority"],
        "symbolCooldownEvents": summary["symbolCooldownEvents"],
        "industryCooldownEvents": summary["industryCooldownEvents"],
        "blockedSymbolCooldownSignals": summary["blockedSymbolCooldownSignals"],
        "blockedIndustryCooldownSignals": summary["blockedIndustryCooldownSignals"],
        "symbolAudit": symbol_audit,
        "tailCapitalRisk": symbol_audit["tailCapitalRisk"],
    }


def summarize_portfolio_symbol_audit(completed_trades: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    by_symbol: dict[str, dict[str, Any]] = {}
    for trade in completed_trades:
        ts_code = trade["ts_code"]
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
    rows = [summarize_symbol_returns(bucket) for bucket in by_symbol.values()]
    rows.sort(key=lambda item: item["totalReturn"], reverse=True)
    top = rows[:10]
    bottom = sorted(rows, key=lambda item: item["totalReturn"])[:10]
    capital_bottom = sorted(
        [row for row in rows if row.get("portfolioImpactPct") is not None],
        key=lambda item: item["portfolioImpactPct"],
    )[:10]
    return {
        "testedSymbols": len(rows),
        "top10": top,
        "bottom10": bottom,
        "capitalBottom10": capital_bottom,
        "tailRisk": summarize_tail_risk(bottom, context),
        "tailLossRisk": summarize_tail_loss_risk(bottom, context),
        "tailRatioEvidence": summarize_tail_ratio_evidence(bottom, context),
        "tailCapitalRisk": summarize_tail_capital_risk(capital_bottom),
    }


def summarize_tail_loss_risk(bottom: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    rules = context["evaluation"]
    max_abs_loss = float(rules.get("tail_max_abs_loss", rules.get("qualified_symbol_max_abs_drawdown", 0.1)))
    max_abs_drawdown = float(rules.get("tail_max_abs_drawdown", rules.get("qualified_symbol_max_abs_drawdown", 0.1)))
    expected_count = int(rules.get("tail_bottom_count", 10))
    loss_violations = [
        item
        for item in bottom
        if float(item.get("totalReturn") or 0) < -max_abs_loss
    ]
    drawdown_violations = [
        item
        for item in bottom
        if abs(float(item.get("maxDrawdown") or 0)) > max_abs_drawdown
    ]
    returns = [float(item.get("totalReturn") or 0) for item in bottom]
    drawdowns = [float(item.get("maxDrawdown") or 0) for item in bottom]
    return {
        "expectedCount": expected_count,
        "checkedCount": len(bottom),
        "tailLossRiskMet": len(bottom) >= expected_count and not loss_violations and not drawdown_violations,
        "thresholds": {
            "maxAbsLoss": max_abs_loss,
            "maxAbsDrawdown": max_abs_drawdown,
        },
        "worstReturn": min(returns) if returns else None,
        "worstDrawdown": min(drawdowns) if drawdowns else None,
        "lossViolationCount": len(loss_violations),
        "drawdownViolationCount": len(drawdown_violations),
    }


def summarize_tail_ratio_evidence(bottom: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    rules = context["evaluation"]
    expected_count = int(rules.get("tail_bottom_count", 10))
    min_trades = int(rules.get("tail_ratio_min_completed_trades", rules.get("minimum_completed_trades", 3)))
    min_profit_loss_ratio = float(rules.get("tail_min_profit_loss_ratio", rules.get("qualified_symbol_min_profit_loss_ratio", 0) or 0))
    eligible = [
        item
        for item in bottom
        if int(item.get("completedTrades") or item.get("tradeCount") or 0) >= min_trades
    ]
    thin_samples = [item for item in bottom if item not in eligible]
    ratio_violations = [
        item
        for item in eligible
        if min_profit_loss_ratio and float(item.get("profitLossRatio") or 0) < min_profit_loss_ratio
    ]
    ratios = [float(item.get("profitLossRatio") or 0) for item in eligible]
    return {
        "expectedCount": expected_count,
        "checkedCount": len(bottom),
        "minimumTradesForRatio": min_trades,
        "eligibleCount": len(eligible),
        "thinSampleCount": len(thin_samples),
        "ratioEvidenceMet": len(bottom) >= expected_count and not thin_samples and not ratio_violations,
        "thresholds": {
            "minProfitLossRatio": min_profit_loss_ratio,
            "minimumTradesForRatio": min_trades,
        },
        "minEligibleProfitLossRatio": min(ratios) if ratios else None,
        "profitLossRatioViolationCount": len(ratio_violations),
        "thinSampleSymbols": [item["ts_code"] for item in thin_samples],
    }


def summarize_tail_capital_risk(capital_bottom: list[dict[str, Any]]) -> dict[str, Any]:
    impacts = [float(item["portfolioImpactPct"]) for item in capital_bottom if item.get("portfolioImpactPct") is not None]
    capital_returns = [float(item["capitalReturnPct"]) for item in capital_bottom if item.get("capitalReturnPct") is not None]
    return {
        "checkedCount": len(capital_bottom),
        "worstPortfolioImpactPct": min(impacts) if impacts else None,
        "totalBottomPortfolioImpactPct": sum(impacts) if impacts else None,
        "worstCapitalReturnPct": min(capital_returns) if capital_returns else None,
    }


def summarize_symbol_returns(bucket: dict[str, Any]) -> dict[str, Any]:
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


def read_source_analysis(run_id: str) -> dict[str, Any] | None:
    if not run_id:
        return None
    path = RUNS_ROOT / run_id / "results.json"
    if not path.exists():
        return None
    return read_json(path).get("analysis")


def format_execution_stress(context: dict[str, Any]) -> str:
    stress = context.get("executionStress") or context.get("execution_stress") or {}
    if not stress:
        return "关闭"
    return (
        f"买入滑点 {float(stress.get('buySlippagePct') or 0):.2%}，"
        f"卖出滑点 {float(stress.get('sellSlippagePct') or 0):.2%}，"
        f"跳空止损开盘成交 {'是' if stress.get('stopGapFillAtOpen') else '否'}，"
        f"跌停附近止损延迟 {'是' if stress.get('limitDownStopDelay') else '否'}，"
        f"涨停附近买入阈值 {format_optional_percent(stress.get('limitUpEntryBlockPct'))}，"
        f"次日开盘执行 {'是' if stress.get('nextOpenEntry') else '否'}，"
        f"早期退出 {int(stress.get('earlyExitDays') or 0)}日/"
        f"{format_optional_percent(stress.get('earlyExitLossPct'))}/"
        f"破开仓低点 {'是' if stress.get('earlyExitEntryLowBreak') else '否'}，"
        f"跳空冷却 全市场{int(stress.get('gapStopMarketCooldownDays') or 0)}日/"
        f"行业{int(stress.get('gapStopIndustryCooldownDays') or 0)}日/"
        f"标的{int(stress.get('gapStopSymbolCooldownDays') or 0)}日，"
        f"行业隔夜风险 {int(stress.get('industryOvernightRiskWindowDays') or 0)}日/"
        f"{format_optional_percent(stress.get('industryOvernightRiskGapDownPct'))}/"
        f"count>={int(stress.get('industryOvernightRiskMinCount') or 0)}/"
        f"ratio>={format_optional_percent(stress.get('industryOvernightRiskMinRatio'))}"
    )


def objective_target_annualized_return(objective: dict[str, Any]) -> float:
    annualized = objective.get("target_annualized_return")
    if annualized is not None:
        return float(annualized)
    evaluation_years = float(objective.get("evaluation_window_years", 3) or 3)
    return (1 + float(objective["target_total_return"])) ** (1 / evaluation_years) - 1


def objective_target_total_return(objective: dict[str, Any]) -> float:
    annualized = objective.get("target_annualized_return")
    if annualized is not None:
        evaluation_years = float(objective.get("evaluation_window_years", 3) or 3)
        return (1 + float(annualized)) ** evaluation_years - 1
    return float(objective["target_total_return"])


def render_hypothesis(run_id: str, started_at: str, strategy: dict[str, Any], context: dict[str, Any], portfolio_rules: dict[str, Any], source_run: str, source_analysis: dict[str, Any] | None) -> str:
    source_text = "未提供逐标的前置结果"
    if source_analysis:
        source_text = f"`{source_run}`：targetMet={source_analysis.get('targetMet')}，tailRiskMet={source_analysis.get('tailRisk', {}).get('tailRiskMet')}"
    objective = context["objective"]
    target_annualized_return = objective_target_annualized_return(objective)
    target_total_return = objective_target_total_return(objective)
    return f"""# {run_id} 组合回测假设

- 时间：{started_at}
- 策略：{strategy["label"]} (`{strategy["name"]}`)
- 前置证据：{source_text}
- 组合假设：若入池过滤、买入排序、仓位和行业集中度约束能把单票信号转换为共享资金组合，则组合收益/回撤/盈亏比应比逐标的尾部更稳定。
- 组合参数：最大持仓 {portfolio_rules["maxPositions"]}，单票建仓上限 {portfolio_rules["maxSinglePositionPct"]:.1%}，单票观测暴露上限 {portfolio_rules["maxSingleExposurePct"]:.1%}，行业上限 {portfolio_rules["maxIndustryExposurePct"]:.1%}，隔夜持仓预算 {format_optional_percent(portfolio_rules.get("maxOvernightExposurePct"))}，每周最多新开仓 {portfolio_rules["weeklyBuyLimit"]}。
- 买入排序：{portfolio_rules["entryPriority"]}。
- 活跃标的上限：{portfolio_rules.get("maxDistinctSymbols")}，老标的加分 {portfolio_rules.get("knownSymbolScoreBonus")}。
- 信号阈值：min={portfolio_rules.get("minSignalScore")}，max={portfolio_rules.get("maxSignalScore")}。
- 成本压力：{context.get("costStress", {}).get("costMultiplier", 1.0)}x。
- 成交压力：{format_execution_stress(context)}。
- 失败节流：{"启用" if portfolio_rules["failureThrottle"]["enabled"] else "关闭"}，标的冷却 {portfolio_rules["failureThrottle"]["symbolCooldownDays"]} 天，行业周亏损阈值 {portfolio_rules["failureThrottle"]["industryWeeklyLossLimit"]}。
- 市场状态：{"启用" if portfolio_rules["marketBreadthFilter"]["enabled"] else "关闭"}，以前一交易日市场宽度决定是否允许新开仓。
- 目标收益：年化 >= {target_annualized_return:.2%}，按 {objective.get("evaluation_window_years", 3)} 年窗口折算总收益 >= {target_total_return:.2%}。
- 注意：目标完成门槛以组合年化收益、组合盈亏比、组合回撤、交易次数、集中度和成交标的尾部亏损/回撤为准；逐标的源审计和尾部盈亏比样本充分性作为诊断项单独报告。
"""


def render_review(run_id: str, started_at: str, strategy: dict[str, Any], analysis: dict[str, Any], result: dict[str, Any]) -> str:
    objective_gate_lines = "\n".join(f"- {key}：{value}" for key, value in analysis["objectiveGates"].items())
    diagnostic_gate_lines = "\n".join(f"- {key}：{value}" for key, value in analysis["diagnosticGates"].items())
    summary = result["summary"]
    symbol_audit = analysis["symbolAudit"]
    tail = symbol_audit["tailRisk"]
    tail_loss = symbol_audit["tailLossRisk"]
    tail_ratio = symbol_audit["tailRatioEvidence"]
    tail_capital = symbol_audit.get("tailCapitalRisk", {})
    symbol_top_lines = "\n".join(
        f"- `{item['ts_code']}` {item.get('name') or ''}：收益 {float(item['totalReturn']):.2%}，回撤 {float(item['maxDrawdown']):.2%}，盈亏比 {format_optional_ratio(item.get('profitLossRatio'))}，完成交易 {item['completedTrades']}"
        for item in symbol_audit["top10"]
    ) or "- 无"
    symbol_bottom_lines = "\n".join(
        f"- `{item['ts_code']}` {item.get('name') or ''}：收益 {float(item['totalReturn']):.2%}，回撤 {float(item['maxDrawdown']):.2%}，盈亏比 {format_optional_ratio(item.get('profitLossRatio'))}，完成交易 {item['completedTrades']}"
        for item in symbol_audit["bottom10"]
    ) or "- 无"
    return f"""# {run_id} 组合回测复盘

- 开始时间：{started_at}
- 结束时间：{now_text()}
- 策略：{strategy["label"]} (`{strategy["name"]}`)
- 结论：{"达标" if analysis["targetMet"] else "未达标"}

## 组合指标

- 总收益：{format_optional_percent(summary.get("totalReturn"))}
- 年化收益：{format_optional_percent(summary.get("annualizedReturn"))}
- 年化波动：{format_optional_percent(summary.get("annualizedVolatility"))}
- Sharpe：{format_optional_number(summary.get("sharpeRatio"))}
- Sortino：{format_optional_number(summary.get("sortinoRatio"))}
- Calmar：{format_optional_number(summary.get("calmarRatio"))}
- 最大回撤：{format_optional_percent(summary.get("maxDrawdown"))}
- 最长回撤持续：{summary.get("maxDrawdownDurationDays")} 天
- 盈亏比：{format_optional_ratio(summary.get("profitLossRatio"))}
- Profit factor：{format_optional_ratio(summary.get("profitFactor"))}
- 胜率：{format_optional_percent(summary.get("winRate"))}
- 交易动作数：{summary.get("tradeCount")}
- 完成交易数：{summary.get("completedTradeCount")}
- 最大同时持仓：{summary.get("maxConcurrentPositions")}
- 最大单票集中度：{format_optional_percent(summary.get("maxSinglePositionPct"))}
- 最大行业集中度：{format_optional_percent(summary.get("maxIndustryPositionPct"))}
- 隔夜持仓预算：{format_optional_percent(summary.get("maxOvernightExposurePct"))}
- 市场 Risk-On 天数：{summary.get("marketRiskOnDays")}
- 市场 Risk-Off 天数：{summary.get("marketRiskOffDays")}
- 市场过滤拦截天数：{summary.get("blockedMarketDays")}
- 市场过滤拦截信号数：{summary.get("blockedMarketSignals")}
- 买入风险过滤拦截信号数：{summary.get("blockedRiskSignals")}
- 涨停附近买入拦截信号数：{summary.get("blockedLimitUpSignals")}
- 跳空止损开盘成交次数：{summary.get("stopGapFillEvents")}
- 估算滑点成本：{summary.get("estimatedSlippageCost"):.2f}
- 次日开盘候选订单：{summary.get("nextOpenEntryOrders")}
- 次日开盘取消订单：{summary.get("nextOpenEntryCancels")}
- 早期弱势退出次数：{summary.get("earlyExitEvents")}
- 跳空止损全市场冷却事件/拦截：{summary.get("gapStopMarketCooldownEvents")} / {summary.get("blockedGapStopMarketCooldownSignals")}
- 跳空止损行业冷却事件/拦截：{summary.get("gapStopIndustryCooldownEvents")} / {summary.get("blockedGapStopIndustryCooldownSignals")}
- 跳空止损标的冷却事件/拦截：{summary.get("gapStopSymbolCooldownEvents")} / {summary.get("blockedGapStopSymbolCooldownSignals")}
- 行业隔夜风险活跃天数/行业日：{summary.get("industryOvernightRiskActiveDays")} / {summary.get("industryOvernightRiskActiveIndustryDays")}
- 行业隔夜风险拦截信号：{summary.get("blockedIndustryOvernightRiskSignals")}
- 隔夜预算拦截/缩量：{summary.get("blockedOvernightBudgetSignals")} / {summary.get("overnightBudgetReducedEntries")}
- 隔夜预算缩减股数：{summary.get("overnightBudgetReductionShares")}
- 入场风险缩仓次数/股数：{summary.get("entrySizeHaircutReducedEntries")} / {summary.get("entrySizeHaircutReductionShares")}
- 跌停附近止损延迟事件：{summary.get("limitDownStopDelayEvents")}
- 买入排序：{summary.get("entryPriority")}
- 标的冷却事件：{summary.get("symbolCooldownEvents")}
- 行业冷却事件：{summary.get("industryCooldownEvents")}
- 标的冷却拦截信号：{summary.get("blockedSymbolCooldownSignals")}
- 行业冷却拦截信号：{summary.get("blockedIndustryCooldownSignals")}

## 成交标的尾部审计

- 已成交标的数：{symbol_audit["testedSymbols"]}
- 收益后 10 严格审计：{"通过" if tail["tailRiskMet"] else "未通过"}
- 尾部亏损/回撤审计：{"通过" if tail_loss["tailLossRiskMet"] else "未通过"}
- 尾部盈亏比证据：{"通过" if tail_ratio["ratioEvidenceMet"] else "未通过"}
- 尾部最差收益：{format_optional_percent(tail.get("worstReturn"))}
- 尾部最深回撤：{format_optional_percent(tail.get("worstDrawdown"))}
- 尾部最低盈亏比：{format_optional_ratio(tail.get("minProfitLossRatio"))}
- 尾部违规：亏损 {tail["lossViolationCount"]}，回撤 {tail["drawdownViolationCount"]}，盈亏比 {tail["profitLossRatioViolationCount"]}
- 盈亏比样本：合格 {tail_ratio["eligibleCount"]}/{tail_ratio["checkedCount"]}，稀疏 {tail_ratio["thinSampleCount"]}，合格样本最低盈亏比 {format_optional_ratio(tail_ratio.get("minEligibleProfitLossRatio"))}
- 尾部资本影响最差：{format_optional_percent(tail_capital.get("worstPortfolioImpactPct"))}
- 尾部资本影响合计：{format_optional_percent(tail_capital.get("totalBottomPortfolioImpactPct"))}

### 成交标的收益前 10

{symbol_top_lines}

### 成交标的收益后 10

{symbol_bottom_lines}

## 门槛

### 目标硬门槛

{objective_gate_lines}

### 诊断项

{diagnostic_gate_lines}

## 解释

本轮使用共享资金、买入排序、最大持仓数、单票上限和行业上限，把逐标的信号转换为组合诊断。目标硬门槛用于判断是否可执行落地；诊断项用于解释边界，不直接否决组合目标。
"""


def render_next_input(run_id: str, strategy: dict[str, Any], analysis: dict[str, Any]) -> str:
    symbol_tail = analysis["symbolAudit"]["tailRisk"]
    symbol_tail_loss = analysis["symbolAudit"]["tailLossRisk"]
    symbol_tail_ratio = analysis["symbolAudit"]["tailRatioEvidence"]
    symbol_tail_capital = analysis["symbolAudit"].get("tailCapitalRisk", {})
    return f"""# 下一轮组合策略输入

上一轮：`{run_id}`，策略 `{strategy["name"]}`。

结论：{"达标" if analysis["targetMet"] else "未达标"}。
严格诊断：{"通过" if analysis.get("strictTargetMet") else "未通过"}。

关键数字：

- 组合总收益：{format_optional_percent(analysis.get("totalReturn"))}
- 年化收益：{format_optional_percent(analysis.get("annualizedReturn"))}
- 年化波动：{format_optional_percent(analysis.get("annualizedVolatility"))}
- Sharpe：{format_optional_number(analysis.get("sharpeRatio"))}
- Sortino：{format_optional_number(analysis.get("sortinoRatio"))}
- Calmar：{format_optional_number(analysis.get("calmarRatio"))}
- 最大回撤：{format_optional_percent(analysis.get("maxDrawdown"))}
- 最长回撤持续：{analysis.get("maxDrawdownDurationDays")} 天
- 盈亏比：{format_optional_ratio(analysis.get("profitLossRatio"))}
- 完成交易数：{analysis.get("completedTradeCount")}
- 最大单票集中度：{format_optional_percent(analysis.get("maxSinglePositionPct"))}
- 最大行业集中度：{format_optional_percent(analysis.get("maxIndustryPositionPct"))}
- 隔夜持仓预算：{format_optional_percent(analysis.get("maxOvernightExposurePct"))}
- 市场 Risk-Off 天数：{analysis.get("marketRiskOffDays")}
- 市场过滤拦截信号数：{analysis.get("blockedMarketSignals")}
- 买入风险过滤拦截信号数：{analysis.get("blockedRiskSignals")}
- 涨停附近买入拦截信号数：{analysis.get("blockedLimitUpSignals")}
- 跳空止损开盘成交次数：{analysis.get("stopGapFillEvents")}
- 估算滑点成本：{analysis.get("estimatedSlippageCost"):.2f}
- 次日开盘候选订单：{analysis.get("nextOpenEntryOrders")}
- 次日开盘取消订单：{analysis.get("nextOpenEntryCancels")}
- 早期弱势退出次数：{analysis.get("earlyExitEvents")}
- 跳空止损全市场冷却事件/拦截：{analysis.get("gapStopMarketCooldownEvents")} / {analysis.get("blockedGapStopMarketCooldownSignals")}
- 跳空止损行业冷却事件/拦截：{analysis.get("gapStopIndustryCooldownEvents")} / {analysis.get("blockedGapStopIndustryCooldownSignals")}
- 跳空止损标的冷却事件/拦截：{analysis.get("gapStopSymbolCooldownEvents")} / {analysis.get("blockedGapStopSymbolCooldownSignals")}
- 行业隔夜风险活跃天数/行业日：{analysis.get("industryOvernightRiskActiveDays")} / {analysis.get("industryOvernightRiskActiveIndustryDays")}
- 行业隔夜风险拦截信号：{analysis.get("blockedIndustryOvernightRiskSignals")}
- 隔夜预算拦截/缩量：{analysis.get("blockedOvernightBudgetSignals")} / {analysis.get("overnightBudgetReducedEntries")}
- 隔夜预算缩减股数：{analysis.get("overnightBudgetReductionShares")}
- 入场风险缩仓次数/股数：{analysis.get("entrySizeHaircutReducedEntries")} / {analysis.get("entrySizeHaircutReductionShares")}
- 跌停附近止损延迟事件：{analysis.get("limitDownStopDelayEvents")}
- 买入排序：{analysis.get("entryPriority")}
- 标的冷却事件：{analysis.get("symbolCooldownEvents")}
- 行业冷却事件：{analysis.get("industryCooldownEvents")}
- 标的冷却拦截信号：{analysis.get("blockedSymbolCooldownSignals")}
- 行业冷却拦截信号：{analysis.get("blockedIndustryCooldownSignals")}
- 成交标的后 10 严格审计：{"通过" if symbol_tail["tailRiskMet"] else "未通过"}
- 成交标的后 10 亏损/回撤审计：{"通过" if symbol_tail_loss["tailLossRiskMet"] else "未通过"}
- 成交标的后 10 盈亏比证据：{"通过" if symbol_tail_ratio["ratioEvidenceMet"] else "未通过"}
- 成交标的尾部最差收益：{format_optional_percent(symbol_tail.get("worstReturn"))}
- 成交标的尾部最深回撤：{format_optional_percent(symbol_tail.get("worstDrawdown"))}
- 成交标的尾部最低盈亏比：{format_optional_ratio(symbol_tail.get("minProfitLossRatio"))}
- 成交标的尾部盈亏比样本：合格 {symbol_tail_ratio["eligibleCount"]}/{symbol_tail_ratio["checkedCount"]}，稀疏 {symbol_tail_ratio["thinSampleCount"]}，合格样本最低盈亏比 {format_optional_ratio(symbol_tail_ratio.get("minEligibleProfitLossRatio"))}
- 成交标的尾部资本影响最差：{format_optional_percent(symbol_tail_capital.get("worstPortfolioImpactPct"))}
- 成交标的尾部资本影响合计：{format_optional_percent(symbol_tail_capital.get("totalBottomPortfolioImpactPct"))}

下一轮建议：

- 若横截面择强提高收益但回撤仍超标，先调冷却和行业节流，不要直接扩大仓位。
- 若横截面择强压低交易数或收益，检查排名因子是否过度偏向短线涨幅。
- 若后 10 亏损/回撤已受控但盈亏比证据未通过，先判断是样本稀疏导致不可判定，还是尾部标的被重复交易后仍然低质量；不要为了制造盈亏比而强制复用弱标的。
"""


def get_week_key(trade_date: str) -> str:
    year, week, _ = date.fromisoformat(trade_date).isocalendar()
    return f"{year}-W{week:02d}"


def format_optional_number(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


if __name__ == "__main__":
    main()
