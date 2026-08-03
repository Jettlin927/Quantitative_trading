from __future__ import annotations

from datetime import datetime, timezone
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.personal_workspace.analysis import (
    AnalysisWorkspace,
    EvidenceCandidate,
    InMemoryAnalysisStore,
    ScriptedResponsesAdapter,
)
from backend.app.personal_workspace.contracts import PersonalActor
from backend.app.personal_workspace.router import PersonalRuntime, create_personal_router
from backend.app.personal_workspace.security import PersonalAccessConfig


NOW = datetime(2026, 8, 3, 4, 0, tzinfo=timezone.utc)


class PersonalAnalysisApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = "analysis-gateway-token"
        self.workspace = AnalysisWorkspace(
            store=InMemoryAnalysisStore(),
            evidence_reader=lambda actor, intent: (
                EvidenceCandidate(
                    evidence_id="sec-1",
                    kind="official_filing",
                    source="sec",
                    field="official_facts",
                    excerpt="公司披露资本开支增加。",
                    content_sha256="1" * 64,
                    authorized_for_ai=True,
                    as_of=NOW,
                ),
            ),
            provider=ScriptedResponsesAdapter.completed(
                claims=(
                    {
                        "kind": "confirmed_fact",
                        "statement": "公司已披露资本开支增加。",
                        "evidence_ids": ["sec-1"],
                        "opposing_evidence_ids": [],
                        "assumptions": [],
                        "horizon": "当前披露期",
                        "invalidation_conditions": ["公司发布更正"],
                    },
                )
            ),
            clock=lambda: NOW,
        )
        runtime = PersonalRuntime(
            access=PersonalAccessConfig(
                gateway_token=self.gateway,
                allowed_origins=frozenset({"http://127.0.0.1:5173"}),
                configured=True,
            ),
            actor=PersonalActor(actor_id="local-owner"),
            journey=None,
            analyses=self.workspace,
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

    def test_prepare_confirm_observe_and_event_stream(self) -> None:
        prepared = self.client.post(
            "/api/personal/analysis-drafts",
            headers=self.write_headers("api-prepare"),
            json={
                "question": "官方事实可能如何影响公司？",
                "subject_ids": ["ACME"],
                "selected_private_fields": [],
            },
        )
        draft = prepared.json()
        readback = self.client.get(
            f"/api/personal/analysis-drafts/{draft['draft_id']}",
            headers=self.read_headers,
        )
        started = self.client.post(
            "/api/personal/analyses",
            headers=self.write_headers("api-start"),
            json={
                "draft_id": draft["draft_id"],
                "preview_sha256": draft["preview_sha256"],
            },
        )
        run_id = started.json()["run_id"]
        self.workspace.run_next(worker_id="api-worker")
        observed = self.client.get(
            f"/api/personal/analyses/{run_id}", headers=self.read_headers
        )
        events = self.client.get(
            f"/api/personal/analyses/{run_id}/events",
            headers={**self.read_headers, "Accept": "text/event-stream"},
        )

        self.assertEqual(prepared.status_code, 202)
        self.assertEqual(readback.status_code, 200)
        self.assertEqual(readback.json()["preview_sha256"], draft["preview_sha256"])
        self.assertEqual(started.status_code, 202)
        self.assertEqual(observed.json()["status"], "completed")
        self.assertEqual(observed.json()["claims"][0]["kind"], "confirmed_fact")
        self.assertEqual(events.headers["content-type"], "text/event-stream; charset=utf-8")
        self.assertIn("event: analysis_stage", events.text)
        self.assertIn('"stage":"completed"', events.text)

    def test_cancel_and_stable_preview_error_mapping(self) -> None:
        prepared = self.client.post(
            "/api/personal/analysis-drafts",
            headers=self.write_headers("cancel-prepare"),
            json={"question": "取消分析", "subject_ids": ["ACME"]},
        ).json()
        changed = self.client.post(
            "/api/personal/analyses",
            headers=self.write_headers("changed-start"),
            json={"draft_id": prepared["draft_id"], "preview_sha256": "0" * 64},
        )
        started = self.client.post(
            "/api/personal/analyses",
            headers=self.write_headers("cancel-start"),
            json={
                "draft_id": prepared["draft_id"],
                "preview_sha256": prepared["preview_sha256"],
            },
        ).json()
        cancelled = self.client.post(
            f"/api/personal/analyses/{started['run_id']}/cancel",
            headers=self.write_headers("cancel-command"),
            json={},
        )

        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed.json()["detail"]["code"], "preview_changed")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
