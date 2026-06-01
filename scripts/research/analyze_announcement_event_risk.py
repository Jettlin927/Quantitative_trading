from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta
from html import unescape
from pathlib import Path
from statistics import mean
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.backtest_engine import finite, json_safe
from scripts.research.analyze_trade_delta import WINDOWS, keyed_trades, select_window, sum_value
from scripts.research.run_research_round import RUNS_ROOT, now_text, read_json, write_json, write_text


RISK_KEYWORDS = {
    "减持解禁": ["减持", "解禁", "限售股上市流通"],
    "监管问询": ["问询", "监管", "处罚", "立案", "警示函", "纪律处分", "通报批评"],
    "质押冻结": ["质押", "冻结", "司法"],
    "业绩减值": ["亏损", "预亏", "业绩预告", "业绩修正", "下修", "下降", "减值", "计提", "商誉"],
    "诉讼债务担保": ["诉讼", "仲裁", "债务", "担保", "违规", "违约"],
    "停牌退市风险": ["停牌", "退市", "风险警示", "ST"],
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose point-in-time announcement/event risk around replacement trades.")
    parser.add_argument("--run-id", required=True, help="Folder name under docs/research/runs.")
    parser.add_argument("--baseline-run", required=True, help="Baseline portfolio run id.")
    parser.add_argument("--candidate-run", required=True, help="Candidate portfolio run id.")
    parser.add_argument("--lookback-days", type=int, default=30, help="Calendar days before entry date to query announcements.")
    parser.add_argument("--page-size", type=int, default=30, help="CNINFO page size for each stock/date-window query.")
    parser.add_argument("--sleep-sec", type=float, default=0.15, help="Delay between uncached CNINFO requests.")
    args = parser.parse_args()

    started_at = now_text()
    run_dir = RUNS_ROOT / args.run_id
    if (run_dir / "results.json").exists():
        raise SystemExit(f"Run already exists: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)

    baseline = read_json(RUNS_ROOT / args.baseline_run / "results.json")
    candidate = read_json(RUNS_ROOT / args.candidate_run / "results.json")
    fetcher = AnnouncementFetcher(run_dir / "announcement-cache.jsonl", args.page_size, args.sleep_sec)

    if "windows" in baseline and "windows" in candidate:
        output = analyze_window_runs(args, baseline, candidate, fetcher, started_at)
    else:
        output = analyze_full_runs(args, baseline, candidate, fetcher, started_at)

    write_json(run_dir / "results.json", json_safe(output))
    write_text(run_dir / "review.md", render_review(output))
    print(json.dumps({"runId": args.run_id, "summary": compact_summary(output), "runDir": str(run_dir)}, ensure_ascii=False, indent=2))


class AnnouncementFetcher:
    def __init__(self, cache_path: Path, page_size: int, sleep_sec: float) -> None:
        self.cache_path = cache_path
        self.page_size = page_size
        self.sleep_sec = sleep_sec
        self.session = requests.Session()
        self.cache: dict[str, dict[str, Any]] = {}
        if cache_path.exists():
            for line in cache_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    self.cache[str(item["cacheKey"])] = item

    def fetch(self, ts_code: str, start: date, end: date) -> dict[str, Any]:
        code = stock_code(ts_code)
        cache_key = f"{code}|{start.isoformat()}|{end.isoformat()}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        item = {
            "cacheKey": cache_key,
            "tsCode": ts_code,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "fetchedAt": now_text(),
            "source": "cninfo",
            "announcements": [],
            "error": None,
        }
        try:
            item["announcements"] = fetch_cninfo_announcements(self.session, code, start, end, self.page_size)
        except Exception as exc:  # noqa: BLE001 - diagnostic cache records external API failures.
            item["error"] = str(exc)
        self.cache[cache_key] = item
        with self.cache_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(json_safe(item), ensure_ascii=False) + "\n")
        if self.sleep_sec > 0:
            time.sleep(self.sleep_sec)
        return item


def analyze_full_runs(args: argparse.Namespace, baseline: dict[str, Any], candidate: dict[str, Any], fetcher: AnnouncementFetcher, started_at: str) -> dict[str, Any]:
    baseline_trades = keyed_trades(baseline["result"].get("completedTrades", []))
    candidate_trades = keyed_trades(candidate["result"].get("completedTrades", []))
    baseline_only_keys = set(baseline_trades) - set(candidate_trades)
    candidate_only_keys = set(candidate_trades) - set(baseline_trades)

    comparisons = []
    for label, bounds in [("ALL", None), *WINDOWS.items()]:
        baseline_only = select_window([baseline_trades[key] for key in baseline_only_keys], bounds)
        candidate_only = select_window([candidate_trades[key] for key in candidate_only_keys], bounds)
        comparisons.append(build_comparison(label, baseline_only, candidate_only, fetcher, args.lookback_days))

    return {
        "runId": args.run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "baselineRun": args.baseline_run,
        "candidateRun": args.candidate_run,
        "mode": "full",
        "lookbackDays": args.lookback_days,
        "baselineOnlyTradeCount": len(baseline_only_keys),
        "candidateOnlyTradeCount": len(candidate_only_keys),
        "comparisons": comparisons,
    }


