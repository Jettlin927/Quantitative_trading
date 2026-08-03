from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import base64
import hmac
import json
from threading import RLock
from typing import Callable, Literal, Protocol
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.models import (
    PersonalRecordPrivateFragment,
    PersonalRecordVersion,
    PersonalRedactionEvent,
    PersonalResearchRecord,
    PersonalVerificationItem,
    PersonalVerificationObservation,
    PersonalWorkspace,
)

from .analysis import AnalysisClaim, AnalysisStore
from .contracts import PersonalActor
from .crypto import EncryptedEnvelope, PersonalDataCipher


CARD_KINDS = (
    "confirmed_fact",
    "inference",
    "conditional_scenario",
    "unknown",
    "user_supplement",
    "verification_item",
)
OBSERVATION_RESULTS = frozenset(
    {"supports", "contradicts", "inconclusive", "data_unavailable"}
)
RECORD_STATES = frozenset({"active", "archived", "trashed"})


@dataclass(frozen=True)
class PrivateFragmentInput:
    holding_id: str
    text: str


@dataclass(frozen=True)
class VerificationDraft:
    claim_id: str | None
    question: str
    target: str
    expected_at: datetime | None
    source: str
    criterion: str


@dataclass(frozen=True)
class ConfirmationCard:
    kind: str
    label: str
    status: Literal["accepted", "empty"]
    claim_ids: tuple[str, ...]


@dataclass(frozen=True)
class RecordVersionView:
    version_id: str
    version: int
    parent_version_id: str | None
    derived_relation: str
    state: str
    content_sha256: str
    as_of: datetime
    created_at: datetime
    analysis_id: str
    evidence_pack_identity: str
    question: str
    config_revision: str
    claims: tuple[AnalysisClaim, ...]
    cards: tuple[ConfirmationCard, ...]
    user_supplement: str
    privacy_level: str
    reasoning_audit: tuple[str, ...]


@dataclass(frozen=True)
class VerificationObservationView:
    observation_id: str
    result: str
    observed_at: datetime
    evidence_ids: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class VerificationItemView:
    item_id: str
    claim_id: str | None
    state: str
    question: str
    target: str
    expected_at: datetime | None
    source: str
    criterion: str
    observations: tuple[VerificationObservationView, ...]


@dataclass(frozen=True)
class PrivateFragmentView:
    fragment_id: str
    holding_id: str
    text: str | None
    status: Literal["available", "redacted"]


@dataclass(frozen=True)
class RedactionView:
    object_type: str
    object_id: str
    reason: str
    occurred_at: datetime
    backup_status: str
    backup_expires_at: datetime


@dataclass(frozen=True)
class ResearchRecordView:
    record_id: str
    analysis_id: str
    state: str
    current_version: int
    title: str
    synthetic: bool
    formal_research_eligible: bool
    created_at: datetime
    updated_at: datetime
    versions: tuple[RecordVersionView, ...]
    verification_items: tuple[VerificationItemView, ...]
    private_fragments: tuple[PrivateFragmentView, ...]
    redactions: tuple[RedactionView, ...]
    backup_status: str | None = None
    backup_expires_at: datetime | None = None


@dataclass(frozen=True)
class PurgeChallengeView:
    record_id: str
    record_version: int
    challenge: str
    expires_at: datetime


class NotebookStore(Protocol):
    def get_by_idempotency(
        self, *, actor_id: str, idempotency_key: str
    ) -> ResearchRecordView | None: ...

    def create(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
        record: ResearchRecordView,
    ) -> ResearchRecordView: ...

    def append(
        self,
        *,
        actor_id: str,
        record_id: str,
        expected_version: int,
        idempotency_key: str,
        version: RecordVersionView,
        state: str,
        fragments: tuple[PrivateFragmentInput, ...] = (),
        verification_items: tuple[VerificationDraft, ...] = (),
        observation: tuple[str, VerificationObservationView] | None = None,
    ) -> ResearchRecordView: ...

    def get(self, *, actor_id: str, record_id: str) -> ResearchRecordView | None: ...

    def list(self, *, actor_id: str) -> tuple[ResearchRecordView, ...]: ...

    def purge(
        self,
        *,
        actor_id: str,
        record_id: str,
        expected_version: int,
        idempotency_key: str,
        now: datetime,
    ) -> ResearchRecordView: ...


class InMemoryNotebookStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[tuple[str, str], ResearchRecordView] = {}
        self._keys: dict[tuple[str, str], tuple[str, int]] = {}

    def create(self, *, actor_id: str, idempotency_key: str, record: ResearchRecordView) -> ResearchRecordView:
        with self._lock:
            key = (actor_id, idempotency_key)
            existing = self._keys.get(key)
            if existing is not None:
                return deepcopy(self._records[(actor_id, existing[0])])
            self._records[(actor_id, record.record_id)] = deepcopy(record)
            self._keys[key] = (record.record_id, 1)
            return deepcopy(record)

    def get_by_idempotency(self, *, actor_id: str, idempotency_key: str) -> ResearchRecordView | None:
        with self._lock:
            existing = self._keys.get((actor_id, idempotency_key))
            return deepcopy(self._records[(actor_id, existing[0])]) if existing is not None else None

    def append(
        self,
        *,
        actor_id: str,
        record_id: str,
        expected_version: int,
        idempotency_key: str,
        version: RecordVersionView,
        state: str,
        fragments: tuple[PrivateFragmentInput, ...] = (),
        verification_items: tuple[VerificationDraft, ...] = (),
        observation: tuple[str, VerificationObservationView] | None = None,
    ) -> ResearchRecordView:
        with self._lock:
            key = (actor_id, idempotency_key)
            existing = self._keys.get(key)
            if existing is not None:
                return deepcopy(self._records[(actor_id, existing[0])])
            current = self._records.get((actor_id, record_id))
            if current is None or current.state == "purged":
                raise ValueError("private_object_not_found")
            if current.current_version != expected_version:
                raise ValueError("revision_conflict")
            now = version.created_at
            fragment_views = tuple(
                PrivateFragmentView(str(uuid4()), item.holding_id, item.text, "available")
                for item in fragments
            )
            item_views = tuple(
                _verification_item_from_draft(item, now) for item in verification_items
            )
            existing_items = list(current.verification_items)
            if observation is not None:
                item_id, value = observation
                found = False
                for index, item in enumerate(existing_items):
                    if item.item_id == item_id:
                        found = True
                        existing_items[index] = replace(
                            item,
                            state="observed",
                            observations=(*item.observations, value),
                        )
                if not found:
                    raise ValueError("private_object_not_found")
            updated = replace(
                current,
                state=state,
                current_version=version.version,
                updated_at=now,
                versions=(*current.versions, version),
                verification_items=(*existing_items, *item_views),
                private_fragments=(*current.private_fragments, *fragment_views),
            )
            self._records[(actor_id, record_id)] = deepcopy(updated)
            self._keys[key] = (record_id, version.version)
            return deepcopy(updated)

    def get(self, *, actor_id: str, record_id: str) -> ResearchRecordView | None:
        with self._lock:
            value = self._records.get((actor_id, record_id))
            return deepcopy(value) if value is not None else None

    def list(self, *, actor_id: str) -> tuple[ResearchRecordView, ...]:
        with self._lock:
            return tuple(
                deepcopy(item)
                for (owner, _), item in self._records.items()
                if owner == actor_id
            )

    def purge(self, *, actor_id: str, record_id: str, expected_version: int, idempotency_key: str, now: datetime) -> ResearchRecordView:
        with self._lock:
            key = (actor_id, idempotency_key)
            existing = self._keys.get(key)
            if existing is not None:
                return deepcopy(self._records[(actor_id, existing[0])])
            current = self._records.get((actor_id, record_id))
            if current is None:
                raise ValueError("private_object_not_found")
            if current.current_version != expected_version:
                raise ValueError("revision_conflict")
            redaction = RedactionView(
                "record", record_id, "record_purged", now,
                "expires_within_window", now + timedelta(days=30),
            )
            purged = replace(
                current,
                state="purged",
                current_version=current.current_version + 1,
                title="已永久删除的个人记录",
                updated_at=now,
                versions=(),
                verification_items=(),
                private_fragments=(),
                redactions=(*current.redactions, redaction),
                backup_status=redaction.backup_status,
                backup_expires_at=redaction.backup_expires_at,
            )
            self._records[(actor_id, record_id)] = purged
            self._keys[key] = (record_id, purged.current_version)
            return deepcopy(purged)


