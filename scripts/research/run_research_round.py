from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
RESEARCH_ROOT = REPO_ROOT / "docs" / "research"
DEFAULT_CONTEXT_PATH = RESEARCH_ROOT / "context.default.json"
NEXT_BRIEF_PATH = RESEARCH_ROOT / "next-strategy-brief.md"
RUNS_ROOT = RESEARCH_ROOT / "runs"
CN_TZ = timezone(timedelta(hours=8))


BASE_STRATEGY_CONFIG: dict[str, Any] = {
    "marketState": "normal",
    "initialCash": 100000,
    "weeklyTradeLimit": 2,
    "positionCapPct": 0.2,
    "riskPct": 0.01,
    "stopLossPct": 0.05,
    "takeProfit1Pct": 0.03,
    "takeProfit2Pct": 0.05,
    "commissionPct": 0.00025,
    "stampDutyPct": 0.0005,
    "lotSize": 100,
    "bollPeriod": 20,
    "bollDev": 2,
    "bollTolerancePct": 0.015,
    "bollBandwidthMaxPct": 0.08,
    "midlineTolerancePct": 0.025,
    "trendFastPeriod": 5,
    "trendSlowPeriod": 10,
    "trendLongPeriod": 20,
    "volumeMaPeriod": 20,
    "volumeBreakoutMultiplier": 1.08,
    "pullbackTolerancePct": 0.025,
    "maxMa20ExtensionPct": 0.06,
    "minMa20ExtensionPct": 0.0,
    "macdFastPeriod": 12,
    "macdSlowPeriod": 26,
    "macdSignalPeriod": 9,
    "macdRequireZeroAxis": False,
    "rsiPeriod": 14,
    "rsiLowerBound": 35,
    "rsiUpperBound": 78,
    "kdjPeriod": 9,
    "atrPeriod": 14,
    "useAtrStop": False,
    "atrStopMultiplier": 1.8,
    "useTrendFilter": True,
    "useMacdFilter": False,
    "useRsiFilter": False,
    "blockWeakMarket": True,
    "forceStopOverridesLimit": True,
    "blockSameDayReentry": True,
    "minEntryMa20ExtensionPct": None,
    "maxEntryMa20ExtensionPct": None,
    "requireMa20AboveMa60": False,
}


