from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models import (
    PersonalAnalysisDraft,
    PersonalHolding,
    PersonalRuleEvaluation,
    PersonalWorkspace,
)

from .crypto import EncryptedEnvelope


@dataclass(frozen=True)
class StoredEncryptedRow:
    kind: str
    row_id: str
    envelope: EncryptedEnvelope
    aad: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class StoredSyntheticTrace:
    actor_id: str
    analysis_id: str
    idempotency_key: str
    preview_sha256: str
    envelope: EncryptedEnvelope
    aad: str
    workspace_id: str
    supporting_rows: tuple[StoredEncryptedRow, ...] = ()


class InMemoryPersonalJourneyStore:
    """只供 synthetic tracer 单测使用；生产持久化由 PostgreSQL Adapter 提供。"""

    def __init__(self) -> None:
        self._traces_by_key: dict[tuple[str, str], StoredSyntheticTrace] = {}
        self._traces_by_id: dict[tuple[str, str], StoredSyntheticTrace] = {}
        self._workspace_by_actor: dict[str, str] = {}

    def workspace_id_for_actor(self, *, actor_id: str) -> str | None:
        return self._workspace_by_actor.get(actor_id)

    def get_trace_by_idempotency(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
    ) -> StoredSyntheticTrace | None:
        return self._traces_by_key.get((actor_id, idempotency_key))

    def get_trace(self, *, actor_id: str, analysis_id: str) -> StoredSyntheticTrace | None:
        return self._traces_by_id.get((actor_id, analysis_id))

    def latest_trace(self, *, actor_id: str) -> StoredSyntheticTrace | None:
        traces = [value for (owner, _), value in self._traces_by_id.items() if owner == actor_id]
        return traces[-1] if traces else None

    def save_trace(self, trace: StoredSyntheticTrace) -> StoredSyntheticTrace:
        key = (trace.actor_id, trace.idempotency_key)
        existing = self._traces_by_key.get(key)
        if existing is not None:
            return existing
        self._traces_by_key[key] = trace
        self._traces_by_id[(trace.actor_id, trace.analysis_id)] = trace
        self._workspace_by_actor.setdefault(trace.actor_id, trace.workspace_id)
        return trace

    def raw_bytes(self) -> bytes:
        values: list[bytes] = []
        for stored in self._traces_by_id.values():
            values.extend(
                [
                    stored.envelope.ciphertext,
                    stored.envelope.nonce,
                    stored.envelope.key_id.encode("utf-8"),
                    stored.envelope.payload_schema.encode("utf-8"),
                ]
            )
            if isinstance(stored, StoredSyntheticTrace):
                for row in stored.supporting_rows:
                    values.extend([row.envelope.ciphertext, row.envelope.nonce])
        return b"|".join(values)


class PostgresPersonalJourneyStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def workspace_id_for_actor(self, *, actor_id: str) -> str | None:
        with self._session_factory() as session:
            return session.scalar(
                select(PersonalWorkspace.id).where(
                    PersonalWorkspace.actor_identity_hash == _identity_hash(actor_id)
                )
            )

    def get_trace_by_idempotency(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
    ) -> StoredSyntheticTrace | None:
        with self._session_factory() as session:
            workspace_id = self._workspace_id(session, actor_id)
            if workspace_id is None:
                return None
            row = session.scalar(
                select(PersonalAnalysisDraft).where(
                    PersonalAnalysisDraft.workspace_id == workspace_id,
                    PersonalAnalysisDraft.synthetic.is_(True),
                    PersonalAnalysisDraft.idempotency_hash
                    == _idempotency_hash(actor_id, idempotency_key),
                )
            )
            return self._stored_trace(actor_id, row) if row is not None else None

    def get_trace(self, *, actor_id: str, analysis_id: str) -> StoredSyntheticTrace | None:
        with self._session_factory() as session:
            workspace_id = self._workspace_id(session, actor_id)
            if workspace_id is None:
                return None
            row = session.get(PersonalAnalysisDraft, analysis_id)
            if row is None or row.workspace_id != workspace_id or not row.synthetic:
                return None
            return self._stored_trace(actor_id, row)

    def latest_trace(self, *, actor_id: str) -> StoredSyntheticTrace | None:
        with self._session_factory() as session:
            workspace_id = self._workspace_id(session, actor_id)
            if workspace_id is None:
                return None
            row = session.scalar(
                select(PersonalAnalysisDraft)
                .where(
                    PersonalAnalysisDraft.workspace_id == workspace_id,
                    PersonalAnalysisDraft.synthetic.is_(True),
                )
                .order_by(PersonalAnalysisDraft.created_at.desc(), PersonalAnalysisDraft.id.desc())
                .limit(1)
            )
            return self._stored_trace(actor_id, row) if row is not None else None

    def save_trace(self, trace: StoredSyntheticTrace) -> StoredSyntheticTrace:
        with self._session_factory() as session:
            existing = self._find_trace_by_key(session, trace.actor_id, trace.idempotency_key)
            if existing is not None:
                return self._stored_trace(trace.actor_id, existing)
            try:
                self._save_supporting_rows(session, trace)
                session.flush()
                session.add(
                    PersonalAnalysisDraft(
                        id=trace.analysis_id,
                        workspace_id=trace.workspace_id,
                        status="ready",
                        preview_sha256=trace.preview_sha256,
                        idempotency_hash=_idempotency_hash(trace.actor_id, trace.idempotency_key),
                        synthetic=True,
                        **_envelope_values(trace.envelope),
                    )
                )
                session.commit()
                return trace
            except IntegrityError:
                session.rollback()
                existing = self._find_trace_by_key(session, trace.actor_id, trace.idempotency_key)
                if existing is None:
                    raise
                return self._stored_trace(trace.actor_id, existing)

    def _save_supporting_rows(self, session: Session, trace: StoredSyntheticTrace) -> None:
        for row in trace.supporting_rows:
            values = _envelope_values(row.envelope)
            if row.kind == "workspace":
                if session.get(PersonalWorkspace, row.row_id) is None:
                    session.add(
                        PersonalWorkspace(
                            id=row.row_id,
                            actor_identity_hash=_identity_hash(trace.actor_id),
                            revision=1,
                            **values,
                        )
                    )
                    session.flush()
            elif row.kind == "holding":
                existing = session.scalar(
                    select(PersonalHolding).where(
                        PersonalHolding.workspace_id == trace.workspace_id,
                        PersonalHolding.symbol_hmac == row.metadata["symbol_hmac"],
                    )
                )
                if existing is None:
                    session.add(
                        PersonalHolding(
                            id=row.row_id,
                            workspace_id=trace.workspace_id,
                            state="active",
                            synthetic=True,
                            symbol_hmac=row.metadata["symbol_hmac"],
                            revision=1,
                            **values,
                        )
                    )
            elif row.kind == "rules":
                session.add(
                    PersonalRuleEvaluation(
                        id=row.row_id,
                        workspace_id=trace.workspace_id,
                        result_summary="four_state_synthetic",
                        synthetic=True,
                        **values,
                    )
                )

    def _workspace_id(self, session: Session, actor_id: str) -> str | None:
        return session.scalar(
            select(PersonalWorkspace.id).where(
                PersonalWorkspace.actor_identity_hash == _identity_hash(actor_id)
            )
        )

    def _find_trace_by_key(self, session: Session, actor_id: str, idempotency_key: str):
        workspace_id = self._workspace_id(session, actor_id)
        if workspace_id is None:
            return None
        return session.scalar(
            select(PersonalAnalysisDraft).where(
                PersonalAnalysisDraft.workspace_id == workspace_id,
                PersonalAnalysisDraft.synthetic.is_(True),
                PersonalAnalysisDraft.idempotency_hash
                == _idempotency_hash(actor_id, idempotency_key),
            )
        )

    @staticmethod
    def _stored_trace(actor_id: str, row: PersonalAnalysisDraft) -> StoredSyntheticTrace:
        return StoredSyntheticTrace(
            actor_id=actor_id,
            analysis_id=row.id,
            idempotency_key="",
            preview_sha256=row.preview_sha256,
            envelope=_row_envelope(row),
            aad=_aad("personal_analysis_drafts", row.id),
            workspace_id=row.workspace_id,
        )

def _identity_hash(actor_id: str) -> str:
    return sha256(actor_id.encode("utf-8")).hexdigest()


def _idempotency_hash(actor_id: str, idempotency_key: str) -> str:
    return sha256(f"{actor_id}|{idempotency_key}".encode("utf-8")).hexdigest()


def _envelope_values(envelope: EncryptedEnvelope) -> dict[str, Any]:
    return {
        "ciphertext": envelope.ciphertext,
        "nonce": envelope.nonce,
        "key_id": envelope.key_id,
        "payload_schema": envelope.payload_schema,
    }


def _row_envelope(row) -> EncryptedEnvelope:
    return EncryptedEnvelope(
        ciphertext=bytes(row.ciphertext),
        nonce=bytes(row.nonce),
        key_id=row.key_id,
        payload_schema=row.payload_schema,
    )


def _aad(table: str, row_id: str) -> str:
    return f"private_workbench|{table}|{row_id}|payload|1"
