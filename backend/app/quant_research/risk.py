from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

import pandas as pd

from .baselines import open_strategy_inputs
from .dataset import build_adjusted_price_panel
from .run_config import validate_risk_policy


RISK_EXPOSURE_COLUMNS = (
    "trade_date",
    "gross_exposure",
    "net_exposure",
    "cash_weight",
    "max_weight",
    "hhi",
    "industry_source_key",
    "industry_weight",
    "benchmark_beta",
)
RISK_CONTRIBUTION_COLUMNS = (
    "trade_date",
    "ts_code",
    "close_weight",
    "marginal_risk_contribution",
    "total_risk_contribution",
    "portfolio_volatility",
)
ANNUALIZATION_FACTOR = 252.0


@dataclass(frozen=True)
class RiskResult:
    exposures: pd.DataFrame
    contributions: pd.DataFrame


def calculate_frozen_risk_frames(
    input_root: Path,
    config: dict[str, Any],
    nav: pd.DataFrame,
    positions: pd.DataFrame,
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]] | None = None,
) -> RiskResult:
    policy = validate_risk_policy(config.get("riskPolicy"))
    if policy["mode"] == "none":
        raise ValueError("riskPolicy 未启用")
    _root, reader = open_strategy_inputs(input_root, compressed, table_artifacts)
    start = pd.Timestamp(config["warmupStart"])
    end = pd.Timestamp(config["endDate"])
    if config["scope"] == "etf_time_series":
        bars = reader("fund_daily_bars")
        factors = reader("fund_adjust_factors")
        membership = pd.DataFrame(columns=["trade_date", "ts_code"])
        industry_source_key = None
    elif config["scope"] == "a_share_cross_section":
        bars = reader("stock_daily_bars")
        factors = reader("stock_adjust_factors")
        membership = reader("universe")[["trade_date", "ts_code"]].copy()
        industry_source_key = config["universe"]["sourceKey"]
    else:
        raise ValueError("风险层不支持该 scope")

    asset_returns = _build_frozen_asset_returns(
        bars,
        factors,
        reader("trade_calendars"),
        start=start,
        end=end,
    )
    benchmark_returns = _build_frozen_benchmark_returns(
        reader("index_daily_bars"),
        benchmark=str(config["benchmark"]),
        start=start,
        end=end,
    )
    return calculate_risk_frames(
        asset_returns,
        positions,
        nav,
        benchmark_returns,
        membership=membership,
        industry_source_key=industry_source_key,
        policy=policy,
    )


