from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from .config import (
    EVAL_END,
    EVAL_START,
    ExperimentPaths,
    SATELLITE_DEFENSE_ASSET,
    SATELLITE_MAX_DRAWDOWN_FLOOR,
    SATELLITE_TARGET_ANNUAL_RETURN,
    SATELLITE_UNIVERSE,
    TRAIN_END,
    TRAIN_START,
)
from .data import load_prices
from .satellite import (
    SatelliteConfig,
    build_satellite_final_markdown,
    compute_asset_diagnostics,
    fixed_blend_configs,
    run_fixed_blend_config,
    run_satellite_config,
    satellite_config_grid,
    select_satellite_candidate,
)


@dataclass(frozen=True)
class SatelliteExperimentResult:
    asset_diagnostics: pd.DataFrame
    scan_df: pd.DataFrame
    train_df: pd.DataFrame
    oos_df: pd.DataFrame
    walk_forward_df: pd.DataFrame


def satellite_date_windows(index: pd.DatetimeIndex) -> dict[str, str]:
    if len(index) < 252:
        raise ValueError("Satellite experiment needs at least 252 observations")

    eval_start = max(pd.Timestamp(EVAL_START), index[0])
    eval_end = min(pd.Timestamp(EVAL_END), index[-1])
    train_start = index[0]
    configured_train_end = pd.Timestamp(TRAIN_END)

    if train_start < configured_train_end < eval_end:
        train_end = configured_train_end
    else:
        train_end_pos = max(252, min(int(len(index) * 0.45), len(index) - 253))
        train_end = index[train_end_pos]

    oos_start_pos = index.get_indexer([train_end], method="nearest")[0] + 1
    if oos_start_pos >= len(index):
        raise ValueError("Satellite experiment cannot create a non-empty OOS window")
    oos_start = index[oos_start_pos]

    return {
        "eval_start": str(eval_start.date()),
        "eval_end": str(eval_end.date()),
        "train_start": str(train_start.date()),
        "train_end": str(train_end.date()),
        "oos_start": str(oos_start.date()),
        "oos_end": str(index[-1].date()),
    }


def _best_config_for_window(
    prices: pd.DataFrame,
    configs: list[SatelliteConfig],
    fixed_configs: list[dict[str, object]],
    train_start: str,
    train_end: str,
) -> tuple[SatelliteConfig | dict[str, object], pd.Series]:
    rows = [run_satellite_config(prices, config, train_start, train_end) for config in configs]
    rows.extend(_fixed_blend_rows(prices, fixed_configs, train_start, train_end))
    df = pd.DataFrame(rows)
    best, _gate = select_satellite_candidate(df)
    config = _find_config(str(best["strategy"]), configs, fixed_configs)
    return config, best


def _fixed_blend_rows(
    prices: pd.DataFrame,
    fixed_configs: list[dict[str, object]],
    start: str,
    end: str,
) -> list[dict[str, float | str | bool]]:
    return [
        run_fixed_blend_config(
            prices=prices,
            name=str(config["name"]),
            weights=config["weights"],  # type: ignore[arg-type]
            gross_exposure=float(config["gross_exposure"]),
            eval_start=start,
            eval_end=end,
        )
        for config in fixed_configs
    ]


def _find_config(
    name: str,
    configs: list[SatelliteConfig],
    fixed_configs: list[dict[str, object]],
) -> SatelliteConfig | dict[str, object]:
    for config in configs:
        if config.name == name:
            return config
    for config in fixed_configs:
        if config["name"] == name:
            return config
    raise ValueError(f"Unknown satellite strategy: {name}")


def _run_any_config(
    prices: pd.DataFrame,
    config: SatelliteConfig | dict[str, object],
    start: str,
    end: str,
) -> dict[str, float | str | bool]:
    if isinstance(config, SatelliteConfig):
        return run_satellite_config(prices, config, start, end)
    return run_fixed_blend_config(
        prices=prices,
        name=str(config["name"]),
        weights=config["weights"],  # type: ignore[arg-type]
        gross_exposure=float(config["gross_exposure"]),
        eval_start=start,
        eval_end=end,
    )


