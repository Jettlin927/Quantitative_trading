from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import DEFAULT_CONFIG, json_safe
from backend.app.database import SessionLocal
from backend.app.main import execute_market_backtest
from backend.app.schemas import MarketBacktestRequest


CN_TZ = timezone(timedelta(hours=8))
RUNS_ROOT = REPO_ROOT / "docs" / "research" / "runs"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run tail-active-next-day parameter grid on local daily/daily_basic data.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--max-stocks", type=int, default=0, help="0 means all matched stocks.")
    parser.add_argument("--min-bars", type=int, default=120)
    parser.add_argument("--min-avg-amount", type=float, default=None)
    parser.add_argument("--min-avg-circ-mv", type=float, default=None)
    parser.add_argument("--min-avg-turnover-rate-f", type=float, default=None)
    parser.add_argument("--exclude-st", action="store_true")
    parser.add_argument("--exclude-bj", action="store_true")
    parser.add_argument("--grid", choices=["base", "risk-refine", "mainline-refine", "best-risk", "all"], default="base")
    args = parser.parse_args()

    run_dir = RUNS_ROOT / args.run_id
    if run_dir.exists():
        raise SystemExit(f"run already exists: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=False)

    results: list[dict[str, Any]] = []
    db = SessionLocal()
    try:
        for variant in build_grid(args.grid):
            payload = MarketBacktestRequest(
                start_date=args.start_date,
                end_date=args.end_date,
                config=build_config(variant),
                min_bars=args.min_bars,
                max_stocks=args.max_stocks,
                exclude_st=args.exclude_st,
                exclude_bj=args.exclude_bj,
                min_avg_amount=args.min_avg_amount,
                min_avg_circ_mv=args.min_avg_circ_mv,
                min_avg_turnover_rate_f=args.min_avg_turnover_rate_f,
            )
            market_result = execute_market_backtest(db, payload)
            results.append(summarize_variant(variant, market_result))
    except SQLAlchemyError as error:
        write_text(run_dir / "review.md", f"# 尾盘活跃网格\n\n数据库不可用，未能运行回测。\n\n```text\n{error}\n```\n")
        raise
    finally:
        db.close()

    results.sort(key=lambda item: variant_rank_key(item), reverse=True)
    output = {
        "runId": args.run_id,
        "createdAt": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "strategy": {
            "name": "tail-active-next-day-grid",
            "label": "尾盘活跃次日纪律参数网格",
            "hypothesis": "尾盘 3%-5% 强势但未涨停、近 15 日有涨停记忆、量比和换手率同时达标的股票，若叠加历史可复现的行业主线状态，次日若未继续涨停则退出，可降低隔夜不确定性。",
        },
        "scope": {
            "startDate": args.start_date.isoformat(),
            "endDate": args.end_date.isoformat(),
            "maxStocks": args.max_stocks,
            "minBars": args.min_bars,
            "excludeSt": args.exclude_st,
            "excludeBj": args.exclude_bj,
            "minAvgAmount": args.min_avg_amount,
            "minAvgCircMv": args.min_avg_circ_mv,
            "minAvgTurnoverRateF": args.min_avg_turnover_rate_f,
        },
        "results": results,
        "best": results[0] if results else None,
    }
    write_json(run_dir / "results.json", output)
    write_json(run_dir / "strategies.json", {"strategy": output["strategy"], "variants": [item["variant"] for item in results]})
    write_text(run_dir / "hypothesis.md", build_hypothesis(output))
    write_text(run_dir / "review.md", build_review(output))
    print(json.dumps({"runId": args.run_id, "variants": len(results), "best": output["best"]}, ensure_ascii=False, indent=2))


def build_grid(mode: str = "base") -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = []
    if mode in {"base", "all"}:
        change_ranges = [(0.03, 0.05), (0.025, 0.05), (0.03, 0.06), (0.035, 0.055)]
        volume_ratios = [1.5, 2.0]
        turnover_rates = [5.0, 7.0]
        lookbacks = [10, 15]
        for min_change, max_change in change_ranges:
            for volume_ratio in volume_ratios:
                for turnover_rate in turnover_rates:
                    for lookback in lookbacks:
                        variants.append(base_variant(min_change, max_change, volume_ratio, turnover_rate, lookback))
    if mode in {"risk-refine", "all"}:
        seed = base_variant(0.025, 0.05, 2.0, 7.0, 15)
        risk_filters = [
            {"maxEntryRangePct": 0.06},
            {"maxEntryRangePct": 0.075},
            {"maxGapPct": 0.025},
            {"maxGapPct": 0.04},
            {"maxUpperShadowPct": 0.02},
            {"maxUpperShadowPct": 0.03},
            {"maxEntryRangePct": 0.075, "maxGapPct": 0.04},
            {"maxEntryRangePct": 0.075, "maxUpperShadowPct": 0.03},
            {"maxEntryRangePct": 0.075, "maxGapPct": 0.04, "maxUpperShadowPct": 0.03},
        ]
        variants.extend([{**seed, "entryRiskFilter": {"enabled": True, **rules}} for rules in risk_filters])
    if mode in {"best-risk", "all"}:
        variants.append(best_risk_variant())
    if mode in {"mainline-refine", "all"}:
        seed = best_risk_variant()
        mainline_filters = [
            {"minSamples": 20, "maxRank": 10, "minAvgReturnPct": 0.005, "minUpPct": 0.55},
            {"minSamples": 20, "maxRank": 8, "minAvgReturnPct": 0.005, "minUpPct": 0.55},
            {"minSamples": 20, "maxRank": 5, "minAvgReturnPct": 0.005, "minUpPct": 0.55},
            {"minSamples": 20, "maxRank": 10, "minAvgReturnPct": 0.01, "minUpPct": 0.55},
            {"minSamples": 20, "maxRank": 10, "minAvgReturnPct": 0.005, "minUpPct": 0.6},
            {"minSamples": 20, "maxRank": 15, "minAvgReturnPct": 0.003, "minUpPct": 0.55},
        ]
        variants.extend([{**seed, "tailMainlineFilter": {"enabled": True, **rules}} for rules in mainline_filters])
    return variants


def best_risk_variant() -> dict[str, Any]:
    return {
        **base_variant(0.025, 0.05, 2.0, 7.0, 15),
        "entryRiskFilter": {"enabled": True, "maxEntryRangePct": 0.06},
    }


def base_variant(min_change: float, max_change: float, volume_ratio: float, turnover_rate: float, lookback: int) -> dict[str, Any]:
    return {
        "tailEntryMinPctChg": min_change,
        "tailEntryMaxPctChg": max_change,
        "tailMinVolumeRatio": volume_ratio,
        "tailMinTurnoverRatePct": turnover_rate,
        "tailPriorLimitUpLookback": lookback,
    }


def build_config(variant: dict[str, Any]) -> dict[str, Any]:
    cfg = deepcopy(DEFAULT_CONFIG)
    risk_filter = variant.get("entryRiskFilter")
    mainline_filter = variant.get("tailMainlineFilter")
    clean_variant = {key: value for key, value in variant.items() if key not in {"entryRiskFilter", "tailMainlineFilter"}}
    cfg.update(
        {
            "entryMode": "tail-active-next-day",
            "useTrendFilter": False,
            "useMacdFilter": False,
            "useRsiFilter": False,
            "blockWeakMarket": True,
            "weeklyTradeLimit": 2,
            "positionCapPct": 0.2,
            "riskPct": 0.01,
            "stopLossPct": 0.05,
            "takeProfit1Pct": 0.03,
            "takeProfit2Pct": 0.05,
            **clean_variant,
        }
    )
    if risk_filter:
        cfg["entryRiskFilter"] = risk_filter
    if mainline_filter:
        cfg["tailMainlineFilter"] = mainline_filter
    return cfg


def summarize_variant(variant: dict[str, Any], market_result: dict[str, Any]) -> dict[str, Any]:
    rows = market_result.get("results") or []
    traded = [row for row in rows if int(row.get("completedTrades") or 0) > 0]
    returns = [float(row.get("totalReturn") or 0) for row in traded]
    drawdowns = [float(row.get("maxDrawdown") or 0) for row in traded]
    profit_loss_ratios = [float(row["profitLossRatio"]) for row in traded if row.get("profitLossRatio") is not None]
    return {
        "variant": variant,
        "tested": market_result.get("summary", {}).get("tested", 0),
        "tradedStocks": len(traded),
        "totalTrades": sum(int(row.get("completedTrades") or 0) for row in traded),
        "positiveRate": len([value for value in returns if value > 0]) / len(returns) if returns else 0,
        "avgReturn": mean(returns) if returns else 0,
        "medianReturn": median(returns) if returns else 0,
        "avgMaxDrawdown": mean(drawdowns) if drawdowns else 0,
        "medianProfitLossRatio": median(profit_loss_ratios) if profit_loss_ratios else None,
        "top10": rows[:10],
        "bottom10": rows[-10:],
    }


def variant_rank_key(item: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(item.get("medianReturn") or 0),
        float(item.get("avgReturn") or 0),
        float(item.get("positiveRate") or 0),
        -abs(float(item.get("avgMaxDrawdown") or 0)),
    )


def build_hypothesis(output: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# 尾盘活跃次日纪律参数网格",
            "",
            f"- Run: `{output['runId']}`",
            f"- 时间: `{output['createdAt']}`",
            "- 假设：尾盘未涨停但已显著走强的活跃票，如果有近端涨停记忆、量比和换手支撑，次日具备继续冲高或涨停概率；若次日未涨停，纪律退出避免继续暴露。",
            "- 主线代理：用本地历史日线按行业计算同日平均涨幅、上涨比例和行业排名，只使用当日尾盘可近似观察的信息，不使用未来榜单。",
            "- 失败条件：交易数过少、收益依赖少数股票、尾部亏损深、不同阈值结果不稳定，或缺少 daily_basic 导致样本覆盖不足。",
            "- 语义说明：这是日线近似回测，不是严格 14:30 分钟级历史回放。",
            "",
        ]
    )


