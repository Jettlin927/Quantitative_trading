from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import pandas as pd

from my_quant.strategy_research.experiment.b1_trend_pullback import (
    B1BacktestConfig,
    build_b1_panels,
    build_b1_summary_markdown,
    load_a_share_symbols,
    run_b1_backtest,
)
from my_quant.strategy_research.experiment.config import DATA_DIR, RESULTS_DIR
from my_quant.strategy_research.experiment.reports import markdown_table
from my_quant.strategy_research.run_b1_trend_pullback import build_market_frame, select_symbol_sample


DEFAULT_LEVELS = [
    (0.05, 0.10, 0.15),
    (0.08, 0.16, 0.24),
    (0.10, 0.20, 0.30),
    (0.12, 0.24, 0.36),
    (0.15, 0.30, 0.45),
]
DEFAULT_FRACTIONS = [
    (0.25, 0.25, 1.0),
    (0.33, 0.33, 1.0),
    (0.50, 0.50, 1.0),
    (1.0, 1.0, 1.0),
]


def _compact_date(value: str) -> str:
    return value.replace("-", "")


def _percent_name(values: Iterable[float]) -> str:
    return "_".join(str(int(round(value * 100))) for value in values)


def exit_scan_configs(
    levels_list: list[tuple[float, ...]] | None = None,
    fractions_list: list[tuple[float, ...]] | None = None,
) -> list[dict[str, tuple[float, ...] | str]]:
    levels_options = levels_list or DEFAULT_LEVELS
    fraction_options = fractions_list or DEFAULT_FRACTIONS
    configs: list[dict[str, tuple[float, ...] | str]] = []
    for levels in levels_options:
        for fractions in fraction_options:
            if len(levels) != len(fractions):
                continue
            configs.append(
                {
                    "name": f"tp{_percent_name(levels)}_f{_percent_name(fractions)}",
                    "levels": levels,
                    "fractions": fractions,
                }
            )
    return configs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan B1 trend-pullback staged take-profit exits.")
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2026-05-15")
    parser.add_argument("--history-start", default="2024-06-01")
    parser.add_argument("--max-symbols", type=int, default=100)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--data-provider", choices=["akshare", "tushare"], default="akshare")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "b1_a_share")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--output-prefix", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    symbols = select_symbol_sample(
        load_a_share_symbols(data_provider=args.data_provider),
        args.max_symbols,
        args.offset,
        args.stride,
    )
    base_config = B1BacktestConfig()
    panels = build_b1_panels(
        symbols,
        start=_compact_date(args.history_start),
        end=_compact_date(args.end),
        data_dir=args.data_dir,
        config=base_config,
        refresh=False,
        data_provider=args.data_provider,
    )
    market = build_market_frame(args.history_start, args.end, args.start, args.data_provider, args.data_dir)

    rows: list[dict[str, object]] = []
    best_result = None
    best_name = ""
    for raw_config in exit_scan_configs():
        config = B1BacktestConfig(
            take_profit_levels=raw_config["levels"],  # type: ignore[arg-type]
            take_profit_fractions=raw_config["fractions"],  # type: ignore[arg-type]
        )
        result = run_b1_backtest(panels, market, config)
        row = {
            "strategy": raw_config["name"],
            "take_profit_levels": "/".join(f"{value:.2f}" for value in raw_config["levels"]),  # type: ignore[union-attr]
            "take_profit_fractions": "/".join(f"{value:.2f}" for value in raw_config["fractions"]),  # type: ignore[union-attr]
            **result.summary,
        }
        rows.append(row)
        if best_result is None or float(result.summary["annual_return"]) > float(best_result.summary["annual_return"]):
            best_result = result
            best_name = str(raw_config["name"])

    scan = pd.DataFrame(rows).sort_values(["passes_drawdown_gate", "annual_return", "calmar"], ascending=False)
    prefix = args.output_prefix or f"b1_exit_scan_m{args.max_symbols or 'all'}_o{args.offset}_s{args.stride}"
    args.results_dir.mkdir(parents=True, exist_ok=True)
    scan_path = args.results_dir / f"{prefix}.csv"
    summary_path = args.results_dir / f"{prefix}_summary.md"
    scan.to_csv(scan_path, index=False)
    if best_result is None:
        summary_path.write_text("# B1 Exit Scan\n\nNo results.\n", encoding="utf-8")
    else:
        summary = build_b1_summary_markdown(best_result, args.start, args.end, len(panels))
        summary += f"\nBest exit config: `{best_name}`.\n"
        summary += "\n## Top Rows\n\n"
        summary += markdown_table(scan.head(10), index=False, floatfmt=".4f")
        summary += "\n"
        summary_path.write_text(summary, encoding="utf-8")
    print(f"B1 exit scan complete: {len(panels)} symbols, {len(scan)} rows")
    print(f"scan={scan_path}")
    print(f"summary={summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
