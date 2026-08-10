from __future__ import annotations

from decimal import Decimal
import unittest

from backend.app.personal_workspace.agent.ai_runtime import (
    AIRuntimeCapabilities,
    RuntimeBudget,
    RuntimeEvent,
    RuntimeRequest,
    RuntimeResult,
    RuntimeUsage,
    run_runtime,
)


class ScriptedCompletionRuntime:
    capabilities = AIRuntimeCapabilities(
        runtime_kind="completion",
        client_tools=True,
        hosted_tools=False,
        cancellation=True,
        usage=True,
    )

    def __init__(self, result: RuntimeResult) -> None:
        self.result = result
        self.requests: list[RuntimeRequest] = []

    def run(self, request: RuntimeRequest) -> RuntimeResult:
        self.requests.append(request)
        return self.result


class ScriptedHostedRuntime(ScriptedCompletionRuntime):
    capabilities = AIRuntimeCapabilities(
        runtime_kind="hosted_tool",
        client_tools=True,
        hosted_tools=True,
        cancellation=True,
        usage=True,
    )


def completed_result() -> RuntimeResult:
    return RuntimeResult.completed(
        events=(
            RuntimeEvent(type="run_started"),
            RuntimeEvent(
                type="tool_requested",
                tool_name="get_today_context",
                tool_call_id="call-1",
                arguments={},
            ),
            RuntimeEvent(
                type="tool_completed",
                tool_name="get_today_context",
                tool_call_id="call-1",
                evidence_ids=("portfolio:snapshot:1",),
            ),
            RuntimeEvent(type="output_completed", text='{"claims": []}'),
        ),
        usage=RuntimeUsage(
            input_tokens=800,
            output_tokens=200,
            cache_read_tokens=300,
            cache_write_tokens=0,
            cost_usd=Decimal("0.0002"),
        ),
    )


