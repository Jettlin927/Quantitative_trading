"""本机 MCP 的固定身份、授权、资源与审计门禁。"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from threading import Lock
import time
from typing import Any
from uuid import uuid4

from backend.app.json_safety import json_safe_value

from .agent.domain_tools import (
    DomainToolContext,
    DomainToolRegistry,
    DomainToolResult,
    RuntimeToolDefinition,
)
from .agent.evidence import (
    CapabilityAuditEvent,
    CapabilityAuditStore,
    EvidenceReadContext,
)


PERSONAL_MCP_TOOL_ALLOWLIST = frozenset(
    {
        "get_today_context",
        "get_symbol_dossier",
        "search_market_news",
        "discover_related_candidates",
        "get_evidence",
    }
)
PERSONAL_MCP_PERMISSIONS = frozenset(
    {"portfolio:read", "market:read", "news:read", "evidence:read"}
)
PERSONAL_MCP_POLICY_REVISION = "personal-mcp-v1"
PERSONAL_MCP_DEADLINE_SECONDS = 20.0
PERSONAL_MCP_MAX_OUTPUT_BYTES = 256 * 1024
PERSONAL_MCP_MAX_CALLS_PER_MINUTE = 30
PERSONAL_MCP_MAX_CONCURRENCY = 2
_ACTOR_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}")


@dataclass(frozen=True)
class PersonalMcpTransportPolicy:
    channel: str
    purpose: str
    policy_revision: str


PERSONAL_MCP_STDIO_POLICY = PersonalMcpTransportPolicy(
    channel="mcp_stdio",
    purpose="mcp_stdio",
    policy_revision=PERSONAL_MCP_POLICY_REVISION,
)
PERSONAL_MCP_HTTP_POLICY = PersonalMcpTransportPolicy(
    channel="mcp_streamable_http",
    purpose="mcp_remote_read",
    policy_revision="personal-mcp-remote-v1",
)


class PersonalMcpGatewayStopped(RuntimeError):
    pass


def normalize_actor_id(value: str) -> str:
    actor_id = value.strip()
    if not _ACTOR_ID_PATTERN.fullmatch(actor_id):
        raise ValueError("personal_mcp_actor_invalid")
    return actor_id


class PersonalMcpGateway:
    """MCP 安全深模块；stdio adapter 只翻译官方协议类型。"""

    def __init__(
        self,
        *,
        registry: DomainToolRegistry,
        audit_store: CapabilityAuditStore,
        actor_id: str,
        transport_policy: PersonalMcpTransportPolicy = PERSONAL_MCP_STDIO_POLICY,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
        deadline_seconds: float = PERSONAL_MCP_DEADLINE_SECONDS,
    ) -> None:
        self._actor_id = normalize_actor_id(actor_id)
        if transport_policy not in {
            PERSONAL_MCP_STDIO_POLICY,
            PERSONAL_MCP_HTTP_POLICY,
        }:
            raise ValueError("personal_mcp_transport_policy_invalid")
        self._transport_policy = transport_policy
        if not 0 < deadline_seconds <= PERSONAL_MCP_DEADLINE_SECONDS:
            raise ValueError("personal_mcp_deadline_invalid")
        self._registry = registry
        self._audit_store = audit_store
        self._clock = clock
        self._monotonic = monotonic
        self._deadline_seconds = deadline_seconds
        self._audit_reserve_seconds = min(0.5, deadline_seconds / 2)
        self._state_lock = Lock()
        self._active_calls = 0
        self._active_workers = 0
        self._call_times: deque[float] = deque()
        self._audit_failed = False
        self._tool_executor = ThreadPoolExecutor(
            max_workers=PERSONAL_MCP_MAX_CONCURRENCY,
            thread_name_prefix="personal-mcp-tool",
        )
        self._audit_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="personal-mcp-audit",
        )
        self._definitions = self._registry.projected_definitions(
            permissions=PERSONAL_MCP_PERMISSIONS,
            names=tuple(PERSONAL_MCP_TOOL_ALLOWLIST),
        )
        if (
            len(self._definitions) != len(PERSONAL_MCP_TOOL_ALLOWLIST)
            or {item.name for item in self._definitions}
            != PERSONAL_MCP_TOOL_ALLOWLIST
        ):
            raise ValueError("personal_mcp_tool_surface_invalid")

    def tool_definitions(self) -> tuple[RuntimeToolDefinition, ...]:
        return deepcopy(self._definitions)

    @property
    def transport_policy(self) -> PersonalMcpTransportPolicy:
        return self._transport_policy

    async def call_tool(
        self, name: str, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        with self._state_lock:
            if self._audit_failed:
                raise PersonalMcpGatewayStopped("capability_audit_unavailable")
        started_at = self._clock()
        started_monotonic = self._monotonic()
        payload: Mapping[str, Any] | None = None
        inflight_acquired = False
        rate_allowed = self._record_rate_attempt(started_monotonic)
        try:
            arguments_sha256 = _arguments_sha256(arguments)
        except Exception:
            arguments_sha256 = sha256(b"invalid_arguments").hexdigest()
            invalid_arguments = True
        else:
            invalid_arguments = False
        if not rate_allowed:
            result = DomainToolResult.unavailable("rate_limited", "mcp_gateway")
        else:
            if not self._reserve_inflight_call():
                result = DomainToolResult.unavailable(
                    "concurrency_limited", "mcp_gateway"
                )
            else:
                inflight_acquired = True
            if inflight_acquired and not _is_allowlisted_name(name):
                result = DomainToolResult.unavailable(
                    "unknown_tool", "not_allowlisted"
                )
            elif inflight_acquired and invalid_arguments:
                result = DomainToolResult.unavailable(
                    "invalid_arguments", "mcp_request"
                )
            elif inflight_acquired:
                if not self._reserve_worker_slot():
                    result = DomainToolResult.unavailable(
                        "concurrency_limited", "mcp_gateway"
                    )
                else:
                    try:
                        result, payload = await self._invoke(
                            name,
                            arguments,
                            started_monotonic=started_monotonic,
                        )
                    except asyncio.CancelledError:
                        audit_task = asyncio.create_task(
                            self._audit_and_return(
                                requested_name=name,
                                arguments_sha256=arguments_sha256,
                                result=DomainToolResult.unavailable(
                                    "tool_cancelled", name
                                ),
                                payload=_result_payload(
                                    DomainToolResult.unavailable(
                                        "tool_cancelled", name
                                    )
                                ),
                                started_at=started_at,
                                started_monotonic=started_monotonic,
                            )
                        )
                        audit_task.add_done_callback(
                            self._finish_cancelled_call
                        )
                        inflight_acquired = False
                        raise

        if payload is None:
            result = self._apply_deadline(started_monotonic, result, name)
            payload = _result_payload(result)
        elif self._remaining(started_monotonic) <= 0:
            result = DomainToolResult.unavailable("tool_deadline_exceeded", name)
            payload = _result_payload(result)
        try:
            return await self._audit_and_return(
                requested_name=name,
                arguments_sha256=arguments_sha256,
                result=result,
                payload=payload,
                started_at=started_at,
                started_monotonic=started_monotonic,
            )
        finally:
            if inflight_acquired:
                self._release_inflight_call()

    async def _invoke(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        started_monotonic: float,
    ) -> tuple[DomainToolResult, Mapping[str, Any]]:
        context = DomainToolContext(
            actor_id=self._actor_id,
            granted_permissions=PERSONAL_MCP_PERMISSIONS,
            purpose=self._transport_policy.purpose,
            clock=self._clock,
        )
        loop = asyncio.get_running_loop()
        try:
            future = loop.run_in_executor(
                self._tool_executor,
                lambda: self._invoke_and_serialize(name, context, arguments),
            )
        except Exception:
            self._release_worker_slot()
            result = DomainToolResult.unavailable("tool_execution_failed", name)
            return result, _result_payload(result)
        future.add_done_callback(lambda _future: self._release_worker_slot())
        try:
            remaining = max(
                0.0,
                self._remaining(started_monotonic) - self._audit_reserve_seconds,
            )
            return await asyncio.wait_for(asyncio.shield(future), timeout=remaining)
        except TimeoutError:
            result = DomainToolResult.unavailable("tool_deadline_exceeded", name)
            return result, _result_payload(result)
        except Exception:
            result = DomainToolResult.unavailable("tool_execution_failed", name)
            return result, _result_payload(result)

    def _invoke_and_serialize(
        self,
        name: str,
        context: DomainToolContext,
        arguments: Mapping[str, Any],
    ) -> tuple[DomainToolResult, Mapping[str, Any]]:
        try:
            result = self._registry.invoke(
                name, context=context, arguments=arguments
            )
        except Exception:
            result = DomainToolResult.unavailable("tool_execution_failed", name)
            return result, _result_payload(result)
        try:
            payload = _result_payload(result)
            if encoded_call_tool_result_size(payload) > PERSONAL_MCP_MAX_OUTPUT_BYTES:
                result = DomainToolResult.unavailable(
                    "tool_result_too_large", name
                )
                payload = _result_payload(result)
            return result, payload
        except Exception:
            result = DomainToolResult.unavailable(
                "tool_serialization_failed", "mcp_gateway"
            )
            return result, _result_payload(result)

    async def _audit_and_return(
        self,
        *,
        requested_name: str,
        arguments_sha256: str,
        result: DomainToolResult,
        payload: Mapping[str, Any],
        started_at: datetime,
        started_monotonic: float,
    ) -> Mapping[str, Any]:
        completed_at = self._clock()
        event = CapabilityAuditEvent(
            request_id=str(uuid4()),
            channel=self._transport_policy.channel,
            canonical_tool=bounded_audit_tool_name(requested_name),
            arguments_sha256=arguments_sha256,
            status=result.status,
            error_code=result.error_code,
            evidence_ids=tuple(item.evidence_id for item in result.evidence),
            field_coverage=result.field_coverage,
            freshness_seconds=result.freshness_seconds,
            cost_usd=result.cost_usd,
            policy_revision=self._transport_policy.policy_revision,
            started_at=started_at,
            completed_at=completed_at,
        )
        context = EvidenceReadContext(
            actor_id=self._actor_id,
            permissions=PERSONAL_MCP_PERMISSIONS,
            purpose=self._transport_policy.purpose,
            now=completed_at,
        )
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            self._audit_executor,
            lambda: self._audit_store.append_audit(context, event),
        )
        try:
            await asyncio.wait_for(
                asyncio.shield(future),
                timeout=max(0.0, self._remaining(started_monotonic) - 0.001),
            )
        except (Exception, asyncio.CancelledError):
            with self._state_lock:
                self._audit_failed = True
            return _result_payload(
                DomainToolResult.unavailable(
                    "capability_audit_unavailable", "mcp_gateway"
                )
            )
        return payload

    def _record_rate_attempt(self, now: float) -> bool:
        with self._state_lock:
            cutoff = now - 60.0
            while self._call_times and self._call_times[0] <= cutoff:
                self._call_times.popleft()
            if len(self._call_times) >= PERSONAL_MCP_MAX_CALLS_PER_MINUTE:
                return False
            self._call_times.append(now)
        return True

    def _reserve_inflight_call(self) -> bool:
        with self._state_lock:
            if self._active_calls >= PERSONAL_MCP_MAX_CONCURRENCY:
                return False
            self._active_calls += 1
        return True

    def _release_inflight_call(self) -> None:
        with self._state_lock:
            if self._active_calls <= 0:
                raise RuntimeError("personal_mcp_inflight_lease_underflow")
            self._active_calls -= 1

    def _finish_cancelled_call(self, task: asyncio.Task[Any]) -> None:
        try:
            if not task.cancelled():
                task.exception()
        finally:
            self._release_inflight_call()

    def _reserve_worker_slot(self) -> bool:
        with self._state_lock:
            if self._active_workers >= PERSONAL_MCP_MAX_CONCURRENCY:
                return False
            self._active_workers += 1
        return True

    def _release_worker_slot(self) -> None:
        with self._state_lock:
            if self._active_workers <= 0:
                raise RuntimeError("personal_mcp_execution_lease_underflow")
            self._active_workers -= 1

    def _remaining(self, started_monotonic: float) -> float:
        return max(
            0.0,
            self._deadline_seconds - (self._monotonic() - started_monotonic),
        )

    def _apply_deadline(
        self,
        started_monotonic: float,
        result: DomainToolResult,
        name: str,
    ) -> DomainToolResult:
        if self._remaining(started_monotonic) <= 0:
            return DomainToolResult.unavailable("tool_deadline_exceeded", name)
        return result

    def close(self) -> None:
        self._tool_executor.shutdown(wait=True, cancel_futures=True)
        self._audit_executor.shutdown(wait=True, cancel_futures=True)


def _arguments_sha256(arguments: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(arguments),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def bounded_audit_tool_name(requested_name: object) -> str:
    if isinstance(requested_name, str) and _is_allowlisted_name(requested_name):
        return requested_name
    bounded_name = (
        requested_name[:128]
        if isinstance(requested_name, str)
        else "invalid-tool-name-type"
    )
    digest = sha256(bounded_name.encode("utf-8", errors="replace")).hexdigest()
    return f"rejected_tool:{digest[:16]}"


def _is_allowlisted_name(requested_name: object) -> bool:
    return (
        isinstance(requested_name, str)
        and len(requested_name) <= 128
        and requested_name in PERSONAL_MCP_TOOL_ALLOWLIST
    )


def _result_payload(result: DomainToolResult) -> Mapping[str, Any]:
    return {
        "status": result.status,
        "data": json_safe_value(dict(result.data)),
        "evidence": [
            {
                "evidence_id": item.evidence_id,
                "source": item.source,
                "as_of": item.as_of.isoformat(),
                "content_sha256": item.content_sha256,
                "authorized_fields": list(item.authorized_fields),
            }
            for item in result.evidence
        ],
        "gaps": [
            {"code": item.code, "subject": item.subject} for item in result.gaps
        ],
        "error_code": result.error_code,
        "field_coverage": (
            str(result.field_coverage)
            if result.field_coverage is not None
            else None
        ),
        "freshness_seconds": result.freshness_seconds,
        "cost_usd": str(result.cost_usd),
    }


def call_tool_result(payload: Mapping[str, Any]):
    from mcp import types

    text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        isError=payload.get("status") == "unavailable",
    )


def encoded_call_tool_result_size(payload: Mapping[str, Any]) -> int:
    result = call_tool_result(payload)
    return len(
        result.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8")
    )
