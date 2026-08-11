from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

from backend.app.personal_workspace.agent.completion_runtime import DeepSeekCompletionRuntime
from backend.app.personal_workspace.agent.domain_tools import (
    DomainToolContext,
    DomainToolRegistry,
    DomainToolResult,
    EvidenceEnvelope,
)
from backend.app.personal_workspace.agent.evidence import (
    EvidenceReadContext,
    EvidenceRecord,
    InMemoryEvidenceStore,
)
from backend.app.personal_workspace.agent.fact_news import (
    FACT_NEWS_AUTHORIZATION_SNAPSHOT_ID,
    FACT_NEWS_RETENTION,
    FACT_NEWS_SOURCE,
    FactNewsReadContext,
    NewsSourceSnapshot,
    RawFactNews,
)
from backend.app.personal_workspace.agent.today_tools import TodayDomainTools
from backend.app.personal_workspace.agent.workspace import AgentAnalysisWorkspace
from backend.app.personal_workspace.agent.workspace import ANALYSIS_TOOL_PERMISSIONS
from backend.app.personal_workspace.analysis import (
    AnalysisIntent,
    DEEPSEEK_MODEL,
    InMemoryAnalysisStore,
    ProviderFailure,
)
from backend.app.personal_workspace.automatic_briefing import BriefingBudgetPolicy
from backend.app.personal_workspace.automatic_briefing_store import (
    ActiveAnalysisBudgetGuard,
    BriefingProviderState,
    InMemoryAutomaticBriefingStore,
)
from backend.app.personal_workspace.contracts import PersonalActor
from backend.app.personal_workspace.portfolio import InMemoryPortfolioStore
from backend.app.personal_workspace.watchlist import (
    InMemoryInstrumentStateStore,
    InstrumentStateBook,
)
from backend.tests.agent_test_helpers import (
    ScriptedAgentProvider,
    claims_content,
    completed_response,
    tool_call,
)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
ACTOR = PersonalActor(actor_id="actor-1")
AUTHORIZATION = "analysis-test-v1"


def payload_hash(payload) -> str:
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def evidence_record(
    evidence_id: str,
    *,
    source: str,
    payload,
    authorized_fields: tuple[str, ...],
    content_sha256: str | None = None,
    allowed_purposes: frozenset[str] = frozenset({"domain_tool"}),
    expires_at: datetime | None = NOW + timedelta(hours=1),
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        logical_identity=f"identity:{evidence_id}",
        scope="actor",
        source=source,
        content_sha256=content_sha256 or payload_hash(payload),
        authorized_fields=authorized_fields,
        required_permissions=frozenset({"evidence:read"}),
        allowed_purposes=allowed_purposes,
        authorization_snapshot_id=AUTHORIZATION,
        observed_at=NOW,
        published_at=None,
        effective_at=None,
        available_from=NOW - timedelta(minutes=1),
        fetched_at=NOW,
        verified_at=NOW,
        expires_at=expires_at,
        persistence="encrypted_payload",
        payload=payload,
    )


def evidence_store(
    records: tuple[EvidenceRecord, ...],
    *,
    actor_id: str = ACTOR.actor_id,
    retention: str = "encrypted_payload",
    put_purpose: str = "domain_tool",
    put_now: datetime = NOW,
) -> InMemoryEvidenceStore:
    store = InMemoryEvidenceStore(
        retention_by_authorization={
            (record.source, record.authorization_snapshot_id): retention
            for record in records
        }
    )
    context = EvidenceReadContext(
        actor_id=actor_id,
        permissions=ANALYSIS_TOOL_PERMISSIONS,
        purpose=put_purpose,
        now=put_now,
    )
    for record in records:
        store.put(context, record)
    return store


