from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .config import COST_RATE, EVAL_END, EVAL_START, TRAIN_END, TRAIN_START


def percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def _format_markdown_value(value: object, floatfmt: str) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, float):
        return format(value, floatfmt)
    return str(value)


def _fallback_markdown_table(frame: pd.DataFrame, index: bool, floatfmt: str) -> str:
    table = frame.reset_index() if index else frame.copy()
    headers = [str(column) for column in table.columns]
    rows = [[_format_markdown_value(value, floatfmt) for value in row] for row in table.itertuples(index=False, name=None)]
    widths = [
        max([len(headers[column_index])] + [len(row[column_index]) for row in rows])
        for column_index in range(len(headers))
    ]

    def render_row(values: list[str]) -> str:
        cells = [value.ljust(widths[index]) for index, value in enumerate(values)]
        return "| " + " | ".join(cells) + " |"

    separator = "| " + " | ".join("-" * width for width in widths) + " |"
    return "\n".join([render_row(headers), separator, *[render_row(row) for row in rows]])


def markdown_table(frame: pd.DataFrame, index: bool = False, floatfmt: str = ".4f") -> str:
    try:
        return frame.to_markdown(index=index, floatfmt=floatfmt)
    except ImportError:
        return _fallback_markdown_table(frame, index=index, floatfmt=floatfmt)


def select_best_candidate(base_df: pd.DataFrame, scan_df: pd.DataFrame) -> tuple[pd.Series, str]:
    baseline = base_df.loc[base_df["strategy"] == "baseline_china_permanent_25_annual_no_cost"].iloc[0]
    qualified = scan_df[
        (scan_df["annual_return"] > baseline["annual_return"])
        & (scan_df["calmar"] > baseline["calmar"])
        & (scan_df["max_drawdown"] >= -0.12)
    ].copy()
    if qualified.empty:
        best = scan_df.sort_values(["calmar", "annual_return"], ascending=False).iloc[0]
        gate_text = "没有参数组同时通过年化、卡玛比和 -12% 回撤门槛；以下为按卡玛比排序的研究候选。"
    else:
        best = qualified.sort_values(["calmar", "annual_return"], ascending=False).iloc[0]
        gate_text = "存在参数组同时通过年化、卡玛比和 -12% 回撤门槛；以下为当前研究候选。"
    return best, gate_text


def build_summary_markdown(
    prices: pd.DataFrame,
    base_df: pd.DataFrame,
    scan_df: pd.DataFrame,
    train_best: pd.Series,
    test_best: pd.Series,
) -> str:
    baseline = base_df.loc[base_df["strategy"] == "baseline_china_permanent_25_annual_no_cost"].iloc[0]
    best, gate_text = select_best_candidate(base_df, scan_df)
    top_scan = scan_df.sort_values(["calmar", "annual_return"], ascending=False).head(10)
    markdown = [
        "# Candidate Backtest Summary",
        "",
        "- Data source: AkShare `fund_etf_hist_sina`, cached under `data_cache/`.",
        f"- Evaluation window: `{EVAL_START}` to `{EVAL_END}`.",
        f"- Latest available date in cache: `{prices.index.max().date()}`.",
        f"- Dynamic strategy cost assumption: `{COST_RATE:.3%}` one-way turnover cost.",
        f"- Baseline annual return: `{percent(baseline['annual_return'])}`; max drawdown: `{percent(baseline['max_drawdown'])}`; Calmar: `{baseline['calmar']:.2f}`.",
        f"- Gate result: {gate_text}",
        "",
        "## Current Best Research Candidate",
        "",
        f"- Strategy: `{best['strategy']}`",
        f"- Annual return: `{percent(best['annual_return'])}`",
        f"- Excess annual return vs baseline: `{percent(best['annual_return'] - baseline['annual_return'])}`",
        f"- Max drawdown: `{percent(best['max_drawdown'])}`",
        f"- Max drawdown difference vs baseline: `{percent(best['max_drawdown'] - baseline['max_drawdown'])}`",
        f"- Calmar: `{best['calmar']:.2f}`",
        f"- Rebalance count: `{int(best['rebalance_count'])}`",
        f"- Estimated cost drag from turnover: `{percent(best['estimated_cost'])}`",
        "",
        "Interpretation: this is the current best research candidate under the lightweight screen, not a production strategy. It still needs full Walk-Forward, factor IC, and notebook-level reproduction before being called stable alpha.",
        "",
        "## Base Strategy Comparison",
        "",
        markdown_table(
            base_df[
                [
                    "strategy",
                    "annual_return",
                    "annual_volatility",
                    "max_drawdown",
                    "sharpe",
                    "calmar",
                    "estimated_cost",
                ]
            ],
            index=False,
            floatfmt=".4f",
        ),
        "",
        "## Top 10 RAM Parameter Scan",
        "",
        markdown_table(
            top_scan[
                [
                    "strategy",
                    "annual_return",
                    "annual_volatility",
                    "max_drawdown",
                    "sharpe",
                    "calmar",
                    "estimated_cost",
                ]
            ],
            index=False,
            floatfmt=".4f",
        ),
        "",
        "## Simple In-Sample / Out-of-Sample Check",
        "",
        f"- Best train-period config selected on `{TRAIN_START}` to `{TRAIN_END}`: `{train_best['strategy']}`.",
        f"- Train annual return: `{percent(train_best['annual_return'])}`; train max drawdown: `{percent(train_best['max_drawdown'])}`; train Calmar: `{train_best['calmar']:.2f}`.",
        f"- Same config on `{EVAL_START}` to `{EVAL_END}` annual return: `{percent(test_best['annual_return'])}`; max drawdown: `{percent(test_best['max_drawdown'])}`; Calmar: `{test_best['calmar']:.2f}`.",
        "",
        "Use this as a warning label: if the train-selected config collapses out of sample, the apparent best current-period parameters are likely path-dependent.",
    ]
    return "\n".join(markdown) + "\n"