def satellite_walk_forward(
    prices: pd.DataFrame,
    configs: list[SatelliteConfig],
    fixed_configs: list[dict[str, object]] | None = None,
    train_size: int = 504,
    test_size: int = 126,
    step_size: int = 126,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    index = prices.index
    selected_fixed_configs = fixed_configs or []
    for mode, anchored in [("rolling", False), ("anchored", True)]:
        start_pos = 0
        window_id = 1
        while start_pos + train_size + test_size <= len(index):
            train_start_pos = 0 if anchored else start_pos
            train_end_pos = start_pos + train_size - 1
            test_start_pos = train_end_pos + 1
            test_end_pos = test_start_pos + test_size - 1
            train_start = index[train_start_pos]
            train_end = index[train_end_pos]
            test_start = index[test_start_pos]
            test_end = index[test_end_pos]
            best_config, train_best = _best_config_for_window(
                prices,
                configs,
                selected_fixed_configs,
                str(train_start.date()),
                str(train_end.date()),
            )
            oos = _run_any_config(prices, best_config, str(test_start.date()), str(test_end.date()))
            rows.append(
                {
                    "mode": mode,
                    "window_id": window_id,
                    "train_start": str(train_start.date()),
                    "train_end": str(train_end.date()),
                    "test_start": str(test_start.date()),
                    "test_end": str(test_end.date()),
                    "best_strategy": best_config.name if isinstance(best_config, SatelliteConfig) else str(best_config["name"]),
                    "best_strategy_family": str(train_best.get("strategy_family", "")),
                    "train_annual_return": float(train_best["annual_return"]),
                    "train_max_drawdown": float(train_best["max_drawdown"]),
                    "train_passes_return_gate": bool(train_best["passes_return_gate"]),
                    "train_passes_drawdown_gate": bool(train_best["passes_drawdown_gate"]),
                    "oos_annual_return": float(oos["annual_return"]),
                    "oos_max_drawdown": float(oos["max_drawdown"]),
                    "oos_calmar": float(oos["calmar"]),
                    "oos_passes_return_gate": bool(oos["passes_return_gate"]),
                    "oos_passes_drawdown_gate": bool(oos["passes_drawdown_gate"]),
                }
            )
            start_pos += step_size
            window_id += 1
    return pd.DataFrame(rows)


def run_satellite_experiment(paths: ExperimentPaths | None = None) -> SatelliteExperimentResult:
    selected_paths = paths or ExperimentPaths()
    selected_paths.results_dir.mkdir(parents=True, exist_ok=True)
    prices = load_prices(
        symbols=SATELLITE_UNIVERSE,
        data_dir=selected_paths.data_dir,
        dropna=False,
        required_symbols=[SATELLITE_DEFENSE_ASSET],
        repair_splits=True,
    )
    windows = satellite_date_windows(prices.index)
    configs = satellite_config_grid()
    fixed_configs = fixed_blend_configs()

    asset_diagnostics = compute_asset_diagnostics(prices)
    asset_diagnostics.to_csv(selected_paths.results_dir / "satellite_asset_diagnostics.csv", index=False)

    scan_rows = [run_satellite_config(prices, config, windows["eval_start"], windows["eval_end"]) for config in configs]
    scan_rows.extend(_fixed_blend_rows(prices, fixed_configs, windows["eval_start"], windows["eval_end"]))
    scan_df = pd.DataFrame(scan_rows)
    scan_df = scan_df.sort_values(["passes_drawdown_gate", "calmar", "annual_return"], ascending=False)
    scan_df.to_csv(selected_paths.results_dir / "satellite_parameter_scan.csv", index=False)
    scan_df[scan_df["strategy_family"] == "fixed_blend"].to_csv(selected_paths.results_dir / "satellite_fixed_blend_scan.csv", index=False)

    train_rows = [run_satellite_config(prices, config, windows["train_start"], windows["train_end"]) for config in configs]
    train_rows.extend(_fixed_blend_rows(prices, fixed_configs, windows["train_start"], windows["train_end"]))
    train_df = pd.DataFrame(train_rows)
    train_df = train_df.sort_values(["passes_drawdown_gate", "calmar", "annual_return"], ascending=False)
    train_df.to_csv(selected_paths.results_dir / "satellite_train_parameter_scan.csv", index=False)

    train_best, _train_gate = select_satellite_candidate(train_df)
    train_best_config = _find_config(str(train_best["strategy"]), configs, fixed_configs)
    oos_df = pd.DataFrame([_run_any_config(prices, train_best_config, windows["oos_start"], windows["oos_end"])])
    oos_df.to_csv(selected_paths.results_dir / "satellite_oos_result.csv", index=False)

    shortlist_names = set(scan_df.head(8)["strategy"])
    shortlist_configs = [config for config in configs if config.name in shortlist_names]
    shortlist_fixed_configs = [config for config in fixed_configs if config["name"] in shortlist_names]
    walk_forward_df = satellite_walk_forward(prices, shortlist_configs, shortlist_fixed_configs)
    walk_forward_df.to_csv(selected_paths.results_dir / "satellite_walk_forward.csv", index=False)

    candidate, gate = select_satellite_candidate(scan_df)
    final_markdown = build_satellite_final_markdown(candidate, gate, scan_df)
    (selected_paths.results_dir / "satellite_final_candidate.md").write_text(final_markdown, encoding="utf-8")

    manifest = {
        "experiment": "satellite_50pct_dd30",
        "target_annual_return": SATELLITE_TARGET_ANNUAL_RETURN,
        "max_drawdown_floor": SATELLITE_MAX_DRAWDOWN_FLOOR,
        "latest_price_date": str(prices.index.max().date()),
        "windows": windows,
        "best_candidate": str(candidate["strategy"]),
        "has_passing_candidate": bool(gate["has_passing_candidate"]),
        "artifacts": [
            "satellite_asset_diagnostics.csv",
            "satellite_parameter_scan.csv",
            "satellite_fixed_blend_scan.csv",
            "satellite_train_parameter_scan.csv",
            "satellite_oos_result.csv",
            "satellite_walk_forward.csv",
            "satellite_final_candidate.md",
            "satellite_manifest.json",
        ],
    }
    (selected_paths.results_dir / "satellite_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return SatelliteExperimentResult(
        asset_diagnostics=asset_diagnostics,
        scan_df=scan_df,
        train_df=train_df,
        oos_df=oos_df,
        walk_forward_df=walk_forward_df,
    )


def main() -> None:
    run_satellite_experiment()
    print((ExperimentPaths().results_dir / "satellite_final_candidate.md").read_text(encoding="utf-8"))
