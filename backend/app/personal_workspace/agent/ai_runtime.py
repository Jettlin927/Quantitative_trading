"""Provider-neutral AI Runtime interface 与执行门禁。

Completion 或未来 Hosted Tool adapter 只能返回这里定义的事件和 usage；供应商原始
JSON 留在 adapter implementation 内。Hosted Tool 目前仅用于隔离合同实验，不接生产路径。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit

from .domain_tools import DomainToolResult, RuntimeToolDefinition


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
    hosted_tool_calls: int = 0
    web_search_queries: int = 0
    hosted_cost_usd: Decimal = Decimal("0")


@dataclass(frozen=True)
class RuntimeEvidence:
    evidence_id: str
    url: str
    title: str
    verified_excerpt: str
    body_sha256: str
    verified_at: datetime


@dataclass(frozen=True)
class RuntimeCitation:
    evidence_id: str
    output_block_index: int
    cited_text_sha256: str
    start_char: int | None = None
    end_char: int | None = None


@dataclass(frozen=True)
class RuntimeEvent:
    type: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    arguments: dict[str, Any] | None = None
    evidence_ids: tuple[str, ...] = ()
    text: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class RuntimeToolEvidence:
    evidence_id: str
    source: str
    as_of: datetime
    content_sha256: str
    authorized_fields: tuple[str, ...]
    excerpt: str


@dataclass(frozen=True)
class RuntimeFailure:
    code: str
    retryable: bool
    outcome_unknown: bool = False


@dataclass(frozen=True)
class RuntimeResult:
    status: str
    events: tuple[RuntimeEvent, ...]
    usage: RuntimeUsage | None
    failure: RuntimeFailure | None
    evidence: tuple[RuntimeEvidence, ...] = ()
    citations: tuple[RuntimeCitation, ...] = ()
    tool_evidence: tuple[RuntimeToolEvidence, ...] = ()

    @classmethod
    def completed(
        cls,
        *,
        events: tuple[RuntimeEvent, ...],
        usage: RuntimeUsage,
        evidence: tuple[RuntimeEvidence, ...] = (),
        citations: tuple[RuntimeCitation, ...] = (),
        tool_evidence: tuple[RuntimeToolEvidence, ...] = (),
    ) -> "RuntimeResult":
        return cls(
            status="completed",
            events=events,
            usage=usage,
            failure=None,
            evidence=evidence,
            citations=citations,
            tool_evidence=tool_evidence,
        )

    @classmethod
    def failed(
        cls,
        code: str,
        *,
        retryable: bool = False,
        outcome_unknown: bool = False,
        events: tuple[RuntimeEvent, ...] | None = None,
        usage: RuntimeUsage | None = None,
        tool_evidence: tuple[RuntimeToolEvidence, ...] = (),
    ) -> "RuntimeResult":
        return cls(
            status="failed",
            events=events or (RuntimeEvent(type="run_failed"),),
            usage=usage,
            failure=RuntimeFailure(
                code=code,
                retryable=retryable,
                outcome_unknown=outcome_unknown,
            ),
            tool_evidence=tool_evidence,
        )

    @classmethod
    def cancelled(
        cls,
        *,
        events: tuple[RuntimeEvent, ...] | None = None,
        usage: RuntimeUsage | None = None,
        tool_evidence: tuple[RuntimeToolEvidence, ...] = (),
    ) -> "RuntimeResult":
        return cls(
            status="cancelled",
            events=events or (RuntimeEvent(type="run_cancelled"),),
            usage=usage,
            failure=RuntimeFailure(code="cancelled", retryable=False),
            tool_evidence=tool_evidence,
        )


class AIRuntime(Protocol):
    capabilities: AIRuntimeCapabilities

    def run(
        self,
        request: RuntimeRequest,
        context: "RuntimeExecutionContext | None" = None,
    ) -> RuntimeResult: ...


class RuntimeToolExecutor(Protocol):
    def invoke(self, name: str, arguments: dict[str, Any]) -> DomainToolResult: ...


@dataclass(frozen=True)
class RuntimeExecutionContext:
    tools: tuple[RuntimeToolDefinition, ...]
    executor: RuntimeToolExecutor | None
    deadline: datetime
    heartbeat: Callable[[], None]
    cancel_requested: Callable[[], bool] = lambda: False


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
        "provider_tool_calls_invalid",
        "agent_tool_rounds_exceeded",
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

_LOCAL_FAILURE_CODES = frozenset(
    {
        "budget_blocked",
        "budget_insufficient",
        "capability_unsupported",
        "provider_request_invalid",
        "provider_request_unsafe",
        "runtime_contract_invalid",
    }
)


def run_runtime(
    runtime: AIRuntime,
    request: RuntimeRequest,
    context: RuntimeExecutionContext | None = None,
) -> RuntimeResult:
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
    if context is not None and not _valid_execution_context(request, context):
        return RuntimeResult.failed("runtime_contract_invalid")
    try:
        result = runtime.run(request, context) if context is not None else runtime.run(request)
    except Exception as exc:
        raw_code = getattr(exc, "code", "runtime_failed")
        code = _PROVIDER_FAILURE_CODES.get(raw_code, raw_code)
        retryable = bool(getattr(exc, "retryable", False))
        if code not in RUNTIME_FAILURE_CODES:
            code = "runtime_failed"
        return RuntimeResult.failed(
            code,
            retryable=retryable,
            outcome_unknown=raw_code not in _LOCAL_FAILURE_CODES,
        )
    if not _valid_result(result, capabilities, request):
        return RuntimeResult.failed("runtime_contract_invalid")
    if (
        result.status == "completed"
        and result.usage is not None
        and result.usage.cost_usd > request.budget.remaining_usd
    ):
        return RuntimeResult(
            status="failed",
            events=(*result.events, RuntimeEvent(type="run_failed")),
            usage=result.usage,
            failure=RuntimeFailure(code="budget_exceeded", retryable=False),
            evidence=result.evidence,
            citations=result.citations,
            tool_evidence=result.tool_evidence,
        )
    return result


def _valid_execution_context(
    request: RuntimeRequest, context: RuntimeExecutionContext
) -> bool:
    if (
        context.deadline.tzinfo is None
        or context.deadline.utcoffset() is None
        or not callable(context.heartbeat)
        or not callable(context.cancel_requested)
    ):
        return False
    names = tuple(item.name for item in context.tools)
    return (
        len(names) == len(set(names))
        and names == request.tools
        and (
            (not names and context.executor is None)
            or (bool(names) and context.executor is not None)
        )
    )


def _valid_result(
    result: Any,
    capabilities: AIRuntimeCapabilities,
    request: RuntimeRequest,
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
    return (
        _tool_events_pair(result.events, request.tools)
        and _valid_client_tool_evidence_contract(result)
        and _hosted_tool_events_pair(result.events, request.hosted_tools)
        and _hosted_usage_matches(result.events, result.usage)
        and _valid_hosted_evidence_contract(result)
    )


def _valid_usage(usage: RuntimeUsage) -> bool:
    token_values = (
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_hit_tokens,
        usage.cache_miss_tokens,
        usage.hosted_tool_calls,
        usage.web_search_queries,
    )
    return all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in token_values
    ) and (
        isinstance(usage.cost_usd, Decimal)
        and isinstance(usage.hosted_cost_usd, Decimal)
        and usage.cost_usd >= Decimal("0")
        and Decimal("0") <= usage.hosted_cost_usd <= usage.cost_usd
    )


def _hosted_usage_matches(
    events: tuple[RuntimeEvent, ...], usage: RuntimeUsage | None
) -> bool:
    if usage is None:
        return not any(event.type.startswith("hosted_tool_") for event in events)
    started = tuple(event for event in events if event.type == "hosted_tool_started")
    query_count = _hosted_query_count(started)
    return query_count is not None and (
        usage.hosted_tool_calls == len(started)
        and usage.web_search_queries == query_count
    )


def _hosted_query_count(events: tuple[RuntimeEvent, ...]) -> int | None:
    count = 0
    for event in events:
        if event.tool_name != "web_search":
            continue
        arguments = event.arguments or {}
        query = arguments.get("query")
        queries = arguments.get("queries")
        if isinstance(query, str) and query.strip() and queries is None:
            count += 1
            continue
        if (
            query is None
            and isinstance(queries, (list, tuple))
            and queries
            and all(isinstance(item, str) and item.strip() for item in queries)
        ):
            count += len(queries)
            continue
        return None
    return count


def _tool_events_pair(
    events: tuple[RuntimeEvent, ...], allowed_tools: tuple[str, ...]
) -> bool:
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
    state: dict[str, tuple[str, bool]] = {}
    for event in tool_events:
        call_id = event.tool_call_id
        tool_name = event.tool_name
        if event.type == "tool_requested":
            if call_id in state:
                return False
            state[call_id] = (tool_name, False)
            continue
        requested = state.get(call_id)
        if (
            requested is None
            or requested[0] != tool_name
            or requested[1]
            or (tool_name not in allowed_tools and event.type != "tool_failed")
        ):
            return False
        state[call_id] = (tool_name, True)
    return all(finished for _, finished in state.values())


def _valid_client_tool_evidence_contract(result: RuntimeResult) -> bool:
    completed_ids = {
        evidence_id
        for event in result.events
        if event.type == "tool_completed"
        for evidence_id in event.evidence_ids
    }
    evidence_ids = [item.evidence_id for item in result.tool_evidence]
    if not completed_ids:
        return not result.tool_evidence
    if not result.tool_evidence:
        return False
    return (
        len(evidence_ids) == len(set(evidence_ids))
        and set(evidence_ids) == completed_ids
        and all(
            isinstance(item, RuntimeToolEvidence)
            and isinstance(item.evidence_id, str)
            and bool(item.evidence_id.strip())
            and isinstance(item.source, str)
            and bool(item.source.strip())
            and isinstance(item.as_of, datetime)
            and item.as_of.tzinfo is not None
            and item.as_of.utcoffset() is not None
            and _valid_sha256(item.content_sha256)
            and isinstance(item.authorized_fields, tuple)
            and all(
                isinstance(field, str) and bool(field.strip())
                for field in item.authorized_fields
            )
            and isinstance(item.excerpt, str)
            for item in result.tool_evidence
        )
    )


def _hosted_tool_events_pair(
    events: tuple[RuntimeEvent, ...], allowed_tools: tuple[str, ...]
) -> bool:
    hosted_events = tuple(
        event
        for event in events
        if event.type in {"hosted_tool_started", "hosted_tool_completed"}
    )
    if any(not event.tool_name or not event.tool_call_id for event in hosted_events):
        return False
    if any(event.tool_name not in allowed_tools for event in hosted_events):
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
    started_events = tuple(
        event for event in hosted_events if event.type == "hosted_tool_started"
    )
    started = {event.tool_call_id: event.tool_name for event in started_events}
    completed = {
        event.tool_call_id: event.tool_name
        for event in hosted_events
        if event.type == "hosted_tool_completed"
    }
    completed_events = tuple(
        event for event in hosted_events if event.type == "hosted_tool_completed"
    )
    return (
        len(started) == len(started_events)
        and len(completed) == len(completed_events)
        and started == completed
    )


def _valid_hosted_evidence_contract(result: RuntimeResult) -> bool:
    completed = tuple(
        event for event in result.events if event.type == "hosted_tool_completed"
    )
    if not completed:
        return not result.evidence and not result.citations
    if not result.evidence or not result.citations:
        return False
    if not all(isinstance(item, RuntimeEvidence) for item in result.evidence):
        return False
    if not all(isinstance(item, RuntimeCitation) for item in result.citations):
        return False
    evidence_ids = [item.evidence_id for item in result.evidence]
    event_evidence_ids = {
        evidence_id for event in completed for evidence_id in event.evidence_ids
    }
    if len(evidence_ids) != len(set(evidence_ids)) or set(evidence_ids) != event_evidence_ids:
        return False
    if not all(_valid_evidence(item) for item in result.evidence):
        return False
    outputs = tuple(
        event.text for event in result.events if event.type == "output_completed"
    )
    return all(
        _valid_citation(item, allowed_ids=set(evidence_ids), outputs=outputs)
        for item in result.citations
    )


def _valid_evidence(evidence: RuntimeEvidence) -> bool:
    if (
        not isinstance(evidence.evidence_id, str)
        or not isinstance(evidence.url, str)
        or not isinstance(evidence.title, str)
        or not isinstance(evidence.verified_excerpt, str)
        or not isinstance(evidence.body_sha256, str)
        or not isinstance(evidence.verified_at, datetime)
    ):
        return False
    parsed = urlsplit(evidence.url)
    return (
        bool(evidence.evidence_id.strip())
        and parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and bool(evidence.title.strip())
        and bool(evidence.verified_excerpt.strip())
        and _valid_sha256(evidence.body_sha256)
        and evidence.verified_at.tzinfo is not None
        and evidence.verified_at.utcoffset() is not None
        and not any(
            _contains_provider_envelope(value)
            for value in (
                evidence.evidence_id,
                evidence.url,
                evidence.title,
                evidence.verified_excerpt,
            )
        )
    )


def _valid_citation(
    citation: RuntimeCitation,
    *,
    allowed_ids: set[str],
    outputs: tuple[str | None, ...],
) -> bool:
    if (
        citation.evidence_id not in allowed_ids
        or not isinstance(citation.output_block_index, int)
        or isinstance(citation.output_block_index, bool)
        or not 0 <= citation.output_block_index < len(outputs)
        or not _valid_sha256(citation.cited_text_sha256)
    ):
        return False
    output = outputs[citation.output_block_index]
    if not isinstance(output, str):
        return False
    if citation.start_char is None and citation.end_char is None:
        cited_text = output
    elif (
        isinstance(citation.start_char, int)
        and not isinstance(citation.start_char, bool)
        and isinstance(citation.end_char, int)
        and not isinstance(citation.end_char, bool)
        and 0 <= citation.start_char < citation.end_char <= len(output)
    ):
        cited_text = output[citation.start_char:citation.end_char]
    else:
        return False
    return sha256(cited_text.encode("utf-8")).hexdigest() == citation.cited_text_sha256


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _contains_provider_envelope(value: Any) -> bool:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return False
    forbidden = {
        "choices",
        "encrypted_content",
        "encrypted_index",
        "provider_raw_response",
        "provider_response",
        "raw_response",
    }
    if isinstance(value, dict):
        raw_block_types = {
            "server_tool_use",
            "web_search_tool_result",
            "web_search_result",
        }
        return (
            bool(set(value) & forbidden)
            or value.get("type") in raw_block_types
            or any(
                _contains_provider_envelope(item) for item in value.values()
            )
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_provider_envelope(item) for item in value)
    return False