STRATEGIES: dict[str, dict[str, Any]] = {
    "boll-rebound": {
        "label": "BOLL下轨反弹",
        "hypothesis": "下轨试错反弹能在弱转强阶段获得较小回撤的反转收益。",
        "config": {"entryMode": "boll-rebound", "useTrendFilter": True, "useMacdFilter": False, "useRsiFilter": False},
    },
    "macd-cross": {
        "label": "MACD金叉",
        "hypothesis": "MACD 金叉叠加趋势过滤能捕捉动量切换，减少纯反转策略的噪声。",
        "config": {"entryMode": "macd-cross", "useTrendFilter": True, "useMacdFilter": False, "useRsiFilter": True, "rsiLowerBound": 35, "rsiUpperBound": 82},
    },
    "boll-squeeze": {
        "label": "BOLL收口突破",
        "hypothesis": "低波动收敛后的向上扩张能提高收益弹性，但需要观察回撤和交易稀疏性。",
        "config": {"entryMode": "boll-squeeze", "useTrendFilter": True, "useMacdFilter": True, "useRsiFilter": False, "bollBandwidthMaxPct": 0.08},
    },
    "boll-breakout": {
        "label": "BOLL上轨突破",
        "hypothesis": "上轨放量突破适合强势趋势延续，但可能牺牲回撤控制。",
        "config": {"entryMode": "boll-breakout", "useTrendFilter": True, "useMacdFilter": True, "useRsiFilter": False, "volumeBreakoutMultiplier": 1.15},
    },
    "ma-cross": {
        "label": "均线金叉",
        "hypothesis": "均线金叉是趋势切换基线，适合检验当前止损止盈纪律是否过早截断趋势。",
        "config": {"entryMode": "ma-cross", "useTrendFilter": False, "useMacdFilter": False, "useRsiFilter": True, "rsiLowerBound": 40, "rsiUpperBound": 80},
    },
    "rsi-reversal": {
        "label": "RSI超卖反转",
        "hypothesis": "RSI 超卖回升可以降低追高风险，但可能在弱势中反复接刀。",
        "config": {"entryMode": "rsi-reversal", "useTrendFilter": False, "useMacdFilter": False, "useRsiFilter": False, "rsiLowerBound": 32, "rsiUpperBound": 70},
    },
    "trend-follow": {
        "label": "MA趋势跟随",
        "hypothesis": "MA 多头排列放量跟随是顺势基线，用于和反转/突破类策略比较。",
        "config": {"entryMode": "trend-follow", "useTrendFilter": True, "useMacdFilter": True, "useRsiFilter": False},
    },
    "trend-follow-wide-profit": {
        "label": "MA趋势跟随-放宽止盈",
        "hypothesis": "默认 3%/5% 止盈可能截断趋势行情；在仍保持 5% 止损和 20% 仓位上限时，把止盈放宽到 6%/12%，检验收益是否能越过 20% 且回撤保持在 15% 以内。",
        "config": {
            "entryMode": "trend-follow",
            "useTrendFilter": True,
            "useMacdFilter": True,
            "useRsiFilter": False,
            "takeProfit1Pct": 0.06,
            "takeProfit2Pct": 0.12,
        },
    },
    "trend-follow-wide-profit-no-macd": {
        "label": "MA趋势跟随-放宽止盈-移除MACD过滤",
        "hypothesis": "上一轮达标可能来自 MA 趋势结构，也可能依赖 MACD 过滤；移除 MACD 过滤后如果合格样本显著增加或减少，可判断 MACD 在当前策略中的边际贡献。",
        "config": {
            "entryMode": "trend-follow",
            "useTrendFilter": True,
            "useMacdFilter": False,
            "useRsiFilter": False,
            "takeProfit1Pct": 0.06,
            "takeProfit2Pct": 0.12,
        },
    },
    "trend-follow-wider-profit-no-macd": {
        "label": "MA趋势跟随-更高止盈-移除MACD过滤",
        "hypothesis": "在 6%/12% 止盈口径下盈亏比和回撤已过线但收益不足；将止盈提高到 8%/16%，检验收益弹性是否能越过 30%，同时观察胜率和回撤是否恶化。",
        "config": {
            "entryMode": "trend-follow",
            "useTrendFilter": True,
            "useMacdFilter": False,
            "useRsiFilter": False,
            "takeProfit1Pct": 0.08,
            "takeProfit2Pct": 0.16,
        },
    },
    "trend-follow-maximum-profit-no-macd": {
        "label": "MA趋势跟随-高弹性止盈-移除MACD过滤",
        "hypothesis": "8%/16% 止盈已经出现 1 个达标样本但覆盖太窄；继续提高到 10%/20%，检验更高收益弹性是否能扩大达标样本数，同时确认最大回撤仍压在 10% 内。",
        "config": {
            "entryMode": "trend-follow",
            "useTrendFilter": True,
            "useMacdFilter": False,
            "useRsiFilter": False,
            "takeProfit1Pct": 0.10,
            "takeProfit2Pct": 0.20,
        },
    },
    "trend-follow-maximum-profit-macd": {
        "label": "MA趋势跟随-高弹性止盈-MACD确认",
        "hypothesis": "尾部亏损来自趋势跟随在弱动量标的上连续试错；恢复 MACD 动量确认，检验能否降低收益后 10 的亏损和回撤，同时保留 10%/20% 的收益弹性。",
        "config": {
            "entryMode": "trend-follow",
            "useTrendFilter": True,
            "useMacdFilter": True,
            "useRsiFilter": False,
            "takeProfit1Pct": 0.10,
            "takeProfit2Pct": 0.20,
        },
    },
    "trend-follow-maximum-profit-strong-volume": {
        "label": "MA趋势跟随-高弹性止盈-强放量",
        "hypothesis": "尾部标的可能只是均线形态满足但缺乏有效资金推动；把放量阈值从 1.08 提高到 1.25，检验更严格资金确认能否减少无效入场和尾部回撤。",
        "config": {
            "entryMode": "trend-follow",
            "useTrendFilter": True,
            "useMacdFilter": False,
            "useRsiFilter": False,
            "volumeBreakoutMultiplier": 1.25,
            "takeProfit1Pct": 0.10,
            "takeProfit2Pct": 0.20,
        },
    },
    "trend-follow-tight-stop-maximum-profit": {
        "label": "MA趋势跟随-高弹性止盈-紧止损",
        "hypothesis": "过滤和动量确认仍无法压住收益后 10，说明连续试错的单笔损失预算过宽；把硬止损从 5% 收紧到 3%，保留 10%/20% 止盈，检验尾部亏损和最大回撤是否收敛。",
        "config": {
            "entryMode": "trend-follow",
            "useTrendFilter": True,
            "useMacdFilter": False,
            "useRsiFilter": False,
            "stopLossPct": 0.03,
            "takeProfit1Pct": 0.10,
            "takeProfit2Pct": 0.20,
        },
    },
    "trend-follow-market-breadth-profit-12-24": {
        "label": "MA趋势跟随-市场宽度-止盈12/24",
        "hypothesis": "市场宽度和过热过滤已经把逐标的头部推到接近 30% 且尾部明显改善；在相同入场约束下把止盈提高到 12%/24%，检验收益弹性是否能过 30%，同时观察尾部是否重新失控。",
        "config": {
            "entryMode": "trend-follow",
            "useTrendFilter": True,
            "useMacdFilter": False,
            "useRsiFilter": False,
            "takeProfit1Pct": 0.12,
            "takeProfit2Pct": 0.24,
        },
    },
    "trend-pullback-confirm-market-breadth": {
        "label": "Risk-On趋势回踩确认",
        "hypothesis": "旧 MA 趋势跟随容易在过热位置追入；只在 Risk-On 日期内买入未明显偏离 MA20、且出现回踩或趋势刚启动确认的标的，检验能否压低收益后 10 的亏损和组合回撤。",
        "config": {
            "entryMode": "trend-pullback-confirm",
            "useTrendFilter": True,
            "useMacdFilter": False,
            "useRsiFilter": False,
            "pullbackTolerancePct": 0.025,
            "maxMa20ExtensionPct": 0.06,
            "minMa20ExtensionPct": 0.0,
            "takeProfit1Pct": 0.10,
            "takeProfit2Pct": 0.20,
        },
    },
    "trend-follow-market-breadth-unextended": {
        "label": "Risk-On趋势跟随-未过热",
        "hypothesis": "回踩确认压住了尾部但牺牲收益弹性；保留 MA 趋势跟随的强势入场，只禁止明显偏离 MA20 的过热买点，检验能否同时保留头部收益并改善收益后 10。",
        "config": {
            "entryMode": "trend-follow",
            "useTrendFilter": True,
            "useMacdFilter": False,
            "useRsiFilter": False,
            "minEntryMa20ExtensionPct": 0.0,
            "maxEntryMa20ExtensionPct": 0.06,
            "takeProfit1Pct": 0.10,
            "takeProfit2Pct": 0.20,
        },
    },
    "trend-follow-market-breadth-ma60": {
        "label": "Risk-On趋势跟随-MA60结构",
        "hypothesis": "收益后 10 多来自反复下行标的中的短线假多头；要求 MA20 位于 MA60 之上，只保留中期结构向上的趋势跟随信号，检验能否压住尾部且保留头部弹性。",
        "config": {
            "entryMode": "trend-follow",
            "useTrendFilter": True,
            "useMacdFilter": False,
            "useRsiFilter": False,
            "requireMa20AboveMa60": True,
            "takeProfit1Pct": 0.10,
            "takeProfit2Pct": 0.20,
        },
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one reproducible AI strategy research round.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--strategy", default="boll-rebound", choices=sorted(STRATEGIES), help="Strategy preset to test.")
    parser.add_argument("--api-base", default="http://localhost:18000", help="FastAPI base URL.")
    parser.add_argument("--context", default=str(DEFAULT_CONTEXT_PATH), help="Research context JSON path.")
    parser.add_argument("--max-stocks", type=int, default=None, help="Override context scope.max_stocks.")
    parser.add_argument("--poll-seconds", type=float, default=2.0, help="Job polling interval.")
    args = parser.parse_args()

    context_path = Path(args.context)
    context = read_json(context_path)
    run_dir = RUNS_ROOT / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    started_at = now_text()
    strategy = build_strategy(args.strategy, context)
    payload = build_market_payload(context, strategy, args.max_stocks)
    previous_brief = read_text(NEXT_BRIEF_PATH)

    write_text(run_dir / "hypothesis.md", render_hypothesis(args.run_id, started_at, strategy, context, previous_brief))
    write_json(run_dir / "context.json", context)
    write_json(run_dir / "strategies.json", {"selected": args.strategy, "strategy": strategy})

    job = api_json(args.api_base, "/api/backtests/market/jobs", method="POST", payload=payload)
    result_job = poll_job(args.api_base, job["jobId"], args.poll_seconds)
    if result_job.get("status") != "ok":
        write_json(run_dir / "results.json", {"job": result_job, "payload": payload})
        review = render_failed_review(args.run_id, started_at, strategy, context, result_job)
        write_text(run_dir / "review.md", review)
        write_text(run_dir / "next-input.md", review)
        raise SystemExit(f"Research job failed: {result_job.get('message') or result_job.get('error')}")

    result = result_job["result"]
    qualified = qualified_symbols(result.get("results", []), context)
    analysis = summarize_result(result, qualified, context)
    output = {
        "runId": args.run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "payload": payload,
        "job": public_job_snapshot(result_job),
        "analysis": analysis,
        "qualifiedSymbols": qualified,
        "result": result,
    }
    write_json(run_dir / "results.json", output)

    review = render_review(args.run_id, started_at, strategy, context, analysis, qualified, result)
    next_input = render_next_input(args.run_id, strategy, analysis, qualified)
    write_text(run_dir / "review.md", review)
    write_text(run_dir / "next-input.md", next_input)
    write_text(NEXT_BRIEF_PATH, next_input)

    print(json.dumps({"runId": args.run_id, "analysis": analysis, "runDir": str(run_dir)}, ensure_ascii=False, indent=2))


def build_strategy(name: str, context: dict[str, Any]) -> dict[str, Any]:
    preset = deepcopy(STRATEGIES[name])
    config = deepcopy(BASE_STRATEGY_CONFIG)
    config.update(context.get("capital", {}))
    config.update(context.get("costs", {}))
    config.update(context.get("risk", {}))
    config.update(preset["config"])
    config.update(context.get("strategy_overrides", {}))
    config.update(context.get("execution_stress", {}))
    entry_risk_filter = context.get("portfolio_target", {}).get("entryRiskFilter")
    if entry_risk_filter:
        config["entryRiskFilter"] = deepcopy(entry_risk_filter)
    preset["name"] = name
    preset["config"] = config
    return preset


def build_market_payload(context: dict[str, Any], strategy: dict[str, Any], max_stocks: int | None) -> dict[str, Any]:
    scope = deepcopy(context["scope"])
    if max_stocks is not None:
        scope["max_stocks"] = max_stocks
    payload = {
        "start_date": scope["start_date"],
        "end_date": scope["end_date"],
        "config": strategy["config"],
        "pool_id": scope.get("pool_id"),
        "q": scope.get("q"),
        "industry": scope.get("industry"),
        "market": scope.get("market"),
        "min_bars": int(scope.get("min_bars", 120)),
        "max_stocks": int(scope.get("max_stocks", 0) or 0),
    }
    for key in [
        "exclude_st",
        "exclude_bj",
        "min_list_days",
        "min_avg_amount",
        "min_avg_circ_mv",
        "min_avg_turnover_rate_f",
    ]:
        if key in scope and scope[key] is not None:
            payload[key] = scope[key]
    return payload


def qualified_symbols(results: list[dict[str, Any]], context: dict[str, Any]) -> list[dict[str, Any]]:
    rules = context["evaluation"]
    min_return = float(rules["qualified_symbol_min_return"])
    max_abs_drawdown = float(rules["qualified_symbol_max_abs_drawdown"])
    min_profit_loss_ratio = float(rules.get("qualified_symbol_min_profit_loss_ratio", 0) or 0)
    min_trades = int(rules["minimum_completed_trades"])
    min_discipline = int(rules["minimum_discipline_score"])
    qualified = [
        item
        for item in results
        if float(item.get("totalReturn") or 0) >= min_return
        and abs(float(item.get("maxDrawdown") or 0)) <= max_abs_drawdown
        and (not min_profit_loss_ratio or float(item.get("profitLossRatio") or 0) >= min_profit_loss_ratio)
        and int(item.get("tradeCount") or 0) >= min_trades
        and int(item.get("disciplineScore") or 0) >= min_discipline
    ]
    qualified.sort(key=lambda item: (float(item["totalReturn"]), int(item["disciplineScore"]), int(item["tradeCount"])), reverse=True)
    return qualified


def symbol_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts_code": item.get("ts_code"),
        "name": item.get("name"),
        "totalReturn": item.get("totalReturn"),
        "maxDrawdown": item.get("maxDrawdown"),
        "winRate": item.get("winRate"),
        "profitLossRatio": item.get("profitLossRatio"),
        "profitFactor": item.get("profitFactor"),
        "annualizedReturn": item.get("annualizedReturn"),
        "annualizedVolatility": item.get("annualizedVolatility"),
        "sharpeRatio": item.get("sharpeRatio"),
        "sortinoRatio": item.get("sortinoRatio"),
        "calmarRatio": item.get("calmarRatio"),
        "maxDrawdownDurationDays": item.get("maxDrawdownDurationDays"),
        "tradeCount": item.get("tradeCount"),
        "completedTrades": item.get("completedTrades"),
        "disciplineScore": item.get("disciplineScore"),
    }


def ranked_symbols(results: list[dict[str, Any]], count: int, reverse: bool) -> list[dict[str, Any]]:
    ranked = sorted(results, key=lambda item: float(item.get("totalReturn") or 0), reverse=reverse)
    return [symbol_snapshot(item) for item in ranked[:count]]


def summarize_tail_risk(bottom: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    rules = context["evaluation"]
    max_abs_loss = float(rules.get("tail_max_abs_loss", rules.get("qualified_symbol_max_abs_drawdown", 0.1)))
    max_abs_drawdown = float(rules.get("tail_max_abs_drawdown", rules.get("qualified_symbol_max_abs_drawdown", 0.1)))
    min_profit_loss_ratio = float(rules.get("tail_min_profit_loss_ratio", rules.get("qualified_symbol_min_profit_loss_ratio", 0) or 0))
    expected_count = int(rules.get("tail_bottom_count", 10))

    loss_violations = [
        item
        for item in bottom
        if float(item.get("totalReturn") or 0) < -max_abs_loss
    ]
    drawdown_violations = [
        item
        for item in bottom
        if abs(float(item.get("maxDrawdown") or 0)) > max_abs_drawdown
    ]
    ratio_violations = [
        item
        for item in bottom
        if min_profit_loss_ratio and float(item.get("profitLossRatio") or 0) < min_profit_loss_ratio
    ]
    returns = [float(item.get("totalReturn") or 0) for item in bottom]
    drawdowns = [float(item.get("maxDrawdown") or 0) for item in bottom]
    ratios = [float(item.get("profitLossRatio") or 0) for item in bottom]

    return {
        "expectedCount": expected_count,
        "checkedCount": len(bottom),
        "tailRiskMet": len(bottom) >= expected_count and not loss_violations and not drawdown_violations and not ratio_violations,
        "thresholds": {
            "maxAbsLoss": max_abs_loss,
            "maxAbsDrawdown": max_abs_drawdown,
            "minProfitLossRatio": min_profit_loss_ratio,
        },
        "worstReturn": min(returns) if returns else None,
        "worstDrawdown": min(drawdowns) if drawdowns else None,
        "minProfitLossRatio": min(ratios) if ratios else None,
        "lossViolationCount": len(loss_violations),
        "drawdownViolationCount": len(drawdown_violations),
        "profitLossRatioViolationCount": len(ratio_violations),
    }


def summarize_result(result: dict[str, Any], qualified: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("summary", {})
    results = result.get("results", [])
    tested = int(summary.get("tested") or 0)
    candidates = int(summary.get("candidates") or 0)
    rules = context["evaluation"]
    full_scan_floor = int(rules["minimum_tested_symbols_for_full_scan"])
    minimum_qualified = int(rules.get("minimum_qualified_symbols_for_full_scan", 1))
    tail_count = int(rules.get("tail_bottom_count", 10))
    top = ranked_symbols(results, 10, reverse=True)
    bottom = ranked_symbols(results, tail_count, reverse=False)
    is_full_enough = tested >= full_scan_floor
    base_target_met = len(qualified) >= minimum_qualified and is_full_enough
    tail_risk = summarize_tail_risk(bottom, context)
    return {
        "targetMet": base_target_met and tail_risk["tailRiskMet"],
        "baseTargetMet": base_target_met,
        "qualifiedCount": len(qualified),
        "minimumQualifiedSymbols": minimum_qualified,
        "tested": tested,
        "candidates": candidates,
        "skipped": int(summary.get("skipped") or 0),
        "failed": int(summary.get("failed") or 0),
        "positiveRate": summary.get("positiveRate"),
        "averageReturn": summary.get("averageReturn", summary.get("avgReturn")),
        "medianReturn": summary.get("medianReturn"),
        "averageDrawdown": summary.get("averageDrawdown", summary.get("avgMaxDrawdown")),
        "isFullEnough": is_full_enough,
        "tailRisk": tail_risk,
        "top10": top,
        "bottom10": bottom,
    }


def render_hypothesis(run_id: str, started_at: str, strategy: dict[str, Any], context: dict[str, Any], previous_brief: str) -> str:
    objective = context["objective"]
    scope = context["scope"]
    return f"""# {run_id} 假设

- 时间：{started_at}
- 策略：{strategy["label"]} (`{strategy["name"]}`)
- 假设：{strategy["hypothesis"]}
- 数据区间：{scope["start_date"]} 至 {scope["end_date"]}
- 候选范围：{"全市场" if not scope.get("pool_id") else "标的池 " + str(scope.get("pool_id"))}
- 成功标准：头部合格样本达到三年总收益率 >= {objective["target_total_return"]:.0%}、盈亏比 >= {objective["target_profit_loss_ratio"]}:1、最大回撤绝对值 <= {objective["max_abs_drawdown"]:.0%}，同时收益后 10 的亏损、回撤和盈亏比不触发尾部风险红线。
- 失败条件：无足够合格样本，或样本不足，或收益前 10 好看但收益后 10 的亏损/回撤/盈亏比失控。
- 口径限制：当前是逐标的单票扫描，不是共享资金组合回测。

## 上一轮输入

{previous_brief.strip()}
"""


def render_review(
    run_id: str,
    started_at: str,
    strategy: dict[str, Any],
    context: dict[str, Any],
    analysis: dict[str, Any],
    qualified: list[dict[str, Any]],
    result: dict[str, Any],
) -> str:
    summary = result.get("summary", {})
    target_text = "达标" if analysis["targetMet"] else "未达标"
    base_target_text = "达标" if analysis["baseTargetMet"] else "未达标"
    tail = analysis["tailRisk"]
    tail_text = "通过" if tail["tailRiskMet"] else "未通过"
    qualified_lines = "\n".join(
        f"- `{item['ts_code']}` {item.get('name') or ''}：收益 {float(item['totalReturn']):.2%}，Sharpe {format_optional_number(item.get('sharpeRatio'))}，盈亏比 {format_optional_ratio(item.get('profitLossRatio'))}，回撤 {float(item['maxDrawdown']):.2%}，交易 {item['tradeCount']}，纪律 {item['disciplineScore']}"
        for item in qualified[:20]
    ) or "- 无"
    top_lines = "\n".join(
        f"- `{item['ts_code']}` {item.get('name') or ''}：收益 {float(item['totalReturn']):.2%}，Sharpe {format_optional_number(item.get('sharpeRatio'))}，盈亏比 {format_optional_ratio(item.get('profitLossRatio'))}，回撤 {float(item['maxDrawdown']):.2%}，交易 {item['tradeCount']}"
        for item in analysis["top10"]
    ) or "- 无"
    bottom_lines = "\n".join(
        f"- `{item['ts_code']}` {item.get('name') or ''}：收益 {float(item['totalReturn']):.2%}，Sharpe {format_optional_number(item.get('sharpeRatio'))}，盈亏比 {format_optional_ratio(item.get('profitLossRatio'))}，回撤 {float(item['maxDrawdown']):.2%}，交易 {item['tradeCount']}"
        for item in analysis["bottom10"]
    ) or "- 无"
    return f"""# {run_id} 复盘

- 开始时间：{started_at}
- 结束时间：{now_text()}
- 策略：{strategy["label"]} (`{strategy["name"]}`)
- 结论：{target_text}

## 结果摘要

- 候选数：{summary.get("candidates")}
- 已测试：{summary.get("tested")}
- 跳过：{summary.get("skipped")}
- 失败：{summary.get("failed")}
- 合格样本数：{analysis["qualifiedCount"]}
- 基础目标：{base_target_text}
- 收益后 10 尾部审计：{tail_text}
- 尾部最差收益：{format_optional_percent(tail.get("worstReturn"))}
- 尾部最深回撤：{format_optional_percent(tail.get("worstDrawdown"))}
- 尾部最低盈亏比：{format_optional_ratio(tail.get("minProfitLossRatio"))}
- 尾部违规：亏损 {tail["lossViolationCount"]}，回撤 {tail["drawdownViolationCount"]}，盈亏比 {tail["profitLossRatioViolationCount"]}
- 正收益占比：{format_optional_percent(summary.get("positiveRate"))}
- 平均收益：{format_optional_percent(analysis.get("averageReturn"))}
- 中位收益：{format_optional_percent(analysis.get("medianReturn"))}
- 平均回撤：{format_optional_percent(analysis.get("averageDrawdown"))}

## 合格样本

{qualified_lines}

## 收益排名前 10

{top_lines}

## 收益排名后 10

{bottom_lines}

## 解释

本轮只验证了 `{strategy["name"]}` 在当前三年窗口下的逐标的单票表现。即使存在头部合格样本，也必须看收益后 10 是否守住亏损、回撤和盈亏比红线；否则不能进入组合策略落地阶段。

## 下一步

{next_step_text(strategy["name"], analysis)}
"""


def render_next_input(run_id: str, strategy: dict[str, Any], analysis: dict[str, Any], qualified: list[dict[str, Any]]) -> str:
    best = qualified[0] if qualified else None
    tail = analysis["tailRisk"]
    best_text = (
        f"最佳合格样本 `{best['ts_code']}` {best.get('name') or ''}，收益 {float(best['totalReturn']):.2%}，盈亏比 {format_optional_ratio(best.get('profitLossRatio'))}，回撤 {float(best['maxDrawdown']):.2%}。"
        if best
        else "没有出现同时满足收益、盈亏比、回撤和最小交易数的合格样本。"
    )
    return f"""# 下一轮策略输入

上一轮：`{run_id}`，策略 `{strategy["name"]}`。

结论：{"达标" if analysis["targetMet"] else "未达标"}。{best_text}

关键数字：

- 已测试：{analysis["tested"]}
- 合格样本数：{analysis["qualifiedCount"]}
- 平均收益：{format_optional_percent(analysis.get("averageReturn"))}
- 中位收益：{format_optional_percent(analysis.get("medianReturn"))}
- 正收益占比：{format_optional_percent(analysis.get("positiveRate"))}
- 收益后 10 审计：{"通过" if tail["tailRiskMet"] else "未通过"}
- 尾部最差收益：{format_optional_percent(tail.get("worstReturn"))}
- 尾部最深回撤：{format_optional_percent(tail.get("worstDrawdown"))}
- 尾部最低盈亏比：{format_optional_ratio(tail.get("minProfitLossRatio"))}
- 尾部违规：亏损 {tail["lossViolationCount"]}，回撤 {tail["drawdownViolationCount"]}，盈亏比 {tail["profitLossRatioViolationCount"]}

下一轮建议：

{next_step_text(strategy["name"], analysis)}
"""


def render_failed_review(run_id: str, started_at: str, strategy: dict[str, Any], context: dict[str, Any], job: dict[str, Any]) -> str:
    return f"""# {run_id} 失败复盘

- 开始时间：{started_at}
- 结束时间：{now_text()}
- 策略：{strategy["label"]} (`{strategy["name"]}`)
- 状态：{job.get("status")}
- 信息：{job.get("message") or job.get("error")}

本轮没有得到可用回测结果。下一轮先修复 API 或数据状态，不要基于失败结果调参。
"""


def next_step_text(strategy_name: str, analysis: dict[str, Any]) -> str:
    if not analysis["isFullEnough"]:
        return "- 当前测试样本不足，先扩大到完整全市场或修复数据覆盖，再比较策略。"
    tail = analysis.get("tailRisk", {})
    if analysis.get("baseTargetMet") and not tail.get("tailRiskMet"):
        return "- 头部样本已经过线，但收益后 10 的亏损/回撤/盈亏比没有守住。下一轮优先做尾部风险控制：加入 ST/退市/低流动性/上市不足过滤，增加市场状态过滤或降低入场频率；在尾部审计通过前，不进入组合落地。"
    if analysis["targetMet"]:
        if analysis["qualifiedCount"] <= 3:
            return "- 硬目标已有样本达标，但覆盖太窄。下一轮优先做稳健性验证和覆盖率提升：不同窗口、排除 ST/低流动性、加入基本面质量过滤，并尝试让达标样本数扩大。"
        return "- 已有多个样本达标。下一轮做消融和稳健性验证，确认收益来自趋势持有而不是少数标的偶然行情。"
    if analysis["qualifiedCount"] == 0:
        top = analysis["top10"][0] if analysis["top10"] else {}
        top_return = float(top.get("totalReturn") or 0)
        top_drawdown = abs(float(top.get("maxDrawdown") or 0))
        top_ratio = float(top.get("profitLossRatio") or 0)
        if top_ratio >= 2 and top_drawdown <= 0.1 and top_return < 0.3:
            return "- 当前瓶颈是收益弹性，不是盈亏比或回撤。下一轮在同一顺势逻辑下提高止盈或加入趋势持有/移动止盈，目标是把头部样本收益推过 30%。"
        return "- 该策略没有满足目标的样本。下一轮先定位主要失败项，再只针对失败项做一次参数或过滤器改动。"
    if strategy_name in {"boll-rebound", "rsi-reversal"}:
        return "- 有合格样本时，下一轮加入更严格趋势/流动性过滤，验证合格样本是否仍存在，避免单纯接反弹。"
    return "- 下一轮围绕合格样本做消融：关闭/开启 MACD、RSI、成交量过滤，确认收益来自哪个因子。"


def api_json(api_base: str, path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{api_base.rstrip('/')}{path}"
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {body}") from exc


def poll_job(api_base: str, job_id: str, poll_seconds: float) -> dict[str, Any]:
    while True:
        job = api_json(api_base, f"/api/backtests/market/jobs/{urllib.parse.quote(job_id)}")
        status = job.get("status")
        if status in {"ok", "failed"}:
            return job
        time.sleep(poll_seconds)


def public_job_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items() if not key.startswith("_") and key != "result"}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write_text(path: Path, value: str) -> None:
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def now_text() -> str:
    return datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M %z")


def format_optional_percent(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2%}"
    except (TypeError, ValueError):
        return str(value)


def format_optional_ratio(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}:1"
    except (TypeError, ValueError):
        return str(value)


def format_optional_number(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


if __name__ == "__main__":
    main()