def calculate_risk_frames(
    asset_returns: pd.DataFrame,
    positions: pd.DataFrame,
    nav: pd.DataFrame,
    benchmark_returns: pd.DataFrame,
    *,
    membership: pd.DataFrame,
    industry_source_key: str | None,
    policy: dict[str, Any],
) -> RiskResult:
    normalized_policy = validate_risk_policy(policy)
    if normalized_policy["mode"] == "none":
        raise ValueError("风险计算要求启用 rolling_covariance")
    returns = _normalize_keyed_frame(
        asset_returns,
        required=("trade_date", "ts_code", "asset_return"),
        natural_key=("trade_date", "ts_code"),
        numeric=("asset_return",),
        label="标的收益",
    )
    holdings = _normalize_keyed_frame(
        positions,
        required=("trade_date", "ts_code", "close_weight"),
        natural_key=("trade_date", "ts_code"),
        numeric=("close_weight",),
        label="持仓",
        allow_empty=True,
    )
    nav_frame = _normalize_keyed_frame(
        nav,
        required=("trade_date", "nav", "cash_weight"),
        natural_key=("trade_date",),
        numeric=("nav", "cash_weight"),
        label="NAV",
    )
    benchmark = _normalize_keyed_frame(
        benchmark_returns,
        required=("trade_date", "benchmark_return"),
        natural_key=("trade_date",),
        numeric=("benchmark_return",),
        label="基准收益",
    )
    members = _normalize_membership(membership)
    if members.empty and industry_source_key is not None:
        raise ValueError("行业风险暴露缺少逐日历史成员")
    if not members.empty and not str(industry_source_key or "").strip():
        raise ValueError("行业风险暴露缺少 sourceKey")
    nav_dates = set(nav_frame["trade_date"])
    if not set(holdings["trade_date"]).issubset(nav_dates):
        raise ValueError("持仓包含 NAV 之外的日期")
    if (nav_frame["nav"] <= 0).any():
        raise ValueError("风险计算要求 NAV 大于 0")

    lookback = normalized_policy["lookbackPeriods"]
    minimum = normalized_policy["minPeriods"]
    return_panel = returns.pivot(
        index="trade_date",
        columns="ts_code",
        values="asset_return",
    ).sort_index()
    portfolio_returns = nav_frame[["trade_date", "nav"]].copy()
    portfolio_returns["portfolio_return"] = portfolio_returns["nav"].pct_change(
        fill_method=None
    )
    paired_returns = portfolio_returns[["trade_date", "portfolio_return"]].merge(
        benchmark,
        on="trade_date",
        how="inner",
        validate="one_to_one",
    ).dropna(subset=["portfolio_return"])
    _require_finite(paired_returns, ("portfolio_return", "benchmark_return"), "组合/基准收益")

    holding_groups = {
        trade_date: group.set_index("ts_code")["close_weight"].sort_index()
        for trade_date, group in holdings.groupby("trade_date", sort=True)
    }
    membership_keys = set(
        zip(members["trade_date"], members["ts_code"], strict=True)
    )
    exposure_rows: list[dict[str, Any]] = []
    contribution_rows: list[dict[str, Any]] = []
    for row in nav_frame.itertuples(index=False):
        trade_date = row.trade_date
        weights = holding_groups.get(
            trade_date,
            pd.Series(dtype=float, name="close_weight"),
        )
        gross = float(weights.abs().sum())
        net = float(weights.sum())
        industry_weight: float | None = None
        if industry_source_key is not None:
            industry_weight = float(
                sum(
                    weight
                    for symbol, weight in weights.items()
                    if (trade_date, symbol) in membership_keys
                )
            )
        beta = _rolling_beta(
            paired_returns,
            trade_date,
            lookback=lookback,
            minimum=minimum,
        )
        exposure_rows.append(
            {
                "trade_date": trade_date,
                "gross_exposure": gross,
                "net_exposure": net,
                "cash_weight": float(row.cash_weight),
                "max_weight": float(weights.abs().max()) if not weights.empty else 0.0,
                "hhi": float(weights.pow(2).sum()),
                "industry_source_key": industry_source_key,
                "industry_weight": industry_weight,
                "benchmark_beta": beta,
            }
        )
        contribution_rows.extend(
            _risk_contribution_rows(
                return_panel,
                trade_date,
                weights,
                lookback=lookback,
                minimum=minimum,
            )
        )

    result = RiskResult(
        exposures=pd.DataFrame(exposure_rows, columns=RISK_EXPOSURE_COLUMNS),
        contributions=pd.DataFrame(
            contribution_rows,
            columns=RISK_CONTRIBUTION_COLUMNS,
        ),
    )
    validate_risk_artifacts(
        result.exposures,
        result.contributions,
        normalized_policy,
    )
    return result


