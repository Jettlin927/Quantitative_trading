from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Callable
from uuid import uuid4

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import current_schema_heads
from ..models import DataQualityRun, DataSnapshot, ResearchRun
from .artifacts import (
    ArtifactIntegrityError,
    atomic_write_json,
    read_canonical_csv_gz,
    sha256_file,
    verify_csv_artifact,
    verify_file_artifact,
    write_dataframe_csv_gz,
)
from .baselines import (
    load_adjusted_etf_prices,
    open_strategy_inputs,
    summarize_sentinel_metrics,
    validate_explicit_universe,
)
from .evaluation import (
    build_capacity_evidence,
    build_cost_stress_config,
    build_oos_metrics,
    validate_oos_metrics_contract,
)
from .manifest import (
    build_environment_fingerprint,
    build_research_manifest,
    build_result_fingerprint,
)
from .metrics import summarize_execution_metrics
from .reporting import summarize_nav_window
from .risk import (
    RISK_CONTRIBUTION_COLUMNS,
    RISK_EXPOSURE_COLUMNS,
    calculate_frozen_risk_frames,
    validate_risk_artifacts,
)
from .run_config import (
    FormalRunConfigurationError,
    build_parameter_neighborhood_configs,
    build_reproducibility_key,
    canonical_sha256,
    canonical_run_config_sha256,
    validate_research_pass_policy,
    validate_risk_policy,
    validate_run_config,
)
from .snapshot import (
    SnapshotCapacityPolicy,
    SnapshotIntegrityError,
    SnapshotResult,
    freeze_input_snapshot,
    materialize_snapshot_inputs,
    validate_quality_gate,
    verify_materialized_inputs,
    verify_snapshot,
    verify_snapshot_identity,
)
from .strategy_registry import StrategyDefinition, resolve_strategy_definition
from .validation import (
    WALK_FORWARD_METRIC_COLUMNS,
    WALK_FORWARD_WINDOW_COLUMNS,
    evaluate_walk_forward,
    validate_validation_policy,
)


TARGET_COLUMNS = ("signal_date", "available_date", "ts_code", "target_weight")
NAV_COLUMNS = (
    "trade_date",
    "nav",
    "cash_weight",
    "gross_exposure",
    "executed_signal_date",
    "traded_weight",
    "one_way_turnover",
    "transaction_cost_rate",
    "blocked_buys",
    "blocked_sells",
    "unfilled_target_weight",
    "carried_valuation_count",
)
BENCHMARK_NAV_COLUMNS = ("trade_date", "nav")
REBALANCE_REQUEST_COLUMNS = (
    "execution_date",
    "signal_date",
    "ts_code",
    "requested_change",
    "side",
)
REBALANCE_EXECUTION_COLUMNS = (
    "execution_date",
    "signal_date",
    "ts_code",
    "requested_change",
    "executed_change",
    "blocked_change",
    "status",
    "reason",
    "transaction_cost_rate",
)
POSITION_COLUMNS = ("trade_date", "ts_code", "close_weight")
STAGES = (
    "quality_gate",
    "input_snapshot",
    "features_targets",
    "simulation",
    "metrics",
    "manifest",
    "finalize",
)
INTERRUPTIBLE_STAGES = {"input_snapshot", "simulation", "finalize"}
CHECKPOINT_SCHEMA_VERSION = 1
ARTIFACT_SCHEMA_VERSION = 5


class ResumeError(RuntimeError):
    pass


class ResumeIdentityError(ResumeError):
    pass


class ResumeIntegrityError(ResumeError):
    pass


class InjectedResearchInterruption(BaseException):
    """测试专用的硬中断；故意绕过业务异常清理，模拟进程被杀。"""


class ResearchStopRequested(RuntimeError):
    """编排器在阶段安全点请求停止，保留 checkpoint 供审计或同身份恢复。"""


@dataclass(frozen=True)
class ResearchRunResult:
    run_id: str
    path: Path
    manifest: dict[str, Any]


