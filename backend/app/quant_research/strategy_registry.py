from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable

from .baselines import (
    build_sentinel_targets,
    sentinel_limitations,
    simulate_sentinel_targets_with_ledger,
    summarize_sentinel_metrics_v2,
    validate_sentinel_config,
    simulate_etf_targets_with_ledger,
)
from .etf_trend_baseline import (
    build_etf_trend_targets,
    etf_trend_limitations,
    summarize_etf_trend_metrics,
    validate_etf_trend_config,
)
from .etf_volatility_managed import (
    build_etf_low_volatility_gate_targets,
    build_etf_volatility_managed_targets,
    etf_low_volatility_gate_limitations,
    etf_volatility_managed_limitations,
    simulate_etf_low_volatility_gate_targets,
    simulate_etf_volatility_managed_targets,
    summarize_etf_low_volatility_gate_metrics,
    summarize_etf_volatility_managed_metrics,
    validate_etf_low_volatility_gate_config,
    validate_etf_volatility_managed_config,
)
from .a_share_price_baseline import (
    a_share_price_limitations,
    build_a_share_price_targets,
    simulate_a_share_price_targets,
    summarize_a_share_price_metrics,
    validate_a_share_price_config,
)
from .a_share_b1_trend_pullback import (
    a_share_b1_limitations,
    build_a_share_b1_targets,
    simulate_a_share_b1_targets,
    summarize_a_share_b1_metrics,
    validate_a_share_b1_config,
)
from .a_share_value_quality_industry_strength import (
    EXECUTION_ARTIFACT_COLUMNS,
    REQUEST_ARTIFACT_COLUMNS,
    TARGET_ARTIFACT_COLUMNS,
    a_share_value_quality_limitations,
    build_a_share_value_quality_targets,
    simulate_a_share_value_quality_targets,
    summarize_a_share_value_quality_metrics,
    validate_a_share_value_quality_config,
)


StrategyCallable = Callable[..., Any]
STRATEGY_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    strategy_version: str
    scope: str
    example_config: str
    walk_forward_benchmark_source: str
    required_tables: tuple[str, ...]
    validate_config: Callable[[dict[str, Any]], None]
    build_targets: StrategyCallable
    simulate: StrategyCallable
    summarize_metrics: StrategyCallable
    limitations: Callable[[], list[str]]
    target_columns: tuple[str, ...] = (
        "signal_date",
        "available_date",
        "ts_code",
        "target_weight",
    )
    request_columns: tuple[str, ...] = (
        "execution_date",
        "signal_date",
        "ts_code",
        "requested_change",
        "side",
    )
    execution_columns: tuple[str, ...] = (
        "execution_date",
        "signal_date",
        "ts_code",
        "requested_change",
        "executed_change",
        "blocked_change",
        "status",
        "reason",
        "transaction_cost_rate",
    )
    summarize_accepts_simulation: bool = False


