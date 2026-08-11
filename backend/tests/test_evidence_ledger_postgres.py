from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
import unittest
from uuid import uuid4

from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from backend.app.database import alembic_config, current_schema_heads, expected_schema_heads
from backend.app.personal_workspace.agent.evidence import (
    CapabilityAuditEvent,
    EvidenceLedgerError,
    EvidenceReadContext,
    EvidenceRecord,
    PostgresEvidenceStore,
)
from backend.app.personal_workspace.crypto import FixedKeyring, PersonalDataCipher


NOW = datetime(2026, 8, 11, 5, 0, tzinfo=timezone.utc)
RETENTION_BY_AUTHORIZATION = {
    ("investment-news", "news-auth-v1"): "encrypted_payload",
    ("ephemeral-news", "news-auth-v1"): "metadata_only",
}


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要隔离 PostgreSQL")
class PostgresEvidenceLedgerContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(os.environ["TEST_POSTGRES_URL"], pool_pre_ping=True)
        with cls.engine.connect() as connection:
            command.upgrade(alembic_config(connection), "head")
            assert current_schema_heads(connection) == expected_schema_heads()
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False)
        cls.cipher = PersonalDataCipher(
            FixedKeyring(
                active_key_id="evidence-key",
                data_keys={"evidence-key": bytes(range(32))},
                lookup_key=b"evidence-postgres-lookup-key-32-bytes",
            )
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def _context(self, actor_id: str, *, now: datetime = NOW) -> EvidenceReadContext:
        return EvidenceReadContext(
            actor_id=actor_id,
            permissions=frozenset({"news:read"}),
            purpose="ai_context",
            now=now,
        )

    def _record(self, evidence_id: str, **overrides) -> EvidenceRecord:
        values = {
            "evidence_id": evidence_id,
            "logical_identity": "news:https://example.test/acme",
            "scope": "actor",
            "source": "investment-news",
            "content_sha256": "",
            "authorized_fields": ("headline", "summary"),
            "required_permissions": frozenset({"news:read"}),
            "allowed_purposes": frozenset({"ai_context"}),
            "authorization_snapshot_id": "news-auth-v1",
            "observed_at": NOW,
            "published_at": NOW - timedelta(minutes=10),
            "effective_at": None,
            "available_from": NOW - timedelta(minutes=9),
            "fetched_at": NOW,
            "verified_at": NOW,
            "expires_at": NOW + timedelta(minutes=30),
            "persistence": "encrypted_payload",
            "payload": {"headline": "private headline", "summary": "private summary"},
        }
        values.update(overrides)
        if "content_sha256" not in overrides and values["payload"] is not None:
            values["content_sha256"] = sha256(
                json.dumps(
                    values["payload"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
        return EvidenceRecord(**values)

    def test_encrypted_restart_actor_conflict_expiry_and_metadata_retention(self) -> None:
        suffix = str(uuid4())
        actor_a = f"evidence-pg-a-{suffix}"
        actor_b = f"evidence-pg-b-{suffix}"
        evidence_id = f"news:{suffix}"
        context_a = self._context(actor_a)
        store = PostgresEvidenceStore(
            self.Session,
            cipher=self.cipher,
            retention_by_authorization=RETENTION_BY_AUTHORIZATION,
        )
        stored = store.put(context_a, self._record(evidence_id))

        restarted = PostgresEvidenceStore(
            self.Session,
            cipher=self.cipher,
            retention_by_authorization=RETENTION_BY_AUTHORIZATION,
        )
        self.assertEqual(restarted.read(context_a, evidence_id), stored)
        mismatched_reader = PostgresEvidenceStore(
            self.Session,
            cipher=self.cipher,
            retention_by_authorization={
                **RETENTION_BY_AUTHORIZATION,
                ("investment-news", "news-auth-v1"): "metadata_only",
            },
        )
        with self.assertRaisesRegex(
            EvidenceLedgerError, "source_retention_mismatch"
        ):
            mismatched_reader.read(context_a, evidence_id)
        with self.assertRaisesRegex(
            EvidenceLedgerError, "source_retention_mismatch"
        ):
            mismatched_reader.put(context_a, self._record(evidence_id))
        with self.assertRaisesRegex(EvidenceLedgerError, "evidence_not_found"):
            restarted.read(self._context(actor_b), evidence_id)
        with self.assertRaisesRegex(EvidenceLedgerError, "evidence_identity_conflict"):
            restarted.put(
                context_a,
                self._record(
                    evidence_id,
                    payload={
                        "headline": "changed headline",
                        "summary": "changed summary",
                    },
                ),
            )
        with self.assertRaisesRegex(EvidenceLedgerError, "evidence_expired"):
            restarted.freeze(
                self._context(actor_a, now=NOW + timedelta(hours=1)),
                (evidence_id,),
            )

        metadata_id = f"metadata:{suffix}"
        metadata = restarted.put(
            context_a,
            self._record(
                metadata_id,
                source="ephemeral-news",
                persistence="encrypted_payload",
                payload={"headline": "must not persist", "summary": "must not persist"},
            ),
        )
        self.assertIsNone(metadata.payload)
        self.assertIsNone(restarted.read(context_a, metadata_id).payload)
        with self.assertRaisesRegex(EvidenceLedgerError, "source_retention_unknown"):
            restarted.put(
                context_a,
                replace(
                    self._record(f"forged:{suffix}"),
                    authorization_snapshot_id="forged-snapshot",
                ),
            )

        with self.engine.connect() as connection:
            table_names = set(inspect(self.engine).get_table_names(schema="private_workbench"))
            raw_projection = "|".join(
                str(value)
                for row in connection.execute(
                    text(
                        "select evidence_id_hmac, logical_identity_hmac, ciphertext, nonce "
                        "from private_workbench.personal_tool_evidence_records "
                        "where evidence_id_hmac = :lookup"
                    ),
                    {
                        "lookup": self.cipher.scoped_lookup(
                            workspace_id=self._workspace_id(actor_a), value=evidence_id
                        )
                    },
                )
                for value in row
            )
        self.assertTrue(
            {
                "personal_tool_evidence_records",
                "personal_capability_audit_events",
            }.issubset(table_names)
        )
        for private_value in (evidence_id, "private headline", "private summary"):
            self.assertNotIn(private_value, raw_projection)

        forged = replace(
            self._record(evidence_id),
            persistence="metadata_only",
            payload=None,
            authorized_fields=(),
            required_permissions=frozenset(),
        )
        with self.assertRaisesRegex(
            EvidenceLedgerError, "evidence_permission_denied"
        ):
            restarted.put(
                EvidenceReadContext(
                    actor_id=actor_a,
                    permissions=frozenset(),
                    purpose="ai_context",
                    now=NOW,
                ),
                forged,
            )
        forged_different_content = self._record(
            evidence_id,
            payload={
                "headline": "different private headline",
                "summary": "different private summary",
            },
            required_permissions=frozenset(),
        )
        with self.assertRaisesRegex(
            EvidenceLedgerError, "evidence_permission_denied"
        ):
            restarted.put(
                EvidenceReadContext(
                    actor_id=actor_a,
                    permissions=frozenset(),
                    purpose="ai_context",
                    now=NOW,
                ),
                forged_different_content,
            )

        future_id = f"future:{suffix}"
        restarted.put(
            context_a,
            self._record(
                future_id,
                available_from=NOW + timedelta(minutes=1),
            ),
        )
        with self.assertRaisesRegex(EvidenceLedgerError, "evidence_not_available"):
            restarted.read(context_a, future_id)
        with self.assertRaisesRegex(EvidenceLedgerError, "evidence_not_available"):
            restarted.freeze(context_a, (future_id,))

    def test_capability_audit_persists_only_hash_and_minimal_projection(self) -> None:
        suffix = str(uuid4())
        actor = f"audit-pg-{suffix}"
        context = self._context(actor)
        secret_arguments = '{"symbol":"PRIVATE-ACME"}'
        secret_result = "PRIVATE-RESULT-PAYLOAD"
        store = PostgresEvidenceStore(
            self.Session,
            cipher=self.cipher,
            retention_by_authorization=RETENTION_BY_AUTHORIZATION,
        )
        raw_evidence_id = f"evidence:PRIVATE-TICKER:{suffix}"
        store.append_audit(
            context,
            CapabilityAuditEvent(
                request_id=f"request-{suffix}",
                channel="mcp",
                canonical_tool="get_symbol_dossier",
                arguments_sha256=sha256(secret_arguments.encode()).hexdigest(),
                status="success",
                evidence_ids=(raw_evidence_id,),
                policy_revision="mcp-v1",
                started_at=NOW,
                completed_at=NOW + timedelta(seconds=1),
            ),
        )

        with self.engine.connect() as connection:
            columns = {
                column["name"]
                for column in inspect(self.engine).get_columns(
                    "personal_capability_audit_events", schema="private_workbench"
                )
            }
            projection = "|".join(
                str(value)
                for row in connection.execute(
                    text(
                        "select * from private_workbench.personal_capability_audit_events "
                        "where request_id = :request_id"
                    ),
                    {"request_id": f"request-{suffix}"},
                )
                for value in row
            )
            evidence_id_hmacs = connection.scalar(
                text(
                    "select evidence_id_hmacs "
                    "from private_workbench.personal_capability_audit_events "
                    "where request_id = :request_id"
                ),
                {"request_id": f"request-{suffix}"},
            )
        self.assertFalse({"arguments", "result", "payload"} & columns)
        self.assertNotIn("evidence_ids", columns)
        self.assertNotIn(secret_arguments, projection)
        self.assertNotIn(secret_result, projection)
        self.assertNotIn(raw_evidence_id, projection)
        self.assertEqual(
            evidence_id_hmacs,
            [
                self.cipher.scoped_lookup(
                    workspace_id=self._workspace_id(actor),
                    value=raw_evidence_id,
                )
            ],
        )

    def _workspace_id(self, actor_id: str) -> str:
        with self.engine.connect() as connection:
            return str(
                connection.scalar(
                    text(
                        "select id from private_workbench.personal_workspaces "
                        "where actor_identity_hash = :actor_hash"
                    ),
                    {"actor_hash": sha256(actor_id.encode()).hexdigest()},
                )
            )


if __name__ == "__main__":
    unittest.main()
