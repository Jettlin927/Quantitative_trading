"""今日工作台领域工具的稳定合同与 registry。

本模块只冻结调用方可见的领域 interface。真实持仓、新闻与 Web 来源由后续阶段以
handler adapter 接入；来源 SDK 或供应商原始响应不得进入这些类型。
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
import math
import re
from threading import Lock
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    required_permissions: frozenset[str]


@dataclass(frozen=True)
class RuntimeToolDefinition:
    """供 AIRuntime/MCP 出口消费的 provider-neutral 工具定义。"""

    name: str
    description: str
    input_schema: Mapping[str, Any]


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
    purpose: str
    clock: Callable[[], datetime]
    requested_name: str | None = None


@dataclass(frozen=True)
class ToolObservation:
    requested_name: str
    tool_name: str
    status: str
    error_code: str | None
    field_coverage: Decimal | None
    freshness_seconds: int | None
    cost_usd: Decimal
    gap_codes: tuple[str, ...]


class DomainToolMetrics:
    """进程内基础工具观测；只记录聚合口径，不记录私有参数或结果。"""

    def __init__(self, *, maximum_observations: int = 10_000) -> None:
        if maximum_observations <= 0:
            raise ValueError("maximum_observations_must_be_positive")
        self._lock = Lock()
        self._observations: deque[ToolObservation] = deque(
            maxlen=maximum_observations
        )

    def record(self, observation: ToolObservation) -> None:
        with self._lock:
            self._observations.append(observation)

    def snapshot(self) -> Mapping[str, Any]:
        with self._lock:
            observations = tuple(self._observations)
        summary = _observation_summary(observations)
        summary["by_tool"] = {
            tool_name: _observation_summary(
                tuple(
                    item
                    for item in observations
                    if item.tool_name == tool_name
                )
            )
            for tool_name in sorted({item.tool_name for item in observations})
        }
        return summary


def _observation_summary(
    observations: tuple[ToolObservation, ...],
) -> dict[str, Any]:
    calls = len(observations)
    successful = sum(item.status == "success" for item in observations)
    coverage = tuple(
        item.field_coverage
        for item in observations
        if item.field_coverage is not None
    )
    freshness = tuple(
        item.freshness_seconds
        for item in observations
        if item.freshness_seconds is not None
    )
    return {
        "calls": calls,
        "successful": successful,
        "partial": sum(item.status == "partial" for item in observations),
        "stale": sum(item.status == "stale" for item in observations),
        "unavailable": sum(
            item.status == "unavailable" for item in observations
        ),
        "success_rate": (
            Decimal(successful) / Decimal(calls) if calls else Decimal("0")
        ),
        "average_field_coverage": (
            sum(coverage, Decimal("0")) / Decimal(len(coverage))
            if coverage
            else None
        ),
        "maximum_freshness_seconds": max(freshness, default=None),
        "gap_reasons": tuple(
            sorted(
                {
                    gap
                    for item in observations
                    for gap in item.gap_codes
                }
            )
        ),
    }


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

LEGACY_TOOL_DEFINITIONS = (
    ToolDefinition(
        name="get_holdings",
        description=(
            "查询当前真实美股持仓（用户手工维护的私有数据）：返回各持仓的 symbol、名称、数量、"
            "平均成本、币种与状态，以及美元现金余额。无参数。数据仅限当前用户，为时间点快照。"
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        required_permissions=frozenset({"portfolio:read"}),
    ),
    ToolDefinition(
        name="get_kline",
        description=(
            "查询目标美股标的的日 K 线（open/high/low/close + 成交量，按交易日升序）。"
            "参数：symbol（美股代码，必需）、days（回溯自然日，默认 90，范围 10-500）、"
            "limit（返回最近 N 根，默认 120，范围 1-500）。数据为 Alpaca 日线快照。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "days": {"type": "integer", "minimum": 10, "maximum": 500},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["symbol"],
        },
        required_permissions=frozenset({"market:read"}),
    ),
    ToolDefinition(
        name="get_news",
        description=(
            "检索目标标的或产业赛道最近 7 天的产业新闻（investment-news 本地抓取，覆盖全球 100+ 权威源）。"
            "参数：symbol（美股代码，可选）、keyword（关键词，可选）、sector（赛道 key，可选，"
            "取值 ai/semi/robot/auto/energy/bio/space/security/tech/consumer/macro/science）、"
            "limit（返回条数，默认 8，最大 20）。标的→赛道为启发式映射，未收录标的使用关键词检索。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "keyword": {"type": "string"},
                "sector": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": [],
        },
        required_permissions=frozenset({"news:read"}),
    ),
)

_LEGACY_HOLDING_FIELDS = (
    "symbol",
    "name",
    "quantity",
    "average_cost",
    "currency",
    "state",
)
_LEGACY_KLINE_FIELDS = (
    "symbol",
    "adjustment",
    "as_of",
    "source_health",
    "bars",
    "count",
)
_LEGACY_BAR_FIELDS = ("date", "open", "high", "low", "close", "volume")
_LEGACY_NEWS_SAFE_FIELDS = (
    "evidence_id",
    "title",
    "url",
    "published_at",
    "fetched_at",
    "summary",
    "source",
    "source_type",
    "related_symbols",
    "confirmation_state",
)
_RFC3339_DATE_TIME = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)


class DomainToolRegistry:
    def __init__(
        self,
        *,
        handlers: Mapping[str, DomainToolHandler],
        observation_recorder: Callable[[ToolObservation], None] | None = None,
    ) -> None:
        self._definitions = {
            item.name: deepcopy(item) for item in DOMAIN_TOOL_DEFINITIONS
        }
        self._legacy_definitions = {
            item.name: deepcopy(item) for item in LEGACY_TOOL_DEFINITIONS
        }
        unknown_handlers = set(handlers) - set(self._definitions)
        if unknown_handlers:
            raise ValueError("unknown_tool_handler")
        self._handlers = dict(handlers)
        self._record = observation_recorder or (lambda _observation: None)

    def definitions(
        self,
        *,
        permissions: frozenset[str],
        names: tuple[str, ...] = (),
    ) -> tuple[ToolDefinition, ...]:
        """发现调用方有权使用的定义；空 names 只发现 canonical 工具。"""

        catalog = self._definitions | self._legacy_definitions
        requested = self._definitions.values() if not names else (
            catalog.get(name) for name in names
        )
        permitted = {
            item.name: item
            for item in requested
            if item is not None and item.required_permissions <= permissions
        }
        return tuple(
            replace(
                permitted[name],
                input_schema=deepcopy(permitted[name].input_schema),
            )
            for name in sorted(permitted)
        )

    def projected_definitions(
        self,
        *,
        permissions: frozenset[str],
        names: tuple[str, ...] = (),
    ) -> tuple[RuntimeToolDefinition, ...]:
        """投影出口所需字段，不暴露内部权限元数据。"""

        return tuple(
            RuntimeToolDefinition(
                name=item.name,
                description=item.description,
                input_schema=deepcopy(item.input_schema),
            )
            for item in self.definitions(permissions=permissions, names=names)
        )

    def invoke(
        self,
        requested_name: str,
        *,
        context: DomainToolContext,
        arguments: Mapping[str, Any],
    ) -> DomainToolResult:
        canonical_name = LEGACY_TOOL_ALIASES.get(requested_name, requested_name)
        definition = self._definitions.get(canonical_name)
        requested_definition = (
            self._legacy_definitions.get(requested_name)
            if requested_name in LEGACY_TOOL_ALIASES
            else definition
        )
        if definition is None or requested_definition is None:
            return self._finish(
                requested_name,
                canonical_name,
                DomainToolResult.unavailable("unknown_tool", requested_name),
            )
        missing_permissions = (
            requested_definition.required_permissions - context.granted_permissions
        )
        if missing_permissions:
            return self._finish(
                requested_name,
                canonical_name,
                DomainToolResult.unavailable(
                    "source_unauthorized", ",".join(sorted(missing_permissions))
                ),
            )
        if requested_name in LEGACY_TOOL_ALIASES:
            if not isinstance(arguments, Mapping):
                return self._finish(
                    requested_name,
                    canonical_name,
                    DomainToolResult.unavailable(
                        "invalid_arguments", canonical_name
                    ),
                )
            normalized_arguments = _normalize_legacy_arguments(
                requested_name, arguments
            )
        else:
            if not _arguments_match(requested_definition.input_schema, arguments):
                return self._finish(
                    requested_name,
                    canonical_name,
                    DomainToolResult.unavailable(
                        "invalid_arguments", canonical_name
                    ),
                )
            normalized_arguments = dict(arguments)
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
            result = handler(
                replace(context, requested_name=requested_name),
                normalized_arguments,
            )
        except Exception:
            result = DomainToolResult.unavailable("tool_failed", canonical_name)
        if not _valid_result(result):
            result = DomainToolResult.unavailable("tool_contract_invalid", canonical_name)
        elif not _valid_legacy_result(requested_name, result):
            result = DomainToolResult.unavailable("tool_contract_invalid", requested_name)
        result = _normalize_legacy_result(requested_name, result)
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
                gap_codes=tuple(gap.code for gap in result.gaps),
            )
        )
        return result


def _arguments_match(schema: Mapping[str, Any], arguments: Any) -> bool:
    if not isinstance(arguments, Mapping):
        return False
    properties = schema.get("properties", {})
    if set(arguments) - set(properties):
        return False
    if any(name not in arguments for name in schema.get("required", ())):
        return False
    return all(_value_matches(properties[name], value) for name, value in arguments.items())


def _normalize_legacy_arguments(
    requested_name: str, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    if requested_name == "get_holdings":
        return {}
    if requested_name == "get_kline":
        return {
            "symbol": str(arguments.get("symbol") or "").strip().upper(),
            "bar_days": _clamp_legacy_int(arguments.get("days"), 90, 10, 500),
            "bar_limit": _clamp_legacy_int(arguments.get("limit"), 120, 1, 500),
        }
    if requested_name == "get_news":
        normalized: dict[str, Any] = {
            "limit": _clamp_legacy_int(arguments.get("limit"), 8, 1, 20)
        }
        symbol = str(arguments.get("symbol") or "").strip()
        if symbol:
            normalized["symbols"] = [symbol]
        keyword = str(arguments.get("keyword") or "").strip()
        if keyword:
            normalized["query"] = keyword
        sector = str(arguments.get("sector") or "").strip()
        if sector:
            normalized["sector"] = sector
        return normalized
    return dict(arguments)


def _clamp_legacy_int(
    value: Any, default: int, minimum: int, maximum: int
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _normalize_legacy_result(
    requested_name: str, result: DomainToolResult
) -> DomainToolResult:
    if result.status == "unavailable":
        return result
    if requested_name == "get_holdings":
        holdings = [
            _project_fields(item, _LEGACY_HOLDING_FIELDS)
            for item in result.data["holdings"]
        ]
        data = _project_fields(result.data, ("holdings", "count", "usd_cash"))
        data["holdings"] = holdings
        return replace(
            result,
            data=data,
        )
    if requested_name == "get_news":
        data = _project_fields(result.data, ("items", "count"))
        authorized_by_evidence_id = {
            item.evidence_id: frozenset(item.authorized_fields)
            for item in result.evidence
        }
        data["items"] = [
            _project_legacy_news_item(item, authorized_by_evidence_id)
            for item in result.data["items"]
        ]
        data["note"] = (
            "标的→赛道为启发式映射；条目为最近 7 天抓取快照"
            if result.data["items"]
            else "未找到匹配新闻（可换关键词或赛道重试）"
        )
        return replace(
            result,
            data=data,
        )
    if requested_name != "get_kline":
        return result
    market = result.data.get("market")
    if not isinstance(market, Mapping):
        return result
    evidence = tuple(
        item
        for item in result.evidence
        if item.source == "market_dossier" or "bars" in item.authorized_fields
    )
    if not evidence:
        return DomainToolResult.unavailable("tool_contract_invalid", requested_name)
    projected = _project_fields(
        market,
        _LEGACY_KLINE_FIELDS,
    )
    projected["bars"] = [
        _project_fields(item, _LEGACY_BAR_FIELDS)
        for item in market.get("bars", ())
    ]
    return replace(result, data=projected, evidence=evidence)


def _project_fields(
    value: Mapping[str, Any], fields: tuple[str, ...]
) -> dict[str, Any]:
    return {name: value[name] for name in fields if name in value}


def _project_legacy_news_item(
    item: Mapping[str, Any],
    authorized_by_evidence_id: Mapping[str, frozenset[str]],
) -> dict[str, Any]:
    evidence_id = item.get("evidence_id")
    authorized_fields = authorized_by_evidence_id.get(
        evidence_id if isinstance(evidence_id, str) else "", frozenset()
    )
    return {
        name: deepcopy(item[name])
        for name in _LEGACY_NEWS_SAFE_FIELDS
        if name in authorized_fields and name in item
    }


def _valid_legacy_result(
    requested_name: str, result: DomainToolResult
) -> bool:
    if result.status == "unavailable" or requested_name not in LEGACY_TOOL_ALIASES:
        return True
    if requested_name == "get_kline":
        market = result.data.get("market")
        if not isinstance(market, Mapping) or not set(
            _LEGACY_KLINE_FIELDS
        ) <= set(market):
            return False
        bars = market["bars"]
        count = market["count"]
        return (
            isinstance(market["symbol"], str)
            and bool(market["symbol"])
            and isinstance(market["adjustment"], str)
            and market["adjustment"] in {"raw", "provider_adjusted"}
            and (market["as_of"] is None or isinstance(market["as_of"], str))
            and isinstance(market["source_health"], str)
            and isinstance(bars, list)
            and _valid_count(count, bars)
            and all(_valid_legacy_bar(item) for item in bars)
        )
    if requested_name == "get_holdings":
        if not {"holdings", "count", "usd_cash"} <= set(result.data):
            return False
        holdings = result.data["holdings"]
        return (
            isinstance(holdings, list)
            and isinstance(result.data["usd_cash"], str)
            and _valid_count(result.data["count"], holdings)
            and all(_valid_legacy_holding(item) for item in holdings)
        )
    if not {"items", "count"} <= set(result.data):
        return False
    items = result.data["items"]
    evidence_by_id = {item.evidence_id: item for item in result.evidence}
    return (
        isinstance(items, list)
        and _valid_count(result.data["count"], items)
        and all(
            _valid_legacy_news_item(item, evidence_by_id) for item in items
        )
    )


def _valid_count(value: Any, items: list[Any]) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == len(items)


def _valid_legacy_bar(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and set(_LEGACY_BAR_FIELDS) <= set(value)
        and all(isinstance(value[name], str) for name in _LEGACY_BAR_FIELDS[:-1])
        and isinstance(value["volume"], int)
        and not isinstance(value["volume"], bool)
    )


def _valid_legacy_holding(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and set(_LEGACY_HOLDING_FIELDS) <= set(value)
        and all(isinstance(value[name], str) for name in _LEGACY_HOLDING_FIELDS)
    )


def _valid_legacy_news_item(
    value: Any, evidence_by_id: Mapping[str, EvidenceEnvelope]
) -> bool:
    if not isinstance(value, Mapping):
        return False
    evidence_id = value.get("evidence_id")
    if not isinstance(evidence_id, str):
        return False
    envelope = evidence_by_id.get(evidence_id)
    safe_fields = set(_LEGACY_NEWS_SAFE_FIELDS)
    if (
        envelope is None
        or not safe_fields <= set(value)
        or not safe_fields <= set(envelope.authorized_fields)
    ):
        return False
    string_fields = safe_fields - {"related_symbols"}
    related_symbols = value["related_symbols"]
    return (
        all(isinstance(value[name], str) for name in string_fields)
        and isinstance(related_symbols, list)
        and all(isinstance(item, str) for item in related_symbols)
    )


def _value_matches(schema: Mapping[str, Any], value: Any) -> bool:
    expected = schema.get("type")
    if expected == "string":
        if not isinstance(value, str) or len(value) < schema.get("minLength", 0):
            return False
        if schema.get("format") == "date-time":
            return _date_time_matches(value)
        return True
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


def _date_time_matches(value: str) -> bool:
    if _RFC3339_DATE_TIME.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _valid_result(result: Any) -> bool:
    if not isinstance(result, DomainToolResult):
        return False
    if not isinstance(result.data, Mapping):
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
    if not _finite_json_value(result.data):
        return False
    if _contains_provider_envelope(result.data):
        return False
    return all(_valid_evidence(item) for item in result.evidence)


def _finite_json_value(value: Any) -> bool:
    if value is None or isinstance(value, (bool, str, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Decimal):
        return value.is_finite()
    if isinstance(value, Mapping):
        return all(
            isinstance(key, str) and _finite_json_value(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return all(_finite_json_value(item) for item in value)
    return False


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
