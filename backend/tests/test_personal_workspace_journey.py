from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from backend.app.personal_workspace.contracts import (
    AddHoldingCommand,
    CreateObservationRuleCommand,
    PersonalActor,
    RemoveHoldingCommand,
    SetObservationRuleStateCommand,
)
from backend.app.personal_workspace.crypto import FixedKeyring, PersonalDataCipher
from backend.app.personal_workspace.journey import PersonalResearchJourney
from backend.app.personal_workspace.persistence import InMemoryPersonalJourneyStore
from backend.app.personal_workspace.portfolio import (
    InMemoryPortfolioStore,
    PortfolioBook,
    PortfolioPriceObservation,
)
from backend.app.personal_workspace.instrument import InstrumentBar
from backend.app.personal_workspace.rules import (
    InMemoryObservationRuleStore,
    ObservationRuleBook,
    RuleEvaluationRequest,
    RuleInput,
)
from backend.app.personal_workspace.synthetic import SyntheticWorkspaceAdapters


class PersonalResearchJourneyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryPersonalJourneyStore()
        self.adapters = SyntheticWorkspaceAdapters(provider_available=False)
        self.cipher = PersonalDataCipher(
            FixedKeyring(
                active_key_id="synthetic-key",
                data_keys={"synthetic-key": bytes(range(32))},
                lookup_key=b"synthetic-lookup-key-for-tests-only",
            )
        )
        self.journey = PersonalResearchJourney(
            store=self.store,
            cipher=self.cipher,
            adapters=self.adapters,
        )
        self.actor = PersonalActor(actor_id="local-owner")

    def test_synthetic_trace_is_idempotent_and_keeps_provider_data_out_of_ai_preview(self) -> None:
        first = self.journey.create_synthetic_trace(
            self.actor,
            idempotency_key="trace-001",
            question="这个虚构事件可能通过什么机制影响合成标的？",
        )
        second = self.journey.create_synthetic_trace(
            self.actor,
            idempotency_key="trace-001",
            question="这个虚构事件可能通过什么机制影响合成标的？",
        )

        self.assertEqual(first.analysis_id, second.analysis_id)
        self.assertTrue(first.synthetic)
        self.assertFalse(first.research_eligible)
        self.assertEqual(first.holding.symbol, "SYNTH-001")
        self.assertEqual(first.holding.quantity, "12.5000")
        self.assertEqual(first.market.source_health, "unavailable")
        self.assertEqual(first.market.bars[0].close, "80.0000")
        self.assertEqual(
            [evaluation.result for evaluation in first.rule_evaluations],
            ["hit", "not_hit", "insufficient_data", "calculation_failed"],
        )
        self.assertEqual(first.analysis_preview.status, "ready")
        self.assertEqual(first.analysis_preview.provider, "synthetic-model")
        self.assertIn("user_symbol", first.analysis_preview.included_fields)
        self.assertEqual(
            [item.field for item in first.analysis_preview.excluded_fields],
            [
                "market_prices",
                "derived_indicators",
                "portfolio_weight",
                "unrealized_return",
                "price_rule_results",
            ],
        )
        self.assertIn("provider_unavailable", first.issues)
        self.assertEqual(first.analysis_claim.kind, "inference")
        self.assertEqual(
            set(self.adapters.analysis_model.captured_payloads[0]),
            {"user_symbol", "user_question", "official_evidence_excerpt"},
        )
        captured_text = str(self.adapters.analysis_model.captured_payloads[0])
        for denied_value in ("80.0000", "12.5000", "portfolio_weight", "price_rule_results"):
            self.assertNotIn(denied_value, captured_text)

        raw_store = self.store.raw_bytes()
        for private_value in (
            b"SYNTH-001",
            b"12.5000",
            "这个虚构事件".encode("utf-8"),
        ):
            self.assertNotIn(private_value, raw_store)

    def test_today_projection_includes_the_same_complete_portfolio_view(self) -> None:
        class FixedMarket:
            def observe_price(self, symbol):
                return PortfolioPriceObservation.available(
                    price=Decimal("120.50"),
                    source_health="fresh",
                    as_of=datetime(2026, 8, 3, tzinfo=timezone.utc),
                    feed="sip",
                    delay_seconds=900,
                    source_ids=("alpaca-acme",),
                )

        portfolio = PortfolioBook(store=InMemoryPortfolioStore(), market=FixedMarket())
        journey = PersonalResearchJourney(
            store=self.store,
            cipher=self.cipher,
            adapters=self.adapters,
            portfolio=portfolio,
        )
        journey.create_synthetic_trace(
            self.actor,
            idempotency_key="trace-with-portfolio",
            question="组合投影测试",
        )
        portfolio.revise(
            self.actor,
            AddHoldingCommand(
                type="add_holding",
                symbol="ACME",
                name="Acme Holdings",
                quantity="2",
                average_cost="100.25",
                expected_portfolio_revision=0,
            ),
            idempotency_key="add-acme-today",
        )

        today = journey.open_today(self.actor)

        self.assertEqual(today.portfolio, portfolio.open(self.actor))
        self.assertEqual(today.portfolio.holdings[0].market_value.value, "241.0000")

    def test_today_default_does_not_return_synthetic_trace(self) -> None:
        self.journey.create_synthetic_trace(
            self.actor,
            idempotency_key="trace-hidden-from-today",
            question="只用于隔离测试",
        )

        today = self.journey.open_today(self.actor)

        self.assertIsNone(today.trace)

    def test_today_does_not_project_old_rule_hit_after_holding_is_removed(self) -> None:
        class FixedMarket:
            def observe_price(self, symbol):
                return PortfolioPriceObservation.unavailable("provider_unavailable")

        class FixedRuleInputs:
            def read(self, symbol, *, as_of, minimum_bars):
                bar = InstrumentBar(
                    trade_date=date(2026, 8, 5),
                    open=Decimal("119"),
                    high=Decimal("121"),
                    low=Decimal("118"),
                    close=Decimal("120"),
                    volume=100,
                    evidence_id="acme-bar",
                )
                return RuleInput(
                    symbol=symbol,
                    raw_bars=(bar,),
                    adjusted_bars=(bar,),
                    events=(),
                    source_health="fresh",
                    evidence_ids=(bar.evidence_id,),
                    corporate_actions_available=True,
                )

        portfolio = PortfolioBook(store=InMemoryPortfolioStore(), market=FixedMarket())
        rules = ObservationRuleBook(
            store=InMemoryObservationRuleStore(), inputs=FixedRuleInputs()
        )
        created_portfolio = portfolio.revise(
            self.actor,
            AddHoldingCommand(
                type="add_holding",
                symbol="ACME",
                name="Acme",
                quantity="1",
                average_cost="100",
                expected_portfolio_revision=0,
            ),
            idempotency_key="add-attention-holding",
        )
        draft = rules.revise(
            self.actor,
            CreateObservationRuleCommand(
                type="create_rule",
                template_id="price_threshold",
                symbol="ACME",
                parameters={"direction": "gte", "price": "110"},
            ),
            idempotency_key="create-attention-rule",
        )
        rules.revise(
            self.actor,
            SetObservationRuleStateCommand(
                type="set_rule_state",
                rule_id=draft.rule_id,
                expected_revision=draft.revision,
                state="enabled",
            ),
            idempotency_key="enable-attention-rule",
        )
        rules.evaluate(
            self.actor,
            RuleEvaluationRequest(
                symbol="ACME", as_of=datetime(2026, 8, 5, tzinfo=timezone.utc)
            ),
            idempotency_key="evaluate-attention-rule",
        )
        portfolio.revise(
            self.actor,
            RemoveHoldingCommand(
                type="remove_holding",
                holding_id=created_portfolio.holdings[0].holding_id,
                expected_portfolio_revision=created_portfolio.portfolio_revision,
            ),
            idempotency_key="remove-attention-holding",
        )
        journey = PersonalResearchJourney(
            store=self.store,
            cipher=self.cipher,
            adapters=self.adapters,
            portfolio=portfolio,
            rulebook=rules,
        )

        today = journey.open_today(self.actor)

        self.assertEqual(today.attention_items, ())


if __name__ == "__main__":
    unittest.main()
