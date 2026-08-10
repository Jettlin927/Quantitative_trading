"""DeepSeek Chat Completions 的 provider-neutral AIRuntime adapter。"""

from __future__ import annotations

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
    RuntimeRequest,
    RuntimeResult,
    RuntimeUsage,
)
from .deepseek_provider import DeepSeekAgentChatAdapter


class DeepSeekCompletionRuntime:
    """把一次固定 DeepSeek completion 收敛为稳定 RuntimeResult。"""

    capabilities = AIRuntimeCapabilities(
        runtime_kind="completion",
        client_tools=False,
        hosted_tools=False,
        cancellation=False,
        usage=True,
    )

    def __init__(
        self,
        *,
        api_key: str,
        transport: Callable[..., dict[str, Any]] | None = None,
        timeout_seconds: int = 90,
    ) -> None:
        self._provider = DeepSeekAgentChatAdapter(
            api_key=api_key,
            transport=transport,
            timeout_seconds=timeout_seconds,
        )

    def __repr__(self) -> str:
        return "DeepSeekCompletionRuntime(api_key=<redacted>)"

    def run(self, request: RuntimeRequest) -> RuntimeResult:
        if request.model != DEEPSEEK_MODEL:
            raise ProviderFailure("provider_request_invalid", retryable=False)
        if request.hosted_tools:
            raise ProviderFailure("provider_request_invalid", retryable=False)

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
            raise ProviderFailure("provider_refusal", retryable=False)

        message = response["message"]
        if message["tool_calls"]:
            raise ProviderFailure("provider_response_envelope_invalid", retryable=False)
        content = message["content"]
        if not isinstance(content, str) or not content.strip():
            raise ProviderFailure("provider_response_envelope_invalid", retryable=False)

        usage = response["usage"]
        return RuntimeResult.completed(
            events=(
                RuntimeEvent(type="run_started"),
                RuntimeEvent(type="output_completed", text=content),
            ),
            usage=RuntimeUsage(
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cache_hit_tokens=usage["cache_hit_tokens"],
                cache_miss_tokens=usage["cache_miss_tokens"],
                cost_usd=Decimal(response["cost_usd"]),
            ),
        )
