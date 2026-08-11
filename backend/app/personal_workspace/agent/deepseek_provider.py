"""DeepSeek agent 适配器：Chat Completions + OpenAI 兼容 function calling。

与单发路径的 DeepSeekChatAdapter 不同，本适配器允许 tools 参数与任意角色消息
（system/user/assistant/tool），响应可能包含 tool_calls；继续强制 model /
stream / thinking / max_tokens 等安全约束，并复用单发路径的传输与记账规范化。
"""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError

from ..analysis import (
    DEEPSEEK_CHAT_URL,
    DEEPSEEK_MAX_OUTPUT_TOKENS,
    DEEPSEEK_MODEL,
    ProviderFailure,
    _deepseek_cost_usd,
    _deepseek_http_transport,
    _normalize_deepseek_usage,
)

AGENT_ALLOWED_ROLES = frozenset({"system", "user", "assistant", "tool"})
AGENT_MAX_TOOLS = 32


class DeepSeekAgentChatAdapter:
    available = True

    def __init__(
        self,
        *,
        api_key: str,
        transport: Callable[..., dict[str, Any]] | None = None,
        timeout_seconds: int = 90,
    ) -> None:
        if not api_key.strip():
            raise ValueError("provider_unavailable")
        self._api_key = api_key.strip()
        self._transport = transport or _deepseek_http_transport
        self._timeout_seconds = timeout_seconds

    def __repr__(self) -> str:
        return "DeepSeekAgentChatAdapter(api_key=<redacted>)"

    def create_response(self, request: dict[str, Any]) -> dict[str, Any]:
        body = {key: value for key, value in request.items() if key != "url"}
        self.validate_request(body)
        try:
            raw = self._transport(
                url=DEEPSEEK_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                body=body,
                timeout_seconds=self._timeout_seconds,
            )
        except HTTPError as exc:
            if exc.code in {401, 403}:
                code, retryable = "provider_auth_failed", False
            elif exc.code == 402:
                code, retryable = "provider_balance_unavailable", False
            elif exc.code == 404:
                code, retryable = "provider_model_unavailable", False
            elif exc.code == 429:
                code, retryable = "provider_rate_limited", True
            elif exc.code >= 500:
                code, retryable = "provider_upstream_error", True
            elif exc.code in {400, 422}:
                code, retryable = "provider_request_invalid", False
            else:
                code, retryable = "provider_http_error", False
            raise ProviderFailure(code, retryable=retryable) from None
        except (TimeoutError, URLError):
            raise ProviderFailure("provider_timeout", retryable=False) from None
        return _normalize_agent_response(raw)

    def validate_request(self, body: dict[str, Any]) -> None:
        if body.get("model") != DEEPSEEK_MODEL:
            raise ProviderFailure("provider_request_unsafe", retryable=False)
        if body.get("stream") is not False:
            raise ProviderFailure("provider_request_unsafe", retryable=False)
        if body.get("thinking") != {"type": "disabled"}:
            raise ProviderFailure("provider_request_unsafe", retryable=False)
        if body.get("max_tokens") != DEEPSEEK_MAX_OUTPUT_TOKENS:
            raise ProviderFailure("provider_request_unsafe", retryable=False)
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ProviderFailure("provider_request_unsafe", retryable=False)
        if any(
            not isinstance(message, dict)
            or message.get("role") not in AGENT_ALLOWED_ROLES
            for message in messages
        ):
            raise ProviderFailure("provider_request_unsafe", retryable=False)
        tools = body.get("tools")
        if tools is not None:
            if not isinstance(tools, list) or not 1 <= len(tools) <= AGENT_MAX_TOOLS:
                raise ProviderFailure("provider_request_unsafe", retryable=False)
            for tool in tools:
                function = (tool or {}).get("function") or {}
                if (
                    not isinstance(tool, dict)
                    or tool.get("type") != "function"
                    or not isinstance(function, dict)
                    or not isinstance(function.get("name"), str)
                    or not function["name"].strip()
                ):
                    raise ProviderFailure(
                        "provider_request_unsafe", retryable=False
                    )


def _normalize_agent_response(raw: dict[str, Any]) -> dict[str, Any]:
    choices = raw.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ProviderFailure("provider_response_envelope_invalid", retryable=False)
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ProviderFailure("provider_response_envelope_invalid", retryable=False)
    finish_reason = choice.get("finish_reason")
    if finish_reason == "length":
        raise ProviderFailure("provider_output_truncated", retryable=False)
    if finish_reason == "insufficient_system_resource":
        raise ProviderFailure("provider_unavailable", retryable=True)
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ProviderFailure("provider_response_envelope_invalid", retryable=False)
    if finish_reason == "content_filter" or message.get("refusal"):
        refusal = {
            "status": "refusal",
            "message": {"content": None, "tool_calls": ()},
        }
        try:
            usage = _normalize_deepseek_usage(raw.get("usage"))
        except ProviderFailure:
            return refusal
        return {
            **refusal,
            "usage": usage,
            "cost_usd": _deepseek_cost_usd(usage),
        }
    tool_calls = _normalize_tool_calls(message.get("tool_calls"))
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise ProviderFailure("provider_response_envelope_invalid", retryable=False)
    if tool_calls:
        usage = _normalize_deepseek_usage(raw.get("usage"))
        return {
            "status": "completed",
            "message": {"content": content, "tool_calls": tool_calls},
            "usage": usage,
            "cost_usd": _deepseek_cost_usd(usage),
        }
    if finish_reason != "stop" or not content or not content.strip():
        raise ProviderFailure("provider_empty_response", retryable=False)
    usage = _normalize_deepseek_usage(raw.get("usage"))
    return {
        "status": "completed",
        "message": {"content": content, "tool_calls": ()},
        "usage": usage,
        "cost_usd": _deepseek_cost_usd(usage),
    }


def _normalize_tool_calls(
    raw: Any,
) -> tuple[dict[str, Any], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw:
        raise ProviderFailure("provider_tool_calls_invalid", retryable=False)
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ProviderFailure("provider_tool_calls_invalid", retryable=False)
        call_id = item.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            raise ProviderFailure("provider_tool_calls_invalid", retryable=False)
        function = item.get("function")
        if not isinstance(function, dict):
            raise ProviderFailure("provider_tool_calls_invalid", retryable=False)
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ProviderFailure("provider_tool_calls_invalid", retryable=False)
        arguments_raw = function.get("arguments")
        if not isinstance(arguments_raw, str):
            raise ProviderFailure("provider_tool_arguments_invalid", retryable=False)
        try:
            arguments = json.loads(arguments_raw)
        except json.JSONDecodeError:
            raise ProviderFailure(
                "provider_tool_arguments_invalid", retryable=False
            ) from None
        if not isinstance(arguments, dict):
            raise ProviderFailure("provider_tool_arguments_invalid", retryable=False)
        normalized.append(
            {
                "id": call_id.strip(),
                "name": name.strip(),
                "arguments": arguments,
            }
        )
    return tuple(normalized)
