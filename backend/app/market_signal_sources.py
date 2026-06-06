from __future__ import annotations

from datetime import date
import json
from threading import Lock
from time import monotonic, sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


UA = "Mozilla/5.0 QuantitativeTradingResearch/0.1"
EASTMONEY_MIN_INTERVAL = 1.1
_eastmoney_lock = Lock()
_eastmoney_last_call = 0.0


def fetch_tencent_quotes(codes: list[str]) -> dict[str, dict[str, Any]]:
    normalized_codes = [normalize_plain_code(code) for code in codes]
    unique_codes = [code for index, code in enumerate(normalized_codes) if code and code not in normalized_codes[:index]]
    if not unique_codes:
        return {}

    prefixed = [tencent_symbol(code) for code in unique_codes]
    request = Request("https://qt.gtimg.cn/q=" + ",".join(prefixed), headers={"User-Agent": UA})
    try:
        with urlopen(request, timeout=10) as response:
            text = response.read().decode("gbk", errors="ignore")
    except (HTTPError, URLError, TimeoutError, OSError):
        return {}

    quotes: dict[str, dict[str, Any]] = {}
    for line in text.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        values = line.split('"')[1].split("~")
        if len(values) < 53:
            continue
        code = key[2:]
        ts_code = ts_code_from_plain(code)
        quotes[ts_code] = {
            "tsCode": ts_code,
            "code": code,
            "name": values[1],
            "price": parse_float(values[3]),
            "lastClose": parse_float(values[4]),
            "open": parse_float(values[5]),
            "changePct": parse_float(values[32]),
            "high": parse_float(values[33]),
            "low": parse_float(values[34]),
            "amountWan": parse_float(values[37]),
            "turnoverPct": parse_float(values[38]),
            "amplitudePct": parse_float(values[43]),
            "marketCapYi": parse_float(values[44]),
            "floatMarketCapYi": parse_float(values[45]),
            "limitUp": parse_float(values[47]),
            "limitDown": parse_float(values[48]),
            "volumeRatio": parse_float(values[49]),
        }
    return quotes


def fetch_ths_hot_reason(target_date: date | None = None) -> list[dict[str, Any]]:
    date_text = (target_date or date.today()).strftime("%Y-%m-%d")
    url = f"http://zx.10jqka.com.cn/event/api/getharden/date/{date_text}/orderby/date/orderway/desc/charset/GBK/"
    request = Request(url, headers={"User-Agent": UA})
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8", errors="ignore"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return []

    if not isinstance(payload, dict) or payload.get("errocode", 0) != 0:
        return []
    rows = payload.get("data") or []
    if not isinstance(rows, list):
        return []

    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = normalize_plain_code(row.get("code"))
        if not code:
            continue
        items.append(
            {
                "tsCode": ts_code_from_plain(code),
                "code": code,
                "name": row.get("name"),
                "reason": row.get("reason"),
                "changePct": parse_float(row.get("zhangfu")),
                "turnoverPct": parse_float(row.get("huanshou")),
                "amount": parse_float(row.get("chengjiaoe")),
                "market": row.get("market"),
            }
        )
    return items


def fetch_eastmoney_industries(top_n: int = 20) -> dict[str, Any]:
    params = {
        "pn": "1",
        "pz": "100",
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fs": "m:90+t:2",
        "fields": "f2,f3,f4,f12,f13,f14,f104,f105,f128,f136,f140,f141,f207",
    }
    payload = eastmoney_get_json("https://push2.eastmoney.com/api/qt/clist/get", params)
    rows = (((payload or {}).get("data") or {}).get("diff") or []) if isinstance(payload, dict) else []
    industries: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        industries.append(
            {
                "rank": index,
                "code": row.get("f12"),
                "name": row.get("f14"),
                "changePct": parse_float(row.get("f3")),
                "upCount": parse_int(row.get("f104")),
                "downCount": parse_int(row.get("f105")),
                "leader": row.get("f140") or row.get("f128"),
                "leaderChangePct": parse_float(row.get("f136")),
            }
        )
    safe_top_n = max(1, min(int(top_n or 20), 100))
    return {"top": industries[:safe_top_n], "bottom": industries[-safe_top_n:], "total": len(industries)}


def eastmoney_get_json(url: str, params: dict[str, Any]) -> dict[str, Any] | None:
    global _eastmoney_last_call
    with _eastmoney_lock:
        elapsed = monotonic() - _eastmoney_last_call
        if elapsed < EASTMONEY_MIN_INTERVAL:
            sleep(EASTMONEY_MIN_INTERVAL - elapsed)
        _eastmoney_last_call = monotonic()

    request = Request(f"{url}?{urlencode(params)}", headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8", errors="ignore"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def normalize_plain_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    text = text.replace("SH", "").replace("SZ", "").replace("BJ", "").replace(".", "")
    return text[-6:] if len(text) >= 6 and text[-6:].isdigit() else ""


def ts_code_from_plain(code: str) -> str:
    suffix = "SH" if code.startswith(("6", "9")) else "BJ" if code.startswith("8") else "SZ"
    return f"{code}.{suffix}"


def tencent_symbol(code: str) -> str:
    prefix = "sh" if code.startswith(("6", "9")) else "bj" if code.startswith("8") else "sz"
    return f"{prefix}{code}"


def parse_float(value: Any) -> float | None:
    try:
        if value in {None, "", "-"}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_int(value: Any) -> int | None:
    number = parse_float(value)
    return int(number) if number is not None else None
