from __future__ import annotations

from collections.abc import Sequence

import pandas as pd


DEFAULT_EXPERIMENT_ARTIFACTS = [
    "base_strategy_comparison.csv",
    "ram_parameter_scan.csv",
    "train_parameter_scan.csv",
    "train_best_oos_result.csv",
    "walk_forward_shortlist_summary.csv",
    "factor_ic_summary.csv",
    "latest_summary.md",
    "latest_summary.json",
    "experiment_manifest.json",
]


def percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def format_markdown_value(value: object, floatfmt: str) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, float):
        return format(value, floatfmt)
    return str(value)


def fallback_markdown_table(frame: pd.DataFrame, index: bool, floatfmt: str) -> str:
    table = frame.reset_index() if index else frame.copy()
    headers = [str(column) for column in table.columns]
    rows = [[format_markdown_value(value, floatfmt) for value in row] for row in table.itertuples(index=False, name=None)]
    widths = [max([len(headers[column_index])] + [len(row[column_index]) for row in rows]) for column_index in range(len(headers))]

    def render_row(values: list[str]) -> str:
        cells = [value.ljust(widths[index]) for index, value in enumerate(values)]
        return "| " + " | ".join(cells) + " |"

    separator = "| " + " | ".join("-" * width for width in widths) + " |"
    return "\n".join([render_row(headers), separator, *[render_row(row) for row in rows]])


def markdown_table(frame: pd.DataFrame, index: bool = False, floatfmt: str = ".4f") -> str:
    try:
        return frame.to_markdown(index=index, floatfmt=floatfmt)
    except ImportError:
        return fallback_markdown_table(frame, index=index, floatfmt=floatfmt)


def select_best_candidate(
    base_df: pd.DataFrame,
    scan_df: pd.DataFrame,
    baseline_strategy: str = "baseline_china_permanent_25_annual_no_cost",
    max_drawdown_floor: float = -0.12,
) -> tuple[pd.Series, str]:
    baseline = base_df.loc[base_df["strategy"] == baseline_strategy].iloc[0]
    qualified = scan_df[
        (scan_df["annual_return"] > baseline["annual_return"])
        & (scan_df["calmar"] > baseline["calmar"])
        & (scan_df["max_drawdown"] >= max_drawdown_floor)
    ].copy()
    if qualified.empty:
        best = scan_df.sort_values(["calmar", "annual_return"], ascending=False).iloc[0]
        gate_text = "没有参数组同时通过年化、卡玛比和 -12% 回撤门槛；以下为按卡玛比排序的研究候选。"
    else:
        best = qualified.sort_values(["calmar", "annual_return"], ascending=False).iloc[0]
        gate_text = "存在参数组同时通过年化、卡玛比和 -12% 回撤门槛；以下为当前研究候选。"
    return best, gate_text


def build_summary_payload(
    prices: pd.DataFrame,
    base_df: pd.DataFrame,
    scan_df: pd.DataFrame,
    train_best: pd.Series,
    test_best: pd.Series,
    evaluation_start: str,
    evaluation_end: str,
    baseline_strategy: str = "baseline_china_permanent_25_annual_no_cost",
) -> dict[str, float | str]:
    baseline = base_df.loc[base_df["strategy"] == baseline_strategy].iloc[0]
    best, _gate_text = select_best_candidate(base_df, scan_df, baseline_strategy=baseline_strategy)
    return {
        "evaluation_start": evaluation_start,
        "evaluation_end": evaluation_end,
        "latest_price_date": str(prices.index.max().date()),
        "baseline_strategy": str(baseline["strategy"]),
        "best_research_candidate": str(best["strategy"]),
        "baseline_annual_return": float(baseline["annual_return"]),
        "best_annual_return": float(best["annual_return"]),
        "best_max_drawdown": float(best["max_drawdown"]),
        "best_calmar": float(best["calmar"]),
        "train_best_strategy": str(train_best["strategy"]),
        "train_best_oos_annual_return": float(test_best["annual_return"]),
        "train_best_oos_max_drawdown": float(test_best["max_drawdown"]),
        "train_best_oos_calmar": float(test_best["calmar"]),
    }


def build_manifest_payload(
    latest_price_date: str,
    base_df: pd.DataFrame,
    scan_df: pd.DataFrame,
    train_df: pd.DataFrame,
    evaluation_start: str,
    evaluation_end: str,
    train_start: str,
    train_end: str,
    artifacts: Sequence[str] = DEFAULT_EXPERIMENT_ARTIFACTS,
    experiment: str = "permanent_portfolio_alpha_research",
) -> dict[str, object]:
    best, _gate_text = select_best_candidate(base_df, scan_df)
    return {
        "experiment": experiment,
        "evaluation_start": evaluation_start,
        "evaluation_end": evaluation_end,
        "train_start": train_start,
        "train_end": train_end,
        "latest_price_date": latest_price_date,
        "best_research_candidate": str(best["strategy"]),
        "base_strategy_rows": int(len(base_df)),
        "ram_scan_rows": int(len(scan_df)),
        "train_scan_rows": int(len(train_df)),
        "artifacts": list(artifacts),
    }
