from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from numbers import Integral, Real

import pandas as pd

from .calendar import OpenTradeCalendar, validate_open_trade_calendar


@dataclass(frozen=True)
class CostModel:
    buy_rate: float = 0.00035
    sell_rate: float = 0.00085
    slippage_rate: float = 0.001

    def __post_init__(self) -> None:
        if min(self.buy_rate, self.sell_rate, self.slippage_rate) < 0:
            raise ValueError("交易成本参数不能为负数")


@dataclass(frozen=True)
class SimulationResult:
    nav: pd.DataFrame
    rebalance_requests: pd.DataFrame
    rebalance_executions: pd.DataFrame
    positions: pd.DataFrame


def simulate_target_weights(
    prices: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    trade_calendar: OpenTradeCalendar,
    initial_nav: float = 1.0,
    cost: CostModel | None = None,
) -> pd.DataFrame:
    return simulate_target_weights_with_ledger(
        prices,
        targets,
        trade_calendar=trade_calendar,
        initial_nav=initial_nav,
        cost=cost,
    ).nav


def simulate_target_weights_with_ledger(
    prices: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    trade_calendar: OpenTradeCalendar,
    initial_nav: float = 1.0,
    cost: CostModel | None = None,
) -> SimulationResult:
    """Simulate full target portfolios that execute at the next trade-date open.

    This is a research return-space simulator. Missing prices remain hard data
    errors, while market constraints keep affected positions frozen and record
    the unfilled target instead of inventing fills.
    """
    if initial_nav <= 0:
        raise ValueError("initial_nav 必须大于 0")

    price_frame = _normalize_prices(prices)
    target_frame = _normalize_targets(targets)
    price_frame = price_frame.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    trade_dates = list(_validate_open_trade_calendar(price_frame, target_frame, trade_calendar))
    schedule = _schedule_targets(target_frame, trade_dates)
    cost_model = cost or CostModel()

    nav = float(initial_nav)
    weights: dict[str, float] = {}
    cash_weight = 1.0
    previous_close: dict[str, float] = {}
    rows: list[dict[str, object]] = []
    request_rows: list[dict[str, object]] = []
    execution_rows: list[dict[str, object]] = []
    position_rows: list[dict[str, object]] = []

    for trade_date in trade_dates:
        day = price_frame[price_frame["trade_date"] == trade_date].set_index("ts_code")
        open_prices = pd.to_numeric(day["adj_open"], errors="coerce").to_dict()
        close_prices = pd.to_numeric(day["adj_close"], errors="coerce").to_dict()
        buyable = day["is_buyable_at_open"].to_dict()
        sellable = day["is_sellable_at_open"].to_dict()
        carried = day.get("is_valuation_carried", pd.Series(False, index=day.index)).to_dict()
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
            _append_rebalance_ledger(
                request_rows,
                execution_rows,
                execution_date=trade_date,
                signal_date=executed_signal_date,
                plan=plan,
                day=day,
                cost=cost_model,
            )
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
        position_rows.extend(
            {
                "trade_date": trade_date,
                "ts_code": symbol,
                "close_weight": float(weight),
            }
            for symbol, weight in sorted(weights.items())
            if weight > 1e-12
        )
        previous_close = close_prices

    return SimulationResult(
        nav=pd.DataFrame(rows),
        rebalance_requests=pd.DataFrame(
            request_rows,
            columns=["execution_date", "signal_date", "ts_code", "requested_change", "side"],
        ).sort_values(["execution_date", "ts_code"], kind="stable").reset_index(drop=True),
        rebalance_executions=pd.DataFrame(
            execution_rows,
            columns=[
                "execution_date",
                "signal_date",
                "ts_code",
                "requested_change",
                "executed_change",
                "blocked_change",
                "status",
                "reason",
                "transaction_cost_rate",
            ],
        ).sort_values(["execution_date", "ts_code"], kind="stable").reset_index(drop=True),
        positions=pd.DataFrame(
            position_rows,
            columns=["trade_date", "ts_code", "close_weight"],
        ).sort_values(["trade_date", "ts_code"], kind="stable").reset_index(drop=True),
    )


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
            "requested_sells": requested_sells,
            "requested_buys": requested_buys,
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


