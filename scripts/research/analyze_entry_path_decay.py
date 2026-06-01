from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any

from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import finite, json_safe
from backend.app.database import SessionLocal
from backend.app.models import StockDailyBar
from scripts.research.run_research_round import RUNS_ROOT, format_optional_percent, now_text, read_json, write_json, write_text


WINDOWS = {
    "Y1": ("2023-05-30", "2024-05-30"),
    "Y2": ("2024-05-30", "2025-05-30"),
    "Y3": ("2025-05-30", "2026-05-30"),
    "R18-1": ("2023-05-30", "2024-11-30"),
    "R18-2": ("2023-11-30", "2025-05-30"),
    "R18-3": ("2024-05-30", "2025-11-30"),
    "R18-4": ("2024-11-30", "2026-05-30"),
}

TARGETS = (0.03, 0.05, 0.10)


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose post-entry path decay for completed portfolio trades.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--source-run", default="002-repair-indicator-ablate-ma-001", help="Portfolio run with completedTrades.")
    parser.add_argument("--horizons", default="1,3,5,10,20", help="Comma-separated forward trading-day horizons.")
    args = parser.parse_args()

    horizons = parse_horizons(args.horizons)
    max_horizon = max(horizons)
    started_at = now_text()
    source = read_json(RUNS_ROOT / args.source_run / "results.json")
    trades = source["result"]["completedTrades"]
    if not trades:
        raise SystemExit(f"No completed trades in {args.source_run}.")

    ts_codes = sorted({str(trade["ts_code"]) for trade in trades})
    min_entry = min(date.fromisoformat(str(trade["entryDate"])) for trade in trades)
    max_exit = max(date.fromisoformat(str(trade["exitDate"])) for trade in trades)

    with SessionLocal() as db:
        bars_by_code = query_bars(db, ts_codes, min_entry, max_exit + timedelta(days=max_horizon * 3 + 15))

    samples = [analyze_trade(trade, bars_by_code.get(str(trade["ts_code"]), []), horizons) for trade in trades]
    output = {
        "runId": args.run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "sourceRun": args.source_run,
        "horizons": horizons,
        "completedTradeCount": len(trades),
        "coverage": coverage_summary(samples, horizons),
        "overall": summarize_samples(samples, horizons),
        "windows": {label: summarize_samples(select_window(samples, bounds), horizons) for label, bounds in WINDOWS.items()},
        "exitRules": summarize_by_exit_rule(samples, horizons),
        "opportunityExamples": opportunity_examples(samples),
        "stopAfterMfeExamples": stop_after_mfe_examples(samples),
        "samples": samples,
    }

    run_dir = RUNS_ROOT / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "results.json", json_safe(output))
    write_text(run_dir / "review.md", render_review(output))
    print(json.dumps({"runId": args.run_id, "coverage": output["coverage"], "overall": compact_summary(output["overall"]), "runDir": str(run_dir)}, ensure_ascii=False, indent=2))


def parse_horizons(text: str) -> list[int]:
    horizons = sorted({int(item.strip()) for item in text.split(",") if item.strip()})
    if not horizons or horizons[0] <= 0:
        raise SystemExit("--horizons must contain positive integers.")
    return horizons


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


