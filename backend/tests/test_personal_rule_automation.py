from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest
from types import SimpleNamespace

from backend.app.personal_workspace.contracts import (
    AddHoldingCommand,
    CreateObservationRuleCommand,
    PersonalActor,
    RemoveHoldingCommand,
    SetObservationRuleStateCommand,
)
from backend.app.personal_workspace.instrument import InstrumentBar
from backend.app.personal_workspace.portfolio import (
    InMemoryPortfolioStore,
    PortfolioBook,
    UnavailablePortfolioMarketReader,
)
from backend.app.personal_workspace.rule_automation import HoldingRuleAutomation
from backend.app.personal_workspace.rules import (
    InMemoryObservationRuleStore,
    ObservationRuleBook,
    RuleInput,
)


NOW = datetime(2026, 8, 5, 22, 0, tzinfo=timezone.utc)


class SymbolRuleInputs:
    def read(self, symbol: str, *, as_of: datetime, minimum_bars: int) -> RuleInput:
        bars = tuple(
            InstrumentBar(
                trade_date=date(2026, 7, 1) + timedelta(days=index),
                open=Decimal("100"),
                high=Decimal("121"),
                low=Decimal("99"),
                close=Decimal("120") if index == 29 else Decimal("100"),
                volume=100,
                evidence_id=f"{symbol}-bar-{index}",
            )
            for index in range(30)
        )
        return RuleInput(
            symbol=symbol,
            raw_bars=bars,
            adjusted_bars=bars,
            events=(),
            source_health="fresh",
            evidence_ids=tuple(bar.evidence_id for bar in bars),
            corporate_actions_available=True,
        )


