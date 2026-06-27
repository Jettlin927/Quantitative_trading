from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from my_quant.strategy_research.experiment.b1_trend_pullback import (
    B1BacktestConfig,
    build_b1_panels,
    build_b1_summary_markdown,
    load_a_share_symbols,
    normalize_a_share_code,
    run_b1_backtest,
    select_active_tushare_universe,
)
from my_quant.strategy_research.experiment.config import DATA_DIR, RESULTS_DIR
from my_quant.strategy_research.experiment.reports import markdown_table
from my_quant.strategy_research.run_b1_exit_scan import exit_scan_configs
from my_quant.strategy_research.run_b1_trend_pullback import build_market_frame, select_symbol_sample


DEFAULT_WINDOWS = [
    ("full", "2025-01-01", "2026-05-15"),
    ("train_2025", "2025-01-01", "2025-12-31"),
    ("oos_2026", "2026-01-01", "2026-05-15"),
    ("wf_2025_h1", "2025-01-01", "2025-06-30"),
    ("wf_2025_h2", "2025-07-01", "2025-12-31"),
]


def _compact_date(value: str) -> str:
    return value.replace("-", "")


def summarize_window_gates(rows: pd.DataFrame) -> pd.DataFrame:
    rows = rows.copy()
    for column in ["annual_return", "max_drawdown", "calmar"]:
        if column not in rows.columns:
            rows[column] = 0.0
    grouped = rows.groupby("strategy", sort=False)
    summary = grouped.agg(
        windows=("window", "count"),
        return_pass_windows=("passes_return_gate", "sum"),
        drawdown_pass_windows=("passes_drawdown_gate", "sum"),
        mean_annual_return=("annual_return", "mean"),
        min_annual_return=("annual_return", "min"),
        worst_drawdown=("max_drawdown", "min"),
        mean_calmar=("calmar", "mean"),
    ).reset_index()
    summary["return_fail_windows"] = summary["windows"] - summary["return_pass_windows"]
    summary["drawdown_fail_windows"] = summary["windows"] - summary["drawdown_pass_windows"]
    summary["passes_all_windows"] = (summary["return_fail_windows"] == 0) & (summary["drawdown_fail_windows"] == 0)
    return summary.sort_values(["passes_all_windows", "min_annual_return", "worst_drawdown"], ascending=False)


def load_symbols_from_csv_file(path: Path) -> list[str]:
    raw = pd.read_csv(path, dtype=str)
    if "symbol" in raw.columns:
        source = raw["symbol"]
    elif "ts_code" in raw.columns:
        source = raw["ts_code"].str.split(".").str[0]
    else:
        raise ValueError("symbols file must contain a symbol or ts_code column")
    return [normalize_a_share_code(str(symbol)) for symbol in source.dropna().tolist()]


def build_active_universe_symbol_map(
    daily_basic_file: Path,
    stock_basic_file: Path,
    windows: list[tuple[str, str, str]],
    limit: int,
    rolling: bool = False,
    universe_as_of: str | None = None,
) -> dict[str, list[str]]:
    daily_basic = pd.read_csv(daily_basic_file, dtype={"ts_code": str, "trade_date": str})
    stock_basic = pd.read_csv(stock_basic_file, dtype={"ts_code": str, "symbol": str, "name": str})
    if rolling:
        as_of_by_window = {window_name: start for window_name, start, _end in windows}
    else:
        as_of = universe_as_of or windows[0][1]
        as_of_by_window = {window_name: as_of for window_name, _start, _end in windows}

    symbol_map: dict[str, list[str]] = {}
    for window_name, as_of in as_of_by_window.items():
        universe = select_active_tushare_universe(
            daily_basic=daily_basic,
            stock_basic=stock_basic,
            as_of_date=as_of,
            limit=limit,
        )
        symbol_map[window_name] = universe["symbol"].astype(str).map(normalize_a_share_code).tolist()
    return symbol_map


