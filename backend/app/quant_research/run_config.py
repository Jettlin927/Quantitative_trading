from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any

from .universe import evaluate_universe_provenance
from .validation import validate_validation_policy


REQUIRED_CONFIG_FIELDS = (
    "strategyId",
    "strategyVersion",
    "scope",
    "universe",
    "warmupStart",
    "startDate",
    "endDate",
    "benchmark",
    "featureParameters",
    "targetWeightParameters",
    "executionPolicy",
    "costModel",
    "randomSeed",
    "timezone",
    "qualityRunId",
    "allowedWarnings",
)
EVALUATION_SAMPLE_ROLES = ("train", "validation", "test_oos")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REPO_ROOT = Path(__file__).resolve().parents[3]


class FormalRunConfigurationError(ValueError):
    pass


def validate_risk_policy(policy: Any) -> dict[str, Any]:
    if policy is None:
        return {"mode": "none"}
    if not isinstance(policy, dict):
        raise ValueError("riskPolicy 必须是 JSON object")
    if policy == {"mode": "none"}:
        return {"mode": "none"}
    required = {"mode", "lookbackPeriods", "minPeriods"}
    if set(policy) != required or policy.get("mode") != "rolling_covariance":
        raise ValueError(
            "riskPolicy 只允许 none 或固定 rolling_covariance/lookbackPeriods/minPeriods"
        )
    lookback = policy["lookbackPeriods"]
    minimum = policy["minPeriods"]
    if (
        isinstance(lookback, bool)
        or not isinstance(lookback, int)
        or isinstance(minimum, bool)
        or not isinstance(minimum, int)
        or minimum < 2
        or lookback < minimum
    ):
        raise ValueError("riskPolicy 必须满足 2 <= minPeriods <= lookbackPeriods")
    return {
        "mode": "rolling_covariance",
        "lookbackPeriods": lookback,
        "minPeriods": minimum,
    }


