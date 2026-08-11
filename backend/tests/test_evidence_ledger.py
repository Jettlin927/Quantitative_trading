from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import unittest

from backend.app.personal_workspace.agent.evidence import (
    CapabilityAuditEvent,
    EvidenceLedgerError,
    EvidenceReadContext,
    EvidenceRecord,
    InMemoryEvidenceStore,
)


NOW = datetime(2026, 8, 11, 5, 0, tzinfo=timezone.utc)


def _context(
    actor_id: str = "actor-a",
    *,
    permissions: frozenset[str] = frozenset({"market:read"}),
    purpose: str = "ai_context",
    now: datetime = NOW,
) -> EvidenceReadContext:
    return EvidenceReadContext(
        actor_id=actor_id,
        permissions=permissions,
        purpose=purpose,
        now=now,
    )


def _record(**overrides) -> EvidenceRecord:
    values = {
        "evidence_id": "market:ACME:2026-08-11",
        "logical_identity": "market:ACME:daily-bar",
        "scope": "actor",
        "source": "alpaca",
        "content_sha256": "",
        "authorized_fields": ("symbol", "close"),
        "required_permissions": frozenset({"market:read"}),
        "allowed_purposes": frozenset({"ai_context", "display"}),
        "authorization_snapshot_id": "alpaca-auth-v1",
        "observed_at": NOW - timedelta(minutes=1),
        "published_at": None,
        "effective_at": None,
        "available_from": NOW - timedelta(minutes=1),
        "fetched_at": NOW,
        "verified_at": NOW,
        "expires_at": NOW + timedelta(minutes=5),
        "persistence": "encrypted_payload",
        "payload": {"symbol": "ACME", "close": "101.25"},
    }
    values.update(overrides)
    if "content_sha256" not in overrides and values["payload"] is not None:
        values["content_sha256"] = _payload_hash(values["payload"])
    return EvidenceRecord(**values)


def _payload_hash(payload) -> str:
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


class EvidenceLedgerContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryEvidenceStore(
            retention_by_authorization={
                ("alpaca", "alpaca-auth-v1"): "encrypted_payload",
                ("ephemeral-news", "alpaca-auth-v1"): "metadata_only",
            }
        )

    def test_identity_is_immutable_and_ttl_does_not_change_identity(self) -> None:
        original = self.store.put(_context(), _record())
        same_content_new_ttl = self.store.put(
            _context(),
            replace(_record(), expires_at=NOW + timedelta(hours=1)),
        )

        self.assertEqual(same_content_new_ttl, original)
        self.assertEqual(same_content_new_ttl.expires_at, NOW + timedelta(minutes=5))
        with self.assertRaisesRegex(
            EvidenceLedgerError, "evidence_identity_conflict"
        ):
            self.store.put(
                _context(),
                _record(payload={"symbol": "ACME", "close": "102.00"}),
            )

    def test_actor_permission_purpose_and_field_boundaries_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            EvidenceLedgerError, "evidence_permission_denied"
        ):
            self.store.put(_context(permissions=frozenset()), _record())
        with self.assertRaisesRegex(
            EvidenceLedgerError, "evidence_purpose_denied"
        ):
            self.store.put(_context(purpose="formal_research"), _record())
        self.store.put(_context(), _record())

        with self.assertRaisesRegex(EvidenceLedgerError, "evidence_not_found"):
            self.store.read(_context("actor-b"), _record().evidence_id)
        with self.assertRaisesRegex(
            EvidenceLedgerError, "evidence_permission_denied"
        ):
            self.store.read(
                _context(permissions=frozenset()), _record().evidence_id
            )
        with self.assertRaisesRegex(
            EvidenceLedgerError, "evidence_purpose_denied"
        ):
            self.store.read(
                _context(purpose="formal_research"), _record().evidence_id
            )
        with self.assertRaisesRegex(
            EvidenceLedgerError, "evidence_field_unauthorized"
        ):
            _record(payload={"symbol": "ACME", "secret": "forbidden"})

    def test_idempotent_put_reauthorizes_the_existing_record(self) -> None:
        self.store.put(_context(), _record())
        forged = replace(
            _record(),
            persistence="metadata_only",
            payload=None,
            authorized_fields=(),
            required_permissions=frozenset(),
        )

        with self.assertRaisesRegex(
            EvidenceLedgerError, "evidence_permission_denied"
        ):
            self.store.put(
                _context(permissions=frozenset()),
                forged,
            )
        forged_different_content = _record(
            payload={"symbol": "ACME", "close": "999.00"},
            required_permissions=frozenset(),
        )
        with self.assertRaisesRegex(
            EvidenceLedgerError, "evidence_permission_denied"
        ):
            self.store.put(
                _context(permissions=frozenset()),
                forged_different_content,
            )

    def test_mutable_authorization_inputs_are_copied_and_normalized(self) -> None:
        fields = ["symbol", "close"]
        permissions = {"market:read"}
        purposes = {"ai_context"}
        record = _record(
            authorized_fields=fields,
            required_permissions=permissions,
            allowed_purposes=purposes,
        )
        fields.clear()
        permissions.clear()
        purposes.clear()

        self.assertEqual(record.authorized_fields, ("symbol", "close"))
        self.assertEqual(record.required_permissions, frozenset({"market:read"}))
        self.assertEqual(record.allowed_purposes, frozenset({"ai_context"}))
        self.store.put(_context(), record)
        with self.assertRaisesRegex(
            EvidenceLedgerError, "evidence_permission_denied"
        ):
            self.store.read(_context(permissions=frozenset()), record.evidence_id)

    def test_expired_record_is_readable_but_cannot_form_a_new_freeze(self) -> None:
        self.store.put(_context(), _record())
        frozen = self.store.freeze(_context(), (_record().evidence_id,))[0]

        later = _context(now=NOW + timedelta(minutes=6))
        self.assertEqual(
            self.store.read(later, _record().evidence_id).content_sha256,
            frozen.content_sha256,
        )
        with self.assertRaisesRegex(EvidenceLedgerError, "evidence_expired"):
            self.store.freeze(later, (_record().evidence_id,))
        self.assertEqual(frozen.frozen_at, NOW)
        self.assertEqual(frozen.payload, {"symbol": "ACME", "close": "101.25"})

    def test_metadata_only_retention_never_keeps_payload(self) -> None:
        stored = self.store.put(
            _context(),
            replace(
                _record(),
                source="ephemeral-news",
                persistence="encrypted_payload",
            ),
        )

        self.assertIsNone(stored.payload)
        self.assertIsNone(self.store.read(_context(), stored.evidence_id).payload)
        self.assertIsNone(self.store.freeze(_context(), (stored.evidence_id,))[0].payload)

        with self.assertRaisesRegex(EvidenceLedgerError, "source_retention_unknown"):
            self.store.put(_context(), replace(_record(), source="unknown-source"))
        with self.assertRaisesRegex(EvidenceLedgerError, "source_retention_unknown"):
            self.store.put(
                _context(),
                replace(_record(), authorization_snapshot_id="forged-snapshot"),
            )

    def test_payload_hash_mismatch_and_non_finite_json_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            EvidenceLedgerError, "evidence_content_hash_mismatch"
        ):
            _record(content_sha256="a" * 64)
        with self.assertRaisesRegex(
            EvidenceLedgerError, "evidence_payload_non_finite"
        ):
            _record(
                payload={"symbol": "ACME", "close": float("nan")},
                content_sha256="a" * 64,
            )
        with self.assertRaisesRegex(
            EvidenceLedgerError, "evidence_payload_non_finite"
        ):
            _record(
                payload={"symbol": "ACME", "close": float("inf")},
                content_sha256="a" * 64,
            )

    def test_record_and_frozen_payloads_are_recursively_immutable_and_serializable(self) -> None:
        record = _record(
            authorized_fields=("symbol", "close", "details"),
            payload={
                "symbol": "ACME",
                "close": "101.25",
                "details": {"tags": ["market", "daily"]},
            },
        )
        stored = self.store.put(_context(), record)
        frozen = self.store.freeze(_context(), (record.evidence_id,))[0]

        self.assertEqual(json.loads(json.dumps(stored.payload, default=dict)), {
            "symbol": "ACME",
            "close": "101.25",
            "details": {"tags": ["market", "daily"]},
        })
        with self.assertRaises(TypeError):
            stored.payload["symbol"] = "TAMPERED"  # type: ignore[index]
        with self.assertRaises(TypeError):
            frozen.payload["details"]["tags"] = ()  # type: ignore[index]
        self.assertEqual(stored.content_sha256, frozen.content_sha256)

    def test_available_from_blocks_future_read_and_freeze(self) -> None:
        record = _record(available_from=NOW + timedelta(minutes=1))
        self.store.put(_context(), record)

        with self.assertRaisesRegex(EvidenceLedgerError, "evidence_not_available"):
            self.store.read(_context(), record.evidence_id)
        with self.assertRaisesRegex(EvidenceLedgerError, "evidence_not_available"):
            self.store.freeze(_context(), (record.evidence_id,))

    def test_audit_contract_accepts_hash_and_contains_no_arguments_or_result(self) -> None:
        evidence_ids = ["evidence-1"]
        event = CapabilityAuditEvent(
            request_id="request-1",
            channel="runtime",
            canonical_tool="get_symbol_dossier",
            arguments_sha256=sha256(b'{"symbol":"ACME"}').hexdigest(),
            status="success",
            evidence_ids=evidence_ids,  # type: ignore[arg-type]
            policy_revision="policy-v1",
            started_at=NOW,
            completed_at=NOW + timedelta(seconds=1),
        )
        evidence_ids.clear()
        self.store.append_audit(_context(), event)

        self.assertEqual(event.evidence_ids, ("evidence-1",))
        self.assertEqual(self.store.audits_for_actor("actor-a"), (event,))
        self.assertNotIn("arguments", CapabilityAuditEvent.__dataclass_fields__)
        self.assertNotIn("result", CapabilityAuditEvent.__dataclass_fields__)
        with self.assertRaisesRegex(
            EvidenceLedgerError, "invalid_audit_evidence_id"
        ):
            CapabilityAuditEvent(
                request_id="request-invalid-evidence",
                channel="runtime",
                canonical_tool="get_symbol_dossier",
                arguments_sha256=sha256(b"{}").hexdigest(),
                status="failed",
                evidence_ids=("",),
                policy_revision="policy-v1",
                started_at=NOW,
            )


if __name__ == "__main__":
    unittest.main()