def _append_rebalance_ledger(
    requests: list[dict[str, object]],
    executions: list[dict[str, object]],
    *,
    execution_date: pd.Timestamp,
    signal_date: pd.Timestamp,
    plan: dict[str, object],
    day: pd.DataFrame,
    cost: CostModel,
) -> None:
    requested_by_side = {
        "buy": plan["requested_buys"],
        "sell": plan["requested_sells"],
    }
    executed_by_side = {
        "buy": plan["buys"],
        "sell": plan["sells"],
    }
    rates = {
        "buy": cost.buy_rate + cost.slippage_rate,
        "sell": cost.sell_rate + cost.slippage_rate,
    }
    for side in ("buy", "sell"):
        requested_changes = requested_by_side[side]
        executed_changes = executed_by_side[side]
        if not isinstance(requested_changes, dict) or not isinstance(executed_changes, dict):
            raise AssertionError("调仓账本输入无效")
        for symbol in sorted(requested_changes):
            requested = float(requested_changes[symbol])
            if requested <= 1e-14:
                continue
            executed = min(float(executed_changes.get(symbol, 0.0)), requested)
            blocked = max(requested - executed, 0.0)
            if blocked <= 1e-12:
                blocked = 0.0
                status = "filled"
                reason = ""
            elif executed > 1e-12:
                status = "partial"
                reason = _blocked_reason(day, symbol, side, market_blocked=False)
            else:
                status = "blocked"
                market_blocked = symbol in plan[f"blocked_{side}s"]
                reason = _blocked_reason(day, symbol, side, market_blocked=market_blocked)
            requests.append(
                {
                    "execution_date": execution_date,
                    "signal_date": signal_date,
                    "ts_code": symbol,
                    "requested_change": requested,
                    "side": side,
                }
            )
            executions.append(
                {
                    "execution_date": execution_date,
                    "signal_date": signal_date,
                    "ts_code": symbol,
                    "requested_change": requested,
                    "executed_change": executed,
                    "blocked_change": blocked,
                    "status": status,
                    "reason": reason,
                    "transaction_cost_rate": executed * rates[side],
                }
            )


def _blocked_reason(
    day: pd.DataFrame,
    symbol: str,
    side: str,
    *,
    market_blocked: bool,
) -> str:
    if not market_blocked:
        return "cash_capacity"
    row = day.loc[symbol]
    if "is_valuation_carried" in day.columns and bool(row.get("is_valuation_carried", False)):
        return "valuation_carried"
    if "is_suspended_at_open" in day.columns and bool(row.get("is_suspended_at_open", False)):
        return "suspended_at_open"
    return "limit_up" if side == "buy" else "limit_down"


def _validate_open_trade_calendar(
    prices: pd.DataFrame,
    targets: pd.DataFrame,
    trade_calendar: OpenTradeCalendar,
) -> pd.DatetimeIndex:
    calendar = validate_open_trade_calendar(trade_calendar)
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


