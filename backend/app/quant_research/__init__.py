"""Pure, research-only contracts for reproducible quantitative studies."""

from .dataset import active_members_as_of, attach_fundamentals_asof, build_adjusted_price_panel
from .calendar import OpenTradeCalendar, build_open_trade_calendar, trade_calendar_content_sha256
from .manifest import build_run_manifest
from .metrics import summarize_performance
from .portfolio import CostModel, simulate_target_weights
from .readiness import evaluate_quality_run_readiness, evaluate_research_readiness
from .repository import load_index_benchmark, load_stock_research_panel
from .universe import build_explicit_universe, build_historical_membership_panel, build_historical_universe, evaluate_universe_provenance
from .strategy_registry import list_strategy_definitions, resolve_strategy_definition
from .validation import WalkForwardWindow, build_walk_forward_windows

__all__ = [
    "CostModel",
    "OpenTradeCalendar",
    "WalkForwardWindow",
    "active_members_as_of",
    "attach_fundamentals_asof",
    "build_adjusted_price_panel",
    "build_open_trade_calendar",
    "build_explicit_universe",
    "build_historical_membership_panel",
    "list_strategy_definitions",
    "resolve_strategy_definition",
    "build_historical_universe",
    "build_run_manifest",
    "build_walk_forward_windows",
    "evaluate_research_readiness",
    "evaluate_universe_provenance",
    "evaluate_quality_run_readiness",
    "load_index_benchmark",
    "load_stock_research_panel",
    "reproduce_quant_research",
    "run_quant_research",
    "simulate_target_weights",
    "summarize_performance",
    "trade_calendar_content_sha256",
]


def run_quant_research(*args, **kwargs):
    from .runner import run_quant_research as implementation

    return implementation(*args, **kwargs)


def reproduce_quant_research(*args, **kwargs):
    from .runner import reproduce_quant_research as implementation

    return implementation(*args, **kwargs)
