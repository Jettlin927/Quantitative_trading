from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from math import ceil, floor
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .a_share_price_baseline import _load_frozen_prices
from .baselines import load_frozen_calendar, open_strategy_inputs
from .calendar import OpenTradeCalendar, validate_open_trade_calendar
from .dataset import attach_fundamentals_asof
from .metrics import summarize_execution_metrics, summarize_performance
from .portfolio import SimulationResult
from .universe import build_historical_industry_level_panel


FEATURE_PARAMETERS = {
    "valuationFactors": ["pe_ttm", "pb"],
    "qualityFactors": ["roe", "netprofit_margin", "debt_to_assets"],
    "valueWeight": "0.5",
    "qualityWeight": "0.5",
    "industryStrengthWindow": 120,
    "industryStrengthTopFraction": "0.5",
    "minimumListingOpenDays": 252,
    "financialAvailability": "available_from_lte_signal_date",
    "financialPeriodPolicy": "latest_end_date",
    "missingValuePolicy": "exclude_without_zero_fill",
}
TARGET_PARAMETERS = {
    "rebalanceEveryOpenDays": 60,
    "targetCount": 20,
    "entryRank": 20,
    "exitRank": 30,
    "allocation": "equal_weight",
    "singleNameCap": "0.05",
    "industryCap": "0.20",
    "maxOneWayTurnover": "0.25",
    "insufficientCandidates": "hold_cash",
}
EXECUTION_POLICY = {
    "allowFractional": False,
    "calendarExchange": "SSE",
    "enforceTPlusOne": True,
    "executionPrice": "next_trade_open",
    "lotSize": 100,
    "signalPrice": "close",
}
LIQUIDITY_POLICY = {
    "advWindows": [20, 60],
    "marketAmountScale": "1000",
    "maxParticipationRate": "0.05",
    "missingCapacityPolicy": "blocked",
}
TRIAL_REGISTRY = [
    {
        "id": "value_quality_baseline",
        "industryStrengthGate": False,
    },
    {
        "id": "value_quality_industry_strength",
        "industryStrengthGate": True,
    },
]
ALLOWED_VARIANTS = {item["id"] for item in TRIAL_REGISTRY}
ALLOWED_COSTS = {
    ("0.00035", "0.00085", "0.001"),
    ("0.00070", "0.00170", "0.002"),
}
LIMITATIONS = (
    "research_only",
    "not_investment_advice",
    "historical_financial_revisions_before_observation_remain_unverified",
    "no_historical_st_status_filter",
    "industry_strength_is_an_entry_gate_not_an_investable_benchmark",
    "fixed_two_trial_registry_without_dynamic_parameter_search",
    "next_trade_open_daily_bar_execution",
    "no_broker_or_live_trading_side_effects",
)
TARGET_ARTIFACT_COLUMNS = (
    "signal_date",
    "available_date",
    "ts_code",
    "target_weight",
    "industry_index_code",
    "listing_open_days",
    "industry_return_120",
    "industry_strength_rank",
    "industry_strength_eligible",
    "value_quality_score",
    "total_rank",
)
REQUEST_ARTIFACT_COLUMNS = (
    "execution_date",
    "signal_date",
    "ts_code",
    "requested_change",
    "side",
    "requested_order_amount",
    "adv20_amount",
    "adv60_amount",
    "requested_participation_rate_20",
    "requested_participation_rate_60",
)
EXECUTION_ARTIFACT_COLUMNS = (
    "execution_date",
    "signal_date",
    "ts_code",
    "requested_change",
    "executed_change",
    "blocked_change",
    "status",
    "reason",
    "transaction_cost_rate",
    "requested_order_amount",
    "executed_order_amount",
    "executed_shares",
    "adv20_amount",
    "adv60_amount",
    "requested_participation_rate_20",
    "requested_participation_rate_60",
    "participation_rate_20",
    "participation_rate_60",
    "commission_cost",
    "sell_tax_cost",
    "slippage_cost",
    "t_plus_one_enforced",
)


@dataclass
class _ValueHolding:
    adjusted_units: float


def validate_a_share_value_quality_config(config: dict[str, Any]) -> None:
    if config.get("strategyId") != "a_share_value_quality_industry_strength":
        raise ValueError("价值质量策略 ID 必须是 a_share_value_quality_industry_strength")
    if config.get("scope") != "a_share_cross_section":
        raise ValueError("价值质量策略只允许 a_share_cross_section scope")
    if config.get("benchmark") != "H00985.CSI":
        raise ValueError("价值质量策略主基准必须是 H00985.CSI 中证全指全收益")
    if config.get("environmentBenchmark") != "000985.CSI":
        raise ValueError("价值质量策略市场环境参考必须是 000985.CSI")
    if config.get("listingHistoryStart") != "2010-01-04":
        raise ValueError("价值质量策略上市开市日计数边界必须固定为 2010-01-04")
    universe = config.get("universe") or {}
    if universe != {
        "mode": "industry_level_membership",
        "source": "industry_classifications+industry_members",
        "classificationSource": "SW2021",
        "classificationLevel": "L1",
    }:
        raise ValueError("价值质量策略必须使用 SW2021/L1 历史行业 universe")
    if config.get("variantId") not in ALLOWED_VARIANTS:
        raise ValueError("价值质量策略 variantId 未在两个事前版本中登记")
    if config.get("trialRegistry") != TRIAL_REGISTRY:
        raise ValueError("价值质量策略试验登记必须固定为对照版与行业强度主版本")
    if config.get("costStressMultipliers") != ["1", "2"]:
        raise ValueError("价值质量策略成本压力必须固定登记为 1 倍与 2 倍")
    if config.get("featureParameters") != FEATURE_PARAMETERS:
        raise ValueError("价值质量策略行业强度窗口与价值质量参数必须保持事前固定")
    if config.get("targetWeightParameters") != TARGET_PARAMETERS:
        raise ValueError("价值质量策略调仓间隔、排名缓冲与组合约束必须保持事前固定")
    if config.get("executionPolicy") != EXECUTION_POLICY:
        raise ValueError("价值质量策略成交政策必须固定为次日开盘、100 股整手与 T+1")
    if config.get("initialCapital") != "10000000":
        raise ValueError("价值质量策略示例目标资金必须固定为 10000000 元")
    if config.get("liquidityPolicy") != LIQUIDITY_POLICY:
        raise ValueError("价值质量策略 ADV 窗口与参与率上限必须保持事前固定")
    costs = config.get("costModel") or {}
    cost_identity = tuple(costs.get(key) for key in ("buyRate", "sellRate", "slippageRate"))
    if cost_identity not in ALLOWED_COSTS:
        raise ValueError("价值质量策略成本必须是事前登记的基础或双倍成本")


