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
from .portfolio import CostModel, SimulationResult, simulate_target_weights_with_ledger
from .snapshot import verify_materialized_inputs


SENTINEL_LIMITATIONS = (
    "research_only",
    "not_investment_advice",
    "pipeline_sentinel_not_alpha_research",
    "fixed_single_etf_weight_no_parameter_search",
    "daily_data_only_no_minute_or_options",
    "no_financial_cross_section",
    "warmup_excluded_from_metrics",
    "risk_free_rate_assumed_zero",
)


@dataclass(frozen=True)
class SentinelBaselineResult:
    targets: pd.DataFrame
    nav: pd.DataFrame
    metrics: dict[str, Any]
    limitations: list[str]
    calendar: OpenTradeCalendar


def build_sentinel_targets(
    input_root: Path,
    config: dict[str, Any],
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> pd.DataFrame:
    target_parameters = validate_sentinel_config(config)
    root, reader = open_strategy_inputs(input_root, compressed, table_artifacts)
    calendar = load_frozen_calendar(root, reader, config, compressed, table_artifacts)
    members = validate_explicit_universe(reader, config, compressed)
    research_start = pd.Timestamp(config["startDate"])
    end = pd.Timestamp(config["endDate"])
    target_weight = float(target_parameters["targetWeight"])
    if not 0 < target_weight <= 1:
        raise ValueError("sentinel targetWeight 必须在 (0, 1] 范围")
    signal_date = pd.Timestamp(target_parameters["signalDate"])
    if not research_start <= signal_date < end:
        raise ValueError("sentinel signalDate 必须位于 startDate 含至 endDate 不含的研究区间")
    if signal_date.date().isoformat() not in calendar.open_dates:
        raise ValueError("sentinel signalDate 必须来自冻结官方开市日历")
    return pd.DataFrame(
        [
            {
                "signal_date": signal_date,
                "available_date": signal_date,
                "ts_code": next(iter(members)),
                "target_weight": target_weight,
            }
        ]
    )


def simulate_sentinel_targets(
    input_root: Path,
    config: dict[str, Any],
    targets: pd.DataFrame,
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> tuple[pd.DataFrame, OpenTradeCalendar]:
    validate_sentinel_config(config)
    simulation, calendar = simulate_etf_targets_with_ledger(
        input_root,
        config,
        targets,
        compressed=compressed,
        table_artifacts=table_artifacts,
    )
    return simulation.nav, calendar


def simulate_sentinel_targets_with_ledger(
    input_root: Path,
    config: dict[str, Any],
    targets: pd.DataFrame,
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> tuple[SimulationResult, OpenTradeCalendar]:
    validate_sentinel_config(config)
    return simulate_etf_targets_with_ledger(
        input_root,
        config,
        targets,
        compressed=compressed,
        table_artifacts=table_artifacts,
    )


def simulate_etf_targets(
    input_root: Path,
    config: dict[str, Any],
    targets: pd.DataFrame,
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> tuple[pd.DataFrame, OpenTradeCalendar]:
    simulation, calendar = simulate_etf_targets_with_ledger(
        input_root,
        config,
        targets,
        compressed=compressed,
        table_artifacts=table_artifacts,
    )
    return simulation.nav, calendar


def simulate_etf_targets_with_ledger(
    input_root: Path,
    config: dict[str, Any],
    targets: pd.DataFrame,
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> tuple[SimulationResult, OpenTradeCalendar]:
    root, reader = open_strategy_inputs(input_root, compressed, table_artifacts)
    calendar = load_frozen_calendar(root, reader, config, compressed, table_artifacts)
    members = set(validate_explicit_universe(reader, config, compressed))
    bars = reader("fund_daily_bars")
    factors = reader("fund_adjust_factors")
    warmup_start = pd.Timestamp(config["warmupStart"])
    end = pd.Timestamp(config["endDate"])
    bars["trade_date"] = pd.to_datetime(bars["trade_date"])
    factors["trade_date"] = pd.to_datetime(factors["trade_date"])
    bars = bars[
        bars["ts_code"].isin(members)
        & bars["trade_date"].between(warmup_start, end)
    ].copy()
    factors = factors[
        factors["ts_code"].isin(members)
        & factors["trade_date"].between(warmup_start, end)
    ].copy()
    if bars.empty or factors.empty:
        raise ValueError("ETF baseline 冻结输入不完整")
    prices = build_adjusted_price_panel(bars, factors)
    prices["is_buyable_at_open"] = True
    prices["is_sellable_at_open"] = True
    cost_config = config["costModel"]
    simulation = simulate_target_weights_with_ledger(
        prices,
        targets,
        trade_calendar=calendar,
        cost=CostModel(
            buy_rate=float(cost_config["buyRate"]),
            sell_rate=float(cost_config["sellRate"]),
            slippage_rate=float(cost_config["slippageRate"]),
        ),
    )
    return simulation, calendar


def summarize_sentinel_metrics(
    input_root: Path,
    config: dict[str, Any],
    nav: pd.DataFrame,
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    validate_sentinel_config(config)
    return summarize_etf_metrics(
        input_root,
        config,
        nav,
        compressed=compressed,
        table_artifacts=table_artifacts,
        include_extended=False,
    )


def summarize_sentinel_metrics_v2(
    input_root: Path,
    config: dict[str, Any],
    nav: pd.DataFrame,
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    validate_sentinel_config(config)
    return summarize_etf_metrics(
        input_root,
        config,
        nav,
        compressed=compressed,
        table_artifacts=table_artifacts,
        include_extended=True,
    )


def summarize_etf_metrics(
    input_root: Path,
    config: dict[str, Any],
    nav: pd.DataFrame,
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
    include_extended: bool = True,
) -> dict[str, Any]:
    _, reader = open_strategy_inputs(input_root, compressed, table_artifacts)
    benchmark_bars = reader("index_daily_bars")
    warmup_start = pd.Timestamp(config["warmupStart"])
    research_start = pd.Timestamp(config["startDate"])
    end = pd.Timestamp(config["endDate"])
    benchmark_bars["trade_date"] = pd.to_datetime(benchmark_bars["trade_date"])
    benchmark_bars = benchmark_bars[
        (benchmark_bars["ts_code"] == config["benchmark"])
        & benchmark_bars["trade_date"].between(warmup_start, end)
    ].copy()
    if benchmark_bars.empty:
        raise ValueError("ETF baseline 冻结基准输入不完整")
    benchmark = benchmark_bars[["trade_date", "close"]].copy().sort_values("trade_date")
    benchmark["close"] = pd.to_numeric(benchmark["close"], errors="raise")
    benchmark["nav"] = benchmark["close"] / benchmark["close"].iloc[0]
    normalized_nav = nav.copy()
    normalized_nav["trade_date"] = pd.to_datetime(normalized_nav["trade_date"])
    evaluation_nav = normalized_nav[normalized_nav["trade_date"].between(research_start, end)]
    evaluation_benchmark = benchmark[benchmark["trade_date"].between(research_start, end)]
    return summarize_performance(
        evaluation_nav[["trade_date", "nav"]],
        evaluation_benchmark[["trade_date", "nav"]],
        include_extended=include_extended,
    )


def sentinel_limitations() -> list[str]:
    return list(SENTINEL_LIMITATIONS)


def run_sentinel_etf_baseline(
    input_root: Path,
    config: dict[str, Any],
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> SentinelBaselineResult:
    targets = build_sentinel_targets(
        input_root,
        config,
        compressed=compressed,
        table_artifacts=table_artifacts,
    )
    nav, calendar = simulate_sentinel_targets(
        input_root,
        config,
        targets,
        compressed=compressed,
        table_artifacts=table_artifacts,
    )
    metrics = summarize_sentinel_metrics(
        input_root,
        config,
        nav,
        compressed=compressed,
        table_artifacts=table_artifacts,
    )
    return SentinelBaselineResult(
        targets=targets,
        nav=nav,
        metrics=metrics,
        limitations=sentinel_limitations(),
        calendar=calendar,
    )


def validate_sentinel_config(config: dict[str, Any]) -> dict[str, Any]:
    if config.get("strategyId") != "sentinel_etf_baseline":
        raise ValueError("Phase 3 runner 只允许 sentinel_etf_baseline")
    if config.get("scope") != "etf_time_series":
        raise ValueError("sentinel baseline 仅允许 ETF 时序范围")
    if config.get("featureParameters") != {}:
        raise ValueError("sentinel featureParameters 必须为空，禁止参数搜索或特征网格")
    target_parameters = config.get("targetWeightParameters") or {}
    if set(target_parameters) != {"signalDate", "targetWeight"}:
        raise ValueError("sentinel baseline 只接受固定 signalDate 与 targetWeight")
    if len(set(config["universe"]["members"])) != 1:
        raise ValueError("sentinel baseline 只允许一只 ETF")
    return target_parameters


def open_strategy_inputs(
    input_root: Path,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None,
) -> tuple[Path, Any]:
    root = Path(input_root)
    if compressed:
        if table_artifacts is None:
            raise ValueError("正式 baseline 必须绑定冻结输入 artifact")
        verify_materialized_inputs(root, table_artifacts)
        return root, _compressed_reader(root)
    return root, _plain_reader(root)


def validate_explicit_universe(reader: Any, config: dict[str, Any], compressed: bool) -> tuple[str, ...]:
    members = tuple(sorted(set(config["universe"]["members"])))
    if compressed:
        universe = reader("universe")
        actual_members = tuple(sorted(universe["ts_code"].dropna().astype(str).str.upper().tolist()))
        if actual_members != members:
            raise ValueError("冻结 universe artifact 与研究配置不一致")
    return members


def load_frozen_calendar(
    root: Path,
    reader: Any,
    config: dict[str, Any],
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None,
) -> OpenTradeCalendar:
    calendars = reader("trade_calendars")
    return _build_frozen_calendar(
        calendars,
        source_path=(
            root / "trade_calendars.csv.gz"
            if compressed
            else root / "trade_calendars.csv"
        ),
        artifact_sha256=(table_artifacts or {}).get("trade_calendars", {}).get("contentSha256"),
        config=config,
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
