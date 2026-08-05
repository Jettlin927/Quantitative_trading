from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from backend.app.personal_workspace.contracts import PersonalActor
from backend.app.personal_workspace.crypto import FixedKeyring, PersonalDataCipher
from backend.app.personal_workspace.journey import PersonalResearchJourney
from backend.app.personal_workspace.persistence import InMemoryPersonalJourneyStore
from backend.app.personal_workspace.router import PersonalRuntime, create_personal_router
from backend.app.personal_workspace.security import PersonalAccessConfig
from backend.app.personal_workspace.synthetic import SyntheticWorkspaceAdapters


class PersonalWorkspaceSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway_token = "synthetic-gateway-token-for-tests"
        store = InMemoryPersonalJourneyStore()
        journey = PersonalResearchJourney(
            store=store,
            cipher=PersonalDataCipher(
                FixedKeyring(
                    active_key_id="synthetic-key",
                    data_keys={"synthetic-key": bytes(range(32))},
                    lookup_key=b"synthetic-lookup-key-for-tests-only",
                )
            ),
            adapters=SyntheticWorkspaceAdapters(provider_available=False),
        )
        runtime = PersonalRuntime(
            access=PersonalAccessConfig(
                gateway_token=self.gateway_token,
                allowed_origins=frozenset({"http://127.0.0.1:5173"}),
                configured=True,
            ),
            actor=PersonalActor(actor_id="local-owner"),
            journey=journey,
        )
        app = FastAPI()
        app.include_router(create_personal_router(lambda: runtime))

        @app.get("/api/public-proof")
        def public_proof() -> dict[str, str]:
            return {"status": "available"}

        self.client = TestClient(app)

    def test_unconfigured_private_router_does_not_block_public_routes(self) -> None:
        unconfigured_app = FastAPI()
        unconfigured_app.include_router(
            create_personal_router(
                lambda: PersonalRuntime.unconfigured()
            )
        )

        @unconfigured_app.get("/api/public-proof")
        def public_proof() -> dict[str, str]:
            return {"status": "available"}

        client = TestClient(unconfigured_app)
        private_response = client.get("/api/personal/today")

        self.assertEqual(private_response.status_code, 503)
        self.assertEqual(private_response.json()["detail"]["code"], "personal_access_unconfigured")
        self.assertEqual(client.get("/api/public-proof").json(), {"status": "available"})

    def test_gateway_is_required_for_private_reads(self) -> None:
        missing = self.client.get("/api/personal/today")
        wrong = self.client.get(
            "/api/personal/today",
            headers={"X-Personal-Gateway": "wrong"},
        )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(missing.json()["detail"]["code"], "personal_access_required")

    def test_private_write_rejects_each_missing_same_origin_proof(self) -> None:
        base_headers = {
            "X-Personal-Gateway": self.gateway_token,
            "Origin": "http://127.0.0.1:5173",
            "Sec-Fetch-Site": "same-origin",
            "X-Personal-Request": "1",
            "Idempotency-Key": "trace-http-001",
        }
        cases = [
            ({key: value for key, value in base_headers.items() if key != "Origin"}, 403, "origin_rejected"),
            ({**base_headers, "Origin": "https://attacker.invalid"}, 403, "origin_rejected"),
            ({**base_headers, "Sec-Fetch-Site": "cross-site"}, 403, "origin_rejected"),
            ({key: value for key, value in base_headers.items() if key != "X-Personal-Request"}, 403, "origin_rejected"),
            ({key: value for key, value in base_headers.items() if key != "Idempotency-Key"}, 422, "invalid_command"),
        ]

        for headers, status, code in cases:
            with self.subTest(code=code, headers=headers):
                response = self.client.post(
                    "/api/personal/synthetic-traces",
                    headers=headers,
                    json={"question": "合成问题"},
                )
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["detail"]["code"], code)

        non_json = self.client.post(
            "/api/personal/synthetic-traces",
            headers=base_headers,
            content="plain text",
        )
        self.assertEqual(non_json.status_code, 422)
        self.assertEqual(non_json.json()["detail"]["code"], "invalid_command")

    def test_valid_proxy_request_keeps_synthetic_trace_out_of_today(self) -> None:
        headers = {
            "X-Personal-Gateway": self.gateway_token,
            "Origin": "http://127.0.0.1:5173",
            "Sec-Fetch-Site": "same-origin",
            "X-Personal-Request": "1",
            "Idempotency-Key": "trace-http-002",
        }
        created = self.client.post(
            "/api/personal/synthetic-traces",
            headers=headers,
            json={"question": "合成问题"},
        )
        read = self.client.get(
            "/api/personal/today",
            headers={"X-Personal-Gateway": self.gateway_token},
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(read.status_code, 200)
        self.assertEqual(created.json()["holding"]["symbol"], "SYNTH-001")
        self.assertIsNone(read.json()["trace"])
        self.assertNotIn("record", read.json())
        self.assertNotIn(self.gateway_token, created.text)
        self.assertNotIn(self.gateway_token, read.text)

    def test_private_store_failure_is_stable_503_and_does_not_expose_database_error(self) -> None:
        class FailingStore(InMemoryPersonalJourneyStore):
            def get_trace_by_idempotency(self, *, actor_id: str, idempotency_key: str):
                raise SQLAlchemyError("synthetic database detail must stay private")

        runtime = PersonalRuntime(
            access=PersonalAccessConfig(
                gateway_token=self.gateway_token,
                allowed_origins=frozenset({"http://127.0.0.1:5173"}),
                configured=True,
            ),
            actor=PersonalActor(actor_id="local-owner"),
            journey=PersonalResearchJourney(
                store=FailingStore(),
                cipher=PersonalDataCipher(
                    FixedKeyring(
                        active_key_id="synthetic-key",
                        data_keys={"synthetic-key": bytes(range(32))},
                        lookup_key=b"synthetic-lookup-key-for-tests-only",
                    )
                ),
                adapters=SyntheticWorkspaceAdapters(provider_available=False),
            ),
        )
        app = FastAPI()
        app.include_router(create_personal_router(lambda: runtime))

        response = TestClient(app).post(
            "/api/personal/synthetic-traces",
            headers={
                "X-Personal-Gateway": self.gateway_token,
                "Origin": "http://127.0.0.1:5173",
                "Sec-Fetch-Site": "same-origin",
                "X-Personal-Request": "1",
                "Idempotency-Key": "failing-store-trace",
            },
            json={"question": "合成问题"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "private_store_unavailable")
        self.assertNotIn("database detail", response.text)


if __name__ == "__main__":
    unittest.main()
