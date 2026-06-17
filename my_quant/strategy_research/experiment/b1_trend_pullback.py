from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Iterable

import pandas as pd

from .config import COST_RATE, RESULTS_DIR, SATELLITE_MAX_DRAWDOWN_FLOOR, SATELLITE_TARGET_ANNUAL_RETURN
from .metrics import calculate_metrics


DEFAULT_BBI_WINDOWS = (14, 28, 57, 114)


def retry_call(func, attempts: int = 3, delay_seconds: float = 0.5):
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return func()
        except Exception as exc:  # pragma: no cover - concrete exception types vary by data provider
            last_error = exc
            if attempt + 1 >= attempts:
                break
            if delay_seconds > 0:
                sleep(delay_seconds)
    if last_error is None:
        raise RuntimeError("retry_call exhausted without capturing an error")
    raise last_error


@dataclass(frozen=True)
class B1BacktestConfig:
    top_n: int = 2
    max_position: float = 0.5
    initial_cash: float = 1.0
    cost_rate: float = COST_RATE
    bbi_windows: tuple[int, ...] = DEFAULT_BBI_WINDOWS
    ema_span: int = 10
    kdj_window: int = 9
    kdj_j_threshold: float = 13.0
    max_entry_close_bbi: float | None = None
    min_entry_mom20: float | None = None
    max_entry_mom20: float | None = None
    take_profit_levels: tuple[float, ...] = (0.08, 0.16, 0.24)
    take_profit_fractions: tuple[float, ...] = (0.33, 0.33, 1.0)


@dataclass
class _Position:
    shares: float
    cost_basis: float
    next_take_profit_index: int = 0


@dataclass
class B1BacktestResult:
    nav: pd.Series
    trades: pd.DataFrame
    candidates: pd.DataFrame
    metrics: dict[str, float]
    summary: dict[str, float | bool | str]


def calculate_bbi(close: pd.Series, windows: tuple[int, ...] = DEFAULT_BBI_WINDOWS) -> pd.Series:
    averages = pd.concat([close.rolling(window=window, min_periods=window).mean() for window in windows], axis=1)
    bbi = averages.mean(axis=1, skipna=False)
    bbi.name = "bbi"
    return bbi


def calculate_double_ema(close: pd.Series, span: int = 10) -> pd.Series:
    ema = close.ewm(span=span, adjust=False, min_periods=span).mean()
    double_ema = ema.ewm(span=span, adjust=False, min_periods=span).mean()
    double_ema.name = f"double_ema{span}"
    return double_ema


def calculate_kdj(bars: pd.DataFrame, window: int = 9) -> pd.DataFrame:
    low_min = bars["low"].rolling(window=window, min_periods=window).min()
    high_max = bars["high"].rolling(window=window, min_periods=window).max()
    denominator = (high_max - low_min).replace(0, pd.NA)
    rsv = ((bars["close"] - low_min) / denominator * 100.0).clip(lower=0.0, upper=100.0)
    k = rsv.ewm(alpha=1 / 3, adjust=False, min_periods=1).mean()
    d = k.ewm(alpha=1 / 3, adjust=False, min_periods=1).mean()
    j = 3.0 * k - 2.0 * d
    return pd.DataFrame({"kdj_k": k, "kdj_d": d, "kdj_j": j}, index=bars.index)