def run_quant_research(
    registry_db: Session,
    config: dict[str, Any],
    output_root: Path,
    *,
    code_commit: str | None = None,
    schema_revision: str | None = None,
    test_mode: bool = False,
    capacity_policy: SnapshotCapacityPolicy | None = None,
    interrupt_after_stage: str | None = None,
    formal_research_id: str | None = None,
    orchestration_attempt_id: str | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ResearchRunResult:
    _validate_interrupt_stage(interrupt_after_stage)
    normalized = validate_run_config(config)
    strategy = resolve_strategy_definition(normalized)
    resolved_commit = _resolve_code_commit(code_commit, test_mode)
    resolved_revision = schema_revision or _read_schema_revision(registry_db)
    environment = build_environment_fingerprint(
        schema_revision=resolved_revision,
        code_commit=resolved_commit,
    )
    config_sha256 = canonical_run_config_sha256(normalized)
    output_root = Path(output_root)
    runs_root = output_root / "runs"
    snapshots_root = output_root / "snapshots"
    runs_root.mkdir(parents=True, exist_ok=True)
    snapshots_root.mkdir(parents=True, exist_ok=True)
    run_id = str(uuid4())
    temporary = runs_root / f".{run_id}.tmp"
    final_path = runs_root / run_id
    temporary.mkdir()
    now = datetime.now(timezone.utc)
    run = ResearchRun(
        run_id=run_id,
        formal_research_id=formal_research_id,
        orchestration_attempt_id=orchestration_attempt_id,
        reproducibility_key=None,
        strategy_id=normalized["strategyId"],
        status="running",
        stage="quality_gate",
        config=normalized,
        config_sha256=config_sha256,
        data_snapshot_id=None,
        code_commit=resolved_commit,
        environment_sha256=environment["sha256"],
        random_seed=normalized["randomSeed"],
        metrics={},
        result_fingerprint=None,
        artifact_root=str(final_path),
        started_at=now,
        heartbeat_at=now,
    )
    registry_db.add(run)
    registry_db.commit()

    try:
        atomic_write_json(temporary / "config.json", normalized)
        _initialize_checkpoint_index(temporary, run_id)
        return _execute_pipeline(
            registry_db,
            run,
            normalized,
            temporary,
            final_path,
            snapshots_root,
            environment,
            strategy,
            {},
            capacity_policy=capacity_policy,
            interrupt_after_stage=interrupt_after_stage,
            should_stop=should_stop,
        )
    except ResearchStopRequested as exc:
        _mark_run_interrupted(registry_db, run_id, temporary, exc)
        raise
    except Exception as exc:
        _mark_run_failed(registry_db, run_id, temporary, runs_root, exc)
        raise


def resume_quant_research(
    registry_db: Session,
    run_id: str,
    output_root: Path,
    *,
    code_commit: str | None = None,
    schema_revision: str | None = None,
    test_mode: bool = False,
    capacity_policy: SnapshotCapacityPolicy | None = None,
    interrupt_after_stage: str | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ResearchRunResult:
    _validate_interrupt_stage(interrupt_after_stage)
    run = registry_db.get(ResearchRun, run_id)
    if run is None:
        raise ResumeIdentityError(f"研究运行不存在：{run_id}")
    if run.status != "interrupted":
        raise ResumeIdentityError(f"只有 interrupted 研究运行可以显式续跑：{run.status}")

    output_root = Path(output_root)
    runs_root = output_root / "runs"
    snapshots_root = output_root / "snapshots"
    temporary = runs_root / f".{run_id}.tmp"
    final_path = runs_root / run_id
    _verify_artifact_root(run, final_path)
    working = _locate_resume_path(temporary, final_path)
    try:
        normalized = validate_run_config(dict(run.config), verify_universe_source=False)
    except Exception as exc:
        raise ResumeIdentityError("registry 中的研究配置已不再满足冻结合同") from exc
    try:
        strategy = resolve_strategy_definition(normalized)
    except ValueError as exc:
        raise ResumeIdentityError("registry 中的策略身份已不再满足静态登记合同") from exc
    resolved_commit = _resolve_code_commit(code_commit, test_mode)
    resolved_revision = schema_revision or _read_schema_revision(registry_db)
    environment = build_environment_fingerprint(
        schema_revision=resolved_revision,
        code_commit=resolved_commit,
    )
    _verify_resume_identity(run, normalized, working, resolved_commit, environment)
    checkpoints = _load_and_verify_checkpoints(
        registry_db,
        run,
        working,
        snapshots_root,
    )
    if working == final_path and set(checkpoints) != set(STAGES):
        raise ResumeIntegrityError("已提升的完成目录缺少 finalize checkpoint")
    _cleanup_uncommitted_stage_files(working, checkpoints)

    _append_recovery_event(
        working,
        {
            "event": "resume_started",
            "runId": run_id,
            "lastValidStage": next(reversed(checkpoints), None),
            "recordedAt": datetime.now(timezone.utc).isoformat(),
        },
    )
    run.status = "running"
    run.finished_at = None
    run.error = None
    run.heartbeat_at = datetime.now(timezone.utc)
    registry_db.commit()
    try:
        return _execute_pipeline(
            registry_db,
            run,
            normalized,
            working,
            final_path,
            snapshots_root,
            environment,
            strategy,
            checkpoints,
            capacity_policy=capacity_policy,
            interrupt_after_stage=interrupt_after_stage,
            should_stop=should_stop,
        )
    except ResearchStopRequested as exc:
        _mark_run_interrupted(registry_db, run_id, working, exc)
        raise
    except Exception as exc:
        _mark_run_failed(registry_db, run_id, working, runs_root, exc)
        raise


def mark_stale_research_runs(
    registry_db: Session,
    output_root: Path,
    *,
    stale_after_seconds: int = 300,
    now: datetime | None = None,
) -> list[str]:
    if isinstance(stale_after_seconds, bool) or stale_after_seconds <= 0:
        raise ValueError("stale_after_seconds 必须是正整数")
    detected_at = _as_utc(now or datetime.now(timezone.utc))
    cutoff = detected_at - timedelta(seconds=int(stale_after_seconds))
    output_root = Path(output_root)
    marked: list[str] = []
    running = registry_db.scalars(
        select(ResearchRun)
        .where(ResearchRun.status == "running")
        .order_by(ResearchRun.started_at, ResearchRun.run_id)
    ).all()
    for run in running:
        heartbeat = _as_utc(run.heartbeat_at or run.started_at)
        if heartbeat > cutoff:
            continue
        temporary = output_root / "runs" / f".{run.run_id}.tmp"
        final_path = output_root / "runs" / run.run_id
        audit_root = temporary if temporary.is_dir() else final_path if final_path.is_dir() else None
        if audit_root is not None:
            _append_recovery_event(
                audit_root,
                {
                    "event": "stale_running_interrupted",
                    "runId": run.run_id,
                    "lastHeartbeatAt": heartbeat.isoformat(),
                    "staleAfterSeconds": int(stale_after_seconds),
                    "recordedAt": detected_at.isoformat(),
                },
            )
        run.status = "interrupted"
        run.error = (
            "StaleResearchRun: heartbeat 超过 "
            f"{int(stale_after_seconds)} 秒未更新；保留临时目录，等待显式 --resume。"
        )
        run.finished_at = detected_at
        run.heartbeat_at = detected_at
        marked.append(run.run_id)
    registry_db.commit()
    return marked


def _execute_pipeline(
    registry_db: Session,
    run: ResearchRun,
    normalized: dict[str, Any],
    working: Path,
    final_path: Path,
    snapshots_root: Path,
    environment: dict[str, Any],
    strategy: StrategyDefinition,
    checkpoints: dict[str, dict[str, Any]],
    *,
    capacity_policy: SnapshotCapacityPolicy | None,
    interrupt_after_stage: str | None,
    should_stop: Callable[[], bool] | None,
) -> ResearchRunResult:
    config_artifact = _json_file_artifact(working / "config.json")

    _raise_if_stop_requested(should_stop, "quality_gate")
    if "quality_gate" not in checkpoints:
        quality_contract = _quality_contract(validate_quality_gate(registry_db, normalized))
        quality_artifact = atomic_write_json(working / "quality.json", quality_contract)
        _write_checkpoint(
            registry_db,
            run,
            working,
            "quality_gate",
            inputs={"config": config_artifact},
            outputs={"quality": quality_artifact},
            checkpoints=checkpoints,
        )
        _maybe_interrupt(interrupt_after_stage, "quality_gate")
    else:
        quality_contract = _read_json(working / "quality.json", "quality.json")
        quality_artifact = _checkpoint_artifact(checkpoints, "quality_gate", "quality")

    _raise_if_stop_requested(should_stop, "input_snapshot")
    if "input_snapshot" not in checkpoints:
        if run.data_snapshot_id:
            snapshot = _load_registered_snapshot(registry_db, run, snapshots_root)
        else:
            snapshot = freeze_input_snapshot(
                registry_db,
                normalized,
                snapshots_root,
                capacity_policy=capacity_policy,
            )
        table_artifacts = materialize_snapshot_inputs(snapshot, working / "inputs")
        reproducibility_key = build_reproducibility_key(
            config_sha256=run.config_sha256,
            data_snapshot_id=snapshot.snapshot_id,
            code_commit=run.code_commit,
            environment_sha256=run.environment_sha256,
            random_seed=run.random_seed,
        )
        run.data_snapshot_id = snapshot.snapshot_id
        run.reproducibility_key = reproducibility_key
        registry_db.commit()
        _write_checkpoint(
            registry_db,
            run,
            working,
            "input_snapshot",
            inputs={"quality": quality_artifact},
            outputs={
                "snapshotId": snapshot.snapshot_id,
                "reproducibilityKey": reproducibility_key,
                "tableArtifacts": table_artifacts,
            },
            checkpoints=checkpoints,
        )
        _maybe_interrupt(interrupt_after_stage, "input_snapshot")
    else:
        snapshot = _load_registered_snapshot(registry_db, run, snapshots_root)
        table_artifacts = snapshot.manifest["tableArtifacts"]
        reproducibility_key = str(run.reproducibility_key)

    _raise_if_stop_requested(should_stop, "features_targets")
    if "features_targets" not in checkpoints:
        targets = _build_strategy_targets(
            strategy,
            working / "inputs",
            normalized,
            compressed=True,
            table_artifacts=table_artifacts,
        )
        targets_artifact = write_dataframe_csv_gz(
            working / "targets.csv.gz",
            targets,
            columns=TARGET_COLUMNS,
            natural_key=("signal_date", "ts_code"),
        )
        _write_checkpoint(
            registry_db,
            run,
            working,
            "features_targets",
            inputs={
                "snapshotId": snapshot.snapshot_id,
                "tableContentSha256": _table_content_hashes(table_artifacts),
            },
            outputs={"targets": targets_artifact},
            checkpoints=checkpoints,
        )
        _maybe_interrupt(interrupt_after_stage, "features_targets")
    else:
        targets_artifact = _checkpoint_artifact(checkpoints, "features_targets", "targets")

    _raise_if_stop_requested(should_stop, "simulation")
    if "simulation" not in checkpoints:
        targets = read_canonical_csv_gz(working / "targets.csv.gz")
        simulation, _calendar = _simulate_strategy_targets(
            strategy,
            working / "inputs",
            normalized,
            targets,
            compressed=True,
            table_artifacts=table_artifacts,
        )
        nav = simulation.nav
        nav_artifact = write_dataframe_csv_gz(
            working / "nav.csv.gz",
            nav,
            columns=NAV_COLUMNS,
            natural_key=("trade_date",),
        )
        requests_artifact = write_dataframe_csv_gz(
            working / "rebalance_requests.csv.gz",
            simulation.rebalance_requests,
            columns=REBALANCE_REQUEST_COLUMNS,
            natural_key=("execution_date", "ts_code"),
        )
        executions_artifact = write_dataframe_csv_gz(
            working / "rebalance_executions.csv.gz",
            simulation.rebalance_executions,
            columns=REBALANCE_EXECUTION_COLUMNS,
            natural_key=("execution_date", "ts_code"),
        )
        positions_artifact = write_dataframe_csv_gz(
            working / "positions.csv.gz",
            simulation.positions,
            columns=POSITION_COLUMNS,
            natural_key=("trade_date", "ts_code"),
        )
        _write_checkpoint(
            registry_db,
            run,
            working,
            "simulation",
            inputs={"targets": targets_artifact},
            outputs={
                "nav": nav_artifact,
                "rebalanceRequests": requests_artifact,
                "rebalanceExecutions": executions_artifact,
                "positions": positions_artifact,
            },
            checkpoints=checkpoints,
        )
        _maybe_interrupt(interrupt_after_stage, "simulation")
    else:
        nav_artifact = _checkpoint_artifact(checkpoints, "simulation", "nav")
        requests_artifact = _checkpoint_artifact(checkpoints, "simulation", "rebalanceRequests")
        executions_artifact = _checkpoint_artifact(checkpoints, "simulation", "rebalanceExecutions")
        positions_artifact = _checkpoint_artifact(checkpoints, "simulation", "positions")

    _raise_if_stop_requested(should_stop, "metrics")
    if "metrics" not in checkpoints:
        nav = read_canonical_csv_gz(working / "nav.csv.gz")
        metrics = _summarize_strategy_metrics(
            strategy,
            working / "inputs",
            normalized,
            nav,
            compressed=True,
            table_artifacts=table_artifacts,
        )
        requests = read_canonical_csv_gz(working / "rebalance_requests.csv.gz")
        executions = read_canonical_csv_gz(working / "rebalance_executions.csv.gz")
        positions = read_canonical_csv_gz(working / "positions.csv.gz")
        metrics.update(summarize_execution_metrics(nav, requests, executions, positions))
        benchmark_nav = _build_primary_benchmark_nav(
            strategy,
            working / "inputs",
            normalized,
            compressed=True,
            table_artifacts=table_artifacts,
        )
        benchmark_nav_artifact = write_dataframe_csv_gz(
            working / "benchmark_nav.csv.gz",
            benchmark_nav,
            columns=BENCHMARK_NAV_COLUMNS,
            natural_key=("trade_date",),
        )
        walk_forward_artifacts: dict[str, dict[str, Any]] = {}
        risk_artifacts: dict[str, dict[str, Any]] = {}
        risk = None
        metric_outputs: dict[str, Any] = {}
        if _walk_forward_enabled(normalized):
            windows, window_metrics, walk_forward_summary = _evaluate_walk_forward(
                working / "inputs",
                normalized,
                nav,
                strategy=strategy,
                compressed=True,
                table_artifacts=table_artifacts,
            )
            windows_artifact = write_dataframe_csv_gz(
                working / "walk_forward_windows.csv.gz",
                windows,
                columns=WALK_FORWARD_WINDOW_COLUMNS,
                natural_key=("window_id",),
            )
            window_metrics_artifact = write_dataframe_csv_gz(
                working / "walk_forward_metrics.csv.gz",
                window_metrics,
                columns=WALK_FORWARD_METRIC_COLUMNS,
                natural_key=("window_id",),
            )
            walk_forward_artifacts = {
                "walk_forward_windows.csv.gz": windows_artifact,
                "walk_forward_metrics.csv.gz": window_metrics_artifact,
            }
            metric_outputs.update(
                {
                    "walkForwardWindows": windows_artifact,
                    "walkForwardMetrics": window_metrics_artifact,
                }
            )
            metrics["walkForward"] = walk_forward_summary
        if _risk_enabled(normalized):
            risk = calculate_frozen_risk_frames(
                working / "inputs",
                normalized,
                nav,
                positions,
                compressed=True,
                table_artifacts=table_artifacts,
            )
            exposures_artifact = write_dataframe_csv_gz(
                working / "risk_exposures.csv.gz",
                risk.exposures,
                columns=RISK_EXPOSURE_COLUMNS,
                natural_key=("trade_date",),
            )
            contributions_artifact = write_dataframe_csv_gz(
                working / "risk_contributions.csv.gz",
                risk.contributions,
                columns=RISK_CONTRIBUTION_COLUMNS,
                natural_key=("trade_date", "ts_code"),
            )
            risk_artifacts = {
                "risk_exposures.csv.gz": exposures_artifact,
                "risk_contributions.csv.gz": contributions_artifact,
            }
            metric_outputs.update(
                {
                    "riskExposures": exposures_artifact,
                    "riskContributions": contributions_artifact,
                }
            )
        stressed_nav = nav
        if "evaluationPolicy" in normalized:
            targets = read_canonical_csv_gz(working / "targets.csv.gz")
            stressed_simulation, _stressed_calendar = _simulate_strategy_targets(
                strategy,
                working / "inputs",
                build_cost_stress_config(normalized),
                targets,
                compressed=True,
                table_artifacts=table_artifacts,
            )
            stressed_nav = stressed_simulation.nav
        parameter_neighborhood = (
            _evaluate_parameter_neighborhood(
                working / "inputs",
                normalized,
                nav,
                benchmark_nav,
                compressed=True,
                table_artifacts=table_artifacts,
            )
            if "researchPassPolicy" in normalized
            else None
        )
        capacity_evidence = None
        if "researchPassPolicy" in normalized:
            test_split = next(
                item
                for item in normalized["evaluationSampleSplits"]
                if item["role"] == "test_oos"
            )
            nav_dates = pd.to_datetime(nav["trade_date"])
            oos_dates = pd.DatetimeIndex(
                nav_dates[
                    nav_dates.between(
                        pd.Timestamp(test_split["startDate"]),
                        pd.Timestamp(test_split["endDate"]),
                    )
                ]
            )
            capacity_evidence = build_capacity_evidence(
                normalized,
                requests,
                _load_market_amount_bars(
                    working / "inputs",
                    normalized,
                    compressed=True,
                    table_artifacts=table_artifacts,
                ),
                dates=oos_dates,
            )
        oos_metrics = build_oos_metrics(
            normalized,
            nav,
            benchmark_nav,
            requests,
            executions,
            positions,
            stressed_nav,
            walk_forward=metrics.get("walkForward"),
            parameter_neighborhood=parameter_neighborhood,
            capacity=capacity_evidence,
            risk_exposures=(risk.exposures if risk is not None else None),
            risk_contributions=(
                risk.contributions if risk is not None else None
            ),
        )
        limitations = sorted(set(strategy.limitations()))
        metrics_artifact = atomic_write_json(working / "metrics.json", metrics)
        oos_metrics_artifact = atomic_write_json(
            working / "oos_metrics.json", oos_metrics
        )
        limitations_artifact = atomic_write_json(working / "limitations.json", limitations)
        _write_checkpoint(
            registry_db,
            run,
            working,
            "metrics",
            inputs={
                "nav": nav_artifact,
                "rebalanceRequests": requests_artifact,
                "rebalanceExecutions": executions_artifact,
                "positions": positions_artifact,
            },
            outputs={
                "metrics": metrics_artifact,
                "oosMetrics": oos_metrics_artifact,
                "limitations": limitations_artifact,
                "primaryBenchmarkNav": benchmark_nav_artifact,
                **metric_outputs,
            },
            checkpoints=checkpoints,
        )
        _maybe_interrupt(interrupt_after_stage, "metrics")
    else:
        metrics = _read_json(working / "metrics.json", "metrics.json")
        oos_metrics_artifact = _checkpoint_artifact(
            checkpoints, "metrics", "oosMetrics"
        )
        limitations = _read_json(working / "limitations.json", "limitations.json")
        metrics_artifact = _checkpoint_artifact(checkpoints, "metrics", "metrics")
        limitations_artifact = _checkpoint_artifact(checkpoints, "metrics", "limitations")
        benchmark_nav_artifact = _checkpoint_artifact(
            checkpoints, "metrics", "primaryBenchmarkNav"
        )
        walk_forward_artifacts = {}
        if _walk_forward_enabled(normalized):
            walk_forward_artifacts = {
                "walk_forward_windows.csv.gz": _checkpoint_artifact(
                    checkpoints, "metrics", "walkForwardWindows"
                ),
                "walk_forward_metrics.csv.gz": _checkpoint_artifact(
                    checkpoints, "metrics", "walkForwardMetrics"
                ),
            }
        risk_artifacts = {}
        if _risk_enabled(normalized):
            risk_artifacts = {
                "risk_exposures.csv.gz": _checkpoint_artifact(
                    checkpoints, "metrics", "riskExposures"
                ),
                "risk_contributions.csv.gz": _checkpoint_artifact(
                    checkpoints, "metrics", "riskContributions"
                ),
            }

    _raise_if_stop_requested(should_stop, "manifest")
    if "manifest" not in checkpoints:
        artifact_hashes = {
            **{
                f"inputs/{Path(artifact['filename']).name}": artifact
                for artifact in table_artifacts.values()
            },
            "quality.json": quality_artifact,
            "targets.csv.gz": targets_artifact,
            "nav.csv.gz": nav_artifact,
            "benchmark_nav.csv.gz": benchmark_nav_artifact,
            "rebalance_requests.csv.gz": requests_artifact,
            "rebalance_executions.csv.gz": executions_artifact,
            "positions.csv.gz": positions_artifact,
            "metrics.json": metrics_artifact,
            "oos_metrics.json": oos_metrics_artifact,
            "limitations.json": limitations_artifact,
            **walk_forward_artifacts,
            **risk_artifacts,
        }
        manifest = build_research_manifest(
            run_id=run.run_id,
            reproducibility_key=reproducibility_key,
            strategy_id=normalized["strategyId"],
            config=normalized,
            config_sha256=run.config_sha256,
            data_snapshot={
                "schemaVersion": snapshot.manifest["schemaVersion"],
                "snapshotId": snapshot.snapshot_id,
                "relativePath": "inputs",
                "scope": snapshot.manifest["scope"],
                "warmupStart": snapshot.manifest["warmupStart"],
                "startDate": snapshot.manifest["startDate"],
                "endDate": snapshot.manifest["endDate"],
                "benchmark": snapshot.manifest["benchmark"],
                "universeHash": snapshot.manifest["universeHash"],
                "universeSourceArtifact": snapshot.manifest[
                    "universeSourceArtifact"
                ],
                "requiredDatasets": snapshot.manifest["requiredDatasets"],
                "tableArtifacts": table_artifacts,
                "rowCounts": snapshot.manifest["rowCounts"],
                "transaction": snapshot.manifest["transaction"],
            },
            quality_run=quality_contract,
            universe={
                **normalized["universe"],
                "artifactContentSha256": table_artifacts["universe"]["contentSha256"],
            },
            random_seed=normalized["randomSeed"],
            environment=environment,
            limitations=limitations,
            artifact_hashes=artifact_hashes,
            artifact_schema_version=ARTIFACT_SCHEMA_VERSION,
        )
        manifest_artifact = atomic_write_json(working / "manifest.json", manifest)
        _write_checkpoint(
            registry_db,
            run,
            working,
            "manifest",
            inputs={"artifactContentSha256": _artifact_content_hashes(artifact_hashes)},
            outputs={
                "manifest": manifest_artifact,
                "resultFingerprint": manifest["resultFingerprint"],
            },
            checkpoints=checkpoints,
        )
        _maybe_interrupt(interrupt_after_stage, "manifest")
    else:
        manifest = _read_json(working / "manifest.json", "manifest.json")
        manifest_artifact = _checkpoint_artifact(checkpoints, "manifest", "manifest")

    _raise_if_stop_requested(should_stop, "finalize")
    if "finalize" not in checkpoints:
        validate_research_archive(working)
        _write_checkpoint(
            registry_db,
            run,
            working,
            "finalize",
            inputs={"manifest": manifest_artifact},
            outputs={"resultFingerprint": manifest["resultFingerprint"]},
            checkpoints=checkpoints,
        )
        _maybe_interrupt(interrupt_after_stage, "finalize")

    if working != final_path:
        _promote_run(working, final_path)
    manifest, _config = validate_research_archive(final_path)
    run.status = "succeeded"
    run.stage = "finalized"
    run.metrics = metrics
    run.result_fingerprint = manifest["resultFingerprint"]
    run.finished_at = datetime.now(timezone.utc)
    run.heartbeat_at = run.finished_at
    run.error = None
    run.artifact_root = str(final_path)
    registry_db.commit()
    return ResearchRunResult(run_id=run.run_id, path=final_path, manifest=manifest)


def validate_research_archive(run_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    run_path = Path(run_path)
    manifest = _read_json(run_path / "manifest.json", "manifest.json")
    try:
        config = validate_run_config(
            _read_json(run_path / "config.json", "config.json"),
            verify_universe_source=False,
        )
    except Exception as exc:
        raise SnapshotIntegrityError("归档 config.json 无效") from exc
    try:
        resolve_strategy_definition(config)
    except ValueError as exc:
        raise SnapshotIntegrityError("归档策略身份无效") from exc
    if manifest.get("runId") != run_path.name and not run_path.name.startswith(f".{manifest.get('runId')}."):
        raise SnapshotIntegrityError("运行目录与 manifest runId 不一致")
    if canonical_run_config_sha256(config) != manifest.get("configSha256"):
        raise SnapshotIntegrityError("config.json 与 manifest configSha256 不一致")
    if canonical_run_config_sha256(manifest.get("config") or {}) != manifest.get("configSha256"):
        raise SnapshotIntegrityError("manifest config 与 configSha256 不一致")
    if canonical_sha256(config) != canonical_sha256(manifest["config"]):
        raise SnapshotIntegrityError("config.json 与 manifest config 不一致")
    verify_snapshot_identity(manifest["dataSnapshot"])
    table_artifacts = manifest["dataSnapshot"]["tableArtifacts"]
    verify_materialized_inputs(run_path / "inputs", table_artifacts)
    environment_contract = dict(manifest["environment"])
    environment_sha256 = environment_contract.pop("sha256", None)
    if canonical_sha256(environment_contract) != environment_sha256:
        raise SnapshotIntegrityError("manifest environment SHA-256 不一致")
    identity = build_reproducibility_key(
        config_sha256=manifest["configSha256"],
        data_snapshot_id=manifest["dataSnapshot"]["snapshotId"],
        code_commit=manifest["environment"]["appGitCommit"],
        environment_sha256=manifest["environment"]["sha256"],
        random_seed=manifest["randomSeed"],
    )
    if identity != manifest.get("reproducibilityKey"):
        raise SnapshotIntegrityError("manifest reproducibilityKey 与冻结身份不一致")
    if manifest.get("codeCommit") != manifest["environment"].get("appGitCommit"):
        raise SnapshotIntegrityError("manifest codeCommit 与 environment 不一致")
    if manifest.get("randomSeed") != config["randomSeed"]:
        raise SnapshotIntegrityError("manifest randomSeed 与 config 不一致")
    artifact_schema_version = manifest.get("artifactSchemaVersion", 1)
    if artifact_schema_version not in {1, 2, 3, 4, 5}:
        raise SnapshotIntegrityError("manifest artifactSchemaVersion 不受支持")
    walk_forward_enabled = _walk_forward_enabled(config)
    if walk_forward_enabled and artifact_schema_version < 2:
        raise SnapshotIntegrityError("walk-forward 工件只允许归档 schema v2+")
    risk_enabled = _risk_enabled(config)
    if risk_enabled and artifact_schema_version < 2:
        raise SnapshotIntegrityError("风险工件只允许归档 schema v2+")

    expected = manifest["artifactHashes"]
    _validate_manifest_input_artifacts(
        expected,
        table_artifacts,
        artifact_schema_version,
        walk_forward_enabled=walk_forward_enabled,
        risk_enabled=risk_enabled,
    )
    if build_result_fingerprint(expected) != manifest.get("resultFingerprint"):
        raise SnapshotIntegrityError("manifest resultFingerprint 与产物哈希不一致")
    csv_contracts = {
        "targets.csv.gz": (TARGET_COLUMNS, ("signal_date", "ts_code")),
        "nav.csv.gz": (NAV_COLUMNS, ("trade_date",)),
    }
    if artifact_schema_version >= 2:
        csv_contracts.update(
            {
                "rebalance_requests.csv.gz": (
                    REBALANCE_REQUEST_COLUMNS,
                    ("execution_date", "ts_code"),
                ),
                "rebalance_executions.csv.gz": (
                    REBALANCE_EXECUTION_COLUMNS,
                    ("execution_date", "ts_code"),
                ),
                "positions.csv.gz": (POSITION_COLUMNS, ("trade_date", "ts_code")),
            }
        )
    if artifact_schema_version >= 3:
        csv_contracts["benchmark_nav.csv.gz"] = (
            BENCHMARK_NAV_COLUMNS,
            ("trade_date",),
        )
    if walk_forward_enabled:
        csv_contracts.update(
            {
                "walk_forward_windows.csv.gz": (
                    WALK_FORWARD_WINDOW_COLUMNS,
                    ("window_id",),
                ),
                "walk_forward_metrics.csv.gz": (
                    WALK_FORWARD_METRIC_COLUMNS,
                    ("window_id",),
                ),
            }
        )
    if risk_enabled:
        csv_contracts.update(
            {
                "risk_exposures.csv.gz": (
                    RISK_EXPOSURE_COLUMNS,
                    ("trade_date",),
                ),
                "risk_contributions.csv.gz": (
                    RISK_CONTRIBUTION_COLUMNS,
                    ("trade_date", "ts_code"),
                ),
            }
        )
    frames: dict[str, Any] = {}
    for name, (columns, natural_key) in csv_contracts.items():
        try:
            verify_csv_artifact(run_path / name, expected[name])
            frames[name] = _read_and_validate_output_csv(
                run_path / name,
                expected[name],
                columns,
                natural_key,
            )
        except (ArtifactIntegrityError, KeyError) as exc:
            raise SnapshotIntegrityError(f"归档研究产物无效：{name}") from exc
    json_artifacts = ["quality.json", "metrics.json", "limitations.json"]
    if artifact_schema_version >= 4:
        json_artifacts.append("oos_metrics.json")
    for name in json_artifacts:
        try:
            verify_file_artifact(run_path / name, expected[name])
        except (ArtifactIntegrityError, KeyError) as exc:
            raise SnapshotIntegrityError(f"归档研究产物无效：{name}") from exc
    quality = _read_json(run_path / "quality.json", "quality.json")
    limitations = _read_json(run_path / "limitations.json", "limitations.json")
    if quality != manifest.get("qualityRun") or limitations != manifest.get("limitations"):
        raise SnapshotIntegrityError("归档审计产物与 manifest 不一致")
    if artifact_schema_version >= 4:
        try:
            validate_oos_metrics_contract(
                _read_json(run_path / "oos_metrics.json", "oos_metrics.json"),
                config,
            )
        except ValueError as exc:
            raise SnapshotIntegrityError("归档 OOS 指标合同无效") from exc
    if artifact_schema_version >= 2:
        persisted_metrics = _read_json(run_path / "metrics.json", "metrics.json")
        try:
            recalculated_execution_metrics = summarize_execution_metrics(
                frames["nav.csv.gz"],
                frames["rebalance_requests.csv.gz"],
                frames["rebalance_executions.csv.gz"],
                frames["positions.csv.gz"],
            )
        except ValueError as exc:
            raise SnapshotIntegrityError("归档模拟账本无法对账") from exc
        if any(persisted_metrics.get(key) != value for key, value in recalculated_execution_metrics.items()):
            raise SnapshotIntegrityError("归档执行指标与模拟账本不一致")
        if walk_forward_enabled:
            expected_summary = _validate_walk_forward_frames(
                frames["walk_forward_windows.csv.gz"],
                frames["walk_forward_metrics.csv.gz"],
                config,
            )
            if persisted_metrics.get("walkForward") != expected_summary:
                raise SnapshotIntegrityError("归档 walk-forward 汇总与 OOS 工件不一致")
        elif "walkForward" in persisted_metrics:
            raise SnapshotIntegrityError("未启用 walk-forward 的归档包含额外汇总")
        if risk_enabled:
            try:
                validate_risk_artifacts(
                    frames["risk_exposures.csv.gz"],
                    frames["risk_contributions.csv.gz"],
                    config["riskPolicy"],
                )
            except ValueError as exc:
                raise SnapshotIntegrityError("归档风险工件无法对账") from exc
    _validate_archive_checkpoint_chain(run_path, manifest)
    return manifest, config


def reproduce_quant_research(run_path: Path) -> dict[str, Any]:
    run_path = Path(run_path)
    manifest, config = validate_research_archive(run_path)
    strategy = resolve_strategy_definition(config)
    table_artifacts = manifest["dataSnapshot"]["tableArtifacts"]
    expected = manifest["artifactHashes"]
    artifact_schema_version = manifest.get("artifactSchemaVersion", 1)
    with tempfile.TemporaryDirectory(prefix="quant-reproduce-") as temporary_name:
        temporary = Path(temporary_name)
        targets = _build_strategy_targets(
            strategy,
            run_path / "inputs",
            config,
            compressed=True,
            table_artifacts=table_artifacts,
        )
        targets_artifact = write_dataframe_csv_gz(
            temporary / "targets.csv.gz",
            targets,
            columns=TARGET_COLUMNS,
            natural_key=("signal_date", "ts_code"),
        )
        persisted_targets = read_canonical_csv_gz(temporary / "targets.csv.gz")
        simulation, _calendar = _simulate_strategy_targets(
            strategy,
            run_path / "inputs",
            config,
            persisted_targets,
            compressed=True,
            table_artifacts=table_artifacts,
        )
        nav = simulation.nav
        nav_artifact = write_dataframe_csv_gz(
            temporary / "nav.csv.gz",
            nav,
            columns=NAV_COLUMNS,
            natural_key=("trade_date",),
        )
        persisted_nav = read_canonical_csv_gz(temporary / "nav.csv.gz")
        actual = {
            "targets.csv.gz": targets_artifact,
            "nav.csv.gz": nav_artifact,
        }
        metrics_simulation = replace(simulation, nav=persisted_nav)
        if artifact_schema_version >= 2:
            actual["rebalance_requests.csv.gz"] = write_dataframe_csv_gz(
                temporary / "rebalance_requests.csv.gz",
                simulation.rebalance_requests,
                columns=REBALANCE_REQUEST_COLUMNS,
                natural_key=("execution_date", "ts_code"),
            )
            actual["rebalance_executions.csv.gz"] = write_dataframe_csv_gz(
                temporary / "rebalance_executions.csv.gz",
                simulation.rebalance_executions,
                columns=REBALANCE_EXECUTION_COLUMNS,
                natural_key=("execution_date", "ts_code"),
            )
            actual["positions.csv.gz"] = write_dataframe_csv_gz(
                temporary / "positions.csv.gz",
                simulation.positions,
                columns=POSITION_COLUMNS,
                natural_key=("trade_date", "ts_code"),
            )
            metrics_simulation = replace(
                simulation,
                nav=persisted_nav,
                rebalance_requests=read_canonical_csv_gz(
                    temporary / "rebalance_requests.csv.gz"
                ),
                rebalance_executions=read_canonical_csv_gz(
                    temporary / "rebalance_executions.csv.gz"
                ),
                positions=read_canonical_csv_gz(temporary / "positions.csv.gz"),
            )
        if artifact_schema_version >= 3:
            benchmark_nav = _build_primary_benchmark_nav(
                strategy,
                run_path / "inputs",
                config,
                compressed=True,
                table_artifacts=table_artifacts,
            )
            actual["benchmark_nav.csv.gz"] = write_dataframe_csv_gz(
                temporary / "benchmark_nav.csv.gz",
                benchmark_nav,
                columns=BENCHMARK_NAV_COLUMNS,
                natural_key=("trade_date",),
            )
        metrics = _summarize_reproduction_metrics(
            strategy,
            artifact_schema_version,
            run_path / "inputs",
            config,
            persisted_nav,
            metrics_simulation,
            table_artifacts,
        )
        if _walk_forward_enabled(config):
            windows, window_metrics, walk_forward_summary = _evaluate_walk_forward(
                run_path / "inputs",
                config,
                persisted_nav,
                strategy=strategy,
                compressed=True,
                table_artifacts=table_artifacts,
            )
            actual["walk_forward_windows.csv.gz"] = write_dataframe_csv_gz(
                temporary / "walk_forward_windows.csv.gz",
                windows,
                columns=WALK_FORWARD_WINDOW_COLUMNS,
                natural_key=("window_id",),
            )
            actual["walk_forward_metrics.csv.gz"] = write_dataframe_csv_gz(
                temporary / "walk_forward_metrics.csv.gz",
                window_metrics,
                columns=WALK_FORWARD_METRIC_COLUMNS,
                natural_key=("window_id",),
            )
            metrics["walkForward"] = walk_forward_summary
        risk = None
        if _risk_enabled(config):
            risk = calculate_frozen_risk_frames(
                run_path / "inputs",
                config,
                persisted_nav,
                metrics_simulation.positions,
                compressed=True,
                table_artifacts=table_artifacts,
            )
            actual["risk_exposures.csv.gz"] = write_dataframe_csv_gz(
                temporary / "risk_exposures.csv.gz",
                risk.exposures,
                columns=RISK_EXPOSURE_COLUMNS,
                natural_key=("trade_date",),
            )
            actual["risk_contributions.csv.gz"] = write_dataframe_csv_gz(
                temporary / "risk_contributions.csv.gz",
                risk.contributions,
                columns=RISK_CONTRIBUTION_COLUMNS,
                natural_key=("trade_date", "ts_code"),
            )
        if artifact_schema_version >= 4:
            stressed_nav = persisted_nav
            if "evaluationPolicy" in config:
                stressed_simulation, _stressed_calendar = _simulate_strategy_targets(
                    strategy,
                    run_path / "inputs",
                    build_cost_stress_config(config),
                    persisted_targets,
                    compressed=True,
                    table_artifacts=table_artifacts,
                )
                stressed_nav = stressed_simulation.nav
            parameter_neighborhood = (
                _evaluate_parameter_neighborhood(
                    run_path / "inputs",
                    config,
                    persisted_nav,
                    benchmark_nav,
                    compressed=True,
                    table_artifacts=table_artifacts,
                )
                if "researchPassPolicy" in config
                else None
            )
            capacity_evidence = None
            if "researchPassPolicy" in config:
                test_split = next(
                    item
                    for item in config["evaluationSampleSplits"]
                    if item["role"] == "test_oos"
                )
                nav_dates = pd.to_datetime(persisted_nav["trade_date"])
                oos_dates = pd.DatetimeIndex(
                    nav_dates[
                        nav_dates.between(
                            pd.Timestamp(test_split["startDate"]),
                            pd.Timestamp(test_split["endDate"]),
                        )
                    ]
                )
                capacity_evidence = build_capacity_evidence(
                    config,
                    metrics_simulation.rebalance_requests,
                    _load_market_amount_bars(
                        run_path / "inputs",
                        config,
                        compressed=True,
                        table_artifacts=table_artifacts,
                    ),
                    dates=oos_dates,
                )
            oos_metrics = build_oos_metrics(
                config,
                persisted_nav,
                benchmark_nav,
                metrics_simulation.rebalance_requests,
                metrics_simulation.rebalance_executions,
                metrics_simulation.positions,
                stressed_nav,
                walk_forward=metrics.get("walkForward"),
                parameter_neighborhood=parameter_neighborhood,
                capacity=capacity_evidence,
                risk_exposures=(risk.exposures if risk is not None else None),
                risk_contributions=(
                    risk.contributions if risk is not None else None
                ),
            )
            actual["oos_metrics.json"] = atomic_write_json(
                temporary / "oos_metrics.json", oos_metrics
            )
        actual["metrics.json"] = atomic_write_json(temporary / "metrics.json", metrics)
    mismatches = [
        name
        for name, artifact in actual.items()
        if artifact["contentSha256"] != expected[name]["contentSha256"]
        or artifact["fileSha256"] != expected[name]["fileSha256"]
    ]
    actual_fingerprint = build_result_fingerprint(actual)
    if actual_fingerprint != manifest["resultFingerprint"]:
        mismatches.append("resultFingerprint")
    return {
        "runId": manifest["runId"],
        "matches": not mismatches,
        "mismatches": sorted(set(mismatches)),
        "expectedResultFingerprint": manifest["resultFingerprint"],
        "actualResultFingerprint": actual_fingerprint,
    }


def _build_strategy_targets(strategy: StrategyDefinition, *args: Any, **kwargs: Any) -> Any:
    return strategy.build_targets(*args, **kwargs)


def _simulate_strategy_targets(strategy: StrategyDefinition, *args: Any, **kwargs: Any) -> Any:
    return strategy.simulate(*args, **kwargs)


def _summarize_strategy_metrics(strategy: StrategyDefinition, *args: Any, **kwargs: Any) -> Any:
    return strategy.summarize_metrics(*args, **kwargs)


def _summarize_reproduction_metrics(
    strategy: StrategyDefinition,
    artifact_schema_version: int,
    input_root: Path,
    config: dict[str, Any],
    nav: Any,
    simulation: Any,
    table_artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if artifact_schema_version == 1 and config["strategyId"] == "sentinel_etf_baseline":
        return summarize_sentinel_metrics(
            input_root,
            config,
            nav,
            compressed=True,
            table_artifacts=table_artifacts,
        )
    metrics = _summarize_strategy_metrics(
        strategy,
        input_root,
        config,
        nav,
        compressed=True,
        table_artifacts=table_artifacts,
    )
    if artifact_schema_version >= 2:
        metrics.update(
            summarize_execution_metrics(
                nav,
                simulation.rebalance_requests,
                simulation.rebalance_executions,
                simulation.positions,
            )
        )
    return metrics


def _walk_forward_enabled(config: dict[str, Any]) -> bool:
    return validate_validation_policy(config.get("validationPolicy"))["mode"] != "none"


def _risk_enabled(config: dict[str, Any]) -> bool:
    return validate_risk_policy(config.get("riskPolicy"))["mode"] != "none"


def _build_primary_benchmark_nav(
    strategy: StrategyDefinition,
    input_root: Path,
    config: dict[str, Any],
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]],
) -> pd.DataFrame:
    """从冻结输入重建与策略指标同口径的主基准净值。"""

    _, reader = open_strategy_inputs(input_root, compressed, table_artifacts)
    research_start = pd.Timestamp(config["startDate"])
    research_end = pd.Timestamp(config["endDate"])
    source = strategy.walk_forward_benchmark_source
    if source == "universe_adjusted_etf":
        members = validate_explicit_universe(reader, config, compressed)
        prices = load_adjusted_etf_prices(reader, config, members)
        benchmark = prices[prices["trade_date"].between(research_start, research_end)][
            ["trade_date", "adj_open", "adj_close"]
        ].copy()
        if benchmark.empty:
            raise ValueError("冻结 ETF 主基准路径为空")
        opening_nav = pd.to_numeric(benchmark["adj_open"], errors="raise").iloc[0]
        benchmark["nav"] = pd.to_numeric(
            benchmark["adj_close"], errors="raise"
        ) / opening_nav
    elif source == "config_market_reference":
        benchmark = reader("index_daily_bars")
        benchmark["trade_date"] = pd.to_datetime(
            benchmark["trade_date"], errors="raise"
        )
        warmup_start = pd.Timestamp(config["warmupStart"])
        benchmark = benchmark[
            benchmark["ts_code"].eq(config["benchmark"])
            & benchmark["trade_date"].between(warmup_start, research_end)
        ][["trade_date", "close", "pre_close"]].copy()
        benchmark = benchmark.sort_values("trade_date", kind="stable")
        if benchmark.empty:
            raise ValueError("冻结市场主基准路径为空")
        benchmark["close"] = pd.to_numeric(benchmark["close"], errors="raise")
        benchmark["pre_close"] = pd.to_numeric(
            benchmark["pre_close"], errors="coerce"
        )
        research_benchmark = benchmark[
            benchmark["trade_date"].between(research_start, research_end)
        ].copy()
        if research_benchmark.empty:
            raise ValueError("冻结市场主基准研究区间为空")
        first_row = research_benchmark.iloc[0]
        opening_nav = first_row["pre_close"]
        if not math.isfinite(float(opening_nav)) or float(opening_nav) <= 0:
            prior = benchmark[benchmark["trade_date"] < first_row["trade_date"]]
            if prior.empty:
                raise ValueError("冻结市场主基准缺少研究边界前收盘或首日 pre_close")
            opening_nav = prior.iloc[-1]["close"]
        research_benchmark["nav"] = research_benchmark["close"] / float(
            opening_nav
        )
        benchmark = research_benchmark
    else:
        raise ValueError("策略主基准来源未登记")
    result = benchmark[["trade_date", "nav"]].copy()
    result = result.sort_values("trade_date", kind="stable").reset_index(drop=True)
    result["nav"] = pd.to_numeric(result["nav"], errors="raise")
    if (
        result.empty
        or not result["nav"].map(math.isfinite).all()
        or (result["nav"] <= 0).any()
    ):
        raise ValueError("冻结主基准净值非有限正数或为空")
    return result


def _evaluate_walk_forward(
    input_root: Path,
    config: dict[str, Any],
    nav: Any,
    *,
    strategy: StrategyDefinition,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]],
) -> tuple[Any, Any, dict[str, Any]]:
    _, reader = open_strategy_inputs(input_root, compressed, table_artifacts)
    benchmark_bars = reader("index_daily_bars")
    benchmark = config["benchmark"]
    if strategy.walk_forward_benchmark_source == "universe_adjusted_etf":
        members = validate_explicit_universe(reader, config, compressed)
        prices = load_adjusted_etf_prices(reader, config, members)
        benchmark = members[0]
        benchmark_bars = prices[["ts_code", "trade_date", "adj_close"]].rename(
            columns={"adj_close": "close"}
        )
    elif strategy.walk_forward_benchmark_source != "config_market_reference":
        raise ValueError("策略 walk-forward 主基准来源未登记")
    return evaluate_walk_forward(
        nav,
        benchmark_bars,
        benchmark=benchmark,
        research_start=config["startDate"],
        research_end=config["endDate"],
        policy=config.get("validationPolicy"),
    )