class AIRuntimeContractTest(unittest.TestCase):
    def test_completion_runtime_returns_provider_neutral_events_and_usage(self) -> None:
        runtime = ScriptedCompletionRuntime(completed_result())
        request = RuntimeRequest(
            model="model-under-test",
            instructions="只依据工具证据回答",
            input_text="今天需要关注什么？",
            tools=("get_today_context",),
            budget=RuntimeBudget(remaining_usd=Decimal("0.01")),
        )
        result = run_runtime(runtime, request)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.events[1].type, "tool_requested")
        self.assertEqual(result.events[2].evidence_ids, ("portfolio:snapshot:1",))
        self.assertEqual(result.usage.cost_usd, Decimal("0.0002"))
        self.assertFalse(hasattr(result, "raw_response"))
        self.assertEqual(runtime.requests, [request])

    def test_budget_is_rejected_before_adapter_call(self) -> None:
        runtime = ScriptedCompletionRuntime(completed_result())
        result = run_runtime(
            runtime,
            RuntimeRequest(
                model="model-under-test",
                instructions="test",
                input_text="test",
                budget=RuntimeBudget(remaining_usd=Decimal("0")),
            ),
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure.code, "budget_insufficient")
        self.assertFalse(result.failure.retryable)
        self.assertEqual(runtime.requests, [])

    def test_cancel_is_stable_and_does_not_call_adapter(self) -> None:
        runtime = ScriptedCompletionRuntime(completed_result())
        result = run_runtime(
            runtime,
            RuntimeRequest(
                model="model-under-test",
                instructions="test",
                input_text="test",
                budget=RuntimeBudget(remaining_usd=Decimal("1")),
                cancel_requested=True,
            ),
        )
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.failure.code, "cancelled")
        self.assertEqual(runtime.requests, [])

    def test_hosted_tools_fail_closed_on_completion_runtime(self) -> None:
        runtime = ScriptedCompletionRuntime(completed_result())
        result = run_runtime(
            runtime,
            RuntimeRequest(
                model="model-under-test",
                instructions="test",
                input_text="test",
                hosted_tools=("web_search",),
                budget=RuntimeBudget(remaining_usd=Decimal("1")),
            ),
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure.code, "capability_unsupported")
        self.assertEqual(runtime.requests, [])

    def test_invalid_adapter_result_is_normalized_to_stable_failure(self) -> None:
        invalid = RuntimeResult(
            status="completed",
            events=(RuntimeEvent(type="run_started"),),
            usage=None,
            failure=None,
        )
        runtime = ScriptedCompletionRuntime(invalid)
        result = run_runtime(
            runtime,
            RuntimeRequest(
                model="model-under-test",
                instructions="test",
                input_text="test",
                budget=RuntimeBudget(remaining_usd=Decimal("1")),
            ),
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure.code, "runtime_contract_invalid")

    def test_provider_specific_failure_is_normalized(self) -> None:
        class ProviderError(RuntimeError):
            code = "provider_auth_failed"
            retryable = False

        class FailingRuntime(ScriptedCompletionRuntime):
            def run(self, request: RuntimeRequest) -> RuntimeResult:
                raise ProviderError()

        result = run_runtime(
            FailingRuntime(completed_result()),
            RuntimeRequest(
                model="model-under-test",
                instructions="test",
                input_text="test",
                budget=RuntimeBudget(remaining_usd=Decimal("1")),
            ),
        )
        self.assertEqual(result.failure.code, "provider_unauthorized")
        self.assertFalse(result.failure.retryable)

    def test_tool_events_require_ids_evidence_and_consistent_terminal_state(self) -> None:
        invalid_events = (
            (
                RuntimeEvent(type="run_started"),
                RuntimeEvent(type="tool_requested", tool_name="get_today_context"),
                RuntimeEvent(type="output_completed", text="done"),
            ),
            (
                RuntimeEvent(type="run_started"),
                RuntimeEvent(
                    type="tool_requested",
                    tool_name="get_today_context",
                    tool_call_id="call-1",
                    arguments={},
                ),
                RuntimeEvent(
                    type="tool_completed",
                    tool_name="get_today_context",
                    tool_call_id="call-1",
                ),
                RuntimeEvent(type="output_completed", text="done"),
            ),
            (
                RuntimeEvent(type="run_started"),
                RuntimeEvent(type="run_failed"),
                RuntimeEvent(type="output_completed", text="done"),
            ),
        )
        for events in invalid_events:
            with self.subTest(events=events):
                runtime = ScriptedCompletionRuntime(
                    RuntimeResult.completed(
                        events=events,
                        usage=RuntimeUsage(1, 1, 0, 0, Decimal("0")),
                    )
                )
                result = run_runtime(
                    runtime,
                    RuntimeRequest(
                        model="model-under-test",
                        instructions="test",
                        input_text="test",
                        budget=RuntimeBudget(remaining_usd=Decimal("1")),
                    ),
                )
                self.assertEqual(result.failure.code, "runtime_contract_invalid")

    def test_provider_envelopes_are_rejected_from_event_payloads(self) -> None:
        cases = (
            (
                RuntimeEvent(type="run_started"),
                RuntimeEvent(type="output_completed", text='{"choices": []}'),
            ),
            (
                RuntimeEvent(type="run_started"),
                RuntimeEvent(
                    type="tool_requested",
                    tool_name="get_today_context",
                    tool_call_id="call-1",
                    arguments={"payload": {"choices": []}},
                ),
                RuntimeEvent(
                    type="tool_failed",
                    tool_name="get_today_context",
                    tool_call_id="call-1",
                ),
                RuntimeEvent(type="output_completed", text="done"),
            ),
        )
        for events in cases:
            result = run_runtime(
                ScriptedCompletionRuntime(
                    RuntimeResult.completed(
                        events=events,
                        usage=RuntimeUsage(1, 1, 0, 0, Decimal("0")),
                    )
                ),
                RuntimeRequest(
                    model="model-under-test",
                    instructions="test",
                    input_text="test",
                    budget=RuntimeBudget(remaining_usd=Decimal("1")),
                ),
            )
            self.assertEqual(result.failure.code, "runtime_contract_invalid")

    def test_hosted_tool_events_must_pair_and_return_verified_evidence(self) -> None:
        invalid = RuntimeResult.completed(
            events=(
                RuntimeEvent(type="run_started"),
                RuntimeEvent(
                    type="hosted_tool_started",
                    tool_name="web_search",
                    tool_call_id="hosted-1",
                    arguments={"query": "NVDA filing"},
                ),
                RuntimeEvent(type="output_completed", text="done"),
            ),
            usage=RuntimeUsage(1, 1, 0, 0, Decimal("0")),
        )
        result = run_runtime(
            ScriptedHostedRuntime(invalid),
            RuntimeRequest(
                model="model-under-test",
                instructions="test",
                input_text="test",
                hosted_tools=("web_search",),
                budget=RuntimeBudget(remaining_usd=Decimal("1")),
            ),
        )
        self.assertEqual(result.failure.code, "runtime_contract_invalid")


if __name__ == "__main__":
    unittest.main()
