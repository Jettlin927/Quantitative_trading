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
from backend.app.personal_workspace.automatic_briefing import BriefingBudgetPolicy
from backend.app.personal_workspace.automatic_briefing_store import (
    ActiveAnalysisBudgetGuard,
    InMemoryAutomaticBriefingStore,
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
    daily_budget_guard=None,
    monthly_spend_reader=None,
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
        monthly_spend_reader=(
            (lambda actor_id, now: monthly_spend_reader(
                PersonalActor(actor_id=actor_id), now
            ))
            if monthly_spend_reader is not None
            else lambda _actor_id, _now: Decimal("0")
        ),
    )
    return AgentAnalysisWorkspace(
        store=store,
        runtime=runtime,
        tools=tools,
        skills=(),
        model=DEEPSEEK_MODEL,
        clock=lambda: NOW,
        monthly_soft_budget_usd=Decimal(budget),
        monthly_spend_reader=monthly_spend_reader or (
            lambda actor, now: Decimal(spend)
        ),
        daily_budget_guard=daily_budget_guard,
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
        self.assertEqual(view.question, "NVDA 怎么看？")
        self.assertEqual(view.subject_ids, ("NVDA",))
        self.assertEqual(view.planned_tools, ("get_holdings", "get_kline", "get_news"))
        self.assertEqual(view.provider_call_state, "completed")
        self.assertEqual(len(view.tool_events), 1)
        self.assertEqual(view.tool_events[0].tool_name, "get_holdings")
        self.assertEqual(view.tool_events[0].status, "completed")
        self.assertEqual(view.tool_events[0].evidence_ids, ("tool:get_holdings:0",))
        self.assertEqual(len(view.tool_evidence), 1)
        self.assertEqual(view.accounted_cost_usd, "0.0004")
        self.assertFalse(view.cancellable)

    def test_preflight_failure_never_starts_provider_or_charges_budget(self) -> None:
        class UnsafeProvider:
            available = True

            def validate_request(self, _request: dict) -> None:
                raise ProviderFailure("provider_request_unsafe", retryable=False)

            def create_response(self, _request: dict) -> dict:
                raise AssertionError("provider transport must not be reached")

        budget_store = InMemoryAutomaticBriefingStore()
        policy = BriefingBudgetPolicy(
            usd_to_cny=Decimal("7.20"),
            fx_snapshot="test",
            target_cny=Decimal("0.50"),
            soft_limit_cny=Decimal("1.00"),
            hard_limit_cny=Decimal("5.00"),
        ).store_policy()
        guard = ActiveAnalysisBudgetGuard(store=budget_store, policy=policy)
        workspace = make_workspace(
            UnsafeProvider(),
            daily_budget_guard=guard,
        )
        receipt = workspace.prepare(
            ACTOR,
            AnalysisIntent(question="NVDA 怎么看？", subject_ids=("NVDA",)),
            idempotency_key="idem-preflight-1",
        )
        queued = workspace.start(
            ACTOR,
            draft_id=receipt.draft_id,
            preview_sha256=receipt.preview_sha256,
            idempotency_key="idem-preflight-2",
        )

        view = workspace.run_next(worker_id="worker-1")

        self.assertEqual(view.status, "failed")
        self.assertEqual(view.failure_code, "provider_request_unsafe")
        self.assertEqual(view.provider_call_state, "not_started")
        self.assertEqual(view.accounted_cost_usd, "0")
        self.assertIsNone(
            budget_store.get(
                actor_id=ACTOR.actor_id,
                trigger_key=f"active-analysis:{queued.run_id}",
            )
        )

    def test_monthly_budget_drift_is_blocked_before_daily_reservation(self) -> None:
        spend = {"value": Decimal("0"), "reads": 0}

        def read_spend(_actor, _now):
            spend["reads"] += 1
            return spend["value"]

        budget_store = InMemoryAutomaticBriefingStore()
        guard = ActiveAnalysisBudgetGuard(
            store=budget_store,
            policy=BriefingBudgetPolicy(
                usd_to_cny=Decimal("7.20"),
                fx_snapshot="test",
                target_cny=Decimal("0.50"),
                soft_limit_cny=Decimal("1.00"),
                hard_limit_cny=Decimal("5.00"),
            ).store_policy(),
        )
        provider = ScriptedAgentProvider([])
        workspace = make_workspace(
            provider,
            budget="25",
            daily_budget_guard=guard,
            monthly_spend_reader=read_spend,
        )
        receipt = workspace.prepare(
            ACTOR,
            AnalysisIntent(question="NVDA 怎么看？", subject_ids=("NVDA",)),
            idempotency_key="idem-monthly-preflight-1",
        )
        queued = workspace.start(
            ACTOR,
            draft_id=receipt.draft_id,
            preview_sha256=receipt.preview_sha256,
            idempotency_key="idem-monthly-preflight-2",
        )
        spend["value"] = Decimal("25")

        view = workspace.run_next(worker_id="worker-1")

        self.assertEqual(view.failure_code, "budget_blocked")
        self.assertEqual(view.provider_call_state, "not_started")
        self.assertEqual(view.accounted_cost_usd, "0")
        self.assertEqual(provider.captured_requests, [])
        self.assertEqual(spend["reads"], 2)
        self.assertIsNone(
            budget_store.get(
                actor_id=ACTOR.actor_id,
                trigger_key=f"active-analysis:{queued.run_id}",
            )
        )

    def test_known_provider_cost_is_settled_when_final_claims_are_invalid(self) -> None:
        budget_store = InMemoryAutomaticBriefingStore()
        guard = ActiveAnalysisBudgetGuard(
            store=budget_store,
            policy=BriefingBudgetPolicy(
                usd_to_cny=Decimal("7.20"),
                fx_snapshot="test",
                target_cny=Decimal("0.50"),
                soft_limit_cny=Decimal("1.00"),
                hard_limit_cny=Decimal("5.00"),
            ).store_policy(),
        )
        workspace = make_workspace(
            ScriptedAgentProvider([completed_response(content="not-json")]),
            daily_budget_guard=guard,
        )
        receipt = workspace.prepare(
            ACTOR,
            AnalysisIntent(question="NVDA 怎么看？", subject_ids=("NVDA",)),
            idempotency_key="idem-known-cost-1",
        )
        workspace.start(
            ACTOR,
            draft_id=receipt.draft_id,
            preview_sha256=receipt.preview_sha256,
            idempotency_key="idem-known-cost-2",
        )

        view = workspace.run_next(worker_id="worker-1")

        self.assertEqual(view.status, "failed")
        self.assertEqual(view.failure_code, "provider_content_invalid_json")
        self.assertEqual(view.provider_call_state, "completed")
        self.assertEqual(view.actual_cost_usd, "0.0002")
        self.assertEqual(view.accounted_cost_usd, "0.0002")

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
        self.assertEqual(view.provider_call_state, "completed")
        self.assertEqual(view.actual_cost_usd, "0.0010")
        self.assertEqual(view.accounted_cost_usd, "0.0010")
        self.assertEqual(len(view.tool_events), 5)
        self.assertEqual(len(view.tool_evidence), 5)


if __name__ == "__main__":
    unittest.main()