def compute_b1_frame(bars: pd.DataFrame, config: B1BacktestConfig | None = None) -> pd.DataFrame:
    cfg = config or B1BacktestConfig()
    required = {"open", "high", "low", "close"}
    missing = required.difference(bars.columns)
    if missing:
        raise ValueError(f"B1 bars missing required columns: {sorted(missing)}")

    frame = bars.sort_index().copy()
    frame["bbi"] = calculate_bbi(frame["close"], cfg.bbi_windows)
    double_ema_name = f"double_ema{cfg.ema_span}"
    frame[double_ema_name] = calculate_double_ema(frame["close"], cfg.ema_span)
    frame["double_ema10"] = frame[double_ema_name]
    frame = frame.join(calculate_kdj(frame, cfg.kdj_window))

    trend_strength = frame["double_ema10"] / frame["bbi"] - 1.0
    pullback_depth = (cfg.kdj_j_threshold - frame["kdj_j"]).clip(lower=0.0) / cfg.kdj_j_threshold
    price_buffer = frame["close"] / frame["bbi"] - 1.0
    frame["entry_close_bbi"] = price_buffer
    frame["entry_mom20"] = frame["close"] / frame["close"].shift(20) - 1.0
    frame["b1_score"] = trend_strength * 100.0 + pullback_depth * 20.0 + price_buffer * 50.0
    entry_signal = (
        (frame["close"] > frame["bbi"])
        & (frame["double_ema10"] > frame["bbi"])
        & (frame["kdj_j"] < cfg.kdj_j_threshold)
    )
    if cfg.max_entry_close_bbi is not None:
        entry_signal &= frame["entry_close_bbi"] <= cfg.max_entry_close_bbi
    if cfg.min_entry_mom20 is not None:
        entry_signal &= frame["entry_mom20"] >= cfg.min_entry_mom20
    if cfg.max_entry_mom20 is not None:
        entry_signal &= frame["entry_mom20"] <= cfg.max_entry_mom20
    frame["entry_signal"] = entry_signal.fillna(False)
    frame.loc[~frame["entry_signal"], "b1_score"] = 0.0
    return frame


def market_allows_entry(market_frame: pd.DataFrame, date: pd.Timestamp) -> bool:
    if date not in market_frame.index:
        return False
    row = market_frame.loc[date]
    if pd.isna(row.get("close")) or pd.isna(row.get("bbi")):
        return False
    return bool(float(row["close"]) > float(row["bbi"]))


def rank_b1_candidates(
    panels: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    config: B1BacktestConfig,
) -> list[dict[str, float | str | pd.Timestamp]]:
    rows: list[dict[str, float | str | pd.Timestamp]] = []
    for symbol, frame in panels.items():
        if date not in frame.index:
            continue
        row = frame.loc[date]
        if not bool(row.get("entry_signal", False)):
            continue
        score = float(row.get("b1_score", 0.0))
        close = float(row.get("close", 0.0)) if "close" in row else 0.0
        if pd.isna(score) or score <= 0:
            continue
        rows.append({"date": date, "symbol": symbol, "score": score, "close": close})

    selected = sorted(rows, key=lambda item: float(item["score"]), reverse=True)[: config.top_n]
    if not selected:
        return []
    target_weight = min(config.max_position, 1.0 / len(selected))
    for row in selected:
        row["target_weight"] = target_weight
    return selected


def _empty_trades() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "symbol", "side", "shares", "price", "value", "reason", "cash_after"])


def _trade_row(
    date: pd.Timestamp,
    symbol: str,
    side: str,
    shares: float,
    price: float,
    reason: str,
    cash_after: float,
) -> dict[str, float | str | pd.Timestamp]:
    return {
        "date": date,
        "symbol": symbol,
        "side": side,
        "shares": shares,
        "price": price,
        "value": shares * price,
        "reason": reason,
        "cash_after": cash_after,
    }


def _portfolio_value(
    cash: float,
    positions: dict[str, _Position],
    panels: dict[str, pd.DataFrame],
    date: pd.Timestamp,
) -> float:
    value = cash
    for symbol, position in positions.items():
        frame = panels[symbol]
        if date in frame.index and pd.notna(frame.loc[date, "close"]):
            value += position.shares * float(frame.loc[date, "close"])
    return value


def _sell_position(
    cash: float,
    position: _Position,
    shares_to_sell: float,
    price: float,
    cost_rate: float,
) -> tuple[float, float]:
    shares = min(position.shares, shares_to_sell)
    proceeds = shares * price * (1.0 - cost_rate)
    position.shares -= shares
    return cash + proceeds, shares


