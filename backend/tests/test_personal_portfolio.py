from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Lock
import time
import unittest

from pydantic import ValidationError

from backend.app.personal_workspace.contracts import (
    AddHoldingCommand,
    EditHoldingCommand,
    PersonalActor,
    PurgeHoldingCommand,
    RemoveHoldingCommand,
    RestoreHoldingCommand,
    SetUsdCashCommand,
)
from backend.app.personal_workspace.portfolio import (
    AlpacaPortfolioMarketReader,
    InMemoryPortfolioStore,
    PortfolioBook,
    PortfolioPriceObservation,
)
from backend.app.market_observation.contracts import (
    DelayedPrice,
    ObservedValue,
    ProvenanceEnvelope,
)


class ScriptedPortfolioMarket:
    def __init__(self, observations: dict[str, PortfolioPriceObservation]) -> None:
        self.observations = observations

    def observe_price(self, symbol: str) -> PortfolioPriceObservation:
        return self.observations.get(
            symbol,
            PortfolioPriceObservation.unavailable("provider_unavailable"),
        )


class FrozenClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class PersonalPortfolioTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 3, 3, 0, tzinfo=timezone.utc)
        self.actor = PersonalActor(actor_id="local-owner")
        self.market = ScriptedPortfolioMarket(
            {
                "ACME": PortfolioPriceObservation.available(
                    price=Decimal("120.5000"),
                    source_health="fresh",
                    as_of=self.now - timedelta(minutes=15),
                    feed="sip",
                    delay_seconds=900,
                    source_ids=("alpaca-price-acme",),
                )
            }
        )
        self.book = PortfolioBook(
            store=InMemoryPortfolioStore(),
            market=self.market,
            clock=FrozenClock(self.now),
        )

    def add_acme(self, *, idempotency_key: str = "add-acme-001"):
        return self.book.revise(
            self.actor,
            AddHoldingCommand(
                type="add_holding",
                symbol=" acme ",
                name="Acme Holdings",
                quantity="2",
                average_cost="100.25",
                expected_portfolio_revision=0,
            ),
            idempotency_key=idempotency_key,
        )

    def test_add_holding_normalizes_symbol_and_derives_decimal_values_on_server(self) -> None:
        portfolio = self.add_acme()

        self.assertEqual(portfolio.portfolio_revision, 1)
        self.assertEqual(portfolio.currency, "USD")
        self.assertEqual(portfolio.usd_cash, "0.0000")
        self.assertEqual(len(portfolio.holdings), 1)
        holding = portfolio.holdings[0]
        self.assertEqual(holding.symbol, "ACME")
        self.assertEqual(holding.quantity, "2.0000")
        self.assertEqual(holding.average_cost, "100.2500")
        self.assertEqual(holding.cost_amount, "200.5000")
        self.assertEqual(holding.market_price.value, "120.5000")
        self.assertEqual(holding.market_value.value, "241.0000")
        self.assertEqual(holding.unrealized_profit_loss.value, "40.5000")
        self.assertEqual(holding.unrealized_return.value, "0.201995")
        self.assertEqual(holding.weight.value, "1.000000")
        self.assertEqual(holding.market_price.feed, "sip")
        self.assertEqual(holding.market_price.delay_seconds, 900)
        self.assertEqual(portfolio.total_market_value.value, "241.0000")
        self.assertEqual(portfolio.total_equity.value, "241.0000")

        repeated = self.add_acme(idempotency_key="add-acme-001")
        self.assertEqual(repeated.portfolio_revision, 1)
        self.assertEqual(repeated.holdings[0].holding_id, holding.holding_id)

    def test_open_observes_active_holdings_concurrently(self) -> None:
        class ConcurrentMarket:
            def __init__(self) -> None:
                self.active = 0
                self.maximum = 0
                self.lock = Lock()

            def observe_price(self, symbol: str) -> PortfolioPriceObservation:
                with self.lock:
                    self.active += 1
                    self.maximum = max(self.maximum, self.active)
                time.sleep(0.03)
                with self.lock:
                    self.active -= 1
                return PortfolioPriceObservation.unavailable("provider_unavailable")

        market = ConcurrentMarket()
        book = PortfolioBook(
            store=InMemoryPortfolioStore(), market=market, clock=FrozenClock(self.now)
        )
        for revision, symbol in enumerate(("AAA", "BBB", "CCC")):
            book.revise(
                self.actor,
                AddHoldingCommand(
                    type="add_holding",
                    symbol=symbol,
                    name=symbol,
                    quantity="1",
                    average_cost="1",
                    expected_portfolio_revision=revision,
                ),
                idempotency_key=f"add-{symbol}",
            )
        market.maximum = 0

        book.open(self.actor)

        self.assertGreater(market.maximum, 1)

    def test_open_stops_waiting_for_slow_prices_at_the_aggregate_deadline(self) -> None:
        class SlowMarket:
            slow = False

            def observe_price(self, symbol: str) -> PortfolioPriceObservation:
                if self.slow:
                    time.sleep(0.2)
                return PortfolioPriceObservation.unavailable("provider_unavailable")

        market = SlowMarket()
        book = PortfolioBook(
            store=InMemoryPortfolioStore(),
            market=market,
            clock=FrozenClock(self.now),
            provider_wait_seconds=0.03,
        )
        for revision, symbol in enumerate(("AAA", "BBB", "CCC")):
            book.revise(
                self.actor,
                AddHoldingCommand(
                    type="add_holding",
                    symbol=symbol,
                    name=symbol,
                    quantity="1",
                    average_cost="1",
                    expected_portfolio_revision=revision,
                ),
                idempotency_key=f"add-slow-{symbol}",
            )
        market.slow = True

        started = time.monotonic()
        portfolio = book.open(self.actor)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.12)
        self.assertEqual(portfolio.priced_holding_count, 0)
        self.assertEqual(portfolio.issues, ("provider_timeout",))

    def test_average_cost_reads_manual_ledger_without_observing_market(self) -> None:
        class CountingMarket:
            def __init__(self) -> None:
                self.calls = 0

            def observe_price(self, symbol: str) -> PortfolioPriceObservation:
                self.calls += 1
                return PortfolioPriceObservation.unavailable("provider_unavailable")

        market = CountingMarket()
        book = PortfolioBook(
            store=InMemoryPortfolioStore(), market=market, clock=FrozenClock(self.now)
        )
        book.revise(
            self.actor,
            AddHoldingCommand(
                type="add_holding",
                symbol="ACME",
                name="Acme Holdings",
                quantity="2",
                average_cost="100.25",
                expected_portfolio_revision=0,
            ),
            idempotency_key="add-acme-cost-read",
        )
        market.calls = 0

        cost = book.average_cost(self.actor, " acme ")

        self.assertEqual(cost, Decimal("100.25"))
        self.assertEqual(market.calls, 0)

    def test_unavailable_price_does_not_block_manual_holding_or_turn_missing_values_into_zero(self) -> None:
        self.market.observations.clear()

        portfolio = self.add_acme()

        holding = portfolio.holdings[0]
        self.assertEqual(holding.cost_amount, "200.5000")
        for observed in (
            holding.market_price,
            holding.market_value,
            holding.unrealized_profit_loss,
            holding.unrealized_return,
            holding.weight,
            portfolio.total_market_value,
            portfolio.total_equity,
        ):
            self.assertEqual(observed.availability, "not_available")
            self.assertIsNone(observed.value)
            self.assertEqual(observed.reason_code, "provider_unavailable")
        self.assertIn("provider_unavailable", portfolio.issues)

    def test_one_unpriced_symbol_keeps_covered_portfolio_value_visible(self) -> None:
        self.add_acme()

        portfolio = self.book.revise(
            self.actor,
            AddHoldingCommand(
                type="add_holding",
                symbol="BETA",
                name="Unsupported Market",
                quantity="3",
                average_cost="10",
                expected_portfolio_revision=1,
            ),
            idempotency_key="add-beta-001",
        )

        self.assertEqual(portfolio.active_holding_count, 2)
        self.assertEqual(portfolio.priced_holding_count, 1)
        self.assertEqual(portfolio.total_market_value.availability, "available")
        self.assertEqual(portfolio.total_market_value.value, "241.0000")
        self.assertEqual(portfolio.total_market_value.reason_code, "partial_valuation")
        self.assertEqual(portfolio.total_equity.value, "241.0000")
        self.assertEqual(portfolio.total_equity.reason_code, "partial_valuation")
        self.assertEqual(portfolio.holdings[0].weight.reason_code, "portfolio_total_unavailable")
        self.assertEqual(portfolio.holdings[1].market_value.availability, "not_available")
        self.assertIn("partial_valuation", portfolio.issues)

    def test_duplicate_symbol_revision_conflict_and_decimal_validation_are_explicit(self) -> None:
        self.add_acme()

        with self.assertRaisesRegex(ValueError, "duplicate_symbol"):
            self.book.revise(
                self.actor,
                AddHoldingCommand(
                    type="add_holding",
                    symbol="ACME",
                    name="Duplicate",
                    quantity="1",
                    average_cost="1",
                    expected_portfolio_revision=1,
                ),
                idempotency_key="duplicate-acme",
            )

        with self.assertRaisesRegex(ValueError, "revision_conflict"):
            self.book.revise(
                self.actor,
                SetUsdCashCommand(
                    type="set_usd_cash",
                    usd_cash="10",
                    expected_portfolio_revision=0,
                ),
                idempotency_key="stale-cash",
            )

        for field, value in (("quantity", "0"), ("average_cost", "-1")):
            payload = {
                "type": "add_holding",
                "symbol": "BETA",
                "name": "Beta",
                "quantity": "1",
                "average_cost": "1",
                "expected_portfolio_revision": 1,
            }
            payload[field] = value
            with self.assertRaises(ValidationError):
                AddHoldingCommand(**payload)
        with self.assertRaises(ValidationError):
            SetUsdCashCommand(
                type="set_usd_cash",
                usd_cash="-0.01",
                expected_portfolio_revision=1,
            )

    def test_edit_cash_remove_and_restore_keep_server_derived_projection_consistent(self) -> None:
        added = self.add_acme()
        holding_id = added.holdings[0].holding_id
        edited = self.book.revise(
            self.actor,
            EditHoldingCommand(
                type="edit_holding",
                holding_id=holding_id,
                name="Acme Revised",
                quantity="2.5",
                average_cost="100",
                expected_portfolio_revision=1,
            ),
            idempotency_key="edit-acme-001",
        )
        with_cash = self.book.revise(
            self.actor,
            SetUsdCashCommand(
                type="set_usd_cash",
                usd_cash="59",
                expected_portfolio_revision=2,
            ),
            idempotency_key="cash-001",
        )

        self.assertEqual(edited.holdings[0].name, "Acme Revised")
        self.assertEqual(with_cash.usd_cash, "59.0000")
        self.assertEqual(with_cash.holdings[0].market_value.value, "301.2500")
        self.assertEqual(with_cash.total_equity.value, "360.2500")
        self.assertEqual(with_cash.holdings[0].weight.value, "0.836225")

        removed = self.book.revise(
            self.actor,
            RemoveHoldingCommand(
                type="remove_holding",
                holding_id=holding_id,
                expected_portfolio_revision=3,
            ),
            idempotency_key="remove-acme-001",
        )
        self.assertEqual(removed.holdings[0].state, "removed")
        self.assertEqual(removed.holdings[0].weight.availability, "not_applicable")
        self.assertEqual(removed.total_market_value.value, "0.0000")
        self.assertEqual(removed.total_equity.value, "59.0000")

        restored = self.book.revise(
            self.actor,
            RestoreHoldingCommand(
                type="restore_holding",
                holding_id=holding_id,
                expected_portfolio_revision=4,
            ),
            idempotency_key="restore-acme-001",
        )
        self.assertEqual(restored.portfolio_revision, 5)
        self.assertEqual(restored.holdings[0].state, "active")
        self.assertEqual(restored.holdings[0].revision, 4)

    def test_purge_requires_current_short_lived_challenge_and_reports_backup_expiry_window(self) -> None:
        added = self.add_acme()
        holding_id = added.holdings[0].holding_id
        challenge = self.book.request_purge(
            self.actor,
            holding_id=holding_id,
            expected_portfolio_revision=1,
        )

        self.assertEqual(challenge.holding_id, holding_id)
        self.assertEqual(challenge.expires_at, self.now + timedelta(minutes=10))
        with self.assertRaisesRegex(ValueError, "purge_challenge_invalid"):
            self.book.purge(
                self.actor,
                PurgeHoldingCommand(
                    holding_id=holding_id,
                    expected_portfolio_revision=1,
                    challenge="tampered",
                ),
                idempotency_key="purge-acme-001",
            )

        receipt = self.book.purge(
            self.actor,
            PurgeHoldingCommand(
                holding_id=holding_id,
                expected_portfolio_revision=1,
                challenge=challenge.challenge,
            ),
            idempotency_key="purge-acme-001",
        )

        self.assertEqual(receipt.status, "purged")
        self.assertEqual(receipt.portfolio_revision, 2)
        self.assertEqual(receipt.backup_status, "expires_within_window")
        self.assertEqual(receipt.backup_expires_at, self.now + timedelta(days=30))
        self.assertEqual(self.book.open(self.actor).holdings, ())

        repeated = self.book.purge(
            self.actor,
            PurgeHoldingCommand(
                holding_id=holding_id,
                expected_portfolio_revision=1,
                challenge=challenge.challenge,
            ),
            idempotency_key="purge-acme-001",
        )
        self.assertEqual(repeated, receipt)


