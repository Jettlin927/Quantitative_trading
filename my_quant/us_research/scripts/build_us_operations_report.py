from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path
from typing import Any


US_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = US_ROOT / "data" / "snapshots" / "us_snapshot_latest.json"
DEFAULT_HOLDINGS = US_ROOT / "data" / "holdings_sample.csv"
DEFAULT_HTML = US_ROOT / "reports" / "latest_us_operations.html"
DEFAULT_MD = US_ROOT / "reports" / "latest_us_operations.md"


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def parse_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def fmt(value: Any, digits: int = 2, suffix: str = "") -> str:
    number = parse_float(value)
    if number is None:
        return "N/A"
    return f"{number:.{digits}f}{suffix}"


def decide_action(row: dict[str, Any], held: bool) -> tuple[str, str]:
    if row.get("is_stale"):
        return "观察不动", "数据源 stale/partial，先刷新数据再判断。"

    close = parse_float(row.get("close"))
    ma20 = parse_float(row.get("ma20"))
    ma50 = parse_float(row.get("ma50"))
    ret20 = parse_float(row.get("return_20d_pct"))
    from_high = parse_float(row.get("pct_from_52w_high"))
    leverage = parse_float(row.get("leverage_factor")) or 1.0

    if leverage >= 2 and ((ret20 is not None and ret20 >= 15) or (from_high is not None and from_high > -5)):
        return "减仓降风险", "杠杆或高 beta 工具靠近高位，先控制同因子敞口。"
    if close is not None and ma20 is not None and ma50 is not None and close >= ma20 >= ma50:
        return ("继续持有" if held else "只等回调"), "趋势仍在 MA20/MA50 上方，但新增仓位等待回调或止跌确认。"
    if close is not None and ma20 is not None and close >= ma20:
        return "止跌后小加", "价格重新站上 MA20，可作为小仓观察信号。"
    return "观察不动", "趋势证据不足，等待更清晰的止跌或回调确认。"


def holdings_by_ticker(holdings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("ticker", "")).strip().upper(): row for row in holdings if row.get("ticker")}


def build_rows(snapshot: dict[str, Any], holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    holding_map = holdings_by_ticker(holdings)
    rows = []
    for row in snapshot.get("symbols", []):
        ticker = str(row.get("ticker", "")).upper()
        held = ticker in holding_map or row.get("role") == "holding"
        action, evidence = decide_action(row, held)
        output = dict(row)
        output["held"] = held
        output["action_label"] = action
        output["evidence"] = evidence
        rows.append(output)
    return rows


def build_markdown(snapshot: dict[str, Any], holdings: list[dict[str, Any]], rows: list[dict[str, Any]]) -> str:
    lines = [
        "# 美股操作层 sample 报告",
        "",
        "本报告是研究辅助，不是交易指令；它不连接券商、不读取真实持仓、不自动下单。",
        "",
        f"- 数据状态：`{snapshot.get('status', 'unknown')}`",
        f"- 数据源：`{snapshot.get('source', 'unknown')}`",
        f"- 抓取时间：`{snapshot.get('fetched_at', '')}`",
        f"- sample 持仓数：`{len(holdings)}`",
        "",
        "| ticker | 主题 | 趋势 | 风险 | 操作标签 | 数据新鲜度 | 证据 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        freshness = "stale" if row.get("is_stale") else "fresh"
        trend = f"close {fmt(row.get('close'))} / MA20 {fmt(row.get('ma20'))} / MA50 {fmt(row.get('ma50'))}"
        lines.append(
            "| {ticker} | {theme} | {trend} | {risk} | {action} | {freshness} | {evidence} |".format(
                ticker=row.get("ticker", ""),
                theme=row.get("theme", ""),
                trend=trend,
                risk=row.get("risk_tag", ""),
                action=row.get("action_label", ""),
                freshness=freshness,
                evidence=row.get("evidence", ""),
            )
        )
    return "\n".join(lines) + "\n"


def build_html(snapshot: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    table_rows = []
    for row in rows:
        freshness = "stale" if row.get("is_stale") else "fresh"
        trend = f"Close {fmt(row.get('close'))}<br>MA20 {fmt(row.get('ma20'))}<br>MA50 {fmt(row.get('ma50'))}"
        table_rows.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('ticker', '')))}</td>"
            f"<td>{html.escape(str(row.get('theme', '')))}<br><span>{html.escape(str(row.get('subtheme', '')))}</span></td>"
            f"<td>{trend}</td>"
            f"<td>{html.escape(str(row.get('risk_tag', '')))}<br>leverage {fmt(row.get('leverage_factor'), 1)}x</td>"
            f"<td><strong>{html.escape(str(row.get('action_label', '')))}</strong><br>{html.escape(str(row.get('evidence', '')))}</td>"
            f"<td>{freshness}<br>{html.escape(str(row.get('stale_reason', '')))}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>美股操作层 sample 报告</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #1f2933; background: #f7f9fb; }}
    h1 {{ margin-bottom: 4px; }}
    .meta {{ color: #52606d; margin-bottom: 20px; }}
    .notice {{ padding: 12px 14px; border-left: 4px solid #52606d; background: #fff; margin-bottom: 18px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; }}
    th, td {{ border: 1px solid #d9e2ec; padding: 10px; text-align: left; vertical-align: top; }}
    th {{ background: #e4eef8; }}
    span {{ color: #627d98; }}
  </style>
</head>
<body>
  <h1>美股操作层 sample 报告</h1>
  <p class="meta">数据新鲜度：{html.escape(str(snapshot.get("status", "unknown")))} / source={html.escape(str(snapshot.get("source", "unknown")))} / fetched_at={html.escape(str(snapshot.get("fetched_at", "")))}</p>
  <div class="notice">本报告是研究辅助，不是交易指令；它不连接券商、不读取真实持仓、不自动下单。数据源失败时会显示 partial 或 stale。</div>
  <table>
    <thead>
      <tr><th>Ticker</th><th>主题</th><th>趋势</th><th>风险</th><th>操作标签</th><th>数据新鲜度</th></tr>
    </thead>
    <tbody>
      {''.join(table_rows)}
    </tbody>
  </table>
</body>
</html>
"""


def build_report_text(snapshot: dict[str, Any], holdings: list[dict[str, Any]]) -> tuple[str, str]:
    rows = build_rows(snapshot, holdings)
    return build_markdown(snapshot, holdings, rows), build_html(snapshot, rows)


def write_reports(snapshot: dict[str, Any], holdings: list[dict[str, Any]], html_path: Path, md_path: Path) -> None:
    markdown, html_text = build_report_text(snapshot, holdings)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_text, encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build sample US operations report from yfinance snapshot.")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--holdings", type=Path, default=DEFAULT_HOLDINGS)
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML)
    parser.add_argument("--md", type=Path, default=DEFAULT_MD)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    holdings = read_csv_rows(args.holdings)
    write_reports(snapshot, holdings=holdings, html_path=args.html, md_path=args.md)
    print(f"wrote {args.html}")
    print(f"wrote {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