def _process_sells(
    date: pd.Timestamp,
    cash: float,
    positions: dict[str, _Position],
    panels: dict[str, pd.DataFrame],
    config: B1BacktestConfig,
    trades: list[dict[str, float | str | pd.Timestamp]],
) -> float:
    for symbol in list(positions):
        if date not in panels[symbol].index:
            continue
        row = panels[symbol].loc[date]
        if pd.isna(row["close"]):
            continue
        position = positions[symbol]
        price = float(row["close"])
        reason = ""
        shares_to_sell = 0.0
        if pd.notna(row.get("bbi")) and price < float(row["bbi"]):
            reason = "break_bbi"
            shares_to_sell = position.shares
        elif position.next_take_profit_index < len(config.take_profit_levels):
            level = config.take_profit_levels[position.next_take_profit_index]
            if price / position.cost_basis - 1.0 >= level:
                fraction = config.take_profit_fractions[position.next_take_profit_index]
                shares_to_sell = position.shares * min(1.0, fraction)
                reason = f"take_profit_{int(level * 100)}"
                position.next_take_profit_index += 1

        if shares_to_sell <= 0:
            continue
        cash, sold_shares = _sell_position(cash, position, shares_to_sell, price, config.cost_rate)
        trades.append(_trade_row(date, symbol, "sell", sold_shares, price, reason, cash))
        if position.shares <= 1e-12:
            del positions[symbol]
    return cash


def _execute_pending_buys(
    date: pd.Timestamp,
    cash: float,
    positions: dict[str, _Position],
    panels: dict[str, pd.DataFrame],
    pending_buys: list[dict[str, float | str | pd.Timestamp]],
    config: B1BacktestConfig,
    trades: list[dict[str, float | str | pd.Timestamp]],
) -> float:
    if not pending_buys:
        return cash
    equity = _portfolio_value(cash, positions, panels, date)
    for candidate in pending_buys:
        symbol = str(candidate["symbol"])
        if symbol in positions or symbol not in panels or date not in panels[symbol].index:
            continue
        price = float(panels[symbol].loc[date, "close"])
        if pd.isna(price) or price <= 0:
            continue
        target_value = equity * float(candidate["target_weight"])
        spendable = min(cash / (1.0 + config.cost_rate), target_value)
        if spendable <= 0:
            continue
        shares = spendable / price
        cash -= spendable * (1.0 + config.cost_rate)
        positions[symbol] = _Position(shares=shares, cost_basis=price)
        trades.append(_trade_row(date, symbol, "buy", shares, price, "next_day_entry", cash))
    return cash


def run_b1_backtest(
    panels: dict[str, pd.DataFrame],
    market_frame: pd.DataFrame,
    config: B1BacktestConfig | None = None,
) -> B1BacktestResult:
    cfg = config or B1BacktestConfig()
    if len(cfg.take_profit_levels) != len(cfg.take_profit_fractions):
        raise ValueError("take_profit_levels and take_profit_fractions must have equal length")
    dates = pd.DatetimeIndex(market_frame.index).sort_values()
    if dates.empty:
        raise ValueError("market_frame has no dates")

    cash = cfg.initial_cash
    positions: dict[str, _Position] = {}
    pending_buys: list[dict[str, float | str | pd.Timestamp]] = []
    trades: list[dict[str, float | str | pd.Timestamp]] = []
    candidate_rows: list[dict[str, float | str | pd.Timestamp]] = []
    nav_values: list[float] = []

    for date in dates:
        cash = _process_sells(date, cash, positions, panels, cfg, trades)
        cash = _execute_pending_buys(date, cash, positions, panels, pending_buys, cfg, trades)
        pending_buys = []

        equity = _portfolio_value(cash, positions, panels, date)
        nav_values.append(equity / cfg.initial_cash)

        if market_allows_entry(market_frame, date):
            ranked = rank_b1_candidates(panels, date, cfg)
            for row in ranked:
                candidate_rows.append(row)
            pending_buys = [row for row in ranked if str(row["symbol"]) not in positions]

    nav = pd.Series(nav_values, index=dates, name="nav")
    trades_df = pd.DataFrame(trades) if trades else _empty_trades()
    candidates_df = pd.DataFrame(candidate_rows)
    metrics = calculate_metrics(nav)
    summary: dict[str, float | bool | str] = {
        "annual_return": metrics["annual_return"],
        "max_drawdown": metrics["max_drawdown"],
        "calmar": metrics["calmar"],
        "passes_return_gate": metrics["annual_return"] >= SATELLITE_TARGET_ANNUAL_RETURN,
        "passes_drawdown_gate": metrics["max_drawdown"] >= SATELLITE_MAX_DRAWDOWN_FLOOR,
        "trade_count": float(len(trades_df)),
        "candidate_count": float(len(candidates_df)),
    }
    return B1BacktestResult(nav=nav, trades=trades_df, candidates=candidates_df, metrics=metrics, summary=summary)


