from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo


def _manifests(
    run_groups: tuple[dict[str, dict[str, Any]], ...],
) -> list[dict[str, Any]]:
    manifests = [
        run["manifest"]
        for run_group in run_groups
        for run in run_group.values()
    ]
    if not manifests:
        raise ValueError("报告没有 canonical 运行清单")
    return manifests


def canonical_report_timestamp(
    *run_groups: dict[str, dict[str, Any]],
) -> str:
    generated_at = max(
        datetime.fromisoformat(manifest["generatedAt"])
        for manifest in _manifests(run_groups)
    )
    return generated_at.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(
        timespec="seconds"
    )


def verify_reproduction_evidence(
    evidence_path: Path,
    *run_groups: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    path = Path(evidence_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"复现证据不存在：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"复现证据不可读：{path}") from exc
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise ValueError("复现证据必须是 schemaVersion=1 的 JSON 对象")
    if payload.get("networkMode") != "none":
        raise ValueError("复现证据没有证明禁用网络")
    image_digest = payload.get("imageDigest")
    if not isinstance(image_digest, str) or re.fullmatch(
        r"sha256:[0-9a-f]{64}", image_digest
    ) is None:
        raise ValueError("复现证据缺少有效镜像 digest")

    manifests = _manifests(run_groups)
    expected = {
        manifest["runId"]: manifest["resultFingerprint"] for manifest in manifests
    }
    if len(expected) != len(manifests):
        raise ValueError("canonical 运行 ID 重复")
    commits = {manifest["codeCommit"] for manifest in manifests}
    if len(commits) != 1 or payload.get("codeCommit") not in commits:
        raise ValueError("复现证据与 canonical 代码身份不一致")

    rounds = payload.get("rounds")
    if not isinstance(rounds, list) or len(rounds) != 2:
        raise ValueError("复现证据必须包含连续两轮结果")
    observed_rounds = []
    for expected_round, round_result in enumerate(rounds, start=1):
        if not isinstance(round_result, dict) or round_result.get("round") != expected_round:
            raise ValueError("复现证据轮次必须依次为 1、2")
        observed = round_result.get("resultFingerprints")
        if not isinstance(observed, dict) or not set(expected).issubset(observed):
            raise ValueError("复现证据没有覆盖全部 canonical 运行")
        for run_id, fingerprint in expected.items():
            if observed[run_id] != fingerprint:
                raise ValueError(f"复现证据指纹不匹配：{run_id}")
        observed_rounds.append(observed)
    if observed_rounds[0] != observed_rounds[1]:
        raise ValueError("复现证据两轮总账本不一致")

    return {
        "evidenceFile": path.name,
        "verifiedAt": payload.get("verifiedAt"),
        "imageDigest": image_digest,
        "matchesPerRun": len(rounds),
        "runCount": len(expected),
        "networkDisabled": True,
        "allMatched": True,
    }
