from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral
from pathlib import Path
from typing import Any

import pandas as pd

from .artifacts import read_canonical_csv_gz
from .calendar import OpenTradeCalendar, build_open_trade_calendar, trade_calendar_content_sha256
from .dataset import build_adjusted_price_panel
from .metrics import summarize_performance
from .portfolio import CostModel, simulate_target_weights
from .snapshot import verify_materialized_inputs


@dataclass(frozen=True)
class SentinelBaselineResult:
    targets: pd.DataFrame
    nav: pd.DataFrame
    metrics: dict[str, Any]
    limitations: list[str]
    calendar: OpenTradeCalendar


def run_sentinel_etf_baseline(
    input_root: Path,
    config: dict[str, Any],
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> SentinelBaselineResult:
    if config.get("strategyId") != "sentinel_etf_baseline":
        raise ValueError("Phase 3 runner 只允许 sentinel_etf_baseline")
    if config.get("scope") != "etf_time_series":
        raise ValueError("sentinel baseline 仅允许 ETF 时序范围")
    if config.get("featureParameters") != {}:
        raise ValueError("sentinel baseline 禁止参数搜索或特征网格")
    target_parameters = config.get("targetWeightParameters") or {}
    if set(target_parameters) != {"signalDate", "targetWeight"}:
        raise ValueError("sentinel baseline 只接受固定 signalDate 与 targetWeight")

    root = Path(input_root)
    if compressed:
        if table_artifacts is None:
            raise ValueError("正式 baseline 必须绑定冻结输入 artifact")
        verify_materialized_inputs(root, table_artifacts)
    reader = _compressed_reader(root) if compressed else _plain_reader(root)
    calendars = reader("trade_calendars")
    bars = reader("fund_daily_bars")
    factors = reader("fund_adjust_factors")
    benchmark_bars = reader("index_daily_bars")
    calendar = _build_frozen_calendar(
        calendars,
        source_path=(
            root / "trade_calendars.csv.gz"
            if compressed
            else root / "trade_calendars.csv"
        ),
        artifact_sha256=(table_artifacts or {}).get("trade_calendars", {}).get("contentSha256"),
        config=config,
    )
    if compressed:
        universe = reader("universe")
        actual_members = sorted(universe["ts_code"].dropna().astype(str).str.upper().tolist())
        if actual_members != config["universe"]["members"]:
            raise ValueError("冻结 universe artifact 与研究配置不一致")

    members = set(config["universe"]["members"])
    if len(members) != 1:
        raise ValueError("sentinel baseline 只允许一只 ETF")
    warmup_start = pd.Timestamp(config["warmupStart"])
    research_start = pd.Timestamp(config["startDate"])
    end = pd.Timestamp(config["endDate"])
    bars["trade_date"] = pd.to_datetime(bars["trade_date"])
    factors["trade_date"] = pd.to_datetime(factors["trade_date"])
    benchmark_bars["trade_date"] = pd.to_datetime(benchmark_bars["trade_date"])
    bars = bars[
        bars["ts_code"].isin(members)
        & bars["trade_date"].between(warmup_start, end)
    ].copy()
    factors = factors[
        factors["ts_code"].isin(members)
        & factors["trade_date"].between(warmup_start, end)
    ].copy()
    benchmark_bars = benchmark_bars[
        (benchmark_bars["ts_code"] == config["benchmark"])
        & benchmark_bars["trade_date"].between(warmup_start, end)
    ].copy()
    if bars.empty or factors.empty or benchmark_bars.empty:
        raise ValueError("sentinel baseline 冻结输入不完整")

    prices = build_adjusted_price_panel(bars, factors)
    prices["is_buyable_at_open"] = True
    prices["is_sellable_at_open"] = True
    target_weight = float(target_parameters["targetWeight"])
    if not 0 < target_weight <= 1:
        raise ValueError("sentinel targetWeight 必须在 (0, 1] 范围")
    signal_date = pd.Timestamp(target_parameters["signalDate"])
    if not research_start <= signal_date < end:
        raise ValueError("sentinel signalDate 必须位于 startDate 含至 endDate 不含的研究区间")
    if signal_date.date().isoformat() not in calendar.open_dates:
        raise ValueError("sentinel signalDate 必须来自冻结官方开市日历")
    targets = pd.DataFrame(
        [
            {
                "signal_date": signal_date,
                "available_date": signal_date,
                "ts_code": sorted(members)[0],
                "target_weight": target_weight,
            }
        ]
    )
    cost_config = config["costModel"]
    nav = simulate_target_weights(
        prices,
        targets,
        trade_calendar=calendar,
        cost=CostModel(
            buy_rate=float(cost_config["buyRate"]),
            sell_rate=float(cost_config["sellRate"]),
            slippage_rate=float(cost_config["slippageRate"]),
        ),
    )
    benchmark = benchmark_bars[["trade_date", "close"]].copy().sort_values("trade_date")
    benchmark["close"] = pd.to_numeric(benchmark["close"], errors="raise")
    benchmark["nav"] = benchmark["close"] / benchmark["close"].iloc[0]
    evaluation_nav = nav[nav["trade_date"].between(research_start, end)]
    evaluation_benchmark = benchmark[benchmark["trade_date"].between(research_start, end)]
    metrics = summarize_performance(
        evaluation_nav[["trade_date", "nav"]],
        evaluation_benchmark[["trade_date", "nav"]],
    )
    limitations = [
        "research_only",
        "not_investment_advice",
        "pipeline_sentinel_not_alpha_research",
        "fixed_single_etf_weight_no_parameter_search",
        "daily_data_only_no_minute_or_options",
        "no_financial_cross_section",
        "warmup_excluded_from_metrics",
    ]
    return SentinelBaselineResult(
        targets=targets,
        nav=nav,
        metrics=metrics,
        limitations=limitations,
        calendar=calendar,
    )


def _build_frozen_calendar(
    frame: pd.DataFrame,
    *,
    source_path: Path,
    artifact_sha256: str | None,
    config: dict[str, Any],
) -> OpenTradeCalendar:
    required = {"exchange", "cal_date", "is_open"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"冻结交易日历缺少字段：{', '.join(missing)}")
    expected_exchange = str(config["executionPolicy"].get("calendarExchange", "SSE")).upper()
    records = [
        {
            "exchange": str(row.exchange).strip().upper(),
            "cal_date": str(row.cal_date),
            "is_open": _strict_calendar_bool(row.is_open),
        }
        for row in frame.itertuples(index=False)
    ]
    actual_sha256 = trade_calendar_content_sha256(records)
    if artifact_sha256 is not None and artifact_sha256 != actual_sha256:
        raise ValueError("冻结交易日历 artifact hash 与规范化记录不一致")
    return build_open_trade_calendar(
        records,
        source_artifact=str(source_path),
        source_artifact_sha256=actual_sha256,
        exchange=expected_exchange,
    )


def _strict_calendar_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, Integral) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str) and value in {"0", "1"}:
        return value == "1"
    raise ValueError("冻结交易日历 is_open 必须是 0/1/bool")


def _compressed_reader(root: Path):
    def read(name: str) -> pd.DataFrame:
        return read_canonical_csv_gz(root / f"{name}.csv.gz")

    return read


def _plain_reader(root: Path):
    def read(name: str) -> pd.DataFrame:
        return pd.read_csv(root / f"{name}.csv")

    return read