class HoldingRuleAutomationTest(unittest.TestCase):
    def test_market_holiday_does_not_read_private_workspace(self) -> None:
        actor = PersonalActor(actor_id="local-owner")

        class UnexpectedPortfolio:
            def active_symbols(self, request_actor):
                raise AssertionError("private workspace should not be read")

        automation = HoldingRuleAutomation(
            portfolio=UnexpectedPortfolio(), rules=SimpleNamespace()
        )

        with self.assertRaisesRegex(
            ValueError, "personal_rule_evaluation_outside_market_sessions"
        ):
            automation.run_once(
                actor, as_of=datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc)
            )

    def test_early_close_enters_post_market_at_actual_close(self) -> None:
        actor = PersonalActor(actor_id="local-owner")

        class ActivePortfolio:
            def active_symbols(self, request_actor):
                return ("ACME",)

        class RecordingRules:
            key = None

            def open(self, request_actor):
                return {
                    "rules": (
                        SimpleNamespace(
                            rule_id="rule-acme",
                            revision=1,
                            state="enabled",
                            symbol="ACME",
                        ),
                    )
                }

            def evaluate(self, request_actor, request, *, idempotency_key):
                self.key = idempotency_key
                return SimpleNamespace(evaluations=(object(),))

        rules = RecordingRules()
        HoldingRuleAutomation(portfolio=ActivePortfolio(), rules=rules).run_once(
            actor,
            as_of=datetime(2026, 11, 27, 18, 30, tzinfo=timezone.utc),
        )

        self.assertIn(":post_market:", rules.key)

    def test_market_session_defines_idempotency_identity(self) -> None:
        actor = PersonalActor(actor_id="local-owner")

        class ActivePortfolio:
            def active_symbols(self, request_actor):
                self.actor = request_actor
                return ("ACME",)

        class RecordingRules:
            def __init__(self):
                self.keys = []

            def open(self, request_actor):
                return {
                    "rules": (
                        SimpleNamespace(
                            rule_id="rule-acme",
                            revision=1,
                            state="enabled",
                            symbol="ACME",
                        ),
                    )
                }

            def evaluate(self, request_actor, request, *, idempotency_key):
                self.keys.append(idempotency_key)
                return SimpleNamespace(evaluations=(object(),))

        rules = RecordingRules()
        automation = HoldingRuleAutomation(portfolio=ActivePortfolio(), rules=rules)

        automation.run_once(
            actor, as_of=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        )
        automation.run_once(
            actor, as_of=datetime(2026, 8, 3, 12, 30, tzinfo=timezone.utc)
        )
        automation.run_once(
            actor, as_of=datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
        )
        automation.run_once(
            actor, as_of=datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc)
        )

        self.assertEqual(rules.keys[0], rules.keys[1])
        self.assertEqual(len(set(rules.keys)), 3)

    def test_one_symbol_failure_does_not_block_other_active_holdings(self) -> None:
        actor = PersonalActor(actor_id="local-owner")

        class ActivePortfolio:
            def active_symbols(self, request_actor):
                return ("ACME", "BETA")

        class PartlyFailingRules:
            evaluated = []

            def open(self, request_actor):
                return {
                    "rules": tuple(
                        SimpleNamespace(
                            rule_id=f"rule-{symbol}",
                            revision=1,
                            state="enabled",
                            symbol=symbol,
                        )
                        for symbol in ("ACME", "BETA")
                    )
                }

            def evaluate(self, request_actor, request, *, idempotency_key):
                self.evaluated.append(request.symbol)
                if request.symbol == "ACME":
                    raise RuntimeError("synthetic_acme_failure")
                return SimpleNamespace(evaluations=(object(),))

        rules = PartlyFailingRules()

        result = HoldingRuleAutomation(
            portfolio=ActivePortfolio(), rules=rules
        ).run_once(actor, as_of=NOW)

        self.assertEqual(rules.evaluated, ["ACME", "BETA"])
        self.assertEqual(result.evaluation_count, 1)
        self.assertEqual(result.evaluated_symbols, ("BETA",))
        self.assertEqual(result.failed_symbols, ("ACME",))

    def test_three_market_sessions_append_three_evaluations(self) -> None:
        actor = PersonalActor(actor_id="local-owner")
        portfolio = PortfolioBook(
            store=InMemoryPortfolioStore(),
            market=UnavailablePortfolioMarketReader(),
        )
        portfolio.revise(
            actor,
            AddHoldingCommand(
                type="add_holding",
                symbol="ACME",
                name="Acme",
                quantity="1",
                average_cost="100",
                expected_portfolio_revision=0,
            ),
            idempotency_key="add-acme-bucket",
        )
        rules = ObservationRuleBook(
            store=InMemoryObservationRuleStore(),
            inputs=SymbolRuleInputs(),
        )
        draft = rules.revise(
            actor,
            CreateObservationRuleCommand(
                type="create_rule",
                template_id="price_threshold",
                symbol="ACME",
                parameters={"direction": "gte", "price": "110"},
            ),
            idempotency_key="create-acme-bucket",
        )
        rules.revise(
            actor,
            SetObservationRuleStateCommand(
                type="set_rule_state",
                rule_id=draft.rule_id,
                expected_revision=draft.revision,
                state="enabled",
            ),
            idempotency_key="enable-acme-bucket",
        )
        automation = HoldingRuleAutomation(portfolio=portfolio, rules=rules)

        automation.run_once(
            actor, as_of=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        )
        automation.run_once(
            actor, as_of=datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
        )
        automation.run_once(
            actor, as_of=datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc)
        )

        self.assertEqual(len(rules.open(actor)["evaluations"]), 3)

    def test_only_enabled_rules_for_active_holdings_are_evaluated(self) -> None:
        actor = PersonalActor(actor_id="local-owner")
        portfolio = PortfolioBook(
            store=InMemoryPortfolioStore(),
            market=UnavailablePortfolioMarketReader(),
        )
        first = portfolio.revise(
            actor,
            AddHoldingCommand(
                type="add_holding",
                symbol="ACME",
                name="Acme",
                quantity="1",
                average_cost="100",
                expected_portfolio_revision=0,
            ),
            idempotency_key="add-acme",
        )
        second = portfolio.revise(
            actor,
            AddHoldingCommand(
                type="add_holding",
                symbol="BETA",
                name="Beta",
                quantity="1",
                average_cost="100",
                expected_portfolio_revision=first.portfolio_revision,
            ),
            idempotency_key="add-beta",
        )
        beta = next(item for item in second.holdings if item.symbol == "BETA")
        portfolio.revise(
            actor,
            RemoveHoldingCommand(
                type="remove_holding",
                holding_id=beta.holding_id,
                expected_portfolio_revision=second.portfolio_revision,
            ),
            idempotency_key="remove-beta",
        )

        rules = ObservationRuleBook(
            store=InMemoryObservationRuleStore(),
            inputs=SymbolRuleInputs(),
        )
        for symbol in ("ACME", "BETA"):
            draft = rules.revise(
                actor,
                CreateObservationRuleCommand(
                    type="create_rule",
                    template_id="price_threshold",
                    symbol=symbol,
                    parameters={"direction": "gte", "price": "110"},
                ),
                idempotency_key=f"create-{symbol}",
            )
            rules.revise(
                actor,
                SetObservationRuleStateCommand(
                    type="set_rule_state",
                    rule_id=draft.rule_id,
                    expected_revision=draft.revision,
                    state="enabled",
                ),
                idempotency_key=f"enable-{symbol}",
            )

        result = HoldingRuleAutomation(portfolio=portfolio, rules=rules).run_once(
            actor, as_of=NOW
        )

        self.assertEqual(result.evaluated_symbols, ("ACME",))
        self.assertEqual(
            tuple(item.symbol for item in rules.open(actor)["evaluations"]),
            ("ACME",),
        )


if __name__ == "__main__":
    unittest.main()