class PostgresNotebookStore:
    def __init__(self, session_factory: Callable[[], Session], *, cipher: PersonalDataCipher) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    def create(self, *, actor_id: str, idempotency_key: str, record: ResearchRecordView) -> ResearchRecordView:
        with self._session_factory() as session, session.begin():
            workspace = self._workspace(session, actor_id, lock=True)
            if workspace is None:
                raise ValueError("private_object_not_found")
            key_hash = _identity_hash(f"{actor_id}|{idempotency_key}")
            existing = session.scalar(select(PersonalResearchRecord).where(
                PersonalResearchRecord.workspace_id == workspace.id,
                PersonalResearchRecord.idempotency_hash == key_hash,
            ))
            if existing is not None:
                return self._project(session, actor_id, existing)
            version = record.versions[0]
            head_envelope = self._cipher.encrypt_json(
                {"title": record.title, "formal_research_eligible": False},
                aad=_aad("personal_research_records", record.record_id),
            )
            row = PersonalResearchRecord(
                id=record.record_id,
                workspace_id=workspace.id,
                analysis_id=version.evidence_pack_identity,
                source_run_id=record.analysis_id,
                version=1,
                state=record.state,
                idempotency_hash=key_hash,
                content_sha256=version.content_sha256,
                synthetic=False,
                current_version_id=version.version_id,
                updated_at=record.updated_at,
                **_envelope_values(head_envelope),
            )
            session.add(row)
            session.flush()
            self._insert_version(session, workspace.id, actor_id, idempotency_key, record.record_id, version)
            session.flush()
            self._insert_children(session, workspace.id, record.record_id, version.version_id, record.private_fragments, record.verification_items)
            session.flush()
            return self._project(session, actor_id, row)

    def get_by_idempotency(self, *, actor_id: str, idempotency_key: str) -> ResearchRecordView | None:
        with self._session_factory() as session:
            workspace = self._workspace(session, actor_id, lock=False)
            if workspace is None:
                return None
            key_hash = _identity_hash(f"{actor_id}|{idempotency_key}")
            record = session.scalar(select(PersonalResearchRecord).where(
                PersonalResearchRecord.workspace_id == workspace.id,
                PersonalResearchRecord.idempotency_hash == key_hash,
            ))
            if record is None:
                version = session.scalar(select(PersonalRecordVersion).where(
                    PersonalRecordVersion.workspace_id == workspace.id,
                    PersonalRecordVersion.idempotency_hash == key_hash,
                ))
                if version is not None:
                    record = session.get(PersonalResearchRecord, version.record_id)
            if record is None:
                redaction = session.scalar(select(PersonalRedactionEvent).where(
                    PersonalRedactionEvent.workspace_id == workspace.id,
                    PersonalRedactionEvent.idempotency_hash == key_hash,
                ))
                if redaction is not None and redaction.object_type == "record":
                    record = session.get(PersonalResearchRecord, redaction.object_id)
            return self._project(session, actor_id, record) if record is not None else None

    def append(
        self, *, actor_id: str, record_id: str, expected_version: int,
        idempotency_key: str, version: RecordVersionView, state: str,
        fragments: tuple[PrivateFragmentInput, ...] = (),
        verification_items: tuple[VerificationDraft, ...] = (),
        observation: tuple[str, VerificationObservationView] | None = None,
    ) -> ResearchRecordView:
        with self._session_factory() as session, session.begin():
            workspace = self._workspace(session, actor_id, lock=True)
            if workspace is None:
                raise ValueError("private_object_not_found")
            key_hash = _identity_hash(f"{actor_id}|{idempotency_key}")
            existing = session.scalar(select(PersonalRecordVersion).where(
                PersonalRecordVersion.workspace_id == workspace.id,
                PersonalRecordVersion.idempotency_hash == key_hash,
            ))
            if existing is not None:
                row = session.get(PersonalResearchRecord, existing.record_id)
                return self._project(session, actor_id, row)
            row = session.scalar(select(PersonalResearchRecord).where(
                PersonalResearchRecord.id == record_id,
                PersonalResearchRecord.workspace_id == workspace.id,
            ).with_for_update())
            if row is None or row.state == "purged":
                raise ValueError("private_object_not_found")
            if row.version != expected_version:
                raise ValueError("revision_conflict")
            self._insert_version(session, workspace.id, actor_id, idempotency_key, record_id, version)
            session.flush()
            fragment_views = tuple(
                PrivateFragmentView(str(uuid4()), item.holding_id, item.text, "available")
                for item in fragments
            )
            item_views = tuple(_verification_item_from_draft(item, version.created_at) for item in verification_items)
            self._insert_children(session, workspace.id, record_id, version.version_id, fragment_views, item_views)
            if observation is not None:
                item_id, value = observation
                item = session.get(PersonalVerificationItem, item_id)
                if item is None or item.workspace_id != workspace.id or item.record_id != record_id:
                    raise ValueError("private_object_not_found")
                envelope = self._cipher.encrypt_json(
                    {"note": value.note}, aad=_aad("personal_verification_observations", value.observation_id)
                )
                session.add(PersonalVerificationObservation(
                    id=value.observation_id, workspace_id=workspace.id, item_id=item_id,
                    record_version_id=version.version_id, result=value.result,
                    observed_at=value.observed_at, evidence_ids=list(value.evidence_ids),
                    **_envelope_values(envelope),
                ))
            row.version = version.version
            row.current_version_id = version.version_id
            row.state = state
            row.content_sha256 = version.content_sha256
            row.updated_at = version.created_at
            session.flush()
            return self._project(session, actor_id, row)

    def get(self, *, actor_id: str, record_id: str) -> ResearchRecordView | None:
        with self._session_factory() as session:
            workspace = self._workspace(session, actor_id, lock=False)
            if workspace is None:
                return None
            row = session.get(PersonalResearchRecord, record_id)
            if row is None or row.workspace_id != workspace.id:
                return None
            return self._project(session, actor_id, row)

    def list(self, *, actor_id: str) -> tuple[ResearchRecordView, ...]:
        with self._session_factory() as session:
            workspace = self._workspace(session, actor_id, lock=False)
            if workspace is None:
                return ()
            rows = session.scalars(select(PersonalResearchRecord).where(
                PersonalResearchRecord.workspace_id == workspace.id
            ).order_by(PersonalResearchRecord.updated_at.desc())).all()
            return tuple(self._project(session, actor_id, row) for row in rows)

    def purge(self, *, actor_id: str, record_id: str, expected_version: int, idempotency_key: str, now: datetime) -> ResearchRecordView:
        with self._session_factory() as session, session.begin():
            workspace = self._workspace(session, actor_id, lock=True)
            if workspace is None:
                raise ValueError("private_object_not_found")
            key_hash = _identity_hash(f"{actor_id}|{idempotency_key}")
            existing = session.scalar(select(PersonalRedactionEvent).where(
                PersonalRedactionEvent.workspace_id == workspace.id,
                PersonalRedactionEvent.idempotency_hash == key_hash,
            ))
            row = session.scalar(select(PersonalResearchRecord).where(
                PersonalResearchRecord.id == record_id,
                PersonalResearchRecord.workspace_id == workspace.id,
            ).with_for_update())
            if row is None:
                raise ValueError("private_object_not_found")
            if existing is not None:
                return self._project(session, actor_id, row)
            if row.version != expected_version:
                raise ValueError("revision_conflict")
            session.execute(delete(PersonalRecordPrivateFragment).where(PersonalRecordPrivateFragment.record_id == record_id))
            session.execute(delete(PersonalVerificationObservation).where(
                PersonalVerificationObservation.item_id.in_(
                    select(PersonalVerificationItem.id).where(PersonalVerificationItem.record_id == record_id)
                )
            ))
            session.execute(delete(PersonalVerificationItem).where(PersonalVerificationItem.record_id == record_id))
            session.execute(delete(PersonalRecordVersion).where(PersonalRecordVersion.record_id == record_id))
            backup_expires_at = now + timedelta(days=30)
            session.add(PersonalRedactionEvent(
                id=str(uuid4()), workspace_id=workspace.id, object_type="record",
                object_id=record_id, reason="record_purged", idempotency_hash=key_hash,
                occurred_at=now, backup_expires_at=backup_expires_at,
            ))
            tombstone = self._cipher.encrypt_json(
                {"title": "已永久删除的个人记录", "formal_research_eligible": False},
                aad=_aad("personal_research_records", record_id),
            )
            row.version += 1
            row.current_version_id = None
            row.state = "purged"
            row.updated_at = now
            row.purged_at = now
            row.content_sha256 = sha256(b"record_purged").hexdigest()
            _apply_envelope(row, tombstone)
            session.flush()
            return self._project(session, actor_id, row)

    def _workspace(self, session: Session, actor_id: str, *, lock: bool) -> PersonalWorkspace | None:
        statement = select(PersonalWorkspace).where(PersonalWorkspace.actor_identity_hash == _identity_hash(actor_id))
        if lock:
            statement = statement.with_for_update()
        return session.scalar(statement)

    def _insert_version(self, session: Session, workspace_id: str, actor_id: str, idempotency_key: str, record_id: str, version: RecordVersionView) -> None:
        envelope = self._cipher.encrypt_json(
            _version_payload(version), aad=_aad("personal_record_versions", version.version_id)
        )
        session.add(PersonalRecordVersion(
            id=version.version_id, workspace_id=workspace_id, record_id=record_id,
            version=version.version, parent_version_id=version.parent_version_id,
            derived_relation=version.derived_relation, state=version.state,
            content_sha256=version.content_sha256,
            idempotency_hash=_identity_hash(f"{actor_id}|{idempotency_key}"),
            as_of=version.as_of, created_at=version.created_at,
            **_envelope_values(envelope),
        ))

    def _insert_children(self, session: Session, workspace_id: str, record_id: str, version_id: str, fragments: tuple[PrivateFragmentView, ...], items: tuple[VerificationItemView, ...]) -> None:
        for item in fragments:
            envelope = self._cipher.encrypt_json({"text": item.text}, aad=_aad("personal_record_private_fragments", item.fragment_id))
            session.add(PersonalRecordPrivateFragment(
                id=item.fragment_id, workspace_id=workspace_id, record_id=record_id,
                record_version_id=version_id, holding_id=item.holding_id,
                **_envelope_values(envelope),
            ))
        for item in items:
            envelope = self._cipher.encrypt_json({
                "question": item.question, "target": item.target,
                "source": item.source, "criterion": item.criterion,
            }, aad=_aad("personal_verification_items", item.item_id))
            session.add(PersonalVerificationItem(
                id=item.item_id, workspace_id=workspace_id, record_id=record_id,
                claim_id=item.claim_id, initial_state="pending", due_at=item.expected_at,
                **_envelope_values(envelope),
            ))

    def _project(self, session: Session, actor_id: str, row: PersonalResearchRecord) -> ResearchRecordView:
        head = self._cipher.decrypt_json(_row_envelope(row), aad=_aad("personal_research_records", row.id))
        versions = tuple(
            _version_from_payload(
                self._cipher.decrypt_json(_row_envelope(item), aad=_aad("personal_record_versions", item.id))
            )
            for item in session.scalars(select(PersonalRecordVersion).where(
                PersonalRecordVersion.record_id == row.id
            ).order_by(PersonalRecordVersion.version)).all()
        )
        observations_by_item: dict[str, list[VerificationObservationView]] = {}
        observations = session.scalars(select(PersonalVerificationObservation).where(
            PersonalVerificationObservation.workspace_id == row.workspace_id,
            PersonalVerificationObservation.item_id.in_(
                select(PersonalVerificationItem.id).where(PersonalVerificationItem.record_id == row.id)
            ),
        ).order_by(PersonalVerificationObservation.observed_at)).all()
        for observation in observations:
            payload = self._cipher.decrypt_json(_row_envelope(observation), aad=_aad("personal_verification_observations", observation.id))
            observations_by_item.setdefault(observation.item_id, []).append(
                VerificationObservationView(
                    observation.id, observation.result, observation.observed_at,
                    tuple(observation.evidence_ids), str(payload.get("note", "")),
                )
            )
        items: list[VerificationItemView] = []
        now = datetime.now(timezone.utc)
        for item in session.scalars(select(PersonalVerificationItem).where(
            PersonalVerificationItem.record_id == row.id
        ).order_by(PersonalVerificationItem.created_at)).all():
            payload = self._cipher.decrypt_json(_row_envelope(item), aad=_aad("personal_verification_items", item.id))
            item_observations = tuple(observations_by_item.get(item.id, ()))
            state = "observed" if item_observations else ("due" if item.due_at is not None and item.due_at <= now else "pending")
            items.append(VerificationItemView(
                item.id, item.claim_id, state, str(payload["question"]), str(payload["target"]),
                item.due_at, str(payload["source"]), str(payload["criterion"]), item_observations,
            ))
        redactions = tuple(
            RedactionView(item.object_type, item.object_id, item.reason, item.occurred_at,
                          "expires_within_window", item.backup_expires_at)
            for item in session.scalars(select(PersonalRedactionEvent).where(
                PersonalRedactionEvent.workspace_id == row.workspace_id,
                ((PersonalRedactionEvent.object_type == "record") & (PersonalRedactionEvent.object_id == row.id))
                | (PersonalRedactionEvent.object_type == "holding"),
            ).order_by(PersonalRedactionEvent.occurred_at)).all()
        )
        redacted_holdings = {item.object_id for item in redactions if item.object_type == "holding"}
        fragments = []
        for item in session.scalars(select(PersonalRecordPrivateFragment).where(
            PersonalRecordPrivateFragment.record_id == row.id
        ).order_by(PersonalRecordPrivateFragment.created_at)).all():
            if item.holding_id in redacted_holdings:
                fragments.append(PrivateFragmentView(item.id, item.holding_id, None, "redacted"))
            else:
                payload = self._cipher.decrypt_json(_row_envelope(item), aad=_aad("personal_record_private_fragments", item.id))
                fragments.append(PrivateFragmentView(item.id, item.holding_id, str(payload["text"]), "available"))
        fragment_holdings = {item.holding_id for item in fragments}
        for holding_id in sorted(redacted_holdings - fragment_holdings):
            fragments.append(PrivateFragmentView(f"redacted-{holding_id}", holding_id, None, "redacted"))
        latest_redaction = redactions[-1] if redactions else None
        return ResearchRecordView(
            record_id=row.id, analysis_id=row.source_run_id or row.analysis_id,
            state=row.state, current_version=row.version, title=str(head.get("title", "个人研究记录")),
            synthetic=row.synthetic, formal_research_eligible=False,
            created_at=row.created_at, updated_at=row.updated_at,
            versions=versions, verification_items=tuple(items),
            private_fragments=tuple(fragments), redactions=redactions,
            backup_status=latest_redaction.backup_status if latest_redaction else None,
            backup_expires_at=latest_redaction.backup_expires_at if latest_redaction else None,
        )


