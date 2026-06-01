from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import finite, json_safe
from backend.app.database import SessionLocal
from backend.app.models import StockDailyBar
from scripts.research.analyze_trade_delta import profit_loss_ratio
from scripts.research.run_research_round import RUNS_ROOT, format_optional_percent, format_optional_ratio, now_text, read_json, write_json, write_text


WINDOWS = {
    "Y1": ("2023-05-30", "2024-05-30"),
    "Y2": ("2024-05-30", "2025-05-30"),
    "Y3": ("2025-05-30", "2026-05-30"),
    "R18-1": ("2023-05-30", "2024-11-30"),
    "R18-2": ("2023-11-30", "2025-05-30"),
    "R18-3": ("2024-05-30", "2025-11-30"),
    "R18-4": ("2024-11-30", "2026-05-30"),
}

VARIANTS = [
    {"name": "be_after_3", "kind": "lock", "armPct": 0.03, "lockPct": 0.0},
    {"name": "lock2_after_5", "kind": "lock", "armPct": 0.05, "lockPct": 0.02},
    {"name": "lock3_after_5", "kind": "lock", "armPct": 0.05, "lockPct": 0.03},
    {"name": "trail50_after_5", "kind": "trail", "armPct": 0.05, "trailRatio": 0.50, "floorPct": 0.0},
    {"name": "time5_no_3", "kind": "time_no_hit", "day": 5, "targetPct": 0.03},
    {"name": "time5_no_5", "kind": "time_no_hit", "day": 5, "targetPct": 0.05},
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-screen non-comparable exit protection variants on completed-trade paths.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--source-run", default="002-repair-indicator-ablate-ma-001", help="Portfolio run with completedTrades.")
    args = parser.parse_args()

    started_at = now_text()
    source = read_json(RUNS_ROOT / args.source_run / "results.json")
    trades = source["result"].get("completedTrades", [])
    if not trades:
        raise SystemExit(f"No completed trades in {args.source_run}.")

    ts_codes = sorted({str(trade["ts_code"]) for trade in trades})
    min_entry = min(date.fromisoformat(str(trade["entryDate"])) for trade in trades)
    max_exit = max(date.fromisoformat(str(trade["exitDate"])) for trade in trades)
    with SessionLocal() as db:
        bars_by_code = query_bars(db, ts_codes, min_entry, max_exit + timedelta(days=10))

    variant_samples: dict[str, list[dict[str, Any]]] = {item["name"]: [] for item in VARIANTS}
    for trade in trades:
        bars = bars_by_code.get(str(trade["ts_code"]), [])
        for variant in VARIANTS:
            variant_samples[variant["name"]].append(simulate_variant(trade, bars, variant))

    output = {
        "runId": args.run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "sourceRun": args.source_run,
        "completedTradeCount": len(trades),
        "mode": "non_comparable_prescreen",
        "semanticWarning": "This diagnostic changes exit assumptions on already completed trades only; it is not a shared-capital portfolio backtest and must not be compared as a stage pass.",
        "variants": {name: summarize_variant(samples) for name, samples in variant_samples.items()},
        "windows": {name: summarize_windows(samples) for name, samples in variant_samples.items()},
        "bestByDelta": sorted([summarize_variant(samples) for samples in variant_samples.values()], key=lambda item: item["avgDeltaPct"], reverse=True),
        "samples": variant_samples,
    }
    run_dir = RUNS_ROOT / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "results.json", json_safe(output))
    write_text(run_dir / "review.md", render_review(output))
    print(json.dumps({"runId": args.run_id, "bestByDelta": output["bestByDelta"][:3], "runDir": str(run_dir)}, ensure_ascii=False, indent=2))


