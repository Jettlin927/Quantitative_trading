from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .baselines import load_frozen_calendar, open_strategy_inputs, summarize_etf_metrics
from .dataset import build_adjusted_price_panel
from .features import cross_section_percentile_rank, equal_weight_targets, rolling_volatility
from .portfolio import CostModel, SimulationResult, simulate_target_weights_with_ledger
from .universe import build_historical_membership_panel


A_SHARE_PRICE_LIMITATIONS = (
    "research_only",
    "not_investment_advice",
    "single_industry_historical_membership_universe",
    "price_only_cross_section_no_financial_factors",
    "fixed_120_to_20_momentum_and_60_day_volatility",
    "fixed_month_end_rebalance_no_parameter_search",
    "next_trade_open_execution",
    "risk_free_rate_assumed_zero",
    "daily_data_only_no_minute_options_or_live_signals",
)


def validate_a_share_price_config(config: dict[str, Any]) -> None:
    if config.get("strategyId") != "a_share_price_baseline":
        raise ValueError("A 股价格 baseline 策略 ID 必须是 a_share_price_baseline")
    if config.get("scope") != "a_share_cross_section":
        raise ValueError("A 股价格 baseline 只允许 a_share_cross_section scope")
    universe = config.get("universe") or {}
    if universe.get("mode") != "industry_membership":
        raise ValueError("A 股价格 baseline 必须使用 industry_membership")
    if universe.get("source") != "industry_members" or not universe.get("sourceKey"):
        raise ValueError("A 股价格 baseline 必须绑定 industry_members/sourceKey")
    expected_features = {
        "momentumLongWindow": 120,
        "momentumSkipWindow": 20,
        "volatilityWindow": 60,
    }
    if config.get("featureParameters") != expected_features:
        raise ValueError("A 股价格 baseline 只允许固定 120-20 动量与 60 日波动")
    targets = config.get("targetWeightParameters") or {}
    if set(targets) != {"rebalanceFrequency", "topN", "maxWeight"}:
        raise ValueError("A 股价格 baseline targetWeightParameters 字段无效")
    if targets.get("rebalanceFrequency") != "month_end":
        raise ValueError("A 股价格 baseline 只允许月末调仓")
    top_n = targets.get("topN")
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n <= 0:
        raise ValueError("A 股价格 baseline topN 必须是正整数")
    max_weight = float(targets.get("maxWeight"))
    if not 0 < max_weight <= 1 or top_n * max_weight < 1 - 1e-12:
        raise ValueError("A 股价格 baseline maxWeight 与 topN 无法形成满仓等权组合")


