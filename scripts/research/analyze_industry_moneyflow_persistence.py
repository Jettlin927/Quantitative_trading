from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Any

from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import finite, json_safe
from backend.app.database import SessionLocal
from backend.app.models import Stock
from scripts.research.run_research_round import RUNS_ROOT, format_optional_percent, now_text, read_json, write_json, write_text


WINDOWS: dict[str, tuple[str, str] | None] = {
    "ALL": None,
    "Y1": ("2023-05-30", "2024-05-30"),
    "Y2": ("2024-05-30", "2025-05-30"),
    "Y3": ("2025-05-30", "2026-05-30"),
    "R18-1": ("2023-05-30", "2024-11-30"),
    "R18-2": ("2023-11-30", "2025-05-30"),
    "R18-3": ("2024-05-30", "2025-11-30"),
    "R18-4": ("2024-11-30", "2026-05-30"),
}

SUMMARY_KEYS = [
    "prev1IndustrySumNetRank",
    "prev1IndustryAvgRank",
    "prev1IndustryPositiveRatio",
    "prev1IndustryPersistentScore",
    "prev3AvgIndustrySumNetRank",
    "prev3AvgIndustryAvgRank",
    "prev3AvgIndustryPositiveRatio",
    "prev3AvgIndustryPersistentScore",
    "prev5AvgIndustrySumNetRank",
    "prev5AvgIndustryAvgRank",
    "prev5AvgIndustryPositiveRatio",
    "prev5AvgIndustryPersistentScore",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose prior industry moneyflow persistence around trade replacements.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--baseline-run", required=True, help="Baseline portfolio run id.")
    parser.add_argument("--candidate-run", required=True, help="Candidate portfolio run id.")
    parser.add_argument("--moneyflow-cache", required=True, help="Run-local JSONL moneyflow rank cache.")
    parser.add_argument("--windows", default="ALL,Y1,R18-1,Y3,R18-4", help="Comma-separated window labels.")
    args = parser.parse_args()

    started_at = now_text()
    baseline = read_json(RUNS_ROOT / args.baseline_run / "results.json")
    candidate = read_json(RUNS_ROOT / args.candidate_run / "results.json")
    cache_path = resolve_path(args.moneyflow_cache)
    moneyflow_rows = load_moneyflow_rows(cache_path)
    industry_by_code = query_industries(sorted({row["ts_code"] for row in moneyflow_rows if row.get("ts_code")}))
    industry_moneyflow = build_industry_moneyflow(moneyflow_rows, industry_by_code)

    window_mode = bool(baseline.get("windows")) and bool(candidate.get("windows"))
    requested_windows = [item.strip() for item in args.windows.split(",") if item.strip()]
    coverage_samples = [
        analyze_trade(trade, industry_moneyflow)
        for trade in all_completed_trades(baseline) + all_completed_trades(candidate)
    ]
    if window_mode:
        windows = analyze_window_validation(requested_windows, baseline, candidate, industry_moneyflow)
    else:
        baseline_samples = [analyze_trade(trade, industry_moneyflow) for trade in completed_trades(baseline)]
        candidate_samples = [analyze_trade(trade, industry_moneyflow) for trade in completed_trades(candidate)]
        windows = [
            analyze_window(label, WINDOWS[label], baseline_samples, candidate_samples)
            for label in requested_windows
            if label in WINDOWS
        ]

    output = {
        "runId": args.run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "baselineRun": args.baseline_run,
        "candidateRun": args.candidate_run,
        "moneyflowCache": str(cache_path.relative_to(REPO_ROOT)) if cache_path.is_relative_to(REPO_ROOT) else str(cache_path),
        "mode": "window_validation" if window_mode else "full_run",
        "coverage": coverage_summary(coverage_samples),
        "windows": windows,
    }

    run_dir = RUNS_ROOT / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "results.json", json_safe(output))
    write_text(run_dir / "review.md", render_review(output))
    print(json.dumps({"runId": args.run_id, "coverage": output["coverage"], "summary": compact_summary(windows), "runDir": str(run_dir)}, ensure_ascii=False, indent=2))


