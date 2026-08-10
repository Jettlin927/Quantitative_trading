from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
import unittest

from backend.app.personal_workspace.agent.ai_runtime import (
    AIRuntimeCapabilities,
    RuntimeEvent,
    RuntimeResult,
    RuntimeUsage,
)
from backend.app.personal_workspace.agent.domain_tools import (
    DomainToolContext,
    DomainToolRegistry,
    DomainToolResult,
    EvidenceEnvelope,
    ToolGap,
)
from backend.app.personal_workspace.automatic_briefing import (
    AutomaticBriefingAutomation,
    AutomaticBriefingCoordinator,
    BriefingBudgetPolicy,
    BriefingTrigger,
)
from backend.app.personal_workspace.automatic_briefing_store import (
    BriefingProviderState,
    InMemoryAutomaticBriefingStore,
)
from backend.app.personal_workspace.contracts import PersonalActor


NOW = datetime(2026, 8, 3, 12, tzinfo=timezone.utc)


def evidence(evidence_id: str, source: str = "synthetic") -> EvidenceEnvelope:
    return EvidenceEnvelope(
        evidence_id=evidence_id,
        source=source,
        as_of=NOW,
        content_sha256="a" * 64,
        authorized_fields=("summary",),
    )


def claims_text(evidence_id: str) -> str:
    claims = []
    for kind in ("confirmed_fact", "inference", "conditional_scenario", "unknown"):
        claims.append(
            {
                "kind": kind,
                "statement": f"{kind} statement",
                "evidence_ids": [evidence_id] if kind != "unknown" else [],
                "opposing_evidence_ids": [],
                "assumptions": ["synthetic"] if kind != "confirmed_fact" else [],
                "horizon": "today",
                "invalidation_conditions": ["new evidence"],
            }
        )
    return json.dumps({"claims": claims})


class RecordingRuntime:
    capabilities = AIRuntimeCapabilities(
        runtime_kind="completion",
        client_tools=False,
        hosted_tools=False,
        cancellation=True,
        usage=True,
    )

    def __init__(self, output: str) -> None:
        self.output = output
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        return RuntimeResult.completed(
            events=(
                RuntimeEvent(type="run_started"),
                RuntimeEvent(type="output_completed", text=self.output),
            ),
            usage=RuntimeUsage(100, 50, 20, 0, Decimal("0.001")),
        )


class AutomaticBriefingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.calls = []

        def today(context: DomainToolContext, arguments):
            self.calls.append(("get_today_context", arguments))
            return DomainToolResult.success(
                data={
                    "active_holding_symbols": ["NVDA"],
                    "followed_symbols": ["TSLA", "NVDA"],
                },
                evidence=(evidence("today:1", "personal_portfolio"),),
            )

        def dossier(context: DomainToolContext, arguments):
            self.calls.append(("get_symbol_dossier", arguments))
            symbol = arguments["symbol"]
            return DomainToolResult.success(
                data={"symbol": symbol, "summary": "synthetic"},
                evidence=(evidence(f"dossier:{symbol}"),),
            )

        def get_evidence(context: DomainToolContext, arguments):
            self.calls.append(("get_evidence", arguments))
            confirmation_state = (
                "source_summary_unconfirmed"
                if arguments["evidence_id"] == "news:unconfirmed"
                else "confirmed"
            )
            return DomainToolResult.success(
                data={
                    "summary": "event fact",
                    "confirmation_state": confirmation_state,
                },
                evidence=(evidence(arguments["evidence_id"], "structured_news"),),
            )

        self.registry = DomainToolRegistry(
            handlers={
                "get_today_context": today,
                "get_symbol_dossier": dossier,
                "get_evidence": get_evidence,
            }
        )
        self.store = InMemoryAutomaticBriefingStore()
        self.runtime = RecordingRuntime(claims_text("today:1"))
        self.coordinator = AutomaticBriefingCoordinator(
            tools=self.registry,
            runtime=self.runtime,
            store=self.store,
            policy=BriefingBudgetPolicy(
                usd_to_cny=Decimal("7.20"),
                fx_snapshot="static-2026-08-10",
            ),
            clock=lambda: NOW,
        )

    def test_premarket_uses_fixed_bounded_recipe_and_one_completion(self) -> None:
        result = self.coordinator.run(
            PersonalActor("local-owner"),
            BriefingTrigger(
                kind="premarket",
                market_date=date(2026, 8, 3),
                as_of=NOW,
            ),
            worker_id="worker-1",
        )

        self.assertEqual(result.provider_state, BriefingProviderState.COMPLETED)
        self.assertEqual(
            [name for name, _arguments in self.calls],
            [
                "get_today_context",
                "get_evidence",
                "get_symbol_dossier",
                "get_evidence",
                "get_symbol_dossier",
                "get_evidence",
            ],
        )
        self.assertNotIn("search_web_evidence", repr(self.calls))
        self.assertEqual(len(self.runtime.requests), 1)
        self.assertEqual(self.runtime.requests[0].tools, ())
        self.assertEqual(self.runtime.requests[0].hosted_tools, ())
        self.assertEqual(
            {
                claim["kind"]
                for claim in result.private_payload["claims"]
            },
            {"confirmed_fact", "inference", "conditional_scenario", "unknown"},
        )
        self.assertEqual(result.private_payload["usage"]["cache_hit_tokens"], 20)

    def test_same_event_is_charged_once_across_symbol_order_and_refresh(self) -> None:
        first = BriefingTrigger(
            kind="intraday_event",
            market_date=date(2026, 8, 3),
            as_of=NOW,
            source_event_id="event-stable-1",
            evidence_id="news:1",
            subject_ids=("TSLA", "NVDA"),
        )
        second = BriefingTrigger(
            kind="intraday_event",
            market_date=date(2026, 8, 4),
            as_of=NOW.replace(minute=30),
            source_event_id="event-stable-1",
            evidence_id="news:changed-content",
            subject_ids=("NVDA", "TSLA"),
        )
        self.runtime.output = claims_text("news:1")

        one = self.coordinator.run(PersonalActor("local-owner"), first, worker_id="w1")
        two = self.coordinator.run(PersonalActor("local-owner"), second, worker_id="w2")

        self.assertEqual(one.provider_state, BriefingProviderState.COMPLETED)
        self.assertEqual(two, one)
        self.assertEqual(len(self.runtime.requests), 1)

    def test_no_authorized_evidence_persists_gap_without_model_call(self) -> None:
        registry = DomainToolRegistry(
            handlers={
                "get_today_context": lambda context, arguments: DomainToolResult.unavailable(
                    "source_unavailable", "portfolio"
                )
            }
        )
        coordinator = AutomaticBriefingCoordinator(
            tools=registry,
            runtime=self.runtime,
            store=self.store,
            policy=self.coordinator.policy,
            clock=lambda: NOW,
        )

        result = coordinator.run(
            PersonalActor("local-owner"),
            BriefingTrigger("postmarket", date(2026, 8, 3), NOW),
            worker_id="w1",
        )

        self.assertEqual(result.provider_state, BriefingProviderState.COMPLETED)
        self.assertEqual(result.failure_code, "evidence_insufficient")
        self.assertEqual(self.runtime.requests, [])
        self.assertEqual(
            result.private_payload["gaps"][0]["code"], "source_unavailable"
        )

    def test_claims_must_have_exactly_one_of_each_kind(self) -> None:
        malformed = json.loads(claims_text("today:1"))
        malformed["claims"].pop()
        self.runtime.output = json.dumps(malformed)

        result = self.coordinator.run(
            PersonalActor("local-owner"),
            BriefingTrigger("premarket", date(2026, 8, 4), NOW),
            worker_id="w1",
        )

        self.assertEqual(result.provider_state, BriefingProviderState.COMPLETED)
        self.assertEqual(result.failure_code, "provider_claims_invalid_schema")

    def test_unconfirmed_news_cannot_be_promoted_to_confirmed_fact(self) -> None:
        self.runtime.output = claims_text("news:unconfirmed")

        result = self.coordinator.run(
            PersonalActor("local-owner"),
            BriefingTrigger(
                "intraday_event",
                date(2026, 8, 5),
                NOW,
                source_event_id="event-unconfirmed",
                evidence_id="news:unconfirmed",
            ),
            worker_id="w1",
        )

        self.assertEqual(result.failure_code, "claim_evidence_unconfirmed")

    def test_automation_maps_xnys_sessions_and_structured_event_identity(self) -> None:
        recorded = []
        reconciled = []

        class RecordingCoordinator:
            def reconcile(self, actor, *, as_of):
                reconciled.append((actor.actor_id, as_of))
                return 0

            def run(self, actor, trigger, *, worker_id):
                recorded.append((trigger, worker_id))

        news_registry = DomainToolRegistry(
            handlers={
                "get_today_context": lambda context, arguments: DomainToolResult.success(
                    data={
                        "fact_events": [
                            {
                                "event_id": "stable-event",
                                "evidence_id": "news:stable",
                                "related_symbols": ["NVDA", "TSLA"],
                            }
                        ]
                    },
                    evidence=(evidence("news:stable"),),
                )
            }
        )
        automation = AutomaticBriefingAutomation(
            coordinator=RecordingCoordinator(), tools=news_registry
        )
        actor = PersonalActor("local-owner")

        automation.run_once(
            actor,
            as_of=datetime(2026, 8, 3, 12, tzinfo=timezone.utc),
            worker_id="worker-1",
        )
        automation.run_once(
            actor,
            as_of=datetime(2026, 8, 3, 15, tzinfo=timezone.utc),
            worker_id="worker-1",
        )
        automation.run_once(
            actor,
            as_of=datetime(2026, 8, 3, 21, tzinfo=timezone.utc),
            worker_id="worker-1",
        )

        self.assertEqual(
            [trigger.kind for trigger, _worker in recorded],
            ["premarket", "intraday_event", "postmarket"],
        )
        self.assertEqual(recorded[1][0].source_event_id, "stable-event")
        self.assertEqual(recorded[1][0].subject_ids, ("NVDA", "TSLA"))
        self.assertEqual(len(reconciled), 3)

        off_session = datetime(2026, 8, 3, 2, tzinfo=timezone.utc)
        result = automation.run_once(
            actor,
            as_of=off_session,
            worker_id="worker-1",
        )
        self.assertIsNone(result.schedule_slot)
        self.assertEqual(reconciled[-1], ("local-owner", off_session))


if __name__ == "__main__":
    unittest.main()