def analyze_window_runs(args: argparse.Namespace, baseline: dict[str, Any], candidate: dict[str, Any], fetcher: AnnouncementFetcher, started_at: str) -> dict[str, Any]:
    candidate_windows = {item["window"]["label"]: item for item in candidate.get("windows", [])}
    windows = []
    for baseline_window in baseline.get("windows", []):
        label = baseline_window["window"]["label"]
        candidate_window = candidate_windows.get(label)
        if not candidate_window:
            continue
        baseline_trades = keyed_trades(baseline_window["result"].get("completedTrades", []))
        candidate_trades = keyed_trades(candidate_window["result"].get("completedTrades", []))
        baseline_only = [baseline_trades[key] for key in set(baseline_trades) - set(candidate_trades)]
        candidate_only = [candidate_trades[key] for key in set(candidate_trades) - set(baseline_trades)]
        windows.append(
            {
                "label": label,
                "window": baseline_window["window"],
                "comparison": build_comparison(label, baseline_only, candidate_only, fetcher, args.lookback_days),
            }
        )
    return {
        "runId": args.run_id,
        "startedAt": started_at,
        "finishedAt": now_text(),
        "baselineRun": args.baseline_run,
        "candidateRun": args.candidate_run,
        "mode": "window_validation",
        "lookbackDays": args.lookback_days,
        "windows": windows,
    }


def build_comparison(label: str, baseline_only: list[dict[str, Any]], candidate_only: list[dict[str, Any]], fetcher: AnnouncementFetcher, lookback_days: int) -> dict[str, Any]:
    baseline_annotated = [annotate_trade(trade, fetcher, lookback_days) for trade in baseline_only]
    candidate_annotated = [annotate_trade(trade, fetcher, lookback_days) for trade in candidate_only]
    return {
        "label": label,
        "replacementNetPnlDelta": sum_value(candidate_only, "netPnl") - sum_value(baseline_only, "netPnl"),
        "baselineOnly": summarize_annotated_trades(baseline_annotated),
        "candidateOnly": summarize_annotated_trades(candidate_annotated),
        "candidateOnlyLosses": summarize_annotated_trades([item for item in candidate_annotated if float(item.get("returnPct") or 0) < 0]),
        "candidateOnlyWins": summarize_annotated_trades([item for item in candidate_annotated if float(item.get("returnPct") or 0) > 0]),
        "baselineOnlyLosses": summarize_annotated_trades([item for item in baseline_annotated if float(item.get("returnPct") or 0) < 0]),
        "candidateOnlyRiskSamples": risk_samples(candidate_annotated),
        "baselineOnlyRiskSamples": risk_samples(baseline_annotated),
    }


def annotate_trade(trade: dict[str, Any], fetcher: AnnouncementFetcher, lookback_days: int) -> dict[str, Any]:
    entry_date = date.fromisoformat(str(trade["entryDate"]))
    start = entry_date - timedelta(days=lookback_days)
    end = entry_date - timedelta(days=1)
    fetched = fetcher.fetch(str(trade["ts_code"]), start, end)
    announcements = [classify_announcement(item) for item in fetched.get("announcements", [])]
    risk_announcements = [item for item in announcements if item["riskCategories"]]
    categories = sorted({category for item in risk_announcements for category in item["riskCategories"]})
    return {
        "ts_code": trade.get("ts_code"),
        "name": trade.get("name"),
        "industry": trade.get("industry"),
        "entryDate": trade.get("entryDate"),
        "exitDate": trade.get("exitDate"),
        "returnPct": trade.get("returnPct"),
        "netPnl": trade.get("netPnl"),
        "exitPriceRule": trade.get("exitPriceRule"),
        "announcementWindow": {"startDate": start.isoformat(), "endDate": end.isoformat()},
        "announcementCount": len(announcements),
        "riskAnnouncementCount": len(risk_announcements),
        "riskCategories": categories,
        "riskAnnouncements": risk_announcements[:5],
        "fetchError": fetched.get("error"),
    }


def summarize_annotated_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "count": 0,
            "netPnl": 0.0,
            "avgReturnPct": None,
            "riskTradeRatio": None,
            "avgAnnouncementCount": None,
            "avgRiskAnnouncementCount": None,
            "riskCategoryCounts": {},
            "fetchErrorCount": 0,
        }
    risk_trades = [item for item in trades if item["riskAnnouncementCount"] > 0]
    category_counts: Counter[str] = Counter()
    for item in trades:
        category_counts.update(item["riskCategories"])
    return {
        "count": len(trades),
        "netPnl": sum(float(item.get("netPnl") or 0.0) for item in trades),
        "avgReturnPct": avg([item.get("returnPct") for item in trades]),
        "riskTradeRatio": len(risk_trades) / len(trades),
        "avgAnnouncementCount": mean([item["announcementCount"] for item in trades]),
        "avgRiskAnnouncementCount": mean([item["riskAnnouncementCount"] for item in trades]),
        "riskCategoryCounts": dict(sorted(category_counts.items())),
        "fetchErrorCount": sum(1 for item in trades if item.get("fetchError")),
    }