def validate_evaluation_policy(policy: Any) -> dict[str, Any]:
    if not isinstance(policy, dict) or set(policy) != {
        "marketRegime",
        "costStressMultiplier",
    }:
        raise ValueError(
            "evaluationPolicy 必须固定 marketRegime 与 costStressMultiplier"
        )
    regime = policy["marketRegime"]
    expected = {
        "directionLookbackPeriods",
        "upThreshold",
        "downThreshold",
        "volatilityLookbackPeriods",
        "highVolatilityThreshold",
    }
    if not isinstance(regime, dict) or set(regime) != expected:
        raise ValueError("evaluationPolicy.marketRegime 字段不完整或含未知字段")
    for field in ("directionLookbackPeriods", "volatilityLookbackPeriods"):
        value = regime[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 2:
            raise ValueError(f"evaluationPolicy.marketRegime.{field} 必须是至少 2 的整数")
    up = _finite_decimal_string(regime["upThreshold"], "upThreshold")
    down = _finite_decimal_string(regime["downThreshold"], "downThreshold")
    high_volatility = _finite_decimal_string(
        regime["highVolatilityThreshold"], "highVolatilityThreshold"
    )
    multiplier = _finite_decimal_string(
        policy["costStressMultiplier"], "costStressMultiplier"
    )
    if up <= 0 or down >= 0 or down >= up:
        raise ValueError("市场方向阈值必须满足 downThreshold < 0 < upThreshold")
    if high_volatility <= 0:
        raise ValueError("highVolatilityThreshold 必须大于 0")
    if multiplier <= 1:
        raise ValueError("costStressMultiplier 必须大于 1")
    return {
        "marketRegime": {
            "directionLookbackPeriods": regime["directionLookbackPeriods"],
            "upThreshold": str(regime["upThreshold"]),
            "downThreshold": str(regime["downThreshold"]),
            "volatilityLookbackPeriods": regime["volatilityLookbackPeriods"],
            "highVolatilityThreshold": str(regime["highVolatilityThreshold"]),
        },
        "costStressMultiplier": str(policy["costStressMultiplier"]),
    }


def validate_evaluation_sample_splits(
    value: Any,
    *,
    start_date: str,
    end_date: str,
) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != len(EVALUATION_SAMPLE_ROLES):
        raise ValueError("evaluationSampleSplits 必须固定 train、validation、test_oos 三段")
    normalized: list[dict[str, str]] = []
    for expected_role, item in zip(EVALUATION_SAMPLE_ROLES, value, strict=True):
        if not isinstance(item, dict) or set(item) != {"role", "startDate", "endDate"}:
            raise ValueError("evaluationSampleSplits 每段只允许 role/startDate/endDate")
        if item.get("role") != expected_role:
            raise ValueError("evaluationSampleSplits 必须按 train、validation、test_oos 排列")
        segment_start = _parse_date(item["startDate"], f"evaluationSampleSplits.{expected_role}.startDate")
        segment_end = _parse_date(item["endDate"], f"evaluationSampleSplits.{expected_role}.endDate")
        if segment_start > segment_end:
            raise ValueError(f"evaluationSampleSplits.{expected_role} 起始日期晚于结束日期")
        normalized.append(
            {
                "role": expected_role,
                "startDate": segment_start.isoformat(),
                "endDate": segment_end.isoformat(),
            }
        )
    if any(
        date.fromisoformat(normalized[index]["endDate"])
        >= date.fromisoformat(normalized[index + 1]["startDate"])
        for index in range(len(normalized) - 1)
    ):
        raise ValueError("evaluationSampleSplits 三段必须严格不重叠")
    if normalized[0]["startDate"] != start_date:
        raise ValueError("evaluationSampleSplits.train.startDate 必须等于 startDate")
    if normalized[-1]["endDate"] != end_date:
        raise ValueError("evaluationSampleSplits.test_oos.endDate 必须等于 endDate")
    return normalized


def canonical_json_bytes(value: Any) -> bytes:
    normalized = _normalize_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


def canonical_run_config_sha256(config: dict[str, Any]) -> str:
    identity = _normalize_value(config)
    universe = dict(identity.get("universe") or {})
    universe.pop("source", None)
    if "sourceArtifact" in universe:
        source_artifact = dict(universe.get("sourceArtifact") or {})
        source_artifact.pop("path", None)
        universe["sourceArtifact"] = source_artifact
    identity["universe"] = universe
    return canonical_sha256(identity)


def validate_run_config(
    config: dict[str, Any],
    *,
    verify_universe_source: bool = True,
) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError("研究配置必须是 JSON object")
    missing = [field for field in REQUIRED_CONFIG_FIELDS if field not in config]
    if missing:
        raise ValueError(f"研究配置缺少字段：{', '.join(missing)}")

    normalized = _normalize_value(config)
    if normalized["scope"] not in {"etf_time_series", "a_share_cross_section"}:
        raise ValueError("scope 不受支持")
    if normalized["timezone"] != "Asia/Shanghai":
        raise ValueError("正式研究时区必须为 Asia/Shanghai")
    if not str(normalized["strategyId"]).strip() or not str(normalized["strategyVersion"]).strip():
        raise ValueError("strategyId 和 strategyVersion 不能为空")
    if not str(normalized["benchmark"]).strip():
        raise ValueError("benchmark 不能为空")
    if not str(normalized["qualityRunId"]).strip() or str(normalized["qualityRunId"]).startswith("__"):
        raise ValueError("qualityRunId 必须绑定已完成的质量运行")
    if isinstance(normalized["randomSeed"], bool) or not isinstance(normalized["randomSeed"], int):
        raise ValueError("randomSeed 必须是整数")
    for field in ("featureParameters", "targetWeightParameters", "executionPolicy", "costModel"):
        if not isinstance(normalized[field], dict):
            raise ValueError(f"{field} 必须是 JSON object")
    if not isinstance(normalized["allowedWarnings"], list):
        raise ValueError("allowedWarnings 必须是数组")
    normalized["allowedWarnings"] = sorted(
        {str(value).strip() for value in normalized["allowedWarnings"] if str(value).strip()}
    )
    if "validationPolicy" in normalized:
        normalized["validationPolicy"] = validate_validation_policy(
            normalized["validationPolicy"]
        )
    else:
        validate_validation_policy(None)
    if "riskPolicy" in normalized:
        normalized["riskPolicy"] = validate_risk_policy(normalized["riskPolicy"])
    else:
        validate_risk_policy(None)

    warmup_start = _parse_date(normalized["warmupStart"], "warmupStart")
    start_date = _parse_date(normalized["startDate"], "startDate")
    end_date = _parse_date(normalized["endDate"], "endDate")
    if not warmup_start <= start_date <= end_date:
        raise ValueError("日期必须满足 warmupStart <= startDate <= endDate")

    has_evaluation_policy = "evaluationPolicy" in normalized
    has_evaluation_splits = "evaluationSampleSplits" in normalized
    if has_evaluation_policy != has_evaluation_splits:
        raise ValueError(
            "evaluationPolicy 与 evaluationSampleSplits 必须同时冻结"
        )
    if has_evaluation_policy:
        normalized["evaluationPolicy"] = validate_evaluation_policy(
            normalized["evaluationPolicy"]
        )
        normalized["evaluationSampleSplits"] = validate_evaluation_sample_splits(
            normalized["evaluationSampleSplits"],
            start_date=normalized["startDate"],
            end_date=normalized["endDate"],
        )

    _validate_universe(
        normalized["universe"],
        scope=normalized["scope"],
        start_date=normalized["startDate"],
        verify_source=verify_universe_source,
    )
    return normalized


def _finite_decimal_string(value: Any, field: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"evaluationPolicy.{field} 必须使用字符串化十进制定点")
    try:
        parsed = Decimal(value)
    except Exception as exc:
        raise ValueError(
            f"evaluationPolicy.{field} 必须使用字符串化十进制定点"
        ) from exc
    if not parsed.is_finite():
        raise ValueError(f"evaluationPolicy.{field} 必须是有限数")
    return parsed


def build_reproducibility_key(
    *,
    config_sha256: str | None,
    data_snapshot_id: str | None,
    code_commit: str | None,
    environment_sha256: str | None,
    random_seed: int | None,
) -> str:
    required = {
        "config_sha256": config_sha256,
        "data_snapshot_id": data_snapshot_id,
        "code_commit": code_commit,
        "environment_sha256": environment_sha256,
        "random_seed": random_seed,
    }
    missing = [
        key
        for key, value in required.items()
        if value is None or (isinstance(value, str) and not value.strip())
    ]
    if missing:
        raise FormalRunConfigurationError(f"正式运行缺少可复现身份字段：{', '.join(missing)}")
    if isinstance(random_seed, bool) or not isinstance(random_seed, int):
        raise FormalRunConfigurationError("正式运行 random_seed 必须是整数")
    return canonical_sha256(required)


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON 禁止 NaN 或 Infinity")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"canonical JSON 不支持类型：{type(value).__name__}")


def _parse_date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{field} 必须是 YYYY-MM-DD") from exc


def _validate_universe(
    universe: Any,
    *,
    scope: str,
    start_date: str,
    verify_source: bool,
) -> None:
    if not isinstance(universe, dict):
        raise ValueError("universe 必须是 JSON object")
    if universe.get("mode") == "industry_membership":
        if set(universe) != {"mode", "source", "sourceKey"}:
            raise ValueError("industry_membership universe 只允许 mode/source/sourceKey")
        if scope != "a_share_cross_section":
            raise ValueError("industry_membership 只允许 A 股横截面研究")
        if universe.get("source") != "industry_members":
            raise ValueError("industry_membership source 必须为 industry_members")
        source_key = str(universe.get("sourceKey") or "").strip().upper()
        if not source_key or source_key != universe.get("sourceKey") or len(source_key) > 32:
            raise ValueError("industry_membership sourceKey 必须是规范化行业代码")
        result = evaluate_universe_provenance(universe, scope, start_date)
        if result["status"] == "blocked":
            raise ValueError(f"universe 来源门禁未通过：{', '.join(result['blockers'])}")
        return
    required = {
        "mode",
        "source",
        "sourceArtifact",
        "asOfDate",
        "members",
        "memberArtifact",
        "universeHash",
    }
    missing = sorted(required - set(universe))
    if missing:
        raise ValueError(f"universe 缺少字段：{', '.join(missing)}")
    if universe["mode"] != "explicit_snapshot":
        raise ValueError("universe mode 不受支持")
    source = str(universe["source"]).strip()
    source_artifact = universe["sourceArtifact"]
    if not source or Path(source).is_absolute():
        raise ValueError("universe.source 必须是仓库相对的实际成员文件，禁止本机绝对路径")
    if not isinstance(source_artifact, dict) or source_artifact.get("path") != source:
        raise ValueError("universe.sourceArtifact 必须绑定同一成员文件")
    if source_artifact.get("format") != "sorted_symbols_v1":
        raise ValueError("universe.sourceArtifact format 必须为 sorted_symbols_v1")
    if not SHA256_PATTERN.fullmatch(str(source_artifact.get("sha256") or "")):
        raise ValueError("universe.sourceArtifact.sha256 无效")
    members = sorted({str(value).strip().upper() for value in universe["members"] if str(value).strip()})
    if not members or members != universe["members"]:
        raise ValueError("universe.members 必须非空、升序且去重")
    _parse_date(universe["asOfDate"], "universe.asOfDate")
    member_artifact = universe["memberArtifact"]
    if (
        not isinstance(member_artifact, dict)
        or member_artifact.get("format") != "inline_sorted_symbols"
        or member_artifact.get("count") != len(members)
        or member_artifact.get("sha256") != canonical_sha256(members)
    ):
        raise ValueError("universe.memberArtifact 与 members 不一致")
    expected_hash = canonical_sha256(_universe_identity(universe))
    if universe.get("universeHash") != expected_hash:
        raise ValueError("universe.universeHash 与来源/成员工件不一致")
    if verify_source:
        source_path = (REPO_ROOT / source).resolve()
        if not source_path.is_relative_to(REPO_ROOT) or not source_path.is_file():
            raise ValueError("universe.source 必须解析为仓库内实际存在的成员文件")
        verified_universe = {
            **universe,
            "source": str(source_path),
            "sourceArtifact": {
                **source_artifact,
                "path": str(source_path),
            },
        }
        result = evaluate_universe_provenance(verified_universe, scope, start_date)
        if result["status"] == "blocked":
            raise ValueError(f"universe 来源门禁未通过：{', '.join(result['blockers'])}")


def _universe_identity(universe: dict[str, Any]) -> dict[str, Any]:
    payload = {
        key: value
        for key, value in universe.items()
        if key not in {"universeHash", "source"}
    }
    source_artifact = payload.get("sourceArtifact")
    if isinstance(source_artifact, dict):
        payload["sourceArtifact"] = {
            key: source_artifact.get(key)
            for key in ("format", "sha256")
            if source_artifact.get(key) is not None
        }
    return payload
