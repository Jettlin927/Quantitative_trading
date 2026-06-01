from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean, median
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
    parser = argparse.ArgumentParser(description="Diagnose prior-day Tushare moneyflow edge for completed entries.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--source-run", default="002-repair-indicator-ablate-ma-001", help="Portfolio run with completedTrades.")
    parser.add_argument("--fetch-hot", action="store_true", help="Also query ths_hot. This endpoint can be tightly rate-limited.")
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
    moneyflow_by_date = {}
    hot_by_date = {}
    fetch_errors = []
    for item in unique_dates:
        trade_date = item.strftime("%Y%m%d")
        try:
            moneyflow_by_date[item.isoformat()] = pro.moneyflow(trade_date=trade_date)
        except Exception as exc:
            fetch_errors.append({"endpoint": "moneyflow", "tradeDate": item.isoformat(), "error": type(exc).__name__, "message": str(exc)[:200]})
        if args.fetch_hot:
            try:
                hot_by_date[item.isoformat()] = pro.ths_hot(trade_date=trade_date)
            except Exception as exc:
                fetch_errors.append({"endpoint": "ths_hot", "tradeDate": item.isoformat(), "error": type(exc).__name__, "message": str(exc)[:200]})
        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    ranked_moneyflow = {key: rank_moneyflow(df) for key, df in moneyflow_by_date.items()}
    hot_maps = {key: hot_rank_map(df) for key, df in hot_by_date.items()}
    samples = []
    for trade in trades:
        prior_date = prior_dates.get(trade_key(trade))
        metrics = ranked_moneyflow.get(prior_date.isoformat() if prior_date else "", {}).get(trade["ts_code"], {})
        hot = hot_maps.get(prior_date.isoformat() if prior_date else "", {}).get(trade["ts_code"], {})
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
                **hot,
            }
        )

    output = {
        "runId": args.run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "sourceRun": args.source_run,
        "sourceCompletedTrades": len(trades),
        "uniquePriorDates": len(unique_dates),
        "fetchHot": bool(args.fetch_hot),
        "fetchErrors": fetch_errors,
        "coverage": coverage_summary(samples),
        "overall": summarize_groups(samples),
        "weakWindows": {label: summarize_groups([item for item in samples if label in item["windowLabels"]]) for label in WEAK_WINDOWS},
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


def rank_moneyflow(df: Any) -> dict[str, dict[str, Any]]:
    if df is None or df.empty:
        return {}
    rows = []
    for item in df.to_dict("records"):
        main_net = value(item.get("buy_lg_amount")) + value(item.get("buy_elg_amount")) - value(item.get("sell_lg_amount")) - value(item.get("sell_elg_amount"))
        retail_net = value(item.get("buy_sm_amount")) + value(item.get("buy_md_amount")) - value(item.get("sell_sm_amount")) - value(item.get("sell_md_amount"))
        net_mf = value(item.get("net_mf_amount"))
        rows.append(
            {
                "ts_code": item.get("ts_code"),
                "moneyflowNetMf": net_mf,
                "moneyflowMainNet": main_net,
                "moneyflowRetailNet": retail_net,
            }
        )
    add_rank(rows, "moneyflowNetMf", "moneyflowNetMfRank")
    add_rank(rows, "moneyflowMainNet", "moneyflowMainNetRank")
    add_rank(rows, "moneyflowRetailNet", "moneyflowRetailNetRank")
    return {str(item["ts_code"]): item for item in rows if item.get("ts_code")}


def hot_rank_map(df: Any) -> dict[str, dict[str, Any]]:
    if df is None or df.empty or "ts_code" not in df.columns:
        return {}
    result = {}
    for item in df.to_dict("records"):
        code = item.get("ts_code")
        if not code:
            continue
        rank = value(item.get("rank"))
        hot = value(item.get("hot"))
        result[str(code)] = {
            "thsHotRank": rank if finite(rank) else None,
            "thsHotScore": hot if finite(hot) else None,
            "thsHotPresent": True,
        }
    return result


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
        "moneyflowNetMfRank": stat([item.get("moneyflowNetMfRank") for item in samples]),
        "moneyflowMainNetRank": stat([item.get("moneyflowMainNetRank") for item in samples]),
        "moneyflowRetailNetRank": stat([item.get("moneyflowRetailNetRank") for item in samples]),
        "thsHotPresentRate": sum(1 for item in samples if item.get("thsHotPresent")) / len(samples) if samples else None,
    }


