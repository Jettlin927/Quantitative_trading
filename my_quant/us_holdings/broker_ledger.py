from __future__ import annotations

import csv
import datetime as dt
import html
import json
import math
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable


EXECUTED_STATUSES = {"全部執行", "全部执行"}
BUY_SIDES = {"買入", "买入", "BUY"}
SELL_SIDES = {"沽出", "賣出", "卖出", "SELL"}

TRADE_FIELDS = [
    "email_ts_utc",
    "trade_date",
    "status",
    "side",
    "ticker",
    "security_name",
    "trade_id",
    "email_id",
    "quantity",
    "price",
    "currency",
    "amount",
]

HOLDING_FIELDS = [
    "ticker",
    "security_name",
    "currency",
    "quantity",
    "average_buy_price",
    "cost_basis",
    "last_trade_date",
    "open_lots",
]

FIELD_ALIASES = {
    "email_ts_utc": ("email_ts_utc", "email_ts", "邮件时间", "電郵時間", "电邮时间"),
    "trade_date": ("trade_date", "trade_date_hkt", "交易日期"),
    "status": ("status", "交易狀況", "交易状态"),
    "side": ("side", "指示類別", "指示类别", "買入/沽出", "买入/沽出"),
    "ticker": ("ticker", "股票編號", "股票编号"),
    "security_name": ("security_name", "股票名稱", "股票名称", "证券名", "證券名"),
    "security": (
        "security",
        "股票名稱/ 股票編號",
        "股票名称/股票编号",
        "股票名稱/股票編號",
        "股票名称/ 股票编号",
    ),
    "trade_id": ("trade_id", "交易編號", "交易编号"),
    "email_id": ("email_id", "gmail_message_id", "gmail_id", "Gmail message id"),
    "quantity": (
        "quantity",
        "已成交數量(股/單位)",
        "已成交数量(股/单位)",
        "共成交數量(股/單位)",
        "共成交数量(股/单位)",
        "成交数量",
    ),
    "price": ("price", "成交價", "成交价"),
    "currency": ("currency", "幣種", "币种"),
}


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv_rows(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            newline="",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in fieldnames})
        temp_path.replace(path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
        temp_path.replace(path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def read_trade_input(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        rows: list[dict[str, Any]] = []
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line:
                rows.append(json.loads(line))
        return rows
    return read_csv_rows(path)


def first_value(row: dict[str, Any], canonical: str) -> Any:
    for key in FIELD_ALIASES.get(canonical, (canonical,)):
        if key in row and str(row.get(key, "")).strip():
            return row[key]
    return ""


def parse_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    text = str(value).strip().replace(",", "")
    text = re.sub(r"^[A-Z]{3}", "", text)
    try:
        number = float(text)
        return 0.0 if math.isnan(number) else number
    except ValueError:
        return 0.0


def format_number(value: float, digits: int = 4) -> str:
    rounded = round(value, digits)
    text = f"{rounded:.{digits}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def parse_price_currency(price_value: Any, currency_value: Any = "") -> tuple[str, float]:
    text = str(price_value or "").strip().replace(",", "")
    currency = str(currency_value or "").strip().upper()
    match = re.match(r"^([A-Z]{3})([-+]?\d+(?:\.\d+)?)$", text)
    if match:
        return match.group(1), parse_float(match.group(2))
    return currency or "USD", parse_float(text)


def parse_security(value: Any) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text:
        return "", ""
    match = re.match(r"^(?P<name>.+?)\s*\((?P<ticker>[^()]+)\)\s*$", text)
    if match:
        return match.group("name").strip(), match.group("ticker").strip().upper()
    return text, ""


def normalize_side(value: Any) -> str:
    side = str(value or "").strip()
    upper = side.upper()
    if side in BUY_SIDES or upper in BUY_SIDES:
        return "買入"
    if side in SELL_SIDES or upper in SELL_SIDES:
        return "沽出"
    return side


def normalize_trade(row: dict[str, Any]) -> dict[str, Any] | None:
    status = str(first_value(row, "status") or "").strip()
    if status not in EXECUTED_STATUSES:
        return None

    security_name = str(first_value(row, "security_name") or "").strip()
    ticker = str(first_value(row, "ticker") or "").strip().upper()
    parsed_name, parsed_ticker = parse_security(first_value(row, "security"))
    security_name = security_name or parsed_name
    ticker = ticker or parsed_ticker

    currency, price = parse_price_currency(first_value(row, "price"), first_value(row, "currency"))
    quantity = parse_float(first_value(row, "quantity"))
    trade_id = str(first_value(row, "trade_id") or "").strip()
    if not trade_id:
        raise ValueError("confirmed trade is missing trade_id; cannot dedupe safely")
    if not ticker:
        raise ValueError(f"confirmed trade {trade_id} is missing ticker")
    if quantity <= 0 or price <= 0:
        raise ValueError(f"confirmed trade {trade_id} has invalid quantity or price")

    email_ts = str(first_value(row, "email_ts_utc") or "").strip()
    trade_date = str(first_value(row, "trade_date") or "").strip() or infer_trade_date(email_ts)
    side = normalize_side(first_value(row, "side"))
    if side not in {"買入", "沽出"}:
        raise ValueError(f"confirmed trade {trade_id} has invalid side")
    amount = quantity * price

    return {
        "email_ts_utc": email_ts,
        "trade_date": trade_date,
        "status": status,
        "side": side,
        "ticker": ticker,
        "security_name": security_name,
        "trade_id": trade_id,
        "email_id": str(first_value(row, "email_id") or "").strip(),
        "quantity": format_number(quantity),
        "price": format_number(price),
        "currency": currency,
        "amount": f"{amount:.2f}",
    }


def infer_trade_date(email_ts: str) -> str:
    if not email_ts:
        return ""
    try:
        timestamp = dt.datetime.fromisoformat(email_ts.replace("Z", "+00:00"))
    except ValueError:
        return email_ts[:10]
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=dt.timezone.utc)
    return timestamp.astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat()


def merge_executed_trades(existing_rows: list[dict[str, Any]], candidate_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_row in [*existing_rows, *candidate_rows]:
        trade = normalize_trade(raw_row)
        if trade is None or trade["trade_id"] in seen:
            continue
        rows.append(trade)
        seen.add(trade["trade_id"])
    return rows


def calculate_holdings(trade_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    lots_by_ticker: dict[str, list[dict[str, Any]]] = {}
    names: dict[str, str] = {}
    currencies: dict[str, str] = {}
    last_dates: dict[str, str] = {}

    for row in trade_rows:
        if str(row.get("status", "")).strip() not in EXECUTED_STATUSES:
            continue
        ticker = str(row.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        names[ticker] = str(row.get("security_name", "")).strip() or names.get(ticker, "")
        currencies[ticker] = str(row.get("currency", "USD")).strip().upper() or "USD"
        last_dates[ticker] = str(row.get("trade_date", "")).strip() or last_dates.get(ticker, "")
        quantity = parse_float(row.get("quantity"))
        price = parse_float(row.get("price"))
        side = normalize_side(row.get("side"))
        if quantity <= 0:
            continue
        if side == "買入":
            lots_by_ticker.setdefault(ticker, []).append({
                "quantity": quantity,
                "price": price,
                "trade_date": row.get("trade_date", ""),
                "trade_id": row.get("trade_id", ""),
            })
            continue
        if side == "沽出":
            consume_lots(lots_by_ticker.setdefault(ticker, []), quantity)

    holdings: list[dict[str, Any]] = []
    for ticker in sorted(lots_by_ticker):
        lots = [lot for lot in lots_by_ticker[ticker] if lot["quantity"] > 1e-9]
        quantity = sum(lot["quantity"] for lot in lots)
        if quantity <= 1e-9:
            continue
        cost_basis = sum(lot["quantity"] * lot["price"] for lot in lots)
        average = cost_basis / quantity if quantity else 0
        holdings.append({
            "ticker": ticker,
            "security_name": names.get(ticker, ""),
            "currency": currencies.get(ticker, "USD"),
            "quantity": format_number(quantity),
            "average_buy_price": f"{average:.4f}",
            "cost_basis": f"{cost_basis:.2f}",
            "last_trade_date": last_dates.get(ticker, ""),
            "open_lots": "; ".join(
                f"{format_number(lot['quantity'])} @ {lot['price']:.4f} ({lot.get('trade_date', '')} {lot.get('trade_id', '')})"
                for lot in lots
            ),
        })
    return holdings


def consume_lots(lots: list[dict[str, Any]], sell_quantity: float) -> None:
    remaining = sell_quantity
    for lot in lots:
        if remaining <= 1e-9:
            break
        used = min(lot["quantity"], remaining)
        lot["quantity"] -= used
        remaining -= used
    if remaining > 1e-9:
        raise ValueError(f"sell quantity exceeds open lots by {remaining:.6f}")


def render_holdings_html(holdings_rows: list[dict[str, Any]], generated_at: dt.datetime | None = None) -> str:
    generated_at = generated_at or dt.datetime.now().astimezone()
    total_cost = sum(parse_float(row.get("cost_basis")) for row in holdings_rows)
    table_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(row.get('ticker', '')))}</td>"
        f"<td>{html.escape(str(row.get('security_name', '')))}</td>"
        f"<td>{html.escape(str(row.get('currency', '')))}</td>"
        f"<td>{html.escape(str(row.get('quantity', '')))}</td>"
        f"<td>{html.escape(str(row.get('average_buy_price', '')))}</td>"
        f"<td>{html.escape(str(row.get('cost_basis', '')))}</td>"
        f"<td>{html.escape(str(row.get('open_lots', '')))}</td>"
        "</tr>"
        for row in holdings_rows
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>美股本地持仓账本</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #172033; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border-bottom: 1px solid #d8dee9; padding: 8px 10px; text-align: left; }}
    th {{ background: #f4f6f8; }}
    .summary {{ margin: 16px 0 24px; font-weight: 600; }}
    .note {{ color: #586174; font-size: 13px; }}
  </style>
</head>
<body>
  <h1>美股本地持仓账本</h1>
  <p class="summary">持仓数：{len(holdings_rows)} ｜ 成本合计：${total_cost:,.2f} ｜ 生成时间：{html.escape(generated_at.isoformat(timespec="seconds"))}</p>
  <table>
    <thead>
      <tr><th>股票</th><th>名称</th><th>币种</th><th>数量</th><th>均价</th><th>成本</th><th>Open lots</th></tr>
    </thead>
    <tbody>{table_rows}</tbody>
  </table>
  <p class="note">此页只读取本地 CSV 计算持仓摘要，不连接券商、不下单、不包含行情建议。</p>
</body>
</html>
"""


def update_ledger_from_input(input_path: Path, ledger_path: Path, holdings_path: Path, html_path: Path | None = None) -> dict[str, Any]:
    existing = read_csv_rows(ledger_path)
    incoming = read_trade_input(input_path)
    normalized_existing = merge_executed_trades([], existing)
    merged = merge_executed_trades(normalized_existing, incoming)
    holdings = calculate_holdings(merged)
    rendered_html = render_holdings_html(holdings) if html_path else ""

    write_csv_rows(ledger_path, merged, TRADE_FIELDS)
    write_csv_rows(holdings_path, holdings, HOLDING_FIELDS)
    if html_path:
        write_text(html_path, rendered_html)
    return {
        "input_count": len(incoming),
        "ledger_count": len(merged),
        "added_count": len(merged) - len(normalized_existing),
        "holding_count": len(holdings),
        "ledger_path": str(ledger_path),
        "holdings_path": str(holdings_path),
        "html_path": str(html_path) if html_path else "",
    }