def make_workspace(
    provider: ScriptedAgentProvider,
    *,
    budget: str = "25",
    spend: str = "0",
    daily_budget_guard=None,
    monthly_spend_reader=None,
    holdings_handler=None,
    ledger=None,
) -> AgentAnalysisWorkspace:
    store = InMemoryAnalysisStore()
    calls = {"count": 0}

    holdings_payload = {"holdings": [], "usd_cash": "0"}

    def today(_context, _arguments):
        calls["count"] += 1
        return DomainToolResult.success(
            data={"holdings": [], "count": 0, "usd_cash": "0"},
            evidence=(
                EvidenceEnvelope(
                    evidence_id=f"ledger:holdings:{calls['count']}",
                    source="portfolio",
                    as_of=NOW,
                    content_sha256=payload_hash(holdings_payload),
                    authorized_fields=("holdings", "usd_cash"),
                ),
            ),
        )

    registry = DomainToolRegistry(
        handlers={
            "get_today_context": holdings_handler or today,
            "get_symbol_dossier": lambda _context, arguments: DomainToolResult.success(
                data={
                    "symbol": arguments["symbol"],
                    "adjustment": "raw",
                    "as_of": NOW.isoformat(),
                    "source_health": "ok",
                    "bars": [],
                    "count": 0,
                },
                evidence=(
                    EvidenceEnvelope(
                        "ledger:kline:1",
                        "market",
                        NOW,
                        payload_hash({"bars": []}),
                        ("bars",),
                    ),
                ),
            ),
            "search_market_news": lambda _context, _arguments: DomainToolResult.success(
                data={"items": [], "count": 0, "note": "synthetic"},
                evidence=(
                    EvidenceEnvelope(
                        "ledger:news:1",
                        "news",
                        NOW,
                        payload_hash({"items": []}),
                        ("items",),
                    ),
                ),
            ),
        }
    )
    tools = registry.projected_definitions(
        permissions=frozenset(
            {"portfolio:read", "market:read", "news:read", "evidence:read"}
        ),
        names=("get_holdings", "get_kline", "get_news"),
    )
    if ledger is None:
        ledger = evidence_store(
            tuple(
                evidence_record(
                    f"ledger:holdings:{index}",
                    source="portfolio",
                    payload=holdings_payload,
                    authorized_fields=("holdings", "usd_cash"),
                )
                for index in range(1, 6)
            )
            + (
                evidence_record(
                    "ledger:kline:1",
                    source="market",
                    payload={"bars": []},
                    authorized_fields=("bars",),
                ),
                evidence_record(
                    "ledger:news:1",
                    source="news",
                    payload={"items": []},
                    authorized_fields=("items",),
                ),
            )
        )
    return AgentAnalysisWorkspace(
        store=store,
        runtime=DeepSeekCompletionRuntime(provider=provider, clock=lambda: NOW),
        domain_tools=registry,
        evidence_ledger=ledger,
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
    def _run_holdings_analysis(self, ledger) -> tuple[AgentAnalysisWorkspace, object]:
        provider = ScriptedAgentProvider(
            [
                completed_response(
                    tool_calls=(tool_call(name="get_holdings", call_id="call-1"),)
                ),
                completed_response(
                    content=claims_content(evidence_id="ledger:holdings:1")
                ),
            ]
        )
        return self._run_holdings_analysis_with_provider(provider, ledger)

    def _run_holdings_analysis_with_provider(
        self, provider, ledger
    ) -> tuple[AgentAnalysisWorkspace, object]:
        workspace = make_workspace(provider, ledger=ledger)
        receipt = workspace.prepare(
            ACTOR,
            AnalysisIntent(question="NVDA 怎么看？", subject_ids=("NVDA",)),
            idempotency_key=str(uuid4()),
        )
        workspace.start(
            ACTOR,
            draft_id=receipt.draft_id,
            preview_sha256=receipt.preview_sha256,
            idempotency_key=str(uuid4()),
        )
        return workspace, workspace.run_next(worker_id="worker-1")

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
                completed_response(content=claims_content(evidence_id="ledger:holdings:1")),
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
        self.assertEqual(view.tool_events[0].evidence_ids, ("ledger:holdings:1",))
        self.assertEqual(len(view.tool_evidence), 1)
        self.assertEqual(view.accounted_cost_usd, "0.0004")
        self.assertFalse(view.cancellable)
        first_request = provider.captured_requests[0]
        self.assertEqual(
            json.loads(first_request["messages"][1]["content"]),
            {"question": "NVDA 怎么看？", "subject_ids": ["NVDA"]},
        )
        self.assertEqual(
            [item["function"]["name"] for item in first_request["tools"]],
            ["get_holdings", "get_kline", "get_news"],
        )

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

    def test_refusal_with_usage_persists_known_cost_in_run_and_budget(self) -> None:
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
        provider = ScriptedAgentProvider(
            [
                {
                    "status": "refusal",
                    "message": {"content": None, "tool_calls": ()},
                    "usage": {
                        "input_tokens": 800,
                        "output_tokens": 400,
                        "cache_hit_tokens": 300,
                        "cache_miss_tokens": 500,
                    },
                    "cost_usd": "0.0002",
                }
            ]
        )
        workspace = make_workspace(provider, daily_budget_guard=guard)
        receipt = workspace.prepare(
            ACTOR,
            AnalysisIntent(question="NVDA 怎么看？", subject_ids=("NVDA",)),
            idempotency_key="idem-refusal-known-1",
        )
        queued = workspace.start(
            ACTOR,
            draft_id=receipt.draft_id,
            preview_sha256=receipt.preview_sha256,
            idempotency_key="idem-refusal-known-2",
        )

        view = workspace.run_next(worker_id="worker-1")
        budget = budget_store.get(
            actor_id=ACTOR.actor_id,
            trigger_key=f"active-analysis:{queued.run_id}",
        )

        self.assertEqual(view.failure_code, "provider_refusal")
        self.assertEqual(view.provider_call_state, "completed")
        self.assertEqual(view.actual_cost_usd, "0.0002")
        self.assertEqual(view.accounted_cost_usd, "0.0002")
        self.assertEqual(view.usage.input_tokens, 800)
        self.assertEqual(budget.provider_state, BriefingProviderState.COMPLETED)
        self.assertEqual(budget.actual_cost_usd, Decimal("0.0002"))
        self.assertEqual(budget.accounted_cost_usd, Decimal("0.0002"))

    def test_refusal_without_usage_is_outcome_unknown(self) -> None:
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
            ScriptedAgentProvider(
                [
                    {
                        "status": "refusal",
                        "message": {"content": None, "tool_calls": ()},
                    }
                ]
            ),
            daily_budget_guard=guard,
        )
        receipt = workspace.prepare(
            ACTOR,
            AnalysisIntent(question="NVDA 怎么看？", subject_ids=("NVDA",)),
            idempotency_key="idem-refusal-unknown-1",
        )
        queued = workspace.start(
            ACTOR,
            draft_id=receipt.draft_id,
            preview_sha256=receipt.preview_sha256,
            idempotency_key="idem-refusal-unknown-2",
        )

        view = workspace.run_next(worker_id="worker-1")
        budget = budget_store.get(
            actor_id=ACTOR.actor_id,
            trigger_key=f"active-analysis:{queued.run_id}",
        )

        self.assertEqual(view.failure_code, "provider_refusal")
        self.assertEqual(view.provider_call_state, "outcome_unknown")
        self.assertIsNone(view.actual_cost_usd)
        self.assertIsNone(view.usage)
        self.assertEqual(budget.provider_state, BriefingProviderState.OUTCOME_UNKNOWN)
        self.assertIsNone(budget.actual_cost_usd)

    def test_claims_cannot_cite_evidence_outside_runtime_subset(self) -> None:
        provider = ScriptedAgentProvider(
            [
                completed_response(
                    tool_calls=(tool_call(name="get_holdings", call_id="call-1"),)
                ),
                completed_response(
                    content=claims_content(evidence_id="ledger:not-returned")
                ),
            ]
        )
        workspace = make_workspace(provider)
        receipt = workspace.prepare(
            ACTOR,
            AnalysisIntent(question="NVDA 怎么看？", subject_ids=("NVDA",)),
            idempotency_key="idem-invalid-evidence-1",
        )
        workspace.start(
            ACTOR,
            draft_id=receipt.draft_id,
            preview_sha256=receipt.preview_sha256,
            idempotency_key="idem-invalid-evidence-2",
        )

        view = workspace.run_next(worker_id="worker-1")

        self.assertEqual(view.status, "failed")
        self.assertEqual(view.failure_code, "claim_evidence_invalid")
        self.assertEqual(
            view.tool_events[0].evidence_ids, ("ledger:holdings:1",)
        )
        self.assertEqual(
            view.tool_evidence[0].evidence_id, "ledger:holdings:1"
        )

    def test_persisted_tool_evidence_excerpts_are_authorized_per_envelope(self) -> None:
        holdings_payload = {
            "holdings": [
                {
                    "symbol": "NVDA",
                    "name": "SAFE-1" + "A" * 300,
                    "quantity": "1",
                    "average_cost": "100",
                    "currency": "USD",
                    "state": "active",
                }
            ]
        }
        cash_payload = {"usd_cash": "SECRET-2"}

        def holdings(_context, _arguments):
            return DomainToolResult.success(
                data={
                    **holdings_payload,
                    "count": 1,
                    "usd_cash": "SECRET-2",
                },
                evidence=(
                    EvidenceEnvelope(
                        "ledger:item:1",
                        "source-1",
                        NOW,
                        payload_hash(holdings_payload),
                        ("holdings",),
                    ),
                    EvidenceEnvelope(
                        "ledger:item:2",
                        "source-2",
                        NOW,
                        payload_hash(cash_payload),
                        ("usd_cash",),
                    ),
                ),
            )

        provider = ScriptedAgentProvider(
            [
                completed_response(
                    tool_calls=(tool_call(name="get_holdings", call_id="call-1"),)
                ),
                completed_response(content=claims_content(evidence_id="ledger:item:1")),
            ]
        )
        workspace = make_workspace(
            provider,
            holdings_handler=holdings,
            ledger=evidence_store(
                (
                    evidence_record(
                        "ledger:item:1",
                        source="source-1",
                        payload=holdings_payload,
                        authorized_fields=("holdings",),
                    ),
                    evidence_record(
                        "ledger:item:2",
                        source="source-2",
                        payload=cash_payload,
                        authorized_fields=("usd_cash",),
                    ),
                )
            ),
        )
        receipt = workspace.prepare(
            ACTOR,
            AnalysisIntent(question="NVDA 怎么看？", subject_ids=("NVDA",)),
            idempotency_key="idem-authorized-excerpt-1",
        )
        queued = workspace.start(
            ACTOR,
            draft_id=receipt.draft_id,
            preview_sha256=receipt.preview_sha256,
            idempotency_key="idem-authorized-excerpt-2",
        )

        workspace.run_next(worker_id="worker-1")
        readback = workspace.observe(ACTOR, queued.run_id)
        evidence = {item.evidence_id: item.excerpt for item in readback.tool_evidence}

        self.assertIn("SAFE-1", evidence["ledger:item:1"])
        self.assertNotIn("SECRET-2", evidence["ledger:item:1"])
        self.assertLessEqual(len(evidence["ledger:item:1"]), 200)
        self.assertIn("SECRET-2", evidence["ledger:item:2"])
        self.assertNotIn("SAFE-1", evidence["ledger:item:2"])

    def test_tool_evidence_freeze_failures_are_stable_and_keep_known_cost(self) -> None:
        expected_payload = {"holdings": [], "usd_cash": "0"}
        expected = evidence_record(
            "ledger:holdings:1",
            source="portfolio",
            payload=expected_payload,
            authorized_fields=("holdings", "usd_cash"),
        )
        mismatched = evidence_record(
            "ledger:holdings:1",
            source="portfolio",
            payload={"holdings": [], "usd_cash": "1"},
            authorized_fields=("holdings", "usd_cash"),
        )
        cases = {
            "expired": evidence_store(
                (evidence_record(
                    "ledger:holdings:1",
                    source="portfolio",
                    payload=expected_payload,
                    authorized_fields=("holdings", "usd_cash"),
                    expires_at=NOW - timedelta(seconds=1),
                ),)
            ),
            "metadata-only": evidence_store((expected,), retention="metadata_only"),
            "purpose-denied": evidence_store(
                (evidence_record(
                    "ledger:holdings:1",
                    source="portfolio",
                    payload=expected_payload,
                    authorized_fields=("holdings", "usd_cash"),
                    allowed_purposes=frozenset({"display"}),
                ),),
                put_purpose="display",
            ),
            "actor-denied": evidence_store((expected,), actor_id="actor-2"),
            "not-found": InMemoryEvidenceStore(retention_by_authorization={}),
            "identity-mismatch": evidence_store((mismatched,)),
        }

        for name, ledger in cases.items():
            with self.subTest(name=name):
                _workspace, view = self._run_holdings_analysis(ledger)
                self.assertEqual(view.status, "failed")
                self.assertEqual(
                    view.failure_code, "tool_evidence_freeze_failed"
                )
                self.assertEqual(view.provider_call_state, "completed")
                self.assertEqual(view.actual_cost_usd, "0.0004")
                self.assertEqual(view.accounted_cost_usd, "0.0004")
                self.assertEqual(view.usage.input_tokens, 1600)
                self.assertEqual(len(view.tool_events), 1)
                self.assertEqual(view.tool_events[0].status, "completed")
                self.assertEqual(view.tool_evidence, ())
                self.assertEqual(view.claims, ())

    def test_tool_evidence_freeze_rejects_incomplete_ledger_readback(self) -> None:
        class IncompleteReadbackLedger:
            def freeze(self, _context, _evidence_ids):
                return ()

        _workspace, view = self._run_holdings_analysis(IncompleteReadbackLedger())

        self.assertEqual(view.status, "failed")
        self.assertEqual(view.failure_code, "tool_evidence_freeze_failed")
        self.assertEqual(view.actual_cost_usd, "0.0004")
        self.assertEqual(len(view.tool_events), 1)
        self.assertEqual(view.tool_evidence, ())

    def test_today_domain_tools_news_runtime_freezes_ledger_truth(self) -> None:
        class SyntheticNewsSource:
            def read(self, *, context: FactNewsReadContext, now: datetime):
                return NewsSourceSnapshot(
                    items=(
                        RawFactNews(
                            title="英伟达供应链更新",
                            url="https://wire.example/nvda-update",
                            published_at=NOW - timedelta(hours=1),
                            fetched_at=NOW - timedelta(minutes=5),
                            summary="结构化来源摘要。",
                            source="Synthetic Wire",
                            source_type="structured_news",
                            sector="semi",
                            related_symbols=("NVDA",),
                        ),
                        RawFactNews(
                            title="英伟达产品节奏更新",
                            url="https://wire.example/nvda-product-update",
                            published_at=NOW - timedelta(minutes=30),
                            fetched_at=NOW - timedelta(minutes=4),
                            summary="另一篇结构化来源摘要。",
                            source="Another Synthetic Wire",
                            source_type="structured_news",
                            sector="ai",
                            related_symbols=("NVDA",),
                        ),
                    ),
                    fetched_at=NOW - timedelta(minutes=5),
                )

        ledger = InMemoryEvidenceStore(
            retention_by_authorization={
                (
                    FACT_NEWS_SOURCE,
                    FACT_NEWS_AUTHORIZATION_SNAPSHOT_ID,
                ): FACT_NEWS_RETENTION
            }
        )
        watchlist = InstrumentStateBook(
            store=InMemoryInstrumentStateStore(),
            holding_states_reader=lambda _actor_id: {},
        )
        registry = TodayDomainTools(
            portfolio_store=InMemoryPortfolioStore(),
            watchlist=watchlist,
            news_source=SyntheticNewsSource(),
            evidence_ledger=ledger,
        ).registry()
        direct = registry.invoke(
            "get_news",
            context=DomainToolContext(
                actor_id=ACTOR.actor_id,
                granted_permissions=ANALYSIS_TOOL_PERMISSIONS,
                clock=lambda: NOW,
            ),
            arguments={"symbols": ["NVDA"], "limit": 8},
        )
        evidence_id = direct.evidence[0].evidence_id
        provider = ScriptedAgentProvider(
            [
                completed_response(
                    tool_calls=(
                        tool_call(
                            name="get_news",
                            arguments={"symbols": ["NVDA"], "limit": 8},
                            call_id="call-news",
                        ),
                    )
                ),
                completed_response(content=claims_content(evidence_id=evidence_id)),
            ]
        )
        tools = registry.projected_definitions(
            permissions=ANALYSIS_TOOL_PERMISSIONS,
            names=("get_holdings", "get_kline", "get_news"),
        )
        workspace = AgentAnalysisWorkspace(
            store=InMemoryAnalysisStore(),
            runtime=DeepSeekCompletionRuntime(provider=provider, clock=lambda: NOW),
            domain_tools=registry,
            evidence_ledger=ledger,
            tools=tools,
            skills=(),
            model=DEEPSEEK_MODEL,
            clock=lambda: NOW,
            monthly_soft_budget_usd=Decimal("25"),
            monthly_spend_reader=lambda _actor, _now: Decimal("0"),
        )
        receipt = workspace.prepare(
            ACTOR,
            AnalysisIntent(question="NVDA 新闻怎么看？", subject_ids=("NVDA",)),
            idempotency_key=str(uuid4()),
        )
        workspace.start(
            ACTOR,
            draft_id=receipt.draft_id,
            preview_sha256=receipt.preview_sha256,
            idempotency_key=str(uuid4()),
        )

        view = workspace.run_next(worker_id="worker-1")

        frozen = ledger.freeze(
            EvidenceReadContext(
                actor_id=ACTOR.actor_id,
                permissions=ANALYSIS_TOOL_PERMISSIONS,
                purpose="domain_tool",
                now=NOW,
            ),
            (evidence_id,),
        )[0]
        self.assertEqual(view.status, "completed")
        self.assertEqual(view.tool_evidence[0].source, FACT_NEWS_SOURCE)
        self.assertNotEqual(view.tool_evidence[0].source, direct.evidence[0].source)
        self.assertEqual(
            view.tool_evidence[0].field, frozen.authorized_fields[0]
        )
        self.assertEqual(view.tool_evidence[0].as_of, frozen.published_at)
        self.assertLessEqual(len(view.tool_evidence[0].excerpt), 200)
        tool_message = next(
            message
            for message in provider.captured_requests[1]["messages"]
            if message["role"] == "tool"
        )
        feedback = json.loads(tool_message["content"])
        item_mapping = {
            item["evidence_id"]: item["url"] for item in feedback["data"]["items"]
        }
        direct_mapping = {
            item["evidence_id"]: item["url"] for item in direct.data["items"]
        }
        self.assertEqual(item_mapping, direct_mapping)
        self.assertEqual(
            set(item_mapping), set(feedback["evidence_ids"])
        )

    def test_cancelled_and_failed_results_keep_original_status_when_freeze_fails(self) -> None:
        expired = evidence_store(
            (
                evidence_record(
                    "ledger:holdings:1",
                    source="portfolio",
                    payload={"holdings": [], "usd_cash": "0"},
                    authorized_fields=("holdings", "usd_cash"),
                    expires_at=NOW - timedelta(seconds=1),
                ),
            )
        )
        holder = {"calls": 0}

        class CancellingProvider:
            available = True

            def create_response(self, _request):
                holder["calls"] += 1
                if holder["calls"] == 1:
                    return completed_response(
                        tool_calls=(
                            tool_call(name="get_holdings", call_id="call-1"),
                        )
                    )
                holder["workspace"].cancel(ACTOR, holder["run_id"])
                return completed_response(
                    content=claims_content(evidence_id="ledger:holdings:1")
                )

        workspace = make_workspace(CancellingProvider(), ledger=expired)
        holder["workspace"] = workspace
        receipt = workspace.prepare(
            ACTOR,
            AnalysisIntent(question="NVDA 怎么看？", subject_ids=("NVDA",)),
            idempotency_key=str(uuid4()),
        )
        queued = workspace.start(
            ACTOR,
            draft_id=receipt.draft_id,
            preview_sha256=receipt.preview_sha256,
            idempotency_key=str(uuid4()),
        )
        holder["run_id"] = queued.run_id

        cancelled = workspace.run_next(worker_id="worker-1")

        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(cancelled.failure_code, "cancelled_by_user")
        self.assertEqual(cancelled.actual_cost_usd, "0.0004")
        self.assertEqual(len(cancelled.tool_events), 1)
        self.assertEqual(cancelled.tool_evidence, ())

        refusal_provider = ScriptedAgentProvider(
            [
                completed_response(
                    tool_calls=(tool_call(name="get_holdings", call_id="call-1"),)
                ),
                {
                    "status": "refusal",
                    "message": {"content": None, "tool_calls": ()},
                    "usage": {
                        "input_tokens": 800,
                        "output_tokens": 400,
                        "cache_hit_tokens": 300,
                        "cache_miss_tokens": 500,
                    },
                    "cost_usd": "0.0002",
                },
            ]
        )
        _workspace, failed = self._run_holdings_analysis_with_provider(
            refusal_provider, expired
        )

        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.failure_code, "provider_refusal")
        self.assertEqual(failed.actual_cost_usd, "0.0004")
        self.assertEqual(len(failed.tool_events), 1)
        self.assertEqual(failed.tool_evidence, ())

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

    def test_running_analysis_honors_user_cancellation_between_rounds(self) -> None:
        holder = {}

        class CancellingProvider:
            available = True

            def create_response(self, _request):
                holder["workspace"].cancel(ACTOR, holder["run_id"])
                return completed_response(
                    tool_calls=(tool_call(name="get_holdings", call_id="call-1"),)
                )

        workspace = make_workspace(CancellingProvider())
        holder["workspace"] = workspace
        receipt = workspace.prepare(
            ACTOR,
            AnalysisIntent(question="NVDA 怎么看？", subject_ids=("NVDA",)),
            idempotency_key="idem-cancel-runtime-1",
        )
        queued = workspace.start(
            ACTOR,
            draft_id=receipt.draft_id,
            preview_sha256=receipt.preview_sha256,
            idempotency_key="idem-cancel-runtime-2",
        )
        holder["run_id"] = queued.run_id

        view = workspace.run_next(worker_id="worker-1")

        self.assertEqual(view.status, "cancelled")
        self.assertEqual(view.failure_code, "cancelled_by_user")
        self.assertEqual(view.tool_events, ())
        self.assertEqual(view.provider_call_state, "completed")
        self.assertEqual(view.actual_cost_usd, "0.0002")
        self.assertEqual(view.accounted_cost_usd, "0.0002")
        self.assertEqual(view.usage.input_tokens, 800)
        self.assertEqual(workspace.observe(ACTOR, queued.run_id), view)

    def test_cancel_before_provider_call_has_known_zero_cost(self) -> None:
        budget_store = InMemoryAutomaticBriefingStore()
        policy = BriefingBudgetPolicy(
            usd_to_cny=Decimal("7.20"),
            fx_snapshot="test",
            target_cny=Decimal("0.50"),
            soft_limit_cny=Decimal("1.00"),
            hard_limit_cny=Decimal("5.00"),
        ).store_policy()
        holder = {}

        class CancellingGuard(ActiveAnalysisBudgetGuard):
            def start_call(self, **kwargs):
                reservation = super().start_call(**kwargs)
                holder["workspace"].cancel(ACTOR, kwargs["run_id"])
                return reservation

        provider = ScriptedAgentProvider([])
        workspace = make_workspace(
            provider,
            daily_budget_guard=CancellingGuard(store=budget_store, policy=policy),
        )
        holder["workspace"] = workspace
        receipt = workspace.prepare(
            ACTOR,
            AnalysisIntent(question="NVDA 怎么看？", subject_ids=("NVDA",)),
            idempotency_key="idem-cancel-before-provider-1",
        )
        queued = workspace.start(
            ACTOR,
            draft_id=receipt.draft_id,
            preview_sha256=receipt.preview_sha256,
            idempotency_key="idem-cancel-before-provider-2",
        )

        view = workspace.run_next(worker_id="worker-1")

        self.assertEqual(view.status, "cancelled")
        self.assertEqual(view.provider_call_state, "not_started")
        self.assertEqual(view.actual_cost_usd, "0")
        self.assertEqual(view.accounted_cost_usd, "0")
        self.assertIsNone(view.usage)
        self.assertEqual(provider.captured_requests, [])
        budget = budget_store.get(
            actor_id=ACTOR.actor_id,
            trigger_key=f"active-analysis:{queued.run_id}",
        )
        self.assertEqual(budget.provider_state, BriefingProviderState.COMPLETED)
        self.assertEqual(budget.actual_cost_usd, Decimal("0"))
        self.assertEqual(budget.accounted_cost_usd, Decimal("0"))

    def test_cancel_after_tool_round_persists_usage_cost_and_tool_projection(self) -> None:
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
        holder = {"calls": 0}

        class CancellingAfterToolProvider:
            available = True

            def create_response(self, _request):
                holder["calls"] += 1
                if holder["calls"] == 1:
                    return completed_response(
                        tool_calls=(
                            tool_call(name="get_holdings", call_id="call-1"),
                        )
                    )
                holder["workspace"].cancel(ACTOR, holder["run_id"])
                return completed_response(content=claims_content(
                    evidence_id="ledger:holdings:1"
                ))

        workspace = make_workspace(
            CancellingAfterToolProvider(), daily_budget_guard=guard
        )
        holder["workspace"] = workspace
        receipt = workspace.prepare(
            ACTOR,
            AnalysisIntent(question="NVDA 怎么看？", subject_ids=("NVDA",)),
            idempotency_key="idem-cancel-after-tool-1",
        )
        queued = workspace.start(
            ACTOR,
            draft_id=receipt.draft_id,
            preview_sha256=receipt.preview_sha256,
            idempotency_key="idem-cancel-after-tool-2",
        )
        holder["run_id"] = queued.run_id

        view = workspace.run_next(worker_id="worker-1")
        readback = workspace.observe(ACTOR, queued.run_id)

        self.assertEqual(view.status, "cancelled")
        self.assertEqual(view.failure_code, "cancelled_by_user")
        self.assertEqual(view.provider_call_state, "completed")
        self.assertEqual(view.actual_cost_usd, "0.0004")
        self.assertEqual(view.accounted_cost_usd, "0.0004")
        self.assertEqual(view.usage.input_tokens, 1600)
        self.assertEqual(len(view.tool_events), 1)
        self.assertEqual(view.tool_events[0].status, "completed")
        self.assertEqual(view.tool_events[0].evidence_ids, ("ledger:holdings:1",))
        self.assertEqual(len(view.tool_evidence), 1)
        self.assertEqual(view.tool_evidence[0].evidence_id, "ledger:holdings:1")
        self.assertEqual(readback, view)
        budget = budget_store.get(
            actor_id=ACTOR.actor_id,
            trigger_key=f"active-analysis:{queued.run_id}",
        )
        self.assertEqual(budget.provider_state, BriefingProviderState.COMPLETED)
        self.assertEqual(budget.actual_cost_usd, Decimal("0.0004"))
        self.assertEqual(budget.accounted_cost_usd, Decimal("0.0004"))

    def test_run_next_agent_round_exhaustion_fails(self) -> None:
        provider = ScriptedAgentProvider(
            [
                completed_response(
                    tool_calls=(
                        tool_call(name="get_holdings", call_id=f"call-{index}"),
                    )
                )
                for index in range(1, 7)
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
        self.assertEqual(view.status, "failed")
        self.assertEqual(view.failure_code, "agent_tool_rounds_exceeded")
        self.assertEqual(view.provider_call_state, "completed")
        self.assertEqual(view.actual_cost_usd, "0.0010")
        self.assertEqual(view.accounted_cost_usd, "0.0010")
        self.assertEqual(len(view.tool_events), 5)
        self.assertEqual(len(view.tool_evidence), 5)


if __name__ == "__main__":
    unittest.main()