def compare_groups(winners: list[dict[str, Any]], losers: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for key in ["moneyflowNetMfRank", "moneyflowMainNetRank", "moneyflowRetailNetRank"]:
        win_avg = avg([item.get(key) for item in winners])
        loss_avg = avg([item.get(key) for item in losers])
        result[key] = win_avg - loss_avg if win_avg is not None and loss_avg is not None else None
    return result


def coverage_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "samples": len(samples),
        "moneyflowMatched": sum(1 for item in samples if item.get("moneyflowNetMfRank") is not None),
        "thsHotMatched": sum(1 for item in samples if item.get("thsHotPresent")),
    }


def window_labels(entry_date: str) -> list[str]:
    labels = []
    current = date.fromisoformat(entry_date)
    for label, (start, end) in WEAK_WINDOWS.items():
        if date.fromisoformat(start) <= current <= date.fromisoformat(end):
            labels.append(label)
    return labels


def render_review(output: dict[str, Any]) -> str:
    lines = [
        f"# {output['runId']} 前一交易日资金流诊断",
        "",
        f"- 来源 run：`{output['sourceRun']}`",
        f"- 样本：完成交易 `{output['sourceCompletedTrades']}` 笔，唯一前一交易日 `{output['uniquePriorDates']}` 个。",
        "- 口径：使用入场日前一交易日 Tushare `moneyflow` 和 `ths_hot`，避免使用入场日收盘后数据。",
        f"- 热榜接口：{'已请求' if output.get('fetchHot') else '未请求'}。",
        "",
        "## 覆盖率",
        "",
        f"- moneyflow 匹配：{output['coverage']['moneyflowMatched']}/{output['coverage']['samples']}",
        f"- ths_hot 匹配：{output['coverage']['thsHotMatched']}/{output['coverage']['samples']}",
        f"- 接口错误：{len(output.get('fetchErrors') or [])}",
        "",
        "## 赢家 vs 输家",
        "",
        "| 样本 | 数量 | 胜率 | 平均收益 | NetMF排名差 | 主力净流排名差 | 散户净流排名差 | 热榜覆盖 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines.append(render_group_row("全样本", output["overall"]))
    for label, summary in output["weakWindows"].items():
        lines.append(render_group_row(label, summary))
    lines.extend(["", "## 初步结论", ""])
    lines.append("- 若资金流排名差为正，说明赢家在入场前一日已有资金确认，可作为后续排序或过滤候选。")
    lines.append("- 若弱窗口排名差接近零或为负，说明单日资金流不能解决弱窗口，需考虑多日资金流、主题持续性或财务质量。")
    lines.append("- `ths_hot` 覆盖率若很低，只能作为题材标签候选，不能直接进入全市场排序。")
    return "\n".join(lines) + "\n"


def render_group_row(label: str, summary: dict[str, Any]) -> str:
    all_summary = summary["all"]
    diff = summary["winnerMinusLoser"]
    return (
        f"| `{label}` | {all_summary['count']} | {format_optional_percent(all_summary.get('winRate'))} | "
        f"{format_optional_percent(all_summary.get('avgReturnPct'))} | {format_rank_diff(diff.get('moneyflowNetMfRank'))} | "
        f"{format_rank_diff(diff.get('moneyflowMainNetRank'))} | {format_rank_diff(diff.get('moneyflowRetailNetRank'))} | "
        f"{format_optional_percent(all_summary.get('thsHotPresentRate'))} |"
    )


def value(item: Any) -> float:
    try:
        if item is None:
            return float("nan")
        return float(item)
    except (TypeError, ValueError):
        return float("nan")


def avg(values: list[Any]) -> float | None:
    clean = [float(item) for item in values if finite(item)]
    return mean(clean) if clean else None


def stat(values: list[Any]) -> dict[str, Any]:
    clean = [float(item) for item in values if finite(item)]
    if not clean:
        return {"count": 0, "mean": None, "median": None}
    return {"count": len(clean), "mean": mean(clean), "median": median(clean)}


def format_rank_diff(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):+.3f}"


def trade_key(trade: dict[str, Any]) -> tuple[str, str, str]:
    return (trade["ts_code"], trade["entryDate"], trade["exitDate"])


if __name__ == "__main__":
    main()
