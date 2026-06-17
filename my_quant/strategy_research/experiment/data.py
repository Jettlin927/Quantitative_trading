from __future__ import annotations

from pathlib import Path

import akshare as ak
import pandas as pd

from .config import ALL_ASSETS, DATA_DIR, EVAL_END, TRAIN_START


def sina_symbol(symbol: str) -> str:
    if symbol.startswith(("15", "16")):
        return f"sz{symbol}"
    return f"sh{symbol}"


def repair_large_price_jumps(close: pd.Series, threshold: float = 0.35) -> pd.Series:
    repaired = close.sort_index().astype(float).copy()
    for i in range(1, len(repaired)):
        prev = repaired.iloc[i - 1]
        current = repaired.iloc[i]
        if pd.isna(prev) or pd.isna(current) or prev <= 0:
            continue
        ratio = current / prev
        if ratio < 1 - threshold or ratio > 1 / (1 - threshold):
            repaired.iloc[:i] *= ratio
    return repaired


def fetch_close(symbol: str, data_dir: Path = DATA_DIR) -> pd.Series:
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_path = data_dir / f"{symbol}.csv"
    if cache_path.exists():
        df = pd.read_csv(cache_path, parse_dates=["date"])
    else:
        request_symbol = sina_symbol(symbol)
        df = ak.fund_etf_hist_sina(symbol=request_symbol)
        if df.empty:
            raise RuntimeError(f"AkShare returned empty data for {request_symbol}")
        df["date"] = pd.to_datetime(df["date"])
        df.to_csv(cache_path, index=False)

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    close = df.set_index("date")["close"].astype(float)
    close.name = symbol
    return close


def load_prices(
    symbols: list[str] | None = None,
    data_dir: Path = DATA_DIR,
    start: str = TRAIN_START,
    end: str = EVAL_END,
    dropna: bool = True,
    required_symbols: list[str] | None = None,
    repair_splits: bool = False,
) -> pd.DataFrame:
    selected_symbols = symbols or ALL_ASSETS
    prices = pd.concat([fetch_close(symbol, data_dir) for symbol in selected_symbols], axis=1)
    prices = prices.sort_index().ffill()
    if repair_splits:
        prices = prices.apply(repair_large_price_jumps)
    prices = prices.loc[start:end]
    if dropna:
        return prices.dropna()
    if required_symbols:
        return prices.dropna(subset=required_symbols)
    return prices.dropna(how="all")