def validate_risk_artifacts(
    exposures: pd.DataFrame,
    contributions: pd.DataFrame,
    policy: dict[str, Any],
) -> None:
    normalized_policy = validate_risk_policy(policy)
    if normalized_policy["mode"] == "none":
        raise ValueError("未启用风险工件")
    if list(exposures.columns) != list(RISK_EXPOSURE_COLUMNS) or exposures.empty:
        raise ValueError("风险暴露工件列合同无效或为空")
    if list(contributions.columns) != list(RISK_CONTRIBUTION_COLUMNS):
        raise ValueError("风险贡献工件列合同无效")
    exposure_frame = exposures.copy()
    contribution_frame = contributions.copy()
    for frame, keys, label in (
        (exposure_frame, ("trade_date",), "风险暴露"),
        (contribution_frame, ("trade_date", "ts_code"), "风险贡献"),
    ):
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
        if frame.duplicated(list(keys)).any():
            raise ValueError(f"{label}自然键重复")
        actual_keys = list(frame.loc[:, list(keys)].itertuples(index=False, name=None))
        if actual_keys != sorted(actual_keys):
            raise ValueError(f"{label}未按自然键排序")

    required_exposures = (
        "gross_exposure",
        "net_exposure",
        "cash_weight",
        "max_weight",
        "hhi",
    )
    for column in (*required_exposures, "industry_weight", "benchmark_beta"):
        exposure_frame[column] = pd.to_numeric(exposure_frame[column], errors="coerce")
    if exposure_frame[list(required_exposures)].isna().any().any():
        raise ValueError("风险暴露必填数值不能为 null")
    _require_finite(exposure_frame, required_exposures, "风险暴露")
    _require_finite_nullable(
        exposure_frame,
        ("industry_weight", "benchmark_beta"),
        "行业暴露或 beta",
    )
    if (
        (exposure_frame["gross_exposure"] < -1e-12).any()
        or (exposure_frame["max_weight"] < -1e-12).any()
        or (exposure_frame["hhi"] < -1e-12).any()
    ):
        raise ValueError("风险暴露包含无效负值")
    industry_key_present = exposure_frame["industry_source_key"].notna()
    if (
        exposure_frame.loc[industry_key_present, "industry_weight"].isna().any()
        or exposure_frame.loc[~industry_key_present, "industry_weight"].notna().any()
    ):
        raise ValueError("行业 sourceKey 与行业权重不一致")

    if contribution_frame.empty:
        return
    numeric_columns = (
        "close_weight",
        "marginal_risk_contribution",
        "total_risk_contribution",
        "portfolio_volatility",
    )
    for column in numeric_columns:
        contribution_frame[column] = pd.to_numeric(
            contribution_frame[column], errors="coerce"
        )
    if contribution_frame["close_weight"].isna().any():
        raise ValueError("风险贡献持仓权重不能为 null")
    _require_finite(contribution_frame, ("close_weight",), "风险贡献权重")
    _require_finite_nullable(
        contribution_frame,
        numeric_columns[1:],
        "风险贡献",
    )
    for _trade_date, group in contribution_frame.groupby("trade_date", sort=True):
        nullable = group[list(numeric_columns[1:])]
        if nullable.isna().all().all():
            continue
        if nullable.isna().any().any():
            raise ValueError("同一日期的风险贡献不能部分为 null")
        volatility = float(group["portfolio_volatility"].iloc[0])
        if volatility < -1e-12 or not group["portfolio_volatility"].eq(volatility).all():
            raise ValueError("同一日期组合波动不一致或为负")
        total = float(group["total_risk_contribution"].sum())
        tolerance = max(1e-12, abs(volatility) * 1e-10)
        if abs(total - volatility) > tolerance:
            raise ValueError("风险贡献之和与组合波动不一致")