def build_review(output: dict[str, Any]) -> str:
    best = output.get("best") or {}
    lines = [
        "# 尾盘活跃网格复盘",
        "",
        f"- Run: `{output['runId']}`",
        f"- 覆盖: `{output['scope']['startDate']}` 至 `{output['scope']['endDate']}`",
        f"- 参数组合: `{len(output.get('results') or [])}`",
    ]
    if best:
        total_trades = int(best.get("totalTrades") or 0)
        median_return = float(best.get("medianReturn") or 0)
        avg_return = float(best.get("avgReturn") or 0)
        avg_drawdown = float(best.get("avgMaxDrawdown") or 0)
        signal_passed = total_trades >= 100 and median_return > 0 and avg_return > 0 and avg_drawdown >= -0.1
        conclusion = (
            "第一阶段信号可行性暂时通过，可以进入共享资金组合回测与滚动窗口验证。"
            if signal_passed
            else "第一阶段信号可行性未通过：当前最优组合仍未取得正的中位收益和平均收益，暂不应推进为组合级候选。"
        )
        lines.extend(
            [
                "",
                "## 当前最优",
                "",
                f"- 参数: `{json.dumps(best.get('variant'), ensure_ascii=False)}`",
                f"- 测试股票: `{best.get('tested')}`，有交易股票: `{best.get('tradedStocks')}`，完成交易: `{total_trades}`",
                f"- 中位收益: `{format_pct(best.get('medianReturn'))}`，平均收益: `{format_pct(best.get('avgReturn'))}`，正收益率: `{format_pct(best.get('positiveRate'))}`",
                f"- 平均最大回撤: `{format_pct(best.get('avgMaxDrawdown'))}`，中位盈亏比: `{format_ratio(best.get('medianProfitLossRatio'))}`",
                "",
                "## 结论",
                "",
                conclusion,
            ]
        )
    return "\n".join(lines) + "\n"


def format_pct(value: Any) -> str:
    return "--" if value is None else f"{float(value) * 100:.2f}%"


def format_ratio(value: Any) -> str:
    return "--" if value is None else f"{float(value):.2f}:1"


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(json_safe(data), ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