class AlpacaPortfolioMarketReaderTest(unittest.TestCase):
    def test_maps_typed_delayed_sip_or_eod_fallback_without_exposing_provider_payload(self) -> None:
        now = datetime(2026, 8, 3, 3, 0, tzinfo=timezone.utc)
        provenance = ProvenanceEnvelope(
            source="alpaca",
            dataset="alpaca_delayed_sip_prices",
            provider_record_id=None,
            source_url="https://data.alpaca.markets/v2/stocks/ACME/bars",
            fetched_at=now,
            content_sha256="a" * 64,
            authorization_snapshot_id="auth-snapshot-001",
            qualification="traceable_history",
            source_health="stale",
            ai_context=False,
            formal_research=False,
            adjustment_policy="raw",
            fallback_identity="eod-acme-2026-08-01",
            missing_reason="provider_timeout_eod_fallback",
        )

        class TypedAlpacaAdapter:
            def observe_delayed_price(self, symbol, *, observed_at, purpose):
                self.call = (symbol, observed_at, purpose)
                return ObservedValue(
                    availability="available",
                    value=DelayedPrice(
                        symbol="ACME",
                        price=Decimal("119.75"),
                        currency="USD",
                        feed="eod",
                        delay_seconds=86400,
                    ),
                    reason_code="provider_timeout_eod_fallback",
                    source_health="stale",
                    as_of=now - timedelta(days=1),
                    provenance=provenance,
                )

        adapter = TypedAlpacaAdapter()
        reader = AlpacaPortfolioMarketReader(adapter=adapter, clock=lambda: now)

        observed = reader.observe_price("ACME")

        self.assertEqual(adapter.call, ("ACME", now, "display"))
        self.assertEqual(observed.price, Decimal("119.75"))
        self.assertEqual(observed.feed, "eod")
        self.assertEqual(observed.source_health, "stale")
        self.assertEqual(observed.reason_code, "provider_timeout_eod_fallback")
        self.assertEqual(observed.source_ids, ("eod-acme-2026-08-01", "auth-snapshot-001"))


if __name__ == "__main__":
    unittest.main()
