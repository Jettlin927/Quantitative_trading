from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any


US_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WATCHLIST = US_ROOT / "config" / "watchlist_symbols.csv"
DEFAULT_JSON = US_ROOT / "data" / "snapshots" / "us_snapshot_latest.json"
DEFAULT_CSV = US_ROOT / "data" / "snapshots" / "us_snapshot_latest.csv"
SNAPSHOT_FIELDS = [
    "ticker",
    "name",
    "role",
    "theme",
    "subtheme",
    "instrument_type",
    "leverage_factor",
    "risk_tag",
    "notes",
    "source",
    "fetched_at",
    "is_stale",
    "stale_reason",
    "latest_date",
    "close",
    "ma20",
    "ma50",
    "ma200",
    "pct_from_52w_high",
    "return_20d_pct",
    "return_60d_pct",
    "volatility_20d_pct",
]


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def parse_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(str(value).replace(",", ""))
        if math.isnan(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def average_last(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current / previous - 1) * 100


def compute_history_metrics(bars: list[dict[str, Any]]) -> dict[str, Any]:
    clean = [
        {
            "date": str(row.get("date", "")),
            "close": parse_float(row.get("close")),
            "high": parse_float(row.get("high")) or parse_float(row.get("close")),
        }
        for row in bars
    ]
    clean = [row for row in clean if row["date"] and row["close"] is not None]
    clean.sort(key=lambda row: row["date"])
    if not clean:
        raise ValueError("no usable yfinance bars")

    closes = [float(row["close"]) for row in clean]
    highs = [float(row["high"] or row["close"]) for row in clean]
    latest_close = closes[-1]
    high_52w = max(highs[-252:])
    daily_returns = [
        closes[index] / closes[index - 1] - 1
        for index in range(1, len(closes))
        if closes[index - 1] != 0
    ]
    last_20_returns = daily_returns[-20:]
    volatility_20d = None
    if len(last_20_returns) >= 2:
        volatility_20d = statistics.stdev(last_20_returns) * math.sqrt(252) * 100

    return {
        "latest_date": clean[-1]["date"],
        "close": latest_close,
        "ma20": average_last(closes, 20),
        "ma50": average_last(closes, 50),
        "ma200": average_last(closes, 200),
        "pct_from_52w_high": pct_change(latest_close, high_52w),
        "return_20d_pct": pct_change(latest_close, closes[-21] if len(closes) > 20 else None),
        "return_60d_pct": pct_change(latest_close, closes[-61] if len(closes) > 60 else None),
        "volatility_20d_pct": volatility_20d,
    }


def normalize_watchlist_row(row: dict[str, Any]) -> dict[str, Any]:
    ticker = str(row.get("ticker", "")).strip().upper()
    return {
        "ticker": ticker,
        "name": str(row.get("name", "")).strip(),
        "role": str(row.get("role", "")).strip(),
        "theme": str(row.get("theme", "")).strip(),
        "subtheme": str(row.get("subtheme", "")).strip(),
        "instrument_type": str(row.get("instrument_type", "")).strip(),
        "leverage_factor": parse_float(row.get("leverage_factor")) or 1.0,
        "risk_tag": str(row.get("risk_tag", "")).strip(),
        "notes": str(row.get("notes", "")).strip(),
    }


def stale_row(item: dict[str, Any], fetched_at: dt.datetime, reason: str) -> dict[str, Any]:
    row = normalize_watchlist_row(item)
    row.update({
        "source": "yfinance",
        "fetched_at": fetched_at.isoformat(),
        "is_stale": True,
        "stale_reason": reason,
        "latest_date": "",
        "close": None,
        "ma20": None,
        "ma50": None,
        "ma200": None,
        "pct_from_52w_high": None,
        "return_20d_pct": None,
        "return_60d_pct": None,
        "volatility_20d_pct": None,
    })
    return row


def build_snapshot(
    watchlist: list[dict[str, Any]],
    history_by_ticker: dict[str, Any],
    fetched_at: dt.datetime | None = None,
) -> dict[str, Any]:
    fetched_at = fetched_at or dt.datetime.now(dt.timezone.utc)
    symbols: list[dict[str, Any]] = []
    ok_count = 0
    stale_count = 0

    for raw_item in watchlist:
        item = normalize_watchlist_row(raw_item)
        if not item["ticker"]:
            continue
        history = history_by_ticker.get(item["ticker"])
        if isinstance(history, BaseException):
            symbols.append(stale_row(item, fetched_at, f"{type(history).__name__}: {history}"))
            stale_count += 1
            continue
        try:
            metrics = compute_history_metrics(history or [])
        except Exception as exc:  # noqa: BLE001 - data provider failures are represented per symbol.
            symbols.append(stale_row(item, fetched_at, f"{type(exc).__name__}: {exc}"))
            stale_count += 1
            continue

        row = dict(item)
        row.update(metrics)
        row.update({
            "source": "yfinance",
            "fetched_at": fetched_at.isoformat(),
            "is_stale": False,
            "stale_reason": "",
        })
        symbols.append(row)
        ok_count += 1

    if not symbols or ok_count == 0:
        status = "stale"
    elif stale_count:
        status = "partial"
    else:
        status = "ok"

    return {
        "status": status,
        "source": "yfinance",
        "fetched_at": fetched_at.isoformat(),
        "symbol_count": len(symbols),
        "ok_count": ok_count,
        "stale_count": stale_count,
        "symbols": symbols,
    }


def yfinance_frame_to_bars(frame: Any) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", True):
        return []
    bars: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        date_value = getattr(index, "date", lambda: index)()
        date_text = date_value.isoformat() if hasattr(date_value, "isoformat") else str(index)[:10]
        close = parse_float(row.get("Close"))
        if close is None:
            continue
        bars.append({
            "date": date_text,
            "open": parse_float(row.get("Open")),
            "high": parse_float(row.get("High")) or close,
            "low": parse_float(row.get("Low")) or close,
            "close": close,
            "volume": parse_float(row.get("Volume")),
        })
    return bars


def fetch_yfinance_histories(watchlist: list[dict[str, Any]], period: str, interval: str, sleep_seconds: float) -> dict[str, Any]:
    import yfinance as yf  # type: ignore[import-not-found]

    histories: dict[str, Any] = {}
    for item in watchlist:
        ticker = normalize_watchlist_row(item)["ticker"]
        if not ticker:
            continue
        try:
            frame = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
            histories[ticker] = yfinance_frame_to_bars(frame)
        except Exception as exc:  # noqa: BLE001 - provider errors are captured in snapshot output.
            histories[ticker] = exc
        time.sleep(max(sleep_seconds, 0))
    return histories


def write_snapshot(snapshot: dict[str, Any], json_path: Path, csv_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SNAPSHOT_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in snapshot.get("symbols", []):
            writer.writerow({field: row.get(field, "") for field in SNAPSHOT_FIELDS})


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh sample US watchlist snapshot with yfinance.")
    parser.add_argument("--watchlist", type=Path, default=DEFAULT_WATCHLIST)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--period", default="1y")
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--sleep", type=float, default=0.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    watchlist = [normalize_watchlist_row(row) for row in read_csv_rows(args.watchlist)]
    histories = fetch_yfinance_histories(watchlist, period=args.period, interval=args.interval, sleep_seconds=args.sleep)
    snapshot = build_snapshot(watchlist, histories)
    write_snapshot(snapshot, json_path=args.json, csv_path=args.csv)
    print(f"wrote {args.json}")
    print(f"wrote {args.csv}")
    print(f"status={snapshot['status']} ok={snapshot['ok_count']} stale={snapshot['stale_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
