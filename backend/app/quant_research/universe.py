from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

import pandas as pd


def build_explicit_universe(
    ts_codes: Iterable[str],
    as_of_date: object | None = None,
    source: str = "explicit",
) -> dict[str, Any]:
    members = sorted({str(code).strip().upper() for code in ts_codes if str(code).strip()})
    if not members:
        raise ValueError("显式 universe 不能为空")
    normalized_as_of = pd.Timestamp(as_of_date).date().isoformat() if as_of_date is not None else None
    payload = {
        "mode": "explicit_snapshot",
        "source": source,
        "asOfDate": normalized_as_of,
        "members": members,
    }
    payload["universeHash"] = _canonical_hash(payload)
    return payload


def build_historical_membership_panel(
    memberships: pd.DataFrame,
    trade_dates: Iterable[object],
    index_code: str,
) -> pd.DataFrame:
    required = {"index_code", "con_code", "in_date", "out_date"}
    missing = sorted(required - set(memberships.columns))
    if missing:
        raise ValueError(f"历史成员缺少字段：{', '.join(missing)}")
    dates = pd.DatetimeIndex(pd.to_datetime(list(trade_dates))).drop_duplicates().sort_values()
    frame = memberships[memberships["index_code"] == index_code].copy()
    frame["in_date"] = pd.to_datetime(frame["in_date"])
    frame["out_date"] = pd.to_datetime(frame["out_date"])
    rows: list[dict[str, object]] = []
    for member in frame.itertuples(index=False):
        active_dates = dates[(dates >= member.in_date) & (pd.isna(member.out_date) | (dates <= member.out_date))]
        rows.extend({"trade_date": trade_date, "ts_code": str(member.con_code)} for trade_date in active_dates)
    if not rows:
        return pd.DataFrame(columns=["trade_date", "ts_code"])
    return (
        pd.DataFrame(rows)
        .drop_duplicates(["trade_date", "ts_code"])
        .sort_values(["trade_date", "ts_code"])
        .reset_index(drop=True)
    )


def evaluate_universe_provenance(
    universe: dict[str, Any],
    scope: str,
    research_start: object,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    mode = universe.get("mode")
    if mode == "explicit_snapshot":
        if scope == "a_share_cross_section":
            as_of_date = universe.get("asOfDate")
            if not as_of_date:
                blockers.append("missing_as_of_date")
            elif pd.Timestamp(as_of_date) > pd.Timestamp(research_start):
                blockers.append("survivorship_risk")
            else:
                warnings.append("static_universe")
    elif mode == "historical_membership":
        if not universe.get("source"):
            blockers.append("missing_membership_source")
    else:
        blockers.append("unsupported_universe_mode")
    status = "blocked" if blockers else "ready_with_warnings" if warnings else "ready"
    return {"status": status, "blockers": blockers, "warnings": warnings}


def _canonical_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
