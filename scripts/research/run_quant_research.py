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
from backend.app.quant_research.execution import (
    ExecutionRuntime,
    InterruptedRun,
    RequestRejected,
    ResumeRun,
    RunFailed,
    StartRun,
    execute,
)
from backend.app.quant_research.runner import mark_stale_research_runs
from backend.app.quant_research.strategy_registry import list_strategy_definitions


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="执行质量门禁、冻结输入并运行已登记的可复现量化研究。")
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "configs" / "research" / "sentinel_etf_baseline.json",
        help="已提交且策略 ID 在静态登记表中的研究配置。",
    )
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--quality-run-id")
    identity.add_argument("--resume", metavar="RUN_ID")
    identity.add_argument(
        "--list-strategies",
        action="store_true",
        help="列出源码静态登记策略；不连接数据库。",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPO_ROOT / "outputs" / "research-runs",
    )
    parser.add_argument("--database-url", default=DATABASE_URL)
    parser.add_argument("--test-mode", action="store_true", help="仅测试：允许没有 APP_GIT_COMMIT。")
    parser.add_argument(
        "--stale-after-seconds",
        type=int,
        default=300,
        help="启动时把超过该心跳阈值的 running 研究标记为 interrupted。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list_strategies:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "strategies": [
                        {
                            "strategyId": definition.strategy_id,
                            "strategyVersion": definition.strategy_version,
                            "scope": definition.scope,
                            "requiredInputs": list(definition.required_tables),
                            "exampleConfig": definition.example_config,
                            "walkForwardBenchmarkSource": (
                                definition.walk_forward_benchmark_source
                            ),
                        }
                        for definition in list_strategy_definitions()
                    ],
                    "boundaries": {
                        "researchOnly": True,
                        "executionEnabled": False,
                        "brokerConnected": False,
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    try:
        engine = create_engine(args.database_url, pool_pre_ping=True)
        try:
            with Session(engine) as db:
                stale_run_ids = mark_stale_research_runs(
                    db,
                    args.output_root,
                    stale_after_seconds=args.stale_after_seconds,
                )
                runtime = ExecutionRuntime(
                    registry_db=db,
                    output_root=args.output_root,
                    test_mode=args.test_mode,
                )
                if args.resume:
                    request = ResumeRun(run_id=args.resume)
                else:
                    config = json.loads(args.config.read_text(encoding="utf-8"))
                    quality_run = db.get(DataQualityRun, args.quality_run_id)
                    if quality_run is None:
                        raise ValueError("quality-run-id 不存在")
                    config["qualityRunId"] = quality_run.id
                    request = StartRun(config=config)
                result = execute(runtime, request)
                if isinstance(result, InterruptedRun):
                    raise RuntimeError(result.reason)
        finally:
            engine.dispose()
    except (RequestRejected, RunFailed) as exc:
        cause = exc.cause
        print(
            json.dumps(
                {"status": "failed", "error": f"{type(cause).__name__}: {cause}"},
                ensure_ascii=False,
            )
        )
        return 3

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
                "resumed": bool(args.resume),
                "interruptedStaleRunIds": stale_run_ids,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
