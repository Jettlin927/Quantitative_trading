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
    normalized_source = str(source).strip()
    if not normalized_source:
        raise ValueError("显式 universe 必须记录 source")
    normalized_as_of = pd.Timestamp(as_of_date).date().isoformat() if as_of_date is not None else None
    member_artifact = {
        "format": "inline_sorted_symbols",
        "count": len(members),
        "sha256": _canonical_hash(members),
    }
    payload = {
        "mode": "explicit_snapshot",
        "source": normalized_source,
        "asOfDate": normalized_as_of,
        "members": members,
        "memberArtifact": member_artifact,
    }
    payload["universeHash"] = _canonical_hash(payload)
    return payload


def build_historical_membership_panel(
    memberships: pd.DataFrame,
    listings: pd.DataFrame,
    trade_dates: Iterable[object],
    index_code: str,
) -> pd.DataFrame:
    membership_required = {"index_code", "con_code", "in_date", "out_date"}
    listing_required = {"ts_code", "list_date", "delist_date"}
    missing_membership = sorted(membership_required - set(memberships.columns))
    missing_listing = sorted(listing_required - set(listings.columns))
    if missing_membership:
        raise ValueError(f"历史成员缺少字段：{', '.join(missing_membership)}")
    if missing_listing:
        raise ValueError(f"上市边界缺少字段：{', '.join(missing_listing)}")

    dates = _normalize_dates(trade_dates, "历史 universe 交易日历")
    frame = memberships[memberships["index_code"] == index_code].copy()
    frame["con_code"] = frame["con_code"].astype(str).str.upper()
    frame["in_date"] = pd.to_datetime(frame["in_date"])
    frame["out_date"] = pd.to_datetime(frame["out_date"])
    listing_frame = listings.copy()
    listing_frame["ts_code"] = listing_frame["ts_code"].astype(str).str.upper()
    listing_frame["list_date"] = pd.to_datetime(listing_frame["list_date"])
    listing_frame["delist_date"] = pd.to_datetime(listing_frame["delist_date"])
    if listing_frame["ts_code"].duplicated().any():
        raise ValueError("上市边界存在重复 ts_code")
    listing_by_code = listing_frame.set_index("ts_code")
    missing_codes = sorted(set(frame["con_code"]) - set(listing_by_code.index))
    if missing_codes:
        raise ValueError(f"历史成员缺少上市/退市边界：{', '.join(missing_codes[:10])}")

    rows: list[dict[str, object]] = []
    for member in frame.itertuples(index=False):
        listing = listing_by_code.loc[member.con_code]
        active = dates[(dates >= member.in_date) & (dates >= listing.list_date)]
        if pd.notna(member.out_date):
            active = active[active <= member.out_date]
        if pd.notna(listing.delist_date):
            active = active[active <= listing.delist_date]
        rows.extend({"trade_date": trade_date, "ts_code": member.con_code} for trade_date in active)
    if not rows:
        return pd.DataFrame(columns=["trade_date", "ts_code"])
    return (
        pd.DataFrame(rows)
        .drop_duplicates(["trade_date", "ts_code"])
        .sort_values(["trade_date", "ts_code"])
        .reset_index(drop=True)
    )


def build_historical_universe(
    memberships: pd.DataFrame,
    listings: pd.DataFrame,
    trade_dates: Iterable[object],
    index_code: str,
    source: str,
) -> dict[str, Any]:
    normalized_source = str(source).strip()
    if not normalized_source:
        raise ValueError("历史 universe 必须记录 source")
    dates = _normalize_dates(trade_dates, "历史 universe 交易日历")
    panel = build_historical_membership_panel(memberships, listings, dates, index_code)
    if panel.empty:
        raise ValueError("历史 universe 成员工件为空")
    records = [
        {"tradeDate": row.trade_date.date().isoformat(), "tsCode": str(row.ts_code)}
        for row in panel.itertuples(index=False)
    ]
    member_artifact = {
        "format": "inline_daily_membership",
        "count": len(records),
        "sha256": _canonical_hash(records),
        "records": records,
    }
    payload = {
        "mode": "historical_membership",
        "source": normalized_source,
        "indexCode": str(index_code),
        "startDate": dates[0].date().isoformat(),
        "endDate": dates[-1].date().isoformat(),
        "memberArtifact": member_artifact,
    }
    payload["universeHash"] = _canonical_hash(payload)
    return payload


