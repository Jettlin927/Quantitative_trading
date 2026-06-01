from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable
from statistics import mean
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import json_safe
from scripts.research.analyze_trade_delta import profit_loss_ratio
from scripts.research.run_research_round import RUNS_ROOT, format_optional_percent, format_optional_ratio, now_text, read_json, write_json, write_text
from scripts.research.screen_exit_protection_variants import WINDOWS, in_window


Predicate = Callable[[dict[str, Any]], bool]
MIN_TARGETED_DELTA_FOR_FORMAL_TEST = 0.002


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose observable state triggers for non-comparable exit protection variants.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--prescreen-run", default="002-exit-protection-prescreen-001", help="Exit protection prescreen run.")
    parser.add_argument("--source-run", default="002-repair-indicator-ablate-ma-001", help="Portfolio run with completedTrades.")
    parser.add_argument("--variants", default="time5_no_3,lock2_after_5,be_after_3,trail50_after_5", help="Comma-separated variant names.")
    parser.add_argument("--min-samples", type=int, default=8, help="Minimum selected trades for the top-candidate table.")
    args = parser.parse_args()

    started_at = now_text()
    source = read_json(RUNS_ROOT / args.source_run / "results.json")
    prescreen = read_json(RUNS_ROOT / args.prescreen_run / "results.json")
    trades = source["result"].get("completedTrades", [])
    trade_lookup = {(str(trade["ts_code"]), str(trade["entryDate"])): trade for trade in trades}
    variants = [name.strip() for name in args.variants.split(",") if name.strip()]

    conditions = build_conditions()
    by_variant: dict[str, list[dict[str, Any]]] = {}
    for variant in variants:
        samples = attach_entry_state(prescreen["samples"].get(variant, []), trade_lookup)
        by_variant[variant] = sorted(
            [summarize_condition(variant, condition, samples) for condition in conditions],
            key=lambda item: (safe_float(item.get("targetedAvgDeltaPct")), safe_float(item.get("selectedAvgDeltaPct"))),
            reverse=True,
        )

    all_summaries = [item for rows in by_variant.values() for item in rows]
    best_observable = sorted(
        [
            item
            for item in all_summaries
            if item["signalType"] == "observable"
            and int(item["selectedCount"]) >= args.min_samples
            and item.get("targetedAvgDeltaPct") is not None
        ],
        key=lambda item: (safe_float(item.get("targetedAvgDeltaPct")), safe_float(item.get("selectedAvgDeltaPct"))),
        reverse=True,
    )
    best_diagnostic = sorted(
        [
            item
            for item in all_summaries
            if item["signalType"] == "diagnostic"
            and int(item["selectedCount"]) >= args.min_samples
            and item.get("targetedAvgDeltaPct") is not None
        ],
        key=lambda item: (safe_float(item.get("targetedAvgDeltaPct")), safe_float(item.get("selectedAvgDeltaPct"))),
        reverse=True,
    )
    output = {
        "runId": args.run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "sourceRun": args.source_run,
        "prescreenRun": args.prescreen_run,
        "variants": variants,
        "minSamples": args.min_samples,
        "mode": "non_comparable_state_trigger_diagnostic",
        "semanticWarning": "This diagnostic only buckets completed-trade path deltas by entry-observable state; it is not a shared-capital portfolio backtest and must not be compared as a stage pass.",
        "conditionsByVariant": by_variant,
        "bestObservable": best_observable[:30],
        "bestDiagnosticOnly": best_diagnostic[:20],
        "takeaway": infer_takeaway(best_observable, args.min_samples),
    }
    run_dir = RUNS_ROOT / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "results.json", json_safe(output))
    write_text(run_dir / "review.md", render_review(output))
    print(
        json.dumps(
            {
                "runId": args.run_id,
                "takeaway": output["takeaway"],
                "bestObservable": output["bestObservable"][:5],
                "runDir": str(run_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def build_conditions() -> list[dict[str, Any]]:
    return [
        observable("entry_range_ge_5", "入场日振幅>=5%", lambda item: ge(item, "entryRangePct", 0.05)),
        observable("entry_range_ge_7", "入场日振幅>=7%", lambda item: ge(item, "entryRangePct", 0.07)),
        observable("entry_gap_ge_4", "入场缺口>=4%", lambda item: ge(item, "gapPct", 0.04)),
        observable("entry_intraday_ge_4", "入场日内涨幅>=4%", lambda item: ge(item, "intradayReturnPct", 0.04)),
        observable("upper_shadow_ge_3", "入场上影>=3%", lambda item: ge(item, "upperShadowPct", 0.03)),
        observable("prior_gapdown60_ge_3", "60日最大低开>=3%", lambda item: ge(item, "priorGapDown60Pct", 0.03)),
        observable("prior_gapdown3_count_ge_1", "60日低开3%以上次数>=1", lambda item: ge(item, "priorGapDown3Count60", 1.0)),
        observable("rsi_balance_low", "RSI均衡排名<=20%", lambda item: le(item, "rsiBalanceRank", 0.20)),
        observable("rsi_balance_high", "RSI均衡排名>=80%", lambda item: ge(item, "rsiBalanceRank", 0.80)),
        observable("boll_squeeze_low", "BOLL收口排名<=20%", lambda item: le(item, "bollSqueezeRank", 0.20)),
        observable("boll_squeeze_high", "BOLL收口排名>=80%", lambda item: ge(item, "bollSqueezeRank", 0.80)),
        observable("macd_hist_low", "MACD柱排名<=30%", lambda item: le(item, "macdHistRank", 0.30)),
        observable("macd_hist_high", "MACD柱排名>=80%", lambda item: ge(item, "macdHistRank", 0.80)),
        observable("boll_position_high", "BOLL位置排名>=80%", lambda item: ge(item, "bollPositionRank", 0.80)),
        observable("high60_high", "60日新高排名>=80%", lambda item: ge(item, "high60Rank", 0.80)),
        observable("return20_high", "20日收益排名>=80%", lambda item: ge(item, "return20Rank", 0.80)),
        observable("volume_high", "量能排名>=80%", lambda item: ge(item, "volumeRank", 0.80)),
        observable(
            "risk_gap_or_range",
            "高缺口或高振幅",
            lambda item: ge(item, "gapPct", 0.04) or ge(item, "entryRangePct", 0.07),
        ),
        observable(
            "hot_momentum_low_rsi_balance",
            "MACD+BOLL过热且RSI不均衡",
            lambda item: ge(item, "macdHistRank", 0.80) and ge(item, "bollPositionRank", 0.80) and le(item, "rsiBalanceRank", 0.20),
        ),
        observable(
            "strong_return_weak_squeeze",
            "20日强但BOLL不收口",
            lambda item: ge(item, "return20Rank", 0.80) and le(item, "bollSqueezeRank", 0.20),
        ),
        observable(
            "hot_open_low_rsi_balance",
            "当日追高且RSI不均衡",
            lambda item: ge(item, "intradayReturnPct", 0.04) and le(item, "rsiBalanceRank", 0.20),
        ),
        observable(
            "indicator_setup_balanced",
            "MACD+BOLL+RSI均衡确认",
            lambda item: ge(item, "macdHistRank", 0.60)
            and ge(item, "bollSqueezeRank", 0.60)
            and ge(item, "rsiBalanceRank", 0.60)
            and le(item, "bollPositionRank", 0.85),
        ),
        diagnostic("actual_stop_exit", "实际止损退出", lambda item: str(item.get("actualExitPriceRule") or "").endswith("stop")),
        diagnostic("actual_gap_stop_exit", "实际跳空止损退出", lambda item: str(item.get("actualExitPriceRule") or "") == "gap_open_stop"),
    ]


def observable(name: str, label: str, predicate: Predicate) -> dict[str, Any]:
    return {"name": name, "label": label, "signalType": "observable", "predicate": predicate}


def diagnostic(name: str, label: str, predicate: Predicate) -> dict[str, Any]:
    return {"name": name, "label": label, "signalType": "diagnostic", "predicate": predicate}


def attach_entry_state(samples: list[dict[str, Any]], trade_lookup: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    merged = []
    for sample in samples:
        trade = trade_lookup.get((str(sample["ts_code"]), str(sample["entryDate"])), {})
        item = dict(sample)
        item["entryRiskMetrics"] = trade.get("entryRiskMetrics") or {}
        item["entryScoreParts"] = trade.get("entryScoreParts") or {}
        item["entryScore"] = trade.get("entryScore")
        merged.append(item)
    return merged


def summarize_condition(variant: str, condition: dict[str, Any], samples: list[dict[str, Any]]) -> dict[str, Any]:
    predicate = condition["predicate"]
    selected = [item for item in samples if predicate(item)]
    unselected = [item for item in samples if item not in selected]
    selected_deltas = [float(item["deltaPct"]) for item in selected]
    total_delta = sum(selected_deltas) / len(samples) if samples else None
    return {
        "variant": variant,
        "condition": condition["name"],
        "label": condition["label"],
        "signalType": condition["signalType"],
        "totalCount": len(samples),
        "selectedCount": len(selected),
        "selectedRate": len(selected) / len(samples) if samples else None,
        "selectedActualAvgReturnPct": avg_value(selected, "actualReturnPct"),
        "selectedProtectedAvgReturnPct": avg_value(selected, "protectedReturnPct"),
        "selectedAvgDeltaPct": mean(selected_deltas) if selected_deltas else None,
        "targetedAvgDeltaPct": total_delta,
        "unselectedAvgDeltaPct": avg_value(unselected, "deltaPct"),
        "changedCount": sum(1 for item in selected if item.get("changed")),
        "improvedCount": sum(1 for item in selected if float(item["deltaPct"]) > 0),
        "worsenedCount": sum(1 for item in selected if float(item["deltaPct"]) < 0),
        "protectedWinRate": sum(1 for item in selected if float(item["protectedReturnPct"]) > 0) / len(selected) if selected else None,
        "protectedProfitLossRatio": profit_loss_ratio([{"returnPct": item["protectedReturnPct"]} for item in selected]),
        "windowTargetedDeltas": summarize_window_deltas(samples, selected),
        "bestExamples": sample_examples(selected, reverse=True),
        "worstExamples": sample_examples(selected, reverse=False),
    }


def summarize_window_deltas(samples: list[dict[str, Any]], selected: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for label, bounds in WINDOWS.items():
        window_all = [item for item in samples if in_window(str(item["entryDate"]), bounds)]
        window_selected = [item for item in selected if in_window(str(item["entryDate"]), bounds)]
        result[label] = {
            "totalCount": len(window_all),
            "selectedCount": len(window_selected),
            "targetedAvgDeltaPct": sum(float(item["deltaPct"]) for item in window_selected) / len(window_all) if window_all else None,
            "selectedAvgDeltaPct": avg_value(window_selected, "deltaPct"),
        }
    return result


def sample_examples(samples: list[dict[str, Any]], reverse: bool) -> list[dict[str, Any]]:
    if reverse:
        changed = [item for item in samples if float(item["deltaPct"]) > 1e-12]
    else:
        changed = [item for item in samples if float(item["deltaPct"]) < -1e-12]
    ordered = sorted(changed, key=lambda item: float(item["deltaPct"]), reverse=reverse)[:6]
    return [
        {
            "ts_code": item["ts_code"],
            "name": item.get("name"),
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


def value(item: dict[str, Any], key: str) -> float | None:
    raw = item.get("entryRiskMetrics", {}).get(key)
    if raw is None:
        raw = item.get("entryScoreParts", {}).get(key)
    if raw is None:
        raw = item.get(key)
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def ge(item: dict[str, Any], key: str, threshold: float) -> bool:
    number = value(item, key)
    return number is not None and number >= threshold


def le(item: dict[str, Any], key: str, threshold: float) -> bool:
    number = value(item, key)
    return number is not None and number <= threshold


def avg_value(samples: list[dict[str, Any]], key: str) -> float | None:
    values = [float(item[key]) for item in samples if item.get(key) is not None]
    return mean(values) if values else None


def safe_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("-inf")
    return number if math.isfinite(number) else float("-inf")


def infer_takeaway(best_observable: list[dict[str, Any]], min_samples: int) -> str:
    usable = [
        item
        for item in best_observable
        if item["selectedCount"] >= min_samples
        and safe_float(item.get("selectedAvgDeltaPct")) > 0
        and safe_float(item.get("targetedAvgDeltaPct")) >= MIN_TARGETED_DELTA_FOR_FORMAL_TEST
    ]
    if not usable:
        best = best_observable[0] if best_observable else None
        if best:
            return (
                f"未发现样本数足够且定向全样本增量达到 {fmt_pct(MIN_TARGETED_DELTA_FOR_FORMAL_TEST)} 观察线的入场可观察触发条件；"
                f"当前最高为 `{best['variant']}` + `{best['condition']}`，命中 {best['selectedCount']}/{best['totalCount']}，"
                f"定向全样本增量仅 {fmt_pct(best['targetedAvgDeltaPct'])}。退出保护仍不应进入正式组合回测。"
            )
        return "未发现样本数足够的入场可观察触发条件；退出保护仍不应进入正式组合回测。"
    best = usable[0]
    return (
        f"最强入场可观察条件是 `{best['variant']}` + `{best['condition']}`，"
        f"命中 {best['selectedCount']}/{best['totalCount']}，定向全样本增量 {fmt_pct(best['targetedAvgDeltaPct'])}；"
        "只能作为下一轮非可比组合回测候选，不能作为阶段通过证据。"
    )


def render_review(output: dict[str, Any]) -> str:
    lines = [
        f"# {output['runId']} 退出保护状态触发诊断",
        "",
        f"- 来源组合 run：`{output['sourceRun']}`",
        f"- 来源保护预筛：`{output['prescreenRun']}`",
        f"- 候选保护：`{', '.join(output['variants'])}`",
        f"- 最小样本：`{output['minSamples']}`",
        "- 口径：只按入场当时可观察的风险指标与复合指标分桶，未释放资金、未生成新买入、不是阶段通过证据。",
        f"- 语义警告：{output['semanticWarning']}",
        "",
        "## 结论",
        "",
        output["takeaway"],
        "",
        "## 入场可观察触发条件 Top 15",
        "",
        "| 保护 | 条件 | 命中 | 选中均值增量 | 定向全样本增量 | 未选中均值增量 | 改善/恶化 | Y1定向 | R18-1定向 | Y3定向 | R18-4定向 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in output["bestObservable"][:15]:
        lines.append(render_condition_row(item))
    lines.extend(["", "## 诊断条件 Top 8（不可作为交易触发）", ""])
    lines.append("| 保护 | 条件 | 命中 | 选中均值增量 | 定向全样本增量 | 改善/恶化 |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: |")
    for item in output["bestDiagnosticOnly"][:8]:
        lines.append(
            f"| `{item['variant']}` | {item['label']} | `{item['selectedCount']}`/`{item['totalCount']}` | "
            f"{fmt_pct(item.get('selectedAvgDeltaPct'))} | {fmt_pct(item.get('targetedAvgDeltaPct'))} | "
            f"`{item['improvedCount']}`/`{item['worsenedCount']}` |"
        )
    best = output["bestObservable"][0] if output["bestObservable"] else None
    if best:
        lines.extend(["", f"## 最强可观察条件样本：`{best['variant']}` + `{best['condition']}`"])
        if best["bestExamples"]:
            lines.extend(["", "改善样本："])
            for item in best["bestExamples"]:
                lines.append(render_example(item))
        if best["worstExamples"]:
            lines.extend(["", "恶化样本："])
            for item in best["worstExamples"]:
                lines.append(render_example(item))
    lines.append("")
    return "\n".join(lines)


def render_condition_row(item: dict[str, Any]) -> str:
    windows = item.get("windowTargetedDeltas") or {}
    return (
        f"| `{item['variant']}` | {item['label']} | `{item['selectedCount']}`/`{item['totalCount']}` | "
        f"{fmt_pct(item.get('selectedAvgDeltaPct'))} | {fmt_pct(item.get('targetedAvgDeltaPct'))} | "
        f"{fmt_pct(item.get('unselectedAvgDeltaPct'))} | `{item['improvedCount']}`/`{item['worsenedCount']}` | "
        f"{fmt_pct((windows.get('Y1') or {}).get('targetedAvgDeltaPct'))} | "
        f"{fmt_pct((windows.get('R18-1') or {}).get('targetedAvgDeltaPct'))} | "
        f"{fmt_pct((windows.get('Y3') or {}).get('targetedAvgDeltaPct'))} | "
        f"{fmt_pct((windows.get('R18-4') or {}).get('targetedAvgDeltaPct'))} |"
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