def rebalance_signal_dates(
    open_dates: Iterable[object],
    start: object,
    end: object,
    *,
    every: int,
) -> pd.DatetimeIndex:
    if isinstance(every, bool) or not isinstance(every, int) or every <= 0:
        raise ValueError("调仓间隔必须是正整数开市日")
    dates = pd.DatetimeIndex(pd.to_datetime(list(open_dates), errors="raise")).sort_values()
    if dates.empty or dates.has_duplicates:
        raise ValueError("调仓交易日历不能为空或重复")
    eligible = dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
    if eligible.empty:
        raise ValueError("研究区间没有可用调仓日")
    return pd.DatetimeIndex(eligible[::every])


def attach_value_quality_fundamentals(
    signal_panel: pd.DataFrame,
    daily_basic: pd.DataFrame,
    financials: pd.DataFrame,
    *,
    trade_calendar: OpenTradeCalendar,
) -> pd.DataFrame:
    signal_required = {"signal_date", "ts_code", "industry_index_code"}
    valuation_required = {"trade_date", "ts_code", "pe_ttm", "pb"}
    missing_signal = sorted(signal_required - set(signal_panel.columns))
    missing_valuation = sorted(valuation_required - set(daily_basic.columns))
    if missing_signal:
        raise ValueError("价值质量信号面板缺少字段：" + ", ".join(missing_signal))
    if missing_valuation:
        raise ValueError("每日估值缺少字段：" + ", ".join(missing_valuation))
    panel = signal_panel.copy()
    valuations = daily_basic.copy()
    panel["signal_date"] = pd.to_datetime(panel["signal_date"], errors="raise")
    valuations["trade_date"] = pd.to_datetime(valuations["trade_date"], errors="raise")
    for frame in (panel, valuations):
        frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    valuations = valuations.loc[:, ["trade_date", "ts_code", "pe_ttm", "pb"]]
    if valuations.duplicated(["trade_date", "ts_code"]).any():
        raise ValueError("每日估值存在重复自然键")
    merged = panel.merge(
        valuations,
        left_on=["signal_date", "ts_code"],
        right_on=["trade_date", "ts_code"],
        how="left",
        validate="one_to_one",
    ).drop(columns=["trade_date"])
    attached = attach_fundamentals_asof(
        merged.rename(columns={"signal_date": "trade_date"}),
        financials,
        trade_calendar=trade_calendar,
        period_policy="latest_end_date",
    ).rename(columns={"trade_date": "signal_date"})
    return attached.sort_values(["signal_date", "ts_code"], kind="stable").reset_index(drop=True)


def calculate_industry_strength(
    prices: pd.DataFrame,
    universe: pd.DataFrame,
    *,
    window: int,
    top_fraction: float,
) -> pd.DataFrame:
    price_required = {"trade_date", "ts_code", "adj_close"}
    universe_required = {"trade_date", "ts_code", "industry_index_code"}
    missing_prices = sorted(price_required - set(prices.columns))
    missing_universe = sorted(universe_required - set(universe.columns))
    if missing_prices:
        raise ValueError("行业强度价格缺少字段：" + ", ".join(missing_prices))
    if missing_universe:
        raise ValueError("行业强度 universe 缺少字段：" + ", ".join(missing_universe))
    if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
        raise ValueError("行业强度窗口必须是正整数")
    if not 0 < float(top_fraction) <= 1:
        raise ValueError("行业强度准入比例必须在 (0, 1] 内")

    price_frame = prices.loc[:, ["trade_date", "ts_code", "adj_close"]].copy()
    universe_frame = universe.loc[:, ["trade_date", "ts_code", "industry_index_code"]].copy()
    for frame in (price_frame, universe_frame):
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
        frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    universe_frame["industry_index_code"] = (
        universe_frame["industry_index_code"].astype(str).str.strip().str.upper()
    )
    if price_frame.duplicated(["trade_date", "ts_code"]).any():
        raise ValueError("行业强度价格存在重复自然键")
    if universe_frame.duplicated(["trade_date", "ts_code"]).any():
        raise ValueError("行业强度 universe 存在重复自然键")
    price_frame["adj_close"] = pd.to_numeric(price_frame["adj_close"], errors="coerce")
    if price_frame["adj_close"].isna().any() or (price_frame["adj_close"] <= 0).any():
        raise ValueError("行业强度价格必须是有限正数")
    price_frame = price_frame.sort_values(["ts_code", "trade_date"], kind="stable")
    price_frame["stock_return"] = price_frame.groupby("ts_code", sort=False)[
        "adj_close"
    ].pct_change(fill_method=None)
    daily = price_frame.merge(
        universe_frame,
        on=["trade_date", "ts_code"],
        how="inner",
        validate="one_to_one",
    )
    daily = (
        daily.groupby(["trade_date", "industry_index_code"], as_index=False)["stock_return"]
        .mean()
        .rename(columns={"stock_return": "industry_daily_return"})
        .sort_values(["industry_index_code", "trade_date"], kind="stable")
    )
    daily["industry_return_120"] = (
        daily.groupby("industry_index_code", sort=False)["industry_daily_return"]
        .rolling(window=window, min_periods=window)
        .apply(lambda values: (1.0 + values).prod() - 1.0, raw=True)
        .reset_index(level=0, drop=True)
    )
    complete = daily.dropna(subset=["industry_return_120"]).copy()
    ranked_parts: list[pd.DataFrame] = []
    for trade_date, group in complete.groupby("trade_date", sort=True):
        ranked = group.sort_values(
            ["industry_return_120", "industry_index_code"],
            ascending=[False, True],
            kind="stable",
        ).copy()
        ranked["industry_strength_rank"] = range(1, len(ranked) + 1)
        ranked["industry_strength_eligible"] = (
            ranked["industry_strength_rank"] <= ceil(len(ranked) * float(top_fraction))
        )
        ranked_parts.append(ranked)
    columns = [
        "trade_date",
        "industry_index_code",
        "industry_daily_return",
        "industry_return_120",
        "industry_strength_rank",
        "industry_strength_eligible",
    ]
    if not ranked_parts:
        return pd.DataFrame(columns=columns)
    return (
        pd.concat(ranked_parts, ignore_index=True)
        .loc[:, columns]
        .sort_values(["trade_date", "industry_strength_rank", "industry_index_code"], kind="stable")
        .reset_index(drop=True)
    )


