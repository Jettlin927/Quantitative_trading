from __future__ import annotations

from datetime import date, datetime
from math import floor, isfinite, sqrt
from statistics import mean
from typing import Any

from .ai_client import analyze_with_deepseek


DEFAULT_CONFIG = {
    "marketState": "normal",
    "entryMode": "boll-rebound",
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
}


def run_backtest(rows: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    cfg = normalize_config(cfg)
    data = enrich_rows(rows, cfg)
    cash = float(cfg["initialCash"])
    shares = 0
    entry_price = 0.0
    stop_price = 0.0
    partial_taken = False
    weekly_actions: dict[str, int] = {}
    trades: list[dict[str, Any]] = []
    equity: list[dict[str, Any]] = []
    completed_trades: list[dict[str, Any]] = []
    blocked = {"market": 0, "weekly": 0, "sizing": 0, "trend": 0, "sameDay": 0}

    def can_trade(trade_date: str) -> bool:
        return weekly_actions.get(get_week_key(trade_date), 0) < int(cfg["weeklyTradeLimit"])

    def record_action(trade_date: str) -> None:
        key = get_week_key(trade_date)
        weekly_actions[key] = weekly_actions.get(key, 0) + 1

    for index, row in enumerate(data):
        profitable_exit_today = False

        def execute_sell(price: float, quantity: int, reason: str, force: bool = False) -> None:
            nonlocal cash, shares, entry_price, stop_price, partial_taken, profitable_exit_today
            if not force and not can_trade(row["date"]):
                blocked["weekly"] += 1
                return

            sell_quantity = min(quantity, shares)
            gross = price * sell_quantity
            fee = gross * (float(cfg["commissionPct"]) + float(cfg["stampDutyPct"]))
            cash += gross - fee
            shares -= sell_quantity
            record_action(row["date"])
            trades.append(
                {
                    "date": row["date"],
                    "action": "卖出" if shares == 0 else "减仓",
                    "price": price,
                    "quantity": sell_quantity,
                    "cash": cash,
                    "reason": reason,
                    "fee": fee,
                    "equity": cash + shares * row["close"],
                }
            )

            if shares == 0:
                profitable_exit_today = price > entry_price
                completed_trades.append(
                    {
                        "exitDate": row["date"],
                        "entryPrice": entry_price,
                        "exitPrice": price,
                        "returnPct": (price - entry_price) / entry_price if entry_price else 0,
                    }
                )
                entry_price = 0
                stop_price = 0
                partial_taken = False

        def execute_buy(price: float, quantity: int, reason: str, stop: float) -> None:
            nonlocal cash, shares, entry_price, stop_price, partial_taken
            if not can_trade(row["date"]):
                blocked["weekly"] += 1
                return

            gross = price * quantity
            fee = gross * float(cfg["commissionPct"])
            cash -= gross + fee
            shares += quantity
            entry_price = price
            stop_price = stop
            partial_taken = False
            record_action(row["date"])
            trades.append(
                {
                    "date": row["date"],
                    "action": "买入",
                    "price": price,
                    "quantity": quantity,
                    "cash": cash,
                    "reason": reason,
                    "fee": fee,
                    "equity": cash + shares * row["close"],
                }
            )

        if shares > 0:
            stop_hit = row["low"] <= stop_price
            tp2_hit = row["high"] >= entry_price * (1 + float(cfg["takeProfit2Pct"]))
            tp1_hit = row["high"] >= entry_price * (1 + float(cfg["takeProfit1Pct"])) and not partial_taken
            weak_exit = cfg["marketState"] == "weak" and row["close"] < row["trendSlowMa"]

            if stop_hit:
                execute_sell(
                    stop_price,
                    shares,
                    "保本线触发" if stop_price >= entry_price else "硬止损触发",
                    bool(cfg.get("forceStopOverridesLimit", True)),
                )
            elif tp2_hit:
                execute_sell(entry_price * (1 + float(cfg["takeProfit2Pct"])), shares, "第二止盈清仓")
            elif tp1_hit:
                quantity = round_to_lot(floor(shares / 2), int(cfg["lotSize"]))
                if quantity > 0:
                    execute_sell(entry_price * (1 + float(cfg["takeProfit1Pct"])), quantity, "第一止盈减半")
                    partial_taken = True
                    stop_price = entry_price
            elif weak_exit:
                execute_sell(row["close"], shares, "退潮跌破慢线防守")

        if shares == 0:
            if bool(cfg.get("blockSameDayReentry", True)) and profitable_exit_today:
                blocked["sameDay"] += 1
            else:
                signal = should_enter(row, data[index - 1] if index else None, cfg)
                if signal["ok"]:
                    risk_cash = float(cfg["initialCash"]) * float(cfg["riskPct"])
                    stop = calc_stop_price(row, cfg)
                    risk_per_share = max(row["close"] - stop, 0.01)
                    risk_sized = floor(risk_cash / risk_per_share)
                    cap_sized = floor((float(cfg["initialCash"]) * float(cfg["positionCapPct"])) / row["close"])
                    affordable = floor(cash / (row["close"] * (1 + float(cfg["commissionPct"]))))
                    quantity = round_to_lot(min(risk_sized, cap_sized, affordable), int(cfg["lotSize"]))
                    if quantity > 0:
                        execute_buy(row["close"], quantity, signal["reason"], stop)
                    else:
                        blocked["sizing"] += 1
                elif signal.get("blockedByMarket"):
                    blocked["market"] += 1
                elif signal.get("blockedByTrend"):
                    blocked["trend"] += 1

        equity.append({"date": row["date"], "equity": cash + shares * row["close"], "price": row["close"], "cash": cash, "shares": shares})

    final_equity = equity[-1]["equity"] if equity else float(cfg["initialCash"])
    total_return = final_equity / float(cfg["initialCash"]) - 1
    max_drawdown = calc_max_drawdown([point["equity"] for point in equity])
    win_rate = (
        len([trade for trade in completed_trades if trade["returnPct"] > 0]) / len(completed_trades)
        if completed_trades
        else 0
    )

    summary = {
        "cfg": cfg,
        "rows": data,
        "trades": trades,
        "equity": equity,
        "blocked": blocked,
        "completedTrades": completed_trades,
        "finalEquity": final_equity,
        "totalReturn": total_return,
        "maxDrawdown": max_drawdown,
        "winRate": win_rate,
        "disciplineScore": calc_discipline_score(trades, blocked, cfg),
    }
    local_analysis = build_strategy_analysis(summary, data, cfg)
    summary["aiAnalysis"] = analyze_with_deepseek(local_analysis, summary, data, cfg)
    return json_safe(summary)


def enrich_rows(rows: list[dict[str, Any]], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    cfg = normalize_config(cfg)
    enriched: list[dict[str, Any]] = []
    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    volumes: list[float] = []
    true_ranges: list[float] = []
    ema_fast = None
    ema_slow = None
    macd_signal = None
    k_value = 50.0
    d_value = 50.0

    for row in rows:
        current = dict(row)
        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        volume = float(row.get("volume") or row.get("vol") or 0)
        previous_close = closes[-1] if closes else close

        closes.append(close)
        highs.append(high)
        lows.append(low)
        volumes.append(volume)
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))

        ema_fast = ema(close, ema_fast, int(cfg["macdFastPeriod"]))
        ema_slow = ema(close, ema_slow, int(cfg["macdSlowPeriod"]))
        macd_dif = ema_fast - ema_slow
        macd_signal = ema(macd_dif, macd_signal, int(cfg["macdSignalPeriod"]))
        macd_hist = (macd_dif - macd_signal) * 2

        boll_mid = sma(closes, int(cfg["bollPeriod"]))
        boll_sd = stdev(closes, int(cfg["bollPeriod"]))
        current["volume"] = volume
        current["ma5"] = sma(closes, 5)
        current["ma10"] = sma(closes, 10)
        current["ma20"] = sma(closes, 20)
        current["ma30"] = sma(closes, 30)
        current["ma60"] = sma(closes, 60)
        current["trendFastMa"] = sma(closes, int(cfg["trendFastPeriod"]))
        current["trendSlowMa"] = sma(closes, int(cfg["trendSlowPeriod"]))
        current["trendLongMa"] = sma(closes, int(cfg["trendLongPeriod"]))
        boll_upper = boll_mid + float(cfg["bollDev"]) * boll_sd if finite(boll_mid) and finite(boll_sd) else float("nan")
        boll_lower = boll_mid - float(cfg["bollDev"]) * boll_sd if finite(boll_mid) and finite(boll_sd) else float("nan")
        current["bollMid"] = boll_mid
        current["bollUpper"] = boll_upper
        current["bollLower"] = boll_lower
        current["bollBandwidthPct"] = (boll_upper - boll_lower) / boll_mid if finite(boll_upper) and finite(boll_lower) and boll_mid else float("nan")
        current["volMa"] = sma(volumes, int(cfg["volumeMaPeriod"]))
        current["macdDif"] = macd_dif
        current["macdDea"] = macd_signal
        current["macdHist"] = macd_hist
        current["rsi6"] = rsi(closes, 6)
        current["rsi12"] = rsi(closes, 12)
        current["rsi24"] = rsi(closes, 24)
        current["rsiStrategy"] = rsi(closes, int(cfg["rsiPeriod"]))
        current["atr14"] = sma(true_ranges, 14)
        current["atrStrategy"] = sma(true_ranges, int(cfg["atrPeriod"]))

        if len(closes) >= int(cfg["kdjPeriod"]):
            period_high = max(highs[-int(cfg["kdjPeriod"]) :])
            period_low = min(lows[-int(cfg["kdjPeriod"]) :])
            rsv = 50.0 if period_high == period_low else (close - period_low) / (period_high - period_low) * 100
            k_value = k_value * 2 / 3 + rsv / 3
            d_value = d_value * 2 / 3 + k_value / 3
            current["kdjK"] = k_value
            current["kdjD"] = d_value
            current["kdjJ"] = 3 * k_value - 2 * d_value
        else:
            current["kdjK"] = float("nan")
            current["kdjD"] = float("nan")
            current["kdjJ"] = float("nan")

        enriched.append(current)

    return enriched


