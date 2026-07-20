from __future__ import annotations

from dataclasses import dataclass
import argparse
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import os
import re
from typing import Any

from .quant_research.run_config import (
    canonical_json_bytes,
    canonical_sha256,
    validate_evaluation_policy,
    validate_run_config,
)
from .quant_research.strategy_registry import resolve_strategy_definition


PLAN_SCHEMA_VERSION = "research-plan/v2"
PLAN_START_MARKER = "<!-- research-plan-json:start -->"
PLAN_END_MARKER = "<!-- research-plan-json:end -->"
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
DECIMAL_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
ISO_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
SPLIT_ROLES = ("train", "validation", "test_oos")
PLAN_FIELDS = {
    "schemaVersion",
    "strategy",
    "economicHypothesis",
    "runConfig",
    "dataPolicy",
    "sampleSplits",
    "parameterSpace",
    "trialBudget",
    "gates",
    "stopRules",
    "resourceBudget",
    "reportContract",
}


class ResearchPlanError(ValueError):
    pass


class ResearchPlanBudgetError(ResearchPlanError):
    pass


@dataclass(frozen=True)
class ResearchServerLimits:
    wall_clock_seconds: int = 7200
    max_trials: int = 1
    cpu_cores: Decimal = Decimal("1")
    memory_mib: int = 1536
    artifact_mib: int = 2048
    max_retries: int = 2

    @classmethod
    def from_env(cls) -> "ResearchServerLimits":
        try:
            limits = cls(
                wall_clock_seconds=int(os.getenv("RESEARCH_MAX_WALL_CLOCK_SECONDS", "7200")),
                max_trials=int(os.getenv("RESEARCH_MAX_TRIALS", "1")),
                cpu_cores=Decimal(os.getenv("RESEARCH_MAX_CPU_CORES", "1")),
                memory_mib=int(os.getenv("RESEARCH_MAX_MEMORY_MIB", "1536")),
                artifact_mib=int(os.getenv("RESEARCH_MAX_ARTIFACT_MIB", "2048")),
                max_retries=int(os.getenv("RESEARCH_MAX_RETRIES", "2")),
            )
        except (InvalidOperation, ValueError) as exc:
            raise ResearchPlanBudgetError("研究服务器资源上限环境变量无效") from exc
        if (
            limits.wall_clock_seconds <= 0
            or limits.max_trials <= 0
            or not limits.cpu_cores.is_finite()
            or limits.cpu_cores <= 0
            or limits.memory_mib <= 0
            or limits.artifact_mib <= 0
            or not 0 <= limits.max_retries <= 2
        ):
            raise ResearchPlanBudgetError("研究服务器资源上限必须为正数，且最多允许两次重试")
        return limits


@dataclass(frozen=True)
class PreparedResearchPlan:
    normalized: dict[str, Any]
    plan_sha256: str

    @property
    def approval_comment(self) -> str:
        return f"批准研究 {self.plan_sha256}"

    @property
    def stop_comment(self) -> str:
        return f"停止研究 {self.plan_sha256}"


def prepare_research_plan(
    issue_body: str,
    *,
    limits: ResearchServerLimits | None = None,
    verify_universe_source: bool = True,
) -> PreparedResearchPlan:
    raw = _extract_plan_json(issue_body)
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_json_keys)
    except json.JSONDecodeError as exc:
        raise ResearchPlanError(f"机器计划不是有效 JSON：第 {exc.lineno} 行") from exc
    normalized = normalize_research_plan(
        payload,
        limits=limits or ResearchServerLimits(),
        verify_universe_source=verify_universe_source,
    )
    return PreparedResearchPlan(normalized=normalized, plan_sha256=canonical_sha256(normalized))