def _evaluate_parameter_neighborhood(
    input_root: Path,
    config: dict[str, Any],
    base_nav: pd.DataFrame,
    benchmark_nav: pd.DataFrame,
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    policy = validate_research_pass_policy(
        config.get("researchPassPolicy"), config
    )["parameterNeighborhood"]
    test_split = next(
        item
        for item in config["evaluationSampleSplits"]
        if item["role"] == "test_oos"
    )
    start = pd.Timestamp(test_split["startDate"])
    end = pd.Timestamp(test_split["endDate"])
    configurations: list[dict[str, Any]] = []
    for variant_id, candidate in build_parameter_neighborhood_configs(config):
        research_pass_policy = candidate.pop("researchPassPolicy")
        candidate = validate_run_config(candidate, verify_universe_source=False)
        candidate["researchPassPolicy"] = research_pass_policy
        candidate_strategy = resolve_strategy_definition(candidate)
        if variant_id == "base":
            candidate_nav = base_nav
        else:
            targets = _build_strategy_targets(
                candidate_strategy,
                input_root,
                candidate,
                compressed=compressed,
                table_artifacts=table_artifacts,
            )
            simulation, _calendar = _simulate_strategy_targets(
                candidate_strategy,
                input_root,
                candidate,
                targets,
                compressed=compressed,
                table_artifacts=table_artifacts,
            )
            candidate_nav = simulation.nav
        summary = summarize_nav_window(
            candidate_nav,
            start=start,
            end=end,
            benchmark_nav=benchmark_nav,
            include_extended=True,
        )
        changes = next(
            item["changes"]
            for item in policy["variants"]
            if item["id"] == variant_id
        )
        configurations.append(
            {
                "id": variant_id,
                "changes": changes,
                "configSha256": canonical_run_config_sha256(candidate),
                "totalReturn": summary["totalReturn"],
                "maxDrawdown": summary["maxDrawdown"],
            }
        )
    returns = [float(item["totalReturn"]) for item in configurations]
    maximum_difference = max(returns) - min(returns)
    minimum_return = min(returns)
    allowed_difference = float(policy["maximumAbsoluteOosReturnDifference"])
    allowed_minimum_return = float(policy["minimumOosTotalReturn"])
    return {
        "status": "complete",
        "policySha256": canonical_sha256(policy),
        "evaluatedConfigurations": len(configurations),
        "maximumAllowedAbsoluteOosReturnDifference": policy[
            "maximumAbsoluteOosReturnDifference"
        ],
        "minimumAllowedOosTotalReturn": policy["minimumOosTotalReturn"],
        "maximumObservedAbsoluteOosReturnDifference": maximum_difference,
        "minimumObservedOosTotalReturn": minimum_return,
        "passed": bool(
            maximum_difference <= allowed_difference
            and minimum_return >= allowed_minimum_return
        ),
        "configurations": configurations,
    }


def _load_market_amount_bars(
    input_root: Path,
    config: dict[str, Any],
    *,
    compressed: bool,
    table_artifacts: dict[str, dict[str, Any]],
) -> pd.DataFrame:
    _, reader = open_strategy_inputs(input_root, compressed, table_artifacts)
    table = (
        "fund_daily_bars"
        if config["scope"] == "etf_time_series"
        else "stock_daily_bars"
    )
    bars = reader(table)
    required = {"trade_date", "ts_code", "amount"}
    missing = sorted(required - set(bars.columns))
    if missing:
        raise ValueError("冻结市场成交额缺少字段：" + ", ".join(missing))
    return bars.loc[:, ["trade_date", "ts_code", "amount"]].copy()


def _initialize_checkpoint_index(working: Path, run_id: str) -> None:
    atomic_write_json(
        working / "checkpoints" / "index.json",
        {
            "schemaVersion": CHECKPOINT_SCHEMA_VERSION,
            "artifactSchemaVersion": ARTIFACT_SCHEMA_VERSION,
            "runId": run_id,
            "completed": [],
        },
    )


def _write_checkpoint(
    registry_db: Session,
    run: ResearchRun,
    working: Path,
    stage: str,
    *,
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    checkpoints: dict[str, dict[str, Any]],
) -> None:
    expected_stage = STAGES[len(checkpoints)] if len(checkpoints) < len(STAGES) else None
    if stage != expected_stage:
        raise RuntimeError(f"checkpoint 阶段顺序错误：expected={expected_stage}, actual={stage}")
    previous_hash = None
    if checkpoints:
        previous_hash = checkpoints[next(reversed(checkpoints))]["checkpointSha256"]
    document = {
        "schemaVersion": CHECKPOINT_SCHEMA_VERSION,
        "runId": run.run_id,
        "stage": stage,
        "completedAt": datetime.now(timezone.utc).isoformat(),
        "previousCheckpointSha256": previous_hash,
        "identity": _checkpoint_identity(run, include_snapshot=stage != "quality_gate"),
        "inputs": inputs,
        "outputs": outputs,
    }
    checkpoint_artifact = atomic_write_json(
        working / "checkpoints" / f"{stage}.json",
        document,
    )
    document["checkpointSha256"] = checkpoint_artifact["fileSha256"]
    index = _read_json(working / "checkpoints" / "index.json", "checkpoint index")
    expected_completed = [
        {"stage": name, "contentSha256": checkpoints[name]["checkpointSha256"]}
        for name in checkpoints
    ]
    if index.get("completed") != expected_completed:
        raise RuntimeError("checkpoint index 在写入阶段前已发生变化")
    index["completed"].append(
        {"stage": stage, "contentSha256": checkpoint_artifact["fileSha256"]}
    )
    atomic_write_json(working / "checkpoints" / "index.json", index)
    checkpoints[stage] = document
    run.stage = stage
    run.heartbeat_at = datetime.now(timezone.utc)
    registry_db.commit()


def _load_and_verify_checkpoints(
    registry_db: Session,
    run: ResearchRun,
    working: Path,
    snapshots_root: Path,
) -> dict[str, dict[str, Any]]:
    try:
        index = _read_json(working / "checkpoints" / "index.json", "checkpoint index")
        if isinstance(index, dict) and "artifactSchemaVersion" not in index:
            raise ResumeIdentityError("v1 未完成研究运行不能跨 artifact schema 续跑；请新建 run")
        if (
            index.get("schemaVersion") != CHECKPOINT_SCHEMA_VERSION
            or index.get("artifactSchemaVersion") != ARTIFACT_SCHEMA_VERSION
            or index.get("runId") != run.run_id
            or not isinstance(index.get("completed"), list)
        ):
            raise ResumeIntegrityError("checkpoint index 身份或 schema 无效")
        completed_entries = index["completed"]
        completed_names = [item.get("stage") for item in completed_entries if isinstance(item, dict)]
        if len(completed_names) != len(completed_entries):
            raise ResumeIntegrityError("checkpoint index 条目无效")
        if completed_names != list(STAGES[: len(completed_names)]):
            raise ResumeIntegrityError("checkpoint 必须是无缺口的阶段前缀")

        checkpoints: dict[str, dict[str, Any]] = {}
        previous_hash = None
        for entry in completed_entries:
            stage = entry["stage"]
            path = working / "checkpoints" / f"{stage}.json"
            actual_hash = sha256_file(path)
            if entry.get("contentSha256") != actual_hash:
                raise ResumeIntegrityError(f"checkpoint hash 不一致：{stage}")
            document = _read_json(path, f"checkpoint {stage}")
            if (
                document.get("schemaVersion") != CHECKPOINT_SCHEMA_VERSION
                or document.get("runId") != run.run_id
                or document.get("stage") != stage
                or document.get("previousCheckpointSha256") != previous_hash
                or not isinstance(document.get("inputs"), dict)
                or not isinstance(document.get("outputs"), dict)
            ):
                raise ResumeIntegrityError(f"checkpoint 合同无效：{stage}")
            _verify_checkpoint_identity(run, stage, document.get("identity"))
            document["checkpointSha256"] = actual_hash
            checkpoints[stage] = document
            previous_hash = actual_hash
        _verify_completed_stage_artifacts(registry_db, run, working, snapshots_root, checkpoints)
        if "input_snapshot" not in checkpoints and (
            run.data_snapshot_id is not None or run.reproducibility_key is not None
        ):
            if not run.data_snapshot_id or not run.reproducibility_key:
                raise ResumeIdentityError("未完成 snapshot checkpoint 的冻结身份不完整")
            expected_reproducibility_key = build_reproducibility_key(
                config_sha256=run.config_sha256,
                data_snapshot_id=run.data_snapshot_id,
                code_commit=run.code_commit,
                environment_sha256=run.environment_sha256,
                random_seed=run.random_seed,
            )
            if expected_reproducibility_key != run.reproducibility_key:
                raise ResumeIdentityError("未完成 snapshot checkpoint 的可复现键已变化")
            _load_registered_snapshot(registry_db, run, snapshots_root)
        return checkpoints
    except ResumeError:
        raise
    except (ArtifactIntegrityError, SnapshotIntegrityError, OSError, KeyError, TypeError, ValueError) as exc:
        raise ResumeIntegrityError(f"checkpoint 或已完成阶段产物损坏：{exc}") from exc


def _verify_completed_stage_artifacts(
    registry_db: Session,
    run: ResearchRun,
    working: Path,
    snapshots_root: Path,
    checkpoints: dict[str, dict[str, Any]],
) -> None:
    config_artifact = _json_file_artifact(working / "config.json")
    if "quality_gate" in checkpoints:
        quality = checkpoints["quality_gate"]
        if quality["inputs"].get("config") != config_artifact:
            raise ResumeIntegrityError("quality checkpoint 的 config 输入 hash 不一致")
        verify_file_artifact(
            working / "quality.json",
            _checkpoint_artifact(checkpoints, "quality_gate", "quality"),
        )
    if "input_snapshot" in checkpoints:
        snapshot = _load_registered_snapshot(registry_db, run, snapshots_root)
        output = checkpoints["input_snapshot"]["outputs"]
        if (
            output.get("snapshotId") != snapshot.snapshot_id
            or output.get("reproducibilityKey") != run.reproducibility_key
            or output.get("tableArtifacts") != snapshot.manifest["tableArtifacts"]
            or checkpoints["input_snapshot"]["inputs"].get("quality")
            != _checkpoint_artifact(checkpoints, "quality_gate", "quality")
        ):
            raise ResumeIntegrityError("input_snapshot checkpoint 与冻结输入不一致")
        verify_materialized_inputs(working / "inputs", snapshot.manifest["tableArtifacts"])
    if "features_targets" in checkpoints:
        snapshot_output = checkpoints["input_snapshot"]["outputs"]
        feature_inputs = checkpoints["features_targets"]["inputs"]
        if (
            feature_inputs.get("snapshotId") != snapshot_output.get("snapshotId")
            or feature_inputs.get("tableContentSha256")
            != _table_content_hashes(snapshot_output["tableArtifacts"])
        ):
            raise ResumeIntegrityError("features_targets checkpoint 输入 hash 不一致")
        verify_csv_artifact(
            working / "targets.csv.gz",
            _checkpoint_artifact(checkpoints, "features_targets", "targets"),
        )
    if "simulation" in checkpoints:
        if checkpoints["simulation"]["inputs"].get("targets") != _checkpoint_artifact(
            checkpoints, "features_targets", "targets"
        ):
            raise ResumeIntegrityError("simulation checkpoint 输入 hash 不一致")
        verify_csv_artifact(
            working / "nav.csv.gz",
            _checkpoint_artifact(checkpoints, "simulation", "nav"),
        )
        for filename, artifact_name in (
            ("rebalance_requests.csv.gz", "rebalanceRequests"),
            ("rebalance_executions.csv.gz", "rebalanceExecutions"),
            ("positions.csv.gz", "positions"),
        ):
            verify_csv_artifact(
                working / filename,
                _checkpoint_artifact(checkpoints, "simulation", artifact_name),
            )
    if "metrics" in checkpoints:
        expected_metric_inputs = {
            "nav": _checkpoint_artifact(checkpoints, "simulation", "nav"),
            "rebalanceRequests": _checkpoint_artifact(checkpoints, "simulation", "rebalanceRequests"),
            "rebalanceExecutions": _checkpoint_artifact(checkpoints, "simulation", "rebalanceExecutions"),
            "positions": _checkpoint_artifact(checkpoints, "simulation", "positions"),
        }
        if checkpoints["metrics"]["inputs"] != expected_metric_inputs:
            raise ResumeIntegrityError("metrics checkpoint 输入 hash 不一致")
        expected_metric_outputs = {
            "metrics": _checkpoint_artifact(checkpoints, "metrics", "metrics"),
            "oosMetrics": _checkpoint_artifact(
                checkpoints, "metrics", "oosMetrics"
            ),
            "limitations": _checkpoint_artifact(
                checkpoints, "metrics", "limitations"
            ),
        }
        verify_file_artifact(
            working / "metrics.json",
            expected_metric_outputs["metrics"],
        )
        verify_file_artifact(
            working / "oos_metrics.json",
            expected_metric_outputs["oosMetrics"],
        )
        verify_file_artifact(
            working / "limitations.json",
            expected_metric_outputs["limitations"],
        )
        benchmark_artifact = _checkpoint_artifact(
            checkpoints, "metrics", "primaryBenchmarkNav"
        )
        expected_metric_outputs["primaryBenchmarkNav"] = benchmark_artifact
        verify_csv_artifact(
            working / "benchmark_nav.csv.gz",
            benchmark_artifact,
        )
        if _walk_forward_enabled(dict(run.config or {})):
            for filename, artifact_name in (
                ("walk_forward_windows.csv.gz", "walkForwardWindows"),
                ("walk_forward_metrics.csv.gz", "walkForwardMetrics"),
            ):
                artifact = _checkpoint_artifact(
                    checkpoints, "metrics", artifact_name
                )
                expected_metric_outputs[artifact_name] = artifact
                verify_csv_artifact(working / filename, artifact)
        if _risk_enabled(dict(run.config or {})):
            for filename, artifact_name in (
                ("risk_exposures.csv.gz", "riskExposures"),
                ("risk_contributions.csv.gz", "riskContributions"),
            ):
                artifact = _checkpoint_artifact(
                    checkpoints, "metrics", artifact_name
                )
                expected_metric_outputs[artifact_name] = artifact
                verify_csv_artifact(working / filename, artifact)
        if checkpoints["metrics"]["outputs"] != expected_metric_outputs:
            raise ResumeIntegrityError("metrics checkpoint 输出集合不一致")
    if "manifest" in checkpoints:
        verify_file_artifact(
            working / "manifest.json",
            _checkpoint_artifact(checkpoints, "manifest", "manifest"),
        )
        manifest, _config = validate_research_archive(working)
        expected_hashes = _artifact_content_hashes(manifest["artifactHashes"])
        if (
            checkpoints["manifest"]["inputs"].get("artifactContentSha256") != expected_hashes
            or checkpoints["manifest"]["outputs"].get("resultFingerprint")
            != manifest["resultFingerprint"]
        ):
            raise ResumeIntegrityError("manifest checkpoint 与研究归档不一致")
    if "finalize" in checkpoints:
        if (
            checkpoints["finalize"]["inputs"].get("manifest")
            != _checkpoint_artifact(checkpoints, "manifest", "manifest")
            or checkpoints["finalize"]["outputs"].get("resultFingerprint")
            != checkpoints["manifest"]["outputs"].get("resultFingerprint")
        ):
            raise ResumeIntegrityError("finalize checkpoint 输入或输出 hash 不一致")
        validate_research_archive(working)


def _verify_resume_identity(
    run: ResearchRun,
    normalized: dict[str, Any],
    working: Path,
    resolved_commit: str,
    environment: dict[str, Any],
) -> None:
    if canonical_run_config_sha256(normalized) != run.config_sha256:
        raise ResumeIdentityError("研究配置或 configSha256 已变化，只能新建 run")
    if run.strategy_id != normalized["strategyId"] or run.random_seed != normalized["randomSeed"]:
        raise ResumeIdentityError("策略或 random seed 已变化，只能新建 run")
    if run.code_commit != resolved_commit:
        raise ResumeIdentityError("代码提交已变化，只能新建 run")
    if run.environment_sha256 != environment["sha256"]:
        raise ResumeIdentityError("运行环境指纹已变化，只能新建 run")
    archived_config = _read_json(working / "config.json", "config.json")
    if canonical_sha256(archived_config) != canonical_sha256(normalized):
        raise ResumeIdentityError("config.json 与 registry 配置不一致")


def _verify_checkpoint_identity(run: ResearchRun, stage: str, identity: Any) -> None:
    if not isinstance(identity, dict):
        raise ResumeIntegrityError(f"checkpoint identity 无效：{stage}")
    expected_static = {
        "strategyId": run.strategy_id,
        "strategyVersion": (run.config or {}).get("strategyVersion"),
        "configSha256": run.config_sha256,
        "codeCommit": run.code_commit,
        "environmentSha256": run.environment_sha256,
        "randomSeed": run.random_seed,
    }
    if any(identity.get(key) != value for key, value in expected_static.items()):
        raise ResumeIdentityError(f"checkpoint 静态身份与 registry 不一致：{stage}")
    expected_snapshot = None if stage == "quality_gate" else run.data_snapshot_id
    expected_repro = None if stage == "quality_gate" else run.reproducibility_key
    if (
        identity.get("dataSnapshotId") != expected_snapshot
        or identity.get("reproducibilityKey") != expected_repro
    ):
        raise ResumeIdentityError(f"checkpoint 快照或可复现键与 registry 不一致：{stage}")
    if stage != "quality_gate":
        expected = build_reproducibility_key(
            config_sha256=run.config_sha256,
            data_snapshot_id=run.data_snapshot_id,
            code_commit=run.code_commit,
            environment_sha256=run.environment_sha256,
            random_seed=run.random_seed,
        )
        if expected != run.reproducibility_key:
            raise ResumeIdentityError("registry reproducibility_key 与冻结身份不一致")


def _load_registered_snapshot(
    registry_db: Session,
    run: ResearchRun,
    _snapshots_root: Path,
) -> SnapshotResult:
    if not run.data_snapshot_id:
        raise ResumeIdentityError("已完成 input_snapshot checkpoint 缺少 data_snapshot_id")
    row = registry_db.get(DataSnapshot, run.data_snapshot_id)
    if row is None or row.status != "complete":
        raise ResumeIntegrityError("冻结 snapshot registry 不存在或不是 complete")
    registered_path = Path(row.artifact_root).expanduser()
    if not registered_path.is_absolute():
        registered_path = registered_path.resolve()
    manifest = verify_snapshot(registered_path)
    expected_registry = {
        "qualityRunId": row.quality_run_id,
        "scope": row.scope,
        "warmupStart": row.start_date.isoformat(),
        "endDate": row.end_date.isoformat(),
        "universeHash": row.universe_hash,
        "tableArtifacts": row.table_artifacts,
        "rowCounts": row.row_counts,
    }
    if {key: manifest.get(key) for key in expected_registry} != expected_registry:
        raise ResumeIntegrityError("snapshot 磁盘 manifest 与 registry 不一致")
    return SnapshotResult(
        snapshot_id=run.data_snapshot_id,
        path=registered_path,
        manifest=manifest,
        reused=True,
    )


def _checkpoint_identity(run: ResearchRun, *, include_snapshot: bool) -> dict[str, Any]:
    return {
        "strategyId": run.strategy_id,
        "strategyVersion": (run.config or {}).get("strategyVersion"),
        "configSha256": run.config_sha256,
        "codeCommit": run.code_commit,
        "environmentSha256": run.environment_sha256,
        "randomSeed": run.random_seed,
        "dataSnapshotId": run.data_snapshot_id if include_snapshot else None,
        "reproducibilityKey": run.reproducibility_key if include_snapshot else None,
    }


def _checkpoint_artifact(
    checkpoints: dict[str, dict[str, Any]],
    stage: str,
    name: str,
) -> dict[str, Any]:
    artifact = checkpoints[stage]["outputs"].get(name)
    if not isinstance(artifact, dict):
        raise ResumeIntegrityError(f"checkpoint 缺少 artifact：{stage}.{name}")
    return artifact


def _table_content_hashes(table_artifacts: dict[str, dict[str, Any]]) -> dict[str, str]:
    return {
        name: str(artifact["contentSha256"])
        for name, artifact in sorted(table_artifacts.items())
    }


def _artifact_content_hashes(artifacts: dict[str, dict[str, Any]]) -> dict[str, str]:
    return {
        name: str(artifact["contentSha256"])
        for name, artifact in sorted(artifacts.items())
    }


def _validate_manifest_input_artifacts(
    artifact_hashes: Any,
    table_artifacts: Any,
    artifact_schema_version: int,
    *,
    walk_forward_enabled: bool,
    risk_enabled: bool,
) -> None:
    if (
        not isinstance(artifact_hashes, dict)
        or not isinstance(table_artifacts, dict)
        or any(not isinstance(artifact, dict) for artifact in artifact_hashes.values())
    ):
        raise SnapshotIntegrityError("manifest 输入 artifact 元数据结构无效")
    expected_inputs: dict[str, dict[str, Any]] = {}
    for table_name, artifact in table_artifacts.items():
        if not isinstance(table_name, str) or not isinstance(artifact, dict):
            raise SnapshotIntegrityError("manifest 输入 artifact 元数据结构无效")
        filename = artifact.get("filename")
        if not isinstance(filename, str) or not filename:
            raise SnapshotIntegrityError("manifest 输入 artifact 元数据缺少 filename")
        archive_name = f"inputs/{Path(filename).name}"
        if archive_name in expected_inputs:
            raise SnapshotIntegrityError("manifest 输入 artifact 文件名重复")
        expected_inputs[archive_name] = artifact

    required_outputs = {
        "quality.json",
        "targets.csv.gz",
        "nav.csv.gz",
        "metrics.json",
        "limitations.json",
    }
    if artifact_schema_version >= 2:
        required_outputs.update(
            {
                "rebalance_requests.csv.gz",
                "rebalance_executions.csv.gz",
                "positions.csv.gz",
            }
        )
    if artifact_schema_version >= 3:
        required_outputs.add("benchmark_nav.csv.gz")
    if artifact_schema_version >= 4:
        required_outputs.add("oos_metrics.json")
    if walk_forward_enabled:
        required_outputs.update(
            {
                "walk_forward_windows.csv.gz",
                "walk_forward_metrics.csv.gz",
            }
        )
    if risk_enabled:
        required_outputs.update(
            {
                "risk_exposures.csv.gz",
                "risk_contributions.csv.gz",
            }
        )
    if set(artifact_hashes) != set(expected_inputs) | required_outputs:
        raise SnapshotIntegrityError("manifest 输入 artifact 元数据集合与冻结输入不一致")
    actual_inputs = {
        name: artifact_hashes[name]
        for name in expected_inputs
    }
    if actual_inputs != expected_inputs:
        raise SnapshotIntegrityError("manifest 输入 artifact 元数据与 dataSnapshot.tableArtifacts 不一致")


def _validate_archive_checkpoint_chain(run_path: Path, manifest: dict[str, Any]) -> None:
    try:
        index = _read_json(run_path / "checkpoints" / "index.json", "checkpoint index")
        artifact_schema_version = manifest.get("artifactSchemaVersion", 1)
        index_artifact_version = index.get("artifactSchemaVersion", 1) if isinstance(index, dict) else None
        if (
            not isinstance(index, dict)
            or index.get("schemaVersion") != CHECKPOINT_SCHEMA_VERSION
            or index_artifact_version != artifact_schema_version
            or index.get("runId") != manifest["runId"]
            or not isinstance(index.get("completed"), list)
        ):
            raise ResumeIntegrityError("checkpoint index 身份或 schema 无效")
        entries = index["completed"]
        if not all(isinstance(entry, dict) for entry in entries):
            raise ResumeIntegrityError("checkpoint index 条目无效")
        completed_names = [entry.get("stage") for entry in entries]
        if completed_names not in (list(STAGES[:-1]), list(STAGES)):
            raise ResumeIntegrityError("归档 checkpoint 必须完整到 manifest 或 finalize")

        checkpoints: dict[str, dict[str, Any]] = {}
        previous_hash = None
        for entry in entries:
            stage = str(entry["stage"])
            checkpoint_path = run_path / "checkpoints" / f"{stage}.json"
            actual_hash = sha256_file(checkpoint_path)
            if entry.get("contentSha256") != actual_hash:
                raise ResumeIntegrityError(f"checkpoint hash 不一致：{stage}")
            document = _read_json(checkpoint_path, f"checkpoint {stage}")
            if (
                not isinstance(document, dict)
                or document.get("schemaVersion") != CHECKPOINT_SCHEMA_VERSION
                or document.get("runId") != manifest["runId"]
                or document.get("stage") != stage
                or document.get("previousCheckpointSha256") != previous_hash
                or not isinstance(document.get("inputs"), dict)
                or not isinstance(document.get("outputs"), dict)
                or not _archive_checkpoint_identity_matches(manifest, stage, document.get("identity"))
            ):
                raise ResumeIntegrityError(f"checkpoint 合同或身份无效：{stage}")
            document["checkpointSha256"] = actual_hash
            checkpoints[stage] = document
            previous_hash = actual_hash

        _validate_archive_checkpoint_artifacts(run_path, manifest, checkpoints)
    except SnapshotIntegrityError:
        raise
    except (ArtifactIntegrityError, ResumeError, OSError, KeyError, TypeError, ValueError) as exc:
        raise SnapshotIntegrityError(f"归档 checkpoint 链无效：{exc}") from exc


def _archive_checkpoint_identity(manifest: dict[str, Any], stage: str) -> dict[str, Any]:
    include_snapshot = stage != "quality_gate"
    identity = {
        "configSha256": manifest["configSha256"],
        "codeCommit": manifest["codeCommit"],
        "environmentSha256": manifest["environment"]["sha256"],
        "randomSeed": manifest["randomSeed"],
        "dataSnapshotId": manifest["dataSnapshot"]["snapshotId"] if include_snapshot else None,
        "reproducibilityKey": manifest["reproducibilityKey"] if include_snapshot else None,
    }
    if manifest.get("artifactSchemaVersion", 1) >= 2:
        identity = {
            "strategyId": manifest["strategyId"],
            "strategyVersion": manifest["config"]["strategyVersion"],
            **identity,
        }
    return identity


def _archive_checkpoint_identity_matches(
    manifest: dict[str, Any],
    stage: str,
    actual: Any,
) -> bool:
    expected = _archive_checkpoint_identity(manifest, stage)
    if actual == expected:
        return True
    if manifest.get("artifactSchemaVersion", 1) != 1 or not isinstance(actual, dict):
        return False
    return actual == {
        "strategyId": manifest["strategyId"],
        "strategyVersion": manifest["config"]["strategyVersion"],
        **expected,
    }


def _validate_archive_checkpoint_artifacts(
    run_path: Path,
    manifest: dict[str, Any],
    checkpoints: dict[str, dict[str, Any]],
) -> None:
    artifact_hashes = manifest["artifactHashes"]
    table_artifacts = manifest["dataSnapshot"]["tableArtifacts"]
    quality = _checkpoint_artifact(checkpoints, "quality_gate", "quality")
    if (
        checkpoints["quality_gate"]["inputs"].get("config")
        != _json_file_artifact(run_path / "config.json")
        or quality != artifact_hashes["quality.json"]
    ):
        raise ResumeIntegrityError("quality checkpoint 与归档不一致")

    snapshot = checkpoints["input_snapshot"]
    snapshot_outputs = snapshot["outputs"]
    if (
        snapshot["inputs"].get("quality") != quality
        or snapshot_outputs.get("snapshotId") != manifest["dataSnapshot"]["snapshotId"]
        or snapshot_outputs.get("reproducibilityKey") != manifest["reproducibilityKey"]
        or snapshot_outputs.get("tableArtifacts") != table_artifacts
    ):
        raise ResumeIntegrityError("input_snapshot checkpoint 与归档冻结输入不一致")

    targets = _checkpoint_artifact(checkpoints, "features_targets", "targets")
    feature_inputs = checkpoints["features_targets"]["inputs"]
    if (
        feature_inputs.get("snapshotId") != manifest["dataSnapshot"]["snapshotId"]
        or feature_inputs.get("tableContentSha256") != _table_content_hashes(table_artifacts)
        or targets != artifact_hashes["targets.csv.gz"]
    ):
        raise ResumeIntegrityError("features_targets checkpoint 与归档不一致")

    nav = _checkpoint_artifact(checkpoints, "simulation", "nav")
    simulation_outputs = {"nav": nav}
    if manifest.get("artifactSchemaVersion", 1) >= 2:
        simulation_outputs.update(
            {
                "rebalanceRequests": _checkpoint_artifact(checkpoints, "simulation", "rebalanceRequests"),
                "rebalanceExecutions": _checkpoint_artifact(checkpoints, "simulation", "rebalanceExecutions"),
                "positions": _checkpoint_artifact(checkpoints, "simulation", "positions"),
            }
        )
    expected_simulation_outputs = {"nav": artifact_hashes["nav.csv.gz"]}
    if manifest.get("artifactSchemaVersion", 1) >= 2:
        expected_simulation_outputs.update(
            {
                "rebalanceRequests": artifact_hashes["rebalance_requests.csv.gz"],
                "rebalanceExecutions": artifact_hashes["rebalance_executions.csv.gz"],
                "positions": artifact_hashes["positions.csv.gz"],
            }
        )
    if (
        checkpoints["simulation"]["inputs"].get("targets") != targets
        or simulation_outputs != expected_simulation_outputs
    ):
        raise ResumeIntegrityError("simulation checkpoint 与归档不一致")

    metrics = _checkpoint_artifact(checkpoints, "metrics", "metrics")
    limitations = _checkpoint_artifact(checkpoints, "metrics", "limitations")
    expected_metric_outputs = {
        "metrics": artifact_hashes["metrics.json"],
        "limitations": artifact_hashes["limitations.json"],
    }
    if manifest.get("artifactSchemaVersion", 1) >= 3:
        expected_metric_outputs["primaryBenchmarkNav"] = artifact_hashes[
            "benchmark_nav.csv.gz"
        ]
    if manifest.get("artifactSchemaVersion", 1) >= 4:
        expected_metric_outputs["oosMetrics"] = artifact_hashes[
            "oos_metrics.json"
        ]
    if _walk_forward_enabled(manifest["config"]):
        expected_metric_outputs.update(
            {
                "walkForwardWindows": artifact_hashes[
                    "walk_forward_windows.csv.gz"
                ],
                "walkForwardMetrics": artifact_hashes[
                    "walk_forward_metrics.csv.gz"
                ],
            }
        )
    if _risk_enabled(manifest["config"]):
        expected_metric_outputs.update(
            {
                "riskExposures": artifact_hashes["risk_exposures.csv.gz"],
                "riskContributions": artifact_hashes[
                    "risk_contributions.csv.gz"
                ],
            }
        )
    expected_metric_inputs = {"nav": nav}
    if manifest.get("artifactSchemaVersion", 1) >= 2:
        expected_metric_inputs.update(
            {
                "rebalanceRequests": simulation_outputs["rebalanceRequests"],
                "rebalanceExecutions": simulation_outputs["rebalanceExecutions"],
                "positions": simulation_outputs["positions"],
            }
        )
    if (
        checkpoints["metrics"]["inputs"] != expected_metric_inputs
        or metrics != artifact_hashes["metrics.json"]
        or limitations != artifact_hashes["limitations.json"]
        or checkpoints["metrics"]["outputs"] != expected_metric_outputs
    ):
        raise ResumeIntegrityError("metrics checkpoint 与归档不一致")

    manifest_checkpoint = checkpoints["manifest"]
    manifest_artifact = _checkpoint_artifact(checkpoints, "manifest", "manifest")
    if (
        manifest_checkpoint["inputs"].get("artifactContentSha256")
        != _artifact_content_hashes(artifact_hashes)
        or manifest_checkpoint["outputs"].get("resultFingerprint") != manifest["resultFingerprint"]
        or manifest_artifact != _json_file_artifact(run_path / "manifest.json")
    ):
        raise ResumeIntegrityError("manifest checkpoint 与归档不一致")

    if "finalize" in checkpoints:
        finalize = checkpoints["finalize"]
        if (
            finalize["inputs"].get("manifest") != manifest_artifact
            or finalize["outputs"].get("resultFingerprint") != manifest["resultFingerprint"]
        ):
            raise ResumeIntegrityError("finalize checkpoint 与归档不一致")


def _json_file_artifact(path: Path) -> dict[str, str]:
    digest = sha256_file(path)
    return {"filename": Path(path).name, "contentSha256": digest, "fileSha256": digest}


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResumeIntegrityError(f"{label} 无法读取或不是有效 JSON") from exc


def _read_and_validate_output_csv(
    path: Path,
    artifact: dict[str, Any],
    columns: tuple[str, ...],
    natural_key: tuple[str, ...],
) -> Any:
    expected_contract = {
        "filename": path.name,
        "columns": list(columns),
        "naturalKey": list(natural_key),
        "nullValue": r"\N",
        "compression": "gzip",
        "gzipMtime": 0,
    }
    if any(artifact.get(key) != value for key, value in expected_contract.items()):
        raise SnapshotIntegrityError(f"归档研究产物合同无效：{path.name}")
    frame = read_canonical_csv_gz(path)
    if list(frame.columns) != list(columns) or artifact.get("rowCount") != len(frame):
        raise SnapshotIntegrityError(f"归档研究产物列或行数无效：{path.name}")
    if frame[list(natural_key)].isna().any().any() or frame.duplicated(list(natural_key)).any():
        raise SnapshotIntegrityError(f"归档研究产物自然键无效：{path.name}")
    keys = list(frame.loc[:, list(natural_key)].itertuples(index=False, name=None))
    if keys != sorted(keys):
        raise SnapshotIntegrityError(f"归档研究产物未按自然键排序：{path.name}")
    return frame


def _validate_walk_forward_frames(
    windows: Any,
    metrics: Any,
    config: dict[str, Any],
) -> dict[str, Any]:
    if windows.empty or metrics.empty:
        raise SnapshotIntegrityError("归档 walk-forward 工件不能为空")
    if list(windows["window_id"]) != list(metrics["window_id"]):
        raise SnapshotIntegrityError("walk-forward 窗口与指标身份不一致")
    policy = validate_validation_policy(config.get("validationPolicy"))
    if (
        set(windows["mode"]) != {policy["mode"]}
        or set(metrics["sample_role"]) != {"test_oos"}
    ):
        raise SnapshotIntegrityError("walk-forward mode 或 sample_role 无效")
    test_observations = 0
    previous_test_end: date | None = None
    for window, metric in zip(
        windows.itertuples(index=False),
        metrics.itertuples(index=False),
        strict=True,
    ):
        train_end = datetime.fromisoformat(str(window.train_end)).date()
        test_start = datetime.fromisoformat(str(window.test_start)).date()
        test_end = datetime.fromisoformat(str(window.test_end)).date()
        metric_start = datetime.fromisoformat(str(metric.start_date)).date()
        metric_end = datetime.fromisoformat(str(metric.end_date)).date()
        train_periods = int(window.train_periods)
        test_periods = int(window.test_periods)
        observations = int(metric.observations)
        if (
            train_end >= test_start
            or test_start > test_end
            or metric_start != test_start
            or metric_end != test_end
            or train_periods != policy["trainPeriods"]
            or test_periods != policy["testPeriods"]
            or observations != test_periods
            or (previous_test_end is not None and test_start <= previous_test_end)
        ):
            raise SnapshotIntegrityError("walk-forward 训练/test 边界或 OOS 观测数无效")
        previous_test_end = test_end
        test_observations += observations
    try:
        window_returns = metrics["total_return"].map(float)
    except (KeyError, TypeError, ValueError) as exc:
        raise SnapshotIntegrityError("walk-forward 窗口收益无效") from exc
    if not window_returns.map(math.isfinite).all():
        raise SnapshotIntegrityError("walk-forward 窗口收益必须是有限数")
    return {
        "mode": policy["mode"],
        "oosOnly": True,
        "testObservationCount": test_observations,
        "windowCount": len(windows),
        "minimumWindowTotalReturn": float(window_returns.min()),
        "medianWindowTotalReturn": float(window_returns.median()),
        "positiveWindowRate": float((window_returns > 0).mean()),
    }


def _cleanup_uncommitted_stage_files(
    working: Path,
    checkpoints: dict[str, dict[str, Any]],
) -> None:
    completed_count = len(checkpoints)
    stage_outputs = {
        0: ("quality.json", "inputs", "targets.csv.gz", "nav.csv.gz", "rebalance_requests.csv.gz", "rebalance_executions.csv.gz", "positions.csv.gz", "walk_forward_windows.csv.gz", "walk_forward_metrics.csv.gz", "risk_exposures.csv.gz", "risk_contributions.csv.gz", "metrics.json", "oos_metrics.json", "limitations.json", "manifest.json"),
        1: ("inputs", "targets.csv.gz", "nav.csv.gz", "rebalance_requests.csv.gz", "rebalance_executions.csv.gz", "positions.csv.gz", "walk_forward_windows.csv.gz", "walk_forward_metrics.csv.gz", "risk_exposures.csv.gz", "risk_contributions.csv.gz", "metrics.json", "oos_metrics.json", "limitations.json", "manifest.json"),
        2: ("targets.csv.gz", "nav.csv.gz", "rebalance_requests.csv.gz", "rebalance_executions.csv.gz", "positions.csv.gz", "walk_forward_windows.csv.gz", "walk_forward_metrics.csv.gz", "risk_exposures.csv.gz", "risk_contributions.csv.gz", "metrics.json", "oos_metrics.json", "limitations.json", "manifest.json"),
        3: ("nav.csv.gz", "rebalance_requests.csv.gz", "rebalance_executions.csv.gz", "positions.csv.gz", "walk_forward_windows.csv.gz", "walk_forward_metrics.csv.gz", "risk_exposures.csv.gz", "risk_contributions.csv.gz", "metrics.json", "oos_metrics.json", "limitations.json", "manifest.json"),
        4: ("walk_forward_windows.csv.gz", "walk_forward_metrics.csv.gz", "risk_exposures.csv.gz", "risk_contributions.csv.gz", "metrics.json", "oos_metrics.json", "limitations.json", "manifest.json"),
        5: ("manifest.json",),
        6: (),
        7: (),
    }
    for name in stage_outputs[completed_count]:
        path = working / name
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    for stage in STAGES[completed_count:]:
        (working / "checkpoints" / f"{stage}.json").unlink(missing_ok=True)


def _append_recovery_event(working: Path, event: dict[str, Any]) -> None:
    path = Path(working) / "checkpoints" / "recovery.json"
    events: list[dict[str, Any]] = []
    if path.exists():
        payload = _read_json(path, "recovery audit")
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            raise ResumeIntegrityError("recovery audit 合同无效")
        events = list(payload["events"])
    events.append(event)
    atomic_write_json(
        path,
        {"schemaVersion": 1, "runId": event["runId"], "events": events},
    )


def _locate_resume_path(temporary: Path, final_path: Path) -> Path:
    if temporary.is_dir() and final_path.is_dir():
        raise ResumeIntegrityError("临时目录与完成目录同时存在，拒绝推断续跑来源")
    if temporary.is_dir():
        return temporary
    if final_path.is_dir():
        return final_path
    raise ResumeIntegrityError("研究运行的临时目录和完成目录均不存在")


def _verify_artifact_root(run: ResearchRun, final_path: Path) -> None:
    if Path(run.artifact_root).resolve(strict=False) != final_path.resolve(strict=False):
        raise ResumeIdentityError("artifact_root 与指定 output_root 不一致")


def _mark_run_failed(
    registry_db: Session,
    run_id: str,
    working: Path,
    runs_root: Path,
    exc: Exception,
) -> None:
    registry_db.rollback()
    persisted = registry_db.get(ResearchRun, run_id)
    if persisted is None:
        return
    persisted.status = "failed"
    persisted.error = f"{type(exc).__name__}: {exc}"[:2000]
    persisted.finished_at = datetime.now(timezone.utc)
    persisted.heartbeat_at = persisted.finished_at
    failed_path = runs_root / f"{run_id}.failed"
    if working.is_dir() and working.name == f".{run_id}.tmp":
        if failed_path.exists():
            raise RuntimeError(f"失败归档目录已存在：{failed_path}")
        os.replace(working, failed_path)
        persisted.artifact_root = str(failed_path)
    registry_db.commit()


def _mark_run_interrupted(
    registry_db: Session,
    run_id: str,
    working: Path,
    exc: ResearchStopRequested,
) -> None:
    registry_db.rollback()
    persisted = registry_db.get(ResearchRun, run_id)
    if persisted is None:
        return
    interrupted_at = datetime.now(timezone.utc)
    if working.is_dir():
        _append_recovery_event(
            working,
            {
                "event": "orchestrator_stop_requested",
                "runId": run_id,
                "recordedAt": interrupted_at.isoformat(),
                "reason": str(exc),
            },
        )
    persisted.status = "interrupted"
    persisted.error = f"ResearchStopRequested: {exc}"[:2000]
    persisted.finished_at = interrupted_at
    persisted.heartbeat_at = interrupted_at
    registry_db.commit()


def _raise_if_stop_requested(
    should_stop: Callable[[], bool] | None,
    next_stage: str,
) -> None:
    if should_stop is not None and should_stop():
        raise ResearchStopRequested(f"在进入 {next_stage} 前按安全点停止")


def _maybe_interrupt(requested_stage: str | None, completed_stage: str) -> None:
    if requested_stage == completed_stage:
        raise InjectedResearchInterruption(f"测试注入硬中断：{completed_stage}")


def _validate_interrupt_stage(stage: str | None) -> None:
    if stage is not None and stage not in INTERRUPTIBLE_STAGES:
        raise ValueError(
            "interrupt_after_stage 只允许 input_snapshot、simulation 或 finalize"
        )


def _resolve_code_commit(explicit: str | None, test_mode: bool) -> str:
    injected = os.getenv("APP_GIT_COMMIT")
    if test_mode:
        return explicit or injected or "test-mode"
    if not injected or injected.lower() in {"unknown", "unspecified", "dev"}:
        raise FormalRunConfigurationError(
            "正式运行要求部署时注入真实 APP_GIT_COMMIT；当前仅允许 test_mode。"
        )
    if not re.fullmatch(r"[0-9a-f]{40,64}", injected):
        raise FormalRunConfigurationError("APP_GIT_COMMIT 必须是 40 到 64 位小写十六进制提交哈希")
    if explicit and explicit != injected:
        raise FormalRunConfigurationError("显式 code_commit 与 APP_GIT_COMMIT 不一致")
    return injected


def _read_schema_revision(registry_db: Session) -> str:
    with registry_db.get_bind().connect() as connection:
        heads = current_schema_heads(connection)
    if len(heads) != 1:
        raise FormalRunConfigurationError("正式运行要求数据库处于唯一 Alembic head")
    return heads[0]


def _quality_contract(run: DataQualityRun) -> dict[str, Any]:
    return {
        "qualityRunId": run.id,
        "scope": run.scope,
        "startDate": run.start_date.isoformat(),
        "endDate": run.end_date.isoformat(),
        "universeHash": run.universe_hash,
        "status": run.status,
        "config": run.config or {},
        "summary": run.summary or {},
        "codeCommit": run.code_commit,
    }


def _promote_run(temporary: Path, final_path: Path) -> None:
    if final_path.exists():
        raise RuntimeError(f"研究运行完成目录已存在：{final_path}")
    os.replace(temporary, final_path)
    descriptor = os.open(final_path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
