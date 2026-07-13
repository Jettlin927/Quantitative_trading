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
    base_names = {"targets.csv.gz", "nav.csv.gz", "metrics.json"}
    ledger_names = {
        "rebalance_requests.csv.gz",
        "rebalance_executions.csv.gz",
        "positions.csv.gz",
    }
    present_ledgers = ledger_names & set(artifact_hashes)
    if present_ledgers and present_ledgers != ledger_names:
        raise ValueError("结果指纹的模拟账本工件必须完整")
    walk_forward_names = {
        "walk_forward_windows.csv.gz",
        "walk_forward_metrics.csv.gz",
    }
    present_walk_forward = walk_forward_names & set(artifact_hashes)
    if present_walk_forward and present_walk_forward != walk_forward_names:
        raise ValueError("结果指纹的 walk-forward 工件必须完整")
    risk_names = {
        "risk_exposures.csv.gz",
        "risk_contributions.csv.gz",
    }
    present_risk = risk_names & set(artifact_hashes)
    if present_risk and present_risk != risk_names:
        raise ValueError("结果指纹的风险工件必须完整")
    deterministic_names = (
        base_names
        | (ledger_names if present_ledgers else set())
        | (walk_forward_names if present_walk_forward else set())
        | (risk_names if present_risk else set())
    )
    deterministic = {
        name: artifact["contentSha256"]
        for name, artifact in sorted(artifact_hashes.items())
        if name in deterministic_names
    }
    if set(deterministic) != deterministic_names:
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
    artifact_schema_version: int = 1,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    if artifact_schema_version not in {1, 2}:
        raise ValueError("artifact_schema_version 只允许 1 或 2")
    generated = generated_at or datetime.now(timezone.utc)
    result_fingerprint = build_result_fingerprint(artifact_hashes)
    return {
        "schemaVersion": 2,
        "artifactSchemaVersion": artifact_schema_version,
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
