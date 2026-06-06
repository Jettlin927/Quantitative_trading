from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = REPO_ROOT / "docs" / "research" / "runs"
CN_TZ = timezone(timedelta(hours=8))


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a fixed manual review worklist for tail-active 14:30 minute checks.")
    parser.add_argument("--run-id", required=True, help="New run folder under docs/research/runs.")
    parser.add_argument("--source-run-id", required=True, help="Existing run containing candidate_dates.json.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--target-time", default="14:30:00")
    args = parser.parse_args()

    source_dir = RUNS_ROOT / args.source_run_id
    source_path = source_dir / "candidate_dates.json"
    if not source_path.exists():
        raise SystemExit(f"candidate_dates.json not found: {source_path}")

    run_dir = RUNS_ROOT / args.run_id
    if run_dir.exists():
        raise SystemExit(f"run already exists: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=False)

    source = json.loads(source_path.read_text(encoding="utf-8"))
    candidates = source.get("selected") or []
    selected = candidates[: args.limit] if args.limit else candidates
    rows = [build_worklist_row(index, item, args.target_time) for index, item in enumerate(selected, start=1)]

    output = {
        "runId": args.run_id,
        "createdAt": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "sourceRunId": args.source_run_id,
        "sourceCandidateTotal": source.get("total"),
        "targetTime": args.target_time,
        "selected": rows,
        "manualFields": [
            "manualMatchedTime",
            "manual1430Price",
            "manualSource",
            "manualCheckedAt",
            "manualNotes",
        ],
    }
    write_json(run_dir / "worklist.json", output)
    write_csv(run_dir / "worklist.csv", rows)
    (run_dir / "review.md").write_text(build_review(output), encoding="utf-8")
    print(json.dumps({"runId": args.run_id, "items": len(rows), "sourceRunId": args.source_run_id}, ensure_ascii=False, indent=2))


def build_worklist_row(index: int, item: dict[str, Any], target_time: str) -> dict[str, Any]:
    ts_code = str(item["ts_code"])
    code = ts_code.split(".")[0]
    prefix = quote_prefix(ts_code)
    return {
        "rank": index,
        "ts_code": ts_code,
        "name": item.get("name"),
        "industry": item.get("industry"),
        "tradeDate": item.get("trade_date"),
        "targetTime": target_time,
        "dailyClose": item.get("daily_close"),
        "nextTradeDate": item.get("next_trade_date"),
        "nextClose": item.get("next_close"),
        "dailyEntryReturnToNextClose": item.get("daily_entry_return_to_next_close"),
        "nextLimitUpClose": item.get("next_limit_up_close"),
        "eastmoneyUrl": f"https://quote.eastmoney.com/{prefix}{code}.html",
        "tencentUrl": f"https://gu.qq.com/{prefix}{code}",
        "manualMatchedTime": "",
        "manual1430Price": "",
        "manualSource": "",
        "manualCheckedAt": "",
        "manualNotes": "",
    }


def quote_prefix(ts_code: str) -> str:
    if ts_code.endswith(".SH") or ts_code.startswith(("6", "9")):
        return "sh"
    if ts_code.endswith(".BJ") or ts_code.startswith("8"):
        return "bj"
    return "sz"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_review(output: dict[str, Any]) -> str:
    lines = [
        "# 尾盘 14:30 人工复核 Worklist",
        "",
        f"- Run: `{output['runId']}`",
        f"- Source run: `{output['sourceRunId']}`",
        f"- Target time: `{output['targetTime']}`",
        f"- Items: `{len(output['selected'])}`",
        "",
        "## Purpose",
        "",
        "当前自动分钟源未通过全量回测准入。本 worklist 固定一组候选样本，用于人工或半自动核对 `14:30` 入场价，避免临时挑样本。",
        "",
        "## Manual Fields",
        "",
        "- `manualMatchedTime`: 实际查到的分钟时间。",
        "- `manual1430Price`: 14:30 或 14:30 前最近一分钟价格。",
        "- `manualSource`: 使用的数据页面或数据源。",
        "- `manualCheckedAt`: 复核时间。",
        "- `manualNotes`: 异常说明，例如停牌、无分钟线、页面不支持历史分钟。",
        "",
        "## Promotion Rule",
        "",
        "人工复核只能作为数据源可用性证据，不能替代全量分钟回测。若复核样本显示稳定可取价，再回到 `sync_tail_minute_bars.py` 扩展 provider 并输出 `canPromoteToBacktest=true` 后，才能进入分钟级收益验证。",
        "",
        "## Items",
        "",
        "| # | Date | Code | Name | Industry | Daily close | Next close | Daily next return | Eastmoney | Tencent |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for item in output["selected"]:
        lines.append(
            "| {rank} | {tradeDate} | {ts_code} | {name} | {industry} | {dailyClose} | {nextClose} | {ret} | [EM]({eastmoneyUrl}) | [QQ]({tencentUrl}) |".format(
                **item,
                ret=format_pct(item.get("dailyEntryReturnToNextClose")),
            )
        )
    return "\n".join(lines) + "\n"


def format_pct(value: Any) -> str:
    if value in (None, ""):
        return "--"
    return f"{float(value) * 100:.2f}%"


if __name__ == "__main__":
    main()
