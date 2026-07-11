from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class CostModel:
    buy_rate: float = 0.00035
    sell_rate: float = 0.00085
    slippage_rate: float = 0.001

    def __post_init__(self) -> None:
        if min(self.buy_rate, self.sell_rate, self.slippage_rate) < 0:
            raise ValueError("交易成本参数不能为负数")


def simulate_target_weights(
    prices: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    open_trade_dates: Iterable[object],
    initial_nav: float = 1.0,
    cost: CostModel | None = None,
) -> pd.DataFrame:
    """Simulate full target portfolios that execute at the next trade-date open.

    This is a research return-space simulator. Missing prices remain hard data
    errors, while market constraints keep affected positions frozen and record
    the unfilled target instead of inventing fills.
    """
    _validate_prices(prices)
    _validate_targets(targets)
    if initial_nav <= 0:
        raise ValueError("initial_nav 必须大于 0")

    price_frame = prices.copy()
    target_frame = targets.copy()
    price_frame["trade_date"] = pd.to_datetime(price_frame["trade_date"])
    target_frame["signal_date"] = pd.to_datetime(target_frame["signal_date"])
    target_frame["available_date"] = pd.to_datetime(target_frame["available_date"])
    price_frame = price_frame.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    trade_dates = list(_validate_open_trade_calendar(price_frame, target_frame, open_trade_dates))
    schedule = _schedule_targets(target_frame, trade_dates)
    cost_model = cost or CostModel()

    nav = float(initial_nav)
    weights: dict[str, float] = {}
    cash_weight = 1.0
    previous_close: dict[str, float] = {}
    rows: list[dict[str, object]] = []

    for trade_date in trade_dates:
        day = price_frame[price_frame["trade_date"] == trade_date].set_index("ts_code")
        open_prices = pd.to_numeric(day["adj_open"], errors="coerce").to_dict()
        close_prices = pd.to_numeric(day["adj_close"], errors="coerce").to_dict()
        buyable = day["is_buyable_at_open"].fillna(False).astype(bool).to_dict()
        sellable = day["is_sellable_at_open"].fillna(False).astype(bool).to_dict()
        carried = day.get("is_valuation_carried", pd.Series(False, index=day.index)).fillna(False).astype(bool).to_dict()
        carried_valuation_count = 0
        for symbol, should_carry in carried.items():
            if should_carry and symbol in previous_close and previous_close[symbol] > 0:
                open_prices[symbol] = previous_close[symbol]
                close_prices[symbol] = previous_close[symbol]
                buyable[symbol] = False
                sellable[symbol] = False
                carried_valuation_count += 1

        if weights:
            _require_held_prices(weights, previous_close, open_prices, trade_date, "开盘")
            overnight_values = {symbol: weight * open_prices[symbol] / previous_close[symbol] for symbol, weight in weights.items()}
            overnight_gross = cash_weight + sum(overnight_values.values())
            if overnight_gross <= 0:
                raise ValueError(f"{trade_date.date()} 隔夜组合价值无效")
            nav *= overnight_gross
            weights = {symbol: value / overnight_gross for symbol, value in overnight_values.items()}
            cash_weight /= overnight_gross

        executed_signal_date = pd.NaT
        traded_weight = 0.0
        one_way_turnover = 0.0
        transaction_cost = 0.0
        blocked_buys: list[str] = []
        blocked_sells: list[str] = []
        unfilled_target_weight = 0.0
        if trade_date in schedule:
            executed_signal_date, target_weights = schedule[trade_date]
            missing_targets = sorted(symbol for symbol in target_weights if symbol not in open_prices or pd.isna(open_prices[symbol]))
            if missing_targets:
                raise ValueError(f"{trade_date.date()} 目标资产缺少开盘价：{', '.join(missing_targets[:10])}")
            plan = _plan_rebalance(weights, cash_weight, target_weights, buyable, sellable, cost_model)
            executable_sells = plan["sells"]
            executable_buys = plan["buys"]
            blocked_buys = plan["blocked_buys"]
            blocked_sells = plan["blocked_sells"]
            sells = sum(executable_sells.values())
            buys = sum(executable_buys.values())
            transaction_cost = plan["transaction_cost"]
            if transaction_cost >= 1:
                raise ValueError("交易成本超过组合净值")

            pre_cost_weights = dict(weights)
            for symbol, change in executable_sells.items():
                pre_cost_weights[symbol] = pre_cost_weights.get(symbol, 0.0) - change
            for symbol, change in executable_buys.items():
                pre_cost_weights[symbol] = pre_cost_weights.get(symbol, 0.0) + change
            pre_cost_weights = {symbol: value for symbol, value in pre_cost_weights.items() if value > 1e-12}
            pre_cost_cash = cash_weight + sells - buys - transaction_cost
            if pre_cost_cash < -1e-9:
                raise ValueError("交易成本导致现金为负")
            nav *= 1 - transaction_cost
            remaining_nav = 1 - transaction_cost
            weights = {symbol: value / remaining_nav for symbol, value in pre_cost_weights.items()}
            cash_weight = max(pre_cost_cash, 0.0) / remaining_nav
            target_symbols = set(weights) | set(target_weights)
            unfilled_target_weight = sum(
                abs(weights.get(symbol, 0.0) - target_weights.get(symbol, 0.0)) for symbol in target_symbols
            )
            if unfilled_target_weight < 1e-12:
                unfilled_target_weight = 0.0
            traded_weight = buys + sells
            one_way_turnover = max(buys, sells)

        if weights:
            _require_held_prices(weights, open_prices, close_prices, trade_date, "收盘")
            intraday_values = {symbol: weight * close_prices[symbol] / open_prices[symbol] for symbol, weight in weights.items()}
            intraday_gross = cash_weight + sum(intraday_values.values())
            if intraday_gross <= 0:
                raise ValueError(f"{trade_date.date()} 日内组合价值无效")
            nav *= intraday_gross
            weights = {symbol: value / intraday_gross for symbol, value in intraday_values.items()}
            cash_weight /= intraday_gross

        rows.append(
            {
                "trade_date": trade_date,
                "nav": float(nav),
                "cash_weight": float(cash_weight),
                "gross_exposure": float(sum(weights.values())),
                "executed_signal_date": executed_signal_date,
                "traded_weight": float(traded_weight),
                "one_way_turnover": float(one_way_turnover),
                "transaction_cost_rate": float(transaction_cost),
                "blocked_buys": ",".join(blocked_buys),
                "blocked_sells": ",".join(blocked_sells),
                "unfilled_target_weight": float(unfilled_target_weight),
                "carried_valuation_count": carried_valuation_count,
            }
        )
        previous_close = close_prices

    return pd.DataFrame(rows)