def _normalize_prices(prices: pd.DataFrame) -> pd.DataFrame:
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
    frame = prices.copy()
    frame["trade_date"] = _strict_date_series(frame["trade_date"], "价格 trade_date")
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    if frame["ts_code"].eq("").any():
        raise ValueError("价格数据 ts_code 不能为空")
    if frame.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("价格数据存在重复的 ts_code + trade_date")
    for column in ("is_buyable_at_open", "is_sellable_at_open"):
        frame[column] = _strict_bool_series(frame[column], column, allow_null=False)
    if "is_valuation_carried" in frame.columns:
        evidence_columns = {"valuation_carry_reason", "is_suspended", "is_suspended_at_open"}
        missing_evidence = sorted(evidence_columns - set(frame.columns))
        if missing_evidence:
            raise ValueError("估值沿用缺少 full_day_suspension 证据")
        carried = _strict_bool_series(frame["is_valuation_carried"], "is_valuation_carried", allow_null=True)
        reasons = frame["valuation_carry_reason"].fillna("").astype(str)
        suspended = _strict_bool_series(frame["is_suspended"], "is_suspended", allow_null=True)
        suspended_at_open = _strict_bool_series(frame["is_suspended_at_open"], "is_suspended_at_open", allow_null=True)
        invalid = carried & ((reasons != "full_day_suspension") | ~suspended | ~suspended_at_open)
        orphan_reason = ~carried & reasons.ne("")
        if invalid.any() or orphan_reason.any():
            raise ValueError("估值沿用必须有 full_day_suspension 证据")
        frame["is_valuation_carried"] = carried
        frame["is_suspended"] = suspended
        frame["is_suspended_at_open"] = suspended_at_open
    return frame


def _normalize_targets(targets: pd.DataFrame) -> pd.DataFrame:
    required = {"signal_date", "available_date", "ts_code", "target_weight"}
    missing = sorted(required - set(targets.columns))
    if missing:
        raise ValueError(f"目标权重缺少字段：{', '.join(missing)}")
    frame = targets.copy()
    frame["signal_date"] = _strict_date_series(frame["signal_date"], "signal_date")
    frame["available_date"] = _strict_date_series(frame["available_date"], "available_date")
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    if frame["ts_code"].eq("").any():
        raise ValueError("目标权重 ts_code 不能为空")
    if frame.duplicated(["signal_date", "ts_code"]).any():
        raise ValueError("目标权重存在重复的 signal_date + ts_code")
    numeric = pd.to_numeric(frame["target_weight"], errors="coerce")
    if numeric.isna().any() or (numeric < 0).any():
        raise ValueError("目标权重必须是非负数")
    frame["target_weight"] = numeric
    totals = frame.groupby("signal_date")["target_weight"].sum()
    if (totals > 1 + 1e-9).any():
        raise ValueError("同一信号日目标权重之和不能超过 1")
    if (frame["available_date"] > frame["signal_date"]).any():
        raise ValueError("目标权重使用了信号日之后才可得的 available_date")
    return frame


def _strict_date_series(values: pd.Series, label: str) -> pd.Series:
    normalized: list[pd.Timestamp] = []
    for value in values:
        if value is None or pd.isna(value) or isinstance(value, bool) or isinstance(value, Real):
            raise ValueError(f"{label} 必须是非空 YYYY-MM-DD 日期")
        if isinstance(value, datetime):
            parsed = value.date()
        elif isinstance(value, date):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = date.fromisoformat(value.strip())
            except ValueError as exc:
                raise ValueError(f"{label} 必须是 YYYY-MM-DD 日期") from exc
        else:
            raise ValueError(f"{label} 必须是 YYYY-MM-DD 日期")
        normalized.append(pd.Timestamp(parsed))
    return pd.Series(normalized, index=values.index, dtype="datetime64[ns]")


def _strict_bool_series(values: pd.Series, label: str, *, allow_null: bool) -> pd.Series:
    normalized: list[bool] = []
    for value in values:
        if value is None or pd.isna(value):
            if allow_null:
                normalized.append(False)
                continue
            raise ValueError(f"{label} 不能为空")
        if isinstance(value, bool) or type(value).__name__ == "bool_":
            normalized.append(bool(value))
            continue
        if isinstance(value, Integral) and not isinstance(value, bool) and value in {0, 1}:
            normalized.append(bool(value))
            continue
        raise ValueError(f"{label} 必须是 bool 或 0/1，禁止字符串真值")
    return pd.Series(normalized, index=values.index, dtype=bool)


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
