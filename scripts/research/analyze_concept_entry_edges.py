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
from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import finite, json_safe
from backend.app.database import SessionLocal
from backend.app.models import StockDailyBar
from scripts.research.run_research_round import RUNS_ROOT, format_optional_percent, now_text, read_json, write_json, write_text


WEAK_WINDOWS = {
    "Y1": ("2023-05-30", "2024-05-30"),
    "R18-1": ("2023-05-30", "2024-11-30"),
    "Y3": ("2025-05-30", "2026-05-30"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose prior-day KPL concept-membership edge for completed entries.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--source-run", default="002-repair-indicator-ablate-ma-001", help="Portfolio run with completedTrades.")
    parser.add_argument("--sleep-seconds", type=float, default=0.12, help="Sleep between Tushare trade_date calls.")
    args = parser.parse_args()

    token = os.getenv("TUSHARE_TOKEN")
    if not token:
        raise SystemExit("TUSHARE_TOKEN is not configured in the api container.")

    source = read_json(RUNS_ROOT / args.source_run / "results.json")
    trades = source["result"]["completedTrades"]
    started_at = now_text()
    run_dir = RUNS_ROOT / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    with SessionLocal() as db:
        prior_dates = {
            trade_key(trade): previous_trade_date(db, trade["ts_code"], date.fromisoformat(trade["entryDate"]))
            for trade in trades
        }

    unique_dates = sorted({item for item in prior_dates.values() if item})
    pro = ts.pro_api(token)
    concept_by_date: dict[str, dict[str, dict[str, Any]]] = {}
    fetch_errors: list[dict[str, Any]] = []
    per_date: list[dict[str, Any]] = []
    for item in unique_dates:
        trade_date = item.strftime("%Y%m%d")
        try:
            df = pro.kpl_concept_cons(trade_date=trade_date)
            ranked = rank_kpl_concepts(df)
            concept_by_date[item.isoformat()] = ranked
            per_date.append({"tradeDate": item.isoformat(), "status": "fetched", "rows": 0 if df is None else len(df), "stocks": len(ranked)})
        except Exception as exc:
            error = {"tradeDate": item.isoformat(), "endpoint": "kpl_concept_cons", "error": type(exc).__name__, "message": str(exc)[:200]}
            fetch_errors.append(error)
            per_date.append({"tradeDate": item.isoformat(), "status": "error", "error": error})
        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    samples = []
    for trade in trades:
        prior_date = prior_dates.get(trade_key(trade))
        metrics = concept_by_date.get(prior_date.isoformat() if prior_date else "", {}).get(trade["ts_code"], {})
        samples.append(
            {
                "ts_code": trade["ts_code"],
                "name": trade.get("name"),
                "industry": trade.get("industry"),
                "entryDate": trade["entryDate"],
                "priorDate": prior_date.isoformat() if prior_date else None,
                "returnPct": trade.get("returnPct"),
                "netPnl": trade.get("netPnl"),
                "isWinner": bool(float(trade.get("returnPct") or 0) > 0),
                "windowLabels": window_labels(trade["entryDate"]),
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
        "fetchErrors": fetch_errors,
        "perDate": per_date,
        "coverage": coverage_summary(samples),
        "overall": summarize_groups(samples),
        "weakWindows": {label: summarize_groups([item for item in samples if label in item["windowLabels"]]) for label in WEAK_WINDOWS},
        "thresholds": threshold_summary(samples),
        "samples": samples,
    }
    write_json(run_dir / "results.json", json_safe(output))
    write_text(run_dir / "review.md", render_review(output))
    print(json.dumps({"runId": args.run_id, "coverage": output["coverage"], "overall": output["overall"], "runDir": str(run_dir)}, ensure_ascii=False, indent=2))


def previous_trade_date(db: Any, ts_code: str, entry_date: date) -> date | None:
    stmt = (
        select(StockDailyBar.trade_date)
        .where(StockDailyBar.ts_code == ts_code, StockDailyBar.trade_date < entry_date)
        .order_by(StockDailyBar.trade_date.desc())
        .limit(1)
    )
    return db.scalar(stmt)


def rank_kpl_concepts(df: Any) -> dict[str, dict[str, Any]]:
    if df is None or df.empty:
        return {}
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {"concepts": set(), "hotValues": []})
    for item in df.to_dict("records"):
        code = item.get("con_code")
        if not code:
            continue
        hot = float(item.get("hot_num") or 0) if finite(item.get("hot_num")) else 0.0
        row = grouped[str(code)]
        row["concepts"].add(str(item.get("ts_code") or ""))
        row["hotValues"].append(hot)
    rows = []
    for code, item in grouped.items():
        values = item["hotValues"]
        rows.append(
            {
                "ts_code": code,
                "kplConceptCount": len(item["concepts"]),
                "kplHotMax": max(values) if values else 0.0,
                "kplHotMean": mean(values) if values else 0.0,
            }
        )
    add_rank(rows, "kplConceptCount", "kplConceptCountRank")
    add_rank(rows, "kplHotMax", "kplHotMaxRank")
    add_rank(rows, "kplHotMean", "kplHotMeanRank")
    return {str(item["ts_code"]): item for item in rows}


def add_rank(rows: list[dict[str, Any]], value_key: str, rank_key: str) -> None:
    valid = sorted([item for item in rows if finite(item.get(value_key))], key=lambda item: float(item[value_key]))
    if not valid:
        for item in rows:
            item[rank_key] = None
        return
    denom = max(1, len(valid) - 1)
    ranks = {id(item): index / denom for index, item in enumerate(valid)}
    for item in rows:
        item[rank_key] = ranks.get(id(item))


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
        "kplMatchedRate": sum(1 for item in samples if item.get("kplHotMaxRank") is not None) / len(samples) if samples else None,
        "kplConceptCountRank": stat([item.get("kplConceptCountRank") for item in samples]),
        "kplHotMaxRank": stat([item.get("kplHotMaxRank") for item in samples]),
        "kplHotMeanRank": stat([item.get("kplHotMeanRank") for item in samples]),
    }


def compare_groups(winners: list[dict[str, Any]], losers: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for key in ["kplConceptCountRank", "kplHotMaxRank", "kplHotMeanRank"]:
        win_avg = avg([item.get(key) for item in winners])
        loss_avg = avg([item.get(key) for item in losers])
        result[key] = win_avg - loss_avg if win_avg is not None and loss_avg is not None else None
    return result


def threshold_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ["kplHotMaxRank", "kplHotMeanRank", "kplConceptCountRank"]:
        kept = [item for item in samples if finite(item.get(key)) and float(item[key]) >= 0.5]
        filtered = [item for item in samples if finite(item.get(key)) and float(item[key]) < 0.5]
        result[key] = {
            "kept": summarize_samples(kept),
            "filtered": summarize_samples(filtered),
        }
    return result


def coverage_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "samples": len(samples),
        "kplMatched": sum(1 for item in samples if item.get("kplHotMaxRank") is not None),
        "kplMatchedRate": sum(1 for item in samples if item.get("kplHotMaxRank") is not None) / len(samples) if samples else None,
    }


def stat(values: list[Any]) -> dict[str, Any]:
    valid = [float(item) for item in values if finite(item)]
    if not valid:
        return {"avg": None, "min": None, "max": None}
    return {"avg": sum(valid) / len(valid), "min": min(valid), "max": max(valid)}


def avg(values: list[Any]) -> float | None:
    valid = [float(item) for item in values if finite(item)]
    return sum(valid) / len(valid) if valid else None


def window_labels(entry_date: str) -> list[str]:
    labels = []
    current = date.fromisoformat(entry_date)
    for label, (start, end) in WEAK_WINDOWS.items():
        if date.fromisoformat(start) <= current <= date.fromisoformat(end):
            labels.append(label)
    return labels


def trade_key(trade: dict[str, Any]) -> tuple[str, str, str]:
    return (str(trade["ts_code"]), str(trade["entryDate"]), str(trade["exitDate"]))


def fmt_pct(value: Any) -> str:
    return format_optional_percent(value)


def fmt_delta(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):+.3f}"