def normalize_a_share_code(symbol: str) -> str:
    return symbol.split(".")[0].zfill(6)


def a_share_tx_symbol(symbol: str) -> str:
    code = normalize_a_share_code(symbol)
    if code.startswith(("60", "68")):
        return f"sh{code}"
    return f"sz{code}"


def a_share_tushare_code(symbol: str) -> str:
    code = normalize_a_share_code(symbol)
    if code.startswith(("60", "68")):
        return f"{code}.SH"
    if code.startswith(("00", "30")):
        return f"{code}.SZ"
    if code.startswith(("43", "83", "87", "92")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def resolve_tushare_token(token: str | None = None, env: Mapping[str, str] | None = None) -> str:
    env_source = os.environ if env is None else env
    raw_token = token if token is not None else env_source.get("TUSHARE_TOKEN", "")
    resolved = raw_token.strip()
    if not resolved:
        raise RuntimeError("TUSHARE_TOKEN is required for the Tushare data provider")
    return resolved


def tushare_pro_api(token: str | None = None):
    import tushare as ts

    return ts.pro_api(resolve_tushare_token(token))


def normalize_tushare_bars(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        raise RuntimeError("Tushare returned empty A-share bars")
    required = {"trade_date", "open", "high", "low", "close"}
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(f"Tushare bars missing required columns: {sorted(missing)}")

    frame = raw.rename(columns={"trade_date": "date", "vol": "volume"}).copy()
    columns = ["date", "open", "high", "low", "close"]
    for optional in ["volume", "amount"]:
        if optional in frame.columns:
            columns.append(optional)
    bars = frame[columns].copy()
    bars["date"] = pd.to_datetime(bars["date"].astype(str), format="%Y%m%d", errors="coerce")
    bars = bars.dropna(subset=["date"]).sort_values("date").drop_duplicates("date", keep="last").set_index("date")
    for column in bars.columns:
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    return bars.astype(float)


def _read_normalized_bar_cache(cache_path: Path) -> pd.DataFrame:
    cached = pd.read_csv(cache_path, parse_dates=["date"])
    bars = cached.sort_values("date").drop_duplicates("date", keep="last").set_index("date")
    for column in bars.columns:
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    return bars.astype(float)


def fetch_a_share_bars_tushare(
    symbol: str,
    start: str,
    end: str,
    data_dir: Path,
    adjust: str = "qfq",
    refresh: bool = False,
    token: str | None = None,
) -> pd.DataFrame:
    data_dir.mkdir(parents=True, exist_ok=True)
    code = normalize_a_share_code(symbol)
    cache_path = data_dir / f"{code}_{start}_{end}_tushare_{adjust or 'raw'}.csv"
    if cache_path.exists() and not refresh:
        return _read_normalized_bar_cache(cache_path)

    import tushare as ts

    api = tushare_pro_api(token)
    raw = retry_call(
        lambda: ts.pro_bar(
            ts_code=a_share_tushare_code(code),
            api=api,
            start_date=start,
            end_date=end,
            freq="D",
            asset="E",
            adj=adjust or None,
        ),
        attempts=3,
        delay_seconds=0.8,
    )
    bars = normalize_tushare_bars(raw)
    bars.to_csv(cache_path, index_label="date")
    return bars


def fetch_tushare_index_bars(
    symbol: str,
    start: str,
    end: str,
    data_dir: Path,
    refresh: bool = False,
    token: str | None = None,
) -> pd.DataFrame:
    data_dir.mkdir(parents=True, exist_ok=True)
    ts_code = symbol.upper()
    cache_path = data_dir / f"{ts_code}_{start}_{end}_tushare_index.csv"
    if cache_path.exists() and not refresh:
        return _read_normalized_bar_cache(cache_path)

    api = tushare_pro_api(token)
    raw = retry_call(
        lambda: api.index_daily(ts_code=ts_code, start_date=start, end_date=end),
        attempts=3,
        delay_seconds=0.8,
    )
    bars = normalize_tushare_bars(raw)
    bars.to_csv(cache_path, index_label="date")
    return bars


def select_eligible_tushare_symbols(raw: pd.DataFrame, limit: int | None = None) -> list[str]:
    symbols: list[str] = []
    symbol_col = "symbol" if "symbol" in raw.columns else "ts_code"
    name_col = "name" if "name" in raw.columns else ""
    for _, row in raw.iterrows():
        symbol = normalize_a_share_code(str(row[symbol_col]))
        name = str(row[name_col]) if name_col else ""
        if is_eligible_a_share(symbol, name):
            symbols.append(symbol)
    return symbols[:limit] if limit else symbols


def load_a_share_symbols_tushare(limit: int | None = None, token: str | None = None) -> list[str]:
    api = tushare_pro_api(token)
    raw = retry_call(
        lambda: api.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,market,list_date",
        ),
        attempts=3,
        delay_seconds=0.8,
    )
    if raw.empty:
        raise RuntimeError("Tushare returned empty stock_basic universe")
    return select_eligible_tushare_symbols(raw, limit=limit)


def is_eligible_a_share(symbol: str, name: str = "") -> bool:
    code = normalize_a_share_code(symbol)
    if "ST" in name.upper() or "退" in name:
        return False
    return code.startswith(("00", "30", "60", "68"))


def fetch_a_share_bars(
    symbol: str,
    start: str,
    end: str,
    data_dir: Path,
    adjust: str = "qfq",
    refresh: bool = False,
) -> pd.DataFrame:
    import akshare as ak

    data_dir.mkdir(parents=True, exist_ok=True)
    code = normalize_a_share_code(symbol)
    cache_path = data_dir / f"{code}_{start}_{end}_{adjust or 'raw'}.csv"
    if cache_path.exists() and not refresh:
        raw = pd.read_csv(cache_path, parse_dates=["date"])
    else:
        try:
            raw = retry_call(
                lambda: ak.stock_zh_a_hist_tx(
                    symbol=a_share_tx_symbol(code),
                    start_date=start,
                    end_date=end,
                    adjust=adjust,
                ),
                attempts=3,
                delay_seconds=0.8,
            )
        except Exception:
            raw = retry_call(
                lambda: ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust=adjust),
                attempts=3,
                delay_seconds=0.8,
            )
        if raw.empty:
            raise RuntimeError(f"AkShare returned empty A-share bars for {code}")
        raw = raw.rename(
            columns={
                "日期": "date",
                "开盘": "open",
                "最高": "high",
                "最低": "low",
                "收盘": "close",
                "成交量": "volume",
                "成交额": "amount",
            }
        )
        raw.to_csv(cache_path, index=False)
    bars = raw[["date", "open", "high", "low", "close"]].copy()
    bars["date"] = pd.to_datetime(bars["date"])
    return bars.sort_values("date").set_index("date").astype(float)


