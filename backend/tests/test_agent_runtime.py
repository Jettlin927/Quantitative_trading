from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from backend.app.personal_workspace.agent.protocol import (
    Skill,
    Tool,
    ToolContext,
    ToolResult,
)
from backend.app.personal_workspace.agent.runtime import AgentRuntime
from backend.app.personal_workspace.agent.deepseek_provider import DeepSeekAgentChatAdapter
from backend.app.personal_workspace.analysis import (
    AnalysisIntent,
    DEEPSEEK_MODEL,
    ProviderFailure,
)

from backend.tests.agent_test_helpers import (
    ScriptedAgentProvider,
    claims_content,
    completed_response,
    make_tool,
    tool_call,
)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def make_runtime(
    provider: ScriptedAgentProvider,
    tools: tuple[Tool, ...],
    *,
    skills: tuple[Skill, ...] = (),
    budget: str = "25",
) -> AgentRuntime:
    return AgentRuntime(
        provider=provider,
        tools=tools,
        skills=skills,
        model=DEEPSEEK_MODEL,
        clock=lambda: NOW,
        monthly_soft_budget_usd=Decimal(budget),
        monthly_spend_reader=lambda _actor_id, _now: Decimal("0"),
    )


def make_intent() -> AnalysisIntent:
    return AnalysisIntent(question="NVDA 当前持仓如何？", subject_ids=("NVDA",))