def analyze_trade(trade: dict[str, Any], bars: list[dict[str, Any]], horizons: list[int]) -> dict[str, Any]:
    entry_date = str(trade["entryDate"])
    entry_price = float(trade["entryPrice"])
    index_by_date = {bar["date"]: index for index, bar in enumerate(bars)}
    entry_index = index_by_date.get(entry_date)
    exit_index = index_by_date.get(str(trade["exitDate"]))
    future = bars[entry_index + 1 :] if entry_index is not None else []
    to_exit = bars[entry_index + 1 : exit_index + 1] if entry_index is not None and exit_index is not None and exit_index > entry_index else []
    actual_return = float(trade.get("returnPct") or 0.0)

    sample: dict[str, Any] = {
        "ts_code": trade.get("ts_code"),
        "name": trade.get("name"),
        "industry": trade.get("industry"),
        "entryDate": entry_date,
        "exitDate": trade.get("exitDate"),
        "entryPrice": entry_price,
        "exitPrice": trade.get("exitPrice"),
        "actualReturnPct": actual_return,
        "netPnl": trade.get("netPnl"),
        "exitReason": trade.get("exitReason"),
        "exitPriceRule": trade.get("exitPriceRule"),
        "holdingBars": exit_index - entry_index if entry_index is not None and exit_index is not None else None,
        "windowLabels": window_labels(entry_date),
        "pathAvailableBars": len(future),
        "mfeToExitPct": max_return(to_exit, entry_price, "high"),
        "maeToExitPct": min_return(to_exit, entry_price, "low"),
    }
    for target in TARGETS:
        sample[f"hit{target_key(target)}ToExit"] = finite(sample.get("mfeToExitPct")) and float(sample["mfeToExitPct"]) >= target
    for horizon in horizons:
        path = future[:horizon]
        sample[f"closeReturn{horizon}d"] = close_return(path, entry_price)
        sample[f"mfe{horizon}dPct"] = max_return(path, entry_price, "high")
        sample[f"mae{horizon}dPct"] = min_return(path, entry_price, "low")
        sample[f"mfeGiveback{horizon}dPct"] = giveback(sample[f"mfe{horizon}dPct"], actual_return)
    for target in TARGETS:
        sample[f"hit{target_key(target)}Day"] = first_hit_day(future, entry_price, target)
    sample["hitStop5Day"] = first_stop_day(future, entry_price, 0.05)
    sample["hit5BeforeStop"] = hit_before_stop(sample["hit5PctDay"], sample["hitStop5Day"])
    sample["stopAfterMfe3"] = is_stop_exit(sample) and finite(sample.get("mfeToExitPct")) and float(sample["mfeToExitPct"]) >= 0.03
    sample["stopAfterMfe5"] = is_stop_exit(sample) and finite(sample.get("mfeToExitPct")) and float(sample["mfeToExitPct"]) >= 0.05
    return sample


def close_return(path: list[dict[str, Any]], entry_price: float) -> float | None:
    if not path:
        return None
    return path[-1]["close"] / entry_price - 1


def max_return(path: list[dict[str, Any]], entry_price: float, key: str) -> float | None:
    values = [bar[key] / entry_price - 1 for bar in path if finite(bar.get(key))]
    return max(values) if values else None


def min_return(path: list[dict[str, Any]], entry_price: float, key: str) -> float | None:
    values = [bar[key] / entry_price - 1 for bar in path if finite(bar.get(key))]
    return min(values) if values else None


def giveback(mfe: Any, actual_return: float) -> float | None:
    if not finite(mfe):
        return None
    return float(mfe) - actual_return


def first_hit_day(path: list[dict[str, Any]], entry_price: float, target: float) -> int | None:
    threshold = entry_price * (1 + target)
    for index, bar in enumerate(path, start=1):
        if bar["high"] >= threshold:
            return index
    return None


def first_stop_day(path: list[dict[str, Any]], entry_price: float, stop_pct: float) -> int | None:
    threshold = entry_price * (1 - stop_pct)
    for index, bar in enumerate(path, start=1):
        if bar["low"] <= threshold:
            return index
    return None


def hit_before_stop(hit_day: int | None, stop_day: int | None) -> bool | None:
    if hit_day is None:
        return False
    return stop_day is None or hit_day <= stop_day


def is_stop_exit(sample: dict[str, Any]) -> bool:
    text = f"{sample.get('exitPriceRule') or ''} {sample.get('exitReason') or ''}".lower()
    return "stop" in text or "止损" in text


def target_key(target: float) -> str:
    return f"{int(round(target * 100))}Pct"


def summarize_samples(samples: list[dict[str, Any]], horizons: list[int]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "count": len(samples),
        "actualReturnPct": stat([item.get("actualReturnPct") for item in samples]),
        "winRate": rate(samples, lambda item: float(item.get("actualReturnPct") or 0) > 0),
        "holdingBars": stat([item.get("holdingBars") for item in samples]),
        "mfeToExitPct": stat([item.get("mfeToExitPct") for item in samples]),
        "maeToExitPct": stat([item.get("maeToExitPct") for item in samples]),
        "hit3Rate": rate(samples, lambda item: item.get("hit3PctDay") is not None),
        "hit5Rate": rate(samples, lambda item: item.get("hit5PctDay") is not None),
        "hit10Rate": rate(samples, lambda item: item.get("hit10PctDay") is not None),
        "hit3ToExitRate": rate(samples, lambda item: item.get("hit3PctToExit") is True),
        "hit5ToExitRate": rate(samples, lambda item: item.get("hit5PctToExit") is True),
        "hit10ToExitRate": rate(samples, lambda item: item.get("hit10PctToExit") is True),
        "hit5BeforeStopRate": rate(samples, lambda item: item.get("hit5BeforeStop") is True),
        "stopAfterMfe3Rate": rate(samples, lambda item: item.get("stopAfterMfe3") is True),
        "stopAfterMfe5Rate": rate(samples, lambda item: item.get("stopAfterMfe5") is True),
    }
    for horizon in horizons:
        result[f"closeReturn{horizon}d"] = stat([item.get(f"closeReturn{horizon}d") for item in samples])
        result[f"mfe{horizon}dPct"] = stat([item.get(f"mfe{horizon}dPct") for item in samples])
        result[f"mae{horizon}dPct"] = stat([item.get(f"mae{horizon}dPct") for item in samples])
        result[f"mfeGiveback{horizon}dPct"] = stat([item.get(f"mfeGiveback{horizon}dPct") for item in samples])
    return result