def load_a_share_symbols(
    limit: int | None = None,
    data_provider: str = "akshare",
    token: str | None = None,
) -> list[str]:
    if data_provider == "tushare":
        return load_a_share_symbols_tushare(limit=limit, token=token)
    if data_provider != "akshare":
        raise ValueError(f"Unknown A-share data provider: {data_provider}")

    import akshare as ak

    codes = ak.stock_info_a_code_name()
    code_col = "code" if "code" in codes.columns else "代码"
    name_col = "name" if "name" in codes.columns else "名称"
    symbols = [
        normalize_a_share_code(str(row[code_col]))
        for _, row in codes.iterrows()
        if is_eligible_a_share(str(row[code_col]), str(row[name_col]))
    ]
    return symbols[:limit] if limit else symbols


def build_b1_panels(
    symbols: Iterable[str],
    start: str,
    end: str,
    data_dir: Path,
    config: B1BacktestConfig | None = None,
    refresh: bool = False,
    data_provider: str = "akshare",
    token: str | None = None,
) -> dict[str, pd.DataFrame]:
    panels: dict[str, pd.DataFrame] = {}
    if data_provider not in {"akshare", "tushare"}:
        raise ValueError(f"Unknown A-share data provider: {data_provider}")
    for symbol in symbols:
        try:
            if data_provider == "tushare":
                bars = fetch_a_share_bars_tushare(symbol, start=start, end=end, data_dir=data_dir, refresh=refresh, token=token)
            else:
                bars = fetch_a_share_bars(symbol, start=start, end=end, data_dir=data_dir, refresh=refresh)
            if len(bars) >= max((config or B1BacktestConfig()).bbi_windows):
                panels[normalize_a_share_code(symbol)] = compute_b1_frame(bars, config)
        except Exception:
            continue
    return panels


