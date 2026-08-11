"""工具证据的身份、授权、读取、冻结与持久化合同。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import math
from threading import RLock
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping, Protocol
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.models import (
    PersonalCapabilityAuditEvent,
    PersonalToolEvidenceRecord,
    PersonalWorkspace,
)

from ..crypto import EncryptedEnvelope, PersonalDataCipher


Persistence = Literal["encrypted_payload", "metadata_only"]
EvidenceWorkspaceMode = Literal["create_if_missing", "existing_only"]


class EvidenceLedgerError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    logical_identity: str
    scope: Literal["actor"]
    source: str
    content_sha256: str
    authorized_fields: tuple[str, ...]
    required_permissions: frozenset[str]
    allowed_purposes: frozenset[str]
    authorization_snapshot_id: str
    observed_at: datetime | None
    published_at: datetime | None
    effective_at: datetime | None
    available_from: datetime | None
    fetched_at: datetime
    verified_at: datetime | None
    expires_at: datetime | None
    persistence: Persistence
    payload: Mapping[str, Any] | None

    def __post_init__(self) -> None:
        if not self.evidence_id or not self.logical_identity or not self.source:
            raise EvidenceLedgerError("invalid_evidence_identity")
        if self.scope != "actor":
            raise EvidenceLedgerError("unsupported_evidence_scope")
        if self.persistence not in {"encrypted_payload", "metadata_only"}:
            raise EvidenceLedgerError("invalid_evidence_persistence")
        if len(self.content_sha256) != 64 or any(
            value not in "0123456789abcdef" for value in self.content_sha256
        ):
            raise EvidenceLedgerError("invalid_content_sha256")
        if (
            not isinstance(self.authorization_snapshot_id, str)
            or not self.authorization_snapshot_id.strip()
        ):
            raise EvidenceLedgerError("missing_authorization_snapshot")
        authorized_fields = _string_tuple(
            self.authorized_fields, code="invalid_authorized_field"
        )
        if len(set(authorized_fields)) != len(authorized_fields):
            raise EvidenceLedgerError("duplicate_authorized_field")
        required_permissions = _string_frozenset(
            self.required_permissions, code="invalid_required_permission"
        )
        allowed_purposes = _string_frozenset(
            self.allowed_purposes, code="invalid_allowed_purpose"
        )
        if not allowed_purposes:
            raise EvidenceLedgerError("missing_allowed_purpose")
        object.__setattr__(self, "authorized_fields", authorized_fields)
        object.__setattr__(self, "required_permissions", required_permissions)
        object.__setattr__(self, "allowed_purposes", allowed_purposes)
        _require_aware("fetched_at", self.fetched_at)
        for name in (
            "observed_at",
            "published_at",
            "effective_at",
            "available_from",
            "verified_at",
            "expires_at",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_aware(name, value)
        if self.payload is not None:
            unauthorized = set(self.payload) - set(authorized_fields)
            if unauthorized:
                raise EvidenceLedgerError("evidence_field_unauthorized")
            payload = _immutable_mapping(self.payload)
            if _payload_sha256(payload) != self.content_sha256:
                raise EvidenceLedgerError("evidence_content_hash_mismatch")
            object.__setattr__(self, "payload", payload)


@dataclass(frozen=True)
class EvidenceReadContext:
    actor_id: str
    permissions: frozenset[str]
    purpose: str
    now: datetime

    def __post_init__(self) -> None:
        if not self.actor_id or not self.purpose:
            raise EvidenceLedgerError("invalid_evidence_context")
        _require_aware("now", self.now)


@dataclass(frozen=True)
class FrozenEvidence:
    evidence_id: str
    logical_identity: str
    source: str
    content_sha256: str
    authorized_fields: tuple[str, ...]
    authorization_snapshot_id: str
    observed_at: datetime | None
    published_at: datetime | None
    effective_at: datetime | None
    available_from: datetime | None
    fetched_at: datetime
    verified_at: datetime | None
    expires_at: datetime | None
    persistence: Persistence
    payload: Mapping[str, Any] | None
    frozen_at: datetime

    def __post_init__(self) -> None:
        if self.payload is not None:
            object.__setattr__(self, "payload", _immutable_mapping(self.payload))


@dataclass(frozen=True)
class CapabilityAuditEvent:
    request_id: str
    channel: str
    canonical_tool: str
    arguments_sha256: str
    status: str
    evidence_ids: tuple[str, ...]
    policy_revision: str
    started_at: datetime
    completed_at: datetime | None = None
    error_code: str | None = None
    field_coverage: Decimal | None = None
    freshness_seconds: int | None = None
    cost_usd: Decimal | None = None

    def __post_init__(self) -> None:
        if not all(
            (self.request_id, self.channel, self.canonical_tool, self.status, self.policy_revision)
        ):
            raise EvidenceLedgerError("invalid_capability_audit")
        if len(self.arguments_sha256) != 64 or any(
            value not in "0123456789abcdef" for value in self.arguments_sha256
        ):
            raise EvidenceLedgerError("invalid_arguments_sha256")
        object.__setattr__(
            self,
            "evidence_ids",
            _string_tuple(
                self.evidence_ids,
                code="invalid_audit_evidence_id",
            ),
        )
        _require_aware("started_at", self.started_at)
        if self.completed_at is not None:
            _require_aware("completed_at", self.completed_at)


class EvidenceLedger(Protocol):
    def put(
        self, context: EvidenceReadContext, record: EvidenceRecord
    ) -> EvidenceRecord: ...

    def read(
        self, context: EvidenceReadContext, evidence_id: str
    ) -> EvidenceRecord: ...

    def freeze(
        self, context: EvidenceReadContext, evidence_ids: tuple[str, ...]
    ) -> tuple[FrozenEvidence, ...]: ...


class CapabilityAuditStore(Protocol):
    def append_audit(
        self, context: EvidenceReadContext, event: CapabilityAuditEvent
    ) -> None: ...


class EvidenceStore(EvidenceLedger, CapabilityAuditStore, Protocol):
    """EvidenceLedger 与共用能力审计持久化的组合端口。"""


class InMemoryEvidenceStore:
    """合同测试与纯领域调用使用；按 actor 隔离且不驱逐历史记录。"""

    def __init__(
        self,
        *,
        retention_by_authorization: Mapping[tuple[str, str], Persistence],
    ) -> None:
        self._lock = RLock()
        self._records: dict[tuple[str, str], EvidenceRecord] = {}
        self._audits: list[tuple[str, CapabilityAuditEvent]] = []
        self._retention_by_authorization = _retention_policy(
            retention_by_authorization
        )

    def put(
        self, context: EvidenceReadContext, record: EvidenceRecord
    ) -> EvidenceRecord:
        retained = _incoming_record(record, self._retention_by_authorization)
        _authorize(context, retained)
        key = (context.actor_id, retained.evidence_id)
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                _authorize(context, existing)
                _validate_stored_retention(
                    existing, self._retention_by_authorization
                )
                _ensure_available(context, existing)
                _ensure_same_content(existing, retained)
                return _copy_record(existing)
            self._records[key] = _copy_record(retained)
        return _copy_record(retained)

    def read(
        self, context: EvidenceReadContext, evidence_id: str
    ) -> EvidenceRecord:
        with self._lock:
            record = self._records.get((context.actor_id, evidence_id))
        if record is None:
            raise EvidenceLedgerError("evidence_not_found")
        _authorize(context, record)
        _validate_stored_retention(record, self._retention_by_authorization)
        _ensure_available(context, record)
        return _copy_record(record)

    def freeze(
        self, context: EvidenceReadContext, evidence_ids: tuple[str, ...]
    ) -> tuple[FrozenEvidence, ...]:
        return tuple(_freeze(context, self.read(context, value)) for value in evidence_ids)

    def append_audit(
        self, context: EvidenceReadContext, event: CapabilityAuditEvent
    ) -> None:
        with self._lock:
            self._audits.append((context.actor_id, event))

    def audits_for_actor(self, actor_id: str) -> tuple[CapabilityAuditEvent, ...]:
        with self._lock:
            return tuple(event for owner, event in self._audits if owner == actor_id)


class PostgresEvidenceStore:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        cipher: PersonalDataCipher,
        retention_by_authorization: Mapping[tuple[str, str], Persistence],
        workspace_mode: EvidenceWorkspaceMode = "create_if_missing",
    ) -> None:
        if workspace_mode not in {"create_if_missing", "existing_only"}:
            raise EvidenceLedgerError("invalid_evidence_workspace_mode")
        self._session_factory = session_factory
        self._cipher = cipher
        self._retention_by_authorization = _retention_policy(
            retention_by_authorization
        )
        self._workspace_mode = workspace_mode

    def put(
        self, context: EvidenceReadContext, record: EvidenceRecord
    ) -> EvidenceRecord:
        retained = _incoming_record(record, self._retention_by_authorization)
        _authorize(context, retained)
        with self._session_factory() as session:
            workspace = self._workspace_for_write(session, context.actor_id)
            evidence_hmac = self._cipher.scoped_lookup(
                workspace_id=workspace.id, value=retained.evidence_id
            )
            statement = select(PersonalToolEvidenceRecord).where(
                PersonalToolEvidenceRecord.workspace_id == workspace.id,
                PersonalToolEvidenceRecord.evidence_id_hmac == evidence_hmac,
            )
            if self._workspace_mode == "create_if_missing":
                statement = statement.with_for_update()
            row = session.scalar(statement)
            if row is not None:
                existing = self._decode(workspace.id, row)
                _authorize(context, existing)
                _validate_stored_retention(
                    existing, self._retention_by_authorization
                )
                _ensure_available(context, existing)
                _ensure_same_content(existing, retained)
                return existing
            row_id = str(uuid4())
            envelope = self._cipher.encrypt_json(
                _record_payload(retained),
                aad=_aad("personal_tool_evidence_records", row_id),
            )
            session.add(
                PersonalToolEvidenceRecord(
                    id=row_id,
                    workspace_id=workspace.id,
                    evidence_id_hmac=evidence_hmac,
                    logical_identity_hmac=self._cipher.scoped_lookup(
                        workspace_id=workspace.id,
                        value=retained.logical_identity,
                    ),
                    source=retained.source,
                    status="active",
                    persistence=retained.persistence,
                    content_sha256=retained.content_sha256,
                    authorization_snapshot_id=retained.authorization_snapshot_id,
                    observed_at=retained.observed_at,
                    published_at=retained.published_at,
                    effective_at=retained.effective_at,
                    available_from=retained.available_from,
                    fetched_at=retained.fetched_at,
                    verified_at=retained.verified_at,
                    expires_at=retained.expires_at,
                    **_envelope_values(envelope),
                )
            )
            session.commit()
        return _copy_record(retained)

    def read(
        self, context: EvidenceReadContext, evidence_id: str
    ) -> EvidenceRecord:
        with self._session_factory() as session:
            workspace = self._workspace(session, context.actor_id, lock=False)
            if workspace is None:
                raise EvidenceLedgerError("evidence_not_found")
            evidence_hmac = self._cipher.scoped_lookup(
                workspace_id=workspace.id, value=evidence_id
            )
            row = session.scalar(
                select(PersonalToolEvidenceRecord).where(
                    PersonalToolEvidenceRecord.workspace_id == workspace.id,
                    PersonalToolEvidenceRecord.evidence_id_hmac == evidence_hmac,
                )
            )
            if row is None:
                raise EvidenceLedgerError("evidence_not_found")
            record = self._decode(workspace.id, row)
        _authorize(context, record)
        _validate_stored_retention(record, self._retention_by_authorization)
        _ensure_available(context, record)
        return record

    def freeze(
        self, context: EvidenceReadContext, evidence_ids: tuple[str, ...]
    ) -> tuple[FrozenEvidence, ...]:
        return tuple(_freeze(context, self.read(context, value)) for value in evidence_ids)

    def append_audit(
        self, context: EvidenceReadContext, event: CapabilityAuditEvent
    ) -> None:
        with self._session_factory() as session:
            workspace = self._workspace_for_write(session, context.actor_id)
            session.add(
                PersonalCapabilityAuditEvent(
                    id=str(uuid4()),
                    request_id=event.request_id,
                    workspace_id=workspace.id,
                    channel=event.channel,
                    canonical_tool=event.canonical_tool,
                    arguments_sha256=event.arguments_sha256,
                    status=event.status,
                    error_code=event.error_code,
                    evidence_id_hmacs=[
                        self._cipher.scoped_lookup(
                            workspace_id=workspace.id, value=evidence_id
                        )
                        for evidence_id in event.evidence_ids
                    ],
                    field_coverage=event.field_coverage,
                    freshness_seconds=event.freshness_seconds,
                    cost_usd=event.cost_usd,
                    policy_revision=event.policy_revision,
                    started_at=event.started_at,
                    completed_at=event.completed_at,
                )
            )
            session.commit()

    def _decode(
        self, workspace_id: str, row: PersonalToolEvidenceRecord
    ) -> EvidenceRecord:
        payload = self._cipher.decrypt_json(
            EncryptedEnvelope(
                ciphertext=row.ciphertext,
                nonce=row.nonce,
                key_id=row.key_id,
                payload_schema=row.payload_schema,
            ),
            aad=_aad("personal_tool_evidence_records", row.id),
        )
        record = _record_from_payload(payload)
        if (
            self._cipher.scoped_lookup(
                workspace_id=workspace_id, value=record.evidence_id
            )
            != row.evidence_id_hmac
            or self._cipher.scoped_lookup(
                workspace_id=workspace_id, value=record.logical_identity
            )
            != row.logical_identity_hmac
            or record.content_sha256 != row.content_sha256
            or record.source != row.source
            or record.persistence != row.persistence
        ):
            raise EvidenceLedgerError("evidence_envelope_mismatch")
        return record

    def _workspace_for_write(
        self, session: Session, actor_id: str
    ) -> PersonalWorkspace:
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            session.execute(
                text("select pg_advisory_xact_lock(hashtextextended(:actor_hash, 0))"),
                {"actor_hash": _identity_hash(actor_id)},
            )
        workspace = self._workspace(
            session,
            actor_id,
            lock=self._workspace_mode == "create_if_missing",
        )
        if workspace is not None:
            return workspace
        if self._workspace_mode == "existing_only":
            raise EvidenceLedgerError("evidence_workspace_not_found")
        workspace_id = str(uuid4())
        envelope = self._cipher.encrypt_json(
            {"usd_cash": "0"}, aad=_aad("personal_workspaces", workspace_id)
        )
        workspace = PersonalWorkspace(
            id=workspace_id,
            actor_identity_hash=_identity_hash(actor_id),
            revision=1,
            **_envelope_values(envelope),
        )
        session.add(workspace)
        session.flush()
        return workspace

    @staticmethod
    def _workspace(
        session: Session, actor_id: str, *, lock: bool
    ) -> PersonalWorkspace | None:
        statement = select(PersonalWorkspace).where(
            PersonalWorkspace.actor_identity_hash == _identity_hash(actor_id)
        )
        if lock:
            statement = statement.with_for_update()
        return session.scalar(statement)


def _authorize(context: EvidenceReadContext, record: EvidenceRecord) -> None:
    if record.required_permissions - context.permissions:
        raise EvidenceLedgerError("evidence_permission_denied")
    if context.purpose not in record.allowed_purposes:
        raise EvidenceLedgerError("evidence_purpose_denied")
    if record.payload is not None and set(record.payload) - set(record.authorized_fields):
        raise EvidenceLedgerError("evidence_field_unauthorized")


def _ensure_same_content(existing: EvidenceRecord, incoming: EvidenceRecord) -> None:
    if existing.content_sha256 != incoming.content_sha256:
        raise EvidenceLedgerError("evidence_identity_conflict")


def _incoming_record(
    record: EvidenceRecord,
    retention_by_authorization: Mapping[tuple[str, str], Persistence],
) -> EvidenceRecord:
    persistence = retention_by_authorization.get(
        (record.source, record.authorization_snapshot_id)
    )
    if persistence is None:
        raise EvidenceLedgerError("source_retention_unknown")
    return replace(
        record,
        persistence=persistence,
        payload=None if persistence == "metadata_only" else record.payload,
    )


def _validate_stored_retention(
    record: EvidenceRecord,
    retention_by_authorization: Mapping[tuple[str, str], Persistence],
) -> None:
    persistence = retention_by_authorization.get(
        (record.source, record.authorization_snapshot_id)
    )
    if persistence is None:
        raise EvidenceLedgerError("source_retention_unknown")
    if record.persistence != persistence:
        raise EvidenceLedgerError("source_retention_mismatch")


def _copy_record(record: EvidenceRecord) -> EvidenceRecord:
    return replace(record)


def _freeze(context: EvidenceReadContext, record: EvidenceRecord) -> FrozenEvidence:
    _ensure_available(context, record)
    if record.expires_at is not None and context.now >= record.expires_at:
        raise EvidenceLedgerError("evidence_expired")
    return FrozenEvidence(
        evidence_id=record.evidence_id,
        logical_identity=record.logical_identity,
        source=record.source,
        content_sha256=record.content_sha256,
        authorized_fields=record.authorized_fields,
        authorization_snapshot_id=record.authorization_snapshot_id,
        observed_at=record.observed_at,
        published_at=record.published_at,
        effective_at=record.effective_at,
        available_from=record.available_from,
        fetched_at=record.fetched_at,
        verified_at=record.verified_at,
        expires_at=record.expires_at,
        persistence=record.persistence,
        payload=record.payload,
        frozen_at=context.now,
    )


def _record_payload(record: EvidenceRecord) -> dict[str, Any]:
    return {
        "evidence_id": record.evidence_id,
        "logical_identity": record.logical_identity,
        "scope": record.scope,
        "source": record.source,
        "content_sha256": record.content_sha256,
        "authorized_fields": list(record.authorized_fields),
        "required_permissions": sorted(record.required_permissions),
        "allowed_purposes": sorted(record.allowed_purposes),
        "authorization_snapshot_id": record.authorization_snapshot_id,
        "observed_at": _iso(record.observed_at),
        "published_at": _iso(record.published_at),
        "effective_at": _iso(record.effective_at),
        "available_from": _iso(record.available_from),
        "fetched_at": _iso(record.fetched_at),
        "verified_at": _iso(record.verified_at),
        "expires_at": _iso(record.expires_at),
        "persistence": record.persistence,
        "payload": _mutable_json(record.payload) if record.payload is not None else None,
    }


def _record_from_payload(payload: Mapping[str, Any]) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=str(payload["evidence_id"]),
        logical_identity=str(payload["logical_identity"]),
        scope=str(payload["scope"]),  # type: ignore[arg-type]
        source=str(payload["source"]),
        content_sha256=str(payload["content_sha256"]),
        authorized_fields=tuple(str(value) for value in payload["authorized_fields"]),
        required_permissions=frozenset(
            str(value) for value in payload["required_permissions"]
        ),
        allowed_purposes=frozenset(str(value) for value in payload["allowed_purposes"]),
        authorization_snapshot_id=str(payload["authorization_snapshot_id"]),
        observed_at=_datetime(payload.get("observed_at")),
        published_at=_datetime(payload.get("published_at")),
        effective_at=_datetime(payload.get("effective_at")),
        available_from=_datetime(payload.get("available_from")),
        fetched_at=_datetime(payload["fetched_at"]),  # type: ignore[arg-type]
        verified_at=_datetime(payload.get("verified_at")),
        expires_at=_datetime(payload.get("expires_at")),
        persistence=str(payload["persistence"]),  # type: ignore[arg-type]
        payload=(
            dict(payload["payload"])
            if isinstance(payload.get("payload"), Mapping)
            else None
        ),
    )


def _envelope_values(envelope: EncryptedEnvelope) -> dict[str, Any]:
    return {
        "ciphertext": envelope.ciphertext,
        "nonce": envelope.nonce,
        "key_id": envelope.key_id,
        "payload_schema": envelope.payload_schema,
    }


def _aad(table: str, row_id: str) -> str:
    return f"private_workbench|{table}|{row_id}|payload|1"


def _identity_hash(actor_id: str) -> str:
    return sha256(actor_id.encode("utf-8")).hexdigest()


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceLedgerError(f"{name}_must_be_timezone_aware")


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value))
    _require_aware("stored_datetime", parsed)
    return parsed


def _retention_policy(
    values: Mapping[tuple[str, str], Persistence],
) -> dict[tuple[str, str], Persistence]:
    policy = dict(values)
    if any(
        not isinstance(key, tuple)
        or len(key) != 2
        or any(
            not isinstance(value, str) or not value.strip() for value in key
        )
        or persistence not in {"encrypted_payload", "metadata_only"}
        for key, persistence in policy.items()
    ):
        raise EvidenceLedgerError("invalid_source_retention_policy")
    return policy


def _immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if any(not isinstance(key, str) or not key.strip() for key in value):
        raise EvidenceLedgerError("evidence_payload_not_json")
    return MappingProxyType(
        {key: _immutable_json(item) for key, item in value.items()}
    )


def _immutable_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _immutable_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_immutable_json(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise EvidenceLedgerError("evidence_payload_non_finite")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise EvidenceLedgerError("evidence_payload_not_json")


def _mutable_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _mutable_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mutable_json(item) for item in value]
    return value


def _payload_sha256(payload: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            _mutable_json(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EvidenceLedgerError("evidence_payload_not_json") from exc
    return sha256(encoded).hexdigest()


def _string_tuple(values: Any, *, code: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise EvidenceLedgerError(code)
    try:
        normalized = tuple(values)
    except TypeError as exc:
        raise EvidenceLedgerError(code) from exc
    if any(
        not isinstance(value, str) or not value.strip() for value in normalized
    ):
        raise EvidenceLedgerError(code)
    if isinstance(values, (set, frozenset)):
        return tuple(sorted(normalized))
    return normalized


def _string_frozenset(values: Any, *, code: str) -> frozenset[str]:
    return frozenset(_string_tuple(values, code=code))


def _ensure_available(
    context: EvidenceReadContext, record: EvidenceRecord
) -> None:
    if record.available_from is not None and context.now < record.available_from:
        raise EvidenceLedgerError("evidence_not_available")
