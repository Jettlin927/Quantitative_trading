from __future__ import annotations

from dataclasses import dataclass
from math import floor
from pathlib import Path
from typing import Any

import pandas as pd

from .a_share_price_baseline import (
    _load_and_validate_membership,
    _load_frozen_prices,
)
from .baselines import load_frozen_calendar, open_strategy_inputs, summarize_etf_metrics
from .calendar import OpenTradeCalendar, validate_open_trade_calendar
from .portfolio import SimulationResult


SOURCE_URL = (
    "https://touzikexue.com/strategy-backtest/"
    "20260516_162608_b1_v2_2025-01-01_to_2026-05-15_top2_min0p0i100p0o"
)
FEATURE_PARAMETERS = {
    "bbiWindows": [14, 28, 57, 114],
    "doubleEmaSpan": 10,
    "kdjJThreshold": "13",
    "kdjWindow": 9,
    "marketGate": "benchmark_close_above_bbi",
    "scorePriceBufferWeight": "50",
    "scorePullbackWeight": "20",
    "scoreTrendWeight": "100",
}
TARGET_PARAMETERS = {
    "allocation": "equal_cash",
    "dailyBuyCap": "1",
    "maxSinglePosition": "0.5",
    "selectionFrequency": "daily",
    "topN": 2,
}
EXIT_PARAMETERS = {
    "heavyVolumeMaxLookback": 4,
    "heavyVolumeMeanLookback": 20,
    "longTrend": "bbi",
    "shortTrend": "double_ema_10",
    "t3MinimumGain": "0.02",
    "t3TradingDays": 3,
    "takeProfitFraction": "0.3333333333333333",
    "takeProfitStep": "0.10",
}
IDEAL_EXECUTION = {
    "allowFractional": True,
    "calendarExchange": "SSE",
    "enforceTPlusOne": True,
    "executionPrice": "signal_close_ideal",
    "lotSize": None,
    "signalPrice": "close",
}
REALISTIC_EXECUTION = {
    "allowFractional": False,
    "calendarExchange": "SSE",
    "enforceTPlusOne": True,
    "executionPrice": "next_trade_open",
    "lotSize": 100,
    "signalPrice": "close",
}
ALLOWED_REALISTIC_COSTS = {
    ("0.00035", "0.00085", "0.001"),
    ("0.00070", "0.00170", "0.002"),
}
B1_LIMITATIONS = (
    "research_only",
    "not_investment_advice",
    "public_rule_approximation_not_exact_source_replica",
    "source_all_market_universe_unavailable_using_largest_pit_industry_sample",
    "source_factor_definitions_and_active_wave_algorithm_unavailable",
    "source_params_conflict_with_public_t3_trade_records",
    "proxy_score_fixed_without_parameter_search",
    "historical_st_status_unavailable",
    "daily_data_only",
    "next_trade_open_execution_for_primary_result",
    "lot_rounding_uses_adjusted_return_units_and_is_approximate_across_corporate_actions",
    "aggregated_costs_without_adv_impact_model",
    "no_broker_or_live_trading_side_effects",
)


@dataclass
class _Position:
    units: float
    entry_adjusted_price: float
    entry_date: pd.Timestamp
    held_closes: int = 0
    crossed_short_trend: bool = False
    next_take_profit_level: int = 1


@dataclass(frozen=True)
class _SellOrder:
    fraction: float
    take_profit_levels: int = 0


