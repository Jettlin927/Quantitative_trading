from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path
from statistics import mean, median
from typing import Any

from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import json_safe
from backend.app.database import SessionLocal
from backend.app.models import StockFinancialIndicator
from scripts.research.run_research_round import RUNS_ROOT, now_text, write_json, write_text


WINDOWS = {
    "Y1": ("2023-05-30", "2024-05-30"),
    "Y2": ("2024-05-30", "2025-05-30"),
    "Y3": ("2025-05-30", "2026-05-30"),
    "R18-1": ("2023-05-30", "2024-11-30"),
    "R18-2": ("2023-11-30", "2025-05-30"),
    "R18-3": ("2024-05-30", "2025-11-30"),
    "R18-4": ("2024-11-30", "2026-05-30"),
}

FINANCIAL_FIELDS = [
    "roe",
    "roe_waa",
    "roa",
    "grossprofit_margin",
    "netprofit_margin",
    "debt_to_assets",
    "current_ratio",
    "quick_ratio",
    "assets_turn",
    "tr_yoy",
    "netprofit_yoy",
    "q_sales_yoy",
    "q_profit_yoy",
    "basic_eps_yoy",
    "bps",
    "eps",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose point-in-time financial quality edges for a portfolio run.")
    parser.add_argument("--run-id", required=True, help="Output diagnostic run id under docs/research/runs.")
    parser.add_argument("--source-run", required=True, help="Portfolio run with completedTrades.")
    args = parser.parse_args()

    started_at = now_text()
    trades = load_completed_trades(args.source_run)
    with SessionLocal() as db:
        enriched = [attach_financial_snapshot(db, trade) for trade in trades]

    output = {
        "runId": args.run_id,
        "sourceRun": args.source_run,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "coverage": coverage_summary(enriched),
        "windows": {
            label: summarize_window(enriched, bounds)
            for label, bounds in [("ALL", None), *WINDOWS.items()]
        },
        "thresholdScreens": screen_thresholds(enriched),
    }

    run_dir = RUNS_ROOT / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "results.json", json_safe(output))
    write_text(run_dir / "review.md", render_review(output))
    print(json.dumps({"runId": args.run_id, "coverage": output["coverage"], "runDir": str(run_dir)}, ensure_ascii=False, indent=2))


