from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from my_quant.strategy_research.experiment.b1_trend_pullback import (
    B1BacktestConfig,
    apply_mainboard_style_gate,
    build_b1_panels,
    filter_mainboard_a_share_symbols,
    run_b1_backtest,
    write_b1_artifacts,
)
from my_quant.strategy_research.experiment.config import DATA_DIR, RESULTS_DIR
from my_quant.strategy_research.experiment.reports import markdown_table
from my_quant.strategy_research.run_b1_trend_pullback import build_market_frame
from my_quant.strategy_research.run_b1_walk_forward import load_symbols_from_csv_file, summarize_window_gates


DEFAULT_SYMBOLS_FILE = RESULTS_DIR / "b1_tushare_active_20241231_top300_universe.csv"
DEFAULT_WINDOWS = [
    ("full", "2025-01-01", "2026-06-17"),
    ("train_2025", "2025-01-01", "2025-12-31"),
    ("oos_2026", "2026-01-01", "2026-06-17"),
    ("wf_2025_h1", "2025-01-01", "2025-06-30"),
    ("wf_2025_h2", "2025-07-01", "2025-12-31"),
    ("wf_2026_h1", "2026-01-01", "2026-06-17"),
]


def _compact_date(value: str) -> str:
    return value.replace("-", "")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run B1 small-capital mainboard-only validation.")
    parser.add_argument("--history-start", default="2024-06-01")
    parser.add_argument("--symbols-file", type=Path, default=DEFAULT_SYMBOLS_FILE)
    parser.add_argument("--data-provider", choices=["tushare", "akshare"], default="tushare")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "b1_a_share")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--output-prefix", default="b1_small_capital_mainboard_20260617")
    parser.add_argument("--initial-cash", type=float, default=20_000.0)
    parser.add_argument("--exclude-permission-boards", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--use-mainboard-style-gate", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--style-gate-min-above-bbi-pct", type=float, default=0.30)
    parser.add_argument("--style-gate-min-median-mom20", type=float, default=0.0)
    parser.add_argument("--style-gate-min-sample-size", type=int, default=20)
    return parser.parse_args(argv)


def build_small_capital_config(args: argparse.Namespace) -> B1BacktestConfig:
    return B1BacktestConfig(
        top_n=1,
        max_position=1.0,
        initial_cash=float(args.initial_cash),
        buy_price_column="open",
        sell_price_column="close",
        lot_size=100,
        require_affordable_lot=True,
        limit_up_pct=0.10,
        limit_down_pct=0.10,
        max_entry_close_bbi=0.275,
        min_entry_mom20=0.02,
        max_entry_mom20=0.75,
        stop_loss_pct=0.05,
        take_profit_levels=(0.05,),
        take_profit_fractions=(1.0,),
    )


def build_window_market(
    args: argparse.Namespace,
    panels: dict[str, pd.DataFrame],
    config: B1BacktestConfig,
    start: str,
    end: str,
) -> pd.DataFrame:
    market = build_market_frame(
        args.history_start,
        end,
        start,
        args.data_provider,
        args.data_dir,
        require_ma20_gt_ma60=True,
    )
    if args.use_mainboard_style_gate:
        market = apply_mainboard_style_gate(
            market,
            panels,
            min_above_bbi_pct=float(args.style_gate_min_above_bbi_pct),
            min_median_mom20=float(args.style_gate_min_median_mom20),
            min_sample_size=int(args.style_gate_min_sample_size),
        )
    return market


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = build_small_capital_config(args)
    symbols = load_symbols_from_csv_file(args.symbols_file)
    if args.exclude_permission_boards:
        symbols = filter_mainboard_a_share_symbols(symbols)
    last_end = max(end for _name, _start, end in DEFAULT_WINDOWS)
    panels = build_b1_panels(
        symbols=symbols,
        start=_compact_date(args.history_start),
        end=_compact_date(last_end),
        data_dir=args.data_dir,
        config=config,
        refresh=False,
        data_provider=args.data_provider,
    )

    rows: list[dict[str, object]] = []
    full_result = None
    for window_name, start, end in DEFAULT_WINDOWS:
        market = build_window_market(args, panels, config, start, end)
        result = run_b1_backtest(panels, market, config)
        if window_name == "full":
            full_result = result
        rows.append({"strategy": args.output_prefix, "window": window_name, "start": start, "end": end, "symbol_count": len(panels), **result.summary})

    details = pd.DataFrame(rows)
    summary = summarize_window_gates(details)
    args.results_dir.mkdir(parents=True, exist_ok=True)
    details_path = args.results_dir / f"{args.output_prefix}_details.csv"
    summary_path = args.results_dir / f"{args.output_prefix}_summary.csv"
    markdown_path = args.results_dir / f"{args.output_prefix}.md"
    details.to_csv(details_path, index=False)
    summary.to_csv(summary_path, index=False)
    if full_result is not None:
        write_b1_artifacts(full_result, DEFAULT_WINDOWS[0][1], DEFAULT_WINDOWS[0][2], len(panels), args.results_dir, artifact_prefix=f"{args.output_prefix}_full")

    lines = [
        "# B1 Small-Capital Mainboard Validation",
        "",
        f"- Initial cash: `{config.initial_cash:,.0f}`.",
        f"- Symbols loaded after permission-board filter: `{len(panels)}`.",
        f"- Top N: `{config.top_n}`.",
        f"- Max position: `{config.max_position:.2f}`.",
        f"- Require affordable 100-share lot: `{config.require_affordable_lot}`.",
        f"- Mainboard style gate: `{args.use_mainboard_style_gate}`.",
        f"- Style gate min above BBI pct: `{args.style_gate_min_above_bbi_pct}`.",
        f"- Style gate min median mom20: `{args.style_gate_min_median_mom20}`.",
        f"- Style gate min sample size: `{args.style_gate_min_sample_size}`.",
        "",
        "## Summary",
        "",
        markdown_table(summary, index=False, floatfmt=".4f"),
        "",
        "## Window Details",
        "",
        markdown_table(details, index=False, floatfmt=".4f"),
        "",
    ]
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    print(markdown_path)
    print(markdown_table(summary, index=False, floatfmt=".4f"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
