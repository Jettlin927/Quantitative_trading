from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .backtest import run_config
from .config import EVAL_END, EVAL_START, TRAIN_END, TRAIN_START, ExperimentPaths, base_configs, scan_configs
from .data import load_prices
from .factor_diagnostics import summarize_factor_ic
from .reports import write_manifest, write_summary
from .validation import walk_forward_analysis


@dataclass(frozen=True)
class ExperimentResult:
    base_df: pd.DataFrame
    scan_df: pd.DataFrame
    train_df: pd.DataFrame
    train_best_oos: pd.Series


def run_experiment(paths: ExperimentPaths | None = None) -> ExperimentResult:
    selected_paths = paths or ExperimentPaths()
    selected_paths.results_dir.mkdir(parents=True, exist_ok=True)
    prices = load_prices(data_dir=selected_paths.data_dir)

    base_rows = [run_config(prices, config, EVAL_START, EVAL_END) for config in base_configs()]
    base_df = pd.DataFrame(base_rows)
    base_df.to_csv(selected_paths.results_dir / "base_strategy_comparison.csv", index=False)

    ram_scan_configs = scan_configs()
    scan_rows = [run_config(prices, config, EVAL_START, EVAL_END) for config in ram_scan_configs]
    scan_df = pd.DataFrame(scan_rows).sort_values(["calmar", "annual_return"], ascending=False)
    scan_df.to_csv(selected_paths.results_dir / "ram_parameter_scan.csv", index=False)

    train_rows = [run_config(prices, config, TRAIN_START, TRAIN_END) for config in ram_scan_configs]
    train_df = pd.DataFrame(train_rows).sort_values(["calmar", "annual_return"], ascending=False)
    train_df.to_csv(selected_paths.results_dir / "train_parameter_scan.csv", index=False)

    train_best_config = next(config for config in ram_scan_configs if config.name == train_df.iloc[0]["strategy"])
    train_best_oos = pd.Series(run_config(prices, train_best_config, EVAL_START, EVAL_END))
    pd.DataFrame([train_best_oos]).to_csv(selected_paths.results_dir / "train_best_oos_result.csv", index=False)

    shortlist_names = set(scan_df.head(8)["strategy"])
    walk_forward_configs = [config for config in ram_scan_configs if config.name in shortlist_names]
    walk_forward_df = walk_forward_analysis(prices, walk_forward_configs)
    walk_forward_df.to_csv(selected_paths.results_dir / "walk_forward_shortlist_summary.csv", index=False)

    factor_ic_summary = summarize_factor_ic(prices)
    factor_ic_summary.to_csv(selected_paths.results_dir / "factor_ic_summary.csv", index=False)

    write_summary(selected_paths.results_dir, prices, base_df, scan_df, train_df.iloc[0], train_best_oos)
    write_manifest(selected_paths.results_dir, prices, base_df, scan_df, train_df)
    return ExperimentResult(base_df=base_df, scan_df=scan_df, train_df=train_df, train_best_oos=train_best_oos)


def main() -> None:
    run_experiment()
    print((ExperimentPaths().results_dir / "latest_summary.md").read_text(encoding="utf-8"))
