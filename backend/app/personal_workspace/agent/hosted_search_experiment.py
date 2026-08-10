"""隔离的 DeepSeek Anthropic Hosted Web Search 合同实验。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import re
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from .ai_runtime import (
    AIRuntimeCapabilities,
    RuntimeEvent,
    RuntimeCitation,
    RuntimeEvidence,
    RuntimeRequest,
    RuntimeResult,
    RuntimeUsage,
)


EXPERIMENT_REVISION = "hosted-search-experiment-v1"
ANTHROPIC_MESSAGES_URL = "https://api.deepseek.com/anthropic/v1/messages"
PUBLIC_EXPERIMENT_INPUT = "核验 NVIDIA 最近一份 SEC 年报的公开来源。"
PUBLIC_EXPERIMENT_INSTRUCTIONS = "仅报告已验证的公开来源。"
DEEPSEEK_MODEL = "deepseek-v4-flash"
WEB_SEARCH_TOOL_TYPE = "web_search_20250305"
MAX_OUTPUT_TOKENS = 512
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class HostedSearchExperimentConfig:
    revision: str
    max_tool_calls: int
    max_queries: int
    cache_hit_input_usd_per_million: Decimal = Decimal("0.0028")
    cache_miss_input_usd_per_million: Decimal = Decimal("0.14")
    output_usd_per_million: Decimal = Decimal("0.28")
    hosted_search_usd_per_request: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if (
            self.revision != EXPERIMENT_REVISION
            or not _valid_limit(self.max_tool_calls, maximum=5)
            or not _valid_limit(self.max_queries, maximum=10)
            or not all(
                isinstance(value, Decimal)
                and value.is_finite()
                and value >= 0
                for value in (
                    self.cache_hit_input_usd_per_million,
                    self.cache_miss_input_usd_per_million,
                    self.output_usd_per_million,
                    self.hosted_search_usd_per_request,
                )
            )
        ):
            raise ValueError("hosted_search_limits_invalid")


@dataclass(frozen=True)
class VerifiedWebEvidence:
    evidence_id: str
    source_url: str
    title: str
    excerpt: str
    content_sha256: str
    verified_at: datetime


class WebEvidenceVerifier(Protocol):
    def verify(
        self, *, url: str, title: str, provider_excerpt: str
    ) -> VerifiedWebEvidence: ...


class HostedSearchFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


Transport = Callable[..., dict[str, Any]]


class DeepSeekHostedSearchExperiment:
    """只接受固定公开问题的合成 Hosted Search runtime。"""

    capabilities = AIRuntimeCapabilities(
        runtime_kind="hosted_tool",
        client_tools=False,
        hosted_tools=True,
        cancellation=False,
        usage=True,
    )

    def __init__(
        self,
        *,
        config: HostedSearchExperimentConfig,
        verifier: WebEvidenceVerifier,
        transport: Transport | None = None,
        timeout_seconds: int = 90,
    ) -> None:
        self._config = config
        self._verifier = verifier
        self._transport = transport or _network_disabled
        self._timeout_seconds = timeout_seconds

    def run(self, request: RuntimeRequest) -> RuntimeResult:
        self._validate_request(request)
        raw = self._send(
            {
                "model": DEEPSEEK_MODEL,
                "max_tokens": MAX_OUTPUT_TOKENS,
                "system": PUBLIC_EXPERIMENT_INSTRUCTIONS,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": PUBLIC_EXPERIMENT_INPUT}
                        ],
                    }
                ],
                "tools": [
                    {
                        "type": WEB_SEARCH_TOOL_TYPE,
                        "name": "web_search",
                        "max_uses": self._config.max_tool_calls,
                        "allowed_domains": ["www.sec.gov"],
                    }
                ],
                "stream": False,
            }
        )
        return self._normalize(raw)

    def _validate_request(self, request: RuntimeRequest) -> None:
        if (
            request.model != DEEPSEEK_MODEL
            or request.instructions != PUBLIC_EXPERIMENT_INSTRUCTIONS
            or request.input_text != PUBLIC_EXPERIMENT_INPUT
            or request.tools
            or request.hosted_tools != ("web_search",)
        ):
            raise HostedSearchFailure("provider_request_invalid")

    def _send(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            raw = self._transport(
                url=ANTHROPIC_MESSAGES_URL,
                headers={
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                },
                body=body,
                timeout_seconds=self._timeout_seconds,
            )
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise HostedSearchFailure("provider_auth_failed") from None
            if exc.code == 402:
                raise HostedSearchFailure("provider_balance_unavailable") from None
            if exc.code == 429:
                raise HostedSearchFailure(
                    "provider_rate_limited", retryable=True
                ) from None
            if exc.code >= 500:
                raise HostedSearchFailure(
                    "provider_upstream_error", retryable=True
                ) from None
            raise HostedSearchFailure("provider_request_invalid") from None
        except (TimeoutError, URLError):
            raise HostedSearchFailure("provider_timeout") from None
        if not isinstance(raw, dict):
            raise HostedSearchFailure("provider_response_envelope_invalid")
        return raw

    def _normalize(self, raw: dict[str, Any]) -> RuntimeResult:
        if (
            raw.get("type") != "message"
            or raw.get("role") != "assistant"
            or raw.get("model") != DEEPSEEK_MODEL
        ):
            raise HostedSearchFailure("provider_response_envelope_invalid")
        stop_reason = raw.get("stop_reason")
        if stop_reason == "refusal":
            raise HostedSearchFailure("provider_refusal")
        if stop_reason != "end_turn":
            raise HostedSearchFailure("provider_invalid_status")
        blocks = raw.get("content")
        if not isinstance(blocks, list) or not blocks:
            raise HostedSearchFailure("provider_response_envelope_invalid")

        calls: dict[str, dict[str, Any]] = {}
        result_blocks: dict[str, list[dict[str, Any]]] = {}
        text_blocks: list[str] = []
        raw_citations: list[tuple[int, dict[str, Any]]] = []
        for block in blocks:
            if not isinstance(block, dict):
                raise HostedSearchFailure("provider_response_envelope_invalid")
            block_type = block.get("type")
            if block_type == "server_tool_use":
                call_id = _text(block.get("id"))
                tool_input = block.get("input")
                arguments = _search_arguments(tool_input)
                if block.get("name") != "web_search" or call_id in calls:
                    raise HostedSearchFailure("provider_response_envelope_invalid")
                calls[call_id] = arguments
            elif block_type == "web_search_tool_result":
                call_id = _text(block.get("tool_use_id"))
                content = block.get("content")
                if call_id in result_blocks or not isinstance(content, list) or not content:
                    raise HostedSearchFailure("provider_response_envelope_invalid")
                if any(not isinstance(item, dict) for item in content):
                    raise HostedSearchFailure("provider_response_envelope_invalid")
                result_blocks[call_id] = content
            elif block_type == "text":
                text_blocks.append(_text(block.get("text")))
                output_block_index = len(text_blocks) - 1
                citations = block.get("citations")
                if citations is None:
                    continue
                if not isinstance(citations, list) or any(
                    not isinstance(item, dict) for item in citations
                ):
                    raise HostedSearchFailure("provider_response_envelope_invalid")
                raw_citations.extend(
                    (output_block_index, citation) for citation in citations
                )
            else:
                raise HostedSearchFailure("provider_response_envelope_invalid")

        if (
            not calls
            or set(calls) != set(result_blocks)
            or len(calls) > self._config.max_tool_calls
            or sum(_query_count(item) for item in calls.values())
            > self._config.max_queries
            or not text_blocks
            or not raw_citations
        ):
            raise HostedSearchFailure("provider_response_envelope_invalid")
        results_by_url: dict[str, tuple[str, str]] = {}
        for call_id, items in result_blocks.items():
            for item in items:
                if item.get("type") != "web_search_result":
                    raise HostedSearchFailure("provider_response_envelope_invalid")
                title = _text(item.get("title"))
                url = _text(item.get("url"))
                if not _allowed_source_url(url) or url in results_by_url:
                    raise HostedSearchFailure("provider_response_envelope_invalid")
                results_by_url[url] = (title, call_id)

        cited_texts_by_url: dict[str, list[str]] = {}
        citation_refs: list[tuple[str, int]] = []
        for output_block_index, citation in raw_citations:
            if citation.get("type") != "web_search_result_location":
                raise HostedSearchFailure("provider_response_envelope_invalid")
            url = _text(citation.get("url"))
            title = _text(citation.get("title"))
            cited_text = _text(citation.get("cited_text"))
            result = results_by_url.get(url)
            if result is None or title != result[0]:
                raise HostedSearchFailure("provider_response_envelope_invalid")
            cited_texts_by_url.setdefault(url, []).append(cited_text)
            citation_refs.append((url, output_block_index))

        evidence_by_url: dict[str, VerifiedWebEvidence] = {}
        evidence_by_call: dict[str, list[str]] = {call_id: [] for call_id in calls}
        for url, cited_texts in cited_texts_by_url.items():
            title, call_id = results_by_url[url]
            provider_excerpt = "\n".join(dict.fromkeys(cited_texts))
            try:
                verified = self._verifier.verify(
                    url=url, title=title, provider_excerpt=provider_excerpt
                )
            except Exception:
                raise HostedSearchFailure(
                    "provider_response_envelope_invalid"
                ) from None
            _validate_evidence(verified, expected_url=url)
            if any(item not in verified.excerpt for item in cited_texts):
                raise HostedSearchFailure("provider_response_envelope_invalid")
            evidence_by_url[url] = verified
            evidence_by_call[call_id].append(verified.evidence_id)
        if any(not evidence_ids for evidence_ids in evidence_by_call.values()):
            raise HostedSearchFailure("provider_response_envelope_invalid")

        citations: list[RuntimeCitation] = []
        for url, output_block_index in citation_refs:
            evidence = evidence_by_url[url]
            output_text = text_blocks[output_block_index]
            citations.append(
                RuntimeCitation(
                    evidence_id=evidence.evidence_id,
                    output_block_index=output_block_index,
                    cited_text_sha256=sha256(
                        output_text.encode("utf-8")
                    ).hexdigest(),
                )
            )

        events: list[RuntimeEvent] = [RuntimeEvent(type="run_started")]
        for call_id, arguments in calls.items():
            events.extend(
                (
                    RuntimeEvent(
                        type="hosted_tool_started",
                        tool_name="web_search",
                        tool_call_id=call_id,
                        arguments=arguments,
                    ),
                    RuntimeEvent(
                        type="hosted_tool_completed",
                        tool_name="web_search",
                        tool_call_id=call_id,
                        evidence_ids=tuple(evidence_by_call[call_id]),
                    ),
                )
            )
        events.extend(
            RuntimeEvent(type="output_completed", text=text)
            for text in text_blocks
        )
        return RuntimeResult.completed(
            events=tuple(events),
            usage=_usage(
                raw.get("usage"),
                observed_tool_calls=len(calls),
                web_search_queries=sum(
                    _query_count(item) for item in calls.values()
                ),
                config=self._config,
            ),
            evidence=tuple(
                RuntimeEvidence(
                    evidence_id=item.evidence_id,
                    url=item.source_url,
                    title=item.title,
                    verified_excerpt=item.excerpt,
                    body_sha256=item.content_sha256,
                    verified_at=item.verified_at,
                )
                for item in evidence_by_url.values()
            ),
            citations=tuple(citations),
        )


def _network_disabled(**_: Any) -> dict[str, Any]:
    raise HostedSearchFailure("provider_unavailable")


def _valid_limit(value: Any, *, maximum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 1 <= value <= maximum
    )


def _allowed_source_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "www.sec.gov"
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HostedSearchFailure("provider_response_envelope_invalid")
    return value.strip()


def _search_arguments(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HostedSearchFailure("provider_response_envelope_invalid")
    query = value.get("query")
    queries = value.get("queries")
    if isinstance(query, str) and query.strip() and queries is None:
        return {"query": query.strip()}
    if (
        query is None
        and isinstance(queries, list)
        and queries
        and all(isinstance(item, str) and item.strip() for item in queries)
    ):
        return {"queries": [item.strip() for item in queries]}
    raise HostedSearchFailure("provider_response_envelope_invalid")


def _query_count(arguments: dict[str, Any]) -> int:
    queries = arguments.get("queries")
    return len(queries) if isinstance(queries, list) else 1


def _validate_evidence(evidence: Any, *, expected_url: str) -> None:
    if (
        not isinstance(evidence, VerifiedWebEvidence)
        or not _allowed_source_url(expected_url)
        or not _allowed_source_url(evidence.source_url)
        or not evidence.evidence_id.strip()
        or not evidence.title.strip()
        or not evidence.excerpt.strip()
        or _SHA256.fullmatch(evidence.content_sha256) is None
        or evidence.verified_at.tzinfo is None
        or evidence.verified_at.utcoffset() is None
    ):
        raise HostedSearchFailure("provider_response_envelope_invalid")


def _usage(
    raw: Any,
    *,
    observed_tool_calls: int,
    web_search_queries: int,
    config: HostedSearchExperimentConfig,
) -> RuntimeUsage:
    if not isinstance(raw, dict):
        raise HostedSearchFailure("provider_usage_invalid")
    input_tokens = _token_count(raw.get("input_tokens"))
    output_tokens = _token_count(raw.get("output_tokens"))
    cache_hit_tokens = _token_count(raw.get("cache_read_input_tokens", 0))
    cache_creation_tokens = _token_count(raw.get("cache_creation_input_tokens", 0))
    server_tool_use = raw.get("server_tool_use")
    if not isinstance(server_tool_use, dict):
        raise HostedSearchFailure("provider_usage_invalid")
    hosted_tool_calls = _token_count(server_tool_use.get("web_search_requests"))
    if hosted_tool_calls != observed_tool_calls:
        raise HostedSearchFailure("provider_usage_invalid")
    cache_miss_tokens = input_tokens + cache_creation_tokens
    total_input_tokens = cache_miss_tokens + cache_hit_tokens
    million = Decimal("1000000")
    token_cost = (
        Decimal(cache_hit_tokens)
        * config.cache_hit_input_usd_per_million
        + Decimal(cache_miss_tokens)
        * config.cache_miss_input_usd_per_million
        + Decimal(output_tokens) * config.output_usd_per_million
    ) / million
    hosted_cost = (
        Decimal(hosted_tool_calls)
        * config.hosted_search_usd_per_request
    )
    return RuntimeUsage(
        input_tokens=total_input_tokens,
        output_tokens=output_tokens,
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
        cost_usd=token_cost + hosted_cost,
        hosted_tool_calls=hosted_tool_calls,
        web_search_queries=web_search_queries,
        hosted_cost_usd=hosted_cost,
    )


def _token_count(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise HostedSearchFailure("provider_usage_invalid")
    return value
