"""Provider-neutral AI Runtime interface 与执行门禁。

Completion 或未来 Hosted Tool adapter 只能返回这里定义的事件和 usage；供应商原始
JSON 留在 adapter implementation 内。本阶段不提供 Hosted Tool adapter。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from typing import Any, Protocol


@dataclass(frozen=True)
class AIRuntimeCapabilities:
    runtime_kind: str
    client_tools: bool
    hosted_tools: bool
    cancellation: bool
    usage: bool


@dataclass(frozen=True)
class RuntimeBudget:
    remaining_usd: Decimal


@dataclass(frozen=True)
class RuntimeRequest:
    model: str
    instructions: str
    input_text: str
    budget: RuntimeBudget
    tools: tuple[str, ...] = ()
    hosted_tools: tuple[str, ...] = ()
    cancel_requested: bool = False


@dataclass(frozen=True)
class RuntimeUsage:
    input_tokens: int
    output_tokens: int
    cache_hit_tokens: int
    cache_miss_tokens: int
    cost_usd: Decimal


@dataclass(frozen=True)
class RuntimeEvent:
    type: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    arguments: dict[str, Any] | None = None
    evidence_ids: tuple[str, ...] = ()
    text: str | None = None


@dataclass(frozen=True)
class RuntimeFailure:
    code: str
    retryable: bool


@dataclass(frozen=True)
class RuntimeResult:
    status: str
    events: tuple[RuntimeEvent, ...]
    usage: RuntimeUsage | None
    failure: RuntimeFailure | None

    @classmethod
    def completed(
        cls, *, events: tuple[RuntimeEvent, ...], usage: RuntimeUsage
    ) -> "RuntimeResult":
        return cls(status="completed", events=events, usage=usage, failure=None)

    @classmethod
    def failed(cls, code: str, *, retryable: bool = False) -> "RuntimeResult":
        return cls(
            status="failed",
            events=(RuntimeEvent(type="run_failed"),),
            usage=None,
            failure=RuntimeFailure(code=code, retryable=retryable),
        )

    @classmethod
    def cancelled(cls) -> "RuntimeResult":
        return cls(
            status="cancelled",
            events=(RuntimeEvent(type="run_cancelled"),),
            usage=None,
            failure=RuntimeFailure(code="cancelled", retryable=False),
        )


class AIRuntime(Protocol):
    capabilities: AIRuntimeCapabilities

    def run(self, request: RuntimeRequest) -> RuntimeResult: ...


RUNTIME_EVENT_TYPES = frozenset(
    {
        "run_started",
        "tool_requested",
        "tool_completed",
        "tool_failed",
        "hosted_tool_started",
        "hosted_tool_completed",
        "output_completed",
        "run_failed",
        "run_cancelled",
    }
)

RUNTIME_FAILURE_CODES = frozenset(
    {
        "budget_insufficient",
        "budget_exceeded",
        "cancelled",
        "capability_unsupported",
        "provider_unavailable",
        "provider_unauthorized",
        "provider_timeout",
        "provider_refusal",
        "provider_rate_limited",
        "provider_invalid_response",
        "runtime_contract_invalid",
        "runtime_failed",
    }
)

_PROVIDER_FAILURE_CODES = {
    "provider_auth_failed": "provider_unauthorized",
    "provider_balance_unavailable": "provider_unavailable",
    "provider_model_unavailable": "provider_unavailable",
    "provider_upstream_error": "provider_unavailable",
    "provider_request_invalid": "provider_invalid_response",
    "provider_response_invalid_json": "provider_invalid_response",
    "provider_response_envelope_invalid": "provider_invalid_response",
    "provider_content_invalid_json": "provider_invalid_response",
    "provider_usage_invalid": "provider_invalid_response",
    "provider_output_truncated": "provider_invalid_response",
    "provider_invalid_status": "provider_invalid_response",
}


def run_runtime(runtime: AIRuntime, request: RuntimeRequest) -> RuntimeResult:
    """执行统一前置门禁，并把 adapter 失败收敛为稳定 RuntimeResult。"""

    if request.cancel_requested:
        return RuntimeResult.cancelled()
    if request.budget.remaining_usd <= Decimal("0"):
        return RuntimeResult.failed("budget_insufficient")
    capabilities = runtime.capabilities
    if capabilities.runtime_kind not in {"completion", "hosted_tool"}:
        return RuntimeResult.failed("runtime_contract_invalid")
    if request.tools and not capabilities.client_tools:
        return RuntimeResult.failed("capability_unsupported")
    if request.hosted_tools and not capabilities.hosted_tools:
        return RuntimeResult.failed("capability_unsupported")
    try:
        result = runtime.run(request)
    except Exception as exc:
        raw_code = getattr(exc, "code", "runtime_failed")
        code = _PROVIDER_FAILURE_CODES.get(raw_code, raw_code)
        retryable = bool(getattr(exc, "retryable", False))
        if code not in RUNTIME_FAILURE_CODES:
            code = "runtime_failed"
        return RuntimeResult.failed(code, retryable=retryable)
    if not _valid_result(result, capabilities):
        return RuntimeResult.failed("runtime_contract_invalid")
    if result.usage is not None and result.usage.cost_usd > request.budget.remaining_usd:
        return RuntimeResult.failed("budget_exceeded")
    return result


def _valid_result(
    result: Any, capabilities: AIRuntimeCapabilities
) -> bool:
    if not isinstance(result, RuntimeResult):
        return False
    if result.status not in {"completed", "failed", "cancelled"}:
        return False
    if not result.events or any(event.type not in RUNTIME_EVENT_TYPES for event in result.events):
        return False
    if result.status == "completed":
        if result.failure is not None or (capabilities.usage and result.usage is None):
            return False
        if not any(event.type == "output_completed" for event in result.events):
            return False
        if result.events[0].type != "run_started" or result.events[-1].type != "output_completed":
            return False
        if any(event.type in {"run_failed", "run_cancelled"} for event in result.events):
            return False
    else:
        if result.failure is None or result.failure.code not in RUNTIME_FAILURE_CODES:
            return False
    if result.usage is not None and not _valid_usage(result.usage):
        return False
    if not capabilities.hosted_tools and any(
        event.type.startswith("hosted_tool_") for event in result.events
    ):
        return False
    if any(
        _contains_provider_envelope(event.arguments)
        or _contains_provider_envelope(event.text)
        for event in result.events
    ):
        return False
    return _tool_events_pair(result.events) and _hosted_tool_events_pair(result.events)


def _valid_usage(usage: RuntimeUsage) -> bool:
    token_values = (
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_hit_tokens,
        usage.cache_miss_tokens,
    )
    return all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in token_values
    ) and usage.cost_usd >= Decimal("0")


def _tool_events_pair(events: tuple[RuntimeEvent, ...]) -> bool:
    tool_events = tuple(
        event
        for event in events
        if event.type in {"tool_requested", "tool_completed", "tool_failed"}
    )
    if any(not event.tool_name or not event.tool_call_id for event in tool_events):
        return False
    if any(
        event.type == "tool_requested" and not isinstance(event.arguments, dict)
        for event in tool_events
    ):
        return False
    if any(
        event.type == "tool_completed" and not event.evidence_ids
        for event in tool_events
    ):
        return False
    requested = [
        event.tool_call_id for event in tool_events if event.type == "tool_requested"
    ]
    finished = [
        event.tool_call_id
        for event in tool_events
        if event.type in {"tool_completed", "tool_failed"}
    ]
    return len(requested) == len(set(requested)) and sorted(requested) == sorted(finished)


def _hosted_tool_events_pair(events: tuple[RuntimeEvent, ...]) -> bool:
    hosted_events = tuple(
        event
        for event in events
        if event.type in {"hosted_tool_started", "hosted_tool_completed"}
    )
    if any(not event.tool_name or not event.tool_call_id for event in hosted_events):
        return False
    if any(
        event.type == "hosted_tool_started" and not isinstance(event.arguments, dict)
        for event in hosted_events
    ):
        return False
    if any(
        event.type == "hosted_tool_completed" and not event.evidence_ids
        for event in hosted_events
    ):
        return False
    started = [
        event.tool_call_id
        for event in hosted_events
        if event.type == "hosted_tool_started"
    ]
    completed = [
        event.tool_call_id
        for event in hosted_events
        if event.type == "hosted_tool_completed"
    ]
    return len(started) == len(set(started)) and sorted(started) == sorted(completed)


def _contains_provider_envelope(value: Any) -> bool:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return False
    forbidden = {
        "choices",
        "provider_raw_response",
        "provider_response",
        "raw_response",
    }
    if isinstance(value, dict):
        return bool(set(value) & forbidden) or any(
            _contains_provider_envelope(item) for item in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_provider_envelope(item) for item in value)
    return False
