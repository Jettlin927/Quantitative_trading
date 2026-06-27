from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def build_us_research_overview(repo_root: Path) -> dict[str, Any]:
    us_root = repo_root / "my_quant" / "us_research"
    watchlist_path = us_root / "config" / "watchlist_symbols.csv"
    holdings_path = us_root / "data" / "holdings_sample.csv"
    snapshot_path = us_root / "data" / "snapshots" / "us_snapshot_latest.json"

    watchlist = [normalize_watchlist_row(row, watchlist_path) for row in read_csv_rows(watchlist_path)]
    holdings = [normalize_holding_row(row, holdings_path) for row in read_csv_rows(holdings_path)]
    snapshot = read_json_dict(snapshot_path, default={"status": "missing", "source": None, "symbols": []})

    snapshot_by_ticker = {str(row.get("ticker", "")).upper(): row for row in snapshot.get("symbols", []) if row.get("ticker")}
    holding_by_ticker = {row["ticker"]: row for row in holdings}
    assets = [build_asset_contract(row, snapshot_by_ticker, holding_by_ticker) for row in watchlist]

    portfolio_snapshots = [
        {
            "snapshotId": "sample-latest",
            "source": "holdings_sample.csv",
            "sourcePath": relative_display_path(holdings_path, repo_root),
            "isSample": True,
            "holdingCount": len(holdings),
            "totalSampleCostBasis": sum(value_or_zero(row.get("sampleCostBasis")) for row in holdings),
            "holdings": holdings,
        }
    ]

    return {
        "source": "file-sample",
        "isSample": True,
        "updatedAt": snapshot.get("fetched_at"),
        "dataBoundary": {
            "brokerConnected": False,
            "realHoldingsImported": False,
            "dbPersistence": "pending_confirmation",
            "executionEnabled": False,
            "notes": "Only sample files under my_quant/us_research are exposed. No broker export or real account data is imported.",
        },
        "assets": assets,
        "watchlist": watchlist,
        "portfolioSnapshots": portfolio_snapshots,
        "marketSnapshot": {
            "status": snapshot.get("status", "missing"),
            "source": snapshot.get("source"),
            "fetchedAt": snapshot.get("fetched_at"),
            "symbolCount": snapshot.get("symbol_count", len(snapshot.get("symbols", []))),
            "okCount": snapshot.get("ok_count"),
            "staleCount": snapshot.get("stale_count"),
            "symbols": snapshot.get("symbols", []),
        },
        "evidenceFiles": {
            "watchlist": relative_display_path(watchlist_path, repo_root),
            "holdingsSample": relative_display_path(holdings_path, repo_root),
            "snapshot": relative_display_path(snapshot_path, repo_root),
        },
    }


def build_us_research_import_preview(repo_root: Path) -> dict[str, Any]:
    overview = build_us_research_overview(repo_root)
    assets = [build_asset_import_record(asset) for asset in overview["assets"]]
    asset_daily_prices = [build_daily_price_import_record(symbol) for symbol in overview["marketSnapshot"].get("symbols", []) if symbol.get("latest_date")]
    watchlist_items = [build_watchlist_import_record(item) for item in overview["watchlist"]]
    portfolio_snapshots = [build_portfolio_snapshot_import_record(snapshot) for snapshot in overview["portfolioSnapshots"]]
    records = {
        "assets": assets,
        "assetDailyPrices": asset_daily_prices,
        "watchlistItems": watchlist_items,
        "portfolioSnapshots": portfolio_snapshots,
    }

    target_tables = [
        {"table": "assets", "uniqueKey": ["market", "symbol"], "rowCount": len(assets)},
        {"table": "asset_daily_prices", "uniqueKey": ["asset_natural_key", "trade_date"], "rowCount": len(asset_daily_prices)},
        {"table": "watchlist_items", "uniqueKey": ["watchlist_name", "asset_natural_key"], "rowCount": len(watchlist_items)},
        {"table": "portfolio_snapshots", "uniqueKey": ["snapshot_id"], "rowCount": len(portfolio_snapshots)},
    ]

    return {
        "source": overview["source"],
        "mode": "preview",
        "isSample": True,
        "writesEnabled": False,
        "requiresConfirmation": False,
        "importEndpoint": "POST /api/us-research/import-sample",
        "summary": {name: len(rows) for name, rows in records.items()},
        "targetTables": target_tables,
        "records": records,
        "validation": {
            "brokerConnected": False,
            "realHoldingsImported": False,
            "executionEnabled": False,
            "canExecute": True,
            "dbSchema": "ready",
            "blockers": [],
        },
        "evidenceFiles": overview["evidenceFiles"],
    }


def build_asset_import_record(asset: dict[str, Any]) -> dict[str, Any]:
    natural_key = f"US:{asset['ticker']}"
    return {
        "naturalKey": natural_key,
        "market": "US",
        "symbol": asset["ticker"],
        "name": asset.get("name"),
        "instrumentType": asset.get("instrumentType"),
        "leverageFactor": asset.get("leverageFactor"),
        "riskTag": asset.get("riskTag"),
        "theme": asset.get("theme"),
        "isSample": True,
        "source": asset.get("source"),
    }