def _risk_contribution_rows(
    return_panel: pd.DataFrame,
    trade_date: pd.Timestamp,
    weights: pd.Series,
    *,
    lookback: int,
    minimum: int,
) -> list[dict[str, Any]]:
    if weights.empty:
        return []
    symbols = list(weights.index)
    missing_symbols = sorted(set(symbols) - set(return_panel.columns))
    if missing_symbols:
        raise ValueError(f"持仓缺少冻结收益序列：{', '.join(missing_symbols[:10])}")
    history = return_panel.loc[return_panel.index <= trade_date, symbols].tail(lookback)
    complete = history.dropna(how="any")
    if len(complete) < minimum:
        return [
            {
                "trade_date": trade_date,
                "ts_code": symbol,
                "close_weight": float(weights[symbol]),
                "marginal_risk_contribution": None,
                "total_risk_contribution": None,
                "portfolio_volatility": None,
            }
            for symbol in symbols
        ]
    _require_finite(complete.reset_index(drop=True), tuple(symbols), "滚动收益窗口")
    covariance = complete.cov()
    _require_finite(covariance.reset_index(drop=True), tuple(symbols), "滚动协方差")
    weight_vector = weights.astype(float).reindex(symbols)
    covariance_weight = covariance.dot(weight_vector)
    variance = float(weight_vector.dot(covariance_weight))
    if not math.isfinite(variance) or variance < -1e-15:
        raise ValueError("组合方差无效")
    variance = max(variance, 0.0)
    daily_volatility = math.sqrt(variance)
    annualized_volatility = daily_volatility * math.sqrt(ANNUALIZATION_FACTOR)
    if daily_volatility == 0:
        marginal = pd.Series(0.0, index=symbols)
    else:
        marginal = covariance_weight * math.sqrt(ANNUALIZATION_FACTOR) / daily_volatility
    total = weight_vector * marginal
    if not all(
        math.isfinite(float(value))
        for value in [annualized_volatility, *marginal.tolist(), *total.tolist()]
    ):
        raise ValueError("风险贡献出现 NaN 或 Infinity")
    return [
        {
            "trade_date": trade_date,
            "ts_code": symbol,
            "close_weight": float(weight_vector[symbol]),
            "marginal_risk_contribution": float(marginal[symbol]),
            "total_risk_contribution": float(total[symbol]),
            "portfolio_volatility": float(annualized_volatility),
        }
        for symbol in symbols
    ]


def _rolling_beta(
    paired_returns: pd.DataFrame,
    trade_date: pd.Timestamp,
    *,
    lookback: int,
    minimum: int,
) -> float | None:
    window = paired_returns[paired_returns["trade_date"] <= trade_date].tail(lookback)
    if len(window) < minimum:
        return None
    variance = float(window["benchmark_return"].var(ddof=1))
    if not math.isfinite(variance):
        raise ValueError("基准方差出现 NaN 或 Infinity")
    if abs(variance) <= 1e-24:
        return None
    covariance = float(
        window["portfolio_return"].cov(window["benchmark_return"], ddof=1)
    )
    beta = covariance / variance
    if not math.isfinite(beta):
        raise ValueError("benchmark beta 出现 NaN 或 Infinity")
    return beta