class ResearchNotebook:
    def __init__(
        self, *, store: NotebookStore, analyses: AnalysisStore,
        challenge_key: bytes, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._store = store
        self._analyses = analyses
        self._challenge_key = challenge_key
        self._clock = clock

    def open(self, actor: PersonalActor, record_id: str | None = None):
        if record_id is None:
            return self._store.list(actor_id=actor.actor_id)
        value = self._store.get(actor_id=actor.actor_id, record_id=record_id)
        if value is None:
            raise ValueError("private_object_not_found")
        return value

    def save_analysis(
        self, actor: PersonalActor, *, analysis_id: str, accepted_claim_ids: tuple[str, ...],
        user_supplement: str, fragments: tuple[PrivateFragmentInput, ...],
        verification_drafts: tuple[VerificationDraft, ...], idempotency_key: str,
    ) -> ResearchRecordView:
        replay = self._store.get_by_idempotency(actor_id=actor.actor_id, idempotency_key=idempotency_key)
        if replay is not None:
            return replay
        run = self._analyses.get_run(actor.actor_id, analysis_id)
        if run is None:
            raise ValueError("private_object_not_found")
        draft = self._analyses.get_draft(actor.actor_id, run.view.draft_id)
        if draft is None:
            raise ValueError("private_object_not_found")
        if run.view.status not in {"completed", "evidence_insufficient"}:
            raise ValueError("analysis_not_saveable")
        selected = _select_claims(run.view.claims, accepted_claim_ids)
        if run.view.status == "evidence_insufficient" and any(
            item.kind not in {"confirmed_fact", "unknown"} for item in selected
        ):
            raise ValueError("analysis_not_saveable")
        now = self._clock()
        record_id = str(uuid4())
        version = _make_version(
            version_id=str(uuid4()), version=1, parent=None, relation="saved_analysis",
            state="active", now=now, analysis_id=analysis_id,
            evidence_pack_identity=run.view.draft_id, question=draft.intent.question,
            config_revision=draft.receipt.config_revision, claims=selected,
            user_supplement=user_supplement, verification_count=len(verification_drafts),
            reasoning_audit=(),
        )
        item_views = tuple(_verification_item_from_draft(item, now) for item in verification_drafts)
        fragment_views = tuple(
            PrivateFragmentView(str(uuid4()), item.holding_id, item.text, "available") for item in fragments
        )
        record = ResearchRecordView(
            record_id, analysis_id, "active", 1, draft.intent.question[:80], False, False,
            now, now, (version,), item_views, fragment_views, (),
        )
        return self._store.create(actor_id=actor.actor_id, idempotency_key=idempotency_key, record=record)

    def append_supplement(
        self, actor: PersonalActor, *, record_id: str, expected_version: int,
        supplement: str, fragments: tuple[PrivateFragmentInput, ...], idempotency_key: str,
    ) -> ResearchRecordView:
        replay = self._store.get_by_idempotency(actor_id=actor.actor_id, idempotency_key=idempotency_key)
        if replay is not None:
            return replay
        current = self.open(actor, record_id)
        latest = _latest_version(current)
        version = _derive_version(latest, "supplement", self._clock(), expected_version, user_supplement=supplement)
        return self._store.append(
            actor_id=actor.actor_id, record_id=record_id, expected_version=expected_version,
            idempotency_key=idempotency_key, version=version, state=current.state, fragments=fragments,
        )

    def start_reasoning_audit(self, actor: PersonalActor, *, record_id: str, expected_version: int, idempotency_key: str) -> ResearchRecordView:
        replay = self._store.get_by_idempotency(actor_id=actor.actor_id, idempotency_key=idempotency_key)
        if replay is not None:
            return replay
        current = self.open(actor, record_id)
        latest = _latest_version(current)
        findings = tuple(
            f"{claim.kind}:{'证据已绑定' if claim.evidence_ids else '缺少直接证据'}:{'含失效条件' if claim.invalidation_conditions else '缺少失效条件'}"
            for claim in latest.claims
        ) or ("无已接受主张，仅保留问题与缺口。",)
        version = _derive_version(latest, "reasoning_audit", self._clock(), expected_version, reasoning_audit=findings)
        return self._store.append(
            actor_id=actor.actor_id, record_id=record_id, expected_version=expected_version,
            idempotency_key=idempotency_key, version=version, state=current.state,
        )

    def create_verification_item(
        self, actor: PersonalActor, *, record_id: str, expected_version: int,
        draft: VerificationDraft, idempotency_key: str,
    ) -> ResearchRecordView:
        replay = self._store.get_by_idempotency(actor_id=actor.actor_id, idempotency_key=idempotency_key)
        if replay is not None:
            return replay
        current = self.open(actor, record_id)
        latest = _latest_version(current)
        if draft.claim_id is not None and draft.claim_id not in {item.claim_id for item in latest.claims}:
            raise ValueError("private_object_not_found")
        version = _derive_version(latest, "verification_item", self._clock(), expected_version, verification_count=1)
        return self._store.append(
            actor_id=actor.actor_id, record_id=record_id, expected_version=expected_version,
            idempotency_key=idempotency_key, version=version, state=current.state,
            verification_items=(draft,),
        )

    def append_verification_observation(
        self, actor: PersonalActor, *, record_id: str, expected_version: int,
        item_id: str, result: str, evidence_ids: tuple[str, ...], note: str,
        idempotency_key: str,
    ) -> ResearchRecordView:
        replay = self._store.get_by_idempotency(actor_id=actor.actor_id, idempotency_key=idempotency_key)
        if replay is not None:
            return replay
        if result not in OBSERVATION_RESULTS:
            raise ValueError("invalid_command")
        current = self.open(actor, record_id)
        if item_id not in {item.item_id for item in current.verification_items}:
            raise ValueError("private_object_not_found")
        now = self._clock()
        version = _derive_version(_latest_version(current), "verification_observation", now, expected_version)
        observation = VerificationObservationView(str(uuid4()), result, now, evidence_ids, note)
        return self._store.append(
            actor_id=actor.actor_id, record_id=record_id, expected_version=expected_version,
            idempotency_key=idempotency_key, version=version, state=current.state,
            observation=(item_id, observation),
        )

    def change_state(
        self, actor: PersonalActor, *, record_id: str, expected_version: int,
        state: str, idempotency_key: str,
    ) -> ResearchRecordView:
        replay = self._store.get_by_idempotency(actor_id=actor.actor_id, idempotency_key=idempotency_key)
        if replay is not None:
            return replay
        if state not in RECORD_STATES:
            raise ValueError("invalid_command")
        current = self.open(actor, record_id)
        allowed = {
            "active": {"archived", "trashed"},
            "archived": {"active", "trashed"},
            "trashed": {"active"},
        }
        if state not in allowed.get(current.state, set()):
            raise ValueError("invalid_command")
        version = _derive_version(_latest_version(current), f"state_{state}", self._clock(), expected_version, state=state)
        return self._store.append(
            actor_id=actor.actor_id, record_id=record_id, expected_version=expected_version,
            idempotency_key=idempotency_key, version=version, state=state,
        )

    def request_purge(self, actor: PersonalActor, *, record_id: str, expected_version: int) -> PurgeChallengeView:
        current = self.open(actor, record_id)
        if current.current_version != expected_version or current.state != "trashed":
            raise ValueError("revision_conflict")
        expires_at = self._clock() + timedelta(minutes=10)
        payload = {"actor": _identity_hash(actor.actor_id), "record_id": record_id,
                   "version": expected_version, "expires_at": int(expires_at.timestamp())}
        encoded = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        signature = _b64(hmac.new(self._challenge_key, encoded.encode(), sha256).digest())
        return PurgeChallengeView(record_id, expected_version, f"{encoded}.{signature}", expires_at)

    def confirm_purge(
        self, actor: PersonalActor, *, record_id: str, expected_version: int,
        challenge: str, idempotency_key: str,
    ) -> ResearchRecordView:
        replay = self._store.get_by_idempotency(actor_id=actor.actor_id, idempotency_key=idempotency_key)
        if replay is not None:
            return replay
        self._verify_challenge(actor, record_id, expected_version, challenge)
        current = self.open(actor, record_id)
        if current.state != "trashed":
            raise ValueError("invalid_command")
        return self._store.purge(
            actor_id=actor.actor_id, record_id=record_id, expected_version=expected_version,
            idempotency_key=idempotency_key, now=self._clock(),
        )

    def _verify_challenge(self, actor: PersonalActor, record_id: str, expected_version: int, challenge: str) -> None:
        try:
            encoded, signature = challenge.split(".", 1)
            expected = _b64(hmac.new(self._challenge_key, encoded.encode(), sha256).digest())
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if payload != {"actor": _identity_hash(actor.actor_id), "record_id": record_id,
                           "version": expected_version, "expires_at": payload.get("expires_at")}:
                raise ValueError
            if int(payload["expires_at"]) < int(self._clock().timestamp()):
                raise ValueError
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise ValueError("purge_challenge_invalid") from None


def _select_claims(claims: tuple[AnalysisClaim, ...], accepted_ids: tuple[str, ...]) -> tuple[AnalysisClaim, ...]:
    if not accepted_ids or len(set(accepted_ids)) != len(accepted_ids):
        raise ValueError("invalid_command")
    by_id = {item.claim_id: item for item in claims}
    try:
        return tuple(by_id[item] for item in accepted_ids)
    except KeyError:
        raise ValueError("private_object_not_found") from None


def _cards(claims: tuple[AnalysisClaim, ...], supplement: str, verification_count: int) -> tuple[ConfirmationCard, ...]:
    labels = {
        "confirmed_fact": "已确认事实", "inference": "推断", "conditional_scenario": "条件情景",
        "unknown": "未知项", "user_supplement": "用户补充", "verification_item": "待验证事项",
    }
    return tuple(
        ConfirmationCard(
            kind, labels[kind],
            "accepted" if ((kind == "user_supplement" and supplement.strip()) or
                           (kind == "verification_item" and verification_count) or
                           any(item.kind == kind for item in claims)) else "empty",
            tuple(item.claim_id for item in claims if item.kind == kind),
        ) for kind in CARD_KINDS
    )


def _make_version(
    *, version_id: str, version: int, parent: str | None, relation: str, state: str,
    now: datetime, analysis_id: str, evidence_pack_identity: str, question: str,
    config_revision: str, claims: tuple[AnalysisClaim, ...], user_supplement: str,
    verification_count: int, reasoning_audit: tuple[str, ...],
) -> RecordVersionView:
    payload = {
        "version": version, "parent_version_id": parent, "derived_relation": relation,
        "state": state, "as_of": now.isoformat(), "analysis_id": analysis_id,
        "evidence_pack_identity": evidence_pack_identity, "question": question,
        "config_revision": config_revision, "claims": [asdict(item) for item in claims],
        "cards": [asdict(item) for item in _cards(claims, user_supplement, verification_count)],
        "user_supplement": user_supplement, "privacy_level": "private",
        "reasoning_audit": list(reasoning_audit),
    }
    content_sha = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return RecordVersionView(
        version_id, version, parent, relation, state, content_sha, now, now,
        analysis_id, evidence_pack_identity, question, config_revision, claims,
        _cards(claims, user_supplement, verification_count), user_supplement,
        "private", reasoning_audit,
    )


def _derive_version(
    current: RecordVersionView, relation: str, now: datetime, expected_version: int,
    *, user_supplement: str | None = None, reasoning_audit: tuple[str, ...] | None = None,
    verification_count: int = 0, state: str | None = None,
) -> RecordVersionView:
    if current.version != expected_version:
        raise ValueError("revision_conflict")
    return _make_version(
        version_id=str(uuid4()), version=expected_version + 1, parent=current.version_id,
        relation=relation, state=state or current.state, now=now,
        analysis_id=current.analysis_id, evidence_pack_identity=current.evidence_pack_identity,
        question=current.question, config_revision=current.config_revision, claims=current.claims,
        user_supplement=current.user_supplement if user_supplement is None else user_supplement,
        verification_count=verification_count, reasoning_audit=current.reasoning_audit if reasoning_audit is None else reasoning_audit,
    )


def _latest_version(record: ResearchRecordView) -> RecordVersionView:
    if not record.versions:
        raise ValueError("private_object_not_found")
    return record.versions[-1]


def _verification_item_from_draft(item: VerificationDraft, now: datetime) -> VerificationItemView:
    state = "due" if item.expected_at is not None and item.expected_at <= now else "pending"
    return VerificationItemView(str(uuid4()), item.claim_id, state, item.question, item.target,
                                item.expected_at, item.source, item.criterion, ())


def _version_payload(value: RecordVersionView) -> dict:
    payload = asdict(value)
    payload["as_of"] = value.as_of.isoformat()
    payload["created_at"] = value.created_at.isoformat()
    return payload


def _version_from_payload(payload: dict) -> RecordVersionView:
    return RecordVersionView(
        version_id=payload["version_id"], version=int(payload["version"]),
        parent_version_id=payload.get("parent_version_id"), derived_relation=payload["derived_relation"],
        state=payload["state"], content_sha256=payload["content_sha256"],
        as_of=datetime.fromisoformat(payload["as_of"]), created_at=datetime.fromisoformat(payload["created_at"]),
        analysis_id=payload["analysis_id"], evidence_pack_identity=payload["evidence_pack_identity"],
        question=payload["question"], config_revision=payload["config_revision"],
        claims=tuple(AnalysisClaim(**{**item, "evidence_ids": tuple(item["evidence_ids"]),
                                      "opposing_evidence_ids": tuple(item["opposing_evidence_ids"]),
                                      "assumptions": tuple(item["assumptions"]),
                                      "invalidation_conditions": tuple(item["invalidation_conditions"])})
                     for item in payload["claims"]),
        cards=tuple(ConfirmationCard(**{**item, "claim_ids": tuple(item["claim_ids"])}) for item in payload["cards"]),
        user_supplement=payload["user_supplement"], privacy_level=payload["privacy_level"],
        reasoning_audit=tuple(payload["reasoning_audit"]),
    )


def _identity_hash(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _aad(table: str, row_id: str) -> str:
    return f"private_workbench|{table}|{row_id}|payload|1"


def _envelope_values(value: EncryptedEnvelope) -> dict:
    return {"ciphertext": value.ciphertext, "nonce": value.nonce, "key_id": value.key_id, "payload_schema": value.payload_schema}


def _row_envelope(row) -> EncryptedEnvelope:
    return EncryptedEnvelope(bytes(row.ciphertext), bytes(row.nonce), row.key_id, row.payload_schema)


def _apply_envelope(row, value: EncryptedEnvelope) -> None:
    row.ciphertext = value.ciphertext
    row.nonce = value.nonce
    row.key_id = value.key_id
    row.payload_schema = value.payload_schema


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")