def stat(values: list[Any]) -> dict[str, Any]:
    selected = [float(value) for value in values if finite(value)]
    if not selected:
        return {"avg": None, "median": None, "min": None, "max": None}
    return {"avg": mean(selected), "median": median(selected), "min": min(selected), "max": max(selected)}


def rate(samples: list[dict[str, Any]], predicate: Any) -> float | None:
    if not samples:
        return None
    return sum(1 for item in samples if predicate(item)) / len(samples)


def coverage_summary(samples: list[dict[str, Any]], horizons: list[int]) -> dict[str, Any]:
    return {
        "samples": len(samples),
        "entryBarMatched": sum(1 for item in samples if item["pathAvailableBars"] > 0),
        "entryBarMatchedRate": rate(samples, lambda item: item["pathAvailableBars"] > 0),
        **{f"horizon{horizon}Available": sum(1 for item in samples if item["pathAvailableBars"] >= horizon) for horizon in horizons},
    }


def summarize_by_exit_rule(samples: list[dict[str, Any]], horizons: list[int]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in samples:
        groups[str(item.get("exitPriceRule") or "unknown")].append(item)
    return {name: summarize_samples(items, horizons) for name, items in sorted(groups.items())}


def select_window(samples: list[dict[str, Any]], bounds: tuple[str, str]) -> list[dict[str, Any]]:
    start = date.fromisoformat(bounds[0])
    end = date.fromisoformat(bounds[1])
    return [item for item in samples if start <= date.fromisoformat(str(item["entryDate"])) <= end]


def window_labels(entry_date: str) -> list[str]:
    current = date.fromisoformat(entry_date)
    return [label for label, (start, end) in WINDOWS.items() if date.fromisoformat(start) <= current <= date.fromisoformat(end)]


def opportunity_examples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [
        item
        for item in samples
        if finite(item.get("mfe10dPct")) and float(item["mfe10dPct"]) >= 0.05 and float(item.get("actualReturnPct") or 0) < 0.03
    ]
    selected.sort(key=lambda item: float(item["mfe10dPct"]) - float(item.get("actualReturnPct") or 0), reverse=True)
    return [sample_view(item) for item in selected[:12]]


def stop_after_mfe_examples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [item for item in samples if item.get("stopAfterMfe3")]
    selected.sort(key=lambda item: float(item.get("mfeToExitPct") or 0), reverse=True)
    return [sample_view(item) for item in selected[:12]]


def sample_view(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts_code": item.get("ts_code"),
        "name": item.get("name"),
        "industry": item.get("industry"),
        "entryDate": item.get("entryDate"),
        "exitDate": item.get("exitDate"),
        "actualReturnPct": item.get("actualReturnPct"),
        "mfe10dPct": item.get("mfe10dPct"),
        "mfeToExitPct": item.get("mfeToExitPct"),
        "maeToExitPct": item.get("maeToExitPct"),
        "hit3PctDay": item.get("hit3PctDay"),
        "hit5PctDay": item.get("hit5PctDay"),
        "hitStop5Day": item.get("hitStop5Day"),
        "exitPriceRule": item.get("exitPriceRule"),
        "exitReason": item.get("exitReason"),
    }


def compact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "count": summary["count"],
        "actualReturnAvg": summary["actualReturnPct"]["avg"],
        "closeReturn5dAvg": summary.get("closeReturn5d", {}).get("avg"),
        "closeReturn10dAvg": summary.get("closeReturn10d", {}).get("avg"),
        "mfe10dAvg": summary.get("mfe10dPct", {}).get("avg"),
        "hit5Rate": summary.get("hit5Rate"),
        "hit5ToExitRate": summary.get("hit5ToExitRate"),
        "stopAfterMfe3Rate": summary.get("stopAfterMfe3Rate"),
    }