def should_enter(row: dict[str, Any], prev_row: dict[str, Any] | None, cfg: dict[str, Any]) -> dict[str, Any]:
    cfg = normalize_config(cfg)
    if not prev_row:
        return {"ok": False, "reason": "指标不足"}

    if bool(cfg.get("blockWeakMarket", True)) and cfg["marketState"] == "weak":
        return {"ok": False, "reason": "退潮期禁止开新仓", "blockedByMarket": True}

    trend_ok = (
        not bool(cfg.get("useTrendFilter", True))
        or row["trendFastMa"] >= row["trendSlowMa"]
        or row["close"] >= row["trendLongMa"]
    )
    if not trend_ok:
        return {"ok": False, "reason": "趋势过滤未通过", "blockedByTrend": True}

    factor_ok, factor_reason = common_factor_filters(row, cfg)
    if not factor_ok:
        return {"ok": False, "reason": factor_reason, "blockedByTrend": True}

    mode = cfg["entryMode"]
    volume_ok = volume_breakout(row, cfg, fallback_ok=True)

    if mode == "boll-rebound":
        if not finite(row["bollLower"]) or not finite(prev_row["bollLower"]):
            return {"ok": False, "reason": "BOLL 指标不足"}
        tolerance = 1 + float(cfg["bollTolerancePct"])
        touched_lower = prev_row["low"] <= prev_row["bollLower"] * tolerance or row["low"] <= row["bollLower"] * tolerance
        rebound = row["close"] > row["open"] and row["close"] > prev_row["close"]
        return {"ok": touched_lower and rebound, "reason": "BOLL 下轨试错反弹" if touched_lower and rebound else "未出现下轨反弹"}

    if mode == "midline-confirm":
        if not finite(row["bollMid"]) or not finite(prev_row["bollMid"]):
            return {"ok": False, "reason": "BOLL 中轨不足"}
        tolerance = float(cfg["midlineTolerancePct"])
        near_mid = abs(row["low"] - row["bollMid"]) / row["bollMid"] <= tolerance or abs(prev_row["close"] - prev_row["bollMid"]) / prev_row["bollMid"] <= tolerance
        confirm = row["close"] > row["bollMid"] and row["close"] > row["trendFastMa"]
        return {"ok": near_mid and confirm, "reason": "中轨回踩后重新站上" if near_mid and confirm else "中轨确认不足"}

    if mode == "macd-cross":
        cross_up = crossed_above(row, prev_row, "macdDif", "macdDea")
        zero_ok = not bool(cfg.get("macdRequireZeroAxis", False)) or row["macdDif"] >= 0
        ok = cross_up and zero_ok and volume_ok
        return {"ok": ok, "reason": "MACD DIF 上穿 DEA" if ok else "MACD 金叉确认不足"}

    if mode == "boll-breakout":
        if not finite(row["bollUpper"]) or not finite(prev_row["bollUpper"]):
            return {"ok": False, "reason": "BOLL 上轨不足"}
        breakout = row["close"] > row["bollUpper"] and prev_row["close"] <= prev_row["bollUpper"]
        ok = breakout and volume_ok
        return {"ok": ok, "reason": "BOLL 上轨放量突破" if ok else "BOLL 突破不足"}

    if mode == "boll-squeeze":
        if not finite(row["bollBandwidthPct"]) or not finite(row["bollMid"]):
            return {"ok": False, "reason": "BOLL 带宽不足"}
        squeeze = row["bollBandwidthPct"] <= float(cfg["bollBandwidthMaxPct"])
        thrust = row["close"] > row["bollMid"] and row["close"] > prev_row["close"] and volume_ok
        ok = squeeze and thrust
        return {"ok": ok, "reason": "BOLL 收口后向上扩张" if ok else "BOLL 收口突破不足"}

    if mode == "rsi-reversal":
        if not finite(row["rsiStrategy"]) or not finite(prev_row["rsiStrategy"]):
            return {"ok": False, "reason": "RSI 指标不足"}
        rebound = prev_row["rsiStrategy"] <= float(cfg["rsiLowerBound"]) and row["rsiStrategy"] > prev_row["rsiStrategy"] and row["close"] > prev_row["close"]
        ok = rebound and row["rsiStrategy"] <= float(cfg["rsiUpperBound"])
        return {"ok": ok, "reason": "RSI 超卖后回升" if ok else "RSI 反转不足"}

    if mode == "ma-cross":
        cross_up = crossed_above(row, prev_row, "trendFastMa", "trendSlowMa")
        long_ok = not finite(row["trendLongMa"]) or row["close"] >= row["trendLongMa"]
        ok = cross_up and long_ok and volume_ok
        return {"ok": ok, "reason": "快均线上穿慢均线" if ok else "均线金叉不足"}

    trend_follow = row["close"] > row["trendFastMa"] and row["trendFastMa"] > row["trendSlowMa"] and row["close"] > row["trendLongMa"] and volume_ok
    return {"ok": trend_follow, "reason": "MA 多头排列放量跟随" if trend_follow else "趋势跟随条件不足"}