def resolve_path(path_value: str) -> Path:
    path = Path(path_value)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.exists():
        raise SystemExit(f"Moneyflow cache not found: {path}")
    return path


def load_moneyflow_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            if not item.get("tradeDate") or not item.get("ts_code"):
                continue
            rows.append(
                {
                    "tradeDate": str(item["tradeDate"]),
                    "ts_code": str(item["ts_code"]),
                    "moneyflowMainNet": none_or_float(item.get("moneyflowMainNet")),
                    "moneyflowMainNetRank": none_or_float(item.get("moneyflowMainNetRank")),
                }
            )
    return rows


def query_industries(ts_codes: list[str]) -> dict[str, str]:
    if not ts_codes:
        return {}
    result: dict[str, str] = {}
    batch_size = 900
    with SessionLocal() as db:
        for start in range(0, len(ts_codes), batch_size):
            batch = ts_codes[start : start + batch_size]
            stmt = select(Stock.ts_code, Stock.industry).where(Stock.ts_code.in_(batch))
            for row in db.execute(stmt):
                result[str(row.ts_code)] = str(row.industry or "未知")
    return result


def build_industry_moneyflow(rows: list[dict[str, Any]], industry_by_code: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        industry = industry_by_code.get(str(row["ts_code"]))
        if not industry:
            continue
        grouped[(industry, str(row["tradeDate"]))].append(row)

    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (industry, trade_date), items in grouped.items():
        main_nets = [float(item["moneyflowMainNet"]) for item in items if finite(item.get("moneyflowMainNet"))]
        main_ranks = [float(item["moneyflowMainNetRank"]) for item in items if finite(item.get("moneyflowMainNetRank"))]
        if len(main_nets) < 3 or not main_ranks:
            continue
        by_date[trade_date].append(
            {
                "industry": industry,
                "tradeDate": trade_date,
                "industrySumNet": sum(main_nets),
                "industryAvgNet": mean(main_nets),
                "industryAvgRank": mean(main_ranks),
                "industryPositiveRatio": sum(1 for value in main_nets if value > 0) / len(main_nets),
                "industrySamples": len(main_nets),
            }
        )

    by_industry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade_date, items in by_date.items():
        add_percentile_rank(items, "industrySumNet", "industrySumNetRank")
        add_percentile_rank(items, "industryAvgNet", "industryAvgNetRank")
        add_percentile_rank(items, "industryAvgRank", "industryAvgRankRank")
        add_percentile_rank(items, "industryPositiveRatio", "industryPositiveRatioRank")
        for item in items:
            item["industryPersistentScore"] = (
                float(item["industrySumNetRank"]) * 0.4
                + float(item["industryAvgRankRank"]) * 0.3
                + float(item["industryPositiveRatioRank"]) * 0.3
            )
            by_industry[item["industry"]].append(item)
    for items in by_industry.values():
        items.sort(key=lambda item: item["tradeDate"])
    return dict(by_industry)


def add_percentile_rank(items: list[dict[str, Any]], value_key: str, rank_key: str) -> None:
    valid = sorted([item for item in items if finite(item.get(value_key))], key=lambda item: float(item[value_key]))
    denom = max(1, len(valid) - 1)
    ranks = {id(item): index / denom for index, item in enumerate(valid)}
    for item in items:
        item[rank_key] = ranks.get(id(item), None)


def completed_trades(run: dict[str, Any]) -> list[dict[str, Any]]:
    return list((run.get("result") or {}).get("completedTrades") or [])


def all_completed_trades(run: dict[str, Any]) -> list[dict[str, Any]]:
    if not run.get("windows"):
        return completed_trades(run)
    trades: list[dict[str, Any]] = []
    for window in run.get("windows", []):
        trades.extend((window.get("result") or {}).get("completedTrades") or [])
    return trades


def analyze_trade(trade: dict[str, Any], industry_moneyflow: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    industry = str(trade.get("industry") or "")
    entry_date = str(trade["entryDate"])
    series = industry_moneyflow.get(industry, [])
    prior = [item for item in series if item["tradeDate"] < entry_date]
    sample: dict[str, Any] = {
        "ts_code": trade.get("ts_code"),
        "name": trade.get("name"),
        "industry": industry,
        "entryDate": entry_date,
        "exitDate": trade.get("exitDate"),
        "actualReturnPct": trade.get("returnPct"),
        "netPnl": trade.get("netPnl"),
        "exitPriceRule": trade.get("exitPriceRule"),
        "industryMoneyflowAvailable": bool(prior),
    }
    if not prior:
        return sample
    attach_prefixed_metrics(sample, "prev1", prior[-1])
    attach_average_metrics(sample, "prev3Avg", prior[-3:])
    attach_average_metrics(sample, "prev5Avg", prior[-5:])
    return sample


def attach_prefixed_metrics(sample: dict[str, Any], prefix: str, metrics: dict[str, Any]) -> None:
    sample[f"{prefix}IndustryMoneyflowDate"] = metrics["tradeDate"]
    for key in [
        "industrySumNetRank",
        "industryAvgRank",
        "industryPositiveRatio",
        "industryPersistentScore",
        "industrySamples",
    ]:
        sample[f"{prefix}{key[0].upper()}{key[1:]}"] = metrics.get(key)


def attach_average_metrics(sample: dict[str, Any], prefix: str, items: list[dict[str, Any]]) -> None:
    if not items:
        return
    for key in [
        "industrySumNetRank",
        "industryAvgRank",
        "industryPositiveRatio",
        "industryPersistentScore",
        "industrySamples",
    ]:
        values = [float(item[key]) for item in items if finite(item.get(key))]
        if values:
            sample[f"{prefix}{key[0].upper()}{key[1:]}"] = mean(values)


def analyze_window(
    label: str,
    bounds: tuple[str, str] | None,
    baseline_samples: list[dict[str, Any]],
    candidate_samples: list[dict[str, Any]],
) -> dict[str, Any]:
    return analyze_replacement(label, select_window(baseline_samples, bounds), select_window(candidate_samples, bounds))


def analyze_window_validation(
    labels: list[str],
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    industry_moneyflow: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    baseline_windows = {item["window"]["label"]: item for item in baseline.get("windows", [])}
    candidate_windows = {item["window"]["label"]: item for item in candidate.get("windows", [])}
    windows = []
    if "ALL" in labels:
        windows.append(
            analyze_replacement(
                "ALL",
                [analyze_trade(trade, industry_moneyflow) for trade in all_completed_trades(baseline)],
                [analyze_trade(trade, industry_moneyflow) for trade in all_completed_trades(candidate)],
            )
        )
    for label in labels:
        if label == "ALL":
            continue
        baseline_window = baseline_windows.get(label)
        candidate_window = candidate_windows.get(label)
        if not baseline_window or not candidate_window:
            continue
        windows.append(
            analyze_replacement(
                label,
                [analyze_trade(trade, industry_moneyflow) for trade in (baseline_window.get("result") or {}).get("completedTrades", [])],
                [analyze_trade(trade, industry_moneyflow) for trade in (candidate_window.get("result") or {}).get("completedTrades", [])],
            )
        )
    return windows


def analyze_replacement(label: str, baseline_samples: list[dict[str, Any]], candidate_samples: list[dict[str, Any]]) -> dict[str, Any]:
    baseline_selected = keyed_samples(baseline_samples)
    candidate_selected = keyed_samples(candidate_samples)
    common_keys = set(baseline_selected) & set(candidate_selected)
    baseline_only = [baseline_selected[key] for key in sorted(set(baseline_selected) - set(candidate_selected))]
    candidate_only = [candidate_selected[key] for key in sorted(set(candidate_selected) - set(baseline_selected))]
    return {
        "label": label,
        "baselineTradeCount": len(baseline_selected),
        "candidateTradeCount": len(candidate_selected),
        "commonTradeCount": len(common_keys),
        "baselineOnly": summarize_samples(baseline_only),
        "candidateOnly": summarize_samples(candidate_only),
        "replacementNetPnlDelta": sum_value(candidate_only, "netPnl") - sum_value(baseline_only, "netPnl"),
        "candidateOnlyWorst": sorted_examples(candidate_only, reverse=False),
        "candidateOnlyBest": sorted_examples(candidate_only, reverse=True),
    }


def keyed_samples(samples: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    return {
        (str(item.get("ts_code")), str(item.get("entryDate")), str(item.get("exitDate"))): item
        for item in samples
        if item.get("ts_code") and item.get("entryDate") and item.get("exitDate")
    }


def select_window(samples: list[dict[str, Any]], bounds: tuple[str, str] | None) -> list[dict[str, Any]]:
    if bounds is None:
        return samples
    start, end = [date.fromisoformat(item) for item in bounds]
    return [
        sample
        for sample in samples
        if sample.get("entryDate") and start <= date.fromisoformat(str(sample["entryDate"])) < end
    ]


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "count": len(samples),
        "netPnl": sum_value(samples, "netPnl"),
        "avgActualReturnPct": average(samples, "actualReturnPct"),
        "winRate": ratio_from_values([sample.get("actualReturnPct") for sample in samples], lambda value: value > 0),
        "coverage": coverage_summary(samples),
    }
    for key in SUMMARY_KEYS:
        summary[f"avg{key[0].upper()}{key[1:]}"] = average(samples, key)
    return summary


def sorted_examples(samples: list[dict[str, Any]], reverse: bool) -> list[dict[str, Any]]:
    selected = sorted(samples, key=lambda item: float(item.get("netPnl") or 0), reverse=reverse)[:5]
    return [
        {
            "ts_code": item.get("ts_code"),
            "name": item.get("name"),
            "industry": item.get("industry"),
            "entryDate": item.get("entryDate"),
            "exitDate": item.get("exitDate"),
            "actualReturnPct": item.get("actualReturnPct"),
            "netPnl": item.get("netPnl"),
            "exitPriceRule": item.get("exitPriceRule"),
            "prev1IndustrySumNetRank": item.get("prev1IndustrySumNetRank"),
            "prev1IndustryAvgRank": item.get("prev1IndustryAvgRank"),
            "prev1IndustryPositiveRatio": item.get("prev1IndustryPositiveRatio"),
            "prev1IndustryPersistentScore": item.get("prev1IndustryPersistentScore"),
            "prev3AvgIndustryPersistentScore": item.get("prev3AvgIndustryPersistentScore"),
        }
        for item in selected
    ]


def sum_value(samples: list[dict[str, Any]], key: str) -> float:
    return sum(float(sample.get(key) or 0.0) for sample in samples if finite(sample.get(key)))


def average(samples: list[dict[str, Any]], key: str) -> float | None:
    values = [float(sample[key]) for sample in samples if finite(sample.get(key))]
    return mean(values) if values else None


def ratio_from_values(values: list[Any], predicate: Any) -> float | None:
    valid = [float(value) for value in values if finite(value)]
    return sum(1 for value in valid if predicate(value)) / len(valid) if valid else None


def coverage_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(samples)
    covered = sum(1 for sample in samples if sample.get("industryMoneyflowAvailable"))
    return {"covered": covered, "total": total, "rate": covered / total if total else None}


def compact_summary(windows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        window["label"]: {
            "replacementNetPnlDelta": window["replacementNetPnlDelta"],
            "candidateOnlyCount": window["candidateOnly"]["count"],
            "candidateOnlyPrev1IndustrySumNetRank": window["candidateOnly"].get("avgPrev1IndustrySumNetRank"),
            "candidateOnlyPrev1IndustryPersistentScore": window["candidateOnly"].get("avgPrev1IndustryPersistentScore"),
            "candidateOnlyPrev3IndustryPersistentScore": window["candidateOnly"].get("avgPrev3AvgIndustryPersistentScore"),
        }
        for window in windows
    }


def render_review(output: dict[str, Any]) -> str:
    lines = [
        f"# {output['runId']} 行业资金流持续性诊断",
        "",
        f"- Baseline: `{output['baselineRun']}`",
        f"- Candidate: `{output['candidateRun']}`",
        f"- Moneyflow cache: `{output['moneyflowCache']}`",
        f"- 模式：`{output['mode']}`",
        f"- 覆盖：`{output['coverage']['covered']}/{output['coverage']['total']}`",
        "",
        "## 替换汇总",
        "",
        "| 窗口 | 替换净差 | 候选独有 | 候选前日行业净流rank | 候选前日行业平均rank | 候选前日正流占比 | 候选前日持续分 | 候选3日持续分 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for window in output["windows"]:
        candidate = window["candidateOnly"]
        lines.append(
            "| `{label}` | `{delta:.2f}` | `{count}` | {sum_rank} | {avg_rank} | {pos_ratio} | {score1} | {score3} |".format(
                label=window["label"],
                delta=float(window["replacementNetPnlDelta"]),
                count=candidate["count"],
                sum_rank=format_optional_number(candidate.get("avgPrev1IndustrySumNetRank")),
                avg_rank=format_optional_number(candidate.get("avgPrev1IndustryAvgRank")),
                pos_ratio=format_optional_percent(candidate.get("avgPrev1IndustryPositiveRatio")),
                score1=format_optional_number(candidate.get("avgPrev1IndustryPersistentScore")),
                score3=format_optional_number(candidate.get("avgPrev3AvgIndustryPersistentScore")),
            )
        )
    lines.extend(["", "## 候选独有最差样本", ""])
    for window in output["windows"]:
        if window["label"] not in {"ALL", "Y1", "R18-1", "Y3", "R18-4"}:
            continue
        lines.append(f"### {window['label']}")
        for item in window["candidateOnlyWorst"]:
            lines.append(
                "- `{code}` {name} {entry}->{exit} 收益 {ret}，净盈亏 `{pnl:.2f}`，"
                "前日行业净流rank {sum_rank}，平均rank {avg_rank}，正流占比 {pos_ratio}，持续分 {score1}。".format(
                    code=item.get("ts_code"),
                    name=item.get("name"),
                    entry=item.get("entryDate"),
                    exit=item.get("exitDate"),
                    ret=format_optional_percent(item.get("actualReturnPct")),
                    pnl=float(item.get("netPnl") or 0.0),
                    sum_rank=format_optional_number(item.get("prev1IndustrySumNetRank")),
                    avg_rank=format_optional_number(item.get("prev1IndustryAvgRank")),
                    pos_ratio=format_optional_percent(item.get("prev1IndustryPositiveRatio")),
                    score1=format_optional_number(item.get("prev1IndustryPersistentScore")),
                )
            )
        lines.append("")
    lines.extend(
        [
            "## 结论提示",
            "",
            "- 若失败窗口坏样本的行业持续分低于好样本，可转成 point-in-time 行业资金流持续性确认因子。",
            "- 若持续分只在完整三年有效、滚动失败窗口无分离，则不能作为阶段修复。",
        ]
    )
    return "\n".join(lines) + "\n"


def none_or_float(value: Any) -> float | None:
    return float(value) if finite(value) else None


def format_optional_number(value: Any) -> str:
    return f"`{float(value):.3f}`" if finite(value) else "`n/a`"


if __name__ == "__main__":
    main()
