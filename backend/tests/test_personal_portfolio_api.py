from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.personal_workspace.contracts import PersonalActor
from backend.app.personal_workspace.portfolio import (
    InMemoryEquitySnapshotStore,
    InMemoryPortfolioStore,
    InMemoryPriceObservationStore,
    PortfolioBook,
    PortfolioPriceObservation,
)
from backend.app.personal_workspace.router import PersonalRuntime, create_personal_router
from backend.app.personal_workspace.security import PersonalAccessConfig


class FixedMarket:
    def observe_price(self, symbol: str) -> PortfolioPriceObservation:
        if symbol == "ACME":
            return PortfolioPriceObservation.available(
                price=Decimal("120.50"),
                source_health="fresh",
                as_of=datetime(2026, 8, 3, 2, 45, tzinfo=timezone.utc),
                feed="sip",
                delay_seconds=900,
                source_ids=("alpaca-acme",),
            )
        return PortfolioPriceObservation.unavailable("provider_unavailable")


class PersonalPortfolioApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = "portfolio-gateway-test-token"
        runtime = PersonalRuntime(
            access=PersonalAccessConfig(
                gateway_token=self.gateway,
                allowed_origins=frozenset({"http://127.0.0.1:5173"}),
                configured=True,
            ),
            actor=PersonalActor(actor_id="local-owner"),
            journey=None,
            portfolio=PortfolioBook(
                store=InMemoryPortfolioStore(),
                market=FixedMarket(),
            ),
        )
        app = FastAPI()
        app.include_router(create_personal_router(lambda: runtime))
        self.client = TestClient(app)

    @property
    def read_headers(self) -> dict[str, str]:
        return {"X-Personal-Gateway": self.gateway}

    def write_headers(self, key: str) -> dict[str, str]:
        return {
            **self.read_headers,
            "Origin": "http://127.0.0.1:5173",
            "Sec-Fetch-Site": "same-origin",
            "X-Personal-Request": "1",
            "Idempotency-Key": key,
        }

    def add_acme(self):
        return self.client.post(
            "/api/personal/portfolio/commands",
            headers=self.write_headers("add-acme-api"),
            json={
                "type": "add_holding",
                "symbol": "ACME",
                "name": "Acme Holdings",
                "quantity": "2",
                "average_cost": "100.25",
                "expected_portfolio_revision": 0,
            },
        )

    def test_get_and_closed_command_endpoint_return_complete_decimal_projection(self) -> None:
        empty = self.client.get("/api/personal/portfolio", headers=self.read_headers)
        created = self.add_acme()
        readback = self.client.get("/api/personal/portfolio", headers=self.read_headers)

        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["portfolio_revision"], 0)
        self.assertEqual(created.status_code, 200)
        self.assertEqual(readback.status_code, 200)
        self.assertEqual(readback.json(), created.json())
        holding = readback.json()["holdings"][0]
        self.assertEqual(holding["cost_amount"], "200.5000")
        self.assertEqual(holding["market_value"]["value"], "241.0000")
        self.assertEqual(holding["unrealized_profit_loss"]["value"], "40.5000")
        self.assertEqual(holding["unrealized_return"]["value"], "0.201995")

    def test_domain_conflicts_and_invalid_decimals_keep_stable_error_codes(self) -> None:
        self.assertEqual(self.add_acme().status_code, 200)
        duplicate = self.client.post(
            "/api/personal/portfolio/commands",
            headers=self.write_headers("duplicate-acme-api"),
            json={
                "type": "add_holding",
                "symbol": "acme",
                "name": "Duplicate",
                "quantity": "1",
                "average_cost": "1",
                "expected_portfolio_revision": 1,
            },
        )
        stale = self.client.post(
            "/api/personal/portfolio/commands",
            headers=self.write_headers("stale-cash-api"),
            json={
                "type": "set_usd_cash",
                "usd_cash": "10",
                "expected_portfolio_revision": 0,
            },
        )
        invalid = self.client.post(
            "/api/personal/portfolio/commands",
            headers=self.write_headers("invalid-cash-api"),
            json={
                "type": "set_usd_cash",
                "usd_cash": "-0.01",
                "expected_portfolio_revision": 1,
            },
        )
        unknown = self.client.post(
            "/api/personal/portfolio/commands",
            headers=self.write_headers("unknown-api"),
            json={"type": "merge_holdings", "expected_portfolio_revision": 1},
        )

        self.assertEqual((duplicate.status_code, duplicate.json()["detail"]["code"]), (409, "duplicate_symbol"))
        self.assertEqual((stale.status_code, stale.json()["detail"]["code"]), (409, "revision_conflict"))
        self.assertEqual((invalid.status_code, invalid.json()["detail"]["code"]), (422, "invalid_decimal"))
        self.assertEqual((unknown.status_code, unknown.json()["detail"]["code"]), (422, "invalid_command"))

    def test_request_and_confirm_purge_use_same_closed_command_endpoint(self) -> None:
        holding_id = self.add_acme().json()["holdings"][0]["holding_id"]
        requested = self.client.post(
            "/api/personal/portfolio/commands",
            headers=self.write_headers("request-purge-api"),
            json={
                "type": "request_purge",
                "holding_id": holding_id,
                "expected_portfolio_revision": 1,
            },
        )
        confirmed = self.client.post(
            "/api/personal/portfolio/commands",
            headers=self.write_headers("confirm-purge-api"),
            json={
                "type": "confirm_purge",
                "holding_id": holding_id,
                "expected_portfolio_revision": 1,
                "challenge": requested.json()["challenge"],
            },
        )

        self.assertEqual(requested.status_code, 200)
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["status"], "purged")
        self.assertEqual(confirmed.json()["portfolio_revision"], 2)
        self.assertEqual(confirmed.json()["backup_status"], "expires_within_window")
        self.assertEqual(
            self.client.get("/api/personal/portfolio", headers=self.read_headers).json()["holdings"],
            [],
        )

    def test_equity_history_returns_ordered_daily_snapshots(self) -> None:
        created = self.add_acme()
        self.assertEqual(created.status_code, 200)
        readback = self.client.get("/api/personal/portfolio", headers=self.read_headers)
        self.assertEqual(readback.status_code, 200)

        response = self.client.get(
            "/api/personal/portfolio/equity-history?limit=10",
            headers=self.read_headers,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["currency"], "USD")
        snapshots = payload["snapshots"]
        self.assertTrue(snapshots)
        latest = snapshots[-1]
        self.assertEqual(latest["total_equity"], "241.0000")
        self.assertEqual(latest["total_market_value"], "241.0000")
        self.assertEqual(latest["usd_cash"], "0.0000")
        self.assertEqual(latest["holdings_count"], 1)
        self.assertEqual(latest["priced_count"], 1)
        self.assertIn("market_day", latest)
        self.assertIn("observed_at", latest)
        self.assertIn(latest["after_close"], (True, False))
        self.assertEqual(
            [item["market_day"] for item in snapshots],
            sorted(item["market_day"] for item in snapshots),
        )

    def test_portfolio_view_marks_cached_fallback_prices(self) -> None:
        now = datetime(2026, 8, 3, 20, 5, tzinfo=timezone.utc)

        class FlakyMarket:
            def __init__(self) -> None:
                self.calls = 0

            def observe_price(self, symbol: str) -> PortfolioPriceObservation:
                self.calls += 1
                if self.calls <= 2:
                    return PortfolioPriceObservation.available(
                        price=Decimal("120.50"),
                        source_health="fresh",
                        as_of=now - timedelta(minutes=15),
                        feed="sip",
                        delay_seconds=900,
                        source_ids=("alpaca-acme",),
                    )
                return PortfolioPriceObservation.unavailable("provider_timeout")

        runtime = PersonalRuntime(
            access=PersonalAccessConfig(
                gateway_token=self.gateway,
                allowed_origins=frozenset({"http://127.0.0.1:5173"}),
                configured=True,
            ),
            actor=PersonalActor(actor_id="local-owner"),
            journey=None,
            portfolio=PortfolioBook(
                store=InMemoryPortfolioStore(),
                market=FlakyMarket(),
                clock=lambda: now,
                prices=InMemoryPriceObservationStore(),
                snapshots=InMemoryEquitySnapshotStore(),
            ),
        )
        app = FastAPI()
        app.include_router(create_personal_router(lambda: runtime))
        client = TestClient(app)

        add = client.post(
            "/api/personal/portfolio/commands",
            headers=self.write_headers("add-acme-api"),
            json={
                "type": "add_holding",
                "symbol": "ACME",
                "name": "Acme Holdings",
                "quantity": "2",
                "average_cost": "100.25",
                "expected_portfolio_revision": 0,
            },
        )
        self.assertEqual(add.status_code, 200)
        first = client.get("/api/personal/portfolio", headers=self.read_headers).json()
        self.assertFalse(first["holdings"][0]["market_price"]["cached"])
        second = client.get("/api/personal/portfolio", headers=self.read_headers).json()
        market_price = second["holdings"][0]["market_price"]
        self.assertTrue(market_price["cached"])
        self.assertEqual(market_price["availability"], "available")
        self.assertEqual(market_price["source_health"], "stale")
        self.assertEqual(second["priced_holding_count"], 1)
        self.assertEqual(second["total_equity"]["value"], "241.0000")
        self.assertNotIn("partial_valuation", second["issues"])


if __name__ == "__main__":
    unittest.main()
