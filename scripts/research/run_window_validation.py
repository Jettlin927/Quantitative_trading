from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import json_safe
from backend.app.database import SessionLocal
from backend.app.schemas import MarketBacktestRequest
from scripts.research.run_portfolio_backtest import (
    apply_cost_multiplier,
    apply_entry_risk_override,
    apply_entry_score_penalty_override,
    apply_entry_size_haircut_override,
    apply_execution_stress,
    apply_failure_throttle_override,
    apply_industry_state_filter_override,
    apply_cross_section_weight_override,
    apply_market_breadth_override,
    apply_market_breadth_soft_gate_override,
    apply_concept_cache_override,
    apply_moneyflow_cache_override,
    apply_portfolio_override,
    build_market_breadth_payload,
    build_portfolio_rules,
    objective_target_annualized_return,
    payload_to_dict,
    require_moneyflow_cache_for_enabled_weights,
    require_concept_cache_for_enabled_weights,
    run_portfolio_backtest,
    summarize_portfolio,
)
from scripts.research.run_research_round import (
    DEFAULT_CONTEXT_PATH,
    NEXT_BRIEF_PATH,
    RUNS_ROOT,
    build_market_payload,
    build_strategy,
    format_optional_percent,
    format_optional_ratio,
    now_text,
    read_json,
    write_json,
    write_text,
)

WARMUP_MONTHS = 3


