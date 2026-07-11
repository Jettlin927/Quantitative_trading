from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from ..database import current_schema_heads
from ..models import DataQualityRun, ResearchRun
from .artifacts import (
    ArtifactIntegrityError,
    atomic_write_json,
    verify_csv_artifact,
    verify_file_artifact,
    write_dataframe_csv_gz,
)
from .baselines import run_sentinel_etf_baseline
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
    freeze_input_snapshot,
    materialize_snapshot_inputs,
    validate_quality_gate,
    verify_materialized_inputs,
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
) -> ResearchRunResult:
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
    failed_path = runs_root / f"{run_id}.failed"
    temporary.mkdir()
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
        started_at=datetime.now(timezone.utc),
        heartbeat_at=datetime.now(timezone.utc),
    )
    registry_db.add(run)
    registry_db.commit()

    try:
        atomic_write_json(temporary / "config.json", normalized)
        quality_run = validate_quality_gate(registry_db, normalized)
        quality_contract = _quality_contract(quality_run)
        quality_artifact = atomic_write_json(temporary / "quality.json", quality_contract)
        _checkpoint(registry_db, run, temporary, "quality_gate", {"quality": quality_artifact})

        snapshot = freeze_input_snapshot(
            registry_db,
            normalized,
            snapshots_root,
            capacity_policy=capacity_policy,
        )
        table_artifacts = materialize_snapshot_inputs(snapshot, temporary / "inputs")
        reproducibility_key = build_reproducibility_key(
            config_sha256=config_sha256,
            data_snapshot_id=snapshot.snapshot_id,
            code_commit=resolved_commit,
            environment_sha256=environment["sha256"],
            random_seed=normalized["randomSeed"],
        )
        run.data_snapshot_id = snapshot.snapshot_id
        run.reproducibility_key = reproducibility_key
        registry_db.commit()
        _checkpoint(
            registry_db,
            run,
            temporary,
            "input_snapshot",
            {
                "snapshotId": snapshot.snapshot_id,
                "tableArtifacts": {
                    name: artifact["contentSha256"] for name, artifact in sorted(table_artifacts.items())
                },
            },
        )

        baseline = run_sentinel_etf_baseline(
            temporary / "inputs",
            normalized,
            compressed=True,
            table_artifacts=table_artifacts,
        )
        targets_artifact = write_dataframe_csv_gz(
            temporary / "targets.csv.gz",
            baseline.targets,
            columns=TARGET_COLUMNS,
            natural_key=("signal_date", "ts_code"),
        )
        _checkpoint(
            registry_db,
            run,
            temporary,
            "features_targets",
            {"targets": targets_artifact},
        )
        nav_artifact = write_dataframe_csv_gz(
            temporary / "nav.csv.gz",
            baseline.nav,
            columns=NAV_COLUMNS,
            natural_key=("trade_date",),
        )
        _checkpoint(registry_db, run, temporary, "simulation", {"nav": nav_artifact})
        metrics_artifact = atomic_write_json(temporary / "metrics.json", baseline.metrics)
        _checkpoint(registry_db, run, temporary, "metrics", {"metrics": metrics_artifact})
        limitations = sorted(set(baseline.limitations))
        limitations_artifact = atomic_write_json(temporary / "limitations.json", limitations)

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
            run_id=run_id,
            reproducibility_key=reproducibility_key,
            strategy_id=normalized["strategyId"],
            config=normalized,
            config_sha256=config_sha256,
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
        atomic_write_json(temporary / "manifest.json", manifest)
        _checkpoint(
            registry_db,
            run,
            temporary,
            "manifest",
            {"resultFingerprint": manifest["resultFingerprint"]},
        )
        _checkpoint(
            registry_db,
            run,
            temporary,
            "finalize",
            {"resultFingerprint": manifest["resultFingerprint"]},
        )
        _promote_run(temporary, final_path)
        run.status = "succeeded"
        run.stage = "finalized"
        run.metrics = baseline.metrics
        run.result_fingerprint = manifest["resultFingerprint"]
        run.finished_at = datetime.now(timezone.utc)
        run.heartbeat_at = run.finished_at
        registry_db.commit()
        return ResearchRunResult(run_id=run_id, path=final_path, manifest=manifest)
    except Exception as exc:
        registry_db.rollback()
        persisted = registry_db.get(ResearchRun, run_id)
        if persisted is not None:
            persisted.status = "failed"
            persisted.error = f"{type(exc).__name__}: {exc}"[:2000]
            persisted.finished_at = datetime.now(timezone.utc)
            persisted.heartbeat_at = persisted.finished_at
            if temporary.exists():
                if failed_path.exists():
                    shutil.rmtree(failed_path)
                os.replace(temporary, failed_path)
                persisted.artifact_root = str(failed_path)
            registry_db.commit()
        raise


def reproduce_quant_research(run_path: Path) -> dict[str, Any]:
    run_path = Path(run_path)
    manifest = json.loads((run_path / "manifest.json").read_text(encoding="utf-8"))
    config = validate_run_config(
        json.loads((run_path / "config.json").read_text(encoding="utf-8")),
        verify_universe_source=False,
    )
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
    quality = json.loads((run_path / "quality.json").read_text(encoding="utf-8"))
    limitations = json.loads((run_path / "limitations.json").read_text(encoding="utf-8"))
    if quality != manifest.get("qualityRun") or limitations != manifest.get("limitations"):
        raise SnapshotIntegrityError("归档审计产物与 manifest 不一致")

    with tempfile.TemporaryDirectory(prefix="quant-reproduce-") as temporary_name:
        temporary = Path(temporary_name)
        baseline = run_sentinel_etf_baseline(
            run_path / "inputs",
            config,
            compressed=True,
            table_artifacts=table_artifacts,
        )
        actual = {
            "targets.csv.gz": write_dataframe_csv_gz(
                temporary / "targets.csv.gz",
                baseline.targets,
                columns=TARGET_COLUMNS,
                natural_key=("signal_date", "ts_code"),
            ),
            "nav.csv.gz": write_dataframe_csv_gz(
                temporary / "nav.csv.gz",
                baseline.nav,
                columns=NAV_COLUMNS,
                natural_key=("trade_date",),
            ),
            "metrics.json": atomic_write_json(temporary / "metrics.json", baseline.metrics),
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


def _checkpoint(
    registry_db: Session,
    run: ResearchRun,
    temporary: Path,
    stage: str,
    evidence: dict[str, Any],
) -> None:
    atomic_write_json(
        temporary / "checkpoints" / f"{stage}.json",
        {"stage": stage, "evidence": evidence},
    )
    run.stage = stage
    run.heartbeat_at = datetime.now(timezone.utc)
    registry_db.commit()


def _promote_run(temporary: Path, final_path: Path) -> None:
    if final_path.exists():
        raise RuntimeError(f"研究运行完成目录已存在：{final_path}")
    os.replace(temporary, final_path)
    descriptor = os.open(final_path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