def build_daily_price_import_record(symbol: dict[str, Any]) -> dict[str, Any]:
    ticker = str(symbol.get("ticker", "")).upper()
    trade_date = symbol.get("latest_date")
    natural_key = f"US:{ticker}:{trade_date}"
    return {
        "naturalKey": natural_key,
        "assetNaturalKey": f"US:{ticker}",
        "tradeDate": trade_date,
        "close": float_or_none(symbol.get("close")),
        "ma20": float_or_none(symbol.get("ma20")),
        "ma50": float_or_none(symbol.get("ma50")),
        "ma200": float_or_none(symbol.get("ma200")),
        "return20dPct": float_or_none(symbol.get("return_20d_pct")),
        "return60dPct": float_or_none(symbol.get("return_60d_pct")),
        "volatility20dPct": float_or_none(symbol.get("volatility_20d_pct")),
        "isSample": True,
        "source": symbol.get("source") or "yfinance",
        "isStale": bool(symbol.get("is_stale", False)),
    }


def build_watchlist_import_record(item: dict[str, Any]) -> dict[str, Any]:
    natural_key = f"sample-watchlist:US:{item['ticker']}"
    return {
        "naturalKey": natural_key,
        "watchlistName": "sample-watchlist",
        "assetNaturalKey": f"US:{item['ticker']}",
        "role": item.get("role"),
        "theme": item.get("theme"),
        "subtheme": item.get("subtheme"),
        "riskTag": item.get("riskTag"),
        "notes": item.get("notes"),
        "isSample": True,
        "source": item.get("source"),
    }


def build_portfolio_snapshot_import_record(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "snapshotId": snapshot["snapshotId"],
        "source": snapshot.get("source"),
        "isSample": True,
        "holdingCount": snapshot.get("holdingCount", 0),
        "totalSampleCostBasis": snapshot.get("totalSampleCostBasis", 0.0),
        "holdings": [
            {
                "assetNaturalKey": f"US:{holding['ticker']}",
                "ticker": holding["ticker"],
                "sampleQuantity": holding.get("sampleQuantity"),
                "sampleCostBasis": holding.get("sampleCostBasis"),
                "riskTag": holding.get("riskTag"),
            }
            for holding in snapshot.get("holdings", [])
        ],
    }


def build_asset_contract(
    watchlist_row: dict[str, Any],
    snapshot_by_ticker: dict[str, dict[str, Any]],
    holding_by_ticker: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    ticker = watchlist_row["ticker"]
    snapshot = snapshot_by_ticker.get(ticker, {})
    holding = holding_by_ticker.get(ticker, {})
    return {
        **watchlist_row,
        "latestDate": snapshot.get("latest_date"),
        "latestClose": float_or_none(snapshot.get("close")),
        "ma20": float_or_none(snapshot.get("ma20")),
        "ma50": float_or_none(snapshot.get("ma50")),
        "ma200": float_or_none(snapshot.get("ma200")),
        "return20dPct": float_or_none(snapshot.get("return_20d_pct")),
        "return60dPct": float_or_none(snapshot.get("return_60d_pct")),
        "volatility20dPct": float_or_none(snapshot.get("volatility_20d_pct")),
        "isStale": bool(snapshot.get("is_stale", False)),
        "staleReason": snapshot.get("stale_reason") or "",
        "sampleQuantity": holding.get("sampleQuantity"),
        "sampleCostBasis": holding.get("sampleCostBasis"),
    }


def normalize_watchlist_row(row: dict[str, str], path: Path) -> dict[str, Any]:
    return {
        "ticker": row.get("ticker", "").strip().upper(),
        "name": row.get("name", "").strip(),
        "role": row.get("role", "").strip(),
        "theme": row.get("theme", "").strip(),
        "subtheme": row.get("subtheme", "").strip(),
        "instrumentType": row.get("instrument_type", "").strip(),
        "leverageFactor": float_or_none(row.get("leverage_factor")),
        "riskTag": row.get("risk_tag", "").strip(),
        "notes": row.get("notes", "").strip(),
        "source": "watchlist_symbols.csv",
        "sourcePath": path.name,
        "isSample": True,
    }


def normalize_holding_row(row: dict[str, str], path: Path) -> dict[str, Any]:
    return {
        "ticker": row.get("ticker", "").strip().upper(),
        "instrumentType": row.get("instrument_type", "").strip(),
        "sampleQuantity": float_or_none(row.get("quantity")),
        "sampleCostBasis": float_or_none(row.get("cost_basis")),
        "theme": row.get("theme", "").strip(),
        "leverageFactor": float_or_none(row.get("leverage_factor")),
        "riskTag": row.get("risk_tag", "").strip(),
        "notes": row.get("notes", "").strip(),
        "source": "holdings_sample.csv",
        "sourcePath": path.name,
        "isSample": True,
    }


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_json_dict(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else default


def float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def value_or_zero(value: Any) -> float:
    numeric = float_or_none(value)
    return numeric if numeric is not None else 0.0


def relative_display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