def build_b1_summary_markdown(result: B1BacktestResult, start: str, end: str, symbol_count: int) -> str:
    summary = result.summary
    lines = [
        "# B1 Trend Pullback Replica",
        "",
        f"- Evaluation window: `{start}` to `{end}`.",
        f"- Symbols loaded: `{symbol_count}`.",
        f"- Annual return: `{float(summary['annual_return']) * 100:.2f}%`.",
        f"- Max drawdown: `{float(summary['max_drawdown']) * 100:.2f}%`.",
        f"- Calmar: `{float(summary['calmar']):.2f}`.",
        f"- Trades: `{int(summary['trade_count'])}`.",
        f"- Candidates: `{int(summary['candidate_count'])}`.",
        f"- Passes 50% annual gate: `{summary['passes_return_gate']}`.",
        f"- Passes -30% drawdown gate: `{summary['passes_drawdown_gate']}`.",
        "",
        "This is a local B1 proxy replica. It is not a full reproduction of the screenshot platform until B1 score and sell rules are matched to platform trade details.",
    ]
    return "\n".join(lines) + "\n"


def write_b1_artifacts(
    result: B1BacktestResult,
    start: str,
    end: str,
    symbol_count: int,
    results_dir: Path = RESULTS_DIR,
    artifact_prefix: str = "b1_trend_pullback",
) -> dict[str, str]:
    import json

    results_dir.mkdir(parents=True, exist_ok=True)
    nav_path = results_dir / f"{artifact_prefix}_nav.csv"
    trades_path = results_dir / f"{artifact_prefix}_trades.csv"
    candidates_path = results_dir / f"{artifact_prefix}_candidates.csv"
    summary_path = results_dir / f"{artifact_prefix}_summary.md"
    manifest_path = results_dir / f"{artifact_prefix}_manifest.json"

    result.nav.rename("nav").to_csv(nav_path, index_label="date")
    result.trades.to_csv(trades_path, index=False)
    result.candidates.to_csv(candidates_path, index=False)
    summary_path.write_text(build_b1_summary_markdown(result, start, end, symbol_count), encoding="utf-8")
    manifest = {
        "strategy": "b1_trend_pullback_replica",
        "start": start,
        "end": end,
        "symbol_count": symbol_count,
        "annual_return": result.summary["annual_return"],
        "max_drawdown": result.summary["max_drawdown"],
        "passes_return_gate": result.summary["passes_return_gate"],
        "passes_drawdown_gate": result.summary["passes_drawdown_gate"],
        "artifacts": {
            "nav": nav_path.name,
            "trades": trades_path.name,
            "candidates": candidates_path.name,
            "summary": summary_path.name,
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "nav": str(nav_path),
        "trades": str(trades_path),
        "candidates": str(candidates_path),
        "summary": str(summary_path),
        "manifest": str(manifest_path),
    }