def render_review(output: dict[str, Any]) -> str:
    horizons = output["horizons"]
    lines = [
        f"# {output['runId']} 入场后路径诊断",
        "",
        f"- 来源 run：`{output['sourceRun']}`",
        f"- 完成交易：`{output['completedTradeCount']}`",
        f"- 前向窗口：`{','.join(str(item) for item in horizons)}` 个交易日。",
        "- 口径：入场发生在信号日收盘价附近，因此前向路径从入场日后的下一交易日开始；本诊断不改变任何买卖规则。",
        "",
        "## 覆盖率",
        "",
        f"- 入场后有日线样本：`{output['coverage']['entryBarMatched']}/{output['coverage']['samples']}`，覆盖率 {fmt_pct(output['coverage']['entryBarMatchedRate'])}。",
    ]
    lines.extend(["", "## 窗口摘要", ""])
    lines.append("| 窗口 | 交易 | 实际均值 | Fwd5收盘 | Fwd10收盘 | MFE10 | 持有内MFE | 持有内触及5% | 10日触及5% | 止损前曾到3% |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for label, summary in [("ALL", output["overall"])] + [(label, output["windows"][label]) for label in ["Y1", "R18-1", "Y3", "R18-4"]]:
        lines.append(render_summary_row(label, summary))
    lines.extend(["", "## 按退出规则", ""])
    lines.append("| 退出规则 | 交易 | 实际均值 | MFE10 | 持有内MFE | 持有内触及5% | 止损前曾到3% |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for label, summary in output["exitRules"].items():
        lines.append(
            f"| `{label}` | `{summary['count']}` | {fmt_pct(summary['actualReturnPct']['avg'])} | "
            f"{fmt_pct(summary.get('mfe10dPct', {}).get('avg'))} | {fmt_pct(summary.get('mfeToExitPct', {}).get('avg'))} | "
            f"{fmt_pct(summary.get('hit5ToExitRate'))} | {fmt_pct(summary.get('stopAfterMfe3Rate'))} |"
        )
    lines.extend(["", "## 机会回吐样本", ""])
    lines.append("MFE10 达到 5% 但实际收益低于 3% 的样本：")
    for item in output["opportunityExamples"][:10]:
        lines.append(
            f"- `{item['ts_code']}` {item.get('name')} {item['entryDate']}->{item['exitDate']}："
            f"实际 {fmt_pct(item.get('actualReturnPct'))}，MFE10 {fmt_pct(item.get('mfe10dPct'))}，"
            f"触及5%日 {item.get('hit5PctDay') or 'n/a'}，退出 `{item.get('exitPriceRule')}`。"
        )
    lines.extend(["", "## 止损前曾有浮盈样本", ""])
    for item in output["stopAfterMfeExamples"][:10]:
        lines.append(
            f"- `{item['ts_code']}` {item.get('name')} {item['entryDate']}->{item['exitDate']}："
            f"实际 {fmt_pct(item.get('actualReturnPct'))}，持有内MFE {fmt_pct(item.get('mfeToExitPct'))}，"
            f"持有内MAE {fmt_pct(item.get('maeToExitPct'))}，退出 `{item.get('exitPriceRule')}`。"
        )
    lines.append("")
    return "\n".join(lines)


def render_summary_row(label: str, summary: dict[str, Any]) -> str:
    return (
        f"| `{label}` | `{summary['count']}` | {fmt_pct(summary['actualReturnPct']['avg'])} | "
        f"{fmt_pct(summary.get('closeReturn5d', {}).get('avg'))} | {fmt_pct(summary.get('closeReturn10d', {}).get('avg'))} | "
        f"{fmt_pct(summary.get('mfe10dPct', {}).get('avg'))} | {fmt_pct(summary.get('mfeToExitPct', {}).get('avg'))} | "
        f"{fmt_pct(summary.get('hit5ToExitRate'))} | {fmt_pct(summary.get('hit5Rate'))} | {fmt_pct(summary.get('stopAfterMfe3Rate'))} |"
    )


def fmt_pct(value: Any) -> str:
    return format_optional_percent(value)


if __name__ == "__main__":
    main()