def risk_samples(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = sorted(trades, key=lambda item: (item["riskAnnouncementCount"], -float(item.get("returnPct") or 0)), reverse=True)[:8]
    return [
        {
            "ts_code": item["ts_code"],
            "name": item["name"],
            "entryDate": item["entryDate"],
            "returnPct": item["returnPct"],
            "netPnl": item["netPnl"],
            "riskAnnouncementCount": item["riskAnnouncementCount"],
            "riskCategories": item["riskCategories"],
            "riskAnnouncements": item["riskAnnouncements"][:3],
            "fetchError": item.get("fetchError"),
        }
        for item in selected
    ]


def fetch_cninfo_announcements(session: requests.Session, code: str, start: date, end: date, page_size: int) -> list[dict[str, Any]]:
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    payload = {
        "stock": f"{code},{cninfo_org_id(code)}",
        "tabName": "fulltext",
        "pageSize": str(page_size),
        "pageNum": "1",
        "column": "",
        "category": "",
        "plate": "",
        "seDate": f"{start.isoformat()}~{end.isoformat()}",
        "searchkey": "",
        "secid": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://www.cninfo.com.cn/new/disclosure",
        "Origin": "https://www.cninfo.com.cn",
    }
    response = session.post(url, data=payload, headers=headers, timeout=20)
    response.raise_for_status()
    data = response.json()
    rows = []
    for item in data.get("announcements", []) or []:
        rows.append(
            {
                "title": clean_title(item.get("announcementTitle", "")),
                "type": item.get("announcementTypeName", "") or "",
                "date": cninfo_ts_to_date(item.get("announcementTime")),
                "url": f"https://www.cninfo.com.cn/new/disclosure/detail?annoId={item.get('announcementId', '')}",
            }
        )
    return rows


def classify_announcement(item: dict[str, Any]) -> dict[str, Any]:
    text = f"{item.get('title', '')} {item.get('type', '')}"
    categories = [category for category, keywords in RISK_KEYWORDS.items() if any(keyword in text for keyword in keywords)]
    return {**item, "riskCategories": categories}


def cninfo_org_id(code: str) -> str:
    if code.startswith("6"):
        return f"gssh0{code}"
    if code.startswith(("8", "4")):
        return f"gsbj0{code}"
    return f"gssz0{code}"


def stock_code(ts_code: str) -> str:
    return str(ts_code).split(".")[0]


def cninfo_ts_to_date(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000).strftime("%Y-%m-%d")
    return str(value)[:10] if value else ""


def clean_title(value: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", str(value))).strip()


def avg(values: list[Any]) -> float | None:
    selected = [float(value) for value in values if finite(value)]
    return mean(selected) if selected else None


def render_review(output: dict[str, Any]) -> str:
    lines = [
        f"# {output['runId']}",
        "",
        "## 结论摘要",
        "",
        f"- baseline: `{output['baselineRun']}`",
        f"- candidate: `{output['candidateRun']}`",
        f"- mode: `{output['mode']}`",
        f"- lookbackDays: `{output['lookbackDays']}`",
        "",
        "## 关键窗口",
        "",
        "| 窗口 | 替换净差 | 候选独有 | 候选风险覆盖 | 候选亏损风险覆盖 | 基准独有风险覆盖 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in comparison_items(output):
        candidate = item["candidateOnly"]
        candidate_losses = item["candidateOnlyLosses"]
        baseline = item["baselineOnly"]
        lines.append(
            f"| `{item['label']}` | `{item['replacementNetPnlDelta']:.2f}` | `{candidate['count']}` | "
            f"`{format_ratio(candidate['riskTradeRatio'])}` | `{format_ratio(candidate_losses['riskTradeRatio'])}` | `{format_ratio(baseline['riskTradeRatio'])}` |"
        )
    return "\n".join(lines) + "\n"


def comparison_items(output: dict[str, Any]) -> list[dict[str, Any]]:
    if output["mode"] == "window_validation":
        return [item["comparison"] for item in output["windows"]]
    return output["comparisons"]


def compact_summary(output: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for item in comparison_items(output):
        result[item["label"]] = {
            "replacementNetPnlDelta": item["replacementNetPnlDelta"],
            "candidateRiskTradeRatio": item["candidateOnly"]["riskTradeRatio"],
            "candidateLossRiskTradeRatio": item["candidateOnlyLosses"]["riskTradeRatio"],
            "baselineRiskTradeRatio": item["baselineOnly"]["riskTradeRatio"],
            "fetchErrors": item["candidateOnly"]["fetchErrorCount"] + item["baselineOnly"]["fetchErrorCount"],
        }
    return result


def format_ratio(value: Any) -> str:
    return "NA" if value is None else f"{float(value):.1%}"


if __name__ == "__main__":
    main()
