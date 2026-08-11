from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import unittest

from backend.app.personal_workspace.agent.ai_runtime import (
    RuntimeBudget,
    RuntimeExecutionContext,
    RuntimeRequest,
    run_runtime,
)
from backend.app.personal_workspace.agent.client_tool_runtime import (
    MAX_ASSISTANT_ENVELOPE_BYTES,
    MAX_TOOL_RESULT_BYTES,
)
from backend.app.personal_workspace.agent.completion_runtime import (
    DeepSeekCompletionRuntime,
)
from backend.app.personal_workspace.agent.domain_tools import (
    DomainToolResult,
    EvidenceEnvelope,
    RuntimeToolDefinition,
)
from backend.app.personal_workspace.analysis import DEEPSEEK_MODEL, ProviderFailure
from backend.tests.agent_test_helpers import (
    ScriptedAgentProvider,
    completed_response,
    tool_call,
)


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


class RecordingExecutor:
    def __init__(
        self, *, fail: bool = False, large: bool = False, duplicate: bool = False
    ) -> None:
        self.fail = fail
        self.large = large
        self.duplicate = duplicate
        self.calls: list[tuple[str, dict]] = []

    def invoke(self, name: str, arguments: dict) -> DomainToolResult:
        self.calls.append((name, arguments))
        if self.fail:
            raise RuntimeError("synthetic secret must not escape")
        value = "数" * 20_000 if self.large else name
        index = len(self.calls)
        envelope = EvidenceEnvelope(
            evidence_id=f"ledger:{name}:{index}",
            source="synthetic-ledger",
            as_of=NOW,
            content_sha256=f"{index:064x}",
            authorized_fields=("value",),
        )
        return DomainToolResult.success(
            data={"value": value},
            evidence=(envelope, envelope) if self.duplicate else (envelope,),
        )


def definition(name: str) -> RuntimeToolDefinition:
    return RuntimeToolDefinition(
        name=name,
        description=f"{name} 描述",
        input_schema={"type": "object", "properties": {}},
    )


def request(*names: str, budget: str = "1") -> RuntimeRequest:
    return RuntimeRequest(
        model=DEEPSEEK_MODEL,
        instructions="只输出 JSON。",
        input_text='{"question":"test","subject_ids":["NVDA"]}',
        budget=RuntimeBudget(remaining_usd=Decimal(budget)),
        tools=tuple(names),
    )


def context(
    executor,
    *names: str,
    deadline: datetime | None = None,
    heartbeat=lambda: None,
    cancel_requested=lambda: False,
) -> RuntimeExecutionContext:
    return RuntimeExecutionContext(
        tools=tuple(definition(name) for name in names),
        executor=executor,
        deadline=deadline or NOW + timedelta(minutes=10),
        heartbeat=heartbeat,
        cancel_requested=cancel_requested,
    )


