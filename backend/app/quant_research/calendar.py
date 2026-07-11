from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from io import StringIO
import json
from numbers import Integral, Real
import re
from typing import Any, Iterable, Mapping

import pandas as pd


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class OpenTradeCalendar:
    exchange: str
    source_artifact: str
    source_artifact_sha256: str
    records: tuple[tuple[str, str, bool], ...]
    open_dates: tuple[str, ...]
    contract_sha256: str


def canonical_trade_calendar_bytes(records: Iterable[Mapping[str, Any]]) -> bytes:
    normalized = _normalize_records(records)
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["exchange", "cal_date", "is_open"])
    for exchange, cal_date, is_open in normalized:
        writer.writerow([exchange, cal_date, "1" if is_open else "0"])
    return buffer.getvalue().encode("utf-8")


def trade_calendar_content_sha256(records: Iterable[Mapping[str, Any]]) -> str:
    return sha256(canonical_trade_calendar_bytes(records)).hexdigest()


def build_open_trade_calendar(
    records: Iterable[Mapping[str, Any]],
    *,
    source_artifact: str,
    source_artifact_sha256: str,
    exchange: str = "SSE",
) -> OpenTradeCalendar:
    normalized = _normalize_records(records)
    normalized_exchange = str(exchange).strip().upper()
    if not normalized_exchange:
        raise ValueError("交易日历 exchange 不能为空")
    source = str(source_artifact).strip()
    if not source:
        raise ValueError("交易日历必须绑定 source_artifact")
    expected_source_hash = sha256(canonical_trade_calendar_bytes(_records_as_dicts(normalized))).hexdigest()
    supplied_source_hash = str(source_artifact_sha256).strip().lower()
    if not _SHA256_PATTERN.fullmatch(supplied_source_hash) or supplied_source_hash != expected_source_hash:
        raise ValueError("交易日历 source_artifact_sha256 与规范化源记录不一致")
    exchange_records = tuple(row for row in normalized if row[0] == normalized_exchange)
    if not exchange_records:
        raise ValueError(f"交易日历缺少 {normalized_exchange} 记录")
    open_dates = tuple(row[1] for row in exchange_records if row[2])
    if not open_dates:
        raise ValueError(f"交易日历 {normalized_exchange} 没有开市日")
    payload = {
        "exchange": normalized_exchange,
        "sourceArtifactSha256": supplied_source_hash,
        "records": normalized,
        "openDates": open_dates,
    }
    return OpenTradeCalendar(
        exchange=normalized_exchange,
        source_artifact=source,
        source_artifact_sha256=supplied_source_hash,
        records=normalized,
        open_dates=open_dates,
        contract_sha256=_canonical_hash(payload),
    )


def validate_open_trade_calendar(calendar: OpenTradeCalendar) -> pd.DatetimeIndex:
    if not isinstance(calendar, OpenTradeCalendar):
        raise ValueError("必须提供带来源和内容哈希的 OpenTradeCalendar，禁止裸交易日列表")
    rebuilt = build_open_trade_calendar(
        _records_as_dicts(calendar.records),
        source_artifact=calendar.source_artifact,
        source_artifact_sha256=calendar.source_artifact_sha256,
        exchange=calendar.exchange,
    )
    if rebuilt != calendar:
        raise ValueError("OpenTradeCalendar 合同或内容哈希已被篡改")
    return pd.DatetimeIndex(pd.to_datetime(calendar.open_dates))


def _normalize_records(records: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, str, bool], ...]:
    rows: list[tuple[str, str, bool]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("交易日历源记录必须是映射")
        exchange = str(record.get("exchange") or "").strip().upper()
        if not exchange:
            raise ValueError("交易日历源记录缺少 exchange")
        cal_date = _strict_iso_date(record.get("cal_date"), "cal_date")
        is_open = _strict_bool(record.get("is_open"), "is_open")
        rows.append((exchange, cal_date, is_open))
    if not rows:
        raise ValueError("交易日历源记录不能为空")
    rows.sort(key=lambda row: (row[0], row[1]))
    if len(set((row[0], row[1]) for row in rows)) != len(rows):
        raise ValueError("交易日历源记录存在重复 exchange + cal_date")
    return tuple(rows)


def _records_as_dicts(records: Iterable[tuple[str, str, bool]]) -> list[dict[str, Any]]:
    return [
        {"exchange": exchange, "cal_date": cal_date, "is_open": is_open}
        for exchange, cal_date, is_open in records
    ]


def _strict_iso_date(value: Any, label: str) -> str:
    if isinstance(value, bool) or isinstance(value, Real):
        raise ValueError(f"{label} 必须是 ISO 日期，不能是数字或布尔值")
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = date.fromisoformat(value.strip())
        except ValueError as exc:
            raise ValueError(f"{label} 必须是 YYYY-MM-DD") from exc
    else:
        raise ValueError(f"{label} 必须是 YYYY-MM-DD")
    return parsed.isoformat()


def _strict_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        return bool(value)
    if isinstance(value, Integral) and not isinstance(value, bool) and value in {0, 1}:
        return bool(value)
    raise ValueError(f"{label} 必须是 bool 或 0/1")


def _canonical_hash(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()