def _build_frozen_asset_returns(
    bars: pd.DataFrame,
    factors: pd.DataFrame,
    calendars: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    price_frame = bars.copy()
    factor_frame = factors.copy()
    for frame in (price_frame, factor_frame):
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
        frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
        frame.drop(frame.index[~frame["trade_date"].between(start, end)], inplace=True)
    if price_frame.empty or factor_frame.empty:
        raise ValueError("风险层冻结行情或复权因子为空")
    adjusted = build_adjusted_price_panel(price_frame, factor_frame)
    adjusted = adjusted[["trade_date", "ts_code", "adj_close"]].copy()
    if adjusted.duplicated(["trade_date", "ts_code"]).any():
        raise ValueError("风险层冻结复权价格自然键重复")
    calendar = calendars.copy()
    calendar["cal_date"] = pd.to_datetime(calendar["cal_date"], errors="raise")
    open_mask = calendar["is_open"].astype(str).isin({"1", "True", "true"})
    open_dates = pd.DatetimeIndex(
        sorted(
            set(
                calendar.loc[
                    open_mask & calendar["cal_date"].between(start, end),
                    "cal_date",
                ]
            )
        )
    )
    if open_dates.empty:
        raise ValueError("风险层冻结交易日历为空")
    prices = adjusted.pivot(
        index="trade_date",
        columns="ts_code",
        values="adj_close",
    ).reindex(open_dates).ffill()
    _require_finite_nullable(prices.reset_index(drop=True), tuple(prices.columns), "复权收盘价")
    returns = prices.pct_change(fill_method=None)
    rows: list[dict[str, Any]] = []
    for trade_date, values in returns.iterrows():
        for symbol, value in values.items():
            if pd.isna(value):
                continue
            if not math.isfinite(float(value)):
                raise ValueError("冻结标的收益出现 NaN 或 Infinity")
            rows.append(
                {
                    "trade_date": trade_date,
                    "ts_code": symbol,
                    "asset_return": float(value),
                }
            )
    if not rows:
        raise ValueError("风险层无法生成冻结标的收益")
    return pd.DataFrame(rows).sort_values(
        ["trade_date", "ts_code"], kind="stable"
    ).reset_index(drop=True)


def _build_frozen_benchmark_returns(
    bars: pd.DataFrame,
    *,
    benchmark: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    frame = bars.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame[
        frame["ts_code"].eq(benchmark)
        & frame["trade_date"].between(start, end)
    ].sort_values("trade_date", kind="stable")
    if frame.empty or frame.duplicated(["trade_date"]).any():
        raise ValueError("风险层冻结基准为空或日期重复")
    _require_finite(frame, ("close",), "冻结基准收盘价")
    if (frame["close"] <= 0).any():
        raise ValueError("冻结基准收盘价必须大于 0")
    frame["benchmark_return"] = frame["close"].pct_change(fill_method=None)
    result = frame.dropna(subset=["benchmark_return"])[
        ["trade_date", "benchmark_return"]
    ].reset_index(drop=True)
    if result.empty:
        raise ValueError("风险层冻结基准收益为空")
    _require_finite(result, ("benchmark_return",), "冻结基准收益")
    return result


def _normalize_keyed_frame(
    frame: pd.DataFrame,
    *,
    required: tuple[str, ...],
    natural_key: tuple[str, ...],
    numeric: tuple[str, ...],
    label: str,
    allow_empty: bool = False,
) -> pd.DataFrame:
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise ValueError(f"{label}缺少字段：{', '.join(missing)}")
    normalized = frame.loc[:, list(required)].copy()
    normalized["trade_date"] = pd.to_datetime(
        normalized["trade_date"], errors="raise"
    )
    if getattr(normalized["trade_date"].dt, "tz", None) is not None:
        raise ValueError(f"{label}日期必须是不带时区的交易日")
    if "ts_code" in normalized:
        normalized["ts_code"] = (
            normalized["ts_code"].astype(str).str.strip().str.upper()
        )
        if normalized["ts_code"].eq("").any():
            raise ValueError(f"{label} ts_code 不能为空")
    for column in numeric:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    if not allow_empty and normalized.empty:
        raise ValueError(f"{label}不能为空")
    if normalized.duplicated(list(natural_key)).any():
        raise ValueError(f"{label}自然键重复")
    _require_finite(normalized, numeric, label)
    return normalized.sort_values(list(natural_key), kind="stable").reset_index(drop=True)


def _normalize_membership(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"trade_date", "ts_code"}
    if not required.issubset(frame.columns):
        raise ValueError("行业成员缺少 trade_date 或 ts_code")
    normalized = frame[["trade_date", "ts_code"]].copy()
    normalized["trade_date"] = pd.to_datetime(
        normalized["trade_date"], errors="raise"
    )
    normalized["ts_code"] = (
        normalized["ts_code"].astype(str).str.strip().str.upper()
    )
    if normalized.duplicated(["trade_date", "ts_code"]).any():
        raise ValueError("行业成员自然键重复")
    return normalized.sort_values(
        ["trade_date", "ts_code"], kind="stable"
    ).reset_index(drop=True)


def _require_finite(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    label: str,
) -> None:
    for column in columns:
        if frame[column].isna().any() or not frame[column].map(
            lambda value: math.isfinite(float(value))
        ).all():
            raise ValueError(f"{label}出现 NaN 或 Infinity：{column}")


def _require_finite_nullable(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    label: str,
) -> None:
    for column in columns:
        values = frame[column].dropna()
        if not values.map(lambda value: math.isfinite(float(value))).all():
            raise ValueError(f"{label}出现 Infinity：{column}")