def build_summary_payload(
    prices: pd.DataFrame,
    base_df: pd.DataFrame,
    scan_df: pd.DataFrame,
    train_best: pd.Series,
    test_best: pd.Series,
) -> dict[str, float | str]:
    baseline = base_df.loc[base_df["strategy"] == "baseline_china_permanent_25_annual_no_cost"].iloc[0]
    best, _gate_text = select_best_candidate(base_df, scan_df)
    return {
        "evaluation_start": EVAL_START,
        "evaluation_end": EVAL_END,
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
) -> dict[str, object]:
    best, _gate_text = select_best_candidate(base_df, scan_df)
    return {
        "experiment": "permanent_portfolio_alpha_research",
        "evaluation_start": EVAL_START,
        "evaluation_end": EVAL_END,
        "train_start": TRAIN_START,
        "train_end": TRAIN_END,
        "latest_price_date": latest_price_date,
        "best_research_candidate": str(best["strategy"]),
        "base_strategy_rows": int(len(base_df)),
        "ram_scan_rows": int(len(scan_df)),
        "train_scan_rows": int(len(train_df)),
        "artifacts": [
            "base_strategy_comparison.csv",
            "ram_parameter_scan.csv",
            "train_parameter_scan.csv",
            "train_best_oos_result.csv",
            "walk_forward_shortlist_summary.csv",
            "factor_ic_summary.csv",
            "latest_summary.md",
            "latest_summary.json",
            "experiment_manifest.json",
        ],
    }


def write_summary(
    results_dir: Path,
    prices: pd.DataFrame,
    base_df: pd.DataFrame,
    scan_df: pd.DataFrame,
    train_best: pd.Series,
    test_best: pd.Series,
) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    summary = build_summary_markdown(prices, base_df, scan_df, train_best, test_best)
    payload = build_summary_payload(prices, base_df, scan_df, train_best, test_best)
    (results_dir / "latest_summary.md").write_text(summary, encoding="utf-8")
    (results_dir / "latest_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_manifest(
    results_dir: Path,
    prices: pd.DataFrame,
    base_df: pd.DataFrame,
    scan_df: pd.DataFrame,
    train_df: pd.DataFrame,
) -> None:
    payload = build_manifest_payload(
        latest_price_date=str(prices.index.max().date()),
        base_df=base_df,
        scan_df=scan_df,
        train_df=train_df,
    )
    (results_dir / "experiment_manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
