from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from backend.app.personal_workspace.agent.runtime import AgentRuntime
from backend.app.personal_workspace.agent.tools import build_agent_tools
from backend.app.personal_workspace.agent.workspace import AgentAnalysisWorkspace
from backend.app.personal_workspace.analysis import (
    AnalysisIntent,
    DEEPSEEK_MODEL,
    InMemoryAnalysisStore,
    ProviderFailure,
)
from backend.app.personal_workspace.contracts import PersonalActor
from backend.app.personal_workspace.portfolio import InMemoryPortfolioStore

from backend.tests.agent_test_helpers import (
    ScriptedAgentProvider,
    claims_content,
    completed_response,
    make_tool,
    tool_call,
)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
ACTOR = PersonalActor(actor_id="actor-1")


def make_workspace(
    provider: ScriptedAgentProvider,
    *,
    budget: str = "25",
    spend: str = "0",
    tools=None,
) -> AgentAnalysisWorkspace:
    store = InMemoryAnalysisStore()
    portfolio_store = InMemoryPortfolioStore()
    if tools is None:
        tools = build_agent_tools(portfolio_store=portfolio_store)
    runtime = AgentRuntime(
        provider=provider,
        tools=tools,
        model=DEEPSEEK_MODEL,
        clock=lambda: NOW,
        monthly_soft_budget_usd=Decimal(budget),
        monthly_spend_reader=lambda _actor_id, _now: Decimal("0"),
    )
    return AgentAnalysisWorkspace(
        store=store,
        runtime=runtime,
        tools=tools,
        skills=(),
        model=DEEPSEEK_MODEL,
        clock=lambda: NOW,
        monthly_soft_budget_usd=Decimal(budget),
        monthly_spend_reader=lambda actor, now: Decimal(spend),
    )


class AgentAnalysisWorkspaceTest(unittest.TestCase):
    def test_prepare_receipt_is_agent_shaped(self) -> None:
        workspace = make_workspace(ScriptedAgentProvider([]))
        receipt = workspace.prepare(
            ACTOR,
            AnalysisIntent(question="NVDA 怎么看？", subject_ids=("NVDA",)),
            idempotency_key=str(uuid4()),
        )
        self.assertEqual(receipt.provider, "deepseek-agent")
        self.assertEqual(receipt.evidence_ids, ())
        self.assertEqual(receipt.gaps, ())
        self.assertIn("user_question", receipt.included_fields)
        self.assertIn("get_holdings", receipt.included_fields)
        self.assertNotEqual(receipt.preview_sha256, "")
        self.assertTrue(Decimal(receipt.estimated_cost_usd) > 0)

    def test_start_without_frozen_evidence_succeeds(self) -> None:
        # agent 模式不再要求冻结证据（legacy 会抛 evidence_insufficient）
        workspace = make_workspace(ScriptedAgentProvider([]))
        receipt = workspace.prepare(
            ACTOR,
            AnalysisIntent(question="NVDA 怎么看？", subject_ids=("NVDA",)),
            idempotency_key=str(uuid4()),
        )
        run = workspace.start(
            ACTOR,
            draft_id=receipt.draft_id,
            preview_sha256=receipt.preview_sha256,
            idempotency_key=str(uuid4()),
        )
        self.assertEqual(run.status, "queued")
        self.assertEqual(run.provider, "deepseek-agent")

    def test_start_budget_blocked(self) -> None:
        workspace = make_workspace(
            ScriptedAgentProvider([]), budget="1", spend="1.5"
        )
        receipt = workspace.prepare(
            ACTOR,
            AnalysisIntent(question="NVDA 怎么看？", subject_ids=("NVDA",)),
            idempotency_key=str(uuid4()),
        )
        with self.assertRaisesRegex(ValueError, "budget_blocked"):
            workspace.start(
                ACTOR,
                draft_id=receipt.draft_id,
                preview_sha256=receipt.preview_sha256,
                idempotency_key=str(uuid4()),
            )

    def test_run_next_completes_with_tool_claims(self) -> None:
        provider = ScriptedAgentProvider(
            [
                completed_response(tool_calls=(tool_call(name="get_holdings"),)),
                completed_response(content=claims_content()),
            ]
        )
        workspace = make_workspace(provider)
        receipt = workspace.prepare(
            ACTOR,
            AnalysisIntent(question="NVDA 怎么看？", subject_ids=("NVDA",)),
            idempotency_key="idem-1",
        )
        workspace.start(
            ACTOR,
            draft_id=receipt.draft_id,
            preview_sha256=receipt.preview_sha256,
            idempotency_key="idem-2",
        )
        view = workspace.run_next(worker_id="worker-1")
        self.assertIsNotNone(view)
        self.assertEqual(view.status, "completed")
        self.assertEqual(len(view.claims), 4)
        self.assertEqual(view.actual_cost_usd, "0.0004")
        self.assertEqual(view.failure_code, None)

    def test_run_next_provider_failure_records_code(self) -> None:
        provider = ScriptedAgentProvider(
            [ProviderFailure("provider_rate_limited", retryable=True)] * 2
        )
        workspace = make_workspace(provider)
        receipt = workspace.prepare(
            ACTOR,
            AnalysisIntent(question="NVDA 怎么看？", subject_ids=("NVDA",)),
            idempotency_key="idem-1",
        )
        workspace.start(
            ACTOR,
            draft_id=receipt.draft_id,
            preview_sha256=receipt.preview_sha256,
            idempotency_key="idem-2",
        )
        view = workspace.run_next(worker_id="worker-1")
        self.assertEqual(view.status, "failed")
        self.assertEqual(view.failure_code, "provider_rate_limited")
        self.assertEqual(view.claims, ())

    def test_run_next_agent_round_exhaustion_fails(self) -> None:
        provider = ScriptedAgentProvider(
            [completed_response(tool_calls=(tool_call(name="get_holdings"),))] * 6
        )
        workspace = make_workspace(provider)
        receipt = workspace.prepare(
            ACTOR,
            AnalysisIntent(question="NVDA 怎么看？", subject_ids=("NVDA",)),
            idempotency_key="idem-1",
        )
        workspace.start(
            ACTOR,
            draft_id=receipt.draft_id,
            preview_sha256=receipt.preview_sha256,
            idempotency_key="idem-2",
        )
        view = workspace.run_next(worker_id="worker-1")
        self.assertEqual(view.status, "failed")
        self.assertEqual(view.failure_code, "agent_tool_rounds_exceeded")


if __name__ == "__main__":
    unittest.main()
