#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.database import DATABASE_URL
from backend.app.models import DataQualityRun
from backend.app.quant_research.runner import run_quant_research


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="执行质量门禁、冻结输入并运行可复现 ETF sentinel。")
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "configs" / "research" / "sentinel_etf_baseline.json",
    )
    parser.add_argument("--quality-run-id", required=True)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPO_ROOT / "outputs" / "research-runs",
    )
    parser.add_argument("--database-url", default=DATABASE_URL)
    parser.add_argument("--test-mode", action="store_true", help="仅测试：允许没有 APP_GIT_COMMIT。")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        engine = create_engine(args.database_url, pool_pre_ping=True)
        try:
            with Session(engine) as db:
                quality_run = db.get(DataQualityRun, args.quality_run_id)
                if quality_run is None:
                    raise ValueError("quality-run-id 不存在")
                config["qualityRunId"] = quality_run.id
                result = run_quant_research(
                    db,
                    config,
                    args.output_root,
                    test_mode=args.test_mode,
                )
        finally:
            engine.dispose()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 3
    print(
        json.dumps(
            {
                "status": "succeeded",
                "runId": result.run_id,
                "artifactRoot": str(result.path),
                "reproducibilityKey": result.manifest["reproducibilityKey"],
                "resultFingerprint": result.manifest["resultFingerprint"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