def query_bars(db: Any, ts_codes: list[str], start_date: date, end_date: date) -> dict[str, list[dict[str, Any]]]:
    stmt = (
        select(
            StockDailyBar.ts_code,
            StockDailyBar.trade_date,
            StockDailyBar.open,
            StockDailyBar.high,
            StockDailyBar.low,
            StockDailyBar.close,
        )
        .where(
            StockDailyBar.ts_code.in_(ts_codes),
            StockDailyBar.trade_date >= start_date,
            StockDailyBar.trade_date <= end_date,
        )
        .order_by(StockDailyBar.ts_code, StockDailyBar.trade_date)
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in db.execute(stmt):
        grouped[row.ts_code].append(
            {
                "date": row.trade_date.isoformat(),
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
            }
        )
    return dict(grouped)


def simulate_variant(trade: dict[str, Any], bars: list[dict[str, Any]], variant: dict[str, Any]) -> dict[str, Any]:
    actual_return = float(trade.get("returnPct") or 0.0)
    entry_price = float(trade["entryPrice"])
    entry_date = str(trade["entryDate"])
    exit_date = str(trade["exitDate"])
    index_by_date = {bar["date"]: index for index, bar in enumerate(bars)}
    entry_index = index_by_date.get(entry_date)
    exit_index = index_by_date.get(exit_date)
    path = bars[entry_index + 1 : exit_index + 1] if entry_index is not None and exit_index is not None and exit_index > entry_index else []

    protected_return = actual_return
    protected_date = exit_date
    protected_reason = "actual_exit"
    armed = False
    peak_return = 0.0
    target_hit = False

    for day_index, bar in enumerate(path, start=1):
        high_return = bar["high"] / entry_price - 1
        low_return = bar["low"] / entry_price - 1
        close_return = bar["close"] / entry_price - 1

        stop_pct = active_stop_pct(variant, armed, peak_return)
        if stop_pct is not None and low_return <= stop_pct:
            protected_return = stop_pct
            protected_date = bar["date"]
            protected_reason = f"{variant['name']}_stop"
            break

        if variant["kind"] == "time_no_hit":
            if high_return >= float(variant["targetPct"]):
                target_hit = True
            if day_index >= int(variant["day"]) and not target_hit:
                protected_return = close_return
                protected_date = bar["date"]
                protected_reason = f"{variant['name']}_time_exit"
                break
            continue

        if high_return >= float(variant["armPct"]):
            armed = True
        peak_return = max(peak_return, high_return)

    return {
        "variant": variant["name"],
        "ts_code": trade.get("ts_code"),
        "name": trade.get("name"),
        "industry": trade.get("industry"),
        "entryDate": entry_date,
        "actualExitDate": exit_date,
        "protectedExitDate": protected_date,
        "actualReturnPct": actual_return,
        "protectedReturnPct": protected_return,
        "deltaPct": protected_return - actual_return,
        "changed": protected_reason != "actual_exit",
        "protectedReason": protected_reason,
        "actualExitPriceRule": trade.get("exitPriceRule"),
        "actualExitReason": trade.get("exitReason"),
        "windowLabels": window_labels(entry_date),
    }


def active_stop_pct(variant: dict[str, Any], armed: bool, peak_return: float) -> float | None:
    if not armed:
        return None
    if variant["kind"] == "lock":
        return float(variant["lockPct"])
    if variant["kind"] == "trail":
        return max(float(variant.get("floorPct", 0.0)), peak_return * float(variant["trailRatio"]))
    return None


def summarize_variant(samples: list[dict[str, Any]]) -> dict[str, Any]:
    actual_returns = [float(item["actualReturnPct"]) for item in samples]
    protected_returns = [float(item["protectedReturnPct"]) for item in samples]
    deltas = [float(item["deltaPct"]) for item in samples]
    changed = [item for item in samples if item["changed"]]
    improved = [item for item in samples if float(item["deltaPct"]) > 0]
    worsened = [item for item in samples if float(item["deltaPct"]) < 0]
    return {
        "variant": samples[0]["variant"] if samples else "",
        "count": len(samples),
        "actualAvgReturnPct": mean(actual_returns) if actual_returns else None,
        "protectedAvgReturnPct": mean(protected_returns) if protected_returns else None,
        "avgDeltaPct": mean(deltas) if deltas else None,
        "medianDeltaPct": median_value(deltas),
        "changedCount": len(changed),
        "changedRate": len(changed) / len(samples) if samples else None,
        "improvedCount": len(improved),
        "worsenedCount": len(worsened),
        "winRate": sum(1 for item in protected_returns if item > 0) / len(protected_returns) if protected_returns else None,
        "profitLossRatio": profit_loss_ratio([{"returnPct": item["protectedReturnPct"]} for item in samples]),
        "worstProtectedReturnPct": min(protected_returns) if protected_returns else None,
        "bestChangedExamples": sample_examples(improved, reverse=True),
        "worstChangedExamples": sample_examples(worsened, reverse=False),
    }


def summarize_windows(samples: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for label, bounds in WINDOWS.items():
        selected = [item for item in samples if in_window(item["entryDate"], bounds)]
        result[label] = summarize_variant(selected) if selected else {}
    return result


def median_value(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def sample_examples(samples: list[dict[str, Any]], reverse: bool) -> list[dict[str, Any]]:
    ordered = sorted(samples, key=lambda item: float(item["deltaPct"]), reverse=reverse)[:8]
    return [
        {
            "ts_code": item["ts_code"],
            "name": item["name"],
            "entryDate": item["entryDate"],
            "actualExitDate": item["actualExitDate"],
            "protectedExitDate": item["protectedExitDate"],
            "actualReturnPct": item["actualReturnPct"],
            "protectedReturnPct": item["protectedReturnPct"],
            "deltaPct": item["deltaPct"],
            "protectedReason": item["protectedReason"],
            "actualExitPriceRule": item["actualExitPriceRule"],
        }
        for item in ordered
    ]


def in_window(entry_date: str, bounds: tuple[str, str]) -> bool:
    current = date.fromisoformat(entry_date)
    return date.fromisoformat(bounds[0]) <= current <= date.fromisoformat(bounds[1])


def window_labels(entry_date: str) -> list[str]:
    return [label for label, bounds in WINDOWS.items() if in_window(entry_date, bounds)]


def render_review(output: dict[str, Any]) -> str:
    lines = [
        f"# {output['runId']} 非可比退出保护预筛",
        "",
        f"- 来源 run：`{output['sourceRun']}`",
        f"- 完成交易：`{output['completedTradeCount']}`",
        "- 口径：只在已完成交易路径上模拟更早保护退出，不释放资金、不生成新买入、不作为组合阶段通过证据。",
        f"- 语义警告：{output['semanticWarning']}",
        "",
        "## 全样本候选",
        "",
        "| 候选 | 保护后均值 | 均值增量 | 改变交易 | 改善/恶化 | 胜率 | 盈亏比 | 最差保护收益 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in output["bestByDelta"]:
        lines.append(render_variant_row(item))
    lines.extend(["", "## 弱窗口增量", ""])
    lines.append("| 候选 | Y1增量 | R18-1增量 | Y3增量 | R18-4增量 |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for item in output["bestByDelta"]:
        variant = item["variant"]
        windows = output["windows"][variant]
        lines.append(
            f"| `{variant}` | {fmt_pct((windows.get('Y1') or {}).get('avgDeltaPct'))} | "
            f"{fmt_pct((windows.get('R18-1') or {}).get('avgDeltaPct'))} | "
            f"{fmt_pct((windows.get('Y3') or {}).get('avgDeltaPct'))} | "
            f"{fmt_pct((windows.get('R18-4') or {}).get('avgDeltaPct'))} |"
        )
    best = output["bestByDelta"][0] if output["bestByDelta"] else None
    if best:
        lines.extend(["", f"## `{best['variant']}` 样本", "", "改善最大的样本："])
        for item in best["bestChangedExamples"][:8]:
            lines.append(render_example(item))
        lines.extend(["", "恶化最大的样本："])
        for item in best["worstChangedExamples"][:8]:
            lines.append(render_example(item))
    lines.append("")
    return "\n".join(lines)


def render_variant_row(item: dict[str, Any]) -> str:
    return (
        f"| `{item['variant']}` | {fmt_pct(item.get('protectedAvgReturnPct'))} | {fmt_pct(item.get('avgDeltaPct'))} | "
        f"`{item['changedCount']}`/{item['count']} | `{item['improvedCount']}`/`{item['worsenedCount']}` | "
        f"{fmt_pct(item.get('winRate'))} | {format_optional_ratio(item.get('profitLossRatio'))} | {fmt_pct(item.get('worstProtectedReturnPct'))} |"
    )


def render_example(item: dict[str, Any]) -> str:
    return (
        f"- `{item['ts_code']}` {item.get('name')} {item['entryDate']}->{item['actualExitDate']}："
        f"实际 {fmt_pct(item.get('actualReturnPct'))}，保护 {fmt_pct(item.get('protectedReturnPct'))}，"
        f"差额 {fmt_pct(item.get('deltaPct'))}，保护退出日 `{item['protectedExitDate']}`，原因 `{item['protectedReason']}`。"
    )


def fmt_pct(value: Any) -> str:
    return format_optional_percent(value)


if __name__ == "__main__":
    main()