def common_factor_filters(row: dict[str, Any], cfg: dict[str, Any]) -> tuple[bool, str]:
    if bool(cfg.get("useMacdFilter", False)) and (not finite(row["macdDif"]) or not finite(row["macdDea"]) or row["macdDif"] < row["macdDea"]):
        return False, "MACD 过滤未通过"

    if bool(cfg.get("useRsiFilter", False)):
        rsi_value = row["rsiStrategy"]
        if not finite(rsi_value) or rsi_value < float(cfg["rsiLowerBound"]) or rsi_value > float(cfg["rsiUpperBound"]):
            return False, "RSI 过滤未通过"

    return True, "常见因子过滤通过"


def volume_breakout(row: dict[str, Any], cfg: dict[str, Any], fallback_ok: bool = False) -> bool:
    if not row.get("volume") or not finite(row.get("volMa")):
        return fallback_ok
    return row["volume"] >= row["volMa"] * float(cfg["volumeBreakoutMultiplier"])


def crossed_above(row: dict[str, Any], prev_row: dict[str, Any], fast_key: str, slow_key: str) -> bool:
    values = [row.get(fast_key), row.get(slow_key), prev_row.get(fast_key), prev_row.get(slow_key)]
    if not all(finite(value) for value in values):
        return False
    return prev_row[fast_key] <= prev_row[slow_key] and row[fast_key] > row[slow_key]