def _schedule_targets(targets: pd.DataFrame, trade_dates: list[pd.Timestamp]) -> dict[pd.Timestamp, tuple[pd.Timestamp, dict[str, float]]]:
    schedule: dict[pd.Timestamp, tuple[pd.Timestamp, dict[str, float]]] = {}
    for signal_date, group in targets.sort_values(["signal_date", "ts_code"]).groupby("signal_date", sort=True):
        future_dates = [trade_date for trade_date in trade_dates if trade_date > signal_date]
        if not future_dates:
            continue
        target_weights = {
            symbol: weight
            for symbol, weight in zip(group["ts_code"].astype(str), group["target_weight"].astype(float), strict=True)
            if weight > 0
        }
        execution_date = future_dates[0]
        if execution_date in schedule:
            previous_signal = schedule[execution_date][0]
            raise ValueError(
                f"多个信号日映射到同一执行日 {execution_date.date()}："
                f"{previous_signal.date()} 与 {signal_date.date()}"
            )
        schedule[execution_date] = (signal_date, target_weights)
    return schedule


def _plan_rebalance(
    weights: dict[str, float],
    cash_weight: float,
    target_weights: dict[str, float],
    buyable: dict[str, bool],
    sellable: dict[str, bool],
    cost: CostModel,
) -> dict[str, object]:
    """Solve target holdings in pre-cost NAV units so post-cost weights hit target."""
    symbols = set(weights) | set(target_weights)
    buy_cost_rate = cost.buy_rate + cost.slippage_rate
    sell_cost_rate = cost.sell_rate + cost.slippage_rate
    remaining_nav = 1.0
    plan: dict[str, object] | None = None
    for _ in range(100):
        desired_values = {symbol: target_weights.get(symbol, 0.0) * remaining_nav for symbol in symbols}
        requested_sells = {
            symbol: max(weights.get(symbol, 0.0) - desired_values[symbol], 0.0) for symbol in symbols
        }
        requested_buys = {
            symbol: max(desired_values[symbol] - weights.get(symbol, 0.0), 0.0) for symbol in symbols
        }
        blocked_sells = sorted(
            symbol for symbol, change in requested_sells.items() if change > 1e-14 and not sellable.get(symbol, False)
        )
        blocked_buys = sorted(
            symbol for symbol, change in requested_buys.items() if change > 1e-14 and not buyable.get(symbol, False)
        )
        executable_sells = {
            symbol: change
            for symbol, change in requested_sells.items()
            if change > 1e-14 and symbol not in blocked_sells
        }
        requested_executable_buys = {
            symbol: change
            for symbol, change in requested_buys.items()
            if change > 1e-14 and symbol not in blocked_buys
        }
        sells = sum(executable_sells.values())
        sell_cost = sells * sell_cost_rate
        buy_capacity = max(cash_weight + sells - sell_cost, 0.0) / (1 + buy_cost_rate)
        requested_buy_total = sum(requested_executable_buys.values())
        buy_scale = min(1.0, buy_capacity / requested_buy_total) if requested_buy_total > 0 else 0.0
        executable_buys = {symbol: change * buy_scale for symbol, change in requested_executable_buys.items()}
        buys = sum(executable_buys.values())
        transaction_cost = sell_cost + buys * buy_cost_rate
        next_remaining_nav = 1 - transaction_cost
        if next_remaining_nav <= 0:
            raise ValueError("交易成本超过组合净值")
        plan = {
            "sells": executable_sells,
            "buys": executable_buys,
            "blocked_sells": blocked_sells,
            "blocked_buys": blocked_buys,
            "transaction_cost": transaction_cost,
        }
        if abs(next_remaining_nav - remaining_nav) <= 1e-14:
            break
        remaining_nav = next_remaining_nav
    else:
        raise ValueError("成本后目标权重求解未收敛")
    if plan is None:
        raise AssertionError("调仓计划未生成")
    return plan


