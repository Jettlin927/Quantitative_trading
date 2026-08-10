from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.personal_workspace.contracts import PersonalActor
from backend.app.personal_workspace.router import PersonalRuntime, create_personal_router
from backend.app.personal_workspace.security import PersonalAccessConfig
from backend.app.personal_workspace.watchlist import (
    HoldingWatchState,
    InMemoryInstrumentStateStore,
    InstrumentStateBook,
)


class PersonalWatchlistApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = "watchlist-gateway-test-token"
        self.holdings = {"NVDA": HoldingWatchState("active", 1)}
        runtime = PersonalRuntime(
            access=PersonalAccessConfig(
                gateway_token=self.gateway,
                allowed_origins=frozenset({"http://127.0.0.1:5173"}),
                configured=True,
            ),
            actor=PersonalActor("local-owner"),
            journey=None,
            watchlist=InstrumentStateBook(
                store=InMemoryInstrumentStateStore(),
                holding_states_reader=lambda _actor_id: dict(self.holdings),
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

    def test_read_and_commands_keep_private_write_gates_idempotency_and_revision(self) -> None:
        initial = self.client.get("/api/personal/watchlist", headers=self.read_headers)
        followed = self.client.post(
            "/api/personal/watchlist/commands",
            headers=self.write_headers("follow-msft-api"),
            json={
                "type": "follow_symbol",
                "symbol": "MSFT",
                "preset_reasons": ["财报观察"],
                "custom_reason": "等待催化",
                "expected_revision": 0,
            },
        )
        repeated = self.client.post(
            "/api/personal/watchlist/commands",
            headers=self.write_headers("follow-msft-api"),
            json={
                "type": "follow_symbol",
                "symbol": "MSFT",
                "preset_reasons": ["财报观察"],
                "custom_reason": "等待催化",
                "expected_revision": 0,
            },
        )
        stale = self.client.post(
            "/api/personal/watchlist/commands",
            headers=self.write_headers("follow-amd-stale"),
            json={
                "type": "follow_symbol",
                "symbol": "AMD",
                "preset_reasons": ["行业映射"],
                "expected_revision": 0,
            },
        )
        forbidden = self.client.post(
            "/api/personal/watchlist/commands",
            headers={**self.write_headers("unfollow-nvda"), "Origin": "https://evil.invalid"},
            json={
                "type": "unfollow_symbol",
                "symbol": "NVDA",
                "expected_revision": 1,
            },
        )
        active_holding = self.client.post(
            "/api/personal/watchlist/commands",
            headers=self.write_headers("unfollow-active-nvda"),
            json={
                "type": "unfollow_symbol",
                "symbol": "NVDA",
                "expected_revision": 1,
            },
        )

        self.assertEqual(initial.status_code, 200)
        self.assertTrue(initial.json()["items"][0]["is_followed"])
        self.assertEqual(
            set(initial.json()),
            {
                "revision",
                "items",
                "followed_items",
                "watch_observations",
                "active_candidates",
                "archived_candidates",
            },
        )
        self.assertEqual(
            [item["symbol"] for item in initial.json()["followed_items"]],
            ["NVDA"],
        )
        self.assertEqual(initial.json()["watch_observations"], [])
        self.assertEqual(followed.status_code, 200)
        self.assertEqual(
            [item["symbol"] for item in followed.json()["followed_items"]],
            ["MSFT", "NVDA"],
        )
        self.assertEqual(
            [item["symbol"] for item in followed.json()["watch_observations"]],
            ["MSFT"],
        )
        self.assertEqual(repeated.json(), followed.json())
        self.assertEqual(followed.json()["revision"], 1)
        self.assertEqual(
            (stale.status_code, stale.json()["detail"]["code"]),
            (409, "revision_conflict"),
        )
        self.assertEqual(
            (forbidden.status_code, forbidden.json()["detail"]["code"]),
            (403, "origin_rejected"),
        )
        self.assertEqual(
            (active_holding.status_code, active_holding.json()["detail"]["code"]),
            (409, "holding_watch_required"),
        )


if __name__ == "__main__":
    unittest.main()