class ClientToolRuntimeTest(unittest.TestCase):
    def test_single_and_multiple_tools_aggregate_usage_and_real_evidence(self) -> None:
        executor = RecordingExecutor()
        provider = ScriptedAgentProvider(
            [
                completed_response(
                    tool_calls=(
                        tool_call(name="get_holdings", call_id="call-1"),
                        tool_call(name="get_news", call_id="call-2"),
                    ),
                    input_tokens=400,
                    output_tokens=20,
                ),
                completed_response(
                    content='{"claims":[]}', input_tokens=500, output_tokens=30
                ),
            ]
        )
        result = run_runtime(
            DeepSeekCompletionRuntime(provider=provider, clock=lambda: NOW),
            request("get_holdings", "get_news"),
            context(executor, "get_holdings", "get_news"),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            executor.calls, [("get_holdings", {}), ("get_news", {})]
        )
        self.assertEqual(result.usage.input_tokens, 900)
        self.assertEqual(result.usage.output_tokens, 50)
        self.assertEqual(result.usage.cost_usd, Decimal("0.0004"))
        self.assertEqual(
            [item.evidence_id for item in result.tool_evidence],
            ["ledger:get_holdings:1", "ledger:get_news:2"],
        )
        self.assertFalse(
            any(item.evidence_id.startswith("tool:") for item in result.tool_evidence)
        )
        terminals = [
            event for event in result.events if event.type.startswith("tool_")
        ]
        self.assertEqual(
            [(event.tool_call_id, event.type) for event in terminals],
            [
                ("call-1", "tool_requested"),
                ("call-1", "tool_completed"),
                ("call-2", "tool_requested"),
                ("call-2", "tool_completed"),
            ],
        )

    def test_unknown_and_exception_are_paired_and_fed_back(self) -> None:
        executor = RecordingExecutor(fail=True)
        provider = ScriptedAgentProvider(
            [
                completed_response(
                    tool_calls=(
                        tool_call(name="unknown", call_id="unknown-1"),
                        tool_call(name="get_holdings", call_id="failed-1"),
                    )
                ),
                completed_response(content='{"claims":[]}'),
            ]
        )
        result = run_runtime(
            DeepSeekCompletionRuntime(provider=provider, clock=lambda: NOW),
            request("get_holdings"),
            context(executor, "get_holdings"),
        )

        self.assertEqual(result.status, "completed")
        failed = [event for event in result.events if event.type == "tool_failed"]
        self.assertEqual(
            [(event.tool_call_id, event.error_code) for event in failed],
            [("unknown-1", "unknown_tool"), ("failed-1", "tool_failed")],
        )
        tool_messages = provider.captured_requests[1]["messages"][-2:]
        self.assertEqual(
            [json.loads(item["content"])["error"] for item in tool_messages],
            ["unknown_tool", "tool_failed"],
        )
        self.assertNotIn("synthetic secret", repr(result))

    def test_duplicate_evidence_ids_in_one_tool_result_are_stably_deduplicated(self) -> None:
        provider = ScriptedAgentProvider(
            [
                completed_response(
                    tool_calls=(tool_call(name="get_holdings", call_id="call-1"),)
                ),
                completed_response(content='{"claims":[]}'),
            ]
        )
        result = run_runtime(
            DeepSeekCompletionRuntime(provider=provider, clock=lambda: NOW),
            request("get_holdings"),
            context(RecordingExecutor(duplicate=True), "get_holdings"),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            [item.evidence_id for item in result.tool_evidence],
            ["ledger:get_holdings:1"],
        )
        completed = next(
            event for event in result.events if event.type == "tool_completed"
        )
        self.assertEqual(completed.evidence_ids, ("ledger:get_holdings:1",))

    def test_result_and_assistant_size_limits_are_enforced(self) -> None:
        executor = RecordingExecutor(large=True)
        provider = ScriptedAgentProvider(
            [
                completed_response(
                    tool_calls=(tool_call(name="get_holdings", call_id="large"),)
                ),
                completed_response(content='{"claims":[]}'),
            ]
        )
        result = run_runtime(
            DeepSeekCompletionRuntime(provider=provider, clock=lambda: NOW),
            request("get_holdings"),
            context(executor, "get_holdings"),
        )
        tool_payload = provider.captured_requests[1]["messages"][-1]["content"]
        self.assertLessEqual(len(tool_payload.encode("utf-8")), MAX_TOOL_RESULT_BYTES)
        self.assertIn("...[truncated]", tool_payload)
        self.assertEqual(result.status, "completed")

        huge_arguments = {"value": "x" * MAX_ASSISTANT_ENVELOPE_BYTES}
        provider = ScriptedAgentProvider(
            [
                completed_response(
                    tool_calls=(
                        tool_call(
                            name="get_holdings",
                            call_id="huge",
                            arguments=huge_arguments,
                        ),
                    )
                )
            ]
        )
        rejected = run_runtime(
            DeepSeekCompletionRuntime(provider=provider, clock=lambda: NOW),
            request("get_holdings"),
            context(RecordingExecutor(), "get_holdings"),
        )
        self.assertEqual(rejected.failure.code, "provider_tool_calls_invalid")
        self.assertEqual(
            [event.type for event in rejected.events], ["run_started", "run_failed"]
        )

        final_too_large = run_runtime(
            DeepSeekCompletionRuntime(
                provider=ScriptedAgentProvider(
                    [completed_response(content="字" * 300_000)]
                ),
                clock=lambda: NOW,
            ),
            request("get_holdings"),
            context(RecordingExecutor(), "get_holdings"),
        )
        self.assertEqual(final_too_large.failure.code, "provider_invalid_response")
        self.assertEqual(final_too_large.usage.cost_usd, Decimal("0.0002"))
        self.assertEqual(
            [event.type for event in final_too_large.events],
            ["run_started", "run_failed"],
        )

    def test_evidence_excerpts_only_project_each_envelopes_authorized_fields(self) -> None:
        class ProjectionExecutor:
            def invoke(self, _name, _arguments):
                return DomainToolResult.success(
                    data={"item1": "SAFE-1", "item2": "SECRET-2"},
                    evidence=(
                        EvidenceEnvelope(
                            "ledger:item:1", "source-1", NOW, "1" * 64, ("item1",)
                        ),
                        EvidenceEnvelope(
                            "ledger:item:2", "source-2", NOW, "2" * 64, ("item2",)
                        ),
                        EvidenceEnvelope(
                            "ledger:item:3", "source-3", NOW, "3" * 64, ("missing",)
                        ),
                    ),
                )

        result = run_runtime(
            DeepSeekCompletionRuntime(
                provider=ScriptedAgentProvider(
                    [
                        completed_response(
                            tool_calls=(
                                tool_call(name="get_holdings", call_id="call-1"),
                            )
                        ),
                        completed_response(content='{"claims":[]}'),
                    ]
                ),
                clock=lambda: NOW,
            ),
            request("get_holdings"),
            context(ProjectionExecutor(), "get_holdings"),
        )

        evidence = {item.evidence_id: item.excerpt for item in result.tool_evidence}
        self.assertIn("SAFE-1", evidence["ledger:item:1"])
        self.assertNotIn("SECRET-2", evidence["ledger:item:1"])
        self.assertIn("SECRET-2", evidence["ledger:item:2"])
        self.assertNotIn("SAFE-1", evidence["ledger:item:2"])
        self.assertNotIn("SAFE-1", evidence["ledger:item:3"])
        self.assertNotIn("SECRET-2", evidence["ledger:item:3"])

    def test_round_call_id_and_per_round_limits_fail_with_known_usage(self) -> None:
        cases = (
            tuple(
                tool_call(name="get_holdings", call_id=f"call-{index}")
                for index in range(9)
            ),
            (
                tool_call(name="get_holdings", call_id="duplicate"),
                tool_call(name="get_holdings", call_id="duplicate"),
            ),
        )
        for calls in cases:
            with self.subTest(calls=len(calls)):
                result = run_runtime(
                    DeepSeekCompletionRuntime(
                        provider=ScriptedAgentProvider(
                            [completed_response(tool_calls=calls)]
                        ),
                        clock=lambda: NOW,
                    ),
                    request("get_holdings"),
                    context(RecordingExecutor(), "get_holdings"),
                )
                self.assertEqual(result.failure.code, "provider_tool_calls_invalid")
                self.assertEqual(result.usage.cost_usd, Decimal("0.0002"))

        script = [
            completed_response(
                tool_calls=(
                    tool_call(name="get_holdings", call_id=f"round-{index}"),
                )
            )
            for index in range(5)
        ]
        exhausted = run_runtime(
            DeepSeekCompletionRuntime(
                provider=ScriptedAgentProvider(script), clock=lambda: NOW
            ),
            request("get_holdings"),
            context(RecordingExecutor(), "get_holdings"),
        )
        self.assertEqual(exhausted.failure.code, "agent_tool_rounds_exceeded")
        self.assertEqual(exhausted.usage.cost_usd, Decimal("0.0010"))
        self.assertEqual(
            len([event for event in exhausted.events if event.type == "tool_requested"]),
            5,
        )

    def test_deadline_cancel_refusal_timeout_and_remaining_budget(self) -> None:
        provider = ScriptedAgentProvider([])
        timed_out = run_runtime(
            DeepSeekCompletionRuntime(provider=provider, clock=lambda: NOW),
            request("get_holdings"),
            context(
                RecordingExecutor(),
                "get_holdings",
                deadline=NOW,
            ),
        )
        self.assertEqual(timed_out.failure.code, "provider_timeout")
        self.assertEqual(provider.captured_requests, [])

        cancelled = run_runtime(
            DeepSeekCompletionRuntime(
                provider=ScriptedAgentProvider([]), clock=lambda: NOW
            ),
            request("get_holdings"),
            context(
                RecordingExecutor(),
                "get_holdings",
                cancel_requested=lambda: True,
            ),
        )
        self.assertEqual(cancelled.status, "cancelled")
        self.assertFalse(cancelled.failure.outcome_unknown)

        refusal = run_runtime(
            DeepSeekCompletionRuntime(
                provider=ScriptedAgentProvider(
                    [{"status": "refusal", "message": {}}]
                ),
                clock=lambda: NOW,
            ),
            request("get_holdings"),
            context(RecordingExecutor(), "get_holdings"),
        )
        self.assertEqual(refusal.failure.code, "provider_refusal")
        self.assertTrue(refusal.failure.outcome_unknown)

        timeout = run_runtime(
            DeepSeekCompletionRuntime(
                provider=ScriptedAgentProvider(
                    [ProviderFailure("provider_timeout", retryable=False)]
                ),
                clock=lambda: NOW,
            ),
            request("get_holdings"),
            context(RecordingExecutor(), "get_holdings"),
        )
        self.assertEqual(timeout.failure.code, "provider_timeout")
        self.assertTrue(timeout.failure.outcome_unknown)

        over_budget = run_runtime(
            DeepSeekCompletionRuntime(
                provider=ScriptedAgentProvider([completed_response()]),
                clock=lambda: NOW,
            ),
            request("get_holdings", budget="0.0001"),
            context(RecordingExecutor(), "get_holdings"),
        )
        self.assertEqual(over_budget.failure.code, "budget_exceeded")
        self.assertEqual(over_budget.usage.cost_usd, Decimal("0.0002"))
        self.assertEqual(
            [event.type for event in over_budget.events],
            ["run_started", "run_failed"],
        )


if __name__ == "__main__":
    unittest.main()
