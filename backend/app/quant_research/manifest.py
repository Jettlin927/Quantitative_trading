from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def build_run_manifest(
    strategy_id: str,
    config: dict[str, Any],
    data_snapshot: dict[str, Any],
    git_commit: str,
    limitations: list[str] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    if not strategy_id.strip():
        raise ValueError("strategy_id 不能为空")
    timestamp = generated_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise ValueError("generated_at 必须包含时区")
    canonical_config = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    config_hash = hashlib.sha256(canonical_config.encode("utf-8")).hexdigest()
    run_id = f"{strategy_id}-{timestamp.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{config_hash[:8]}"
    return {
        "schemaVersion": 1,
        "runId": run_id,
        "strategyId": strategy_id,
        "generatedAt": timestamp.astimezone(timezone.utc).isoformat(),
        "config": config,
        "configSha256": config_hash,
        "code": {"gitCommit": git_commit},
        "dataSnapshot": data_snapshot,
        "limitations": list(limitations or []),
        "boundaries": {
            "researchOnly": True,
            "executionEnabled": False,
            "brokerConnected": False,
            "realHoldingsImported": False,
        },
    }
