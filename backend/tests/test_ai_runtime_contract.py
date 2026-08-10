from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import unittest

from backend.app.personal_workspace.agent.ai_runtime import (
    AIRuntimeCapabilities,
    RuntimeBudget,
    RuntimeCitation,
    RuntimeEvidence,
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
            cache_hit_tokens=300,
            cache_miss_tokens=0,
            cost_usd=Decimal("0.0002"),
        ),
    )


class AIRuntimeContractTest(unittest.TestCase):
    def test_hosted_runtime_returns_verified_evidence_citations_and_usage(self) -> None:
        output = "英伟达已发布最新公开文件。"
        result = run_runtime(
            ScriptedHostedRuntime(
                RuntimeResult.completed(
                    events=(
                        RuntimeEvent(type="run_started"),
                        RuntimeEvent(
                            type="hosted_tool_started",
                            tool_name="web_search",
                            tool_call_id="hosted-1",
                            arguments={"queries": ["NVDA latest filing", "NVDA IR"]},
                        ),
                        RuntimeEvent(
                            type="hosted_tool_completed",
                            tool_name="web_search",
                            tool_call_id="hosted-1",
                            evidence_ids=("web:1",),
                        ),
                        RuntimeEvent(type="output_completed", text=output),
                    ),
                    usage=RuntimeUsage(
                        100,
                        20,
                        10,
                        90,
                        Decimal("0.003"),
                        hosted_tool_calls=1,
                        web_search_queries=2,
                        hosted_cost_usd=Decimal("0.001"),
                    ),
                    evidence=(
                        RuntimeEvidence(
                            evidence_id="web:1",
                            url="https://investor.example/nvda",
                            title="NVDA Investor Relations",
                            verified_excerpt="latest public filing",
                            body_sha256="a" * 64,
                            verified_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
                        ),
                    ),
                    citations=(
                        RuntimeCitation(
                            evidence_id="web:1",
                            output_block_index=0,
                            start_char=0,
                            end_char=len(output),
                            cited_text_sha256=sha256(output.encode("utf-8")).hexdigest(),
                        ),
                    ),
                )
            ),
            RuntimeRequest(
                model="model-under-test",
                instructions="test",
                input_text="test",
                hosted_tools=("web_search",),
                budget=RuntimeBudget(remaining_usd=Decimal("1")),
            ),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.evidence[0].evidence_id, "web:1")
        self.assertEqual(result.citations[0].evidence_id, "web:1")
        self.assertEqual(result.usage.web_search_queries, 2)

    def test_hosted_usage_must_match_events_queries_and_total_cost(self) -> None:
        events = (
            RuntimeEvent(type="run_started"),
            RuntimeEvent(
                type="hosted_tool_started",
                tool_name="web_search",
                tool_call_id="hosted-1",
                arguments={"queries": ["query one", "query two"]},
            ),
            RuntimeEvent(
                type="hosted_tool_completed",
                tool_name="web_search",
                tool_call_id="hosted-1",
                evidence_ids=("web:1",),
            ),
            RuntimeEvent(type="output_completed", text="verified output"),
        )
        evidence = (
            RuntimeEvidence(
                evidence_id="web:1",
                url="https://example.com/source",
                title="Source",
                verified_excerpt="verified",
                body_sha256="b" * 64,
                verified_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
            ),
        )
        citation = (
            RuntimeCitation(
                evidence_id="web:1",
                output_block_index=0,
                cited_text_sha256=sha256(b"verified output").hexdigest(),
            ),
        )
        invalid_usage = (
            RuntimeUsage(10, 10, 0, 10, Decimal("0.003"), 0, 2, Decimal("0.001")),
            RuntimeUsage(10, 10, 0, 10, Decimal("0.003"), 1, 1, Decimal("0.001")),
            RuntimeUsage(10, 10, 0, 10, Decimal("0.003"), 1, 2, Decimal("0.004")),
        )

        for usage in invalid_usage:
            with self.subTest(usage=usage):
                result = run_runtime(
                    ScriptedHostedRuntime(
                        RuntimeResult.completed(
                            events=events,
                            usage=usage,
                            evidence=evidence,
                            citations=citation,
                        )
                    ),
                    RuntimeRequest(
                        model="model-under-test",
                        instructions="test",
                        input_text="test",
                        hosted_tools=("web_search",),
                        budget=RuntimeBudget(remaining_usd=Decimal("1")),
                    ),
                )
                self.assertEqual(result.failure.code, "runtime_contract_invalid")

    def test_hosted_evidence_and_citations_must_be_verified_and_consistent(self) -> None:
        output = "verified output"
        events = (
            RuntimeEvent(type="run_started"),
            RuntimeEvent(
                type="hosted_tool_started",
                tool_name="web_search",
                tool_call_id="hosted-1",
                arguments={"query": "public query"},
            ),
            RuntimeEvent(
                type="hosted_tool_completed",
                tool_name="web_search",
                tool_call_id="hosted-1",
                evidence_ids=("web:1",),
            ),
            RuntimeEvent(type="output_completed", text=output),
        )
        evidence = RuntimeEvidence(
            evidence_id="web:1",
            url="https://example.com/source",
            title="Source",
            verified_excerpt="verified",
            body_sha256="c" * 64,
            verified_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        citation = RuntimeCitation(
            evidence_id="web:1",
            output_block_index=0,
            start_char=0,
            end_char=len(output),
            cited_text_sha256=sha256(output.encode("utf-8")).hexdigest(),
        )
        invalid_contracts = (
            ((), ()),
            ((evidence,), ()),
            (({"evidence_id": "web:1"},), (citation,)),
            ((evidence,), ({"evidence_id": "web:1"},)),
            ((evidence, evidence), (citation,)),
            ((replace(evidence, url="file:///tmp/source"),), (citation,)),
            ((replace(evidence, body_sha256="not-a-hash"),), (citation,)),
            ((replace(evidence, verified_at=datetime(2026, 8, 10)),), (citation,)),
            ((replace(evidence, verified_at="2026-08-10T00:00:00Z"),), (citation,)),
            ((replace(evidence, verified_excerpt='{"choices": []}'),), (citation,)),
            ((evidence,), (replace(citation, evidence_id="web:missing"),)),
            ((evidence,), (replace(citation, output_block_index=1),)),
            ((evidence,), (replace(citation, cited_text_sha256="d" * 64),)),
            ((evidence,), (replace(citation, end_char=None),)),
        )

        for evidence_items, citations in invalid_contracts:
            with self.subTest(evidence=evidence_items, citations=citations):
                result = run_runtime(
                    ScriptedHostedRuntime(
                        RuntimeResult.completed(
                            events=events,
                            usage=RuntimeUsage(
                                10,
                                10,
                                0,
                                10,
                                Decimal("0.003"),
                                hosted_tool_calls=1,
                                web_search_queries=1,
                                hosted_cost_usd=Decimal("0.001"),
                            ),
                            evidence=evidence_items,
                            citations=citations,
                        )
                    ),
                    RuntimeRequest(
                        model="model-under-test",
                        instructions="test",
                        input_text="test",
                        hosted_tools=("web_search",),
                        budget=RuntimeBudget(remaining_usd=Decimal("1")),
                    ),
                )
                self.assertEqual(result.failure.code, "runtime_contract_invalid")

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
                    type="output_completed",
                    text='{"encrypted_content":"provider-opaque"}',
                ),
            ),
            (
                RuntimeEvent(type="run_started"),
                RuntimeEvent(
                    type="output_completed",
                    text='{"type":"server_tool_use","id":"raw"}',
                ),
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
