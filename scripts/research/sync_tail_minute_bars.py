from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, time as time_type, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import DEFAULT_CONFIG, enrich_rows, json_safe, limit_up_close, should_enter
from backend.app.database import SessionLocal
from backend.app.main import query_backtest_rows_by_code, query_backtest_stocks
from backend.app.schemas import MarketBacktestRequest
from backend.app.tushare_client import get_pro_api


CN_TZ = timezone(timedelta(hours=8))
RUNS_ROOT = REPO_ROOT / "docs" / "research" / "runs"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build tail-active candidate dates and sample 14:30 minute entry prices.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--target-time", default="14:30:00")
    parser.add_argument("--max-stocks", type=int, default=0)
    parser.add_argument("--min-bars", type=int, default=120)
    parser.add_argument("--exclude-st", action="store_true")
    parser.add_argument("--exclude-bj", action="store_true")
    parser.add_argument("--profile", choices=["base", "best-risk"], default="best-risk")
    parser.add_argument("--max-candidates", type=int, default=50)
    parser.add_argument("--max-requests", type=int, default=0, help="0 means fetch all selected candidate symbols.")
    parser.add_argument("--provider", choices=["tushare", "eastmoney-recent", "mootdx"], default="tushare")
    parser.add_argument("--sleep-seconds", type=float, default=65.0, help="Tushare stk_mins is commonly limited to 1 call/minute.")
    parser.add_argument("--mootdx-pages", type=int, default=1, help="Number of 800-row 1-minute pages to fetch when provider=mootdx.")
    parser.add_argument("--include-open-candidates", action="store_true", help="Include final-date candidates without next-day return for minute coverage checks.")
    parser.add_argument("--dry-run", action="store_true", help="Only build candidate dates; do not call minute data source.")
    args = parser.parse_args()

    target_time = parse_target_time(args.target_time)
    run_dir = RUNS_ROOT / args.run_id
    if run_dir.exists():
        raise SystemExit(f"run already exists: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=False)

    cfg = build_tail_config(args.profile)
    db = SessionLocal()
    try:
        candidates = build_candidates(db, args, cfg)
    finally:
        db.close()

    selected = candidates[: args.max_candidates] if args.max_candidates else candidates
    write_json(run_dir / "candidate_dates.json", {"total": len(candidates), "selected": selected})

    minute_records: list[dict[str, Any]] = []
    fetch_errors: list[dict[str, str]] = []
    fetch_stats: list[dict[str, Any]] = []
    if not args.dry_run and selected:
        pro = None
        if args.provider == "tushare":
            load_env_token()
            pro = get_pro_api(None)
        symbols = unique_symbols_in_order(selected)
        if args.max_requests:
            symbols = symbols[: args.max_requests]
        for index, ts_code in enumerate(symbols, start=1):
            try:
                rows = fetch_minutes(args.provider, ts_code, pro, args.mootdx_pages)
                matched = match_candidate_minutes(selected, rows, ts_code, target_time, args.provider)
                minute_records.extend(matched)
                fetch_stats.append(build_fetch_stat(ts_code, rows, len(matched)))
            except Exception as error:  # noqa: BLE001 - external data source failures are evidence.
                fetch_errors.append({"ts_code": ts_code, "error": str(error)[:300]})
                fetch_stats.append({"ts_code": ts_code, "rows": 0, "matches": 0, "error": str(error)[:300]})
            if index < len(symbols):
                time.sleep(max(args.sleep_seconds, 0))

    write_jsonl(run_dir / "minute_cache.jsonl", minute_records)
    summary = build_summary(args, candidates, selected, minute_records, fetch_errors, fetch_stats)
    write_json(run_dir / "results.json", summary)
    write_text(run_dir / "review.md", build_review(summary))
    print(json.dumps(summary["summary"], ensure_ascii=False, indent=2))


def build_tail_config(profile: str) -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(
        {
            "entryMode": "tail-active-next-day",
            "useTrendFilter": False,
            "useMacdFilter": False,
            "useRsiFilter": False,
            "blockWeakMarket": True,
            "tailEntryMinPctChg": 0.025,
            "tailEntryMaxPctChg": 0.05,
            "tailMinVolumeRatio": 2.0,
            "tailMinTurnoverRatePct": 7.0,
            "tailPriorLimitUpLookback": 15,
        }
    )
    if profile == "best-risk":
        cfg["entryRiskFilter"] = {"enabled": True, "maxEntryRangePct": 0.06}
    return cfg


def load_env_token() -> None:
    if os.getenv("TUSHARE_TOKEN"):
        return
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == "TUSHARE_TOKEN":
            os.environ["TUSHARE_TOKEN"] = value.strip().strip('"').strip("'")
            return


def build_candidates(db: Any, args: argparse.Namespace, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    payload = MarketBacktestRequest(
        start_date=args.start_date,
        end_date=args.end_date,
        config=cfg,
        min_bars=args.min_bars,
        max_stocks=args.max_stocks,
        exclude_st=args.exclude_st,
        exclude_bj=args.exclude_bj,
    )
    stocks = query_backtest_stocks(db, payload)
    result: list[dict[str, Any]] = []
    for batch_start in range(0, len(stocks), 200):
        batch = stocks[batch_start : batch_start + 200]
        bars_by_code = query_backtest_rows_by_code(db, [stock.ts_code for stock in batch], args.start_date, args.end_date)
        for stock in batch:
            rows = bars_by_code.get(stock.ts_code) or []
            if len(rows) < args.min_bars:
                continue
            enriched = enrich_rows(rows, cfg)
            last_entry_index = len(enriched) if args.include_open_candidates else len(enriched) - 1
            for index in range(1, last_entry_index):
                row = enriched[index]
                signal = should_enter(row, enriched[index - 1], cfg)
                if not signal.get("ok"):
                    continue
                next_row = enriched[index + 1] if index + 1 < len(enriched) else None
                next_close = next_row["close"] if next_row else None
                result.append(
                    {
                        "ts_code": stock.ts_code,
                        "name": stock.name,
                        "industry": stock.industry,
                        "trade_date": row["date"],
                        "daily_close": row["close"],
                        "next_trade_date": next_row["date"] if next_row else None,
                        "next_close": next_close,
                        "next_limit_up_close": limit_up_close(next_row, cfg) if next_row else None,
                        "daily_entry_return_to_next_close": next_close / row["close"] - 1 if next_close and row["close"] else None,
                    }
                )
    return sorted(result, key=lambda item: (item["trade_date"], item["ts_code"]), reverse=True)


def fetch_tushare_minutes(pro: Any, ts_code: str) -> list[dict[str, Any]]:
    df = pro.stk_mins(ts_code=ts_code, freq="1min")
    if df is None or len(df) == 0:
        return []
    rows = df.to_dict("records")
    return [
        {
            "ts_code": str(row.get("ts_code") or ts_code),
            "trade_time": str(row.get("trade_time") or ""),
            "open": float(row.get("open") or 0),
            "high": float(row.get("high") or 0),
            "low": float(row.get("low") or 0),
            "close": float(row.get("close") or 0),
            "vol": float(row.get("vol") or 0),
            "amount": float(row.get("amount") or 0),
        }
        for row in rows
    ]


def match_candidate_minutes(
    candidates: list[dict[str, Any]],
    minute_rows: list[dict[str, Any]],
    ts_code: str,
    target_time: time_type,
    provider: str,
) -> list[dict[str, Any]]:
    by_date: dict[str, list[dict[str, Any]]] = {}
    for row in minute_rows:
        trade_time = parse_trade_time(row.get("trade_time"))
        if not trade_time:
            continue
        row["trade_date"] = trade_time.date().isoformat()
        row["_dt"] = trade_time
        by_date.setdefault(row["trade_date"], []).append(row)

    records: list[dict[str, Any]] = []
    for candidate in [item for item in candidates if item["ts_code"] == ts_code]:
        rows = sorted(by_date.get(candidate["trade_date"], []), key=lambda item: item["_dt"])
        target_row = find_target_minute(rows, target_time)
        if not target_row:
            continue
        minute_close = float(target_row["close"])
        next_close = float(candidate["next_close"]) if candidate.get("next_close") is not None else None
        record = {
            **candidate,
            "source": provider,
            "target_time": target_time.isoformat(),
            "matched_trade_time": target_row["trade_time"],
            "minute_open": target_row["open"],
            "minute_high": target_row["high"],
            "minute_low": target_row["low"],
            "minute_close": minute_close,
            "minute_vol": target_row["vol"],
            "minute_amount": target_row["amount"],
            "minute_entry_return_to_next_close": next_close / minute_close - 1 if next_close and minute_close else None,
            "daily_close_vs_minute_entry_pct": float(candidate["daily_close"]) / minute_close - 1 if minute_close else None,
        }
        records.append(record)
    return records


def find_target_minute(rows: list[dict[str, Any]], target_time: time_type) -> dict[str, Any] | None:
    exact = [row for row in rows if row["_dt"].time() == target_time]
    if exact:
        return exact[-1]
    before = [row for row in rows if row["_dt"].time() <= target_time]
    return before[-1] if before else None


def build_summary(
    args: argparse.Namespace,
    candidates: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    minute_records: list[dict[str, Any]],
    fetch_errors: list[dict[str, str]],
    fetch_stats: list[dict[str, Any]],
) -> dict[str, Any]:
    minute_returns = [float(row["minute_entry_return_to_next_close"]) for row in minute_records if row.get("minute_entry_return_to_next_close") is not None]
    daily_returns = [float(row["daily_entry_return_to_next_close"]) for row in minute_records if row.get("daily_entry_return_to_next_close") is not None]
    deltas = [float(row["daily_close_vs_minute_entry_pct"]) for row in minute_records if row.get("daily_close_vs_minute_entry_pct") is not None]
    coverage_rate = len(minute_records) / len(selected) if selected else 0
    source_status = classify_source_status(args, selected, minute_records, fetch_errors)
    return json_safe(
        {
            "runId": args.run_id,
            "createdAt": datetime.now(CN_TZ).isoformat(timespec="seconds"),
            "scope": {
                "startDate": args.start_date.isoformat(),
                "endDate": args.end_date.isoformat(),
                "targetTime": args.target_time,
                "maxStocks": args.max_stocks,
                "minBars": args.min_bars,
                "excludeSt": args.exclude_st,
                "excludeBj": args.exclude_bj,
                "maxCandidates": args.max_candidates,
                "maxRequests": args.max_requests,
                "profile": args.profile,
                "provider": args.provider,
                "mootdxPages": args.mootdx_pages,
                "dryRun": args.dry_run,
                "includeOpenCandidates": args.include_open_candidates,
            },
            "summary": {
                "candidateDates": len(candidates),
                "selectedCandidates": len(selected),
                "minuteMatches": len(minute_records),
                "coverageRate": coverage_rate,
                "avgMinuteReturnToNextClose": mean(minute_returns) if minute_returns else None,
                "medianMinuteReturnToNextClose": median(minute_returns) if minute_returns else None,
                "avgDailyReturnToNextCloseForMatched": mean(daily_returns) if daily_returns else None,
                "medianDailyReturnToNextCloseForMatched": median(daily_returns) if daily_returns else None,
                "avgDailyCloseVsMinuteEntryPct": mean(deltas) if deltas else None,
                "medianDailyCloseVsMinuteEntryPct": median(deltas) if deltas else None,
                "fetchErrors": len(fetch_errors),
                "sourceStatus": source_status["status"],
                "sourceStatusReason": source_status["reason"],
                "canPromoteToBacktest": source_status["canPromoteToBacktest"],
            },
            "fetchErrors": fetch_errors,
            "fetchStats": fetch_stats,
            "minuteRecords": minute_records,
        }
    )


def build_review(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        "# 尾盘分钟入场价数据源探测",
        "",
        f"- Run: `{result['runId']}`",
        f"- 覆盖: `{result['scope']['startDate']}` 至 `{result['scope']['endDate']}`",
        f"- 目标时间: `{result['scope']['targetTime']}`",
        f"- 候选口径: `{result['scope']['profile']}`",
        f"- 分钟源: `{result['scope']['provider']}`",
        f"- 候选日期: `{summary['candidateDates']}`，选中候选: `{summary['selectedCandidates']}`，分钟匹配: `{summary['minuteMatches']}`",
        f"- 覆盖率: `{format_pct(summary['coverageRate'])}`，请求错误: `{summary['fetchErrors']}`",
        f"- 数据源状态: `{summary['sourceStatus']}`，可晋级回测: `{summary['canPromoteToBacktest']}`",
        f"- 状态原因: {summary['sourceStatusReason']}",
        "",
        "## 收益对比",
        "",
        f"- 分钟入场至次日收盘均值: `{format_pct(summary['avgMinuteReturnToNextClose'])}`，中位数: `{format_pct(summary['medianMinuteReturnToNextClose'])}`",
        f"- 日线收盘入场至次日收盘均值: `{format_pct(summary['avgDailyReturnToNextCloseForMatched'])}`，中位数: `{format_pct(summary['medianDailyReturnToNextCloseForMatched'])}`",
        f"- 日线收盘相对 14:30 入场价均值差: `{format_pct(summary['avgDailyCloseVsMinuteEntryPct'])}`，中位数: `{format_pct(summary['medianDailyCloseVsMinuteEntryPct'])}`",
        "",
        "## 口径说明",
        "",
        f"- 候选日期来自本地日线和 daily_basic，候选口径为 `{result['scope']['profile']}`。",
        "- 分钟数据源可选 `tushare.stk_mins(freq=1min)`、`eastmoney-recent` 或 `mootdx`；只作为小批量探测源，覆盖率必须写入证据。",
        "- 本脚本不写数据库，只把候选和分钟匹配结果写入 run 目录，避免污染长期数据口径。",
    ]
    return "\n".join(lines) + "\n"


def classify_source_status(
    args: argparse.Namespace,
    selected: list[dict[str, Any]],
    minute_records: list[dict[str, Any]],
    fetch_errors: list[dict[str, str]],
) -> dict[str, Any]:
    if args.dry_run:
        return {"status": "candidate_rebuild_only", "reason": "dry-run 只验证候选日期重建，不验证分钟源。", "canPromoteToBacktest": False}
    if not selected:
        return {"status": "no_candidates", "reason": "所选窗口没有候选日期，无法验证分钟源覆盖率。", "canPromoteToBacktest": False}
    if fetch_errors and not minute_records:
        return {"status": "source_failed", "reason": "分钟源请求失败且没有任何匹配记录。", "canPromoteToBacktest": False}
    coverage_rate = len(minute_records) / len(selected)
    if len(minute_records) < 20 or coverage_rate < 0.8:
        return {"status": "insufficient_coverage", "reason": "分钟匹配数或覆盖率不足，不能进入全量分钟回测。", "canPromoteToBacktest": False}
    if fetch_errors:
        return {"status": "partial_with_errors", "reason": "分钟源有覆盖但仍存在请求错误，需要扩大样本复核。", "canPromoteToBacktest": False}
    return {"status": "probe_passed", "reason": "分钟源小样本覆盖率达到晋级门槛，可进入更大样本验证。", "canPromoteToBacktest": True}


def build_fetch_stat(ts_code: str, rows: list[dict[str, Any]], matches: int) -> dict[str, Any]:
    dates = []
    for row in rows:
        trade_time = parse_trade_time(row.get("trade_time"))
        if trade_time:
            dates.append(trade_time.date().isoformat())
    return {
        "ts_code": ts_code,
        "rows": len(rows),
        "matches": matches,
        "firstDate": min(dates) if dates else None,
        "lastDate": max(dates) if dates else None,
    }


def parse_target_time(value: str) -> time_type:
    return time_type.fromisoformat(value if len(value.split(":")) == 3 else f"{value}:00")


def parse_trade_time(value: Any) -> datetime | None:
    text = str(value or "")
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def unique_symbols_in_order(candidates: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    symbols: list[str] = []
    for item in candidates:
        ts_code = str(item["ts_code"])
        if ts_code in seen:
            continue
        seen.add(ts_code)
        symbols.append(ts_code)
    return symbols


def fetch_eastmoney_recent_minutes(ts_code: str) -> list[dict[str, Any]]:
    plain_code = ts_code.split(".")[0]
    secid = f"1.{plain_code}" if ts_code.endswith(".SH") or plain_code.startswith(("6", "9")) else f"0.{plain_code}"
    params = {
        "secid": secid,
        "klt": "1",
        "fqt": "1",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = []
    for line in (payload.get("data") or {}).get("klines") or []:
        parts = line.split(",")
        if len(parts) < 7:
            continue
        rows.append(
            {
                "ts_code": ts_code,
                "trade_time": parts[0],
                "open": float(parts[1] or 0),
                "close": float(parts[2] or 0),
                "high": float(parts[3] or 0),
                "low": float(parts[4] or 0),
                "vol": float(parts[5] or 0),
                "amount": float(parts[6] or 0),
            }
        )
    return rows


def fetch_minutes(provider: str, ts_code: str, pro: Any | None = None, mootdx_pages: int = 1) -> list[dict[str, Any]]:
    if provider == "tushare":
        return fetch_tushare_minutes(pro, ts_code)
    if provider == "eastmoney-recent":
        return fetch_eastmoney_recent_minutes(ts_code)
    if provider == "mootdx":
        return fetch_mootdx_minutes(ts_code, mootdx_pages)
    raise ValueError(f"unsupported provider: {provider}")


def fetch_mootdx_minutes(ts_code: str, pages: int) -> list[dict[str, Any]]:
    try:
        from mootdx.consts import KLINE_1MIN
        from mootdx.quotes import Quotes
    except ModuleNotFoundError as error:
        raise RuntimeError("mootdx is not installed; install and verify it before using provider=mootdx") from error

    plain_code = ts_code.split(".")[0]
    client = Quotes.factory(market="std")
    frames = []
    page_size = 800
    for page in range(max(pages, 1)):
        frame = client.bars(symbol=plain_code, frequency=KLINE_1MIN, start=page * page_size, offset=page_size)
        if frame is not None and len(frame) > 0:
            frames.append(frame)
    if not frames:
        return []
    rows: list[dict[str, Any]] = []
    records = []
    for frame in frames:
        records.extend(frame.to_dict("records") if hasattr(frame, "to_dict") else list(frame))
    seen: set[str] = set()
    for row in records:
        trade_time = row.get("datetime") or row.get("date") or row.get("trade_time")
        trade_time_text = str(trade_time or "")
        if trade_time_text in seen:
            continue
        seen.add(trade_time_text)
        rows.append(
            {
                "ts_code": ts_code,
                "trade_time": trade_time_text,
                "open": float(row.get("open") or 0),
                "high": float(row.get("high") or 0),
                "low": float(row.get("low") or 0),
                "close": float(row.get("close") or 0),
                "vol": float(row.get("vol") or row.get("volume") or 0),
                "amount": float(row.get("amount") or 0),
            }
        )
    return sorted(rows, key=lambda item: item["trade_time"])


def format_pct(value: Any) -> str:
    return "--" if value is None else f"{float(value) * 100:.2f}%"


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(json_safe(data), ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(json_safe(row), ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