def main() -> None:
    parser = argparse.ArgumentParser(description="Run fixed-parameter portfolio validation across time windows.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--strategy", default="trend-follow-maximum-profit-no-macd", help="Strategy preset from run_research_round.py.")
    parser.add_argument("--context", default=str(DEFAULT_CONTEXT_PATH), help="Research context JSON path.")
    parser.add_argument("--cost-multiplier", type=float, default=1.0, help="Multiply commission and stamp duty.")
    parser.add_argument("--max-single-position-pct", type=float, default=None, help="Override portfolio_target.maxSinglePositionPct.")
    parser.add_argument("--disable-market-breadth-filter", action="store_true", help="Disable the portfolio market breadth gate while keeping allowed entry dates.")
    parser.add_argument("--market-min-samples", type=int, default=None, help="Override portfolio_target.marketBreadthFilter.minSamples.")
    parser.add_argument("--market-min-above-ma20-pct", type=float, default=None, help="Override portfolio_target.marketBreadthFilter.minAboveMa20Pct.")
    parser.add_argument("--market-min-above-ma60-pct", type=float, default=None, help="Override portfolio_target.marketBreadthFilter.minAboveMa60Pct.")
    parser.add_argument("--market-min-up-pct", type=float, default=None, help="Override portfolio_target.marketBreadthFilter.minUpPct.")
    parser.add_argument("--market-soft-gate", action="store_true", help="Enable a default-off state-gated soft market-breadth entry experiment.")
    parser.add_argument("--market-soft-min-samples", type=int, default=None, help="Minimum samples required by the soft market-breadth gate.")
    parser.add_argument("--market-soft-min-above-ma20-pct", type=float, default=None, help="Minimum above-MA20 ratio required by the soft market-breadth gate.")
    parser.add_argument("--market-soft-min-above-ma60-pct", type=float, default=None, help="Minimum above-MA60 ratio required by the soft market-breadth gate.")
    parser.add_argument("--market-soft-min-up-pct", type=float, default=None, help="Minimum up ratio required by the soft market-breadth gate.")
    parser.add_argument("--market-soft-max-base-failed-checks", type=int, default=None, help="Maximum failed hard breadth checks allowed by the soft gate.")
    parser.add_argument("--cross-section-return20-weight", type=float, default=None, help="Override the return20-rank weight in cross-section strength scoring.")
    parser.add_argument("--cross-section-return60-weight", type=float, default=None, help="Override the return60-rank weight in cross-section strength scoring.")
    parser.add_argument("--cross-section-high60-weight", type=float, default=None, help="Override the high60-rank weight in cross-section strength scoring.")
    parser.add_argument("--cross-section-recovery20-weight", type=float, default=None, help="Override the recovery20-rank weight in cross-section strength scoring.")
    parser.add_argument("--cross-section-volume-weight", type=float, default=None, help="Override the volume-rank weight in cross-section strength scoring.")
    parser.add_argument("--cross-section-base-weight", type=float, default=None, help="Override the base score weight in cross-section strength scoring.")
    parser.add_argument("--cross-section-industry-return20-weight", type=float, default=None, help="Override the industry average 20-day return rank weight in cross-section strength scoring.")
    parser.add_argument("--cross-section-industry-relative-return20-weight", type=float, default=None, help="Override the stock-vs-industry 20-day return rank weight in cross-section strength scoring.")
    parser.add_argument("--cross-section-stock-specific-breakout-quality-weight", type=float, default=None, help="Override the stock-specific breakout quality rank weight.")
    parser.add_argument("--cross-section-stock-specific-mature-breadth-quality-weight", type=float, default=None, help="Override the stock-specific breakout rank weight gated by mature but non-euphoric market breadth.")
    parser.add_argument("--cross-section-macd-hist-weight", type=float, default=None, help="Override the MACD histogram rank weight in cross-section strength scoring.")
    parser.add_argument("--cross-section-macd-hist-delta-weight", type=float, default=None, help="Override the MACD histogram day-over-day improvement rank weight in cross-section strength scoring.")
    parser.add_argument("--cross-section-boll-squeeze-weight", type=float, default=None, help="Override the BOLL squeeze rank weight in cross-section strength scoring.")
    parser.add_argument("--cross-section-boll-position-weight", type=float, default=None, help="Override the BOLL position rank weight in cross-section strength scoring.")
    parser.add_argument("--cross-section-boll-position-balance-weight", type=float, default=None, help="Override the balanced BOLL position rank weight in cross-section strength scoring.")
    parser.add_argument("--cross-section-rsi-balance-weight", type=float, default=None, help="Override the RSI balance rank weight in cross-section strength scoring.")
    parser.add_argument("--cross-section-ma-alignment-weight", type=float, default=None, help="Override the MA alignment rank weight in cross-section strength scoring.")
    parser.add_argument("--cross-section-indicator-setup-weight", type=float, default=None, help="Override the MACD+BOLL+RSI setup rank weight in cross-section strength scoring.")
    parser.add_argument("--cross-section-indicator-pulse-quality-weight", type=float, default=None, help="Override the MACD+BOLL+RSI setup confirmed by high-60 quality and prior-gap stability.")
    parser.add_argument("--cross-section-indicator-confluence-quality-weight", type=float, default=None, help="Override the MACD+BOLL+RSI+MA+amount confluence rank weight.")
    parser.add_argument("--cross-section-indicator-turn-quality-weight", type=float, default=None, help="Override the MACD-turn+BOLL-position+RSI+gap-stability composite rank weight.")
    parser.add_argument("--cross-section-rsi-momentum-quality-weight", type=float, default=None, help="Override the RSI-level and MACD-improvement quality rank inside broad moneyflow surges.")
    parser.add_argument("--cross-section-rsi-momentum-confirmed-quality-weight", type=float, default=None, help="Override the RSI-level and MACD-improvement quality rank inside confirmed moneyflow surges.")
    parser.add_argument("--cross-section-turnover-rate-f-weight", type=float, default=None, help="Override the free-float turnover rank weight from daily_basic.")
    parser.add_argument("--cross-section-volume-ratio-basic-weight", type=float, default=None, help="Override the daily_basic volume ratio rank weight.")
    parser.add_argument("--cross-section-low-volume-ratio-basic-weight", type=float, default=None, help="Override the low daily_basic volume ratio rank weight.")
    parser.add_argument("--cross-section-small-circ-mv-weight", type=float, default=None, help="Override the small circulating market value rank weight from daily_basic.")
    parser.add_argument("--cross-section-prior-gap-stability-weight", type=float, default=None, help="Override the prior overnight gap stability rank weight in cross-section strength scoring.")
    parser.add_argument("--cross-section-amount-ratio-weight", type=float, default=None, help="Override the amount/20-day-average amount rank weight.")
    parser.add_argument("--cross-section-amount-efficiency20-weight", type=float, default=None, help="Override the 20-day return per amount-expansion rank weight.")
    parser.add_argument("--cross-section-amount-efficiency-rsi-weight", type=float, default=None, help="Override the amount-efficiency confirmed by RSI-balance rank weight.")
    parser.add_argument("--moneyflow-cache", default=None, help="Run-local JSONL moneyflow rank cache generated by build_moneyflow_cache.py.")
    parser.add_argument("--cross-section-moneyflow-main-rank1-weight", type=float, default=None, help="Override the previous-trading-day main moneyflow rank weight.")
    parser.add_argument("--cross-section-moneyflow-main-rank3-weight", type=float, default=None, help="Override the 3-day average main moneyflow rank weight.")
    parser.add_argument("--cross-section-moneyflow-main-rank5-weight", type=float, default=None, help="Override the 5-day average main moneyflow rank weight.")
    parser.add_argument("--cross-section-moneyflow-industry-confirm-weight", type=float, default=None, help="Override the industry-state-confirmed prior-day main moneyflow rank weight.")
    parser.add_argument("--cross-section-moneyflow-rsi-confirm-weight", type=float, default=None, help="Override the RSI-confirmed prior-day main moneyflow rank weight.")
    parser.add_argument("--cross-section-moneyflow-market-strong-weight", type=float, default=None, help="Override the strong-market-confirmed prior-day main moneyflow rank weight.")
    parser.add_argument("--cross-section-moneyflow-market-quality-weight", type=float, default=None, help="Override the market/rsi/industry-quality confirmed prior-day main moneyflow rank weight.")
    parser.add_argument("--cross-section-moneyflow-market-surge-quality-weight", type=float, default=None, help="Override the broad-upsurge/rsi/industry-quality confirmed prior-day main moneyflow rank weight.")
    parser.add_argument("--cross-section-moneyflow-market-surge-strict-quality-weight", type=float, default=None, help="Override the strict broad-upsurge moneyflow quality rank that ignores zero-strength ties.")
    parser.add_argument("--cross-section-moneyflow-market-surge-relative-quality-weight", type=float, default=None, help="Override the broad-upsurge/rsi/industry-relative-quality confirmed prior-day main moneyflow rank weight.")
    parser.add_argument("--cross-section-moneyflow-market-surge-confirmed-quality-weight", type=float, default=None, help="Override the broad-upsurge moneyflow rank only when RSI and stock-vs-industry strength pass confirmation floors.")
    parser.add_argument("--cross-section-industry-moneyflow-sum-net-rank1-weight", type=float, default=None, help="Override the prior-day industry aggregate main-moneyflow net rank weight.")
    parser.add_argument("--concept-cache", default=None, help="Run-local JSONL concept rank cache generated by build_concept_cache.py.")
    parser.add_argument("--cross-section-kpl-concept-count-rank1-weight", type=float, default=None, help="Override the prior-day KPL concept-count rank weight.")
    parser.add_argument("--max-entry-gap-pct", type=float, default=None, help="Override portfolio_target.entryRiskFilter.maxGapPct.")
    parser.add_argument("--min-entry-gap-pct", type=float, default=None, help="Override portfolio_target.entryRiskFilter.minGapPct.")
    parser.add_argument("--max-entry-range-pct", type=float, default=None, help="Override portfolio_target.entryRiskFilter.maxEntryRangePct.")
    parser.add_argument("--max-intraday-return-pct", type=float, default=None, help="Override portfolio_target.entryRiskFilter.maxIntradayReturnPct.")
    parser.add_argument("--entry-prior-volume-ratio-basic-score-penalty-threshold", type=float, default=None, help="Score-penalize entries whose prior-day daily_basic volume_ratio is above this threshold.")
    parser.add_argument("--entry-prior-volume-ratio-basic-score-penalty", type=float, default=0.0, help="Fixed score penalty applied above the prior-day daily_basic volume_ratio threshold.")
    parser.add_argument("--entry-volume-inefficiency-crowding-prior-volume-ratio-basic-threshold", type=float, default=None, help="Score-penalize entries with high prior-day volume_ratio and weak amount-efficiency/RSI rank.")
    parser.add_argument("--entry-volume-inefficiency-crowding-amount-efficiency-rsi-rank-max", type=float, default=None, help="Maximum amountEfficiencyRsi rank allowed for the volume-inefficiency crowding penalty.")
    parser.add_argument("--entry-volume-inefficiency-crowding-score-penalty", type=float, default=0.0, help="Fixed score penalty applied to high-volume but low-efficiency crowding.")
    parser.add_argument("--entry-industry-return-overheat-rank-threshold", type=float, default=None, help="Score-penalize entries whose industry 20-day return rank is above this threshold.")
    parser.add_argument("--entry-industry-return-overheat-score-penalty", type=float, default=0.0, help="Fixed score penalty applied to industry return overheat.")
    parser.add_argument("--entry-unsupported-boll-squeeze-boll-rank-threshold", type=float, default=None, help="Score-penalize BOLL squeeze entries lacking industry moneyflow support.")
    parser.add_argument("--entry-unsupported-boll-squeeze-industry-moneyflow-rank-max", type=float, default=None, help="Maximum industry moneyflow rank allowed for unsupported BOLL squeeze penalty.")
    parser.add_argument("--entry-unsupported-boll-squeeze-score-penalty", type=float, default=0.0, help="Fixed score penalty applied to unsupported BOLL squeeze.")
    parser.add_argument("--entry-industry-moneyflow-crowding-sum-rank-threshold", type=float, default=None, help="Score-penalize entries whose prior-day industry main-moneyflow sum rank is above this threshold.")
    parser.add_argument("--entry-industry-moneyflow-crowding-persistent-score-max", type=float, default=None, help="Score-penalize entries whose prior-day industry moneyflow persistence score is at or below this threshold.")
    parser.add_argument("--entry-industry-moneyflow-crowding-score-penalty", type=float, default=0.0, help="Fixed score penalty applied to high-sum but low-persistence industry moneyflow crowding.")
    parser.add_argument("--entry-moneyflow-surge-rsi-crowding-surge-rank-threshold", type=float, default=None, help="Score-penalize entries with high market-surge moneyflow rank.")
    parser.add_argument("--entry-moneyflow-surge-rsi-crowding-rsi-rank-threshold", type=float, default=None, help="Score-penalize entries with high RSI-balance rank inside market-surge moneyflow.")
    parser.add_argument("--entry-moneyflow-surge-rsi-crowding-gap-threshold-pct", type=float, default=None, help="Only apply the moneyflow+RSI crowding penalty when the entry gap is at least this high.")
    parser.add_argument("--entry-moneyflow-surge-rsi-crowding-range-threshold-pct", type=float, default=None, help="Only apply the moneyflow+RSI crowding penalty when the entry-day range is at least this high.")
    parser.add_argument("--entry-moneyflow-surge-rsi-crowding-score-penalty", type=float, default=0.0, help="Fixed score penalty applied to high market-surge moneyflow plus high RSI-balance crowding.")
    parser.add_argument("--entry-indicator-confluence-moneyflow-crowding-confluence-rank-threshold", type=float, default=None, help="Score-penalize entries with high indicator-confluence rank.")
    parser.add_argument("--entry-indicator-confluence-moneyflow-crowding-moneyflow5-rank-threshold", type=float, default=None, help="Score-penalize entries with high 5-day moneyflow rank inside high indicator confluence.")
    parser.add_argument("--entry-indicator-confluence-moneyflow-crowding-min-gap-pct", type=float, default=None, help="Only apply the indicator-confluence moneyflow crowding penalty when entry gap is at least this value.")
    parser.add_argument("--entry-indicator-confluence-moneyflow-crowding-score-penalty", type=float, default=0.0, help="Fixed score penalty applied to high indicator confluence plus high 5-day moneyflow crowding.")
    parser.add_argument("--entry-unconfirmed-gap-range-min-gap-pct", type=float, default=None, help="Score-penalize high-gap high-range entries lacking moneyflow-surge confirmation.")
    parser.add_argument("--entry-unconfirmed-gap-range-min-range-pct", type=float, default=None, help="Minimum entry-day range for the unconfirmed gap-range penalty.")
    parser.add_argument("--entry-unconfirmed-gap-range-max-surge-rank", type=float, default=None, help="Maximum moneyflow-surge rank allowed for the unconfirmed gap-range penalty.")
    parser.add_argument("--entry-unconfirmed-gap-range-score-penalty", type=float, default=0.0, help="Fixed score penalty applied to unconfirmed high-gap high-range entries.")
    parser.add_argument("--failure-symbol-cooldown-days", type=int, default=None, help="Override repeat-loss symbol cooldown days.")
    parser.add_argument("--failure-industry-weekly-loss-limit", type=int, default=None, help="Override weekly loss count that cools down an industry.")
    parser.add_argument("--failure-industry-cooldown-days", type=int, default=None, help="Override industry cooldown days after the weekly loss limit is reached.")
    parser.add_argument("--industry-state-filter", action="store_true", help="Enable pre-entry industry state filtering.")
    parser.add_argument("--industry-state-min-samples", type=int, default=None, help="Minimum same-day industry samples required by the industry state filter.")
    parser.add_argument("--industry-state-min-up-pct", type=float, default=None, help="Minimum same-day industry up ratio required by the industry state filter.")
    parser.add_argument("--industry-state-min-above-ma20-pct", type=float, default=None, help="Minimum same-day industry above-MA20 ratio required by the industry state filter.")
    parser.add_argument("--industry-state-min-above-ma60-pct", type=float, default=None, help="Minimum same-day industry above-MA60 ratio required by the industry state filter.")
    parser.add_argument("--industry-state-min-return20-pct", type=float, default=None, help="Minimum same-day industry average 20-day return required by the industry state filter.")
    parser.add_argument("--entry-gap-size-haircut-threshold-pct", type=float, default=None, help="Reduce entry size when the entry gap is above this threshold.")
    parser.add_argument("--entry-gap-size-haircut-pct", type=float, default=0.0, help="Position-size haircut applied above the entry gap threshold.")
    parser.add_argument("--entry-range-size-haircut-threshold-pct", type=float, default=None, help="Reduce entry size when the entry-day range is above this threshold.")
    parser.add_argument("--entry-range-size-haircut-pct", type=float, default=0.0, help="Position-size haircut applied above the entry range threshold.")
    parser.add_argument("--entry-intraday-size-haircut-threshold-pct", type=float, default=None, help="Reduce entry size when the intraday return is above this threshold.")
    parser.add_argument("--entry-intraday-size-haircut-pct", type=float, default=0.0, help="Position-size haircut applied above the intraday return threshold.")
    parser.add_argument("--slippage-pct", type=float, default=0.0, help="Apply the same buy/sell slippage to portfolio executions.")
    parser.add_argument("--buy-slippage-pct", type=float, default=None, help="Override buy-side slippage.")
    parser.add_argument("--sell-slippage-pct", type=float, default=None, help="Override sell-side slippage.")
    parser.add_argument("--stop-gap-fill-at-open", action="store_true", help="Fill stop exits at the open when the open gaps below the stop.")
    parser.add_argument("--limit-down-stop-delay", action="store_true", help="Delay gap-stop exits when the open is near the stock's limit-down price.")
    parser.add_argument("--limit-band-tolerance-pct", type=float, default=0.002, help="Tolerance around inferred daily price-limit bands.")
    args = parser.parse_args()

    context = read_json(Path(args.context))
    apply_cost_multiplier(context, args.cost_multiplier)
    apply_portfolio_override(context, args.max_single_position_pct)
    apply_market_breadth_override(
        context,
        args.disable_market_breadth_filter,
        args.market_min_samples,
        args.market_min_above_ma20_pct,
        args.market_min_above_ma60_pct,
        args.market_min_up_pct,
    )
    apply_market_breadth_soft_gate_override(
        context,
        args.market_soft_gate,
        args.market_soft_min_samples,
        args.market_soft_min_above_ma20_pct,
        args.market_soft_min_above_ma60_pct,
        args.market_soft_min_up_pct,
        args.market_soft_max_base_failed_checks,
    )
    apply_cross_section_weight_override(
        context,
        {
            "return20": args.cross_section_return20_weight,
            "return60": args.cross_section_return60_weight,
            "high60": args.cross_section_high60_weight,
            "recovery20": args.cross_section_recovery20_weight,
            "volume": args.cross_section_volume_weight,
            "base": args.cross_section_base_weight,
            "industryReturn20": args.cross_section_industry_return20_weight,
            "industryRelativeReturn20": args.cross_section_industry_relative_return20_weight,
            "stockSpecificBreakoutQuality": args.cross_section_stock_specific_breakout_quality_weight,
            "stockSpecificMatureBreadthQuality": args.cross_section_stock_specific_mature_breadth_quality_weight,
            "macdHist": args.cross_section_macd_hist_weight,
            "macdHistDelta": args.cross_section_macd_hist_delta_weight,
            "bollSqueeze": args.cross_section_boll_squeeze_weight,
            "bollPosition": args.cross_section_boll_position_weight,
            "bollPositionBalance": args.cross_section_boll_position_balance_weight,
            "rsiBalance": args.cross_section_rsi_balance_weight,
            "maAlignment": args.cross_section_ma_alignment_weight,
            "indicatorSetup": args.cross_section_indicator_setup_weight,
            "indicatorPulseQuality": args.cross_section_indicator_pulse_quality_weight,
            "indicatorConfluenceQuality": args.cross_section_indicator_confluence_quality_weight,
            "indicatorTurnQuality": args.cross_section_indicator_turn_quality_weight,
            "rsiMomentumQuality": args.cross_section_rsi_momentum_quality_weight,
            "rsiMomentumConfirmedQuality": args.cross_section_rsi_momentum_confirmed_quality_weight,
            "turnoverRateF": args.cross_section_turnover_rate_f_weight,
            "volumeRatioBasic": args.cross_section_volume_ratio_basic_weight,
            "lowVolumeRatioBasic": args.cross_section_low_volume_ratio_basic_weight,
            "smallCircMv": args.cross_section_small_circ_mv_weight,
            "priorGapStability": args.cross_section_prior_gap_stability_weight,
            "amountRatio": args.cross_section_amount_ratio_weight,
            "amountEfficiency20": args.cross_section_amount_efficiency20_weight,
            "amountEfficiencyRsi": args.cross_section_amount_efficiency_rsi_weight,
            "moneyflowMainNetRank1": args.cross_section_moneyflow_main_rank1_weight,
            "moneyflowMainNetRank3": args.cross_section_moneyflow_main_rank3_weight,
            "moneyflowMainNetRank5": args.cross_section_moneyflow_main_rank5_weight,
            "moneyflowIndustryConfirm": args.cross_section_moneyflow_industry_confirm_weight,
            "moneyflowRsiConfirm": args.cross_section_moneyflow_rsi_confirm_weight,
            "moneyflowMarketStrong": args.cross_section_moneyflow_market_strong_weight,
            "moneyflowMarketQuality": args.cross_section_moneyflow_market_quality_weight,
            "moneyflowMarketSurgeQuality": args.cross_section_moneyflow_market_surge_quality_weight,
            "moneyflowMarketSurgeStrictQuality": args.cross_section_moneyflow_market_surge_strict_quality_weight,
            "moneyflowMarketSurgeRelativeQuality": args.cross_section_moneyflow_market_surge_relative_quality_weight,
            "moneyflowMarketSurgeConfirmedQuality": args.cross_section_moneyflow_market_surge_confirmed_quality_weight,
            "industryMoneyflowSumNetRank1": args.cross_section_industry_moneyflow_sum_net_rank1_weight,
            "kplConceptCountRank1": args.cross_section_kpl_concept_count_rank1_weight,
        },
    )
    apply_moneyflow_cache_override(context, args.moneyflow_cache)
    apply_concept_cache_override(context, args.concept_cache)
    require_moneyflow_cache_for_enabled_weights(context)
    require_concept_cache_for_enabled_weights(context)
    apply_entry_risk_override(context, args.max_entry_gap_pct, args.min_entry_gap_pct, args.max_entry_range_pct, args.max_intraday_return_pct)
    apply_entry_score_penalty_override(
        context,
        None,
        0.0,
        None,
        0.0,
        args.entry_prior_volume_ratio_basic_score_penalty_threshold,
        args.entry_prior_volume_ratio_basic_score_penalty,
        args.entry_volume_inefficiency_crowding_prior_volume_ratio_basic_threshold,
        args.entry_volume_inefficiency_crowding_amount_efficiency_rsi_rank_max,
        args.entry_volume_inefficiency_crowding_score_penalty,
        args.entry_industry_return_overheat_rank_threshold,
        args.entry_industry_return_overheat_score_penalty,
        args.entry_unsupported_boll_squeeze_boll_rank_threshold,
        args.entry_unsupported_boll_squeeze_industry_moneyflow_rank_max,
        args.entry_unsupported_boll_squeeze_score_penalty,
        args.entry_industry_moneyflow_crowding_sum_rank_threshold,
        args.entry_industry_moneyflow_crowding_persistent_score_max,
        args.entry_industry_moneyflow_crowding_score_penalty,
        args.entry_moneyflow_surge_rsi_crowding_surge_rank_threshold,
        args.entry_moneyflow_surge_rsi_crowding_rsi_rank_threshold,
        args.entry_moneyflow_surge_rsi_crowding_gap_threshold_pct,
        args.entry_moneyflow_surge_rsi_crowding_range_threshold_pct,
        args.entry_moneyflow_surge_rsi_crowding_score_penalty,
        args.entry_indicator_confluence_moneyflow_crowding_confluence_rank_threshold,
        args.entry_indicator_confluence_moneyflow_crowding_moneyflow5_rank_threshold,
        args.entry_indicator_confluence_moneyflow_crowding_min_gap_pct,
        args.entry_indicator_confluence_moneyflow_crowding_score_penalty,
        args.entry_unconfirmed_gap_range_min_gap_pct,
        args.entry_unconfirmed_gap_range_min_range_pct,
        args.entry_unconfirmed_gap_range_max_surge_rank,
        args.entry_unconfirmed_gap_range_score_penalty,
    )
    apply_failure_throttle_override(
        context,
        args.failure_symbol_cooldown_days,
        args.failure_industry_weekly_loss_limit,
        args.failure_industry_cooldown_days,
    )
    apply_industry_state_filter_override(
        context,
        args.industry_state_filter,
        args.industry_state_min_samples,
        args.industry_state_min_up_pct,
        args.industry_state_min_above_ma20_pct,
        args.industry_state_min_above_ma60_pct,
        args.industry_state_min_return20_pct,
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
    apply_execution_stress(
        context,
        args.slippage_pct,
        args.buy_slippage_pct,
        args.sell_slippage_pct,
        args.stop_gap_fill_at_open,
        args.limit_down_stop_delay,
        args.limit_band_tolerance_pct,
        None,
        False,
        None,
        0,
        None,
        False,
        0,
        0,
        0,
        0,
        0.03,
        0,
        0.0,
    )
    windows = [with_warmup_window(window) for window in build_validation_windows(context)]
    run_dir = RUNS_ROOT / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    started_at = now_text()

    window_results = []
    with SessionLocal() as db:
        for window in windows:
            window_start = date.fromisoformat(window["startDate"])
            window_context = deepcopy(context)
            window_context["scope"]["start_date"] = window["warmupStartDate"]
            window_context["scope"]["end_date"] = window["endDate"]
            filter_allowed_entry_dates(window_context, window_start, date.fromisoformat(window["endDate"]))
            strategy = build_strategy(args.strategy, window_context)
            portfolio_rules = build_portfolio_rules(window_context, strategy["config"])
            payload = MarketBacktestRequest(**build_market_payload(window_context, strategy, max_stocks=None))
            market_state_payload = build_market_breadth_payload(window_context, strategy)
            result = run_portfolio_backtest(
                db,
                payload,
                strategy["config"],
                portfolio_rules,
                evaluation_start_date=window_start,
                market_state_payload=market_state_payload,
            )
            analysis = summarize_portfolio(result, window_context, source_analysis=None)
            window_results.append(
                {
                    "window": window,
                    "payload": payload_to_dict(payload),
                    "marketStatePayload": payload_to_dict(market_state_payload),
                    "analysis": analysis,
                    "segmentGates": segment_gates(analysis, window_context, window),
                    "result": result,
                }
            )

    strategy = build_strategy(args.strategy, context)
    portfolio_rules = build_portfolio_rules(context, strategy["config"])
    validation = summarize_windows(window_results, context)
    output = {
        "runId": args.run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "strategy": strategy,
        "portfolioRules": portfolio_rules,
        "context": context,
        "validation": validation,
        "windows": window_results,
    }
    write_json(run_dir / "context.json", context)
    write_json(run_dir / "strategies.json", {"selected": args.strategy, "strategy": strategy, "portfolioRules": portfolio_rules})
    write_json(run_dir / "results.json", json_safe(output))
    write_text(run_dir / "hypothesis.md", render_hypothesis(args.run_id, started_at, strategy, context, windows, portfolio_rules))
    write_text(run_dir / "review.md", render_review(args.run_id, started_at, strategy, validation, window_results))
    next_input = render_next_input(args.run_id, strategy, validation, window_results)
    write_text(run_dir / "next-input.md", next_input)
    write_text(NEXT_BRIEF_PATH, next_input)

    print(json.dumps({"runId": args.run_id, "validation": validation, "runDir": str(run_dir)}, ensure_ascii=False, indent=2))


def build_validation_windows(context: dict[str, Any]) -> list[dict[str, Any]]:
    scope = context["scope"]
    start = date.fromisoformat(scope["start_date"])
    end = date.fromisoformat(scope["end_date"])
    windows: list[dict[str, Any]] = []

    current = start
    index = 1
    while current < end:
        window_end = min(add_months(current, 12), end)
        if (window_end - current).days >= 300:
            windows.append(window_payload(f"Y{index}", "annual", current, window_end))
        current = window_end
        index += 1

    current = start
    index = 1
    while True:
        window_end = add_months(current, 18)
        if window_end > end:
            break
        windows.append(window_payload(f"R18-{index}", "rolling18m", current, window_end))
        current = add_months(current, 6)
        index += 1

    return windows


def window_payload(label: str, kind: str, start: date, end: date) -> dict[str, Any]:
    return {
        "label": label,
        "kind": kind,
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "years": (end - start).days / 365.25,
    }


def with_warmup_window(window: dict[str, Any]) -> dict[str, Any]:
    window_start = date.fromisoformat(window["startDate"])
    warmup_start = add_months(window_start, -WARMUP_MONTHS)
    return {
        **window,
        "warmupStartDate": warmup_start.isoformat(),
        "warmupDays": (window_start - warmup_start).days,
        "warmupMonths": WARMUP_MONTHS,
    }


def filter_allowed_entry_dates(context: dict[str, Any], start: date, end: date) -> None:
    overrides = context.get("strategy_overrides")
    if not overrides or "allowedEntryDates" not in overrides:
        return
    start_text = start.isoformat()
    end_text = end.isoformat()
    overrides["allowedEntryDates"] = [
        entry_date
        for entry_date in overrides["allowedEntryDates"]
        if start_text <= str(entry_date) <= end_text
    ]


def add_months(value: date, months: int) -> date:
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    days_in_month = [31, 29 if is_leap_year(year) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(year, month, min(value.day, days_in_month[month - 1]))


def is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def segment_gates(analysis: dict[str, Any], context: dict[str, Any], window: dict[str, Any]) -> dict[str, Any]:
    objective = context["objective"]
    portfolio_target = context["portfolio_target"]
    tail_loss = analysis.get("symbolAudit", {}).get("tailLossRisk", {})
    evaluation_years = float(objective.get("evaluation_window_years", 3) or 3)
    min_annualized_return = objective_target_annualized_return(objective)
    years = float(window["years"])
    min_trades = max(5, round(int(portfolio_target.get("minimumCompletedTrades", 20)) * years / evaluation_years))
    gates = {
        "annualizedReturnMet": float(analysis.get("annualizedReturn") or 0) >= min_annualized_return,
        "sharpeMet": float(analysis.get("sharpeRatio") or 0) >= 1.0,
        "maxDrawdownMet": abs(float(analysis.get("maxDrawdown") or 0)) <= float(objective["max_abs_drawdown"]),
        "profitLossRatioMet": float(analysis.get("profitLossRatio") or 0) >= float(objective["target_profit_loss_ratio"]),
        "minimumTradesMet": int(analysis.get("completedTradeCount") or 0) >= min_trades,
        "singleConcentrationMet": float(analysis.get("maxSinglePositionPct") or 0) <= float(portfolio_target.get("maxSingleExposurePct", portfolio_target["maxSinglePositionPct"])) + 0.001,
        "industryConcentrationMet": float(analysis.get("maxIndustryPositionPct") or 0) <= float(portfolio_target.get("maxIndustryExposurePct", portfolio_target["maxIndustryPositionPct"])) + 0.001,
        "tailLossMet": bool(tail_loss.get("tailLossRiskMet")),
    }
    return {
        "passed": all(gates.values()),
        "gates": gates,
        "thresholds": {
            "minAnnualizedReturn": min_annualized_return,
            "minSharpeRatio": 1.0,
            "maxAbsDrawdown": float(objective["max_abs_drawdown"]),
            "minProfitLossRatio": float(objective["target_profit_loss_ratio"]),
            "minCompletedTrades": min_trades,
            "tailMaxAbsLoss": float(context.get("evaluation", {}).get("tail_max_abs_loss", 0.1)),
            "tailMaxAbsDrawdown": float(context.get("evaluation", {}).get("tail_max_abs_drawdown", 0.1)),
        },
    }


def summarize_windows(window_results: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    analyses = [item["analysis"] for item in window_results]
    failed = [item for item in window_results if not item["segmentGates"]["passed"]]
    returns = [float(item.get("annualizedReturn") or 0) for item in analyses]
    sharpes = [float(item.get("sharpeRatio") or 0) for item in analyses]
    drawdowns = [float(item.get("maxDrawdown") or 0) for item in analyses]
    tail_returns = [
        float(item.get("symbolAudit", {}).get("tailLossRisk", {}).get("worstReturn"))
        for item in analyses
        if item.get("symbolAudit", {}).get("tailLossRisk", {}).get("worstReturn") is not None
    ]
    risk_on_days = [int(item.get("marketRiskOnDays") or 0) for item in analyses]
    objective = context["objective"]
    min_annualized_return = objective_target_annualized_return(objective)
    return {
        "targetMet": not failed and bool(window_results),
        "windowCount": len(window_results),
        "passedCount": len(window_results) - len(failed),
        "failedCount": len(failed),
        "minAnnualizedReturn": min(returns) if returns else None,
        "minSharpeRatio": min(sharpes) if sharpes else None,
        "worstDrawdown": min(drawdowns) if drawdowns else None,
        "worstTailReturn": min(tail_returns) if tail_returns else None,
        "minMarketRiskOnDays": min(risk_on_days) if risk_on_days else None,
        "thresholds": {
            "minAnnualizedReturn": min_annualized_return,
            "minSharpeRatio": 1.0,
            "maxAbsDrawdown": float(objective["max_abs_drawdown"]),
            "minProfitLossRatio": float(objective["target_profit_loss_ratio"]),
        },
        "failedWindows": [
            {
                "label": item["window"]["label"],
                "kind": item["window"]["kind"],
                "startDate": item["window"]["startDate"],
                "endDate": item["window"]["endDate"],
                "gates": item["segmentGates"]["gates"],
                "annualizedReturn": item["analysis"].get("annualizedReturn"),
                "sharpeRatio": item["analysis"].get("sharpeRatio"),
                "maxDrawdown": item["analysis"].get("maxDrawdown"),
                "completedTradeCount": item["analysis"].get("completedTradeCount"),
                "tailWorstReturn": item["analysis"].get("symbolAudit", {}).get("tailLossRisk", {}).get("worstReturn"),
                "tailLossViolationCount": item["analysis"].get("symbolAudit", {}).get("tailLossRisk", {}).get("lossViolationCount"),
                "tailDrawdownViolationCount": item["analysis"].get("symbolAudit", {}).get("tailLossRisk", {}).get("drawdownViolationCount"),
                "marketRiskOnDays": item["analysis"].get("marketRiskOnDays"),
                "blockedMarketSignals": item["analysis"].get("blockedMarketSignals"),
                "blockedRiskSignals": item["analysis"].get("blockedRiskSignals"),
                "blockedRiskReasons": item["analysis"].get("blockedRiskReasons"),
                "candidateCount": item["result"]["scope"].get("candidates"),
                "testedCount": item["result"]["scope"].get("tested"),
            }
            for item in failed
        ],
    }


def render_hypothesis(run_id: str, started_at: str, strategy: dict[str, Any], context: dict[str, Any], windows: list[dict[str, Any]], portfolio_rules: dict[str, Any]) -> str:
    window_lines = "\n".join(
        f"- `{item['label']}` {item['kind']}：计绩 {item['startDate']} 至 {item['endDate']}，预热起点 {item['warmupStartDate']}"
        for item in windows
    )
    objective = context["objective"]
    min_annualized_return = objective_target_annualized_return(objective)
    stress = context.get("execution_stress", {})
    market_filter = context.get("portfolio_target", {}).get("marketBreadthFilter", {})
    rank_weights = context.get("portfolio_target", {}).get("crossSectionScoreWeights", {})
    entry_haircut = context.get("portfolio_target", {}).get("entrySizeHaircut", {})
    return f"""# {run_id} 滚动窗口验证假设

- 时间：{started_at}
- 策略：{strategy["label"]} (`{strategy["name"]}`)
- 验证目标：检查 `cross-section-strength-risk8-pos135` 的收益是否集中在少数阶段，而不是只在完整三年窗口上好看。
- 口径：固定参数分段验证，不重新调参，不改变入场/出场/仓位语义；每个窗口最多使用 {WARMUP_MONTHS} 个月历史数据预热指标，交易和绩效只从窗口开始日计算。
- 建仓上限：{portfolio_rules["maxSinglePositionPct"]:.1%}
- 市场宽度过滤：{"开" if market_filter.get("enabled") else "关"}，MA20 {format_optional_percent(market_filter.get("minAboveMa20Pct"))}，MA60 {format_optional_percent(market_filter.get("minAboveMa60Pct"))}，上涨占比 {format_optional_percent(market_filter.get("minUpPct"))}。
- 横截面成交量排序权重：{format_optional_number(rank_weights.get("volume", 0.5))}。
- 执行压力：买入滑点 {format_optional_percent(stress.get("buySlippagePct"))}，卖出滑点 {format_optional_percent(stress.get("sellSlippagePct"))}，跳空止损按开盘成交 {"是" if stress.get("stopGapFillAtOpen") else "否"}，跌停附近延迟 {"是" if stress.get("limitDownStopDelay") else "否"}。
- 入场缩仓：振幅阈值 {format_optional_percent(entry_haircut.get("rangeThresholdPct"))} / 缩仓 {format_optional_percent(entry_haircut.get("rangeHaircutPct"))}，日内涨幅阈值 {format_optional_percent(entry_haircut.get("intradayThresholdPct"))} / 缩仓 {format_optional_percent(entry_haircut.get("intradayHaircutPct"))}。
- 成功标准：每个窗口年化收益 >= {min_annualized_return:.2%}，Sharpe >= 1.00，最大回撤 <= {objective["max_abs_drawdown"]:.0%}，盈亏比 >= {objective["target_profit_loss_ratio"]}:1，尾部亏损/回撤通过，并满足交易数和集中度门槛。

## 验证窗口

{window_lines}
"""


def render_review(run_id: str, started_at: str, strategy: dict[str, Any], validation: dict[str, Any], window_results: list[dict[str, Any]]) -> str:
    rows = "\n".join(render_window_row(item) for item in window_results)
    failed = validation["failedWindows"]
    failed_lines = "\n".join(
        f"- `{item['label']}` {item['startDate']} 至 {item['endDate']}：年化 {format_optional_percent(item.get('annualizedReturn'))}，Sharpe {format_optional_number(item.get('sharpeRatio'))}，回撤 {format_optional_percent(item.get('maxDrawdown'))}，尾部 {format_optional_percent(item.get('tailWorstReturn'))}，交易 {item.get('completedTradeCount')}，Risk-On {item.get('marketRiskOnDays')} 天，候选 {item.get('candidateCount')}，风险拦截 {format_blocked_risk_reasons(item.get('blockedRiskReasons'))}"
        for item in failed
    ) or "- 无"
    return f"""# {run_id} 滚动窗口验证复盘

- 开始时间：{started_at}
- 结束时间：{now_text()}
- 策略：{strategy["label"]} (`{strategy["name"]}`)
- 结论：{"通过" if validation["targetMet"] else "未通过"}

## 总览

- 窗口数：{validation["windowCount"]}
- 通过窗口：{validation["passedCount"]}
- 失败窗口：{validation["failedCount"]}
- 最低年化收益：{format_optional_percent(validation.get("minAnnualizedReturn"))}
- 最低 Sharpe：{format_optional_number(validation.get("minSharpeRatio"))}
- 最深窗口回撤：{format_optional_percent(validation.get("worstDrawdown"))}
- 最差窗口尾部收益：{format_optional_percent(validation.get("worstTailReturn"))}
- 最低窗口 Risk-On 天数：{validation.get("minMarketRiskOnDays")}

## 窗口结果

| 窗口 | 区间 | 年化 | Sharpe | Sortino | 最大回撤 | 盈亏比 | 尾部最差 | Risk-On天 | 完成交易 | 结论 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
{rows}

## 失败窗口

{failed_lines}

## 解释

这轮只检验同一套固定参数在不同时间切片中的稳定性。短窗口内交易数天然更少，所以本轮直接使用研究目标中的年化收益门槛，并保留 Sharpe、回撤、盈亏比、尾部亏损/回撤、交易数和集中度作为硬检查。每个窗口最多使用 {WARMUP_MONTHS} 个月历史数据预热指标，交易和绩效仍从窗口开始日计算。
"""


def render_window_row(item: dict[str, Any]) -> str:
    window = item["window"]
    analysis = item["analysis"]
    tail_loss = analysis.get("symbolAudit", {}).get("tailLossRisk", {})
    passed = item["segmentGates"]["passed"]
    period = f"{window['startDate']}..{window['endDate']}"
    return (
        f"| `{window['label']}` | {period} | {format_optional_percent(analysis.get('annualizedReturn'))} | "
        f"{format_optional_number(analysis.get('sharpeRatio'))} | {format_optional_number(analysis.get('sortinoRatio'))} | "
        f"{format_optional_percent(analysis.get('maxDrawdown'))} | {format_optional_ratio(analysis.get('profitLossRatio'))} | "
        f"{format_optional_percent(tail_loss.get('worstReturn'))} | {analysis.get('marketRiskOnDays')} | {analysis.get('completedTradeCount')} | {'通过' if passed else '未通过'} |"
    )


def render_next_input(run_id: str, strategy: dict[str, Any], validation: dict[str, Any], window_results: list[dict[str, Any]]) -> str:
    failed = validation["failedWindows"]
    failed_text = "\n".join(
        f"- `{item['label']}`：年化 {format_optional_percent(item.get('annualizedReturn'))}，Sharpe {format_optional_number(item.get('sharpeRatio'))}，回撤 {format_optional_percent(item.get('maxDrawdown'))}，尾部 {format_optional_percent(item.get('tailWorstReturn'))}，交易 {item.get('completedTradeCount')}，Risk-On {item.get('marketRiskOnDays')} 天，候选 {item.get('candidateCount')}，风险拦截 {format_blocked_risk_reasons(item.get('blockedRiskReasons'))}"
        for item in failed
    ) or "- 无"
    return f"""# 下一轮组合策略输入

上一轮：`{run_id}`，策略 `{strategy["name"]}`。

结论：{"滚动窗口通过" if validation["targetMet"] else "滚动窗口未通过"}。

关键数字：

- 窗口数：{validation["windowCount"]}
- 通过窗口：{validation["passedCount"]}
- 失败窗口：{validation["failedCount"]}
- 最低年化收益：{format_optional_percent(validation.get("minAnnualizedReturn"))}
- 最低 Sharpe：{format_optional_number(validation.get("minSharpeRatio"))}
- 最深窗口回撤：{format_optional_percent(validation.get("worstDrawdown"))}
- 最差窗口尾部收益：{format_optional_percent(validation.get("worstTailReturn"))}
- 最低窗口 Risk-On 天数：{validation.get("minMarketRiskOnDays")}
- 预热口径：最多 {WARMUP_MONTHS} 个月历史数据预热指标，交易和绩效只从窗口开始日计算。

失败窗口：

{failed_text}

下一轮建议：

- 若失败窗口 Risk-On 天数为 0，先修正市场宽度样本口径，避免候选集规模低于 `minSamples` 时把整段市场误判成禁止开仓。
- 若年度窗口失败但 18 个月窗口通过，优先研究市场 Risk-On 日期稀疏期的空仓成本和信号不足，不要立刻改核心止盈止损。
- 若窗口回撤过线但收益不足，检查横截面排序是否在该阶段缺少强势行业暴露。
- 若窗口收益过线但 Sharpe 不足，优先加入更现实成交约束和滑点，确认收益不是由少数跳跃行情贡献。
"""


def format_optional_number(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def format_blocked_risk_reasons(reasons: Any) -> str:
    if not isinstance(reasons, dict) or not reasons:
        return "n/a"
    ordered = sorted(reasons.items(), key=lambda item: int(item[1] or 0), reverse=True)
    return "，".join(f"{key} {int(value)}" for key, value in ordered[:3])


if __name__ == "__main__":
    main()