def build_b1_config_from_exit_config(
    raw_config: dict[str, tuple[float, ...] | str],
    max_entry_close_bbi: float | None = None,
    min_entry_mom20: float | None = None,
    max_entry_mom20: float | None = None,
) -> B1BacktestConfig:
    return B1BacktestConfig(
        take_profit_levels=raw_config["levels"],  # type: ignore[arg-type]
        take_profit_fractions=raw_config["fractions"],  # type: ignore[arg-type]
        max_entry_close_bbi=max_entry_close_bbi,
        min_entry_mom20=min_entry_mom20,
        max_entry_mom20=max_entry_mom20,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run strict B1 walk-forward/time-split validation.")
    parser.add_argument("--history-start", default="2024-06-01")
    parser.add_argument("--max-symbols", type=int, default=300)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--stride", type=int, default=10)
    parser.add_argument("--symbols-file", type=Path, default=None, help="Optional CSV with symbol or ts_code column.")
    parser.add_argument("--daily-basic-file", type=Path, default=None, help="Optional Tushare daily_basic CSV for active universe construction.")
    parser.add_argument("--stock-basic-file", type=Path, default=None, help="Optional Tushare stock_basic CSV for active universe construction.")
    parser.add_argument("--rolling-active-universe", action="store_true", help="Build an active universe as of each validation window start.")
    parser.add_argument("--universe-as-of", default=None, help="Fixed as-of date for active universe construction when not rolling.")
    parser.add_argument("--data-provider", choices=["akshare", "tushare"], default="akshare")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "b1_a_share")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--output-prefix", default="")
    parser.add_argument("--market-ma20-gt-ma60", action="store_true", help="Require market MA20 > MA60 for new entries.")
    parser.add_argument("--max-entry-close-bbi", type=float, default=None, help="Reject entries too far above BBI.")
    parser.add_argument("--min-entry-mom20", type=float, default=None, help="Require minimum 20-day entry momentum.")
    parser.add_argument("--max-entry-mom20", type=float, default=None, help="Reject overheated 20-day entry momentum.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    windows = DEFAULT_WINDOWS
    last_end = max(end for _name, _start, end in windows)
    window_symbol_map: dict[str, list[str]] | None = None
    if args.daily_basic_file or args.stock_basic_file:
        if not args.daily_basic_file or not args.stock_basic_file:
            raise ValueError("--daily-basic-file and --stock-basic-file must be provided together")
        window_symbol_map = build_active_universe_symbol_map(
            daily_basic_file=args.daily_basic_file,
            stock_basic_file=args.stock_basic_file,
            windows=windows,
            limit=args.max_symbols,
            rolling=args.rolling_active_universe,
            universe_as_of=args.universe_as_of,
        )
        symbols = sorted({symbol for values in window_symbol_map.values() for symbol in values})
    else:
        raw_symbols = load_symbols_from_csv_file(args.symbols_file) if args.symbols_file else load_a_share_symbols(data_provider=args.data_provider)
        symbols = select_symbol_sample(raw_symbols, args.max_symbols, args.offset, args.stride)
    base_config = B1BacktestConfig(
        max_entry_close_bbi=args.max_entry_close_bbi,
        min_entry_mom20=args.min_entry_mom20,
        max_entry_mom20=args.max_entry_mom20,
    )
    panels = build_b1_panels(
        symbols,
        start=_compact_date(args.history_start),
        end=_compact_date(last_end),
        data_dir=args.data_dir,
        config=base_config,
        refresh=False,
        data_provider=args.data_provider,
    )

    rows: list[dict[str, object]] = []
    exit_configs = exit_scan_configs()
    for raw_config in exit_configs:
        config = build_b1_config_from_exit_config(
            raw_config,
            max_entry_close_bbi=args.max_entry_close_bbi,
            min_entry_mom20=args.min_entry_mom20,
            max_entry_mom20=args.max_entry_mom20,
        )
        for window_name, start, end in windows:
            market = build_market_frame(
                args.history_start,
                end,
                start,
                args.data_provider,
                args.data_dir,
                require_ma20_gt_ma60=args.market_ma20_gt_ma60,
            )
            active_panels = panels
            if window_symbol_map is not None:
                active_symbols = set(window_symbol_map.get(window_name, []))
                active_panels = {symbol: frame for symbol, frame in panels.items() if symbol in active_symbols}
            result = run_b1_backtest(active_panels, market, config)
            rows.append(
                {
                    "strategy": raw_config["name"],
                    "window": window_name,
                    "start": start,
                    "end": end,
                    "take_profit_levels": "/".join(f"{value:.2f}" for value in raw_config["levels"]),  # type: ignore[union-attr]
                    "take_profit_fractions": "/".join(f"{value:.2f}" for value in raw_config["fractions"]),  # type: ignore[union-attr]
                    **result.summary,
                }
            )

    details = pd.DataFrame(rows)
    summary = summarize_window_gates(details)
    prefix = args.output_prefix or f"b1_walk_forward_m{args.max_symbols or 'all'}_o{args.offset}_s{args.stride}"
    args.results_dir.mkdir(parents=True, exist_ok=True)
    details_path = args.results_dir / f"{prefix}_details.csv"
    summary_path = args.results_dir / f"{prefix}_summary.csv"
    markdown_path = args.results_dir / f"{prefix}.md"
    details.to_csv(details_path, index=False)
    summary.to_csv(summary_path, index=False)

    best_strategy = str(summary.iloc[0]["strategy"]) if not summary.empty else ""
    best_detail = details[details["strategy"] == best_strategy].sort_values("window") if best_strategy else pd.DataFrame()
    lines = [
        "# B1 Walk-Forward Validation",
        "",
        f"- Symbols loaded: `{len(panels)}`.",
        f"- Window count: `{len(windows)}`.",
        f"- Config count: `{len(exit_configs)}`.",
        f"- Passing all-window configs: `{int(summary['passes_all_windows'].sum()) if not summary.empty else 0}`.",
        f"- Best strategy by strict gate ordering: `{best_strategy}`.",
        f"- Symbols file: `{args.symbols_file}`.",
        f"- Daily basic file: `{args.daily_basic_file}`.",
        f"- Stock basic file: `{args.stock_basic_file}`.",
        f"- Rolling active universe: `{args.rolling_active_universe}`.",
        f"- Universe as-of: `{args.universe_as_of}`.",
        f"- Market MA20 > MA60 filter: `{args.market_ma20_gt_ma60}`.",
        f"- Max entry close/BBI: `{args.max_entry_close_bbi}`.",
        f"- Min entry 20-day momentum: `{args.min_entry_mom20}`.",
        f"- Max entry 20-day momentum: `{args.max_entry_mom20}`.",
        "",
        "## Summary Top Rows",
        "",
        markdown_table(summary.head(10), index=False, floatfmt=".4f"),
        "",
        "## Best Strategy Window Detail",
        "",
        markdown_table(best_detail, index=False, floatfmt=".4f") if not best_detail.empty else "No rows.",
        "",
    ]
    best_raw_config = next((config for config in exit_configs if str(config["name"]) == best_strategy), None)
    if best_raw_config is not None:
        best_full_config = build_b1_config_from_exit_config(
            best_raw_config,
            max_entry_close_bbi=args.max_entry_close_bbi,
            min_entry_mom20=args.min_entry_mom20,
            max_entry_mom20=args.max_entry_mom20,
        )
    else:
        best_full_config = B1BacktestConfig()
    lines.append(
        build_b1_summary_markdown(
            run_b1_backtest(
                panels,
                build_market_frame(
                    args.history_start,
                    "2026-05-15",
                    "2025-01-01",
                    args.data_provider,
                    args.data_dir,
                    require_ma20_gt_ma60=args.market_ma20_gt_ma60,
                ),
                best_full_config,
            ),
            "2025-01-01",
            "2026-05-15",
            len(panels),
        )
    )
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"B1 walk-forward complete: {len(panels)} symbols, {len(details)} rows")
    print(f"details={details_path}")
    print(f"summary={summary_path}")
    print(f"markdown={markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
