"""DeepSeek Chat Completions 的 provider-neutral AIRuntime adapter。"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from typing import Any, Callable

from ..analysis import (
    DEEPSEEK_MAX_OUTPUT_TOKENS,
    DEEPSEEK_MODEL,
    ProviderFailure,
)
from .ai_runtime import (
    AIRuntimeCapabilities,
    RuntimeEvent,
    RuntimeExecutionContext,
    RuntimeRequest,
    RuntimeResult,
    RuntimeUsage,
)
from .client_tool_runtime import ClientToolRuntime, _runtime_usage, maximum_cost_usd
from .deepseek_provider import DeepSeekAgentChatAdapter


class DeepSeekCompletionRuntime:
    """把一次固定 DeepSeek completion 收敛为稳定 RuntimeResult。"""

    capabilities = AIRuntimeCapabilities(
        runtime_kind="completion",
        client_tools=True,
        hosted_tools=False,
        cancellation=True,
        usage=True,
    )

    def __init__(
        self,
        *,
        api_key: str | None = None,
        provider: Any | None = None,
        transport: Callable[..., dict[str, Any]] | None = None,
        timeout_seconds: int = 90,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if provider is not None:
            if api_key is not None or transport is not None:
                raise ValueError("completion_runtime_config_invalid")
            self._provider = provider
        else:
            if api_key is None:
                raise ValueError("completion_runtime_config_invalid")
            self._provider = DeepSeekAgentChatAdapter(
                api_key=api_key,
                transport=transport,
                timeout_seconds=timeout_seconds,
            )
        self._client_tools = ClientToolRuntime(provider=self._provider, clock=clock)

    @property
    def available(self) -> bool:
        return bool(getattr(self._provider, "available", True))

    def __repr__(self) -> str:
        return "DeepSeekCompletionRuntime(api_key=<redacted>)"

    def run(
        self,
        request: RuntimeRequest,
        context: RuntimeExecutionContext | None = None,
    ) -> RuntimeResult:
        if request.model != DEEPSEEK_MODEL:
            raise ProviderFailure("provider_request_invalid", retryable=False)
        if request.hosted_tools:
            raise ProviderFailure("provider_request_invalid", retryable=False)
        if request.tools:
            if context is None:
                return RuntimeResult.failed("runtime_contract_invalid")
            return self._client_tools.run(request, context)

        response = self._provider.create_response(
            {
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": request.instructions},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "input": request.input_text,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    },
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": DEEPSEEK_MAX_OUTPUT_TOKENS,
                "stream": False,
                "thinking": {"type": "disabled"},
            }
        )
        if response["status"] == "refusal":
            try:
                usage = _runtime_usage(response)
            except (KeyError, TypeError, ValueError, ProviderFailure):
                return RuntimeResult.failed(
                    "provider_refusal", outcome_unknown=True
                )
            return RuntimeResult.failed(
                "provider_refusal",
                events=(
                    RuntimeEvent(type="run_started"),
                    RuntimeEvent(type="run_failed"),
                ),
                usage=usage,
            )

        message = response["message"]
        if message["tool_calls"]:
            raise ProviderFailure("provider_response_envelope_invalid", retryable=False)
        content = message["content"]
        if not isinstance(content, str) or not content.strip():
            raise ProviderFailure("provider_response_envelope_invalid", retryable=False)

        usage = _runtime_usage(response)
        return RuntimeResult.completed(
            events=(
                RuntimeEvent(type="run_started"),
                RuntimeEvent(type="output_completed", text=content),
            ),
            usage=usage,
        )

    def maximum_cost_usd(
        self, request: RuntimeRequest, context: RuntimeExecutionContext
    ) -> Decimal:
        return maximum_cost_usd(request, context)

    def validate_request(
        self, request: RuntimeRequest, context: RuntimeExecutionContext
    ) -> None:
        self._client_tools.validate_request(request, context)
