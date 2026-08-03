from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from backend.app.personal_workspace.analysis import (
    AnalysisClaim,
    AnalysisDraftReceipt,
    AnalysisIntent,
    AnalysisRunView,
    InMemoryAnalysisStore,
    StoredAnalysisDraft,
    StoredAnalysisRun,
)
from backend.app.personal_workspace.contracts import PersonalActor
from backend.app.personal_workspace.notebook import (
    InMemoryNotebookStore,
    PrivateFragmentInput,
    ResearchNotebook,
    VerificationDraft,
)


NOW = datetime(2026, 8, 3, 5, 0, tzinfo=timezone.utc)


def claim(kind: str, index: int) -> AnalysisClaim:
    return AnalysisClaim(
        claim_id=f"claim-{index}",
        kind=kind,
        statement=f"{kind} 主张",
        evidence_ids=("sec-1",),
        opposing_evidence_ids=("macro-1",) if kind == "inference" else (),
        assumptions=("假设 A",) if kind != "confirmed_fact" else (),
        horizon="未来两个季度",
        invalidation_conditions=("新增官方披露",),
    )


def notebook_fixture(*, status: str = "completed") -> tuple[ResearchNotebook, PersonalActor]:
    analyses = InMemoryAnalysisStore()
    actor = PersonalActor("notebook-owner")
    receipt = AnalysisDraftReceipt(
        draft_id="draft-1", status="ready", provider="deepseek", model="deepseek-v4-flash",
        config_revision="personal-impact-deepseek-v1", included_fields=("official_facts",),
        excluded_fields=(), gaps=("missing_guidance",), preview_sha256="a" * 64,
        retention="DeepSeek 默认磁盘上下文缓存", estimated_cost_usd="0.0013",
        pricing_currency="USD", pricing_effective_on="2026-04-24",
        pricing_snapshot_sha256="b" * 64,
        expires_at=NOW + timedelta(minutes=30), consumed_at=NOW, evidence_ids=("sec-1",),
    )
    analyses.save_draft(StoredAnalysisDraft(
        actor.actor_id, "prepare-1", AnalysisIntent("官方事实如何影响公司？", ("ACME",)), receipt, (),
    ))
    claims = tuple(claim(kind, index) for index, kind in enumerate(
        ("confirmed_fact", "inference", "conditional_scenario", "unknown")
    ))
    analyses.save_run(StoredAnalysisRun(
        actor.actor_id, "run-key",
        AnalysisRunView(
            run_id="run-1",
            draft_id="draft-1",
            status=status,
            stage=status,
            provider="deepseek",
            model="deepseek-v4-flash",
            attempts=1,
            estimated_cost_usd="0.0013",
            actual_cost_usd="0.0002",
            usage=None,
            failure_code=None,
            claims=claims,
            events=(),
            cancellable=False,
        ),
    ))
    return ResearchNotebook(
        store=InMemoryNotebookStore(), analyses=analyses,
        challenge_key=b"notebook-challenge-key" * 2, clock=lambda: NOW,
    ), actor