def build_a_share_price_targets(
    input_root: Path,
    config: dict[str, Any],
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> pd.DataFrame:
    validate_a_share_price_config(config)
    root, reader = open_strategy_inputs(input_root, compressed, table_artifacts)
    calendar = load_frozen_calendar(root, reader, config, compressed, table_artifacts)
    universe = _load_and_validate_membership(reader, config, calendar)
    prices = _load_frozen_prices(reader, config, calendar)
    features = prices[["trade_date", "ts_code", "adj_close", "is_valuation_carried"]].copy()
    features["adj_close"] = features.groupby("ts_code", sort=False)["adj_close"].ffill()
    features = features.sort_values(["ts_code", "trade_date"], kind="stable").reset_index(drop=True)
    grouped = features.groupby("ts_code", sort=False)["adj_close"]
    features["momentum_120_20"] = grouped.shift(20) / grouped.shift(120) - 1.0
    volatility = rolling_volatility(
        features[["trade_date", "ts_code", "adj_close"]],
        "adj_close",
        window=60,
        min_periods=60,
        output_column="volatility_60",
    )
    features = features.merge(
        volatility,
        on=["trade_date", "ts_code"],
        how="left",
        validate="one_to_one",
    )
    signal_dates = _month_end_signal_dates(
        pd.DatetimeIndex(pd.to_datetime(calendar.open_dates)),
        pd.Timestamp(config["startDate"]),
        pd.Timestamp(config["endDate"]),
    )
    selected = features[features["trade_date"].isin(signal_dates)].merge(
        universe,
        on=["trade_date", "ts_code"],
        how="inner",
        validate="one_to_one",
    )
    selected = selected.sort_values(["trade_date", "ts_code"], kind="stable").reset_index(drop=True)
    momentum_rank = cross_section_percentile_rank(
        selected[["trade_date", "ts_code", "momentum_120_20"]],
        "momentum_120_20",
        ascending=True,
        output_column="momentum_rank",
    )
    volatility_rank = cross_section_percentile_rank(
        selected[["trade_date", "ts_code", "volatility_60"]],
        "volatility_60",
        ascending=False,
        output_column="low_volatility_rank",
    )
    scores = momentum_rank.merge(
        volatility_rank,
        on=["trade_date", "ts_code"],
        how="inner",
        validate="one_to_one",
    )
    scores["score"] = (
        scores["momentum_rank"] + scores["low_volatility_rank"]
    ) / 2.0
    targets = equal_weight_targets(
        scores[["trade_date", "ts_code", "score"]],
        "score",
        top_n=int(config["targetWeightParameters"]["topN"]),
        max_weight=float(config["targetWeightParameters"]["maxWeight"]),
    )
    actual_signal_dates = set(pd.to_datetime(targets["signal_date"]))
    missing_signals = signal_dates.difference(pd.DatetimeIndex(actual_signal_dates))
    if not missing_signals.empty:
        sample = ", ".join(value.date().isoformat() for value in missing_signals[:5])
        raise ValueError(f"A 股价格 baseline 月末没有完整可用特征：{sample}")
    expected_count = int(config["targetWeightParameters"]["topN"])
    incomplete = targets.groupby("signal_date")["ts_code"].size()
    incomplete = incomplete[incomplete != expected_count]
    if not incomplete.empty:
        sample = ", ".join(value.date().isoformat() for value in incomplete.index[:5])
        raise ValueError(f"A 股价格 baseline 月末可用标的不足 topN：{sample}")
    return targets


def simulate_a_share_price_targets(
    input_root: Path,
    config: dict[str, Any],
    targets: pd.DataFrame,
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> tuple[SimulationResult, Any]:
    validate_a_share_price_config(config)
    root, reader = open_strategy_inputs(input_root, compressed, table_artifacts)
    calendar = load_frozen_calendar(root, reader, config, compressed, table_artifacts)
    _load_and_validate_membership(reader, config, calendar)
    prices = _load_frozen_prices(reader, config, calendar)
    cost = config["costModel"]
    simulation = simulate_target_weights_with_ledger(
        prices,
        targets,
        trade_calendar=calendar,
        cost=CostModel(
            buy_rate=float(cost["buyRate"]),
            sell_rate=float(cost["sellRate"]),
            slippage_rate=float(cost["slippageRate"]),
        ),
    )
    return simulation, calendar


def summarize_a_share_price_metrics(
    input_root: Path,
    config: dict[str, Any],
    nav: pd.DataFrame,
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    validate_a_share_price_config(config)
    return summarize_etf_metrics(
        input_root,
        config,
        nav,
        compressed=compressed,
        table_artifacts=table_artifacts,
        include_extended=True,
    )


def a_share_price_limitations() -> list[str]:
    return list(A_SHARE_PRICE_LIMITATIONS)


def _load_and_validate_membership(
    reader: Any,
    config: dict[str, Any],
    calendar: Any,
) -> pd.DataFrame:
    universe = reader("universe")
    required = {"trade_date", "ts_code"}
    if not required.issubset(universe.columns):
        raise ValueError("冻结 A 股 universe 缺少 trade_date 或 ts_code")
    universe = universe[["trade_date", "ts_code"]].copy()
    universe["trade_date"] = pd.to_datetime(universe["trade_date"], errors="raise")
    universe["ts_code"] = universe["ts_code"].astype(str).str.strip().str.upper()
    universe = universe.sort_values(["trade_date", "ts_code"], kind="stable").reset_index(drop=True)
    if universe.empty or universe.duplicated(["trade_date", "ts_code"]).any():
        raise ValueError("冻结 A 股 universe 为空或自然键重复")

    memberships = reader("industry_members")
    listings = reader("stock_listings")
    expected = build_historical_membership_panel(
        memberships,
        listings,
        pd.DatetimeIndex(pd.to_datetime(calendar.open_dates)),
        config["universe"]["sourceKey"],
    )
    if not universe.equals(expected):
        raise ValueError("冻结 universe 与 industry_members/listing 边界不一致")
    return universe


def _load_frozen_prices(reader: Any, config: dict[str, Any], calendar: Any) -> pd.DataFrame:
    bars = reader("stock_daily_bars")
    factors = reader("stock_adjust_factors")
    limits = reader("stock_limit_prices")
    suspensions = reader("stock_suspend_events")
    universe = reader("universe")
    start = pd.Timestamp(config["warmupStart"])
    end = pd.Timestamp(config["endDate"])
    for frame in (bars, factors, limits, suspensions):
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
        frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
        frame.drop(
            frame.index[~frame["trade_date"].between(start, end)],
            inplace=True,
        )
    if bars.empty or factors.empty or limits.empty:
        raise ValueError("冻结 A 股行情、复权或涨跌停输入为空")
    prices = build_adjusted_price_panel(bars, factors)
    limit_frame = limits[["ts_code", "trade_date", "up_limit", "down_limit"]].copy()
    for column in ("up_limit", "down_limit"):
        limit_frame[column] = pd.to_numeric(limit_frame[column], errors="coerce")
    prices = prices.merge(
        limit_frame,
        on=["ts_code", "trade_date"],
        how="left",
        validate="one_to_one",
    )
    universe["trade_date"] = pd.to_datetime(universe["trade_date"], errors="raise")
    universe["ts_code"] = universe["ts_code"].astype(str).str.strip().str.upper()
    first_membership = universe.groupby("ts_code", sort=False)["trade_date"].min()
    required_from = prices["ts_code"].map(first_membership)
    if required_from.isna().any():
        raise ValueError("冻结 A 股行情包含不在历史 universe 的代码")
    missing_limit = prices[["up_limit", "down_limit"]].isna().any(axis=1)
    missing_required_limit = missing_limit & (prices["trade_date"] >= required_from)
    if missing_required_limit.any():
        sample = prices.loc[
            missing_required_limit,
            ["ts_code", "trade_date"],
        ].head(5).to_dict("records")
        raise ValueError(f"冻结 A 股入选后日线缺少涨跌停价格：{sample}")
    prices["has_limit_price"] = ~missing_limit

    suspension_keys = set()
    open_suspension_keys = set()
    full_day_keys = set()
    for row in suspensions.itertuples(index=False):
        if str(row.suspend_type).strip().upper() != "S":
            continue
        key = (str(row.ts_code), row.trade_date)
        suspension_keys.add(key)
        if _suspends_at_open(row.suspend_timing):
            open_suspension_keys.add(key)
        if _is_full_day_suspension(row.suspend_timing):
            full_day_keys.add(key)
    keys = list(zip(prices["ts_code"], prices["trade_date"], strict=True))
    prices["is_suspended"] = [key in suspension_keys for key in keys]
    prices["is_suspended_at_open"] = [key in open_suspension_keys for key in keys]
    prices["is_buyable_at_open"] = (
        prices["has_limit_price"]
        & ~prices["is_suspended_at_open"]
        & (prices["open"] < prices["up_limit"])
    )
    prices["is_sellable_at_open"] = (
        prices["has_limit_price"]
        & ~prices["is_suspended_at_open"]
        & (prices["open"] > prices["down_limit"])
    )
    prices["is_valuation_carried"] = False
    prices["valuation_carry_reason"] = ""

    existing = set(keys)
    carried_rows: list[dict[str, object]] = []
    columns = list(prices.columns)
    for symbol, trade_date in sorted(full_day_keys, key=lambda item: (item[0], item[1])):
        if (symbol, trade_date) in existing:
            continue
        row = {column: float("nan") for column in columns}
        row.update(
            {
                "ts_code": symbol,
                "trade_date": trade_date,
                "is_suspended": True,
                "is_suspended_at_open": True,
                "has_limit_price": False,
                "is_buyable_at_open": False,
                "is_sellable_at_open": False,
                "is_valuation_carried": True,
                "valuation_carry_reason": "full_day_suspension",
            }
        )
        carried_rows.append(row)
    for row in carried_rows:
        prices.loc[len(prices)] = row
    open_dates = set(pd.DatetimeIndex(pd.to_datetime(calendar.open_dates)))
    outside = sorted(set(prices["trade_date"]) - open_dates)
    if outside:
        raise ValueError("冻结 A 股价格包含非官方开市日")
    return prices.sort_values(["trade_date", "ts_code"], kind="stable").reset_index(drop=True)


def _month_end_signal_dates(
    open_dates: pd.DatetimeIndex,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DatetimeIndex:
    candidates = [
        trade_date
        for index, trade_date in enumerate(open_dates[:-1])
        if start <= trade_date < end
        and trade_date.to_period("M") != open_dates[index + 1].to_period("M")
    ]
    if not candidates:
        raise ValueError("A 股价格 baseline 研究区间内没有可执行月末信号")
    return pd.DatetimeIndex(candidates)


def _suspends_at_open(value: object) -> bool:
    normalized = str(value or "").strip().lower()
    return not normalized or any(
        marker in normalized
        for marker in ("全天", "全日", "盘前", "开盘", "09:30", "9:30", "all day", "full day", "pre-open")
    )


def _is_full_day_suspension(value: object) -> bool:
    normalized = str(value or "").strip().lower()
    return not normalized or any(
        marker in normalized
        for marker in ("全天", "全日", "all day", "full day")
    )
