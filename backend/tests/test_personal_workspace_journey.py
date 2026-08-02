from __future__ import annotations

import unittest

from backend.app.personal_workspace.contracts import PersonalActor
from backend.app.personal_workspace.crypto import FixedKeyring, PersonalDataCipher
from backend.app.personal_workspace.journey import PersonalResearchJourney
from backend.app.personal_workspace.persistence import InMemoryPersonalJourneyStore
from backend.app.personal_workspace.synthetic import SyntheticWorkspaceAdapters


class PersonalResearchJourneyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryPersonalJourneyStore()
        self.adapters = SyntheticWorkspaceAdapters(provider_available=False)
        self.journey = PersonalResearchJourney(
            store=self.store,
            cipher=PersonalDataCipher(
                FixedKeyring(
                    active_key_id="synthetic-key",
                    data_keys={"synthetic-key": bytes(range(32))},
                    lookup_key=b"synthetic-lookup-key-for-tests-only",
                )
            ),
            adapters=self.adapters,
        )
        self.actor = PersonalActor(actor_id="local-owner")

    def test_synthetic_trace_is_idempotent_and_keeps_provider_data_out_of_ai_preview(self) -> None:
        first = self.journey.create_synthetic_trace(
            self.actor,
            idempotency_key="trace-001",
            question="这个虚构事件可能通过什么机制影响合成标的？",
        )
        second = self.journey.create_synthetic_trace(
            self.actor,
            idempotency_key="trace-001",
            question="这个虚构事件可能通过什么机制影响合成标的？",
        )

        self.assertEqual(first.analysis_id, second.analysis_id)
        self.assertTrue(first.synthetic)
        self.assertFalse(first.research_eligible)
        self.assertEqual(first.holding.symbol, "SYNTH-001")
        self.assertEqual(first.holding.quantity, "12.5000")
        self.assertEqual(first.market.source_health, "unavailable")
        self.assertEqual(first.market.bars[0].close, "80.0000")
        self.assertEqual(
            [evaluation.result for evaluation in first.rule_evaluations],
            ["hit", "not_hit", "insufficient_data", "calculation_failed"],
        )
        self.assertEqual(first.analysis_preview.status, "ready")
        self.assertEqual(first.analysis_preview.provider, "synthetic-model")
        self.assertIn("user_symbol", first.analysis_preview.included_fields)
        self.assertEqual(
            [item.field for item in first.analysis_preview.excluded_fields],
            [
                "market_prices",
                "derived_indicators",
                "portfolio_weight",
                "unrealized_return",
                "price_rule_results",
            ],
        )
        self.assertIn("provider_unavailable", first.issues)
        self.assertEqual(first.analysis_claim.kind, "inference")
        self.assertEqual(
            set(self.adapters.analysis_model.captured_payloads[0]),
            {"user_symbol", "user_question", "official_evidence_excerpt"},
        )
        captured_text = str(self.adapters.analysis_model.captured_payloads[0])
        for denied_value in ("80.0000", "12.5000", "portfolio_weight", "price_rule_results"):
            self.assertNotIn(denied_value, captured_text)

        raw_store = self.store.raw_bytes()
        for private_value in (
            b"SYNTH-001",
            b"12.5000",
            "这个虚构事件".encode("utf-8"),
        ):
            self.assertNotIn(private_value, raw_store)

    def test_record_requires_same_preview_hash_and_is_immutable(self) -> None:
        trace = self.journey.create_synthetic_trace(
            self.actor,
            idempotency_key="trace-002",
            question="保存前先确认外发字段。",
        )

        with self.assertRaisesRegex(ValueError, "preview_changed"):
            self.journey.save_synthetic_record(
                self.actor,
                analysis_id=trace.analysis_id,
                preview_sha256="0" * 64,
                idempotency_key="record-002",
            )

        record = self.journey.save_synthetic_record(
            self.actor,
            analysis_id=trace.analysis_id,
            preview_sha256=trace.analysis_preview.preview_sha256,
            idempotency_key="record-002",
        )
        repeated = self.journey.save_synthetic_record(
            self.actor,
            analysis_id=trace.analysis_id,
            preview_sha256=trace.analysis_preview.preview_sha256,
            idempotency_key="record-002",
        )

        self.assertEqual(record.record_id, repeated.record_id)
        self.assertEqual(record.version, 1)
        self.assertTrue(record.synthetic)
        self.assertFalse(record.research_eligible)
        self.assertEqual(record.status, "saved")

        today = self.journey.open_today(self.actor)
        self.assertEqual(today.record.record_id, record.record_id)
        self.assertEqual(today.analysis_preview.preview_sha256, trace.analysis_preview.preview_sha256)


if __name__ == "__main__":
    unittest.main()