def evaluate_universe_provenance(
    universe: dict[str, Any],
    scope: str,
    research_start: object,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    survivorship_risk = False
    if not isinstance(universe, dict):
        return {
            "status": "blocked",
            "blockers": ["invalid_universe_definition"],
            "warnings": [],
            "survivorshipRisk": True,
        }

    mode = universe.get("mode")
    source = str(universe.get("source") or "").strip()
    if not source:
        blockers.append("missing_universe_source")
    expected_universe_hash = _canonical_hash({key: value for key, value in universe.items() if key != "universeHash"})
    if universe.get("universeHash") != expected_universe_hash:
        blockers.append("invalid_universe_hash")

    artifact = universe.get("memberArtifact")
    if not isinstance(artifact, dict):
        blockers.append("missing_member_artifact")
    if mode == "explicit_snapshot":
        members = universe.get("members")
        normalized_members = sorted({str(code).strip().upper() for code in members or [] if str(code).strip()})
        if not normalized_members or normalized_members != members:
            blockers.append("invalid_explicit_members")
        elif not _artifact_matches(artifact, normalized_members, "inline_sorted_symbols"):
            blockers.append("invalid_member_artifact")
        if scope == "a_share_cross_section":
            survivorship_risk = True
            as_of_date = universe.get("asOfDate")
            if not as_of_date:
                blockers.append("missing_as_of_date")
            elif pd.Timestamp(as_of_date) > pd.Timestamp(research_start):
                blockers.append("survivorship_risk")
            else:
                warnings.append("static_universe")
    elif mode == "historical_membership":
        records = artifact.get("records") if isinstance(artifact, dict) else None
        if not isinstance(records, list) or not records:
            blockers.append("missing_historical_members")
        elif not _artifact_matches(artifact, records, "inline_daily_membership"):
            blockers.append("invalid_member_artifact")
        else:
            try:
                normalized_records = sorted(
                    (
                        {"tradeDate": pd.Timestamp(item["tradeDate"]).date().isoformat(), "tsCode": str(item["tsCode"]).upper()}
                        for item in records
                    ),
                    key=lambda item: (item["tradeDate"], item["tsCode"]),
                )
            except (KeyError, TypeError, ValueError):
                blockers.append("invalid_historical_members")
            else:
                if normalized_records != records:
                    blockers.append("unsorted_historical_members")
        if not universe.get("indexCode"):
            blockers.append("missing_membership_definition")
    else:
        blockers.append("unsupported_universe_mode")
        survivorship_risk = True
    status = "blocked" if blockers else "ready_with_warnings" if warnings else "ready"
    return {
        "status": status,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "survivorshipRisk": survivorship_risk,
        "universeHash": universe.get("universeHash"),
        "memberArtifactHash": artifact.get("sha256") if isinstance(artifact, dict) else None,
    }


def resolve_universe_members(
    universe: dict[str, Any],
    research_start: object,
    research_end: object,
) -> tuple[list[str], pd.DataFrame | None, dict[str, Any]]:
    result = evaluate_universe_provenance(universe, "a_share_cross_section", research_start)
    if result["status"] == "blocked":
        raise ValueError(f"universe 来源门禁未通过：{', '.join(result['blockers'])}")
    if universe["mode"] == "explicit_snapshot":
        return list(universe["members"]), None, result

    records = pd.DataFrame(universe["memberArtifact"]["records"])
    records["trade_date"] = pd.to_datetime(records.pop("tradeDate"))
    records["ts_code"] = records.pop("tsCode").astype(str).str.upper()
    start = pd.Timestamp(research_start)
    end = pd.Timestamp(research_end)
    records = records[(records["trade_date"] >= start) & (records["trade_date"] <= end)]
    records = records[["trade_date", "ts_code"]].drop_duplicates().sort_values(["trade_date", "ts_code"])
    if records.empty:
        raise ValueError("universe 成员工件在研究区间内为空")
    return sorted(records["ts_code"].unique()), records.reset_index(drop=True), result


def _artifact_matches(artifact: object, content: object, expected_format: str) -> bool:
    return bool(
        isinstance(artifact, dict)
        and artifact.get("format") == expected_format
        and artifact.get("count") == len(content)  # type: ignore[arg-type]
        and artifact.get("sha256") == _canonical_hash(content)
    )


def _normalize_dates(values: Iterable[object], label: str) -> pd.DatetimeIndex:
    dates = pd.DatetimeIndex(pd.to_datetime(list(values), errors="coerce")).dropna().drop_duplicates().sort_values()
    if dates.empty:
        raise ValueError(f"{label}不能为空")
    return dates


def _canonical_hash(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