def score_value_quality_candidates(candidates: pd.DataFrame) -> pd.DataFrame:
    factor_columns = ["pe_ttm", "pb", "roe", "netprofit_margin", "debt_to_assets"]
    required = {"signal_date", "ts_code", "industry_index_code", *factor_columns}
    missing = sorted(required - set(candidates.columns))
    if missing:
        raise ValueError("价值质量候选缺少字段：" + ", ".join(missing))
    frame = candidates.copy()
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="raise")
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    frame["industry_index_code"] = frame["industry_index_code"].astype(str).str.strip().str.upper()
    for column in factor_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame[(frame["pe_ttm"] > 0) & (frame["pb"] > 0)].dropna(
        subset=factor_columns
    )
    if frame.empty:
        return frame.assign(
            pe_score=pd.Series(dtype=float),
            pb_score=pd.Series(dtype=float),
            roe_score=pd.Series(dtype=float),
            margin_score=pd.Series(dtype=float),
            debt_score=pd.Series(dtype=float),
            value_score=pd.Series(dtype=float),
            quality_score=pd.Series(dtype=float),
            value_quality_score=pd.Series(dtype=float),
            total_rank=pd.Series(dtype=int),
        )
    groups = frame.groupby(["signal_date", "industry_index_code"], sort=False)
    frame["pe_score"] = groups["pe_ttm"].rank(method="average", ascending=False, pct=True)
    frame["pb_score"] = groups["pb"].rank(method="average", ascending=False, pct=True)
    frame["roe_score"] = groups["roe"].rank(method="average", ascending=True, pct=True)
    frame["margin_score"] = groups["netprofit_margin"].rank(method="average", ascending=True, pct=True)
    frame["debt_score"] = groups["debt_to_assets"].rank(method="average", ascending=False, pct=True)
    frame["value_score"] = (frame["pe_score"] + frame["pb_score"]) / 2.0
    frame["quality_score"] = (
        frame["roe_score"] + frame["margin_score"] + frame["debt_score"]
    ) / 3.0
    frame["value_quality_score"] = (frame["value_score"] + frame["quality_score"]) / 2.0
    frame = frame.sort_values(
        ["signal_date", "value_quality_score", "ts_code"],
        ascending=[True, False, True],
        kind="stable",
    )
    frame["total_rank"] = frame.groupby("signal_date", sort=False).cumcount() + 1
    return frame.reset_index(drop=True)


def select_buffered_targets(
    ranked: pd.DataFrame,
    *,
    previous_symbols: set[str],
    target_count: int,
    entry_rank: int,
    exit_rank: int,
    single_name_cap: float,
    industry_cap: float,
) -> pd.DataFrame:
    required = {
        "signal_date",
        "ts_code",
        "industry_index_code",
        "total_rank",
        "value_quality_score",
    }
    missing = sorted(required - set(ranked.columns))
    if missing:
        raise ValueError("排名缓冲候选缺少字段：" + ", ".join(missing))
    if not 0 < single_name_cap <= industry_cap <= 1:
        raise ValueError("单票与行业上限无效")
    maximum_per_industry = floor((industry_cap + 1e-12) / single_name_cap)
    if maximum_per_industry <= 0:
        raise ValueError("行业上限不足以容纳单票权重")
    ordered = ranked.sort_values(["total_rank", "ts_code"], kind="stable").copy()
    selected_indices: list[int] = []
    industry_counts: dict[str, int] = {}

    def add_rows(rows: pd.DataFrame) -> None:
        for index, row in rows.iterrows():
            if len(selected_indices) >= target_count:
                return
            industry = str(row["industry_index_code"])
            if industry_counts.get(industry, 0) >= maximum_per_industry:
                continue
            selected_indices.append(index)
            industry_counts[industry] = industry_counts.get(industry, 0) + 1

    retained = ordered[
        ordered["ts_code"].isin({str(value).upper() for value in previous_symbols})
        & (ordered["total_rank"] <= exit_rank)
    ]
    add_rows(retained)
    entries = ordered[
        (ordered["total_rank"] <= entry_rank)
        & ~ordered.index.isin(selected_indices)
    ]
    add_rows(entries)
    selected = ordered.loc[selected_indices].copy()
    selected["available_date"] = selected["signal_date"]
    selected["target_weight"] = float(single_name_cap)
    return selected.sort_values(["signal_date", "ts_code"], kind="stable").reset_index(drop=True)


def build_value_quality_target_frame(
    calendar: OpenTradeCalendar,
    universe: pd.DataFrame,
    listings: pd.DataFrame,
    prices: pd.DataFrame,
    daily_basic: pd.DataFrame,
    financials: pd.DataFrame,
    config: dict[str, Any],
) -> pd.DataFrame:
    validate_a_share_value_quality_config(config)
    open_dates = validate_open_trade_calendar(calendar)
    universe_required = {"trade_date", "ts_code", "industry_index_code"}
    listing_required = {"ts_code", "list_date", "delist_date"}
    missing_universe = sorted(universe_required - set(universe.columns))
    missing_listings = sorted(listing_required - set(listings.columns))
    if missing_universe:
        raise ValueError("价值质量 universe 缺少字段：" + ", ".join(missing_universe))
    if missing_listings:
        raise ValueError("价值质量上市边界缺少字段：" + ", ".join(missing_listings))
    universe_frame = universe.loc[:, sorted(universe_required)].copy()
    listing_frame = listings.loc[:, sorted(listing_required)].copy()
    universe_frame["trade_date"] = pd.to_datetime(universe_frame["trade_date"], errors="raise")
    universe_frame["ts_code"] = universe_frame["ts_code"].astype(str).str.strip().str.upper()
    universe_frame["industry_index_code"] = (
        universe_frame["industry_index_code"].astype(str).str.strip().str.upper()
    )
    listing_frame["ts_code"] = listing_frame["ts_code"].astype(str).str.strip().str.upper()
    listing_frame["list_date"] = pd.to_datetime(listing_frame["list_date"], errors="raise")
    listing_frame["delist_date"] = pd.to_datetime(listing_frame["delist_date"], errors="coerce")
    if universe_frame.duplicated(["trade_date", "ts_code"]).any():
        raise ValueError("价值质量 universe 存在重复自然键")
    if listing_frame["ts_code"].duplicated().any():
        raise ValueError("价值质量上市边界存在重复代码")

    parameters = config["targetWeightParameters"]
    feature_parameters = config["featureParameters"]
    signal_dates = rebalance_signal_dates(
        open_dates,
        config["startDate"],
        config["endDate"],
        every=int(parameters["rebalanceEveryOpenDays"]),
    )
    signals = universe_frame[universe_frame["trade_date"].isin(signal_dates)].rename(
        columns={"trade_date": "signal_date"}
    )
    listing_dates = listing_frame.set_index("ts_code")["list_date"].to_dict()
    date_positions = {trade_date: offset for offset, trade_date in enumerate(open_dates)}
    listing_positions = {
        symbol: int(open_dates.searchsorted(list_date, side="left"))
        for symbol, list_date in listing_dates.items()
    }
    missing_listing_codes = sorted(set(signals["ts_code"]) - set(listing_positions))
    if missing_listing_codes:
        raise ValueError("价值质量候选缺少上市日期：" + ", ".join(missing_listing_codes[:10]))
    signals["listing_open_days"] = [
        date_positions[trade_date] - listing_positions[symbol] + 1
        for trade_date, symbol in zip(signals["signal_date"], signals["ts_code"], strict=True)
    ]
    signals = signals[
        signals["listing_open_days"] >= int(feature_parameters["minimumListingOpenDays"])
    ].copy()
    if signals.empty:
        raise ValueError("价值质量策略没有达到最短上市日要求的候选")

    attached = attach_value_quality_fundamentals(
        signals,
        daily_basic,
        financials,
        trade_calendar=calendar,
    )
    strength = calculate_industry_strength(
        prices,
        universe_frame,
        window=int(feature_parameters["industryStrengthWindow"]),
        top_fraction=float(feature_parameters["industryStrengthTopFraction"]),
    ).rename(columns={"trade_date": "signal_date"})
    attached = attached.merge(
        strength,
        on=["signal_date", "industry_index_code"],
        how="left",
        validate="many_to_one",
    )
    if config["variantId"] == "value_quality_industry_strength":
        attached = attached[attached["industry_strength_eligible"].fillna(False)].copy()
    ranked = score_value_quality_candidates(attached)

    target_parts: list[pd.DataFrame] = []
    previous_symbols: set[str] = set()
    for signal_date in signal_dates:
        candidates = ranked[ranked["signal_date"].eq(signal_date)]
        selected = select_buffered_targets(
            candidates,
            previous_symbols=previous_symbols,
            target_count=int(parameters["targetCount"]),
            entry_rank=int(parameters["entryRank"]),
            exit_rank=int(parameters["exitRank"]),
            single_name_cap=float(parameters["singleNameCap"]),
            industry_cap=float(parameters["industryCap"]),
        )
        if selected.empty and previous_symbols:
            selected = pd.DataFrame(
                [
                    {
                        "signal_date": signal_date,
                        "available_date": signal_date,
                        "ts_code": symbol,
                        "industry_index_code": "",
                        "target_weight": 0.0,
                    }
                    for symbol in sorted(previous_symbols)
                ]
            )
        if not selected.empty:
            target_parts.append(selected)
        previous_symbols = set(selected.loc[selected["target_weight"] > 0, "ts_code"])
    if not target_parts:
        columns = [
            "signal_date",
            "available_date",
            "ts_code",
            "target_weight",
            "industry_index_code",
            "listing_open_days",
            "industry_return_120",
            "industry_strength_rank",
            "industry_strength_eligible",
            "value_quality_score",
            "total_rank",
        ]
        return pd.DataFrame(columns=columns)
    targets = pd.concat(target_parts, ignore_index=True)
    targets["variant_id"] = config["variantId"]
    return targets.sort_values(["signal_date", "ts_code"], kind="stable").reset_index(drop=True)