def render_review(output: dict[str, Any]) -> str:
    lines = [
        f"# {output['runId']} 前一交易日题材成分诊断",
        "",
        f"- 来源 run：`{output['sourceRun']}`",
        f"- 样本：完成交易 `{output['sourceCompletedTrades']}` 笔，唯一前一交易日 `{output['uniquePriorDates']}` 个。",
        "- 口径：使用入场日前一交易日 Tushare `kpl_concept_cons(trade_date=...)`，不使用入场日收盘后数据。",
        "",
        "## 覆盖率",
        "",
        f"- KPL 匹配：`{output['coverage']['kplMatched']}/{output['coverage']['samples']}`，覆盖率 {fmt_pct(output['coverage']['kplMatchedRate'])}。",
        f"- 拉取错误：`{len(output['fetchErrors'])}`。",
        "",
        "## 赢家 vs 输家",
        "",
        "| 样本 | 数量 | 胜率 | 平均收益 | 概念数排名差 | 最高热度排名差 | 平均热度排名差 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, groups in [("全样本", output["overall"]), *[(key, value) for key, value in output["weakWindows"].items()]]:
        all_group = groups["all"]
        diff = groups["winnerMinusLoser"]
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    f"`{all_group['count']}`",
                    fmt_pct(all_group["winRate"]),
                    fmt_pct(all_group["avgReturnPct"]),
                    fmt_delta(diff.get("kplConceptCountRank")),
                    fmt_delta(diff.get("kplHotMaxRank")),
                    fmt_delta(diff.get("kplHotMeanRank")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 简单阈值",
            "",
        ]
    )
    for key, title in [
        ("kplHotMaxRank", "最高题材热度排名"),
        ("kplHotMeanRank", "平均题材热度排名"),
        ("kplConceptCountRank", "题材覆盖数量排名"),
    ]:
        item = output["thresholds"][key]
        kept = item["kept"]
        filtered = item["filtered"]
        lines.append(
            f"- `{title} >= 0.50`：保留 `{kept['count']}` 笔，平均收益 {fmt_pct(kept['avgReturnPct'])}、胜率 {fmt_pct(kept['winRate'])}；"
            f"被过滤 `{filtered['count']}` 笔，平均收益 {fmt_pct(filtered['avgReturnPct'])}、胜率 {fmt_pct(filtered['winRate'])}。"
        )
    if output["fetchErrors"]:
        lines.extend(["", "## 拉取错误", ""])
        for item in output["fetchErrors"][:20]:
            lines.append(f"- `{item['tradeDate']}` {item['error']}: {item['message']}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
