from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Any

import tushare as ts

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import finite, json_safe
from backend.app.database import SessionLocal
from scripts.research.analyze_concept_entry_edges import add_rank, avg, fmt_delta, fmt_pct, previous_trade_date, stat, trade_key, window_labels
from scripts.research.run_research_round import RUNS_ROOT, now_text, read_json, write_json, write_text


RANK_KEYS = [
    "dcConceptPctMaxRank",
    "dcConceptPctMeanRank",
    "dcConceptTurnoverMaxRank",
    "dcConceptTurnoverMeanRank",
    "dcConceptAmountMaxRank",
    "dcConceptAmountMeanRank",
    "dcConceptSwingMaxRank",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose prior-day Eastmoney concept-board strength for completed entries.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--source-run", default="002-repair-indicator-ablate-ma-001", help="Portfolio run with completedTrades.")
    parser.add_argument("--sleep-seconds", type=float, default=0.10, help="Sleep between Tushare calls.")
    args = parser.parse_args()

    token = os.getenv("TUSHARE_TOKEN")
    if not token:
        raise SystemExit("TUSHARE_TOKEN is not configured in the api container.")

    started_at = now_text()
    run_dir = RUNS_ROOT / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    source = read_json(RUNS_ROOT / args.source_run / "results.json")
    trades = source["result"]["completedTrades"]

    with SessionLocal() as db:
        prior_dates = {
            trade_key(trade): previous_trade_date(db, trade["ts_code"], date.fromisoformat(trade["entryDate"]))
            for trade in trades
        }

    unique_dates = sorted({item for item in prior_dates.values() if item})
    pro = ts.pro_api(token)
    board_by_date: dict[str, dict[str, dict[str, Any]]] = {}
    fetch_errors: list[dict[str, Any]] = []
    per_date: list[dict[str, Any]] = []
    for item in unique_dates:
        trade_date = item.strftime("%Y%m%d")
        try:
            df = pro.dc_daily(trade_date=trade_date)
            ranked = rank_dc_boards(df)
            board_by_date[item.isoformat()] = ranked
            per_date.append({"tradeDate": item.isoformat(), "status": "fetched", "rows": 0 if df is None else len(df), "conceptBoards": len(ranked)})
        except Exception as exc:
            error = {"tradeDate": item.isoformat(), "endpoint": "dc_daily", "error": type(exc).__name__, "message": str(exc)[:200]}
            fetch_errors.append(error)
            per_date.append({"tradeDate": item.isoformat(), "status": "error", "error": error})
        sleep(args.sleep_seconds)

    member_by_pair: dict[tuple[str, str], list[str]] = {}
    per_pair: list[dict[str, Any]] = []
    pairs = sorted({(prior_dates[trade_key(trade)].isoformat(), trade["ts_code"]) for trade in trades if prior_dates.get(trade_key(trade))})
    for prior_date, ts_code in pairs:
        try:
            df = pro.dc_member(trade_date=prior_date.replace("-", ""), con_code=ts_code)
            concepts = concept_codes_from_member(df)
            member_by_pair[(prior_date, ts_code)] = concepts
            per_pair.append({"tradeDate": prior_date, "ts_code": ts_code, "status": "fetched", "concepts": len(concepts)})
        except Exception as exc:
            error = {"tradeDate": prior_date, "ts_code": ts_code, "endpoint": "dc_member", "error": type(exc).__name__, "message": str(exc)[:200]}
            fetch_errors.append(error)
            per_pair.append({"tradeDate": prior_date, "ts_code": ts_code, "status": "error", "error": error})
        sleep(args.sleep_seconds)

    samples = []
    for trade in trades:
        prior_date = prior_dates.get(trade_key(trade))
        prior_text = prior_date.isoformat() if prior_date else None
        board_metrics = board_by_date.get(prior_text or "", {})
        concept_codes = member_by_pair.get((prior_text or "", trade["ts_code"]), [])
        metrics = aggregate_stock_concepts(concept_codes, board_metrics)
        samples.append(
            {
                "ts_code": trade["ts_code"],
                "name": trade.get("name"),
                "industry": trade.get("industry"),
                "entryDate": trade["entryDate"],
                "priorDate": prior_text,
                "returnPct": trade.get("returnPct"),
                "netPnl": trade.get("netPnl"),
                "isWinner": bool(float(trade.get("returnPct") or 0) > 0),
                "windowLabels": window_labels(trade["entryDate"]),
                "dcConceptCodes": concept_codes[:20],
                **metrics,
            }
        )

    output = {
        "runId": args.run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "sourceRun": args.source_run,
        "sourceCompletedTrades": len(trades),
        "uniquePriorDates": len(unique_dates),
        "uniqueDateSymbolPairs": len(pairs),
        "fetchErrors": fetch_errors,
        "perDate": per_date,
        "perPair": per_pair,
        "coverage": coverage_summary(samples),
        "overall": summarize_groups(samples),
        "weakWindows": {label: summarize_groups([item for item in samples if label in item["windowLabels"]]) for label in ["Y1", "R18-1", "Y3"]},
        "thresholds": threshold_summary(samples),
        "samples": samples,
    }
    write_json(run_dir / "results.json", json_safe(output))
    write_text(run_dir / "review.md", render_review(output))
    print(json.dumps({"runId": args.run_id, "coverage": output["coverage"], "overall": output["overall"], "runDir": str(run_dir)}, ensure_ascii=False, indent=2))


def sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def rank_dc_boards(df: Any) -> dict[str, dict[str, Any]]:
    if df is None or df.empty:
        return {}
    rows = []
    for item in df.to_dict("records"):
        if str(item.get("category") or "") != "概念板块":
            continue
        ts_code = item.get("ts_code")
        if not ts_code:
            continue
        rows.append(
            {
                "dcConceptCode": str(ts_code),
                "dcConceptPctChange": number(item.get("pct_change")),
                "dcConceptTurnoverRate": number(item.get("turnover_rate")),
                "dcConceptAmount": number(item.get("amount")),
                "dcConceptSwing": number(item.get("swing")),
            }
        )
    add_rank(rows, "dcConceptPctChange", "dcConceptPctRank")
    add_rank(rows, "dcConceptTurnoverRate", "dcConceptTurnoverRank")
    add_rank(rows, "dcConceptAmount", "dcConceptAmountRank")
    add_rank(rows, "dcConceptSwing", "dcConceptSwingRank")
    return {str(item["dcConceptCode"]): item for item in rows}


def number(value: Any) -> float | None:
    return float(value) if finite(value) else None


def concept_codes_from_member(df: Any) -> list[str]:
    if df is None or df.empty:
        return []
    codes = []
    seen = set()
    for item in df.to_dict("records"):
        code = item.get("ts_code")
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(str(code))
    return codes


def aggregate_stock_concepts(concept_codes: list[str], board_metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    matched = [board_metrics[code] for code in concept_codes if code in board_metrics]
    pct_ranks = values(matched, "dcConceptPctRank")
    turnover_ranks = values(matched, "dcConceptTurnoverRank")
    amount_ranks = values(matched, "dcConceptAmountRank")
    swing_ranks = values(matched, "dcConceptSwingRank")
    pct_changes = values(matched, "dcConceptPctChange")
    return {
        "dcConceptCount": len(concept_codes),
        "dcConceptMatched": len(matched),
        "dcConceptPositiveRatio": sum(1 for item in pct_changes if item > 0) / len(pct_changes) if pct_changes else None,
        "dcConceptPctMaxRank": max(pct_ranks) if pct_ranks else None,
        "dcConceptPctMeanRank": mean(pct_ranks) if pct_ranks else None,
        "dcConceptTurnoverMaxRank": max(turnover_ranks) if turnover_ranks else None,
        "dcConceptTurnoverMeanRank": mean(turnover_ranks) if turnover_ranks else None,
        "dcConceptAmountMaxRank": max(amount_ranks) if amount_ranks else None,
        "dcConceptAmountMeanRank": mean(amount_ranks) if amount_ranks else None,
        "dcConceptSwingMaxRank": max(swing_ranks) if swing_ranks else None,
    }


def values(rows: list[dict[str, Any]], key: str) -> list[float]:
    return [float(item[key]) for item in rows if finite(item.get(key))]


def summarize_groups(samples: list[dict[str, Any]]) -> dict[str, Any]:
    winners = [item for item in samples if item["isWinner"]]
    losers = [item for item in samples if not item["isWinner"]]
    return {
        "all": summarize_samples(samples),
        "winners": summarize_samples(winners),
        "losers": summarize_samples(losers),
        "winnerMinusLoser": compare_groups(winners, losers),
    }


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(samples),
        "avgReturnPct": avg([item.get("returnPct") for item in samples]),
        "winRate": sum(1 for item in samples if item["isWinner"]) / len(samples) if samples else None,
        "dcMatchedRate": sum(1 for item in samples if finite(item.get("dcConceptPctMaxRank"))) / len(samples) if samples else None,
        "dcConceptCount": stat([item.get("dcConceptCount") for item in samples]),
        "dcConceptMatched": stat([item.get("dcConceptMatched") for item in samples]),
        "dcConceptPositiveRatio": stat([item.get("dcConceptPositiveRatio") for item in samples]),
        **{key: stat([item.get(key) for item in samples]) for key in RANK_KEYS},
    }


def compare_groups(winners: list[dict[str, Any]], losers: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for key in ["dcConceptCount", "dcConceptMatched", "dcConceptPositiveRatio", *RANK_KEYS]:
        win_avg = avg([item.get(key) for item in winners])
        loss_avg = avg([item.get(key) for item in losers])
        result[key] = win_avg - loss_avg if win_avg is not None and loss_avg is not None else None
    return result


def threshold_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ["dcConceptPctMaxRank", "dcConceptPctMeanRank", "dcConceptTurnoverMaxRank", "dcConceptAmountMaxRank"]:
        for threshold in [0.5, 0.7]:
            kept = [item for item in samples if finite(item.get(key)) and float(item[key]) >= threshold]
            filtered = [item for item in samples if finite(item.get(key)) and float(item[key]) < threshold]
            result[f"{key}>={threshold}"] = {"kept": summarize_samples(kept), "filtered": summarize_samples(filtered)}
    return result


def coverage_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    matched = [item for item in samples if finite(item.get("dcConceptPctMaxRank"))]
    return {
        "samples": len(samples),
        "dcMatched": len(matched),
        "dcMatchedRate": len(matched) / len(samples) if samples else None,
        "avgConceptCount": avg([item.get("dcConceptCount") for item in samples]),
        "avgMatchedConceptCount": avg([item.get("dcConceptMatched") for item in samples]),
    }


def render_review(output: dict[str, Any]) -> str:
    lines = [
        f"# {output['runId']} 东财概念板块强度诊断",
        "",
        f"- 来源 run：`{output['sourceRun']}`",
        f"- 样本：完成交易 `{output['sourceCompletedTrades']}` 笔，唯一前一交易日 `{output['uniquePriorDates']}` 个，日期-标的对 `{output['uniqueDateSymbolPairs']}` 个。",
        "- 口径：每笔交易只使用入场日前一交易日 `dc_member(con_code=股票)` 和 `dc_daily(trade_date)` 的概念板块行情，避免使用入场日收盘后数据。",
        "",
        "## 覆盖率",
        "",
        f"- 东财概念匹配：`{output['coverage']['dcMatched']}/{output['coverage']['samples']}`，覆盖率 {fmt_pct(output['coverage']['dcMatchedRate'])}。",
        f"- 平均概念数量：`{format_number(output['coverage']['avgConceptCount'])}`；平均匹配概念数量：`{format_number(output['coverage']['avgMatchedConceptCount'])}`。",
        f"- 拉取错误：`{len(output['fetchErrors'])}`。",
        "",
        "## 赢家 vs 输家",
        "",
        "| 样本 | 数量 | 胜率 | 平均收益 | 概念涨幅最高排名差 | 概念涨幅均值排名差 | 概念换手最高排名差 | 概念成交额最高排名差 | 概念正收益比例差 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    rows = [("全样本", output["overall"])] + [(label, output["weakWindows"][label]) for label in ["Y1", "R18-1", "Y3"]]
    for label, summary in rows:
        diff = summary["winnerMinusLoser"]
        all_summary = summary["all"]
        lines.append(
            f"| {label} | `{all_summary['count']}` | {fmt_pct(all_summary.get('winRate'))} | {fmt_pct(all_summary.get('avgReturnPct'))} | "
            f"{fmt_delta(diff.get('dcConceptPctMaxRank'))} | {fmt_delta(diff.get('dcConceptPctMeanRank'))} | "
            f"{fmt_delta(diff.get('dcConceptTurnoverMaxRank'))} | {fmt_delta(diff.get('dcConceptAmountMaxRank'))} | "
            f"{fmt_delta(diff.get('dcConceptPositiveRatio'))} |"
        )
    lines.extend(["", "## 简单阈值", ""])
    for key, item in output["thresholds"].items():
        kept = item["kept"]
        filtered = item["filtered"]
        lines.append(
            f"- `{key}`：保留 `{kept['count']}` 笔，平均收益 {fmt_pct(kept.get('avgReturnPct'))}、胜率 {fmt_pct(kept.get('winRate'))}；"
            f"被过滤 `{filtered['count']}` 笔，平均收益 {fmt_pct(filtered.get('avgReturnPct'))}、胜率 {fmt_pct(filtered.get('winRate'))}。"
        )
    if output["fetchErrors"]:
        lines.extend(["", "## 拉取错误样本", ""])
        for item in output["fetchErrors"][:20]:
            lines.append(f"- `{item.get('tradeDate')}` `{item.get('ts_code', '')}` {item['endpoint']} {item['error']}: {item['message']}")
    lines.append("")
    return "\n".join(lines)


def format_number(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.2f}"


if __name__ == "__main__":
    main()