def validate_a_share_b1_config(config: dict[str, Any]) -> None:
    if config.get("strategyId") != "a_share_b1_trend_pullback":
        raise ValueError("B1 趋势回调策略 ID 必须是 a_share_b1_trend_pullback")
    if config.get("scope") != "a_share_cross_section":
        raise ValueError("B1 趋势回调只允许 a_share_cross_section scope")
    universe = config.get("universe") or {}
    if (
        universe.get("mode") != "industry_membership"
        or universe.get("source") != "industry_members"
        or not universe.get("sourceKey")
    ):
        raise ValueError("B1 趋势回调必须使用 point-in-time industry_membership")
    if config.get("sourceReference") != SOURCE_URL:
        raise ValueError("B1 趋势回调必须绑定已登记的公开来源")
    if config.get("initialCapital") != "100000":
        raise ValueError("B1 趋势回调初始本金固定为 100000 元")
    if config.get("featureParameters") != FEATURE_PARAMETERS:
        raise ValueError("B1 趋势回调 BBI/EMA/KDJ 与代理分数参数必须保持事前固定")
    if config.get("targetWeightParameters") != TARGET_PARAMETERS:
        raise ValueError("B1 趋势回调 Top2 与仓位参数必须保持事前固定")

    exits = config.get("exitParameters") or {}
    if set(exits) != set(EXIT_PARAMETERS) | {"t3WeakEnabled"}:
        raise ValueError("B1 趋势回调卖出参数字段无效")
    if any(exits.get(key) != value for key, value in EXIT_PARAMETERS.items()):
        raise ValueError("B1 趋势回调卖出参数必须保持事前固定")
    if not isinstance(exits.get("t3WeakEnabled"), bool):
        raise ValueError("B1 趋势回调 t3WeakEnabled 必须是布尔值")

    execution = config.get("executionPolicy")
    costs = config.get("costModel") or {}
    cost_identity = tuple(costs.get(key) for key in ("buyRate", "sellRate", "slippageRate"))
    if execution == IDEAL_EXECUTION:
        if cost_identity != ("0", "0", "0"):
            raise ValueError("网页机械口径成交只允许零成本")
    elif execution == REALISTIC_EXECUTION:
        if cost_identity not in ALLOWED_REALISTIC_COSTS:
            raise ValueError("现实成交只允许事前登记的基础或双倍成本")
    else:
        raise ValueError("B1 趋势回调成交政策不在事前登记范围")


