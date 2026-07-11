from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import platform
from typing import Any, Mapping
from uuid import uuid4

from .run_config import canonical_sha256


DEFAULT_REQUIREMENTS_PATH = Path(__file__).resolve().parents[2] / "requirements.txt"


def build_run_manifest(
    *,
    strategy_id: str,
    config: dict[str, Any],
    data_snapshot: dict[str, Any],
    git_commit: str | None = None,
    limitations: list[str] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """兼容原研究协议的轻量 manifest；正式闭环使用 build_research_manifest。"""
    generated = generated_at or datetime.now(timezone.utc)
    return {
        "schemaVersion": 1,
        "runId": str(uuid4()),
        "strategyId": strategy_id,
        "generatedAt": generated.isoformat(),
        "config": config,
        "configSha256": canonical_sha256(config),
        "gitCommit": git_commit,
        "dataSnapshot": data_snapshot,
        "limitations": limitations or [],
        "boundaries": {
            "researchOnly": True,
            "executionEnabled": False,
            "realBrokerConnected": False,
        },
    }


def build_environment_fingerprint(
    *,
    schema_revision: str,
    code_commit: str,
    requirements_path: Path = DEFAULT_REQUIREMENTS_PATH,
    python_version: str | None = None,
    build_identifier: str | None = None,
    timezone_name: str = "Asia/Shanghai",
) -> dict[str, Any]:
    dependency_hash = sha256(Path(requirements_path).read_bytes()).hexdigest()
    contract = {
        "schemaRevision": schema_revision,
        "pythonVersion": python_version or platform.python_version(),
        "dependenciesSha256": dependency_hash,
        "buildIdentifier": build_identifier or os.getenv("APP_BUILD_ID") or f"git:{code_commit}",
        "timezone": timezone_name,
        "appGitCommit": code_commit,
    }
    return {**contract, "sha256": canonical_sha256(contract)}


def build_result_fingerprint(artifact_hashes: Mapping[str, Mapping[str, Any]]) -> str:
    deterministic = {
        name: artifact["contentSha256"]
        for name, artifact in sorted(artifact_hashes.items())
        if name in {"targets.csv.gz", "nav.csv.gz", "metrics.json"}
    }
    if set(deterministic) != {"targets.csv.gz", "nav.csv.gz", "metrics.json"}:
        raise ValueError("结果指纹缺少 targets、nav 或 metrics 的 canonical hash")
    return canonical_sha256(deterministic)


def build_research_manifest(
    *,
    run_id: str,
    reproducibility_key: str,
    strategy_id: str,
    config: dict[str, Any],
    config_sha256: str,
    data_snapshot: dict[str, Any],
    quality_run: dict[str, Any],
    universe: dict[str, Any],
    random_seed: int,
    environment: dict[str, Any],
    limitations: list[str],
    artifact_hashes: dict[str, dict[str, Any]],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    result_fingerprint = build_result_fingerprint(artifact_hashes)
    return {
        "schemaVersion": 2,
        "runId": run_id,
        "reproducibilityKey": reproducibility_key,
        "strategyId": strategy_id,
        "generatedAt": generated.isoformat(),
        "config": config,
        "configSha256": config_sha256,
        "qualityRun": quality_run,
        "dataSnapshot": data_snapshot,
        "universe": universe,
        "randomSeed": random_seed,
        "codeCommit": environment["appGitCommit"],
        "environment": environment,
        "limitations": sorted(set(limitations)),
        "artifactHashes": artifact_hashes,
        "resultFingerprint": result_fingerprint,
        "boundaries": {
            "researchOnly": True,
            "notInvestmentAdvice": True,
            "executionEnabled": False,
            "realBrokerConnected": False,
        },
    }
