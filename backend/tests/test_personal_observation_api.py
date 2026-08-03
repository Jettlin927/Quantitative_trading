from __future__ import annotations

from datetime import datetime, timezone
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.personal_workspace.contracts import PersonalActor
from backend.app.personal_workspace.instrument import (
    InstrumentWorkbench,
    UnavailableInstrumentObservationReader,
)
from backend.app.personal_workspace.router import PersonalRuntime, create_personal_router
from backend.app.personal_workspace.rules import (
    InMemoryObservationRuleStore,
    ObservationRuleBook,
    UnavailableRuleInputReader,
)
from backend.app.personal_workspace.security import PersonalAccessConfig


class PersonalObservationApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = "observation-gateway-test-token"
        rules = ObservationRuleBook(
            store=InMemoryObservationRuleStore(),
            inputs=UnavailableRuleInputReader(),
        )
        runtime = PersonalRuntime(
            access=PersonalAccessConfig(
                gateway_token=self.gateway,
                allowed_origins=frozenset({"http://127.0.0.1:5173"}),
                configured=True,
            ),
            actor=PersonalActor(actor_id="local-owner"),
            journey=None,
            instruments=InstrumentWorkbench(
                source=UnavailableInstrumentObservationReader(),
                cost_reader=lambda actor, symbol: None,
                rule_attention_reader=lambda actor, symbol: (),
                formal_overlay_reader=lambda symbol: (),
            ),
            rules=rules,
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

    def test_instrument_and_exact_eight_templates_are_readable(self) -> None:
        instrument = self.client.get(
            "/api/personal/instruments/acme", headers=self.read_headers
        )
        templates = self.client.get(
            "/api/personal/rule-templates", headers=self.read_headers
        )

        self.assertEqual(instrument.status_code, 200)
        self.assertEqual(instrument.json()["identity"]["symbol"], "ACME")
        self.assertEqual(instrument.json()["evidence_inspector"]["source_health"], "unavailable")
        self.assertEqual(templates.status_code, 200)
        self.assertEqual(len(templates.json()), 8)

    def test_rule_requires_explicit_enable_then_returns_stable_four_state(self) -> None:
        created = self.client.post(
            "/api/personal/rules/commands",
            headers=self.write_headers("create-rule-api"),
            json={
                "type": "create_rule",
                "template_id": "price_threshold",
                "symbol": "ACME",
                "parameters": {"direction": "gte", "price": "110"},
            },
        )
        enabled = self.client.post(
            "/api/personal/rules/commands",
            headers=self.write_headers("enable-rule-api"),
            json={
                "type": "set_rule_state",
                "rule_id": created.json()["rule_id"],
                "expected_revision": 1,
                "state": "enabled",
            },
        )
        evaluated = self.client.post(
            "/api/personal/rules/commands",
            headers=self.write_headers("evaluate-rule-api"),
            json={
                "type": "evaluate_rules",
                "symbol": "ACME",
                "as_of": datetime(2026, 8, 3, tzinfo=timezone.utc).isoformat(),
            },
        )
        readback = self.client.get("/api/personal/rules", headers=self.read_headers)

        self.assertEqual(created.json()["state"], "draft")
        self.assertEqual(enabled.json()["state"], "enabled")
        self.assertEqual(evaluated.json()["evaluations"][0]["result"], "insufficient_data")
        self.assertEqual(readback.json()["rules"][0]["latest_evaluation"]["result"], "insufficient_data")


if __name__ == "__main__":
    unittest.main()