class AgentRuntimeTest(unittest.TestCase):
    def test_runtime_request_reaches_deepseek_adapter_with_json_array_tools(self) -> None:
        captured: list[dict] = []

        def transport(*, body: dict, **_kwargs) -> dict:
            captured.append(body)
            if len(captured) == 1:
                return {
                    "choices": [{
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [{
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "get_holdings",
                                    "arguments": "{}",
                                },
                            }],
                        },
                    }],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "prompt_cache_hit_tokens": 0,
                        "prompt_cache_miss_tokens": 100,
                    },
                }
            return {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": claims_content()},
                }],
                "usage": {
                    "prompt_tokens": 200,
                    "completion_tokens": 80,
                    "prompt_cache_hit_tokens": 0,
                    "prompt_cache_miss_tokens": 200,
                },
            }

        runtime = AgentRuntime(
            provider=DeepSeekAgentChatAdapter(
                api_key="synthetic-key",
                transport=transport,
            ),
            tools=(make_tool("get_holdings"),),
            model=DEEPSEEK_MODEL,
            clock=lambda: NOW,
        )

        result = runtime.run(
            actor_id="actor-1",
            intent=make_intent(),
            spend_before=Decimal("0"),
        )

        self.assertEqual(len(result.claims), 4)
        self.assertEqual(len(captured), 2)
        self.assertIsInstance(captured[0]["tools"], list)
        self.assertEqual(
            captured[0]["tools"][0]["function"]["name"],
            "get_holdings",
        )

    def test_single_tool_round_then_final_claims(self) -> None:
        provider = ScriptedAgentProvider(
            [
                completed_response(
                    tool_calls=(tool_call(name="get_holdings"),)
                ),
                completed_response(content=claims_content()),
            ]
        )
        runtime = make_runtime(provider, (make_tool("get_holdings"),))
        result = runtime.run(actor_id="actor-1", intent=make_intent(), spend_before=Decimal("0"))
        self.assertEqual(len(result.claims), 4)
        self.assertEqual(result.rounds, 2)
        self.assertEqual(result.cost_usd, "0.0004")
        self.assertEqual(result.usage.input_tokens, 1600)
        self.assertEqual(len(result.tool_evidence), 1)
        self.assertEqual(result.tool_evidence[0].evidence_id, "tool:get_holdings:0")
        self.assertEqual(result.tool_evidence[0].kind, "tool_output")
        # 请求里必须带 tools schema 与多轮消息
        first_request = provider.captured_requests[0]
        self.assertEqual(first_request["model"], DEEPSEEK_MODEL)
        self.assertEqual(first_request["tools"][0]["function"]["name"], "get_holdings")
        self.assertEqual(
            [message["role"] for message in first_request["messages"]],
            ["system", "user"],
        )
        second_request = provider.captured_requests[1]
        roles = [message["role"] for message in second_request["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "tool"])
        tool_message = second_request["messages"][3]
        self.assertEqual(tool_message["tool_call_id"], "call-1")
        wrapped = json.loads(tool_message["content"])
        self.assertEqual(wrapped["evidence_id"], "tool:get_holdings:0")
        self.assertTrue(wrapped["ok"])

    def test_two_tool_calls_in_same_round_get_sequential_evidence_ids(self) -> None:
        provider = ScriptedAgentProvider(
            [
                completed_response(
                    tool_calls=(
                        tool_call(name="get_holdings", call_id="call-a"),
                        tool_call(name="get_kline", call_id="call-b"),
                    )
                ),
                completed_response(content=claims_content(evidence_id="tool:get_kline:1")),
            ]
        )
        runtime = make_runtime(
            provider, (make_tool("get_holdings"), make_tool("get_kline"))
        )
        result = runtime.run(actor_id="actor-1", intent=make_intent(), spend_before=Decimal("0"))
        self.assertEqual(result.rounds, 2)
        self.assertEqual(
            [item.evidence_id for item in result.tool_evidence],
            ["tool:get_holdings:0", "tool:get_kline:1"],
        )

    def test_tool_failure_is_fed_back_and_loop_recovers(self) -> None:
        def failing(ctx: ToolContext, args: dict) -> ToolResult:
            raise RuntimeError("boom")

        provider = ScriptedAgentProvider(
            [
                completed_response(tool_calls=(tool_call(name="get_holdings"),)),
                completed_response(
                    tool_calls=(tool_call(name="get_news", call_id="call-2"),)
                ),
                completed_response(content=claims_content(evidence_id="tool:get_news:0")),
            ]
        )
        runtime = make_runtime(
            provider,
            (make_tool("get_holdings", handler=failing), make_tool("get_news")),
        )
        result = runtime.run(actor_id="actor-1", intent=make_intent(), spend_before=Decimal("0"))
        self.assertEqual(result.rounds, 3)
        failure_message = provider.captured_requests[1]["messages"][3]
        self.assertEqual(json.loads(failure_message["content"])["ok"], False)
        self.assertIn("RuntimeError", json.loads(failure_message["content"])["error"])

    def test_unknown_tool_call_is_reported_back(self) -> None:
        provider = ScriptedAgentProvider(
            [
                completed_response(tool_calls=(tool_call(name="get_unknown"),)),
                completed_response(
                    tool_calls=(tool_call(name="get_holdings", call_id="call-2"),)
                ),
                completed_response(content=claims_content()),
            ]
        )
        runtime = make_runtime(provider, (make_tool("get_holdings"),))
        result = runtime.run(actor_id="actor-1", intent=make_intent(), spend_before=Decimal("0"))
        self.assertEqual(result.rounds, 3)
        unknown_tool_message = provider.captured_requests[1]["messages"][3]
        self.assertEqual(
            json.loads(unknown_tool_message["content"])["error"], "agent_unknown_tool"
        )

    def test_rounds_exceeded_fails(self) -> None:
        provider = ScriptedAgentProvider(
            [completed_response(tool_calls=(tool_call(name="get_holdings"),))] * 6
        )
        runtime = make_runtime(provider, (make_tool("get_holdings"),))
        with self.assertRaisesRegex(ProviderFailure, "agent_tool_rounds_exceeded"):
            runtime.run(actor_id="actor-1", intent=make_intent(), spend_before=Decimal("0"))

    def test_budget_blocked_mid_loop(self) -> None:
        provider = ScriptedAgentProvider(
            [completed_response(tool_calls=(tool_call(name="get_holdings"),))] * 3
        )
        runtime = make_runtime(
            provider,
            (make_tool("get_holdings"),),
            budget="0.0002",
        )
        with self.assertRaisesRegex(ProviderFailure, "budget_blocked"):
            runtime.run(actor_id="actor-1", intent=make_intent(), spend_before=Decimal("0.0001"))

    def test_invalid_final_json_fails(self) -> None:
        provider = ScriptedAgentProvider([completed_response(content="不是 JSON")])
        runtime = make_runtime(provider, (make_tool("get_holdings"),))
        with self.assertRaisesRegex(ProviderFailure, "provider_content_invalid_json"):
            runtime.run(actor_id="actor-1", intent=make_intent(), spend_before=Decimal("0"))

    def test_fenced_json_is_accepted(self) -> None:
        provider = ScriptedAgentProvider(
            [
                completed_response(tool_calls=(tool_call(name="get_holdings"),)),
                completed_response(content="```json\n" + claims_content() + "\n```"),
            ]
        )
        runtime = make_runtime(provider, (make_tool("get_holdings"),))
        result = runtime.run(actor_id="actor-1", intent=make_intent(), spend_before=Decimal("0"))
        self.assertEqual(len(result.claims), 4)

    def test_refusal_fails(self) -> None:
        provider = ScriptedAgentProvider([{"status": "refusal", "message": {"content": None, "tool_calls": ()}}])
        runtime = make_runtime(provider, (make_tool("get_holdings"),))
        with self.assertRaisesRegex(ProviderFailure, "provider_refusal"):
            runtime.run(actor_id="actor-1", intent=make_intent(), spend_before=Decimal("0"))

    def test_claims_citing_unknown_evidence_are_rejected(self) -> None:
        content = claims_content(evidence_id="tool:get_holdings:9")  # 不存在该证据
        provider = ScriptedAgentProvider(
            [
                completed_response(tool_calls=(tool_call(name="get_holdings"),)),
                completed_response(content=content),
            ]
        )
        runtime = make_runtime(provider, (make_tool("get_holdings"),))
        with self.assertRaisesRegex(ProviderFailure, "claim_evidence_invalid"):
            runtime.run(actor_id="actor-1", intent=make_intent(), spend_before=Decimal("0"))

    def test_skill_prompt_is_injected(self) -> None:
        skill = Skill(
            skill_id="deep_impact",
            name="深度影响分析",
            description="完整影响分析",
            system_prompt="请先查看持仓。",
            tools=("get_holdings",),
        )
        provider = ScriptedAgentProvider(
            [
                completed_response(tool_calls=(tool_call(name="get_holdings"),)),
                completed_response(content=claims_content()),
            ]
        )
        runtime = make_runtime(provider, (make_tool("get_holdings"),), skills=(skill,))
        runtime.run(actor_id="actor-1", intent=make_intent(), spend_before=Decimal("0"))
        system_prompt = provider.captured_requests[0]["messages"][0]["content"]
        self.assertIn("请先查看持仓。", system_prompt)
        self.assertIn("不得输出买卖评级", system_prompt)

    def test_maximum_cost_bounds_multi_round_transcript_and_tool_payload(self) -> None:
        def large_tool(ctx: ToolContext, args: dict) -> ToolResult:
            return ToolResult(ok=True, content='"\\\x00' * 100_000)

        provider = ScriptedAgentProvider(
            [
                completed_response(tool_calls=(tool_call(name="get_holdings"),)),
                completed_response(content=claims_content()),
            ]
        )
        runtime = make_runtime(
            provider, (make_tool("get_holdings", handler=large_tool),)
        )
        maximum = runtime.maximum_cost_usd(make_intent())
        heartbeat_count = 0

        def heartbeat() -> None:
            nonlocal heartbeat_count
            heartbeat_count += 1

        result = runtime.run(
            actor_id="actor-1",
            intent=make_intent(),
            spend_before=Decimal("0"),
            heartbeat=heartbeat,
        )

        self.assertGreaterEqual(maximum, Decimal(result.cost_usd))
        tool_payload = provider.captured_requests[1]["messages"][3]["content"]
        self.assertLessEqual(len(tool_payload.encode("utf-8")), 16_000)
        self.assertIn("[truncated]", tool_payload)
        self.assertGreaterEqual(heartbeat_count, 6)

    def test_tool_call_id_is_bounded_for_cost_estimate(self) -> None:
        provider = ScriptedAgentProvider(
            [
                completed_response(
                    tool_calls=(
                        tool_call(
                            name="get_holdings",
                            call_id="x" * 257,
                        ),
                    )
                )
            ]
        )
        runtime = make_runtime(provider, (make_tool("get_holdings"),))

        with self.assertRaisesRegex(ProviderFailure, "provider_tool_calls_invalid"):
            runtime.run(
                actor_id="actor-1",
                intent=make_intent(),
                spend_before=Decimal("0"),
            )


if __name__ == "__main__":
    unittest.main()
