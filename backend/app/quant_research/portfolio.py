from __future__ import annotations

from dataclasses import dataclass

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
    initial_nav: float = 1.0,
    cost: CostModel | None = None,
) -> pd.DataFrame:
    """Simulate full target portfolios that execute at the next trade-date open.

    This is a research return-space simulator. It deliberately rejects missing
    prices instead of inventing fills or silently carrying stale valuations.
    """
    _validate_prices(prices)
    _validate_targets(targets)
    if initial_nav <= 0:
        raise ValueError("initial_nav 必须大于 0")

    price_frame = prices.copy()
    target_frame = targets.copy()
    price_frame["trade_date"] = pd.to_datetime(price_frame["trade_date"])
    target_frame["signal_date"] = pd.to_datetime(target_frame["signal_date"])
    price_frame = price_frame.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    trade_dates = list(price_frame["trade_date"].drop_duplicates().sort_values())
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
        if trade_date in schedule:
            executed_signal_date, target_weights = schedule[trade_date]
            missing_targets = sorted(symbol for symbol in target_weights if symbol not in open_prices or pd.isna(open_prices[symbol]))
            if missing_targets:
                raise ValueError(f"{trade_date.date()} 目标资产缺少开盘价：{', '.join(missing_targets[:10])}")
            symbols = set(weights) | set(target_weights)
            buy_changes = {
                symbol: max(target_weights.get(symbol, 0.0) - weights.get(symbol, 0.0), 0.0) for symbol in symbols
            }
            sell_changes = {
                symbol: max(weights.get(symbol, 0.0) - target_weights.get(symbol, 0.0), 0.0) for symbol in symbols
            }
            blocked_buys = sorted(symbol for symbol, change in buy_changes.items() if change > 0 and not buyable.get(symbol, False))
            blocked_sells = sorted(
                symbol for symbol, change in sell_changes.items() if change > 0 and not sellable.get(symbol, False)
            )
            if blocked_buys or blocked_sells:
                details = []
                if blocked_buys:
                    details.append(f"不可买入={','.join(blocked_buys[:10])}")
                if blocked_sells:
                    details.append(f"不可卖出={','.join(blocked_sells[:10])}")
                raise ValueError(f"{trade_date.date()} 目标组合无法按开盘价执行：{'；'.join(details)}")
            buys = sum(buy_changes.values())
            sells = sum(sell_changes.values())
            transaction_cost = buys * (cost_model.buy_rate + cost_model.slippage_rate) + sells * (
                cost_model.sell_rate + cost_model.slippage_rate
            )
            if transaction_cost >= 1:
                raise ValueError("交易成本超过组合净值")
            nav *= 1 - transaction_cost
            weights = target_weights
            cash_weight = 1 - sum(weights.values())
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
        schedule[future_dates[0]] = (signal_date, target_weights)
    return schedule


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


def _validate_targets(targets: pd.DataFrame) -> None:
    required = {"signal_date", "ts_code", "target_weight"}
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
