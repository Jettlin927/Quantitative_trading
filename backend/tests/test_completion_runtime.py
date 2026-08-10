from __future__ import annotations

from decimal import Decimal
import json
import unittest
from urllib.error import HTTPError

from backend.app.personal_workspace.agent.ai_runtime import (
    RuntimeBudget,
    RuntimeRequest,
    run_runtime,
)
from backend.app.personal_workspace.agent.completion_runtime import (
    DeepSeekCompletionRuntime,
)
from backend.app.personal_workspace.analysis import DEEPSEEK_MODEL


class DeepSeekCompletionRuntimeTest(unittest.TestCase):
    def test_client_or_hosted_tools_are_rejected_before_transport(self) -> None:
        calls = []
        runtime = DeepSeekCompletionRuntime(
            api_key="synthetic",
            transport=lambda **kwargs: calls.append(kwargs),
        )

        for request in (
            RuntimeRequest(
                model=DEEPSEEK_MODEL,
                instructions="test",
                input_text="test",
                tools=("get_today_context",),
                budget=RuntimeBudget(remaining_usd=Decimal("1")),
            ),
            RuntimeRequest(
                model=DEEPSEEK_MODEL,
                instructions="test",
                input_text="test",
                hosted_tools=("web_search",),
                budget=RuntimeBudget(remaining_usd=Decimal("1")),
            ),
        ):
            with self.subTest(request=request):
                result = run_runtime(runtime, request)
                self.assertEqual(result.failure.code, "capability_unsupported")
        self.assertEqual(calls, [])

    def test_one_completion_uses_fixed_request_and_returns_neutral_events(self) -> None:
        captured: list[dict] = []

        def transport(*, url, headers, body, timeout_seconds):
            captured.append(
                {
                    "url": url,
                    "headers": headers,
                    "body": body,
                    "timeout_seconds": timeout_seconds,
                }
            )
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"claims":[]}'},
                    }
                ],
                "usage": {
                    "prompt_tokens": 3000,
                    "completion_tokens": 500,
                    "prompt_cache_hit_tokens": 1000,
                    "prompt_cache_miss_tokens": 2000,
                },
            }

        runtime = DeepSeekCompletionRuntime(
            api_key="synthetic-key-never-log",
            transport=transport,
        )
        result = run_runtime(
            runtime,
            RuntimeRequest(
                model=DEEPSEEK_MODEL,
                instructions="只依据冻结证据输出 JSON。",
                input_text="生成盘前简报。",
                budget=RuntimeBudget(remaining_usd=Decimal("0.01")),
            ),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            [(event.type, event.text) for event in result.events],
            [("run_started", None), ("output_completed", '{"claims":[]}')],
        )
        self.assertEqual(result.usage.input_tokens, 3000)
        self.assertEqual(result.usage.output_tokens, 500)
        self.assertEqual(result.usage.cache_hit_tokens, 1000)
        self.assertEqual(result.usage.cache_miss_tokens, 2000)
        self.assertEqual(result.usage.cost_usd, Decimal("0.0004228"))
        self.assertFalse(hasattr(result, "raw_response"))

        self.assertEqual(len(captured), 1)
        provider_request = captured[0]
        self.assertEqual(
            provider_request["url"], "https://api.deepseek.com/chat/completions"
        )
        self.assertEqual(
            provider_request["headers"]["Authorization"],
            "Bearer synthetic-key-never-log",
        )
        body = provider_request["body"]
        self.assertEqual(body["model"], "deepseek-v4-flash")
        self.assertEqual(body["stream"], False)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["max_tokens"], 4096)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertNotIn("tools", body)
        self.assertEqual(
            [message["role"] for message in body["messages"]], ["system", "user"]
        )
        self.assertEqual(body["messages"][0]["content"], "只依据冻结证据输出 JSON。")
        user_payload = json.loads(body["messages"][1]["content"])
        self.assertEqual(user_payload["input"], "生成盘前简报。")
        self.assertEqual(set(user_payload), {"input"})

    def test_provider_failure_is_normalized_by_runtime_boundary(self) -> None:
        def fail_http(**_kwargs):
            raise HTTPError(
                "https://api.deepseek.com/chat/completions",
                503,
                "synthetic provider details",
                None,
                None,
            )

        runtime = DeepSeekCompletionRuntime(api_key="synthetic", transport=fail_http)
        result = run_runtime(
            runtime,
            RuntimeRequest(
                model=DEEPSEEK_MODEL,
                instructions="输出 JSON。",
                input_text="生成简报。",
                budget=RuntimeBudget(remaining_usd=Decimal("0.01")),
            ),
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure.code, "provider_unavailable")
        self.assertTrue(result.failure.retryable)
        self.assertEqual([event.type for event in result.events], ["run_failed"])

    def test_refusal_is_a_stable_runtime_failure(self) -> None:
        runtime = DeepSeekCompletionRuntime(
            api_key="synthetic",
            transport=lambda **_kwargs: {
                "choices": [
                    {
                        "finish_reason": "content_filter",
                        "message": {"content": None, "refusal": "raw refusal"},
                    }
                ]
            },
        )
        result = run_runtime(
            runtime,
            RuntimeRequest(
                model=DEEPSEEK_MODEL,
                instructions="输出 JSON。",
                input_text="生成简报。",
                budget=RuntimeBudget(remaining_usd=Decimal("0.01")),
            ),
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure.code, "provider_refusal")
        self.assertNotIn("raw refusal", repr(result))


if __name__ == "__main__":
    unittest.main()