_STRATEGIES = {
    "sentinel_etf_baseline": StrategyDefinition(
        strategy_id="sentinel_etf_baseline",
        strategy_version="1",
        scope="etf_time_series",
        example_config="configs/research/sentinel_etf_baseline.json",
        walk_forward_benchmark_source="config_market_reference",
        required_tables=(
            "trade_calendars",
            "funds",
            "fund_daily_bars",
            "fund_adjust_factors",
            "indices",
            "index_daily_bars",
            "universe",
        ),
        validate_config=validate_sentinel_config,
        build_targets=build_sentinel_targets,
        simulate=simulate_sentinel_targets_with_ledger,
        summarize_metrics=summarize_sentinel_metrics_v2,
        limitations=sentinel_limitations,
    ),
    "etf_trend_120d": StrategyDefinition(
        strategy_id="etf_trend_120d",
        strategy_version="1",
        scope="etf_time_series",
        example_config="configs/research/etf_trend_baseline.json",
        walk_forward_benchmark_source="universe_adjusted_etf",
        required_tables=(
            "trade_calendars",
            "funds",
            "fund_daily_bars",
            "fund_adjust_factors",
            "indices",
            "index_daily_bars",
            "universe",
        ),
        validate_config=validate_etf_trend_config,
        build_targets=build_etf_trend_targets,
        simulate=simulate_etf_targets_with_ledger,
        summarize_metrics=summarize_etf_trend_metrics,
        limitations=etf_trend_limitations,
    ),
    "etf_volatility_managed": StrategyDefinition(
        strategy_id="etf_volatility_managed",
        strategy_version="1",
        scope="etf_time_series",
        example_config="configs/research/etf_volatility_managed_baseline.json",
        walk_forward_benchmark_source="universe_adjusted_etf",
        required_tables=(
            "trade_calendars",
            "funds",
            "fund_daily_bars",
            "fund_adjust_factors",
            "indices",
            "index_daily_bars",
            "universe",
        ),
        validate_config=validate_etf_volatility_managed_config,
        build_targets=build_etf_volatility_managed_targets,
        simulate=simulate_etf_volatility_managed_targets,
        summarize_metrics=summarize_etf_volatility_managed_metrics,
        limitations=etf_volatility_managed_limitations,
    ),
    "etf_low_volatility_gate": StrategyDefinition(
        strategy_id="etf_low_volatility_gate",
        strategy_version="1",
        scope="etf_time_series",
        example_config="configs/research/etf_low_volatility_gate.json",
        walk_forward_benchmark_source="universe_adjusted_etf",
        required_tables=(
            "trade_calendars",
            "funds",
            "fund_daily_bars",
            "fund_adjust_factors",
            "indices",
            "index_daily_bars",
            "universe",
        ),
        validate_config=validate_etf_low_volatility_gate_config,
        build_targets=build_etf_low_volatility_gate_targets,
        simulate=simulate_etf_low_volatility_gate_targets,
        summarize_metrics=summarize_etf_low_volatility_gate_metrics,
        limitations=etf_low_volatility_gate_limitations,
    ),
    "a_share_price_baseline": StrategyDefinition(
        strategy_id="a_share_price_baseline",
        strategy_version="1",
        scope="a_share_cross_section",
        example_config="configs/research/a_share_price_baseline.json",
        walk_forward_benchmark_source="config_market_reference",
        required_tables=(
            "trade_calendars",
            "stock_listings",
            "stock_daily_bars",
            "stock_adjust_factors",
            "stock_limit_prices",
            "stock_suspend_events",
            "industry_members",
            "indices",
            "index_daily_bars",
            "universe",
        ),
        validate_config=validate_a_share_price_config,
        build_targets=build_a_share_price_targets,
        simulate=simulate_a_share_price_targets,
        summarize_metrics=summarize_a_share_price_metrics,
        limitations=a_share_price_limitations,
    ),
    "a_share_b1_trend_pullback": StrategyDefinition(
        strategy_id="a_share_b1_trend_pullback",
        strategy_version="1",
        scope="a_share_cross_section",
        example_config="configs/research/a_share_b1_long_history.json",
        walk_forward_benchmark_source="config_market_reference",
        required_tables=(
            "trade_calendars",
            "stock_listings",
            "stock_daily_bars",
            "stock_adjust_factors",
            "stock_limit_prices",
            "stock_suspend_events",
            "industry_members",
            "indices",
            "index_daily_bars",
            "universe",
        ),
        validate_config=validate_a_share_b1_config,
        build_targets=build_a_share_b1_targets,
        simulate=simulate_a_share_b1_targets,
        summarize_metrics=summarize_a_share_b1_metrics,
        limitations=a_share_b1_limitations,
    ),
    "a_share_value_quality_industry_strength": StrategyDefinition(
        strategy_id="a_share_value_quality_industry_strength",
        strategy_version="1",
        scope="a_share_cross_section",
        example_config="configs/research/a_share_value_quality_industry_strength.json",
        walk_forward_benchmark_source="config_market_reference",
        required_tables=(
            "trade_calendars",
            "stock_listings",
            "stock_daily_bars",
            "stock_adjust_factors",
            "stock_limit_prices",
            "stock_suspend_events",
            "industry_classifications",
            "industry_members",
            "stock_daily_basic",
            "stock_financial_indicators",
            "indices",
            "index_daily_bars",
            "universe",
        ),
        validate_config=validate_a_share_value_quality_config,
        build_targets=build_a_share_value_quality_targets,
        simulate=simulate_a_share_value_quality_targets,
        summarize_metrics=summarize_a_share_value_quality_metrics,
        limitations=a_share_value_quality_limitations,
        target_columns=TARGET_ARTIFACT_COLUMNS,
        request_columns=REQUEST_ARTIFACT_COLUMNS,
        execution_columns=EXECUTION_ARTIFACT_COLUMNS,
        summarize_accepts_simulation=True,
    ),
}


def resolve_strategy_definition(config: dict[str, Any]) -> StrategyDefinition:
    strategy_id = str(config.get("strategyId") or "").strip()
    if not STRATEGY_ID_PATTERN.fullmatch(strategy_id):
        raise ValueError("策略 ID 格式无效；只允许小写字母、数字和下划线")
    definition = _STRATEGIES.get(strategy_id)
    if definition is None:
        raise ValueError(f"策略未登记：{strategy_id}")
    if str(config.get("strategyVersion") or "") != definition.strategy_version:
        raise ValueError(
            f"策略版本不匹配：{strategy_id} 只允许 {definition.strategy_version}"
        )
    if config.get("scope") != definition.scope:
        raise ValueError(
            f"策略 scope 不匹配：{strategy_id} 只允许 {definition.scope}"
        )
    definition.validate_config(config)
    return definition


def list_strategy_definitions() -> tuple[StrategyDefinition, ...]:
    return tuple(_STRATEGIES[key] for key in sorted(_STRATEGIES))
