from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping

import pandas as pd

from .metrics import summarize_performance, summarize_trading_observations
from .reporting import tail_metrics
from .run_config import canonical_json_bytes, canonical_sha256


EVIDENCE_SCHEMA_VERSION = "strategy-evidence-bundle/v1"
EVALUATION_SCHEMA_VERSION = "research-evaluation/v1"
EVALUATION_VERSION = 1
EVIDENCE_STATUSES = {
    "complete",
    "not_applicable",
    "not_available",
    "blocked",
    "failed",
}
RESEARCH_CONCLUSIONS = {
    "研究通过",
    "有条件候选",
    "证据不足",
    "受阻",
    "不通过",
}
_ROBUSTNESS_FIELDS = (
    "walkForward",
    "parameterNeighborhood",
    "costStress",
    "marketRegimes",
    "capacity",
)


class EvaluationContractError(ValueError):
    """The frozen evidence cannot produce a canonical evaluation."""


@dataclass(frozen=True, init=False)
class StrategyEvidenceBundle:
    """An immutable, canonical JSON strategy-evidence contract."""

    _canonical_json: bytes

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StrategyEvidenceBundle":
        normalized = _validate_bundle(value)
        instance = object.__new__(cls)
        object.__setattr__(instance, "_canonical_json", canonical_json_bytes(normalized))
        return instance

    @property
    def schema_version(self) -> str:
        return EVIDENCE_SCHEMA_VERSION

    @property
    def evidence_sha256(self) -> str:
        return canonical_sha256(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._canonical_json)


@dataclass(frozen=True, init=False)
class EvaluationResult:
    """An immutable evaluation whose hash identifies all canonical fields."""

    _canonical_json: bytes

    @classmethod
    def _from_payload(cls, payload: Mapping[str, Any]) -> "EvaluationResult":
        identity = dict(payload)
        if identity.get("conclusion") not in RESEARCH_CONCLUSIONS:
            raise EvaluationContractError("EvaluationResult 结论不属于冻结五类结论")
        evaluation_sha256 = canonical_sha256(identity)
        instance = object.__new__(cls)
        object.__setattr__(
            instance,
            "_canonical_json",
            canonical_json_bytes(
                {**identity, "evaluationSha256": evaluation_sha256}
            ),
        )
        return instance

    @property
    def schema_version(self) -> str:
        return EVALUATION_SCHEMA_VERSION

    @property
    def evaluation_sha256(self) -> str:
        return str(self.to_dict()["evaluationSha256"])

    @property
    def conclusion(self) -> str:
        return str(self.to_dict()["conclusion"])

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._canonical_json)


def evaluate_research(bundle: StrategyEvidenceBundle) -> EvaluationResult:
    """Evaluate only frozen evidence; this function performs no I/O."""

    if not isinstance(bundle, StrategyEvidenceBundle):
        raise TypeError("evaluate_research 只接受 StrategyEvidenceBundle")
    evidence = bundle.to_dict()
    sections = {
        "navEvidence": evidence["navEvidence"],
        "tradingEvidence": evidence["tradingEvidence"],
        **evidence["robustnessEvidence"],
    }
    failed = [name for name, item in sections.items() if item["status"] == "failed"]
    failed.extend(
        gate["gateId"] for gate in evidence["hardGates"] if gate["status"] == "failed"
    )
    if failed:
        raise EvaluationContractError(
            "评价计算失败的证据不能冻结 EvaluationResult：" + ", ".join(sorted(failed))
        )

    nav_metrics = _nav_metrics(evidence["navEvidence"])
    trading_metrics = _trading_metrics(evidence["tradingEvidence"])
    robustness = {
        name: evidence["robustnessEvidence"][name] for name in _ROBUSTNESS_FIELDS
    }
    canonical_metrics = {**nav_metrics, "trading": trading_metrics}
    conclusion = _conclusion(evidence, sections, canonical_metrics)
    gates = sorted(evidence["hardGates"], key=lambda item: item["gateId"])
    supporting = [
        gate["gateId"]
        for gate in gates
        if gate["status"] == "complete" and gate["passed"] is True
    ]
    opposing = [
        gate["gateId"]
        for gate in gates
        if gate["status"] == "complete" and gate["passed"] is False
    ]
    missing = [
        {"evidence": name, "status": item["status"], "reason": item["reason"]}
        for name, item in sorted(sections.items())
        if item["status"] in {"not_available", "blocked"}
    ]
    missing.extend(
        {
            "evidence": gate["gateId"],
            "status": gate["status"],
            "reason": gate["reason"],
        }
        for gate in gates
        if gate["status"] in {"not_available", "blocked"}
    )
    missing.extend(_collect_missing(canonical_metrics, "metrics"))
    return EvaluationResult._from_payload(
        {
            "schemaVersion": EVALUATION_SCHEMA_VERSION,
            "evaluationVersion": EVALUATION_VERSION,
            "evidenceSchemaVersion": EVIDENCE_SCHEMA_VERSION,
            "evidenceBundleSha256": bundle.evidence_sha256,
            "strategyId": evidence["strategyId"],
            "strategyVersion": evidence["strategyVersion"],
            "formalResearchId": evidence["formalResearchId"],
            "planSha256": evidence["planSha256"],
            "conclusion": conclusion,
            "runIdentities": evidence["runIdentities"],
            "evidenceRefs": evidence["evidenceRefs"],
            "metrics": canonical_metrics,
            "robustness": robustness,
            "gates": gates,
            "supportingEvidence": supporting,
            "opposingEvidence": opposing,
            "missingEvidence": missing,
            "limitations": evidence["limitations"],
            "strategyFacts": evidence["strategyFacts"],
        }
    )


