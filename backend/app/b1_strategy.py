from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any, Iterable

import pandas as pd

from my_quant.strategy_research.experiment.b1_trend_pullback import (
    B1BacktestConfig,
    B1BacktestResult,
    apply_mainboard_style_gate,
    calculate_bbi,
    compute_b1_frame,
    is_mainboard_a_share,
    run_b1_backtest,
)


B1_STRATEGY_ID = "b1-trend-pullback"
B1_STRATEGY_LABEL = "B1 趋势回踩组合策略"
B1_RESPONSE_ROW_LIMIT = 500
B1_DEFAULT_CONFIG: dict[str, Any] = {
    "top_n": 1,
    "max_position": 1.0,
    "initial_cash": 20_000.0,
    "buy_price_column": "open",
    "sell_price_column": "close",
    "lot_size": 100,
    "require_affordable_lot": True,
    "limit_up_pct": 0.10,
    "limit_down_pct": 0.10,
    "max_entry_close_bbi": 0.275,
    "min_entry_mom20": 0.02,
    "max_entry_mom20": 0.75,
    "stop_loss_pct": 0.05,
    "take_profit_levels": (0.05,),
    "take_profit_fractions": (1.0,),
}


BacktestRow = tuple[str, float, float, float, float, float]


def filter_mainboard_stock_codes(codes: Iterable[str]) -> list[str]:
    return [str(code) for code in codes if is_mainboard_a_share(str(code))]


def build_b1_config(raw_config: dict[str, Any] | None) -> B1BacktestConfig:
    payload = {**B1_DEFAULT_CONFIG, **(raw_config or {})}
    return B1BacktestConfig(
        top_n=int(payload["top_n"]),
        max_position=float(payload["max_position"]),
        initial_cash=float(payload["initial_cash"]),
        cost_rate=float(payload.get("cost_rate", B1BacktestConfig.cost_rate)),
        buy_price_column=_price_column(payload["buy_price_column"], "buy_price_column"),
        sell_price_column=_price_column(payload["sell_price_column"], "sell_price_column"),
        lot_size=_optional_int(payload.get("lot_size")),
        require_affordable_lot=bool(payload.get("require_affordable_lot", False)),
        limit_up_pct=_optional_float(payload.get("limit_up_pct")),
        limit_down_pct=_optional_float(payload.get("limit_down_pct")),
        volume_limit_pct=_optional_float(payload.get("volume_limit_pct")),
        bbi_windows=_tuple_int(payload.get("bbi_windows", B1BacktestConfig.bbi_windows)),
        ema_span=int(payload.get("ema_span", B1BacktestConfig.ema_span)),
        kdj_window=int(payload.get("kdj_window", B1BacktestConfig.kdj_window)),
        kdj_j_threshold=float(payload.get("kdj_j_threshold", B1BacktestConfig.kdj_j_threshold)),
        score_trend_weight=float(payload.get("score_trend_weight", B1BacktestConfig.score_trend_weight)),
        score_pullback_weight=float(payload.get("score_pullback_weight", B1BacktestConfig.score_pullback_weight)),
        score_price_buffer_weight=float(payload.get("score_price_buffer_weight", B1BacktestConfig.score_price_buffer_weight)),
        max_entry_close_bbi=_optional_float(payload.get("max_entry_close_bbi")),
        min_entry_mom20=_optional_float(payload.get("min_entry_mom20")),
        max_entry_mom20=_optional_float(payload.get("max_entry_mom20")),
        stop_loss_pct=_optional_float(payload.get("stop_loss_pct")),
        take_profit_levels=_tuple_float(payload["take_profit_levels"]),
        take_profit_fractions=_tuple_float(payload["take_profit_fractions"]),
    )


def rows_to_b1_panel(rows: Iterable[BacktestRow], config: B1BacktestConfig, volume_unit: str = "hand") -> pd.DataFrame:
    frame = rows_to_ohlcv_frame(rows, volume_unit=volume_unit)
    return compute_b1_frame(frame, config)


def rows_to_ohlcv_frame(rows: Iterable[BacktestRow], volume_unit: str = "hand") -> pd.DataFrame:
    multiplier = volume_multiplier(volume_unit)
    frame = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    if frame.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    frame["date"] = pd.to_datetime(frame["date"])
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["volume"] = frame["volume"].fillna(0.0) * multiplier
    return frame.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date").drop_duplicates("date", keep="last").set_index("date")


def build_b1_panels_from_rows(
    rows_by_code: dict[str, list[BacktestRow]],
    config: B1BacktestConfig,
    min_bars: int,
    volume_unit: str = "hand",
) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
    panels: dict[str, pd.DataFrame] = {}
    skipped: dict[str, int] = {}
    for ts_code, rows in rows_by_code.items():
        if len(rows) < min_bars:
            skipped[ts_code] = len(rows)
            continue
        panel = rows_to_b1_panel(rows, config, volume_unit=volume_unit)
        if len(panel) < min_bars:
            skipped[ts_code] = len(panel)
            continue
        panels[ts_code] = panel
    return panels, skipped