def load_completed_trades(source_run: str) -> list[dict[str, Any]]:
    path = RUNS_ROOT / source_run / "results.json"
    if not path.exists():
        raise SystemExit(f"Missing run results: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("result", {}).get("completedTrades", []))


def attach_financial_snapshot(db: Any, trade: dict[str, Any]) -> dict[str, Any]:
    entry_date = date.fromisoformat(trade["entryDate"])
    row = db.scalars(
        select(StockFinancialIndicator)
        .where(
            StockFinancialIndicator.ts_code == trade["ts_code"],
            StockFinancialIndicator.ann_date <= entry_date,
        )
        .order_by(StockFinancialIndicator.ann_date.desc(), StockFinancialIndicator.end_date.desc())
        .limit(1)
    ).first()
    snapshot = financial_to_dict(row)
    result = dict(trade)
    result["financialSnapshot"] = snapshot
    result["financialAvailable"] = row is not None
    return result


def financial_to_dict(row: StockFinancialIndicator | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = {
        "ann_date": row.ann_date.isoformat(),
        "end_date": row.end_date.isoformat(),
    }
    for field in FINANCIAL_FIELDS:
        value = getattr(row, field)
        result[field] = float(value) if value is not None else None
    return result


def coverage_summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    symbols = sorted({trade["ts_code"] for trade in trades})
    covered_symbols = sorted({trade["ts_code"] for trade in trades if trade.get("financialAvailable")})
    covered_trades = [trade for trade in trades if trade.get("financialAvailable")]
    by_window = {}
    for label, bounds in [("ALL", None), *WINDOWS.items()]:
        subset = select_window(trades, bounds)
        covered = [trade for trade in subset if trade.get("financialAvailable")]
        by_window[label] = {
            "trades": len(subset),
            "coveredTrades": len(covered),
            "coveragePct": safe_ratio(len(covered), len(subset)),
        }
    return {
        "completedTrades": len(trades),
        "uniqueSymbols": len(symbols),
        "coveredTrades": len(covered_trades),
        "coveredTradePct": safe_ratio(len(covered_trades), len(trades)),
        "coveredSymbols": len(covered_symbols),
        "coveredSymbolPct": safe_ratio(len(covered_symbols), len(symbols)),
        "missingSymbols": [symbol for symbol in symbols if symbol not in set(covered_symbols)],
        "byWindow": by_window,
    }


def summarize_window(trades: list[dict[str, Any]], bounds: tuple[str, str] | None) -> dict[str, Any]:
    subset = [trade for trade in select_window(trades, bounds) if trade.get("financialAvailable")]
    winners = [trade for trade in subset if float(trade.get("netPnl") or 0) > 0]
    losers = [trade for trade in subset if float(trade.get("netPnl") or 0) <= 0]
    fields = {}
    for field in FINANCIAL_FIELDS:
        win_values = field_values(winners, field)
        loss_values = field_values(losers, field)
        fields[field] = {
            "winnerCount": len(win_values),
            "loserCount": len(loss_values),
            "winnerMean": safe_mean(win_values),
            "loserMean": safe_mean(loss_values),
            "meanDiff": safe_diff(safe_mean(win_values), safe_mean(loss_values)),
            "winnerMedian": safe_median(win_values),
            "loserMedian": safe_median(loss_values),
        }
    return {
        "trades": len(subset),
        "netPnl": sum(float(trade.get("netPnl") or 0) for trade in subset),
        "winners": len(winners),
        "losers": len(losers),
        "fields": fields,
    }


def screen_thresholds(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    covered = [trade for trade in trades if trade.get("financialAvailable")]
    screens = []
    for field in FINANCIAL_FIELDS:
        values = sorted(set(field_values(covered, field)))
        if len(values) < 10:
            continue
        for quantile in (0.2, 0.3, 0.4, 0.6, 0.7, 0.8):
            threshold = values[min(len(values) - 1, max(0, int((len(values) - 1) * quantile)))]
            for operator in ("<=", ">="):
                hits = [trade for trade in covered if compare(financial_value(trade, field), operator, threshold)]
                if not 5 <= len(hits) <= 60:
                    continue
                y1 = select_window(hits, WINDOWS["Y1"])
                r18_1 = select_window(hits, WINDOWS["R18-1"])
                r18_4 = select_window(hits, WINDOWS["R18-4"])
                if len(y1) < 2 or len(r18_1) < 3:
                    continue
                screens.append(
                    {
                        "field": field,
                        "operator": operator,
                        "threshold": threshold,
                        "all": summarize_hits(hits),
                        "Y1": summarize_hits(y1),
                        "R18-1": summarize_hits(r18_1),
                        "R18-4": summarize_hits(r18_4),
                    }
                )
    return sorted(
        screens,
        key=lambda item: (
            float(item["Y1"]["netPnl"]) + float(item["R18-1"]["netPnl"]),
            float(item["all"]["netPnl"]),
        ),
    )[:30]


def summarize_hits(trades: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [float(trade.get("returnPct") or 0) for trade in trades]
    net_pnl = sum(float(trade.get("netPnl") or 0) for trade in trades)
    return {
        "count": len(trades),
        "netPnl": net_pnl,
        "avgReturnPct": safe_mean(returns),
        "winRate": safe_ratio(sum(1 for trade in trades if float(trade.get("netPnl") or 0) > 0), len(trades)),
    }


def select_window(trades: list[dict[str, Any]], bounds: tuple[str, str] | None) -> list[dict[str, Any]]:
    if bounds is None:
        return list(trades)
    start, end = bounds
    return [trade for trade in trades if start <= trade["entryDate"] < end]


def field_values(trades: list[dict[str, Any]], field: str) -> list[float]:
    values = [financial_value(trade, field) for trade in trades]
    return [value for value in values if value is not None and math.isfinite(value)]


def financial_value(trade: dict[str, Any], field: str) -> float | None:
    snapshot = trade.get("financialSnapshot")
    if not snapshot:
        return None
    value = snapshot.get(field)
    return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None


def compare(value: float | None, operator: str, threshold: float) -> bool:
    if value is None:
        return False
    if operator == "<=":
        return value <= threshold
    return value >= threshold


def safe_mean(values: list[float]) -> float | None:
    return mean(values) if values else None


def safe_median(values: list[float]) -> float | None:
    return median(values) if values else None


def safe_diff(left: float | None, right: float | None) -> float | None:
    return left - right if left is not None and right is not None else None


def safe_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def render_review(output: dict[str, Any]) -> str:
    coverage = output["coverage"]
    lines = [
        f"# {output['runId']} 财务质量 point-in-time 诊断",
        "",
        f"- 来源 run：`{output['sourceRun']}`",
        f"- 开始时间：{output['startedAt']}",
        f"- 结束时间：{output['finishedAt']}",
        f"- 覆盖交易：`{coverage['coveredTrades']}/{coverage['completedTrades']}`",
        f"- 覆盖标的：`{coverage['coveredSymbols']}/{coverage['uniqueSymbols']}`",
        "",
        "## 窗口覆盖",
        "",
        "| 窗口 | 交易 | 已覆盖 | 覆盖率 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for label, item in coverage["byWindow"].items():
        lines.append(f"| `{label}` | `{item['trades']}` | `{item['coveredTrades']}` | {format_pct(item['coveragePct'])} |")

    lines.extend(["", "## 财务字段区分度", ""])
    for label in ("ALL", "Y1", "R18-1", "R18-4"):
        window = output["windows"][label]
        ranked = sorted(
            [
                (abs(field_data["meanDiff"]), field, field_data)
                for field, field_data in window["fields"].items()
                if field_data["meanDiff"] is not None
            ],
            reverse=True,
        )[:8]
        lines.extend([f"### {label}", "", "| 字段 | 赢家均值 | 输家均值 | 差值 | 样本 |", "| --- | ---: | ---: | ---: | ---: |"])
        for _, field, item in ranked:
            lines.append(
                f"| `{field}` | {format_number(item['winnerMean'])} | {format_number(item['loserMean'])} | {format_number(item['meanDiff'])} | `{item['winnerCount']}/{item['loserCount']}` |"
            )
        lines.append("")

    lines.extend(
        [
            "## 阈值筛查",
            "",
            "| 条件 | ALL | Y1 | R18-1 | R18-4 |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in output["thresholdScreens"][:12]:
        condition = f"`{item['field']}` {item['operator']} {format_number(item['threshold'])}"
        lines.append(
            f"| {condition} | {format_hit(item['all'])} | {format_hit(item['Y1'])} | {format_hit(item['R18-1'])} | {format_hit(item['R18-4'])} |"
        )

    lines.extend(
        [
            "",
            "## 结论提示",
            "",
            "- 本诊断按 `ann_date <= entryDate` 取最近财务指标，避免使用入场后才公告的数据。",
            "- 阈值筛查只用于发现候选方向，不能直接作为策略通过证据；若出现稳定信号，仍需默认关闭参数、完整三年回测和滚动验证。",
        ]
    )
    return "\n".join(lines) + "\n"


def format_number(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"`{value:.2f}`"


def format_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"`{value:.2%}`"


def format_hit(item: dict[str, Any]) -> str:
    return f"`{item['count']} / {item['netPnl']:.2f}`"


if __name__ == "__main__":
    main()
