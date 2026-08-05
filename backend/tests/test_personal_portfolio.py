from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Lock
import time
import unittest

from pydantic import ValidationError

from backend.app.personal_workspace.contracts import (
    AddHoldingCommand,
    BuyHoldingCommand,
    EditHoldingCommand,
    PersonalActor,
    PurgeHoldingCommand,
    RemoveHoldingCommand,
    RestoreHoldingCommand,
    SellHoldingCommand,
    SetUsdCashCommand,
)
from backend.app.personal_workspace.portfolio import (
    AlpacaPortfolioMarketReader,
    InMemoryEquitySnapshotStore,
    InMemoryPortfolioStore,
    PortfolioBook,
    PortfolioMarketReader,
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
        # 新增持仓按数量×均价自动扣减现金：0 - 2×100.25 = -200.50
        self.assertEqual(portfolio.usd_cash, "-200.5000")
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
        # 权重 = 市值 / 总权益 = 241 / 40.50（现金被扣减后权益变小）
        self.assertEqual(holding.weight.value, "5.950617")
        self.assertEqual(holding.market_price.feed, "sip")
        self.assertEqual(holding.market_price.delay_seconds, 900)
        self.assertEqual(portfolio.total_market_value.value, "241.0000")
        # 总权益 = 市值 + 现金 = 241 + (-200.50)
        self.assertEqual(portfolio.total_equity.value, "40.5000")

        repeated = self.add_acme(idempotency_key="add-acme-001")
        self.assertEqual(repeated.portfolio_revision, 1)
        self.assertEqual(repeated.holdings[0].holding_id, holding.holding_id)
        # 幂等重复提交不重复扣现金
        self.assertEqual(repeated.usd_cash, "-200.5000")

    def test_add_holding_debits_cash_but_edit_remove_restore_do_not(self) -> None:
        added = self.add_acme()
        self.assertEqual(added.usd_cash, "-200.5000")  # 0 - 2×100.25

        holding_id = added.holdings[0].holding_id
        edited = self.book.revise(
            self.actor,
            EditHoldingCommand(
                type="edit_holding",
                holding_id=holding_id,
                name="Acme Revised",
                quantity="3",
                average_cost="90",
                expected_portfolio_revision=1,
            ),
            idempotency_key="edit-acme-cash",
        )
        # 编辑数量/均价视为修正录入，不改变现金
        self.assertEqual(edited.usd_cash, "-200.5000")

        removed = self.book.revise(
            self.actor,
            RemoveHoldingCommand(
                type="remove_holding",
                holding_id=holding_id,
                expected_portfolio_revision=2,
            ),
            idempotency_key="remove-acme-cash",
        )
        # 移出不是卖出，不贷记现金
        self.assertEqual(removed.usd_cash, "-200.5000")

        restored = self.book.revise(
            self.actor,
            RestoreHoldingCommand(
                type="restore_holding",
                holding_id=holding_id,
                expected_portfolio_revision=3,
            ),
            idempotency_key="restore-acme-cash",
        )
        # 恢复不是重新买入，不再次扣现金
        self.assertEqual(restored.usd_cash, "-200.5000")

        # 多笔新增累计扣减：再加 BETA 3×10=30
        with_cash = self.book.revise(
            self.actor,
            AddHoldingCommand(
                type="add_holding",
                symbol="BETA",
                name="Beta",
                quantity="3",
                average_cost="10",
                expected_portfolio_revision=4,
            ),
            idempotency_key="add-beta-cash",
        )
        self.assertEqual(with_cash.usd_cash, "-230.5000")
        # set_usd_cash 仍可手工覆盖
        override = self.book.revise(
            self.actor,
            SetUsdCashCommand(
                type="set_usd_cash",
                usd_cash="59",
                expected_portfolio_revision=5,
            ),
            idempotency_key="override-cash",
        )
        self.assertEqual(override.usd_cash, "59.0000")

    def test_buy_holding_debits_cash_and_weights_average_cost(self) -> None:
        added = self.add_acme()
        holding_id = added.holdings[0].holding_id
        # 加仓 3 股 @ 90：现金 -3×90；加权均价 = (2×100.25 + 3×90) / 5 = 94.10
        bought = self.book.revise(
            self.actor,
            BuyHoldingCommand(
                type="buy_holding",
                holding_id=holding_id,
                quantity="3",
                price="90",
                expected_portfolio_revision=1,
            ),
            idempotency_key="buy-acme-001",
        )
        holding = bought.holdings[0]
        self.assertEqual(holding.quantity, "5.0000")
        self.assertEqual(holding.average_cost, "94.1000")
        # 现金：-200.50（首笔） - 270（加仓） = -470.50
        self.assertEqual(bought.usd_cash, "-470.5000")
        # 加仓不产生已实现交易
        self.assertEqual(bought.realized_trades, ())
        self.assertEqual(bought.realized_pnl_total.availability, "not_applicable")

        # 幂等重复不重复扣现金
        repeated = self.book.revise(
            self.actor,
            BuyHoldingCommand(
                type="buy_holding",
                holding_id=holding_id,
                quantity="3",
                price="90",
                expected_portfolio_revision=1,
            ),
            idempotency_key="buy-acme-001",
        )
        self.assertEqual(repeated.holdings[0].quantity, "5.0000")
        self.assertEqual(repeated.usd_cash, "-470.5000")

    def test_sell_holding_credits_cash_and_records_realized_pnl(self) -> None:
        added = self.add_acme()
        holding_id = added.holdings[0].holding_id
        # 卖出 1.5 股 @ 120：现金 +180；已实现盈亏 = (120-100.25)×1.5 = 29.625
        sold = self.book.revise(
            self.actor,
            SellHoldingCommand(
                type="sell_holding",
                holding_id=holding_id,
                quantity="1.5",
                price="120",
                expected_portfolio_revision=1,
            ),
            idempotency_key="sell-acme-001",
        )
        holding = sold.holdings[0]
        self.assertEqual(holding.quantity, "0.5000")
        self.assertEqual(holding.state, "active")
        self.assertEqual(holding.average_cost, "100.2500")  # 均价不变（平均成本法）
        self.assertEqual(sold.usd_cash, "-20.5000")  # -200.50 + 180
        self.assertEqual(sold.realized_pnl_total.value, "29.6250")
        self.assertEqual(len(sold.realized_trades), 1)
        trade = sold.realized_trades[0]
        self.assertEqual(trade.symbol, "ACME")
        self.assertEqual(trade.shares, "1.500000")
        self.assertEqual(trade.price, "120.0000")
        self.assertEqual(trade.proceeds, "180.0000")
        self.assertEqual(trade.cost_basis, "150.3750")
        self.assertEqual(trade.realized_pnl, "29.6250")
        self.assertEqual(trade.portfolio_revision, 2)

        # 幂等重放不重复记录交易
        self.book.revise(
            self.actor,
            SellHoldingCommand(
                type="sell_holding",
                holding_id=holding_id,
                quantity="1.5",
                price="120",
                expected_portfolio_revision=2,
            ),
            idempotency_key="sell-acme-001",
        )
        reopened = self.book.open(self.actor)
        self.assertEqual(len(reopened.realized_trades), 1)
        self.assertEqual(reopened.realized_pnl_total.value, "29.6250")

    def test_sell_all_marks_holding_sold_and_rejects_restore_and_over_sell(self) -> None:
        added = self.add_acme()
        holding_id = added.holdings[0].holding_id
        closed = self.book.revise(
            self.actor,
            SellHoldingCommand(
                type="sell_holding",
                holding_id=holding_id,
                quantity="2",
                price="110",
                expected_portfolio_revision=1,
            ),
            idempotency_key="sell-all-acme-001",
        )
        holding = closed.holdings[0]
        self.assertEqual(holding.state, "sold")
        self.assertEqual(holding.quantity, "0.0000")
        self.assertEqual(closed.usd_cash, "19.5000")  # -200.50 + 220
        self.assertEqual(closed.realized_pnl_total.value, "19.5000")

        # 已清仓：不可恢复、不可再卖
        with self.assertRaises(ValueError):
            self.book.revise(
                self.actor,
                RestoreHoldingCommand(
                    type="restore_holding",
                    holding_id=holding_id,
                    expected_portfolio_revision=2,
                ),
                idempotency_key="restore-sold-acme",
            )
        with self.assertRaises(ValueError):
            self.book.revise(
                self.actor,
                SellHoldingCommand(
                    type="sell_holding",
                    holding_id=holding_id,
                    quantity="1",
                    price="110",
                    expected_portfolio_revision=2,
                ),
                idempotency_key="sell-sold-acme",
            )
        # 卖出数量超过持仓 → 拒绝
        with self.assertRaises(ValueError):
            self.book.revise(
                self.actor,
                SellHoldingCommand(
                    type="sell_holding",
                    holding_id=holding_id,
                    quantity="3",
                    price="110",
                    expected_portfolio_revision=1,
                ),
                idempotency_key="sell-over-acme",
            )
        # 对已清仓持仓加仓 → 拒绝
        with self.assertRaises(ValueError):
            self.book.revise(
                self.actor,
                BuyHoldingCommand(
                    type="buy_holding",
                    holding_id=holding_id,
                    quantity="1",
                    price="110",
                    expected_portfolio_revision=2,
                ),
                idempotency_key="buy-sold-acme",
            )

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
        self.assertEqual(portfolio.total_equity.value, "10.5000")
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


class PortfolioEquityTrackingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = PersonalActor(actor_id="local-owner")

    def build_book(
        self,
        market: PortfolioMarketReader,
        *,
        now: datetime,
        prices=None,
        snapshots=None,
        cached_price_max_age_days: int = 7,
    ) -> PortfolioBook:
        return PortfolioBook(
            store=InMemoryPortfolioStore(),
            market=market,
            clock=MutableClock(now),
            prices=prices,
            snapshots=snapshots,
            cached_price_max_age_days=cached_price_max_age_days,
        )

    @staticmethod
    def available(
        symbol: str, price: Decimal, *, as_of: datetime
    ) -> PortfolioPriceObservation:
        return PortfolioPriceObservation.available(
            price=price,
            source_health="fresh",
            as_of=as_of,
            feed="delayed_sip",
            delay_seconds=900,
            source_ids=(f"alpaca-{symbol.lower()}",),
        )

    def test_unavailable_live_price_falls_back_to_last_persisted_observation(self) -> None:
        now = datetime(2026, 8, 3, 20, 5, tzinfo=timezone.utc)

        class FailingMarket:
            def __init__(self) -> None:
                self.calls = 0

            def observe_price(self, symbol: str) -> PortfolioPriceObservation:
                self.calls += 1
                if self.calls <= 2:
                    return self.available(symbol, Decimal("120.5000"), as_of=now)
                return PortfolioPriceObservation.unavailable("provider_timeout")

            available = staticmethod(
                lambda symbol, price, *, as_of: PortfolioEquityTrackingTest.available(
                    symbol, price, as_of=as_of
                )
            )

        book = self.build_book(FailingMarket(), now=now)
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
            idempotency_key="add-acme-001",
        )

        first = book.open(self.actor)
        self.assertEqual(first.holdings[0].market_price.value, "120.5000")
        self.assertFalse(first.holdings[0].market_price.cached)

        second = book.open(self.actor)
        holding = second.holdings[0]
        self.assertEqual(second.priced_holding_count, 1)
        self.assertEqual(holding.market_price.availability, "available")
        self.assertTrue(holding.market_price.cached)
        self.assertEqual(holding.market_price.value, "120.5000")
        self.assertEqual(holding.market_price.feed, "delayed_sip")
        self.assertEqual(holding.market_price.source_health, "stale")
        self.assertEqual(second.total_equity.value, "40.5000")
        self.assertNotIn("partial_valuation", second.issues)

    def test_cached_fallback_older_than_max_age_stays_unavailable(self) -> None:
        now = datetime(2026, 8, 3, 20, 5, tzinfo=timezone.utc)

        class AgingMarket:
            def __init__(self) -> None:
                self.calls = 0

            def observe_price(self, symbol: str) -> PortfolioPriceObservation:
                self.calls += 1
                if self.calls <= 2:
                    return PortfolioEquityTrackingTest.available(
                        symbol, Decimal("120.5000"), as_of=now - timedelta(days=3)
                    )
                return PortfolioPriceObservation.unavailable("provider_timeout")

        book = self.build_book(AgingMarket(), now=now)
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
            idempotency_key="add-acme-001",
        )
        self.assertEqual(book.open(self.actor).priced_holding_count, 1)

        stale = book.open(self.actor)
        self.assertEqual(stale.holdings[0].market_price.value, "120.5000")
        self.assertTrue(stale.holdings[0].market_price.cached)

        aged_book = self.build_book(
            ScriptedPortfolioMarket({}), now=now
        )
        aged_book.revise(
            self.actor,
            AddHoldingCommand(
                type="add_holding",
                symbol="ACME",
                name="Acme Holdings",
                quantity="2",
                average_cost="100.25",
                expected_portfolio_revision=0,
            ),
            idempotency_key="add-acme-001",
        )
        # 缓存 10 天前的报价，超过 7 天有效期：不回落，保持不可用
        aged_book._prices.upsert(
            actor_id=self.actor.actor_id,
            observations={
                "ACME": PortfolioPriceObservation(
                    availability="available",
                    price=Decimal("120.5000"),
                    reason_code=None,
                    source_health="fresh",
                    as_of=now - timedelta(days=10),
                    feed="delayed_sip",
                    delay_seconds=900,
                    source_ids=(),
                )
            },
        )
        aged = aged_book.open(self.actor)
        self.assertEqual(aged.priced_holding_count, 0)
        self.assertEqual(aged.holdings[0].market_price.availability, "not_available")
        self.assertEqual(aged.total_equity.availability, "not_available")

    def test_equity_snapshot_upserted_per_market_day_after_close_flag(self) -> None:
        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)  # 周一 11:00 EDT 盘中
        clock = MutableClock(now)
        market = ScriptedPortfolioMarket(
            {
                "ACME": self.available(
                    "ACME", Decimal("120.5000"), as_of=now - timedelta(minutes=15)
                )
            }
        )
        snapshots = InMemoryEquitySnapshotStore()
        book = PortfolioBook(
            store=InMemoryPortfolioStore(),
            market=market,
            clock=clock,
            snapshots=snapshots,
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
            idempotency_key="add-acme-001",
        )
        history = book.equity_history(self.actor)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].market_day, "2026-08-03")
        # 现金被新增持仓扣减：0 - 2×100.25 = -200.50；权益 = 市值 241 + 现金
        self.assertEqual(history[0].total_equity, "40.5000")
        self.assertEqual(history[0].total_market_value, "241.0000")
        self.assertEqual(history[0].usd_cash, "-200.5000")
        self.assertEqual(history[0].holdings_count, 1)
        self.assertEqual(history[0].priced_count, 1)
        self.assertFalse(history[0].after_close)

        # 同日收盘后再次打开：upsert 覆盖当天行，标记收盘
        clock.advance(timedelta(hours=5, minutes=5))  # 16:05 EDT
        book.open(self.actor)
        history = book.equity_history(self.actor)
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0].after_close)

        # 次日打开：新增第二天行
        clock.advance(timedelta(days=1))
        book.open(self.actor)
        history = book.equity_history(self.actor, limit=10)
        self.assertEqual([item.market_day for item in history], ["2026-08-03", "2026-08-04"])
        self.assertTrue(history[-1].after_close)

    def test_equity_snapshot_skipped_on_weekend_and_when_unpriced(self) -> None:
        now = datetime(2026, 8, 1, 20, 5, tzinfo=timezone.utc)  # 周六 16:05 EDT
        clock = MutableClock(now)
        market = ScriptedPortfolioMarket(
            {
                "ACME": self.available(
                    "ACME", Decimal("120.5000"), as_of=now - timedelta(days=1)
                )
            }
        )
        snapshots = InMemoryEquitySnapshotStore()
        book = PortfolioBook(
            store=InMemoryPortfolioStore(),
            market=market,
            clock=clock,
            snapshots=snapshots,
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
            idempotency_key="add-acme-001",
        )
        # 周六：ET 非工作日，不写快照
        self.assertEqual(book.equity_history(self.actor), ())

        # 从未成功定价的组合（无落盘缓存）：权益不可计算，不写快照
        monday = datetime(2026, 8, 3, 20, 5, tzinfo=timezone.utc)  # 周一 16:05 EDT
        fresh_book = PortfolioBook(
            store=InMemoryPortfolioStore(),
            market=ScriptedPortfolioMarket({}),
            clock=FrozenClock(monday),
            snapshots=InMemoryEquitySnapshotStore(),
        )
        fresh_book.revise(
            self.actor,
            AddHoldingCommand(
                type="add_holding",
                symbol="ACME",
                name="Acme Holdings",
                quantity="2",
                average_cost="100.25",
                expected_portfolio_revision=0,
            ),
            idempotency_key="add-acme-001",
        )
        self.assertEqual(fresh_book.equity_history(self.actor), ())
        self.assertEqual(fresh_book.open(self.actor).total_equity.availability, "not_available")

    def test_equity_history_respects_limit(self) -> None:
        now = datetime(2026, 8, 3, 20, 5, tzinfo=timezone.utc)
        clock = MutableClock(now)
        market = ScriptedPortfolioMarket(
            {
                "ACME": self.available(
                    "ACME", Decimal("120.5000"), as_of=now - timedelta(minutes=15)
                )
            }
        )
        snapshots = InMemoryEquitySnapshotStore()
        book = PortfolioBook(
            store=InMemoryPortfolioStore(),
            market=market,
            clock=clock,
            snapshots=snapshots,
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
            idempotency_key="add-acme-001",
        )
        for _ in range(7):
            clock.advance(timedelta(days=1))
            book.open(self.actor)
        full = book.equity_history(self.actor)
        self.assertEqual(len(full), 6)
        limited = book.equity_history(self.actor, limit=2)
        self.assertEqual([item.market_day for item in limited], ["2026-08-07", "2026-08-10"])


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now = self.now + delta


if __name__ == "__main__":
    unittest.main()