def build_b1_market_frame_from_rows(
    rows: Iterable[BacktestRow],
    config: B1BacktestConfig,
    eval_start: date,
    eval_end: date,
    require_ma20_gt_ma60: bool = True,
    volume_unit: str = "hand",
) -> pd.DataFrame:
    frame = rows_to_ohlcv_frame(rows, volume_unit=volume_unit)
    if frame.empty:
        return pd.DataFrame(columns=["close", "bbi", "ma20", "ma60"])
    market = pd.DataFrame({"close": frame["close"]})
    market["bbi"] = calculate_bbi(market["close"], config.bbi_windows)
    market["ma20"] = market["close"].rolling(20, min_periods=20).mean()
    market["ma60"] = market["close"].rolling(60, min_periods=60).mean()
    market = market.loc[str(eval_start) : str(eval_end)].copy()
    if require_ma20_gt_ma60:
        allowed = (market["ma20"] > market["ma60"]).fillna(False)
        market.loc[~allowed, "bbi"] = market.loc[~allowed, "close"] * 2.0
    return market


def build_permissive_market_frame(panels: dict[str, pd.DataFrame], eval_start: date, eval_end: date) -> pd.DataFrame:
    dates = sorted({date_value for panel in panels.values() for date_value in panel.index if pd.Timestamp(eval_start) <= date_value <= pd.Timestamp(eval_end)})
    return pd.DataFrame({"close": 1.0, "bbi": 0.0, "ma20": 1.0, "ma60": 1.0}, index=pd.DatetimeIndex(dates))


def run_backend_b1_backtest(
    panels: dict[str, pd.DataFrame],
    market_frame: pd.DataFrame,
    config: B1BacktestConfig,
    max_response_rows: int = B1_RESPONSE_ROW_LIMIT,
) -> dict[str, Any]:
    result = run_b1_backtest(panels, market_frame, config)
    return b1_result_to_response(result, config, max_response_rows=max_response_rows)


def b1_result_to_response(
    result: B1BacktestResult,
    config: B1BacktestConfig,
    max_response_rows: int = B1_RESPONSE_ROW_LIMIT,
) -> dict[str, Any]:
    nav = result.nav.astype(float)
    running_max = nav.cummax()
    drawdown = nav / running_max - 1.0
    equity_curve = [
        {
            "date": date_value.strftime("%Y-%m-%d"),
            "nav": float(nav_value),
            "equity": float(nav_value * config.initial_cash),
            "drawdown": float(drawdown.loc[date_value]),
        }
        for date_value, nav_value in nav.items()
    ]
    final_nav = float(nav.iloc[-1]) if not nav.empty else 1.0
    summary = {
        "finalNav": final_nav,
        "finalEquity": final_nav * config.initial_cash,
        "totalReturn": final_nav - 1.0,
        "annualReturn": float(result.summary["annual_return"]),
        "maxDrawdown": float(result.summary["max_drawdown"]),
        "sharpe": float(result.summary["sharpe"]),
        "beta": float(result.summary["beta"]),
        "calmar": float(result.summary["calmar"]),
        "tradeCount": int(float(result.summary["trade_count"])),
        "candidateCount": int(float(result.summary["candidate_count"])),
        "passesReturnGate": bool(result.summary["passes_return_gate"]),
        "passesDrawdownGate": bool(result.summary["passes_drawdown_gate"]),
    }
    return {
        "strategy": {
            "id": B1_STRATEGY_ID,
            "label": B1_STRATEGY_LABEL,
            "mode": "research_backtest",
            "disclaimer": "研究回测输出，不构成真实交易指令。",
        },
        "config": config_to_response(config),
        "summary": summary,
        "equityCurve": equity_curve,
        "trades": dataframe_records(result.trades, max_rows=max_response_rows),
        "recentTrades": dataframe_records(result.trades.tail(80), max_rows=80),
        "candidates": dataframe_records(result.candidates.tail(max_response_rows), max_rows=max_response_rows),
    }


def config_to_response(config: B1BacktestConfig) -> dict[str, Any]:
    payload = asdict(config)
    for key, value in list(payload.items()):
        if isinstance(value, tuple):
            payload[key] = list(value)
    return payload


def dataframe_records(frame: pd.DataFrame, max_rows: int) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    trimmed = frame.head(max_rows).copy()
    for column in trimmed.columns:
        if pd.api.types.is_datetime64_any_dtype(trimmed[column]):
            trimmed[column] = trimmed[column].dt.strftime("%Y-%m-%d")
    records: list[dict[str, Any]] = []
    for record in trimmed.to_dict("records"):
        records.append({key: scalar_to_json(value) for key, value in record.items()})
    return records


def scalar_to_json(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    return value


def volume_multiplier(volume_unit: str) -> float:
    if volume_unit == "hand":
        return 100.0
    if volume_unit == "share":
        return 1.0
    raise ValueError("volume_unit 仅支持 hand 或 share")


def _price_column(value: Any, name: str) -> str:
    column = str(value)
    if column not in {"open", "high", "low", "close"}:
        raise ValueError(f"{name} 仅支持 open/high/low/close")
    return column


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _tuple_float(value: Any) -> tuple[float, ...]:
    if isinstance(value, str):
        value = [item.strip() for item in value.split(",") if item.strip()]
    return tuple(float(item) for item in value)


def _tuple_int(value: Any) -> tuple[int, ...]:
    if isinstance(value, str):
        value = [item.strip() for item in value.split(",") if item.strip()]
    return tuple(int(item) for item in value)
