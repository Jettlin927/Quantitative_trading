from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from my_quant.strategy_research.experiment.b1_trend_pullback import (
    B1BacktestConfig,
    build_b1_panels,
    calculate_bbi,
    fetch_tushare_index_bars,
    load_a_share_symbols,
    run_b1_backtest,
    write_b1_artifacts,
)
from my_quant.strategy_research.experiment.config import DATA_DIR, RESULTS_DIR


def _compact_date(value: str) -> str:
    return value.replace("-", "")


def apply_market_regime_filter(market: pd.DataFrame, require_ma20_gt_ma60: bool = False) -> pd.DataFrame:
    filtered = market.copy()
    if require_ma20_gt_ma60:
        allowed = (filtered["ma20"] > filtered["ma60"]).fillna(False)
        filtered.loc[~allowed, "bbi"] = filtered.loc[~allowed, "close"] * 2.0
    return filtered


def build_market_frame(
    history_start: str,
    end: str,
    eval_start: str,
    data_provider: str = "akshare",
    data_dir: Path = DATA_DIR / "b1_a_share",
    require_ma20_gt_ma60: bool = False,
) -> pd.DataFrame:
    if data_provider == "tushare":
        bars = fetch_tushare_index_bars("000300.SH", _compact_date(history_start), _compact_date(end), data_dir)
        close = bars["close"].rename("close")
    elif data_provider == "akshare":
        from my_quant.strategy_research.experiment.data import load_prices

        prices = load_prices(
            symbols=["510300"],
            start=history_start,
            end=end,
            dropna=True,
            repair_splits=True,
        )
        close = prices["510300"].rename("close")
    else:
        raise ValueError(f"Unknown market data provider: {data_provider}")
    market = pd.DataFrame({"close": close})
    market["bbi"] = calculate_bbi(market["close"])
    market["ma20"] = market["close"].rolling(20, min_periods=20).mean()
    market["ma60"] = market["close"].rolling(60, min_periods=60).mean()
    return apply_market_regime_filter(market.loc[eval_start:end], require_ma20_gt_ma60=require_ma20_gt_ma60)


def select_symbol_sample(symbols: list[str], limit: int | None, offset: int = 0, stride: int = 1) -> list[str]:
    if stride <= 0:
        raise ValueError("stride must be positive")
    if offset < 0:
        raise ValueError("offset must be non-negative")
    selected = symbols[offset::stride]
    return selected[:limit] if limit else selected


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the A-share B1 trend-pullback replica experiment.")
    parser.add_argument("--start", default="2025-01-01", help="Evaluation start date, YYYY-MM-DD.")
    parser.add_argument("--end", default="2026-05-15", help="Evaluation end date, YYYY-MM-DD.")
    parser.add_argument("--history-start", default="2024-06-01", help="History start date for indicators, YYYY-MM-DD.")
    parser.add_argument("--max-symbols", type=int, default=100, help="Limit A-share symbols for smoke/probe runs.")
    parser.add_argument("--offset", type=int, default=0, help="Offset into eligible A-share symbols before sampling.")
    parser.add_argument("--stride", type=int, default=1, help="Stride through eligible A-share symbols for broad samples.")
    parser.add_argument("--data-provider", choices=["akshare", "tushare"], default="akshare", help="A-share stock data provider.")
    parser.add_argument("--refresh", action="store_true", help="Refresh cached stock bars from the selected provider.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "b1_a_share", help="Directory for A-share bar cache.")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR, help="Directory for result artifacts.")
    parser.add_argument("--output-prefix", default="", help="Artifact prefix. Defaults to sample settings.")
    parser.add_argument("--market-ma20-gt-ma60", action="store_true", help="Require market MA20 > MA60 for new entries.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = B1BacktestConfig()
    symbols = select_symbol_sample(
        load_a_share_symbols(data_provider=args.data_provider),
        limit=args.max_symbols,
        offset=args.offset,
        stride=args.stride,
    )
    panels = build_b1_panels(
        symbols=symbols,
        start=_compact_date(args.history_start),
        end=_compact_date(args.end),
        data_dir=args.data_dir,
        config=config,
        refresh=args.refresh,
        data_provider=args.data_provider,
    )
    market = build_market_frame(
        args.history_start,
        args.end,
        args.start,
        args.data_provider,
        args.data_dir,
        require_ma20_gt_ma60=args.market_ma20_gt_ma60,
    )
    result = run_b1_backtest(panels, market, config)
    prefix = args.output_prefix or f"b1_trend_pullback_m{args.max_symbols or 'all'}_o{args.offset}_s{args.stride}"
    paths = write_b1_artifacts(result, args.start, args.end, len(panels), args.results_dir, artifact_prefix=prefix)

    print(f"B1 trend-pullback replica complete: {len(panels)} symbols")
    print(f"annual_return={float(result.summary['annual_return']) * 100:.2f}%")
    print(f"max_drawdown={float(result.summary['max_drawdown']) * 100:.2f}%")
    print(f"summary={paths['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
