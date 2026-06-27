from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from backend.app.research_engine.reports import (
    build_manifest_payload as build_backend_manifest_payload,
    build_summary_payload as build_backend_summary_payload,
    markdown_table,
    percent,
    select_best_candidate,
)

from .config import COST_RATE, EVAL_END, EVAL_START, TRAIN_END, TRAIN_START


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
    return build_backend_summary_payload(
        prices,
        base_df,
        scan_df,
        train_best,
        test_best,
        evaluation_start=EVAL_START,
        evaluation_end=EVAL_END,
    )


def build_manifest_payload(
    latest_price_date: str,
    base_df: pd.DataFrame,
    scan_df: pd.DataFrame,
    train_df: pd.DataFrame,
) -> dict[str, object]:
    return build_backend_manifest_payload(
        latest_price_date=latest_price_date,
        base_df=base_df,
        scan_df=scan_df,
        train_df=train_df,
        evaluation_start=EVAL_START,
        evaluation_end=EVAL_END,
        train_start=TRAIN_START,
        train_end=TRAIN_END,
    )


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