def _validate_open_trade_calendar(
    prices: pd.DataFrame,
    targets: pd.DataFrame,
    open_trade_dates: Iterable[object],
) -> pd.DatetimeIndex:
    raw_dates = list(open_trade_dates)
    parsed = pd.DatetimeIndex(pd.to_datetime(raw_dates, errors="coerce"))
    if parsed.isna().any() or parsed.empty:
        raise ValueError("官方开市交易日历不能为空或包含无效日期")
    calendar = parsed.drop_duplicates().sort_values()
    price_dates = pd.DatetimeIndex(prices["trade_date"].drop_duplicates())
    outside = price_dates.difference(calendar)
    if not outside.empty:
        sample = ", ".join(value.date().isoformat() for value in outside[:5])
        raise ValueError(f"价格面板包含非官方开市日：{sample}")
    missing = calendar.difference(price_dates)
    if not missing.empty:
        sample = ", ".join(value.date().isoformat() for value in missing[:5])
        raise ValueError(f"官方开市日价格面板整日缺失：{sample}")
    invalid_signals = pd.DatetimeIndex(targets["signal_date"].drop_duplicates()).difference(calendar)
    if not invalid_signals.empty:
        sample = ", ".join(value.date().isoformat() for value in invalid_signals[:5])
        raise ValueError(f"信号日不是官方开市日：{sample}")
    return calendar


def _validate_prices(prices: pd.DataFrame) -> None:
    required = {
        "ts_code",
        "trade_date",
        "adj_open",
        "adj_close",
        "is_buyable_at_open",
        "is_sellable_at_open",
    }
    missing = sorted(required - set(prices.columns))
    if missing:
        raise ValueError(f"价格数据缺少字段：{', '.join(missing)}")
    if prices.empty:
        raise ValueError("价格数据为空")
    if prices.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("价格数据存在重复的 ts_code + trade_date")
    if "is_valuation_carried" in prices.columns:
        evidence_columns = {"valuation_carry_reason", "is_suspended", "is_suspended_at_open"}
        missing_evidence = sorted(evidence_columns - set(prices.columns))
        if missing_evidence:
            raise ValueError("估值沿用缺少 full_day_suspension 证据")
        carried = prices["is_valuation_carried"].fillna(False).astype(bool)
        reasons = prices["valuation_carry_reason"].fillna("").astype(str)
        suspended = prices["is_suspended"].fillna(False).astype(bool)
        suspended_at_open = prices["is_suspended_at_open"].fillna(False).astype(bool)
        invalid = carried & ((reasons != "full_day_suspension") | ~suspended | ~suspended_at_open)
        orphan_reason = ~carried & reasons.ne("")
        if invalid.any() or orphan_reason.any():
            raise ValueError("估值沿用必须有 full_day_suspension 证据")


def _validate_targets(targets: pd.DataFrame) -> None:
    required = {"signal_date", "available_date", "ts_code", "target_weight"}
    missing = sorted(required - set(targets.columns))
    if missing:
        raise ValueError(f"目标权重缺少字段：{', '.join(missing)}")
    if targets.duplicated(["signal_date", "ts_code"]).any():
        raise ValueError("目标权重存在重复的 signal_date + ts_code")
    numeric = pd.to_numeric(targets["target_weight"], errors="coerce")
    if numeric.isna().any() or (numeric < 0).any():
        raise ValueError("目标权重必须是非负数")
    totals = targets.assign(target_weight=numeric).groupby("signal_date")["target_weight"].sum()
    if (totals > 1 + 1e-9).any():
        raise ValueError("同一信号日目标权重之和不能超过 1")
    available_dates = pd.to_datetime(targets["available_date"], errors="coerce")
    signal_dates = pd.to_datetime(targets["signal_date"], errors="coerce")
    if available_dates.isna().any():
        raise ValueError("正式目标权重的 available_date 不能为空")
    if signal_dates.isna().any():
        raise ValueError("目标权重的 signal_date 无效")
    if (available_dates > signal_dates).any():
        raise ValueError("目标权重使用了信号日之后才可得的 available_date")


def _require_held_prices(
    weights: dict[str, float],
    previous_prices: dict[str, float],
    current_prices: dict[str, float],
    trade_date: pd.Timestamp,
    stage: str,
) -> None:
    missing = [
        symbol
        for symbol in weights
        if symbol not in previous_prices
        or symbol not in current_prices
        or pd.isna(previous_prices[symbol])
        or pd.isna(current_prices[symbol])
        or previous_prices[symbol] <= 0
        or current_prices[symbol] <= 0
    ]
    if missing:
        raise ValueError(f"{trade_date.date()} {stage}缺少持仓价格：{', '.join(sorted(missing)[:10])}")