class PersonalNotebookTest(unittest.TestCase):
    def test_save_reassembles_canonical_claims_and_six_cards_without_browser_model_text(self) -> None:
        notebook, actor = notebook_fixture()
        record = notebook.save_analysis(
            actor, analysis_id="run-1", accepted_claim_ids=("claim-0", "claim-1", "claim-3"),
            user_supplement="用户只补充边界，不回传模型正文。",
            fragments=(PrivateFragmentInput("holding-1", "精确成本只在私有片段"),),
            verification_drafts=(VerificationDraft(
                "claim-1", "下一次披露是否支持？", "公司指引", NOW + timedelta(days=30),
                "SEC", "官方披露出现同方向证据",
            ),), idempotency_key="save-1",
        )

        self.assertEqual(record.current_version, 1)
        self.assertEqual(len(record.versions[0].cards), 6)
        self.assertEqual([item.claim_id for item in record.versions[0].claims], ["claim-0", "claim-1", "claim-3"])
        self.assertFalse(record.formal_research_eligible)
        self.assertEqual(record.private_fragments[0].text, "精确成本只在私有片段")
        self.assertNotIn("精确成本", record.versions[0].user_supplement)
        self.assertEqual(notebook.open(actor, record.record_id), record)

    def test_invalid_claim_and_nonterminal_analysis_cannot_be_saved(self) -> None:
        notebook, actor = notebook_fixture(status="running")
        with self.assertRaisesRegex(ValueError, "analysis_not_saveable"):
            notebook.save_analysis(
                actor, analysis_id="run-1", accepted_claim_ids=("claim-0",),
                user_supplement="", fragments=(), verification_drafts=(), idempotency_key="save-running",
            )

        notebook, actor = notebook_fixture()
        with self.assertRaisesRegex(ValueError, "private_object_not_found"):
            notebook.save_analysis(
                actor, analysis_id="run-1", accepted_claim_ids=("browser-forged-claim",),
                user_supplement="伪造正文", fragments=(), verification_drafts=(), idempotency_key="save-forged",
            )

    def test_evidence_insufficient_only_allows_fact_and_unknown(self) -> None:
        notebook, actor = notebook_fixture(status="evidence_insufficient")
        record = notebook.save_analysis(
            actor, analysis_id="run-1", accepted_claim_ids=("claim-0", "claim-3"),
            user_supplement="", fragments=(), verification_drafts=(), idempotency_key="save-limited",
        )
        self.assertEqual({item.kind for item in record.versions[0].claims}, {"confirmed_fact", "unknown"})
        with self.assertRaisesRegex(ValueError, "analysis_not_saveable"):
            notebook.save_analysis(
                actor, analysis_id="run-1", accepted_claim_ids=("claim-1",),
                user_supplement="", fragments=(), verification_drafts=(), idempotency_key="save-directional",
            )

    def test_supplement_audit_and_observation_append_versions_and_are_idempotent(self) -> None:
        notebook, actor = notebook_fixture()
        saved = notebook.save_analysis(
            actor, analysis_id="run-1", accepted_claim_ids=("claim-0", "claim-1"),
            user_supplement="初始说明", fragments=(),
            verification_drafts=(VerificationDraft("claim-1", "验证问题", "指标", None, "SEC", "判别条件"),),
            idempotency_key="save-versions",
        )
        supplemented = notebook.append_supplement(
            actor, record_id=saved.record_id, expected_version=1, supplement="补充说明",
            fragments=(), idempotency_key="supplement-1",
        )
        replay = notebook.append_supplement(
            actor, record_id=saved.record_id, expected_version=1, supplement="不会覆盖",
            fragments=(), idempotency_key="supplement-1",
        )
        self.assertEqual(replay.current_version, 2)
        audited = notebook.start_reasoning_audit(
            actor, record_id=saved.record_id, expected_version=2, idempotency_key="audit-1",
        )
        observed = notebook.append_verification_observation(
            actor, record_id=saved.record_id, expected_version=3,
            item_id=saved.verification_items[0].item_id, result="contradicts",
            evidence_ids=("sec-2",), note="新披露形成反对证据", idempotency_key="observe-1",
        )
        self.assertEqual([item.version for item in observed.versions], [1, 2, 3, 4])
        self.assertEqual(audited.versions[-1].derived_relation, "reasoning_audit")
        self.assertEqual(observed.verification_items[0].observations[0].result, "contradicts")
        self.assertEqual(observed.versions[0].content_sha256, saved.versions[0].content_sha256)

    def test_revision_conflict_state_machine_and_purge_tombstone(self) -> None:
        notebook, actor = notebook_fixture()
        saved = notebook.save_analysis(
            actor, analysis_id="run-1", accepted_claim_ids=("claim-0",), user_supplement="",
            fragments=(), verification_drafts=(), idempotency_key="save-purge",
        )
        with self.assertRaisesRegex(ValueError, "revision_conflict"):
            notebook.append_supplement(
                actor, record_id=saved.record_id, expected_version=0, supplement="过期写入",
                fragments=(), idempotency_key="stale",
            )
        trashed = notebook.change_state(
            actor, record_id=saved.record_id, expected_version=1, state="trashed", idempotency_key="trash",
        )
        challenge = notebook.request_purge(actor, record_id=saved.record_id, expected_version=2)
        purged = notebook.confirm_purge(
            actor, record_id=saved.record_id, expected_version=2, challenge=challenge.challenge,
            idempotency_key="purge",
        )
        self.assertEqual(purged.state, "purged")
        self.assertEqual(purged.versions, ())
        self.assertEqual(purged.title, "已永久删除的个人记录")
        self.assertEqual(purged.backup_status, "expires_within_window")
        self.assertEqual(purged.backup_expires_at, NOW + timedelta(days=30))


if __name__ == "__main__":
    unittest.main()
