"""今日工作台领域工具的稳定合同与 registry。

本模块只冻结调用方可见的领域 interface。真实持仓、新闻与 Web 来源由后续阶段以
handler adapter 接入；来源 SDK 或供应商原始响应不得进入这些类型。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    required_permissions: frozenset[str]


@dataclass(frozen=True)
class EvidenceEnvelope:
    evidence_id: str
    source: str
    as_of: datetime
    content_sha256: str
    authorized_fields: tuple[str, ...]


@dataclass(frozen=True)
class ToolGap:
    code: str
    subject: str


@dataclass(frozen=True)
class DomainToolResult:
    status: str
    data: Mapping[str, Any]
    evidence: tuple[EvidenceEnvelope, ...] = ()
    gaps: tuple[ToolGap, ...] = ()
    error_code: str | None = None
    field_coverage: Decimal | None = None
    freshness_seconds: int | None = None
    cost_usd: Decimal = Decimal("0")

    @classmethod
    def success(
        cls,
        *,
        data: Mapping[str, Any],
        evidence: tuple[EvidenceEnvelope, ...] = (),
        field_coverage: Decimal | None = None,
        freshness_seconds: int | None = None,
        cost_usd: Decimal = Decimal("0"),
    ) -> "DomainToolResult":
        return cls(
            status="success",
            data=data,
            evidence=evidence,
            field_coverage=field_coverage,
            freshness_seconds=freshness_seconds,
            cost_usd=cost_usd,
        )

    @classmethod
    def partial(
        cls,
        *,
        data: Mapping[str, Any],
        gaps: tuple[ToolGap, ...],
        evidence: tuple[EvidenceEnvelope, ...] = (),
        field_coverage: Decimal | None = None,
        freshness_seconds: int | None = None,
        cost_usd: Decimal = Decimal("0"),
    ) -> "DomainToolResult":
        return cls(
            status="partial",
            data=data,
            evidence=evidence,
            gaps=gaps,
            field_coverage=field_coverage,
            freshness_seconds=freshness_seconds,
            cost_usd=cost_usd,
        )

    @classmethod
    def stale(
        cls,
        *,
        data: Mapping[str, Any],
        gaps: tuple[ToolGap, ...],
        evidence: tuple[EvidenceEnvelope, ...] = (),
        field_coverage: Decimal | None = None,
        freshness_seconds: int | None = None,
        cost_usd: Decimal = Decimal("0"),
    ) -> "DomainToolResult":
        return cls(
            status="stale",
            data=data,
            evidence=evidence,
            gaps=gaps,
            field_coverage=field_coverage,
            freshness_seconds=freshness_seconds,
            cost_usd=cost_usd,
        )

    @classmethod
    def unavailable(cls, code: str, subject: str) -> "DomainToolResult":
        return cls(
            status="unavailable",
            data={},
            gaps=(ToolGap(code, subject),),
            error_code=code,
        )


@dataclass(frozen=True)
class DomainToolContext:
    actor_id: str
    granted_permissions: frozenset[str]
    clock: Callable[[], datetime]


@dataclass(frozen=True)
class ToolObservation:
    requested_name: str
    tool_name: str
    status: str
    error_code: str | None
    field_coverage: Decimal | None
    freshness_seconds: int | None
    cost_usd: Decimal


DomainToolHandler = Callable[[DomainToolContext, dict[str, Any]], DomainToolResult]


def _object_schema(
    properties: Mapping[str, Any], required: tuple[str, ...] = ()
) -> Mapping[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


DOMAIN_TOOL_DEFINITIONS = (
    ToolDefinition(
        name="get_today_context",
        description="读取当前时段、组合覆盖、注意事项和证据缺口，不生成交易动作。",
        input_schema=_object_schema(
            {"as_of": {"type": "string", "format": "date-time"}}
        ),
        required_permissions=frozenset({"portfolio:read", "market:read"}),
    ),
    ToolDefinition(
        name="get_symbol_dossier",
        description="读取获准美股标的的身份、持仓或自选状态及事实摘要。",
        input_schema=_object_schema(
            {
                "symbol": {"type": "string", "minLength": 1},
                "bar_days": {"type": "integer", "minimum": 1, "maximum": 3650},
                "bar_limit": {"type": "integer", "minimum": 1, "maximum": 1000},
            },
            ("symbol",),
        ),
        required_permissions=frozenset({"portfolio:read", "market:read"}),
    ),
    ToolDefinition(
        name="search_market_news",
        description="查询结构化、去重且带来源时点的事实新闻。",
        input_schema=_object_schema(
            {
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "query": {"type": "string"},
                "sector": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            }
        ),
        required_permissions=frozenset({"news:read"}),
    ),
    ToolDefinition(
        name="search_web_evidence",
        description="补充长尾事实线索；结果仍须经服务端来源验证后才能成为证据。",
        input_schema=_object_schema(
            {
                "query": {"type": "string", "minLength": 1},
                "subject_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            ("query",),
        ),
        required_permissions=frozenset({"web_evidence:read"}),
    ),
    ToolDefinition(
        name="discover_related_candidates",
        description="以关系证据和近期事实证据发现候选，不按预测收益率排序。",
        input_schema=_object_schema(
            {
                "subject_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                }
            },
            ("subject_ids",),
        ),
        required_permissions=frozenset({"market:read", "news:read"}),
    ),
    ToolDefinition(
        name="get_evidence",
        description="按证据身份读取标准化摘要、元数据和授权范围。",
        input_schema=_object_schema(
            {"evidence_id": {"type": "string", "minLength": 1}},
            ("evidence_id",),
        ),
        required_permissions=frozenset({"evidence:read"}),
    ),
)


LEGACY_TOOL_ALIASES = {
    "get_holdings": "get_today_context",
    "get_kline": "get_symbol_dossier",
    "get_news": "search_market_news",
}

_LEGACY_REQUIRED_PERMISSIONS = {
    "get_holdings": frozenset({"portfolio:read"}),
    "get_kline": frozenset({"market:read"}),
    "get_news": frozenset({"news:read"}),
}


class DomainToolRegistry:
    def __init__(
        self,
        *,
        handlers: Mapping[str, DomainToolHandler],
        observation_recorder: Callable[[ToolObservation], None] | None = None,
    ) -> None:
        self._definitions = {item.name: item for item in DOMAIN_TOOL_DEFINITIONS}
        unknown_handlers = set(handlers) - set(self._definitions)
        if unknown_handlers:
            raise ValueError("unknown_tool_handler")
        self._handlers = dict(handlers)
        self._record = observation_recorder or (lambda _observation: None)

    def invoke(
        self,
        requested_name: str,
        *,
        context: DomainToolContext,
        arguments: dict[str, Any],
    ) -> DomainToolResult:
        canonical_name = LEGACY_TOOL_ALIASES.get(requested_name, requested_name)
        definition = self._definitions.get(canonical_name)
        if definition is None:
            return self._finish(
                requested_name,
                canonical_name,
                DomainToolResult.unavailable("unknown_tool", requested_name),
            )
        required_permissions = _LEGACY_REQUIRED_PERMISSIONS.get(
            requested_name, definition.required_permissions
        )
        missing_permissions = required_permissions - context.granted_permissions
        if missing_permissions:
            return self._finish(
                requested_name,
                canonical_name,
                DomainToolResult.unavailable(
                    "source_unauthorized", ",".join(sorted(missing_permissions))
                ),
            )
        normalized_arguments = _normalize_legacy_arguments(requested_name, arguments)
        if not _arguments_match(definition.input_schema, normalized_arguments):
            return self._finish(
                requested_name,
                canonical_name,
                DomainToolResult.unavailable("invalid_arguments", canonical_name),
            )
        handler = self._handlers.get(canonical_name)
        if handler is None:
            return self._finish(
                requested_name,
                canonical_name,
                DomainToolResult.unavailable("tool_unavailable", canonical_name),
            )
        try:
            result = handler(context, normalized_arguments)
        except Exception:
            result = DomainToolResult.unavailable("tool_failed", canonical_name)
        if not _valid_result(result):
            result = DomainToolResult.unavailable("tool_contract_invalid", canonical_name)
        return self._finish(requested_name, canonical_name, result)

    def _finish(
        self, requested_name: str, canonical_name: str, result: DomainToolResult
    ) -> DomainToolResult:
        self._record(
            ToolObservation(
                requested_name=requested_name,
                tool_name=canonical_name,
                status=result.status,
                error_code=result.error_code,
                field_coverage=result.field_coverage,
                freshness_seconds=result.freshness_seconds,
                cost_usd=result.cost_usd,
            )
        )
        return result


def _arguments_match(schema: Mapping[str, Any], arguments: Any) -> bool:
    if not isinstance(arguments, dict):
        return False
    properties = schema.get("properties", {})
    if set(arguments) - set(properties):
        return False
    if any(name not in arguments for name in schema.get("required", ())):
        return False
    return all(_value_matches(properties[name], value) for name, value in arguments.items())


def _normalize_legacy_arguments(
    requested_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    if requested_name == "get_kline":
        mapping = {"days": "bar_days", "limit": "bar_limit"}
        return {mapping.get(name, name): value for name, value in arguments.items()}
    if requested_name == "get_news":
        normalized = dict(arguments)
        symbol = normalized.pop("symbol", None)
        if symbol:
            normalized["symbols"] = [symbol]
        keyword = normalized.pop("keyword", None)
        if keyword:
            normalized["query"] = keyword
        return normalized
    return dict(arguments)


def _value_matches(schema: Mapping[str, Any], value: Any) -> bool:
    expected = schema.get("type")
    if expected == "string":
        return isinstance(value, str) and len(value) >= schema.get("minLength", 0)
    if expected == "integer":
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value >= schema.get("minimum", value)
            and value <= schema.get("maximum", value)
        )
    if expected == "array":
        return (
            isinstance(value, list)
            and len(value) >= schema.get("minItems", 0)
            and all(_value_matches(schema.get("items", {}), item) for item in value)
        )
    return False


def _valid_result(result: Any) -> bool:
    if not isinstance(result, DomainToolResult):
        return False
    if result.status not in {"success", "partial", "stale", "unavailable"}:
        return False
    if result.status in {"partial", "stale", "unavailable"} and not result.gaps:
        return False
    if result.status == "unavailable" and not result.error_code:
        return False
    if result.status != "unavailable" and not result.evidence:
        return False
    if result.field_coverage is not None and not Decimal("0") <= result.field_coverage <= Decimal("1"):
        return False
    if result.freshness_seconds is not None and result.freshness_seconds < 0:
        return False
    if result.cost_usd < Decimal("0"):
        return False
    if _contains_provider_envelope(result.data):
        return False
    return all(_valid_evidence(item) for item in result.evidence)


def _contains_provider_envelope(value: Any) -> bool:
    forbidden = {
        "choices",
        "provider_raw_response",
        "provider_response",
        "raw_response",
    }
    if isinstance(value, Mapping):
        return bool(set(value) & forbidden) or any(
            _contains_provider_envelope(item) for item in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_provider_envelope(item) for item in value)
    return False


def _valid_evidence(evidence: EvidenceEnvelope) -> bool:
    return (
        bool(evidence.evidence_id)
        and bool(evidence.source)
        and evidence.as_of.tzinfo is not None
        and len(evidence.content_sha256) == 64
        and all(character in "0123456789abcdef" for character in evidence.content_sha256)
        and bool(evidence.authorized_fields)
    )