def _validate_bundle(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EvaluationContractError("StrategyEvidenceBundle 必须是对象")
    payload = dict(value)
    required = {
        "schemaVersion",
        "strategyId",
        "strategyVersion",
        "formalResearchId",
        "planSha256",
        "runIdentities",
        "evidenceRefs",
        "navEvidence",
        "tradingEvidence",
        "robustnessEvidence",
        "hardGates",
        "conditionalCandidateRule",
        "strategyFacts",
        "limitations",
    }
    if set(payload) != required:
        raise EvaluationContractError(
            "StrategyEvidenceBundle 字段必须精确冻结；差异："
            + ", ".join(sorted(set(payload) ^ required))
        )
    if payload["schemaVersion"] != EVIDENCE_SCHEMA_VERSION:
        raise EvaluationContractError("StrategyEvidenceBundle schemaVersion 无效")
    for field in ("strategyId", "strategyVersion", "formalResearchId"):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise EvaluationContractError(f"{field} 必须是非空字符串")
    _require_sha256(payload["planSha256"], "planSha256")
    payload["runIdentities"] = _validate_run_identities(payload["runIdentities"])
    payload["evidenceRefs"] = _validate_evidence_refs(payload["evidenceRefs"])
    payload["navEvidence"] = _validate_nav_evidence(payload["navEvidence"])
    payload["tradingEvidence"] = _validate_trading_evidence(payload["tradingEvidence"])
    if (
        payload["navEvidence"]["status"] == "complete"
        and payload["tradingEvidence"]["status"] == "complete"
        and [item["tradeDate"] for item in payload["navEvidence"]["observations"]]
        != [
            item["tradeDate"]
            for item in payload["tradingEvidence"]["observations"]
        ]
    ):
        raise EvaluationContractError("NAV 与交易证据必须覆盖相同交易日")
    robustness = payload["robustnessEvidence"]
    if not isinstance(robustness, Mapping) or set(robustness) != set(_ROBUSTNESS_FIELDS):
        raise EvaluationContractError("robustnessEvidence 必须包含固定五类稳健性证据")
    payload["robustnessEvidence"] = {
        name: _validate_status(
            robustness[name],
            f"robustnessEvidence.{name}",
            require_not_applicable_capability=True,
        )
        for name in _ROBUSTNESS_FIELDS
    }
    payload["hardGates"] = _validate_gates(payload["hardGates"])
    payload["conditionalCandidateRule"] = _validate_conditional_rule(
        payload["conditionalCandidateRule"], payload["hardGates"]
    )
    if not isinstance(payload["strategyFacts"], Mapping):
        raise EvaluationContractError("strategyFacts 必须是对象")
    payload["strategyFacts"] = dict(payload["strategyFacts"])
    if not isinstance(payload["limitations"], list) or any(
        not isinstance(item, str) or not item.strip() for item in payload["limitations"]
    ):
        raise EvaluationContractError("limitations 必须是非空字符串数组")
    canonical_json_bytes(payload)
    return json.loads(canonical_json_bytes(payload))


def _validate_run_identities(value: Any) -> list[dict[str, Any]]:
    fields = {
        "runId",
        "status",
        "codeCommit",
        "dataSnapshotId",
        "configSha256",
        "environmentSha256",
        "reproducibilityKey",
        "resultFingerprint",
    }
    if not isinstance(value, list) or not value:
        raise EvaluationContractError("runIdentities 必须包含全部终态运行")
    result = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != fields:
            raise EvaluationContractError("runIdentity 字段必须精确冻结")
        run = dict(item)
        if run["status"] not in {"succeeded", "failed", "interrupted", "stopped"}:
            raise EvaluationContractError("runIdentity.status 必须是终态")
        for field in fields - {"status"}:
            if not isinstance(run[field], str) or not run[field]:
                raise EvaluationContractError(f"runIdentity.{field} 必须是非空字符串")
        _require_sha256(run["configSha256"], "runIdentity.configSha256")
        _require_sha256(run["environmentSha256"], "runIdentity.environmentSha256")
        _require_sha256(run["resultFingerprint"], "runIdentity.resultFingerprint")
        result.append(run)
    if len({item["runId"] for item in result}) != len(result):
        raise EvaluationContractError("runIdentities.runId 不能重复")
    return sorted(result, key=lambda item: item["runId"])


def _validate_evidence_refs(value: Any) -> list[dict[str, str]]:
    fields = {"artifactName", "uri", "sha256"}
    if not isinstance(value, list) or not value:
        raise EvaluationContractError("evidenceRefs 必须绑定 canonical 工件")
    result = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != fields:
            raise EvaluationContractError("evidenceRef 字段必须精确冻结")
        ref = dict(item)
        for field in ("artifactName", "uri"):
            if not isinstance(ref[field], str) or not ref[field].strip():
                raise EvaluationContractError(f"evidenceRef.{field} 必须是非空字符串")
        _require_sha256(ref["sha256"], "evidenceRef.sha256")
        result.append(ref)
    if len({item["artifactName"] for item in result}) != len(result):
        raise EvaluationContractError("evidenceRef.artifactName 不能重复")
    return sorted(result, key=lambda item: item["artifactName"])


def _validate_nav_evidence(value: Any) -> dict[str, Any]:
    status = _validate_status(
        value,
        "navEvidence",
        complete_fields={"initialNav", "initialBenchmarkNav", "observations"},
        require_not_applicable_capability=True,
        allowed_statuses=EVIDENCE_STATUSES - {"not_applicable"},
    )
    if status["status"] != "complete":
        return status
    for field in ("initialNav", "initialBenchmarkNav"):
        status[field] = _finite_number(status[field], f"navEvidence.{field}", positive=True)
    observations = status["observations"]
    fields = {"tradeDate", "nav", "benchmarkNav"}
    if not isinstance(observations, list) or not observations:
        raise EvaluationContractError("navEvidence.observations 不能为空")
    normalized = []
    for item in observations:
        if not isinstance(item, Mapping) or set(item) != fields:
            raise EvaluationContractError("NAV 观察字段必须为 tradeDate/nav/benchmarkNav")
        row = dict(item)
        row["nav"] = _finite_number(row["nav"], "navEvidence.nav", positive=True)
        row["benchmarkNav"] = _finite_number(
            row["benchmarkNav"], "navEvidence.benchmarkNav", positive=True
        )
        try:
            row["tradeDate"] = pd.Timestamp(row["tradeDate"]).date().isoformat()
        except (TypeError, ValueError) as exc:
            raise EvaluationContractError("navEvidence.tradeDate 无效") from exc
        normalized.append(row)
    dates = [item["tradeDate"] for item in normalized]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise EvaluationContractError("navEvidence 日期必须严格升序且唯一")
    status["observations"] = normalized
    return status


def _validate_trading_evidence(value: Any) -> dict[str, Any]:
    status = _validate_status(
        value,
        "tradingEvidence",
        complete_fields={"observations"},
        require_not_applicable_capability=True,
        allowed_statuses=EVIDENCE_STATUSES - {"not_applicable"},
    )
    if status["status"] != "complete":
        return status
    fields = {
        "tradeDate",
        "requestCount",
        "executionCount",
        "blockedCount",
        "blockedRequestCount",
        "oneWayTurnover",
        "transactionCostRate",
    }
    observations = status["observations"]
    if not isinstance(observations, list) or not observations:
        raise EvaluationContractError("tradingEvidence.observations 不能为空")
    normalized = []
    for item in observations:
        if not isinstance(item, Mapping) or set(item) != fields:
            raise EvaluationContractError("交易观察字段必须精确冻结")
        row = dict(item)
        for field in (
            "requestCount",
            "executionCount",
            "blockedCount",
            "blockedRequestCount",
        ):
            if isinstance(row[field], bool) or not isinstance(row[field], int) or row[field] < 0:
                raise EvaluationContractError(f"tradingEvidence.{field} 必须是非负整数")
        if (
            row["executionCount"] > row["requestCount"]
            or row["blockedCount"] > row["requestCount"]
            or row["blockedRequestCount"] > row["blockedCount"]
        ):
            raise EvaluationContractError("交易请求、成交与阻塞计数不闭合")
        for field in ("oneWayTurnover", "transactionCostRate"):
            row[field] = _finite_number(row[field], f"tradingEvidence.{field}")
            if row[field] < 0:
                raise EvaluationContractError(f"tradingEvidence.{field} 不能为负")
        try:
            row["tradeDate"] = pd.Timestamp(row["tradeDate"]).date().isoformat()
        except (TypeError, ValueError) as exc:
            raise EvaluationContractError("tradingEvidence.tradeDate 无效") from exc
        normalized.append(row)
    dates = [item["tradeDate"] for item in normalized]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise EvaluationContractError("tradingEvidence 日期必须严格升序且唯一")
    status["observations"] = normalized
    return status


def _validate_status(
    value: Any,
    field: str,
    *,
    complete_fields: set[str] | None = None,
    require_not_applicable_capability: bool = False,
    allowed_statuses: set[str] = EVIDENCE_STATUSES,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("status") not in allowed_statuses:
        raise EvaluationContractError(f"{field}.status 无效")
    result = dict(value)
    status = result["status"]
    if status == "complete":
        expected = {"status", *(complete_fields or {"value"})}
        if set(result) != expected:
            raise EvaluationContractError(f"{field} complete 字段无效")
        if complete_fields is None and not _is_meaningful(result["value"]):
            raise EvaluationContractError(f"{field} complete value 不能为空")
    else:
        expected = {"status", "reason"}
        if status == "not_applicable" and require_not_applicable_capability:
            expected.add("capability")
        if (
            set(result) != expected
            or not isinstance(result["reason"], str)
            or not result["reason"].strip()
            or (
                "capability" in expected
                and (
                    not isinstance(result["capability"], str)
                    or not result["capability"].strip()
                )
            )
        ):
            raise EvaluationContractError(f"{field} 缺失状态必须提供结构化原因")
    canonical_json_bytes(result)
    return result


def _is_meaningful(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (list, dict)) and not value:
        return False
    return True


def _validate_gates(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise EvaluationContractError("hardGates 不能为空")
    result = []
    for item in value:
        if (
            not isinstance(item, Mapping)
            or item.get("status") not in EVIDENCE_STATUSES - {"not_applicable"}
        ):
            raise EvaluationContractError("hardGate.status 无效")
        gate = dict(item)
        if not isinstance(gate.get("gateId"), str) or not gate["gateId"]:
            raise EvaluationContractError("hardGate.gateId 必须是非空字符串")
        if gate["status"] == "complete":
            if set(gate) != {"gateId", "status", "passed"} or not isinstance(gate["passed"], bool):
                raise EvaluationContractError("complete hardGate 必须提供 passed")
        elif set(gate) != {"gateId", "status", "reason"} or not isinstance(gate["reason"], str) or not gate["reason"].strip():
            raise EvaluationContractError("缺失 hardGate 必须提供 reason")
        result.append(gate)
    if len({item["gateId"] for item in result}) != len(result):
        raise EvaluationContractError("hardGate.gateId 不能重复")
    return sorted(result, key=lambda item: item["gateId"])


def _validate_conditional_rule(value: Any, gates: list[dict[str, Any]]) -> dict[str, Any]:
    rule = _validate_status(value, "conditionalCandidateRule", complete_fields={"allowedFailedGateIds"})
    if rule["status"] == "complete":
        allowed = rule["allowedFailedGateIds"]
        gate_ids = {item["gateId"] for item in gates}
        if not isinstance(allowed, list) or not allowed or any(not isinstance(item, str) for item in allowed):
            raise EvaluationContractError("有条件候选规则必须列出允许失败的冻结 gateId")
        if len(set(allowed)) != len(allowed) or not set(allowed).issubset(gate_ids):
            raise EvaluationContractError("有条件候选规则引用未知或重复 gateId")
        rule["allowedFailedGateIds"] = sorted(allowed)
    return rule


def _nav_metrics(evidence: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if evidence["status"] != "complete":
        status = dict(evidence)
        return {"returns": status, "risk": status, "benchmark": status}
    frame = pd.DataFrame(evidence["observations"])
    strategy = frame[["tradeDate", "nav"]].rename(
        columns={"tradeDate": "trade_date"}
    )
    benchmark = frame[["tradeDate", "benchmarkNav"]].rename(
        columns={"tradeDate": "trade_date", "benchmarkNav": "nav"}
    )
    summary = summarize_performance(
        strategy,
        benchmark,
        include_extended=True,
        initial_strategy_nav=float(evidence["initialNav"]),
        initial_benchmark_nav=float(evidence["initialBenchmarkNav"]),
    )
    path = pd.concat(
        [pd.Series([float(evidence["initialNav"])]), frame["nav"].astype(float)],
        ignore_index=True,
    )
    returns = path.pct_change(fill_method=None).dropna()
    tails = {key: _finite_or_none(value) for key, value in tail_metrics(returns).items()}
    return {
        "returns": {
            "status": "complete",
            "value": {
                key: _structured_metric(key, summary[key])
                for key in (
                    "startDate",
                    "endDate",
                    "observations",
                    "totalReturn",
                    "annualizedReturn",
                    "annualizedVolatility",
                    "sharpe",
                    "positiveDayRate",
                    "downsideVolatility",
                    "sortino",
                    "calmar",
                )
            },
        },
        "risk": {
            "status": "complete",
            "value": {
                "maxDrawdown": summary["maxDrawdown"],
                "maxDrawdownDuration": summary["maxDrawdownDuration"],
                **{
                    key: _structured_metric(key, value)
                    for key, value in tails.items()
                },
            },
        },
        "benchmark": {
            "status": "complete",
            "value": {
                key: _structured_metric(key, summary[key])
                for key in (
                    "benchmarkTotalReturn",
                    "excessTotalReturn",
                    "trackingError",
                    "informationRatio",
                    "beta",
                )
            },
        },
    }


def _trading_metrics(evidence: Mapping[str, Any]) -> dict[str, Any]:
    if evidence["status"] != "complete":
        return dict(evidence)
    frame = pd.DataFrame(evidence["observations"]).rename(
        columns={
            "tradeDate": "trade_date",
            "requestCount": "request_count",
            "executionCount": "execution_count",
            "blockedCount": "blocked_count",
            "blockedRequestCount": "fully_blocked_count",
            "oneWayTurnover": "one_way_turnover",
            "transactionCostRate": "transaction_cost_rate",
        }
    )
    return {
        "status": "complete",
        "value": summarize_trading_observations(frame),
    }


def _conclusion(
    evidence: Mapping[str, Any],
    sections: Mapping[str, Mapping[str, Any]],
    canonical_metrics: Mapping[str, Any],
) -> str:
    gates = evidence["hardGates"]
    if any(item["status"] == "blocked" for item in sections.values()) or any(
        gate["status"] == "blocked" for gate in gates
    ):
        return "受阻"
    if (
        any(item["status"] == "not_available" for item in sections.values())
        or any(gate["status"] == "not_available" for gate in gates)
        or _contains_status(canonical_metrics, "not_available")
    ):
        return "证据不足"
    failed_gate_ids = {
        gate["gateId"]
        for gate in gates
        if gate["status"] == "complete" and gate["passed"] is False
    }
    if failed_gate_ids:
        rule = evidence["conditionalCandidateRule"]
        if rule["status"] == "complete" and failed_gate_ids.issubset(
            set(rule["allowedFailedGateIds"])
        ):
            return "有条件候选"
        return "不通过"
    return "研究通过"


def _collect_missing(value: Any, path: str) -> list[dict[str, str]]:
    if isinstance(value, Mapping):
        if value.get("status") in {"not_available", "blocked"}:
            return [
                {
                    "evidence": path,
                    "status": str(value["status"]),
                    "reason": str(value["reason"]),
                }
            ]
        result = []
        for key, item in value.items():
            result.extend(_collect_missing(item, f"{path}.{key}"))
        return result
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value):
            result.extend(_collect_missing(item, f"{path}.{index}"))
        return result
    return []


def _contains_status(value: Any, expected: str) -> bool:
    if isinstance(value, Mapping):
        return value.get("status") == expected or any(
            _contains_status(item, expected) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_status(item, expected) for item in value)
    return False


def _structured_metric(field: str, value: Any) -> Any:
    if value is not None:
        return value
    if field == "calmar":
        return {
            "status": "not_applicable",
            "capability": "nonzero_drawdown",
            "reason": "最大回撤为零，Calmar 无定义",
        }
    return {
        "status": "not_available",
        "reason": f"冻结样本不足以计算 {field}",
    }


def _finite_number(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationContractError(f"{field} 必须是有限数")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise EvaluationContractError(f"{field} 必须是有限正数" if positive else f"{field} 必须是有限数")
    return number


def _finite_or_none(value: Any) -> float | None:
    number = float(value)
    return number if math.isfinite(number) else None


def _require_sha256(value: Any, field: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise EvaluationContractError(f"{field} 必须是小写 sha256")