def build_a_share_value_quality_targets(
    input_root: Path,
    config: dict[str, Any],
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> pd.DataFrame:
    validate_a_share_value_quality_config(config)
    root, reader = open_strategy_inputs(input_root, compressed, table_artifacts)
    calendar = load_frozen_calendar(root, reader, config, compressed, table_artifacts)
    universe = _load_and_validate_industry_level_membership(reader, config, calendar)
    listings = reader("stock_listings")
    prices = _load_frozen_prices(reader, config, calendar)
    return build_value_quality_target_frame(
        calendar,
        universe,
        listings,
        prices,
        reader("stock_daily_basic"),
        reader("stock_financial_indicators"),
        config,
    )


def _load_and_validate_industry_level_membership(
    reader: Any,
    config: dict[str, Any],
    calendar: OpenTradeCalendar,
) -> pd.DataFrame:
    frozen = reader("universe")
    required = {"trade_date", "ts_code", "industry_index_code"}
    missing = sorted(required - set(frozen.columns))
    if missing:
        raise ValueError("冻结 SW2021/L1 universe 缺少字段：" + ", ".join(missing))
    frozen = frozen.loc[:, ["trade_date", "ts_code", "industry_index_code"]].copy()
    frozen["trade_date"] = pd.to_datetime(frozen["trade_date"], errors="raise")
    frozen["ts_code"] = frozen["ts_code"].astype(str).str.strip().str.upper()
    frozen["industry_index_code"] = (
        frozen["industry_index_code"].astype(str).str.strip().str.upper()
    )
    frozen = frozen.sort_values(
        ["trade_date", "ts_code", "industry_index_code"], kind="stable"
    ).reset_index(drop=True)
    calendar_dates = pd.DatetimeIndex(pd.to_datetime(calendar.open_dates))
    universe_dates = calendar_dates[
        (calendar_dates >= pd.Timestamp(config["warmupStart"]))
        & (calendar_dates <= pd.Timestamp(config["endDate"]))
    ]
    expected = build_historical_industry_level_panel(
        reader("industry_classifications"),
        reader("industry_members"),
        reader("stock_listings"),
        universe_dates,
        classification_src=config["universe"]["classificationSource"],
        classification_level=config["universe"]["classificationLevel"],
    )
    if not frozen.equals(expected):
        raise ValueError("冻结 universe 与 SW2021/L1 历史行业边界不一致")
    return frozen


def simulate_value_quality_portfolio(
    prices: pd.DataFrame,
    calendar: OpenTradeCalendar,
    targets: pd.DataFrame,
    config: dict[str, Any],
) -> SimulationResult:
    validate_a_share_value_quality_config(config)
    trade_dates = validate_open_trade_calendar(calendar)
    start = pd.Timestamp(config["startDate"])
    end = pd.Timestamp(config["endDate"])
    trade_dates = trade_dates[(trade_dates >= start) & (trade_dates <= end)]
    if trade_dates.empty:
        raise ValueError("价值质量研究周期没有开市日")
    price_frame = _prepare_value_execution_prices(prices, config)
    price_index = price_frame.set_index(["trade_date", "ts_code"]).sort_index()
    schedule = _value_target_schedule(targets, trade_dates, config)
    initial_capital = float(config["initialCapital"])
    cash = initial_capital
    holdings: dict[str, _ValueHolding] = {}
    request_rows: list[dict[str, object]] = []
    execution_rows: list[dict[str, object]] = []
    nav_rows: list[dict[str, object]] = []
    position_rows: list[dict[str, object]] = []
    commission_rate = float(config["costModel"]["buyRate"])
    sell_tax_rate = max(
        float(config["costModel"]["sellRate"]) - commission_rate,
        0.0,
    )
    slippage_rate = float(config["costModel"]["slippageRate"])
    turnover_cap = float(config["targetWeightParameters"]["maxOneWayTurnover"])
    lot_size = int(config["executionPolicy"]["lotSize"])

    for trade_date in trade_dates:
        day = _value_day_slice(price_index, trade_date)
        execution_cost = 0.0
        gross_buys = 0.0
        gross_sells = 0.0
        blocked_buys: list[str] = []
        blocked_sells: list[str] = []
        executed_signal_date = pd.NaT
        unfilled_target_weight = 0.0

        pre_trade_equity = _value_portfolio_equity(cash, holdings, day, "adj_open", trade_date)
        if trade_date in schedule:
            signal_date, target_weights = schedule[trade_date]
            executed_signal_date = signal_date
            symbols = sorted(set(holdings) | set(target_weights))
            current_values = {
                symbol: (
                    holdings[symbol].adjusted_units
                    * float(_value_required_row(day, symbol, trade_date)["adj_open"])
                )
                if symbol in holdings
                else 0.0
                for symbol in symbols
            }
            desired_values = {
                symbol: pre_trade_equity * float(target_weights.get(symbol, 0.0))
                for symbol in symbols
            }
            requested_sells = {
                symbol: max(current_values[symbol] - desired_values[symbol], 0.0)
                for symbol in symbols
            }
            requested_buys = {
                symbol: max(desired_values[symbol] - current_values[symbol], 0.0)
                for symbol in symbols
            }
            sell_total = sum(requested_sells.values())
            buy_total = sum(requested_buys.values())
            turnover_value_cap = pre_trade_equity * turnover_cap
            sell_scale = min(1.0, turnover_value_cap / sell_total) if sell_total > 0 else 1.0
            buy_scale = min(1.0, turnover_value_cap / buy_total) if buy_total > 0 else 1.0

            for symbol in symbols:
                requested = requested_sells[symbol]
                if requested <= 1e-12:
                    continue
                row = _value_required_row(day, symbol, trade_date)
                capacity = _value_capacity_row(price_index, signal_date, symbol)
                market_reason = _value_market_block_reason(row, "sell")
                executable_budget = requested * sell_scale
                capacity_limit, capacity_reason = _value_capacity_limit(capacity, config)
                limiting_reason = "turnover_cap" if executable_budget < requested - 1e-9 else ""
                if capacity_reason:
                    executable_budget = 0.0
                    limiting_reason = capacity_reason
                elif capacity_limit < executable_budget - 1e-9:
                    executable_budget = capacity_limit
                    limiting_reason = "adv_capacity"
                if market_reason:
                    executable_budget = 0.0
                    limiting_reason = market_reason
                raw_price = float(row["open"])
                adjusted_price = float(row["adj_open"])
                current_raw_shares = floor(
                    holdings[symbol].adjusted_units * adjusted_price / raw_price + 1e-9
                )
                shares = min(
                    floor(executable_budget / raw_price / lot_size) * lot_size,
                    floor(current_raw_shares / lot_size) * lot_size,
                )
                executed = shares * raw_price
                if not limiting_reason and executed < requested - 1e-9:
                    limiting_reason = "lot_size"
                commission = executed * commission_rate
                sell_tax = executed * sell_tax_rate
                slippage = executed * slippage_rate
                if executed > 0:
                    holdings[symbol].adjusted_units -= shares * raw_price / adjusted_price
                    if holdings[symbol].adjusted_units <= 1e-12:
                        del holdings[symbol]
                    cash += executed - commission - sell_tax - slippage
                _append_value_execution(
                    request_rows,
                    execution_rows,
                    execution_date=trade_date,
                    signal_date=signal_date,
                    symbol=symbol,
                    side="sell",
                    requested_amount=requested,
                    executed_amount=executed,
                    executed_shares=shares,
                    pre_trade_equity=pre_trade_equity,
                    reason=limiting_reason,
                    capacity=capacity,
                    commission=commission,
                    sell_tax=sell_tax,
                    slippage=slippage,
                )
                if limiting_reason and executed <= 1e-12:
                    blocked_sells.append(symbol)
                gross_sells += executed
                execution_cost += commission + sell_tax + slippage

            for symbol in symbols:
                requested = requested_buys[symbol]
                if requested <= 1e-12:
                    continue
                row = _value_required_row(day, symbol, trade_date)
                capacity = _value_capacity_row(price_index, signal_date, symbol)
                market_reason = _value_market_block_reason(row, "buy")
                executable_budget = requested * buy_scale
                limiting_reason = "turnover_cap" if executable_budget < requested - 1e-9 else ""
                capacity_limit, capacity_reason = _value_capacity_limit(capacity, config)
                if capacity_reason:
                    executable_budget = 0.0
                    limiting_reason = capacity_reason
                elif capacity_limit < executable_budget - 1e-9:
                    executable_budget = capacity_limit
                    limiting_reason = "adv_capacity"
                cash_limit = cash / (1 + commission_rate + slippage_rate)
                if cash_limit < executable_budget - 1e-9:
                    executable_budget = max(cash_limit, 0.0)
                    limiting_reason = "cash_capacity"
                if market_reason:
                    executable_budget = 0.0
                    limiting_reason = market_reason
                raw_price = float(row["open"])
                adjusted_price = float(row["adj_open"])
                shares = floor(executable_budget / raw_price / lot_size) * lot_size
                executed = shares * raw_price
                if not limiting_reason and executed < requested - 1e-9:
                    limiting_reason = "lot_size"
                commission = executed * commission_rate
                sell_tax = 0.0
                slippage = executed * slippage_rate
                if executed > 0:
                    cash -= executed + commission + slippage
                    holding = holdings.setdefault(symbol, _ValueHolding(adjusted_units=0.0))
                    holding.adjusted_units += shares * raw_price / adjusted_price
                _append_value_execution(
                    request_rows,
                    execution_rows,
                    execution_date=trade_date,
                    signal_date=signal_date,
                    symbol=symbol,
                    side="buy",
                    requested_amount=requested,
                    executed_amount=executed,
                    executed_shares=shares,
                    pre_trade_equity=pre_trade_equity,
                    reason=limiting_reason,
                    capacity=capacity,
                    commission=commission,
                    sell_tax=sell_tax,
                    slippage=slippage,
                )
                if limiting_reason and executed <= 1e-12:
                    blocked_buys.append(symbol)
                gross_buys += executed
                execution_cost += commission + slippage

            actual_open_weights = {
                symbol: (
                    holding.adjusted_units
                    * float(_value_required_row(day, symbol, trade_date)["adj_open"])
                    / pre_trade_equity
                )
                for symbol, holding in holdings.items()
            }
            unfilled_target_weight = sum(
                abs(actual_open_weights.get(symbol, 0.0) - target_weights.get(symbol, 0.0))
                for symbol in set(actual_open_weights) | set(target_weights)
            )

        close_equity = _value_portfolio_equity(cash, holdings, day, "adj_close", trade_date)
        if close_equity <= 0:
            raise ValueError("价值质量组合净值无效")
        close_weights = {
            symbol: (
                holding.adjusted_units
                * float(_value_required_row(day, symbol, trade_date)["adj_close"])
                / close_equity
            )
            for symbol, holding in holdings.items()
        }
        nav_rows.append(
            {
                "trade_date": trade_date,
                "nav": close_equity / initial_capital,
                "cash_weight": cash / close_equity,
                "gross_exposure": sum(close_weights.values()),
                "executed_signal_date": executed_signal_date,
                "traded_weight": (gross_buys + gross_sells) / pre_trade_equity,
                "one_way_turnover": max(gross_buys, gross_sells) / pre_trade_equity,
                "transaction_cost_rate": execution_cost / pre_trade_equity,
                "blocked_buys": ",".join(sorted(blocked_buys)),
                "blocked_sells": ",".join(sorted(blocked_sells)),
                "unfilled_target_weight": unfilled_target_weight,
                "carried_valuation_count": int(
                    sum(bool(row) for row in day.get("is_valuation_carried", pd.Series(dtype=bool)))
                ),
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

    request_columns = [
        "execution_date",
        "signal_date",
        "ts_code",
        "requested_change",
        "side",
        "requested_order_amount",
        "adv20_amount",
        "adv60_amount",
        "requested_participation_rate_20",
        "requested_participation_rate_60",
    ]
    execution_columns = [
        "execution_date",
        "signal_date",
        "ts_code",
        "requested_change",
        "executed_change",
        "blocked_change",
        "status",
        "reason",
        "transaction_cost_rate",
        "requested_order_amount",
        "executed_order_amount",
        "executed_shares",
        "adv20_amount",
        "adv60_amount",
        "requested_participation_rate_20",
        "requested_participation_rate_60",
        "participation_rate_20",
        "participation_rate_60",
        "commission_cost",
        "sell_tax_cost",
        "slippage_cost",
        "t_plus_one_enforced",
    ]
    return SimulationResult(
        nav=pd.DataFrame(nav_rows),
        rebalance_requests=pd.DataFrame(request_rows, columns=request_columns).sort_values(
            ["execution_date", "ts_code"], kind="stable"
        ).reset_index(drop=True),
        rebalance_executions=pd.DataFrame(execution_rows, columns=execution_columns).sort_values(
            ["execution_date", "ts_code"], kind="stable"
        ).reset_index(drop=True),
        positions=pd.DataFrame(
            position_rows,
            columns=["trade_date", "ts_code", "close_weight"],
        ).sort_values(["trade_date", "ts_code"], kind="stable").reset_index(drop=True),
    )


def _prepare_value_execution_prices(prices: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    required = {
        "trade_date",
        "ts_code",
        "open",
        "close",
        "adj_open",
        "adj_close",
        "amount",
        "is_buyable_at_open",
        "is_sellable_at_open",
    }
    missing = sorted(required - set(prices.columns))
    if missing:
        raise ValueError("价值质量执行行情缺少字段：" + ", ".join(missing))
    frame = prices.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    if frame.duplicated(["trade_date", "ts_code"]).any():
        raise ValueError("价值质量执行行情存在重复自然键")
    frame = frame.sort_values(["ts_code", "trade_date"], kind="stable").reset_index(drop=True)
    carried = frame.get("is_valuation_carried", pd.Series(False, index=frame.index)).fillna(False).astype(bool)
    for column in ("open", "close", "adj_open", "adj_close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        filled = frame.groupby("ts_code", sort=False)[column].ffill()
        frame.loc[carried, column] = filled[carried]
    invalid_prices = frame[["open", "close", "adj_open", "adj_close"]].isna().any(axis=1)
    if (invalid_prices & ~carried).any():
        raise ValueError("价值质量执行行情存在无停牌证据的缺失价格")
    for column in ("is_buyable_at_open", "is_sellable_at_open"):
        frame[column] = frame[column].fillna(False).astype(bool)
    frame["is_suspended_at_open"] = frame.get(
        "is_suspended_at_open", pd.Series(False, index=frame.index)
    ).fillna(False).astype(bool)
    frame["is_valuation_carried"] = carried
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    amount_value = frame["amount"] * float(config["liquidityPolicy"]["marketAmountScale"])
    for window in config["liquidityPolicy"]["advWindows"]:
        frame[f"adv{window}_amount"] = (
            amount_value.groupby(frame["ts_code"], sort=False)
            .rolling(int(window), min_periods=int(window))
            .mean()
            .reset_index(level=0, drop=True)
        )
    return frame.sort_values(["trade_date", "ts_code"], kind="stable").reset_index(drop=True)


def _value_target_schedule(
    targets: pd.DataFrame,
    trade_dates: pd.DatetimeIndex,
    config: dict[str, Any],
) -> dict[pd.Timestamp, tuple[pd.Timestamp, dict[str, float]]]:
    required = {"signal_date", "available_date", "ts_code", "target_weight", "industry_index_code"}
    missing = sorted(required - set(targets.columns))
    if missing:
        raise ValueError("价值质量目标候选缺少字段：" + ", ".join(missing))
    frame = targets.copy()
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="raise")
    frame["available_date"] = pd.to_datetime(frame["available_date"], errors="raise")
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    frame["target_weight"] = pd.to_numeric(frame["target_weight"], errors="raise")
    if frame.duplicated(["signal_date", "ts_code"]).any():
        raise ValueError("价值质量目标存在重复自然键")
    if (frame["available_date"] > frame["signal_date"]).any():
        raise ValueError("价值质量目标使用了信号日之后的数据")
    maximum_weight = float(config["targetWeightParameters"]["singleNameCap"])
    if (frame["target_weight"] < 0).any() or (frame["target_weight"] > maximum_weight + 1e-12).any():
        raise ValueError("价值质量目标违反单票权重上限")
    industry_totals = frame.groupby(["signal_date", "industry_index_code"])["target_weight"].sum()
    if (industry_totals > float(config["targetWeightParameters"]["industryCap"]) + 1e-12).any():
        raise ValueError("价值质量目标违反行业权重上限")
    totals = frame.groupby("signal_date")["target_weight"].sum()
    if (totals > 1 + 1e-12).any():
        raise ValueError("价值质量目标总权重不能超过 1")
    schedule: dict[pd.Timestamp, tuple[pd.Timestamp, dict[str, float]]] = {}
    for signal_date, group in frame.groupby("signal_date", sort=True):
        future = trade_dates[trade_dates > signal_date]
        if future.empty:
            continue
        execution_date = future[0]
        if execution_date in schedule:
            raise ValueError("多个价值质量信号映射到同一执行日")
        schedule[execution_date] = (
            signal_date,
            dict(zip(group["ts_code"], group["target_weight"], strict=True)),
        )
    return schedule


def _value_day_slice(
    price_index: pd.DataFrame,
    trade_date: pd.Timestamp,
) -> pd.DataFrame:
    try:
        day = price_index.xs(trade_date, level="trade_date")
    except KeyError as exc:
        raise ValueError(f"{trade_date.date()} 缺少价值质量执行行情") from exc
    return day


def _value_required_row(
    day: pd.DataFrame,
    symbol: str,
    trade_date: pd.Timestamp,
) -> pd.Series:
    if symbol not in day.index:
        raise ValueError(f"{trade_date.date()} 缺少价值质量标的行情：{symbol}")
    return day.loc[symbol]


def _value_capacity_row(
    price_index: pd.DataFrame,
    signal_date: pd.Timestamp,
    symbol: str,
) -> pd.Series | None:
    try:
        return price_index.loc[(signal_date, symbol)]
    except KeyError:
        return None


def _value_capacity_limit(
    capacity: pd.Series | None,
    config: dict[str, Any],
) -> tuple[float, str]:
    if capacity is None:
        return 0.0, "missing_capacity"
    values = [capacity.get(f"adv{window}_amount") for window in config["liquidityPolicy"]["advWindows"]]
    if any(pd.isna(value) or float(value) <= 0 for value in values):
        return 0.0, "missing_capacity"
    return min(float(value) for value in values) * float(
        config["liquidityPolicy"]["maxParticipationRate"]
    ), ""


def _value_market_block_reason(row: pd.Series, side: str) -> str:
    if bool(row.get("is_valuation_carried", False)):
        return "valuation_carried"
    if bool(row.get("is_suspended_at_open", False)):
        return "suspended_at_open"
    tradable = bool(row["is_buyable_at_open" if side == "buy" else "is_sellable_at_open"])
    if tradable:
        return ""
    return "limit_up" if side == "buy" else "limit_down"


def _value_portfolio_equity(
    cash: float,
    holdings: dict[str, _ValueHolding],
    day: pd.DataFrame,
    price_column: str,
    trade_date: pd.Timestamp,
) -> float:
    return cash + sum(
        holding.adjusted_units
        * float(_value_required_row(day, symbol, trade_date)[price_column])
        for symbol, holding in holdings.items()
    )


def _append_value_execution(
    requests: list[dict[str, object]],
    executions: list[dict[str, object]],
    *,
    execution_date: pd.Timestamp,
    signal_date: pd.Timestamp,
    symbol: str,
    side: str,
    requested_amount: float,
    executed_amount: float,
    executed_shares: int,
    pre_trade_equity: float,
    reason: str,
    capacity: pd.Series | None,
    commission: float,
    sell_tax: float,
    slippage: float,
) -> None:
    requested_change = requested_amount / pre_trade_equity
    executed_change = executed_amount / pre_trade_equity
    blocked_change = max(requested_change - executed_change, 0.0)
    if blocked_change <= 1e-12:
        blocked_change = 0.0
        status = "filled"
        reason = ""
    elif executed_change > 1e-12:
        status = "partial"
    else:
        status = "blocked"
    adv20 = float(capacity["adv20_amount"]) if capacity is not None and pd.notna(capacity.get("adv20_amount")) else float("nan")
    adv60 = float(capacity["adv60_amount"]) if capacity is not None and pd.notna(capacity.get("adv60_amount")) else float("nan")

    def participation(amount: float, adv: float) -> float:
        return amount / adv if adv > 0 else float("nan")

    common = {
        "execution_date": execution_date,
        "signal_date": signal_date,
        "ts_code": symbol,
        "requested_change": requested_change,
        "requested_order_amount": requested_amount,
        "adv20_amount": adv20,
        "adv60_amount": adv60,
        "requested_participation_rate_20": participation(requested_amount, adv20),
        "requested_participation_rate_60": participation(requested_amount, adv60),
    }
    requests.append({**common, "side": side})
    executions.append(
        {
            **common,
            "executed_change": executed_change,
            "blocked_change": blocked_change,
            "status": status,
            "reason": reason,
            "transaction_cost_rate": (commission + sell_tax + slippage) / pre_trade_equity,
            "executed_order_amount": executed_amount,
            "executed_shares": int(executed_shares),
            "participation_rate_20": participation(executed_amount, adv20),
            "participation_rate_60": participation(executed_amount, adv60),
            "commission_cost": commission,
            "sell_tax_cost": sell_tax,
            "slippage_cost": slippage,
            "t_plus_one_enforced": True,
        }
    )


def summarize_value_quality_artifacts(
    nav: pd.DataFrame,
    requests: pd.DataFrame,
    executions: pd.DataFrame,
    positions: pd.DataFrame,
    config: dict[str, Any],
    *,
    benchmark_nav: pd.DataFrame | None,
    comparison_nav: pd.DataFrame | None,
    environment_nav: pd.DataFrame | None,
    sample_role: str,
) -> dict[str, Any]:
    validate_a_share_value_quality_config(config)
    performance = summarize_performance(
        nav.loc[:, ["trade_date", "nav"]],
        None if benchmark_nav is None else benchmark_nav.loc[:, ["trade_date", "nav"]],
        include_extended=True,
    )
    if benchmark_nav is None:
        benchmark_comparison = {
            "status": "not_available",
            "reason": "缺少 H00985.CSI 总收益基准 canonical 工件",
        }
    else:
        benchmark_comparison = {
            "status": "complete",
            "benchmark": config["benchmark"],
            "benchmarkTotalReturn": performance["benchmarkTotalReturn"],
            "excessTotalReturn": performance["excessTotalReturn"],
            "trackingError": performance["trackingError"],
            "informationRatio": performance["informationRatio"],
        }
    if comparison_nav is None:
        value_quality_comparison = {
            "status": "not_available",
            "reason": "缺少同 universe、同调仓日的价值质量对照版 canonical 工件",
        }
    else:
        comparison = summarize_performance(
            nav.loc[:, ["trade_date", "nav"]],
            comparison_nav.loc[:, ["trade_date", "nav"]],
            include_extended=True,
        )
        value_quality_comparison = {
            "status": "complete",
            "comparisonVariant": "value_quality_baseline",
            "comparisonTotalReturn": comparison["benchmarkTotalReturn"],
            "activeTotalReturn": comparison["excessTotalReturn"],
            "trackingError": comparison["trackingError"],
            "informationRatio": comparison["informationRatio"],
        }
    execution = summarize_execution_metrics(nav, requests, executions, positions)
    capacity = _summarize_value_capacity(executions, config)
    concentration = _summarize_value_concentration(nav, positions)
    market_environment = _summarize_market_environment(nav, environment_nav)
    return {
        "schemaVersion": "a-share-value-quality-metrics/v1",
        "source": "canonical_simulation_artifacts",
        "sampleRole": sample_role,
        "strategyId": config["strategyId"],
        "strategyVersion": config["strategyVersion"],
        "variantId": config["variantId"],
        "performance": performance,
        "benchmarkComparison": benchmark_comparison,
        "valueQualityComparison": value_quality_comparison,
        "execution": execution,
        "capacity": capacity,
        "concentration": concentration,
        "marketEnvironment": market_environment,
    }


def _summarize_value_capacity(
    executions: pd.DataFrame,
    config: dict[str, Any],
) -> dict[str, Any]:
    if executions.empty:
        return {
            "status": "not_available",
            "reason": "canonical OOS 工件没有调仓请求，无法计算容量",
        }
    required = {
        "requested_order_amount",
        "executed_order_amount",
        "requested_participation_rate_20",
        "requested_participation_rate_60",
        "participation_rate_20",
        "participation_rate_60",
    }
    missing = sorted(required - set(executions.columns))
    if missing:
        return {
            "status": "not_available",
            "reason": "canonical OOS 工件缺少容量字段：" + ", ".join(missing),
        }
    frame = executions.copy()
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    participation_columns = [
        "requested_participation_rate_20",
        "requested_participation_rate_60",
        "participation_rate_20",
        "participation_rate_60",
    ]
    if frame[participation_columns].isna().any().any():
        return {
            "status": "not_available",
            "reason": "部分 canonical OOS 订单缺少 20/60 日 ADV 容量证据",
        }
    requested20 = frame["requested_participation_rate_20"]
    requested60 = frame["requested_participation_rate_60"]
    executed20 = frame["participation_rate_20"]
    executed60 = frame["participation_rate_60"]
    maximum = float(config["liquidityPolicy"]["maxParticipationRate"])
    return {
        "status": "complete",
        "targetCapital": config["initialCapital"],
        "advWindows": config["liquidityPolicy"]["advWindows"],
        "maximumAllowedParticipationRate": config["liquidityPolicy"]["maxParticipationRate"],
        "requestCount": int(len(frame)),
        "totalRequestedOrderAmount": float(frame["requested_order_amount"].sum()),
        "totalExecutedOrderAmount": float(frame["executed_order_amount"].sum()),
        "medianRequestedParticipationRate20": float(requested20.median()),
        "p95RequestedParticipationRate20": float(requested20.quantile(0.95)),
        "maxRequestedParticipationRate20": float(requested20.max()),
        "medianRequestedParticipationRate60": float(requested60.median()),
        "p95RequestedParticipationRate60": float(requested60.quantile(0.95)),
        "maxRequestedParticipationRate60": float(requested60.max()),
        "maxExecutedParticipationRate20": float(executed20.max()),
        "maxExecutedParticipationRate60": float(executed60.max()),
        "passed": bool(max(requested20.max(), requested60.max()) <= maximum + 1e-12),
    }


def _summarize_value_concentration(
    nav: pd.DataFrame,
    positions: pd.DataFrame,
) -> dict[str, Any]:
    if positions.empty:
        return {
            "status": "complete",
            "maxWeight": 0.0,
            "maxHhi": 0.0,
            "maxHoldingCount": 0,
            "meanCashWeight": float(pd.to_numeric(nav["cash_weight"], errors="raise").mean()),
        }
    frame = positions.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
    frame["close_weight"] = pd.to_numeric(frame["close_weight"], errors="raise")
    by_date = frame.groupby("trade_date")
    hhi = by_date["close_weight"].apply(lambda values: float((values**2).sum()))
    return {
        "status": "complete",
        "maxWeight": float(frame["close_weight"].max()),
        "maxHhi": float(hhi.max()),
        "maxHoldingCount": int(by_date["ts_code"].nunique().max()),
        "meanCashWeight": float(pd.to_numeric(nav["cash_weight"], errors="raise").mean()),
    }


def _summarize_market_environment(
    nav: pd.DataFrame,
    environment_nav: pd.DataFrame | None,
) -> dict[str, Any]:
    if environment_nav is None:
        return {
            "status": "not_available",
            "reason": "缺少 000985.CSI 价格指数 canonical 市场环境工件",
        }
    strategy = nav.loc[:, ["trade_date", "nav"]].copy()
    environment = environment_nav.loc[:, ["trade_date", "nav"]].copy()
    for frame in (strategy, environment):
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
        frame["nav"] = pd.to_numeric(frame["nav"], errors="raise")
    aligned = strategy.merge(environment, on="trade_date", suffixes=("_strategy", "_environment"))
    if len(aligned) < 2:
        return {
            "status": "not_available",
            "reason": "策略与 000985.CSI 市场环境重叠日期不足",
        }
    strategy_returns = aligned["nav_strategy"].pct_change(fill_method=None)
    environment_returns = aligned["nav_environment"].pct_change(fill_method=None)
    up = strategy_returns[environment_returns >= 0].dropna()
    down = strategy_returns[environment_returns < 0].dropna()
    return {
        "status": "complete",
        "reference": "000985.CSI",
        "upMarketObservations": int(len(up)),
        "downMarketObservations": int(len(down)),
        "meanStrategyReturnUpMarket": float(up.mean()) if not up.empty else None,
        "meanStrategyReturnDownMarket": float(down.mean()) if not down.empty else None,
    }


def simulate_a_share_value_quality_targets(
    input_root: Path,
    config: dict[str, Any],
    targets: pd.DataFrame,
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> tuple[SimulationResult, Any]:
    validate_a_share_value_quality_config(config)
    root, reader = open_strategy_inputs(input_root, compressed, table_artifacts)
    calendar = load_frozen_calendar(root, reader, config, compressed, table_artifacts)
    _load_and_validate_industry_level_membership(reader, config, calendar)
    prices = _load_frozen_prices(reader, config, calendar)
    return simulate_value_quality_portfolio(prices, calendar, targets, config), calendar


def summarize_a_share_value_quality_metrics(
    input_root: Path,
    config: dict[str, Any],
    nav: pd.DataFrame,
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
    simulation: SimulationResult | None = None,
) -> dict[str, Any]:
    validate_a_share_value_quality_config(config)
    if simulation is None:
        return summarize_value_quality_artifacts(
            nav,
            pd.DataFrame(columns=["execution_date", "signal_date", "ts_code", "requested_change", "side"]),
            pd.DataFrame(
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
                ]
            ),
            pd.DataFrame(columns=["trade_date", "ts_code", "close_weight"]),
            config,
            benchmark_nav=None,
            comparison_nav=None,
            environment_nav=None,
            sample_role="full_sample",
        )
    root, reader = open_strategy_inputs(input_root, compressed, table_artifacts)
    calendar = load_frozen_calendar(root, reader, config, compressed, table_artifacts)
    universe = _load_and_validate_industry_level_membership(reader, config, calendar)
    prices = _load_frozen_prices(reader, config, calendar)
    benchmark_nav = _load_value_index_nav(reader, config["benchmark"], config)
    environment_nav = _load_value_index_nav(reader, config["environmentBenchmark"], config)
    if config["variantId"] == "value_quality_baseline":
        comparison_nav = nav.loc[:, ["trade_date", "nav"]].copy()
    else:
        comparison_config = deepcopy(config)
        comparison_config["variantId"] = "value_quality_baseline"
        comparison_targets = build_value_quality_target_frame(
            calendar,
            universe,
            reader("stock_listings"),
            prices,
            reader("stock_daily_basic"),
            reader("stock_financial_indicators"),
            comparison_config,
        )
        comparison_nav = simulate_value_quality_portfolio(
            prices,
            calendar,
            comparison_targets,
            comparison_config,
        ).nav
    return summarize_value_quality_artifacts(
        nav,
        simulation.rebalance_requests,
        simulation.rebalance_executions,
        simulation.positions,
        config,
        benchmark_nav=benchmark_nav,
        comparison_nav=comparison_nav,
        environment_nav=environment_nav,
        sample_role="full_sample",
    )


def _load_value_index_nav(
    reader: Any,
    ts_code: str,
    config: dict[str, Any],
) -> pd.DataFrame | None:
    bars = reader("index_daily_bars")
    required = {"trade_date", "ts_code", "close", "pre_close"}
    if not required.issubset(bars.columns):
        return None
    frame = bars.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    frame = frame[
        frame["ts_code"].eq(str(ts_code).strip().upper())
        & frame["trade_date"].between(
            pd.Timestamp(config["startDate"]),
            pd.Timestamp(config["endDate"]),
        )
    ].sort_values("trade_date", kind="stable")
    if frame.empty or frame["trade_date"].duplicated().any():
        return None
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["pre_close"] = pd.to_numeric(frame["pre_close"], errors="coerce")
    opening = frame.iloc[0]["pre_close"]
    if pd.isna(opening) or float(opening) <= 0 or frame["close"].isna().any():
        return None
    frame["nav"] = frame["close"] / float(opening)
    return frame.loc[:, ["trade_date", "nav"]].reset_index(drop=True)


def a_share_value_quality_limitations() -> list[str]:
    return list(LIMITATIONS)
