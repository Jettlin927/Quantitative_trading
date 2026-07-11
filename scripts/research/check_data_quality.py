#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.data_quality.contracts import QualityCheckContract, SUPPORTED_DATASETS
from backend.app.data_quality.runner import run_data_quality_check
from backend.app.database import DATABASE_URL


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日期必须为 YYYY-MM-DD") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按显式研究切片执行只读数据质量检查并登记结果。")
    parser.add_argument("--scope", required=True, choices=["a_share_cross_section", "etf_time_series"])
    parser.add_argument("--start-date", required=True, type=parse_date)
    parser.add_argument("--end-date", required=True, type=parse_date)
    parser.add_argument("--universe", required=True, nargs="+", help="空格或逗号分隔的证券代码。")
    parser.add_argument(
        "--universe-type",
        choices=["explicit_snapshot", "static_current", "industry_membership"],
        default="explicit_snapshot",
    )
    parser.add_argument("--universe-source", help="股票池文件、配置或历史成员来源标识。")
    parser.add_argument("--universe-as-of-date", type=parse_date, help="显式快照的形成日期。")
    parser.add_argument("--required-datasets", nargs="*", choices=sorted(SUPPORTED_DATASETS), default=[])
    parser.add_argument("--benchmark")
    parser.add_argument("--statement-timeout-ms", type=int, default=30_000)
    parser.add_argument("--code-commit")
    parser.add_argument("--database-url", default=DATABASE_URL)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    universe = [code for item in args.universe for code in item.split(",") if code.strip()]
    try:
        contract = QualityCheckContract.create(
            scope=args.scope,
            start_date=args.start_date,
            end_date=args.end_date,
            universe=universe,
            universe_type=args.universe_type,
            universe_source=args.universe_source,
            universe_as_of_date=args.universe_as_of_date,
            required_datasets=args.required_datasets,
            benchmark=args.benchmark,
            statement_timeout_ms=args.statement_timeout_ms,
        )
        engine = create_engine(args.database_url, pool_pre_ping=True)
        try:
            with Session(engine) as db:
                report = run_data_quality_check(db, contract, code_commit=args.code_commit)
        finally:
            engine.dispose()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 3

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return {"ready": 0, "ready_with_warnings": 0, "blocked": 2, "failed": 3}.get(report["status"], 3)


if __name__ == "__main__":
    raise SystemExit(main())