def normalize_research_plan(
    payload: Any,
    *,
    limits: ResearchServerLimits,
    verify_universe_source: bool = True,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ResearchPlanError("机器计划必须是 JSON object")
    _reject_floats(payload)
    if set(payload) != PLAN_FIELDS:
        _raise_field_mismatch("机器计划", PLAN_FIELDS, set(payload))
    if payload.get("schemaVersion") != PLAN_SCHEMA_VERSION:
        raise ResearchPlanError(f"schemaVersion 只允许 {PLAN_SCHEMA_VERSION}")

    strategy = _normalize_strategy(payload["strategy"])
    hypothesis = _required_text(payload["economicHypothesis"], "economicHypothesis", 2000)
    try:
        run_config = validate_run_config(
            payload["runConfig"], verify_universe_source=verify_universe_source
        )
        resolve_strategy_definition(run_config)
    except (TypeError, ValueError) as exc:
        raise ResearchPlanError(f"runConfig 无效：{exc}") from exc
    if run_config["strategyId"] != strategy["id"]:
        raise ResearchPlanError("strategy.id 与 runConfig.strategyId 不一致")
    if run_config["strategyVersion"] != strategy["version"]:
        raise ResearchPlanError("strategy.version 与 runConfig.strategyVersion 不一致")
    for field in ("warmupStart", "startDate", "endDate"):
        _validate_iso_date(run_config[field], f"runConfig.{field}")
    universe_as_of = run_config["universe"].get("asOfDate")
    if universe_as_of is not None:
        _validate_iso_date(universe_as_of, "runConfig.universe.asOfDate")
    if run_config["executionPolicy"].get("executionPrice") != "next_trade_open":
        raise ResearchPlanError("正式研究必须使用下一交易日执行口径 next_trade_open")
    if run_config["executionPolicy"].get("signalPrice") != "close":
        raise ResearchPlanError("正式研究必须固定收盘信号口径 signalPrice=close")
    if run_config.get("validationPolicy", {}).get("mode") == "none":
        raise ResearchPlanError("正式研究必须冻结非 none 的 walk-forward validationPolicy")
    _validate_decimal_strings(run_config.get("costModel"), "runConfig.costModel")

    data_policy = payload["dataPolicy"]
    if data_policy != {"freezeSnapshot": True, "pointInTime": True}:
        raise ResearchPlanError("dataPolicy 必须明确 pointInTime=true 且 freezeSnapshot=true")
    sample_splits = _normalize_splits(payload["sampleSplits"], run_config)
    parameter_space, combinations = _normalize_parameter_space(payload["parameterSpace"])
    if combinations != 1:
        raise ResearchPlanError("初期单批次 Worker 只允许一个冻结参数组合")
    trial_budget = _normalize_trial_budget(payload["trialBudget"], combinations, limits)
    gates = _normalize_string_set(payload["gates"], "gates")
    stop_rules = _normalize_string_set(payload["stopRules"], "stopRules")
    resource_budget = _normalize_resource_budget(payload["resourceBudget"], limits)
    report_contract = _normalize_report_contract(payload["reportContract"])
    run_config = validate_run_config(
        {
            **run_config,
            "evaluationSampleSplits": sample_splits,
            "evaluationPolicy": report_contract["evaluationPolicy"],
        },
        verify_universe_source=False,
    )

    return {
        "schemaVersion": PLAN_SCHEMA_VERSION,
        "strategy": strategy,
        "economicHypothesis": hypothesis,
        "runConfig": run_config,
        "dataPolicy": {"freezeSnapshot": True, "pointInTime": True},
        "sampleSplits": sample_splits,
        "parameterSpace": parameter_space,
        "trialBudget": trial_budget,
        "gates": gates,
        "stopRules": stop_rules,
        "resourceBudget": resource_budget,
        "reportContract": report_contract,
    }


def canonical_plan_json(plan: dict[str, Any]) -> str:
    return canonical_json_bytes(plan).decode("utf-8")


def _extract_plan_json(body: str) -> str:
    if not isinstance(body, str):
        raise ResearchPlanError("Issue 正文必须是字符串")
    if body.count(PLAN_START_MARKER) != 1 or body.count(PLAN_END_MARKER) != 1:
        raise ResearchPlanError("Issue 正文必须且只能包含一组机器计划标记")
    start_marker = body.index(PLAN_START_MARKER)
    end_marker = body.index(PLAN_END_MARKER)
    if end_marker <= start_marker:
        raise ResearchPlanError("机器计划结束标记必须位于开始标记之后")
    start = start_marker + len(PLAN_START_MARKER)
    end = end_marker
    raw = body[start:end].strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n(?P<body>.*)\n```", raw, flags=re.DOTALL)
    if fenced:
        raw = fenced.group("body").strip()
    if not raw:
        raise ResearchPlanError("机器计划不能为空")
    return raw


def _normalize_strategy(value: Any) -> dict[str, str]:
    expected = {"id", "version", "displayName", "codeCommit"}
    if not isinstance(value, dict):
        raise ResearchPlanError("strategy 必须是 JSON object")
    if set(value) != expected:
        _raise_field_mismatch("strategy", expected, set(value))
    code_commit = str(value["codeCommit"])
    if not COMMIT_PATTERN.fullmatch(code_commit):
        raise ResearchPlanError("strategy.codeCommit 必须是 40 到 64 位小写十六进制提交哈希")
    return {
        "id": _required_text(value["id"], "strategy.id", 80),
        "version": _required_text(value["version"], "strategy.version", 40),
        "displayName": _required_text(value["displayName"], "strategy.displayName", 160),
        "codeCommit": code_commit,
    }


def _normalize_splits(value: Any, run_config: dict[str, Any]) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != len(SPLIT_ROLES):
        raise ResearchPlanError("sampleSplits 必须固定 train、validation、test_oos 三段")
    by_role: dict[str, dict[str, str]] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"role", "startDate", "endDate"}:
            raise ResearchPlanError("sampleSplits 每项只允许 role/startDate/endDate")
        role = str(item["role"])
        if role not in SPLIT_ROLES or role in by_role:
            raise ResearchPlanError("sampleSplits role 必须唯一且覆盖 train/validation/test_oos")
        start = _validate_iso_date(item["startDate"], f"sampleSplits.{role}.startDate")
        end = _validate_iso_date(item["endDate"], f"sampleSplits.{role}.endDate")
        if start > end:
            raise ResearchPlanError(f"sampleSplits.{role} 起始日期晚于结束日期")
        by_role[role] = {"role": role, "startDate": start.isoformat(), "endDate": end.isoformat()}
    ordered = [by_role[role] for role in SPLIT_ROLES]
    if any(
        date.fromisoformat(ordered[index]["endDate"])
        >= date.fromisoformat(ordered[index + 1]["startDate"])
        for index in range(len(ordered) - 1)
    ):
        raise ResearchPlanError("sampleSplits 必须按 train、validation、test_oos 严格不重叠")
    if ordered[0]["startDate"] != run_config["startDate"]:
        raise ResearchPlanError("sampleSplits.train.startDate 必须等于 runConfig.startDate")
    if ordered[-1]["endDate"] != run_config["endDate"]:
        raise ResearchPlanError("sampleSplits.test_oos.endDate 必须等于 runConfig.endDate")
    return ordered


def _normalize_parameter_space(value: Any) -> tuple[dict[str, list[Any]], int]:
    frozen_single_run = {"singleRun": ["frozen"]}
    if value != frozen_single_run:
        raise ResearchPlanError("初期 parameterSpace 必须固定为单一冻结批次 singleRun=[frozen]")
    return frozen_single_run, 1


def _normalize_trial_budget(
    value: Any, combinations: int, limits: ResearchServerLimits
) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != {"maxTrials"}:
        raise ResearchPlanError("trialBudget 只允许 maxTrials")
    maximum = value["maxTrials"]
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
        raise ResearchPlanError("trialBudget.maxTrials 必须是正整数")
    if maximum > combinations:
        raise ResearchPlanError("trialBudget.maxTrials 不能超过有限参数组合数")
    if maximum > limits.max_trials:
        raise ResearchPlanBudgetError(
            f"trialBudget.maxTrials={maximum} 超过服务器上限 {limits.max_trials}"
        )
    return {"maxTrials": maximum}


def _normalize_resource_budget(value: Any, limits: ResearchServerLimits) -> dict[str, Any]:
    expected = {
        "wallClockSeconds",
        "cpuCores",
        "memoryMiB",
        "artifactMiB",
        "maxRetries",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ResearchPlanError("resourceBudget 字段不完整或含未知字段")
    wall = _positive_int(value["wallClockSeconds"], "resourceBudget.wallClockSeconds")
    memory = _positive_int(value["memoryMiB"], "resourceBudget.memoryMiB")
    artifact = _positive_int(value["artifactMiB"], "resourceBudget.artifactMiB")
    retries = value["maxRetries"]
    if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 2:
        raise ResearchPlanError("resourceBudget.maxRetries 必须是 0 到 2 的整数")
    cpu_value = value["cpuCores"]
    if not isinstance(cpu_value, str) or not DECIMAL_PATTERN.fullmatch(cpu_value):
        raise ResearchPlanError("resourceBudget.cpuCores 必须使用字符串化十进制定点")
    cpu_text = cpu_value
    cpu = Decimal(cpu_text)
    if cpu <= 0:
        raise ResearchPlanError("resourceBudget.cpuCores 必须大于 0")
    exceeded = []
    if wall > limits.wall_clock_seconds:
        exceeded.append(f"wallClockSeconds>{limits.wall_clock_seconds}")
    if cpu > limits.cpu_cores:
        exceeded.append(f"cpuCores>{format(limits.cpu_cores, 'f')}")
    if memory > limits.memory_mib:
        exceeded.append(f"memoryMiB>{limits.memory_mib}")
    if artifact > limits.artifact_mib:
        exceeded.append(f"artifactMiB>{limits.artifact_mib}")
    if retries > limits.max_retries:
        exceeded.append(f"maxRetries>{limits.max_retries}")
    if exceeded:
        raise ResearchPlanBudgetError("资源预算超过服务器上限：" + "、".join(exceeded))
    return {
        "wallClockSeconds": wall,
        "cpuCores": cpu_text,
        "memoryMiB": memory,
        "artifactMiB": artifact,
        "maxRetries": retries,
    }


def _normalize_report_contract(value: Any) -> dict[str, Any]:
    expected = {
        "language",
        "requiredArtifacts",
        "conclusionValues",
        "evaluationPolicy",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ResearchPlanError("reportContract 字段不完整或含未知字段")
    if value["language"] != "zh-CN":
        raise ResearchPlanError("reportContract.language 必须是 zh-CN")
    artifacts = _normalize_string_set(value["requiredArtifacts"], "reportContract.requiredArtifacts")
    required = {
        "manifest.json",
        "metrics.json",
        "oos_metrics.json",
        "report.html",
    }
    if not required.issubset(artifacts):
        raise ResearchPlanError("reportContract.requiredArtifacts 缺少基础证据工件")
    conclusions = _normalize_string_set(value["conclusionValues"], "reportContract.conclusionValues")
    expected_conclusions = sorted({"研究通过", "有条件候选", "证据不足", "受阻", "不通过"})
    if conclusions != expected_conclusions:
        raise ResearchPlanError("reportContract.conclusionValues 必须固定为五种研究结论")
    return {
        "language": "zh-CN",
        "requiredArtifacts": artifacts,
        "conclusionValues": conclusions,
        "evaluationPolicy": validate_evaluation_policy(value["evaluationPolicy"]),
    }


def _normalize_string_set(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ResearchPlanError(f"{field} 必须是数组")
    normalized = sorted({_required_text(item, field, 500) for item in value})
    if not normalized:
        raise ResearchPlanError(f"{field} 不能为空")
    if len(normalized) != len(value):
        raise ResearchPlanError(f"{field} 不允许重复值")
    return normalized


def _validate_decimal_strings(value: Any, field: str) -> None:
    if not isinstance(value, dict) or not value:
        raise ResearchPlanError(f"{field} 必须是非空 JSON object")
    for key, item in value.items():
        if not isinstance(item, str) or not DECIMAL_PATTERN.fullmatch(item):
            raise ResearchPlanError(f"{field}.{key} 必须使用字符串化十进制定点")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ResearchPlanError(f"机器计划 JSON 不允许重复键：{key}")
        result[key] = value
    return result


def _validate_iso_date(value: Any, field: str) -> date:
    if not isinstance(value, str) or not ISO_DATE_PATTERN.fullmatch(value):
        raise ResearchPlanError(f"{field} 必须是 YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ResearchPlanError(f"{field} 必须是有效的 YYYY-MM-DD 日期") from exc


def _reject_floats(value: Any, path: str = "机器计划") -> None:
    if isinstance(value, float):
        raise ResearchPlanError(f"{path} 禁止 JSON 浮点数；阈值必须使用字符串化十进制定点")
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_floats(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_floats(item, f"{path}[{index}]")


def _required_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ResearchPlanError(f"{field} 必须是无首尾空白的非空字符串")
    if len(value) > maximum:
        raise ResearchPlanError(f"{field} 长度不能超过 {maximum}")
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ResearchPlanError(f"{field} 必须是正整数")
    return value


def _raise_field_mismatch(field: str, expected: set[str], actual: set[str]) -> None:
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    details = []
    if missing:
        details.append("缺少 " + ",".join(missing))
    if extra:
        details.append("未知 " + ",".join(extra))
    raise ResearchPlanError(f"{field} 字段不匹配：" + "；".join(details))


def _main() -> None:
    parser = argparse.ArgumentParser(description="只读校验 GitHub 研究 Issue 正文并计算规范化计划哈希。")
    parser.add_argument("issue_body", help="包含机器计划标记的 UTF-8 Markdown 文件")
    args = parser.parse_args()
    try:
        with open(args.issue_body, encoding="utf-8") as source:
            prepared = prepare_research_plan(
                source.read(),
                limits=ResearchServerLimits.from_env(),
            )
    except (OSError, ResearchPlanError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "状态": "计划合同通过",
                "plan_sha256": prepared.plan_sha256,
                "批准评论": prepared.approval_comment,
                "停止评论": prepared.stop_comment,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    _main()
