"""Pure, research-only contracts for reproducible quantitative studies."""

from .dataset import active_members_as_of, attach_fundamentals_asof, build_adjusted_price_panel
from .manifest import build_run_manifest
from .metrics import summarize_performance
from .portfolio import CostModel, simulate_target_weights
from .readiness import evaluate_quality_run_readiness, evaluate_research_readiness
from .repository import load_index_benchmark, load_stock_research_panel
from .universe import build_explicit_universe, build_historical_membership_panel, build_historical_universe, evaluate_universe_provenance
from .validation import WalkForwardWindow, build_walk_forward_windows

__all__ = [
    "CostModel",
    "WalkForwardWindow",
    "active_members_as_of",
    "attach_fundamentals_asof",
    "build_adjusted_price_panel",
    "build_explicit_universe",
    "build_historical_membership_panel",
    "build_historical_universe",
    "build_run_manifest",
    "build_walk_forward_windows",
    "evaluate_research_readiness",
    "evaluate_universe_provenance",
    "evaluate_quality_run_readiness",
    "load_index_benchmark",
    "load_stock_research_panel",
    "simulate_target_weights",
    "summarize_performance",
]