def calculate_b1_feature_frame(
    bars: pd.DataFrame,
    config: dict[str, Any],
) -> pd.DataFrame:
    validate_a_share_b1_config(config)
    required = {
        "trade_date",
        "ts_code",
        "adj_high",
        "adj_low",
        "adj_close",
        "vol",
    }
    missing = sorted(required - set(bars.columns))
    if missing:
        raise ValueError(f"B1 冻结行情缺少字段：{', '.join(missing)}")
    frame = bars.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    if frame.empty or frame.duplicated(["trade_date", "ts_code"]).any():
        raise ValueError("B1 冻结行情为空或自然键重复")
    for column in ("adj_high", "adj_low", "adj_close", "vol"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.sort_values(["ts_code", "trade_date"], kind="stable").reset_index(drop=True)
    carried = frame.get("is_valuation_carried", pd.Series(False, index=frame.index)).fillna(False).astype(bool)
    for column in ("adj_high", "adj_low", "adj_close"):
        filled = frame.groupby("ts_code", sort=False)[column].ffill()
        frame.loc[carried, column] = filled[carried]
    frame.loc[carried & frame["vol"].isna(), "vol"] = 0.0
    missing_market_data = frame[["adj_high", "adj_low", "adj_close", "vol"]].isna().any(axis=1)
    if (missing_market_data & ~carried).any():
        raise ValueError("B1 行情存在无停牌沿用证据的缺失价格或成交量")

    parts: list[pd.DataFrame] = []
    windows = tuple(int(value) for value in FEATURE_PARAMETERS["bbiWindows"])
    span = int(FEATURE_PARAMETERS["doubleEmaSpan"])
    kdj_window = int(FEATURE_PARAMETERS["kdjWindow"])
    threshold = float(FEATURE_PARAMETERS["kdjJThreshold"])
    for _symbol, group in frame.groupby("ts_code", sort=False):
        item = group.copy()
        close = item["adj_close"]
        averages = pd.concat(
            [close.rolling(window, min_periods=window).mean() for window in windows],
            axis=1,
        )
        item["bbi"] = averages.mean(axis=1, skipna=False)
        first_ema = close.ewm(span=span, adjust=False, min_periods=span).mean()
        item["double_ema_10"] = first_ema.ewm(
            span=span,
            adjust=False,
            min_periods=span,
        ).mean()
        low_min = item["adj_low"].rolling(kdj_window, min_periods=kdj_window).min()
        high_max = item["adj_high"].rolling(kdj_window, min_periods=kdj_window).max()
        denominator = (high_max - low_min).replace(0, pd.NA)
        rsv = ((close - low_min) / denominator * 100.0).clip(lower=0.0, upper=100.0)
        item["kdj_k"] = rsv.ewm(alpha=1 / 3, adjust=False, min_periods=1).mean()
        item["kdj_d"] = item["kdj_k"].ewm(alpha=1 / 3, adjust=False, min_periods=1).mean()
        item["kdj_j"] = 3.0 * item["kdj_k"] - 2.0 * item["kdj_d"]
        trend_strength = item["double_ema_10"] / item["bbi"] - 1.0
        pullback_depth = (threshold - item["kdj_j"]).clip(lower=0.0) / threshold
        price_buffer = close / item["bbi"] - 1.0
        item["b1_proxy_score"] = (
            trend_strength * float(FEATURE_PARAMETERS["scoreTrendWeight"])
            + pullback_depth * float(FEATURE_PARAMETERS["scorePullbackWeight"])
            + price_buffer * float(FEATURE_PARAMETERS["scorePriceBufferWeight"])
        )
        item["entry_signal"] = (
            (close > item["bbi"])
            & (item["double_ema_10"] > item["bbi"])
            & (item["kdj_j"] < threshold)
        ).fillna(False)
        previous_volume = item["vol"].shift(1)
        previous_close = close.shift(1)
        item["bearish_heavy_volume"] = (
            (close < previous_close)
            & (
                item["vol"]
                > previous_volume.rolling(
                    int(EXIT_PARAMETERS["heavyVolumeMaxLookback"]),
                    min_periods=int(EXIT_PARAMETERS["heavyVolumeMaxLookback"]),
                ).max()
            )
            & (
                item["vol"]
                > previous_volume.rolling(
                    int(EXIT_PARAMETERS["heavyVolumeMeanLookback"]),
                    min_periods=int(EXIT_PARAMETERS["heavyVolumeMeanLookback"]),
                ).mean()
            )
        ).fillna(False)
        parts.append(item)
    return pd.concat(parts, ignore_index=True).sort_values(
        ["trade_date", "ts_code"], kind="stable"
    ).reset_index(drop=True)


def build_a_share_b1_targets(
    input_root: Path,
    config: dict[str, Any],
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> pd.DataFrame:
    validate_a_share_b1_config(config)
    root, reader = open_strategy_inputs(input_root, compressed, table_artifacts)
    calendar = load_frozen_calendar(root, reader, config, compressed, table_artifacts)
    universe = _load_and_validate_membership(reader, config, calendar)
    prices = _load_frozen_prices(reader, config, calendar)
    features = calculate_b1_feature_frame(prices, config)
    market = _load_market_features(reader, config)
    start = pd.Timestamp(config["startDate"])
    end = pd.Timestamp(config["endDate"])
    candidates = features[
        features["trade_date"].between(start, end) & features["entry_signal"]
    ].merge(
        universe,
        on=["trade_date", "ts_code"],
        how="inner",
        validate="one_to_one",
    ).merge(
        market[["trade_date", "market_allows_entry"]],
        on="trade_date",
        how="left",
        validate="many_to_one",
    )
    candidates = candidates[candidates["market_allows_entry"].fillna(False)].copy()
    candidates = candidates.sort_values(
        ["trade_date", "b1_proxy_score", "ts_code"],
        ascending=[True, False, True],
        kind="stable",
    )
    selected = candidates.groupby("trade_date", sort=True).head(
        int(config["targetWeightParameters"]["topN"])
    )
    if selected.empty:
        raise ValueError("B1 研究周期没有满足固定规则的候选")
    targets = selected[["trade_date", "ts_code"]].rename(
        columns={"trade_date": "signal_date"}
    )
    targets["available_date"] = targets["signal_date"]
    targets["target_weight"] = float(
        config["targetWeightParameters"]["maxSinglePosition"]
    )
    return targets[
        ["signal_date", "available_date", "ts_code", "target_weight"]
    ].sort_values(["signal_date", "ts_code"], kind="stable").reset_index(drop=True)


def simulate_a_share_b1_targets(
    input_root: Path,
    config: dict[str, Any],
    targets: pd.DataFrame,
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> tuple[SimulationResult, OpenTradeCalendar]:
    validate_a_share_b1_config(config)
    root, reader = open_strategy_inputs(input_root, compressed, table_artifacts)
    calendar = load_frozen_calendar(root, reader, config, compressed, table_artifacts)
    _load_and_validate_membership(reader, config, calendar)
    prices = _load_frozen_prices(reader, config, calendar)
    features = calculate_b1_feature_frame(prices, config)
    market = _load_market_features(reader, config)
    return (
        simulate_b1_portfolio(prices, features, market, calendar, targets, config),
        calendar,
    )


def simulate_b1_portfolio(
    prices: pd.DataFrame,
    features: pd.DataFrame,
    market: pd.DataFrame,
    calendar: OpenTradeCalendar,
    targets: pd.DataFrame,
    config: dict[str, Any],
) -> SimulationResult:
    validate_a_share_b1_config(config)
    trade_dates = validate_open_trade_calendar(calendar)
    start = pd.Timestamp(config["startDate"])
    end = pd.Timestamp(config["endDate"])
    trade_dates = trade_dates[(trade_dates >= start) & (trade_dates <= end)]
    if trade_dates.empty:
        raise ValueError("B1 研究周期没有开市日")
    price_frame = _prepare_execution_prices(prices)
    feature_frame = features.copy()
    feature_frame["trade_date"] = pd.to_datetime(feature_frame["trade_date"], errors="raise")
    feature_frame["ts_code"] = feature_frame["ts_code"].astype(str).str.upper()
    price_index = price_frame.set_index(["trade_date", "ts_code"]).sort_index()
    feature_index = feature_frame.set_index(["trade_date", "ts_code"]).sort_index()
    market_frame = market.copy()
    market_frame["trade_date"] = pd.to_datetime(market_frame["trade_date"], errors="raise")
    market_gate = market_frame.set_index("trade_date")["market_allows_entry"].astype(bool).to_dict()
    schedule = _target_schedule(targets, trade_dates)
    initial_capital = float(config["initialCapital"])
    cash = initial_capital
    positions: dict[str, _Position] = {}
    pending_signal: pd.Timestamp | None = None
    pending_sells: dict[str, _SellOrder] = {}
    pending_buys: list[tuple[str, float]] = []
    nav_rows: list[dict[str, object]] = []
    request_rows: list[dict[str, object]] = []
    execution_rows: list[dict[str, object]] = []
    position_rows: list[dict[str, object]] = []
    ideal = config["executionPolicy"]["executionPrice"] == "signal_close_ideal"

    for trade_date in trade_dates:
        day_prices = _day_slice(price_index, trade_date)
        day_features = _day_slice(feature_index, trade_date)
        daily = _empty_execution_day()
        if ideal:
            _update_position_state(positions, day_features, trade_date)
            sell_orders = _build_sell_orders(
                positions,
                day_features,
                bool(market_gate.get(trade_date, False)),
                config,
            )
            buys = schedule.get(trade_date, [])
            cash, daily = _execute_orders(
                cash,
                positions,
                day_prices,
                trade_date,
                trade_date,
                sell_orders,
                buys,
                config,
                request_rows,
                execution_rows,
            )
            _initialize_new_position_state(positions, day_features, trade_date)
        else:
            if pending_signal is not None:
                cash, daily = _execute_orders(
                    cash,
                    positions,
                    day_prices,
                    trade_date,
                    pending_signal,
                    pending_sells,
                    pending_buys,
                    config,
                    request_rows,
                    execution_rows,
                )
            _update_position_state(positions, day_features, trade_date)
            pending_signal = trade_date
            pending_sells = _build_sell_orders(
                positions,
                day_features,
                bool(market_gate.get(trade_date, False)),
                config,
            )
            pending_buys = schedule.get(trade_date, [])

        close_equity, carried_count = _portfolio_equity(
            cash,
            positions,
            day_prices,
            "close",
            trade_date,
        )
        if close_equity <= 0:
            raise ValueError("B1 组合净值无效")
        close_weights = {
            symbol: position.units * float(day_prices.loc[symbol, "adj_close"]) / close_equity
            for symbol, position in positions.items()
        }
        cash_weight = cash / close_equity
        if abs(cash_weight + sum(close_weights.values()) - 1.0) > 1e-9:
            raise ValueError("B1 每日现金与持仓权重不闭合")
        nav_rows.append(
            {
                "trade_date": trade_date,
                "nav": close_equity / initial_capital,
                "cash_weight": cash_weight,
                "gross_exposure": sum(close_weights.values()),
                "executed_signal_date": daily["signal_date"],
                "traded_weight": daily["gross_buys"] + daily["gross_sells"],
                "one_way_turnover": max(daily["gross_buys"], daily["gross_sells"]),
                "transaction_cost_rate": daily["transaction_cost_rate"],
                "blocked_buys": ",".join(daily["blocked_buys"]),
                "blocked_sells": ",".join(daily["blocked_sells"]),
                "unfilled_target_weight": daily["unfilled_target_weight"],
                "carried_valuation_count": carried_count,
            }
        )
        position_rows.extend(
            {
                "trade_date": trade_date,
                "ts_code": symbol,
                "close_weight": weight,
            }
            for symbol, weight in sorted(close_weights.items())
            if weight > 1e-12
        )

    return SimulationResult(
        nav=pd.DataFrame(nav_rows),
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


def summarize_a_share_b1_metrics(
    input_root: Path,
    config: dict[str, Any],
    nav: pd.DataFrame,
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    validate_a_share_b1_config(config)
    metrics = summarize_etf_metrics(
        input_root,
        config,
        nav,
        compressed=compressed,
        table_artifacts=table_artifacts,
        include_extended=True,
    )
    metrics.update(
        {
            "initialCapital": float(config["initialCapital"]),
            "terminalCapital": float(config["initialCapital"]) * float(nav.iloc[-1]["nav"]),
            "sourceReplication": "approximate",
            "t3WeakEnabled": bool(config["exitParameters"]["t3WeakEnabled"]),
        }
    )
    return metrics


def a_share_b1_limitations() -> list[str]:
    return list(B1_LIMITATIONS)


def _load_market_features(reader: Any, config: dict[str, Any]) -> pd.DataFrame:
    bars = reader("index_daily_bars")
    bars["trade_date"] = pd.to_datetime(bars["trade_date"], errors="raise")
    bars = bars[
        bars["ts_code"].eq(config["benchmark"])
        & bars["trade_date"].between(
            pd.Timestamp(config["warmupStart"]),
            pd.Timestamp(config["endDate"]),
        )
    ][["trade_date", "close"]].copy()
    bars["close"] = pd.to_numeric(bars["close"], errors="coerce")
    if bars.empty or bars["close"].isna().any() or bars["trade_date"].duplicated().any():
        raise ValueError("B1 沪深300市场门输入缺失或重复")
    bars = bars.sort_values("trade_date", kind="stable").reset_index(drop=True)
    averages = pd.concat(
        [
            bars["close"].rolling(window, min_periods=window).mean()
            for window in FEATURE_PARAMETERS["bbiWindows"]
        ],
        axis=1,
    )
    bars["market_bbi"] = averages.mean(axis=1, skipna=False)
    bars["market_allows_entry"] = (bars["close"] > bars["market_bbi"]).fillna(False)
    return bars[["trade_date", "market_bbi", "market_allows_entry"]]


def _prepare_execution_prices(prices: pd.DataFrame) -> pd.DataFrame:
    required = {
        "trade_date",
        "ts_code",
        "open",
        "close",
        "adj_open",
        "adj_close",
        "is_buyable_at_open",
        "is_sellable_at_open",
    }
    missing = sorted(required - set(prices.columns))
    if missing:
        raise ValueError(f"B1 执行行情缺少字段：{', '.join(missing)}")
    frame = prices.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    frame = frame.sort_values(["ts_code", "trade_date"], kind="stable").reset_index(drop=True)
    carried = frame.get("is_valuation_carried", pd.Series(False, index=frame.index)).fillna(False).astype(bool)
    for column in ("open", "close", "adj_open", "adj_close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        filled = frame.groupby("ts_code", sort=False)[column].ffill()
        frame.loc[carried, column] = filled[carried]
    if frame[["open", "close", "adj_open", "adj_close"]].isna().any().any():
        raise ValueError("B1 执行行情存在无停牌证据的缺失价格")
    for column in ("is_buyable_at_open", "is_sellable_at_open"):
        frame[column] = frame[column].fillna(False).astype(bool)
    frame["is_valuation_carried"] = carried
    frame["is_suspended_at_open"] = frame.get(
        "is_suspended_at_open", pd.Series(False, index=frame.index)
    ).fillna(False).astype(bool)
    return frame.sort_values(["trade_date", "ts_code"], kind="stable").reset_index(drop=True)


def _target_schedule(
    targets: pd.DataFrame,
    trade_dates: pd.DatetimeIndex,
) -> dict[pd.Timestamp, list[tuple[str, float]]]:
    required = {"signal_date", "available_date", "ts_code", "target_weight"}
    if not required.issubset(targets.columns):
        raise ValueError("B1 目标候选字段不完整")
    frame = targets.copy()
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="raise")
    frame["available_date"] = pd.to_datetime(frame["available_date"], errors="raise")
    frame["ts_code"] = frame["ts_code"].astype(str).str.upper()
    frame["target_weight"] = pd.to_numeric(frame["target_weight"], errors="raise")
    if (frame["available_date"] > frame["signal_date"]).any():
        raise ValueError("B1 候选使用了信号日之后数据")
    valid_dates = set(trade_dates)
    return {
        trade_date: sorted(
            zip(group["ts_code"], group["target_weight"], strict=True),
            key=lambda item: item[0],
        )
        for trade_date, group in frame.groupby("signal_date", sort=True)
        if trade_date in valid_dates
    }


def _execute_orders(
    cash: float,
    positions: dict[str, _Position],
    day: pd.DataFrame,
    execution_date: pd.Timestamp,
    signal_date: pd.Timestamp,
    sells: dict[str, _SellOrder],
    buys: list[tuple[str, float]],
    config: dict[str, Any],
    requests: list[dict[str, object]],
    executions: list[dict[str, object]],
) -> tuple[float, dict[str, Any]]:
    fill_column = "close" if config["executionPolicy"]["executionPrice"] == "signal_close_ideal" else "open"
    enforce_market = fill_column == "open"
    pre_trade_equity, _ = _portfolio_equity(cash, positions, day, fill_column, execution_date)
    if pre_trade_equity <= 0:
        raise ValueError("B1 成交前组合价值无效")
    buy_rate = float(config["costModel"]["buyRate"]) + float(config["costModel"]["slippageRate"])
    sell_rate = float(config["costModel"]["sellRate"]) + float(config["costModel"]["slippageRate"])
    total_cost = 0.0
    gross_buys = 0.0
    gross_sells = 0.0
    blocked_buys: list[str] = []
    blocked_sells: list[str] = []
    unfilled = 0.0
    request_count_before = len(requests)
    sold_symbols: set[str] = set()

    for symbol, order in sorted(sells.items()):
        position = positions.get(symbol)
        if position is None:
            continue
        row = _required_row(day, symbol, execution_date)
        adj_price = float(row[f"adj_{fill_column}"])
        raw_price = float(row[fill_column])
        requested_value = position.units * adj_price * min(max(order.fraction, 0.0), 1.0)
        requested_weight = requested_value / pre_trade_equity
        reason = _market_block_reason(row, "sell") if enforce_market else ""
        executed_value = 0.0
        cost = 0.0
        if not reason and requested_value > 1e-12:
            if order.fraction >= 1 - 1e-12 or config["executionPolicy"]["allowFractional"]:
                units_to_sell = position.units * min(order.fraction, 1.0)
            else:
                raw_equivalent_shares = position.units * adj_price / raw_price
                desired_shares = raw_equivalent_shares * order.fraction
                lot_size = int(config["executionPolicy"]["lotSize"])
                sell_shares = floor(desired_shares / lot_size) * lot_size
                units_to_sell = min(position.units, sell_shares * raw_price / adj_price)
            executed_value = units_to_sell * adj_price
            cost = executed_value * sell_rate
            position.units -= units_to_sell
            cash += executed_value - cost
            if executed_value > 1e-12:
                sold_symbols.add(symbol)
                position.next_take_profit_level += order.take_profit_levels
            if position.units <= 1e-12:
                del positions[symbol]
        blocked_value = max(requested_value - executed_value, 0.0)
        if reason:
            blocked_sells.append(symbol)
        ledger_reason = reason or ("cash_capacity" if blocked_value > 1e-12 else "")
        _append_execution(
            requests,
            executions,
            execution_date,
            signal_date,
            symbol,
            "sell",
            requested_weight,
            executed_value / pre_trade_equity,
            ledger_reason,
            cost / pre_trade_equity,
        )
        gross_sells += executed_value / pre_trade_equity
        total_cost += cost
        unfilled += blocked_value / pre_trade_equity

    candidates = [
        (symbol, weight)
        for symbol, weight in buys
        if symbol not in positions and symbol not in sold_symbols and symbol in day.index
    ]
    if candidates and cash > 1e-12:
        buyable = [
            (symbol, weight)
            for symbol, weight in candidates
            if not enforce_market or not _market_block_reason(day.loc[symbol], "buy")
        ]
        gross_budget = min(
            cash / (1 + buy_rate),
            pre_trade_equity * float(config["targetWeightParameters"]["dailyBuyCap"]),
        )
        budget_per_buyable = gross_budget / len(buyable) if buyable else 0.0
        nominal_per_candidate = gross_budget / len(candidates)
        for symbol, target_weight in candidates:
            row = day.loc[symbol]
            reason = _market_block_reason(row, "buy") if enforce_market else ""
            requested_value = min(
                pre_trade_equity * float(target_weight),
                budget_per_buyable if not reason else nominal_per_candidate,
            )
            requested_weight = requested_value / pre_trade_equity
            executed_value = 0.0
            cost = 0.0
            if not reason and requested_value > 1e-12:
                raw_price = float(row[fill_column])
                adj_price = float(row[f"adj_{fill_column}"])
                if config["executionPolicy"]["allowFractional"]:
                    shares = requested_value / raw_price
                else:
                    lot_size = int(config["executionPolicy"]["lotSize"])
                    shares = floor(requested_value / raw_price / lot_size) * lot_size
                executed_value = min(shares * raw_price, cash / (1 + buy_rate))
                if not config["executionPolicy"]["allowFractional"]:
                    lot_size = int(config["executionPolicy"]["lotSize"])
                    shares = floor(executed_value / raw_price / lot_size) * lot_size
                    executed_value = shares * raw_price
                if executed_value > 1e-12:
                    cost = executed_value * buy_rate
                    cash -= executed_value + cost
                    positions[symbol] = _Position(
                        units=executed_value / adj_price,
                        entry_adjusted_price=adj_price
                        * (1 + float(config["costModel"]["slippageRate"])),
                        entry_date=execution_date,
                    )
            blocked_value = max(requested_value - executed_value, 0.0)
            if reason:
                blocked_buys.append(symbol)
            ledger_reason = reason or ("cash_capacity" if blocked_value > 1e-12 else "")
            _append_execution(
                requests,
                executions,
                execution_date,
                signal_date,
                symbol,
                "buy",
                requested_weight,
                executed_value / pre_trade_equity,
                ledger_reason,
                cost / pre_trade_equity,
            )
            gross_buys += executed_value / pre_trade_equity
            total_cost += cost
            unfilled += blocked_value / pre_trade_equity

    return cash, {
        "signal_date": signal_date if len(requests) > request_count_before else pd.NaT,
        "gross_buys": gross_buys,
        "gross_sells": gross_sells,
        "transaction_cost_rate": total_cost / pre_trade_equity,
        "blocked_buys": sorted(blocked_buys),
        "blocked_sells": sorted(blocked_sells),
        "unfilled_target_weight": unfilled,
    }


def _append_execution(
    requests: list[dict[str, object]],
    executions: list[dict[str, object]],
    execution_date: pd.Timestamp,
    signal_date: pd.Timestamp,
    symbol: str,
    side: str,
    requested: float,
    executed: float,
    reason: str,
    cost: float,
) -> None:
    blocked = max(requested - executed, 0.0)
    if requested <= 1e-14:
        return
    if blocked <= 1e-12:
        blocked = 0.0
        status = "filled"
        reason = ""
    elif executed > 1e-12:
        status = "partial"
        reason = "cash_capacity"
    else:
        status = "blocked"
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
            "transaction_cost_rate": cost,
        }
    )


def _update_position_state(
    positions: dict[str, _Position],
    day_features: pd.DataFrame,
    trade_date: pd.Timestamp,
) -> None:
    for symbol, position in positions.items():
        row = _required_row(day_features, symbol, trade_date)
        position.held_closes += 1
        if float(row["adj_close"]) > float(row["double_ema_10"]):
            position.crossed_short_trend = True


def _initialize_new_position_state(
    positions: dict[str, _Position],
    day_features: pd.DataFrame,
    trade_date: pd.Timestamp,
) -> None:
    for symbol, position in positions.items():
        if position.entry_date != trade_date or position.held_closes:
            continue
        row = _required_row(day_features, symbol, trade_date)
        position.held_closes = 1
        position.crossed_short_trend = bool(
            float(row["adj_close"]) > float(row["double_ema_10"])
        )


def _build_sell_orders(
    positions: dict[str, _Position],
    day_features: pd.DataFrame,
    market_allows_entry: bool,
    config: dict[str, Any],
) -> dict[str, _SellOrder]:
    orders: dict[str, _SellOrder] = {}
    exits = config["exitParameters"]
    for symbol, position in positions.items():
        row = day_features.loc[symbol]
        close = float(row["adj_close"])
        full_exit = (
            close < float(row["bbi"])
            or (
                position.crossed_short_trend
                and close < float(row["double_ema_10"])
            )
            or bool(row["bearish_heavy_volume"])
            or (
                bool(exits["t3WeakEnabled"])
                and market_allows_entry
                and position.held_closes >= int(exits["t3TradingDays"])
                and close / position.entry_adjusted_price - 1
                < float(exits["t3MinimumGain"])
            )
        )
        if full_exit:
            orders[symbol] = _SellOrder(1.0)
            continue
        gain = close / position.entry_adjusted_price - 1
        reached_level = floor(max(gain, 0.0) / float(exits["takeProfitStep"]) + 1e-12)
        if reached_level >= position.next_take_profit_level:
            levels = reached_level - position.next_take_profit_level + 1
            fraction = 1 - (1 - float(exits["takeProfitFraction"])) ** levels
            orders[symbol] = _SellOrder(fraction, levels)
    return orders


def _portfolio_equity(
    cash: float,
    positions: dict[str, _Position],
    day: pd.DataFrame,
    price_column: str,
    trade_date: pd.Timestamp,
) -> tuple[float, int]:
    equity = cash
    carried = 0
    for symbol, position in positions.items():
        row = _required_row(day, symbol, trade_date)
        equity += position.units * float(row[f"adj_{price_column}"])
        carried += int(bool(row.get("is_valuation_carried", False)))
    return equity, carried


def _market_block_reason(row: pd.Series, side: str) -> str:
    if bool(row.get("is_valuation_carried", False)) or bool(
        row.get("is_suspended_at_open", False)
    ):
        return "valuation_carried" if bool(row.get("is_valuation_carried", False)) else "suspended_at_open"
    allowed = bool(row["is_buyable_at_open"] if side == "buy" else row["is_sellable_at_open"])
    if allowed:
        return ""
    return "limit_up" if side == "buy" else "limit_down"


def _day_slice(frame: pd.DataFrame, trade_date: pd.Timestamp) -> pd.DataFrame:
    try:
        day = frame.loc[trade_date]
    except KeyError:
        return pd.DataFrame()
    if isinstance(day, pd.Series):
        day = day.to_frame().T
    return day


def _required_row(day: pd.DataFrame, symbol: str, trade_date: pd.Timestamp) -> pd.Series:
    if day.empty or symbol not in day.index:
        raise ValueError(f"{trade_date.date()} B1 持仓缺少价格或特征：{symbol}")
    row = day.loc[symbol]
    if isinstance(row, pd.DataFrame):
        raise ValueError(f"{trade_date.date()} B1 行情自然键重复：{symbol}")
    return row


def _empty_execution_day() -> dict[str, Any]:
    return {
        "signal_date": pd.NaT,
        "gross_buys": 0.0,
        "gross_sells": 0.0,
        "transaction_cost_rate": 0.0,
        "blocked_buys": [],
        "blocked_sells": [],
        "unfilled_target_weight": 0.0,
    }
