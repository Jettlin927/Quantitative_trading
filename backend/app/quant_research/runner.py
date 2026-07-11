from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any
from uuid import uuid4

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
    build_sentinel_targets,
    sentinel_limitations,
    simulate_sentinel_targets,
    summarize_sentinel_metrics,
)
from .manifest import (
    build_environment_fingerprint,
    build_research_manifest,
    build_result_fingerprint,
)
from .run_config import (
    FormalRunConfigurationError,
    build_reproducibility_key,
    canonical_sha256,
    canonical_run_config_sha256,
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


class ResumeError(RuntimeError):
    pass


class ResumeIdentityError(ResumeError):
    pass


class ResumeIntegrityError(ResumeError):
    pass


class InjectedResearchInterruption(BaseException):
    """测试专用的硬中断；故意绕过业务异常清理，模拟进程被杀。"""


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
) -> ResearchRunResult:
    _validate_interrupt_stage(interrupt_after_stage)
    normalized = validate_run_config(config)
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
            {},
            capacity_policy=capacity_policy,
            interrupt_after_stage=interrupt_after_stage,
        )
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
            checkpoints,
            capacity_policy=capacity_policy,
            interrupt_after_stage=interrupt_after_stage,
        )
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
    checkpoints: dict[str, dict[str, Any]],
    *,
    capacity_policy: SnapshotCapacityPolicy | None,
    interrupt_after_stage: str | None,
) -> ResearchRunResult:
    config_artifact = _json_file_artifact(working / "config.json")

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

    if "features_targets" not in checkpoints:
        targets = build_sentinel_targets(
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

    if "simulation" not in checkpoints:
        targets = read_canonical_csv_gz(working / "targets.csv.gz")
        nav, _calendar = simulate_sentinel_targets(
            working / "inputs",
            normalized,
            targets,
            compressed=True,
            table_artifacts=table_artifacts,
        )
        nav_artifact = write_dataframe_csv_gz(
            working / "nav.csv.gz",
            nav,
            columns=NAV_COLUMNS,
            natural_key=("trade_date",),
        )
        _write_checkpoint(
            registry_db,
            run,
            working,
            "simulation",
            inputs={"targets": targets_artifact},
            outputs={"nav": nav_artifact},
            checkpoints=checkpoints,
        )
        _maybe_interrupt(interrupt_after_stage, "simulation")
    else:
        nav_artifact = _checkpoint_artifact(checkpoints, "simulation", "nav")

    if "metrics" not in checkpoints:
        nav = read_canonical_csv_gz(working / "nav.csv.gz")
        metrics = summarize_sentinel_metrics(
            working / "inputs",
            normalized,
            nav,
            compressed=True,
            table_artifacts=table_artifacts,
        )
        limitations = sorted(set(sentinel_limitations()))
        metrics_artifact = atomic_write_json(working / "metrics.json", metrics)
        limitations_artifact = atomic_write_json(working / "limitations.json", limitations)
        _write_checkpoint(
            registry_db,
            run,
            working,
            "metrics",
            inputs={"nav": nav_artifact},
            outputs={
                "metrics": metrics_artifact,
                "limitations": limitations_artifact,
            },
            checkpoints=checkpoints,
        )
        _maybe_interrupt(interrupt_after_stage, "metrics")
    else:
        metrics = _read_json(working / "metrics.json", "metrics.json")
        limitations = _read_json(working / "limitations.json", "limitations.json")
        metrics_artifact = _checkpoint_artifact(checkpoints, "metrics", "metrics")
        limitations_artifact = _checkpoint_artifact(checkpoints, "metrics", "limitations")

    if "manifest" not in checkpoints:
        artifact_hashes = {
            **{
                f"inputs/{Path(artifact['filename']).name}": artifact
                for artifact in table_artifacts.values()
            },
            "quality.json": quality_artifact,
            "targets.csv.gz": targets_artifact,
            "nav.csv.gz": nav_artifact,
            "metrics.json": metrics_artifact,
            "limitations.json": limitations_artifact,
        }
        manifest = build_research_manifest(
            run_id=run.run_id,
            reproducibility_key=reproducibility_key,
            strategy_id=normalized["strategyId"],
            config=normalized,
            config_sha256=run.config_sha256,
            data_snapshot={
                "snapshotId": snapshot.snapshot_id,
                "relativePath": "inputs",
                "scope": snapshot.manifest["scope"],
                "warmupStart": snapshot.manifest["warmupStart"],
                "startDate": snapshot.manifest["startDate"],
                "endDate": snapshot.manifest["endDate"],
                "benchmark": snapshot.manifest["benchmark"],
                "universeHash": snapshot.manifest["universeHash"],
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

    expected = manifest["artifactHashes"]
    if build_result_fingerprint(expected) != manifest.get("resultFingerprint"):
        raise SnapshotIntegrityError("manifest resultFingerprint 与产物哈希不一致")
    for name in ("targets.csv.gz", "nav.csv.gz"):
        try:
            verify_csv_artifact(run_path / name, expected[name])
        except (ArtifactIntegrityError, KeyError) as exc:
            raise SnapshotIntegrityError(f"归档研究产物无效：{name}") from exc
    for name in ("quality.json", "metrics.json", "limitations.json"):
        try:
            verify_file_artifact(run_path / name, expected[name])
        except (ArtifactIntegrityError, KeyError) as exc:
            raise SnapshotIntegrityError(f"归档研究产物无效：{name}") from exc
    quality = _read_json(run_path / "quality.json", "quality.json")
    limitations = _read_json(run_path / "limitations.json", "limitations.json")
    if quality != manifest.get("qualityRun") or limitations != manifest.get("limitations"):
        raise SnapshotIntegrityError("归档审计产物与 manifest 不一致")
    return manifest, config


def reproduce_quant_research(run_path: Path) -> dict[str, Any]:
    run_path = Path(run_path)
    manifest, config = validate_research_archive(run_path)
    table_artifacts = manifest["dataSnapshot"]["tableArtifacts"]
    expected = manifest["artifactHashes"]
    with tempfile.TemporaryDirectory(prefix="quant-reproduce-") as temporary_name:
        temporary = Path(temporary_name)
        targets = build_sentinel_targets(
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
        nav, _calendar = simulate_sentinel_targets(
            run_path / "inputs",
            config,
            persisted_targets,
            compressed=True,
            table_artifacts=table_artifacts,
        )
        nav_artifact = write_dataframe_csv_gz(
            temporary / "nav.csv.gz",
            nav,
            columns=NAV_COLUMNS,
            natural_key=("trade_date",),
        )
        persisted_nav = read_canonical_csv_gz(temporary / "nav.csv.gz")
        metrics = summarize_sentinel_metrics(
            run_path / "inputs",
            config,
            persisted_nav,
            compressed=True,
            table_artifacts=table_artifacts,
        )
        actual = {
            "targets.csv.gz": targets_artifact,
            "nav.csv.gz": nav_artifact,
            "metrics.json": atomic_write_json(temporary / "metrics.json", metrics),
        }
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


def _initialize_checkpoint_index(working: Path, run_id: str) -> None:
    atomic_write_json(
        working / "checkpoints" / "index.json",
        {"schemaVersion": CHECKPOINT_SCHEMA_VERSION, "runId": run_id, "completed": []},
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
        if (
            index.get("schemaVersion") != CHECKPOINT_SCHEMA_VERSION
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
    if "metrics" in checkpoints:
        if checkpoints["metrics"]["inputs"].get("nav") != _checkpoint_artifact(
            checkpoints, "simulation", "nav"
        ):
            raise ResumeIntegrityError("metrics checkpoint 输入 hash 不一致")
        verify_file_artifact(
            working / "metrics.json",
            _checkpoint_artifact(checkpoints, "metrics", "metrics"),
        )
        verify_file_artifact(
            working / "limitations.json",
            _checkpoint_artifact(checkpoints, "metrics", "limitations"),
        )
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


def _json_file_artifact(path: Path) -> dict[str, str]:
    digest = sha256_file(path)
    return {"filename": Path(path).name, "contentSha256": digest, "fileSha256": digest}


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResumeIntegrityError(f"{label} 无法读取或不是有效 JSON") from exc


def _cleanup_uncommitted_stage_files(
    working: Path,
    checkpoints: dict[str, dict[str, Any]],
) -> None:
    completed_count = len(checkpoints)
    stage_outputs = {
        0: ("quality.json", "inputs", "targets.csv.gz", "nav.csv.gz", "metrics.json", "limitations.json", "manifest.json"),
        1: ("inputs", "targets.csv.gz", "nav.csv.gz", "metrics.json", "limitations.json", "manifest.json"),
        2: ("targets.csv.gz", "nav.csv.gz", "metrics.json", "limitations.json", "manifest.json"),
        3: ("nav.csv.gz", "metrics.json", "limitations.json", "manifest.json"),
        4: ("metrics.json", "limitations.json", "manifest.json"),
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