def calc_stop_price(row: dict[str, Any], cfg: dict[str, Any]) -> float:
    fixed_stop = row["close"] * (1 - float(cfg["stopLossPct"]))
    if not bool(cfg.get("useAtrStop", False)) or not finite(row.get("atrStrategy")):
        return fixed_stop
    atr_stop = row["close"] - row["atrStrategy"] * float(cfg["atrStopMultiplier"])
    return min(fixed_stop, atr_stop)


def build_strategy_analysis(result: dict[str, Any], rows: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    trade_count = len(result["completedTrades"])
    sample_days = len(rows)
    total_return = float(result["totalReturn"])
    max_drawdown = float(result["maxDrawdown"])
    win_rate = float(result["winRate"])
    discipline_score = int(result["disciplineScore"])
    filter_count = sum(
        1
        for key in ["useTrendFilter", "useMacdFilter", "useRsiFilter", "blockWeakMarket", "blockSameDayReentry", "useAtrStop"]
        if bool(cfg.get(key))
    )

    score = 50
    score += min(18, max(-18, total_return * 100))
    score += min(12, max_drawdown * 100 * 0.9)
    score += min(10, (win_rate - 0.45) * 40)
    score += min(10, max(0, trade_count - 3))
    score += min(10, (discipline_score - 70) / 3)
    if sample_days < 250:
        score -= 12
    if trade_count < 6:
        score -= 12
    if filter_count >= 5:
        score -= 6
    score = max(0, min(100, round(score)))

    risks: list[str] = []
    strengths: list[str] = []
    next_checks = [
        "换 5-10 个同行业和非同行业标的复测，确认不是单票偶然性。",
        "把最近 20%-30% 日期作为样本外区间，避免参数只贴合历史。",
        "加入滑点、停牌、涨跌停无法成交等 A 股执行约束后再比较。",
        "和买入持有、简单 MA20 防守两个基准策略对照。",
    ]

    if sample_days < 250:
        risks.append("样本天数偏少，结论只能视为试运行。")
    if trade_count < 6:
        risks.append("完成交易笔数不足，胜率和收益不稳定。")
    if total_return <= 0:
        risks.append("当前参数在该区间未产生正收益。")
    else:
        strengths.append("该区间总收益为正，可以进入更多标的和样本外验证。")
    if max_drawdown <= -0.12:
        risks.append("最大回撤偏深，策略心理和资金承受度需要重新评估。")
    elif max_drawdown > -0.06:
        strengths.append("回撤暂时受控，但仍需用更长周期验证。")
    if win_rate >= 0.5 and trade_count >= 6:
        strengths.append("完成交易胜率高于 50%，具备继续拆解盈亏来源的价值。")
    if discipline_score >= 85:
        strengths.append("纪律约束执行良好，交易频率和仓位控制没有明显失控。")
    if filter_count >= 5:
        risks.append("过滤条件较多，存在参数过拟合风险。")

    latest = rows[-1] if rows else {}
    market_fit = describe_market_fit(str(cfg["entryMode"]), latest)
    verdict = "样本不足"
    if score >= 72 and trade_count >= 6:
        verdict = "可继续验证"
    elif score >= 55:
        verdict = "需要扩大样本"
    elif trade_count >= 3:
        verdict = "暂不可靠"

    return {
        "score": score,
        "verdict": verdict,
        "marketFit": market_fit,
        "summary": [
            f"当前入场模型：{entry_mode_label(str(cfg['entryMode']))}。",
            f"样本 {sample_days} 根日线，完成交易 {trade_count} 笔。",
            f"收益 {total_return * 100:.2f}%，最大回撤 {max_drawdown * 100:.2f}%，胜率 {win_rate * 100:.1f}%。",
        ],
        "strengths": strengths or ["暂无足够正面证据，先扩大样本。"],
        "risks": risks or ["未发现明显红旗，但这不代表策略已经有效。"],
        "nextChecks": next_checks,
        "factorRead": [
            {"name": "MACD", "value": factor_state(latest, "macdDif", "macdDea"), "comment": "趋势和动量确认，震荡市容易来回打脸。"},
            {"name": "BOLL", "value": format_factor_value(latest.get("bollBandwidthPct"), scale=100, suffix="%"), "comment": "带宽越窄越像压缩，单独突破需要成交量或趋势确认。"},
            {"name": "RSI", "value": format_factor_value(latest.get("rsiStrategy")), "comment": "强趋势中可长期超买或超卖，不应机械反向。"},
            {"name": "ATR", "value": format_factor_value(latest.get("atrStrategy")), "comment": "只衡量波动，不判断方向，适合做止损和仓位校准。"},
        ],
    }


def describe_market_fit(entry_mode: str, latest: dict[str, Any]) -> str:
    if entry_mode in {"macd-cross", "ma-cross", "trend-follow", "boll-breakout", "boll-squeeze"}:
        if finite(latest.get("trendFastMa")) and finite(latest.get("trendSlowMa")) and latest["trendFastMa"] >= latest["trendSlowMa"]:
            return "偏趋势跟随：更适合有持续方向和成交量配合的阶段。"
        return "偏趋势跟随：但最新均线结构未明显支持趋势。"
    if entry_mode in {"boll-rebound", "midline-confirm", "rsi-reversal"}:
        return "偏回撤/反转：更依赖止损纪律，强单边下跌时容易连续试错。"
    return "混合模型：需要用分市场状态的回测来判断适用区间。"


def entry_mode_label(entry_mode: str) -> str:
    return {
        "boll-rebound": "BOLL 下轨反弹",
        "midline-confirm": "BOLL 中轨确认",
        "trend-follow": "MA 多头趋势跟随",
        "macd-cross": "MACD 金叉",
        "boll-breakout": "BOLL 上轨突破",
        "boll-squeeze": "BOLL 收口突破",
        "rsi-reversal": "RSI 超卖反转",
        "ma-cross": "均线金叉",
    }.get(entry_mode, entry_mode)


def factor_state(row: dict[str, Any], fast_key: str, slow_key: str) -> str:
    if not finite(row.get(fast_key)) or not finite(row.get(slow_key)):
        return "--"
    return "多头" if row[fast_key] >= row[slow_key] else "空头"


def format_factor_value(value: Any, scale: float = 1, suffix: str = "") -> str:
    if not finite(value):
        return "--"
    return f"{float(value) * scale:.2f}{suffix}"


def calc_discipline_score(trades: list[dict[str, Any]], blocked: dict[str, int], cfg: dict[str, Any]) -> int:
    score = 100
    score -= min(30, blocked["weekly"] * 6)
    score -= min(18, blocked["market"] * 3)
    score -= min(12, blocked["sizing"] * 3)
    score -= min(8, blocked["sameDay"] * 4)
    oversized = [
        trade
        for trade in trades
        if trade["action"] == "买入" and trade["price"] * trade["quantity"] > float(cfg["initialCash"]) * float(cfg["positionCapPct"]) * 1.01
    ]
    score -= len(oversized) * 10
    return max(0, round(score))


def normalize_config(cfg: dict[str, Any]) -> dict[str, Any]:
    return {**DEFAULT_CONFIG, **cfg}


def sma(values: list[float], period: int) -> float:
    if len(values) < period:
        return float("nan")
    return mean(values[-period:])


def ema(value: float, previous: float | None, period: int) -> float:
    if previous is None:
        return value
    multiplier = 2 / (period + 1)
    return value * multiplier + previous * (1 - multiplier)


def stdev(values: list[float], period: int) -> float:
    if len(values) < period:
        return float("nan")
    window = values[-period:]
    avg = mean(window)
    return sqrt(sum((value - avg) ** 2 for value in window) / period)


def rsi(values: list[float], period: int) -> float:
    if len(values) <= period:
        return float("nan")
    window = values[-(period + 1) :]
    gains = [max(window[index] - window[index - 1], 0) for index in range(1, len(window))]
    losses = [max(window[index - 1] - window[index], 0) for index in range(1, len(window))]
    avg_loss = mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = mean(gains) / avg_loss
    return 100 - (100 / (1 + rs))


def calc_max_drawdown(values: list[float]) -> float:
    if not values:
        return 0
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = min(max_drawdown, value / peak - 1)
    return max_drawdown


def round_to_lot(quantity: int, lot_size: int) -> int:
    if lot_size <= 1:
        return max(0, floor(quantity))
    return max(0, floor(quantity / lot_size) * lot_size)


def get_week_key(date_text: str) -> str:
    parsed = datetime.strptime(date_text, "%Y-%m-%d").date()
    first_day = date(parsed.year, 1, 1)
    day_offset = (parsed - first_day).days
    return f"{parsed.year}-{(day_offset + first_day.weekday()) // 7}"


def finite(value: Any) -> bool:
    return isinstance(value, int | float) and isfinite(float(value))


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and not isfinite(value):
        return None
    return value
