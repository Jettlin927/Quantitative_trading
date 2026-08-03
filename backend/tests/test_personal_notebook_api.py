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
from backend.app.personal_workspace.notebook import InMemoryNotebookStore, ResearchNotebook
from backend.app.personal_workspace.router import PersonalRuntime, create_personal_router
from backend.app.personal_workspace.security import PersonalAccessConfig


NOW = datetime(2026, 8, 3, 6, 0, tzinfo=timezone.utc)


class PersonalNotebookApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = "notebook-gateway"
        self.actor = PersonalActor("notebook-api-owner")
        analysis_store = InMemoryAnalysisStore()
        self.analyses = AnalysisWorkspace(
            store=analysis_store,
            evidence_reader=lambda actor, intent: (
                EvidenceCandidate("sec-1", "official_filing", "sec", "official_facts",
                                  "官方披露事实", "1" * 64, True, NOW),
            ),
            provider=ScriptedResponsesAdapter.completed(claims=(
                {"kind": "confirmed_fact", "statement": "官方事实", "evidence_ids": ["sec-1"],
                 "opposing_evidence_ids": [], "assumptions": [], "horizon": "当前",
                 "invalidation_conditions": ["官方更正"]},
                {"kind": "unknown", "statement": "仍缺指引", "evidence_ids": ["sec-1"],
                 "opposing_evidence_ids": [], "assumptions": ["尚未披露"], "horizon": "下一季",
                 "invalidation_conditions": ["新指引"]},
            )),
            clock=lambda: NOW,
        )
        notebook = ResearchNotebook(
            store=InMemoryNotebookStore(), analyses=analysis_store,
            challenge_key=b"api-notebook-challenge" * 2, clock=lambda: NOW,
        )
        runtime = PersonalRuntime(
            access=PersonalAccessConfig(self.gateway, frozenset({"http://127.0.0.1:5173"}), True),
            actor=self.actor, journey=None, analyses=self.analyses, notebook=notebook,
        )
        app = FastAPI()
        app.include_router(create_personal_router(lambda: runtime))
        self.client = TestClient(app)

    @property
    def read_headers(self) -> dict[str, str]:
        return {"X-Personal-Gateway": self.gateway}

    def write_headers(self, key: str) -> dict[str, str]:
        return {
            **self.read_headers, "Origin": "http://127.0.0.1:5173",
            "Sec-Fetch-Site": "same-origin", "X-Personal-Request": "1",
            "Idempotency-Key": key,
        }

    def completed_run(self) -> str:
        draft = self.client.post(
            "/api/personal/analysis-drafts", headers=self.write_headers("prepare"),
            json={"question": "事实如何影响？", "subject_ids": ["ACME"]},
        ).json()
        run = self.client.post(
            "/api/personal/analyses", headers=self.write_headers("start"),
            json={"draft_id": draft["draft_id"], "preview_sha256": draft["preview_sha256"]},
        ).json()
        self.analyses.run_next(worker_id="api-worker")
        return run["run_id"]

    def test_save_list_open_and_append_audit_use_closed_commands(self) -> None:
        run_id = self.completed_run()
        run = self.client.get(f"/api/personal/analyses/{run_id}", headers=self.read_headers).json()
        saved = self.client.post(
            "/api/personal/records/commands", headers=self.write_headers("save"),
            json={"type": "save_analysis", "analysis_id": run_id,
                  "accepted_claim_ids": [item["claim_id"] for item in run["claims"]],
                  "user_supplement": "用户补充", "private_fragments": [],
                  "verification_drafts": []},
        )
        record = saved.json()
        audited = self.client.post(
            "/api/personal/records/commands", headers=self.write_headers("audit"),
            json={"type": "start_reasoning_audit", "record_id": record["record_id"],
                  "expected_version": 1},
        )
        listing = self.client.get("/api/personal/records", headers=self.read_headers)
        detail = self.client.get(
            f"/api/personal/records/{record['record_id']}", headers=self.read_headers,
        )

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(audited.json()["current_version"], 2)
        self.assertEqual(len(audited.json()["versions"][0]["cards"]), 6)
        self.assertEqual(len(listing.json()), 1)
        self.assertEqual(detail.json()["record_id"], record["record_id"])
        self.assertFalse(detail.json()["formal_research_eligible"])

    def test_browser_cannot_submit_model_output_or_unknown_command(self) -> None:
        run_id = self.completed_run()
        run = self.client.get(f"/api/personal/analyses/{run_id}", headers=self.read_headers).json()
        forged = self.client.post(
            "/api/personal/records/commands", headers=self.write_headers("forged"),
            json={"type": "save_analysis", "analysis_id": run_id,
                  "accepted_claim_ids": [run["claims"][0]["claim_id"]], "model_output": "浏览器伪造正文"},
        )
        unknown = self.client.post(
            "/api/personal/records/commands", headers=self.write_headers("unknown"),
            json={"type": "overwrite_record", "record_id": "x"},
        )
        self.assertEqual(forged.status_code, 422)
        self.assertEqual(unknown.status_code, 422)
        self.assertEqual(forged.json()["detail"]["code"], "invalid_command")


if __name__ == "__main__":
    unittest.main()
