"""持仓、自选与 AI 候选的多状态领域聚合。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Callable, Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models import (
    PersonalInstrumentRevision,
    PersonalInstrumentState,
    PersonalWorkspace,
)

from .contracts import PersonalActor
from .crypto import PersonalDataCipher
from .portfolio import (
    _portfolio_aad,
    _portfolio_envelope_values,
    _portfolio_identity_hash,
    _portfolio_row_envelope,
)


@dataclass(frozen=True)
class FollowSymbol:
    symbol: str
    expected_revision: int
    preset_reasons: tuple[str, ...] = ()
    custom_reason: str | None = None


@dataclass(frozen=True)
class UnfollowSymbol:
    symbol: str
    expected_revision: int


@dataclass(frozen=True)
class CandidateEvidence:
    symbol: str
    relation_evidence_ids: tuple[str, ...]
    fact_evidence_ids: tuple[str, ...]
    observed_at: datetime
    expected_revision: int


@dataclass(frozen=True)
class InstrumentStateView:
    symbol: str
    is_holding: bool
    is_followed: bool
    follow_source: str
    preset_reasons: tuple[str, ...]
    custom_reason: str | None
    candidate_status: str | None
    relation_evidence_ids: tuple[str, ...]
    fact_evidence_ids: tuple[str, ...]
    candidate_refreshed_at: datetime | None
    candidate_archived_at: datetime | None


@dataclass(frozen=True)
class InstrumentStatesView:
    revision: int
    items: tuple[InstrumentStateView, ...]


@dataclass
class StoredInstrumentState:
    symbol: str
    manual_following: bool | None = None
    manual_unfollow_holding_revision: int | None = None
    preset_reasons: tuple[str, ...] = ()
    custom_reason: str | None = None
    candidate_status: str | None = None
    relation_evidence_ids: tuple[str, ...] = ()
    fact_evidence_ids: tuple[str, ...] = ()
    candidate_refreshed_at: datetime | None = None
    candidate_archived_at: datetime | None = None


@dataclass
class InstrumentStateSnapshot:
    revision: int
    items: dict[str, StoredInstrumentState]


@dataclass(frozen=True)
class HoldingWatchState:
    state: str
    revision: int


class InstrumentStateStore(Protocol):
    def load(self, *, actor_id: str) -> InstrumentStateSnapshot: ...

    def revise(
        self,
        *,
        actor_id: str,
        expected_revision: int,
        idempotency_key: str,
        action: str,
        mutate: Callable[[InstrumentStateSnapshot], None],
    ) -> InstrumentStateSnapshot: ...


class InMemoryInstrumentStateStore:
    def __init__(self) -> None:
        self._states: dict[str, InstrumentStateSnapshot] = {}
        self._idempotency: dict[tuple[str, str], int] = {}

    def load(self, *, actor_id: str) -> InstrumentStateSnapshot:
        return deepcopy(
            self._states.get(actor_id, InstrumentStateSnapshot(revision=0, items={}))
        )

    def revise(
        self,
        *,
        actor_id: str,
        expected_revision: int,
        idempotency_key: str,
        action: str,
        mutate: Callable[[InstrumentStateSnapshot], None],
    ) -> InstrumentStateSnapshot:
        key = (actor_id, idempotency_key)
        if key in self._idempotency:
            return self.load(actor_id=actor_id)
        state = self.load(actor_id=actor_id)
        if state.revision != expected_revision:
            raise ValueError("revision_conflict")
        mutate(state)
        state.revision += 1
        self._states[actor_id] = deepcopy(state)
        self._idempotency[key] = state.revision
        return deepcopy(state)


class PostgresInstrumentStateStore:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        cipher: PersonalDataCipher,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    def load(self, *, actor_id: str) -> InstrumentStateSnapshot:
        with self._session_factory() as session:
            workspace = self._workspace(session, actor_id, lock=False)
            return self._load_state(session, workspace)

    def revise(
        self,
        *,
        actor_id: str,
        expected_revision: int,
        idempotency_key: str,
        action: str,
        mutate: Callable[[InstrumentStateSnapshot], None],
    ) -> InstrumentStateSnapshot:
        try:
            return self._revise_once(
                actor_id=actor_id,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                action=action,
                mutate=mutate,
            )
        except IntegrityError:
            with self._session_factory() as session:
                workspace = self._workspace(session, actor_id, lock=False)
                if workspace is not None:
                    existing = session.scalar(
                        select(PersonalInstrumentRevision).where(
                            PersonalInstrumentRevision.workspace_id == workspace.id,
                            PersonalInstrumentRevision.idempotency_hash
                            == _instrument_idempotency_hash(
                                actor_id, idempotency_key
                            ),
                        )
                    )
                    if existing is not None:
                        return self._load_state(session, workspace)
            raise ValueError("revision_conflict") from None

    def _revise_once(
        self,
        *,
        actor_id: str,
        expected_revision: int,
        idempotency_key: str,
        action: str,
        mutate: Callable[[InstrumentStateSnapshot], None],
    ) -> InstrumentStateSnapshot:
        with self._session_factory() as session, session.begin():
            workspace = self._workspace(session, actor_id, lock=True)
            if workspace is None:
                if expected_revision != 0:
                    raise ValueError("revision_conflict")
                workspace_id = str(uuid4())
                envelope = self._cipher.encrypt_json(
                    {"usd_cash": "0.0000"},
                    aad=_portfolio_aad("personal_workspaces", workspace_id),
                )
                workspace = PersonalWorkspace(
                    id=workspace_id,
                    actor_identity_hash=_portfolio_identity_hash(actor_id),
                    revision=0,
                    instrument_revision=0,
                    **_portfolio_envelope_values(envelope),
                )
                session.add(workspace)
                session.flush()

            idempotency_hash = _instrument_idempotency_hash(
                actor_id, idempotency_key
            )
            existing = session.scalar(
                select(PersonalInstrumentRevision).where(
                    PersonalInstrumentRevision.workspace_id == workspace.id,
                    PersonalInstrumentRevision.idempotency_hash
                    == idempotency_hash,
                )
            )
            if existing is not None:
                return self._load_state(session, workspace)
            if workspace.instrument_revision != expected_revision:
                raise ValueError("revision_conflict")

            state = self._load_state(session, workspace)
            before = _instrument_state_payload(state)
            mutate(state)
            state.revision += 1
            after = _instrument_state_payload(state)
            self._write_state(session, workspace, state)

            revision_id = str(uuid4())
            revision_envelope = self._cipher.encrypt_json(
                {"before": before, "after": after},
                aad=_portfolio_aad(
                    "personal_instrument_revisions", revision_id
                ),
            )
            session.add(
                PersonalInstrumentRevision(
                    id=revision_id,
                    workspace_id=workspace.id,
                    instrument_revision=state.revision,
                    action=action,
                    idempotency_hash=idempotency_hash,
                    **_portfolio_envelope_values(revision_envelope),
                )
            )
            session.flush()
            return deepcopy(state)

    @staticmethod
    def _workspace(
        session: Session, actor_id: str, *, lock: bool
    ) -> PersonalWorkspace | None:
        statement = select(PersonalWorkspace).where(
            PersonalWorkspace.actor_identity_hash
            == _portfolio_identity_hash(actor_id)
        )
        if lock:
            statement = statement.with_for_update()
        return session.scalar(statement)

    def _load_state(
        self, session: Session, workspace: PersonalWorkspace | None
    ) -> InstrumentStateSnapshot:
        if workspace is None:
            return InstrumentStateSnapshot(revision=0, items={})
        rows = session.scalars(
            select(PersonalInstrumentState)
            .where(PersonalInstrumentState.workspace_id == workspace.id)
            .order_by(PersonalInstrumentState.id)
        ).all()
        items: dict[str, StoredInstrumentState] = {}
        for row in rows:
            payload = self._cipher.decrypt_json(
                _portfolio_row_envelope(row),
                aad=_portfolio_aad("personal_instrument_states", row.id),
            )
            item = _stored_instrument_state(payload)
            items[item.symbol] = item
        return InstrumentStateSnapshot(
            revision=workspace.instrument_revision,
            items=items,
        )

    def _write_state(
        self,
        session: Session,
        workspace: PersonalWorkspace,
        state: InstrumentStateSnapshot,
    ) -> None:
        workspace.instrument_revision = state.revision
        existing = {
            row.symbol_hmac: row
            for row in session.scalars(
                select(PersonalInstrumentState).where(
                    PersonalInstrumentState.workspace_id == workspace.id
                )
            ).all()
        }
        for item in state.items.values():
            symbol_hmac = self._cipher.symbol_lookup(
                workspace_id=workspace.id,
                normalized_symbol=item.symbol,
            )
            row = existing.get(symbol_hmac)
            row_id = row.id if row is not None else str(uuid4())
            envelope = self._cipher.encrypt_json(
                _stored_instrument_payload(item),
                aad=_portfolio_aad("personal_instrument_states", row_id),
            )
            if row is None:
                session.add(
                    PersonalInstrumentState(
                        id=row_id,
                        workspace_id=workspace.id,
                        symbol_hmac=symbol_hmac,
                        revision=state.revision,
                        **_portfolio_envelope_values(envelope),
                    )
                )
                continue
            row.revision = state.revision
            for key, value in _portfolio_envelope_values(envelope).items():
                setattr(row, key, value)


class InstrumentStateBook:
    def __init__(
        self,
        *,
        store: InstrumentStateStore,
        holding_states_reader: Callable[[str], dict[str, HoldingWatchState]],
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        trading_days_elapsed: Callable[[datetime, datetime], int] | None = None,
    ) -> None:
        self._store = store
        self._holding_states_reader = holding_states_reader
        self._clock = clock
        self._trading_days_elapsed = trading_days_elapsed or _xnys_trading_days_elapsed

    def open(self, actor: PersonalActor) -> InstrumentStatesView:
        return self._project(
            actor.actor_id,
            self._store.load(actor_id=actor.actor_id),
        )

    def revise(
        self,
        actor: PersonalActor,
        command: FollowSymbol | UnfollowSymbol,
        *,
        idempotency_key: str,
    ) -> InstrumentStatesView:
        if not idempotency_key.strip():
            raise ValueError("invalid_command")
        symbol = _normalize_symbol(command.symbol)
        holdings = self._holding_states_reader(actor.actor_id)

        def mutate(state: InstrumentStateSnapshot) -> None:
            current = state.items.get(symbol, StoredInstrumentState(symbol=symbol))
            if isinstance(command, FollowSymbol):
                reasons = tuple(
                    dict.fromkeys(
                        _normalize_reason(item) for item in command.preset_reasons
                    )
                )
                custom_reason = _normalize_custom_reason(command.custom_reason)
                if not reasons and custom_reason is None:
                    raise ValueError("watch_reason_required")
                state.items[symbol] = replace(
                    current,
                    manual_following=True,
                    manual_unfollow_holding_revision=None,
                    preset_reasons=reasons,
                    custom_reason=custom_reason,
                )
                return
            holding = holdings.get(symbol)
            if holding is not None and holding.state == "active":
                raise ValueError("holding_watch_required")
            state.items[symbol] = replace(
                current,
                manual_following=False,
                manual_unfollow_holding_revision=(
                    holding.revision if holding is not None else None
                ),
                preset_reasons=(),
                custom_reason=None,
            )

        state = self._store.revise(
            actor_id=actor.actor_id,
            expected_revision=command.expected_revision,
            idempotency_key=idempotency_key,
            action="follow_symbol" if isinstance(command, FollowSymbol) else "unfollow_symbol",
            mutate=mutate,
        )
        return self._project(actor.actor_id, state)

    def consider_candidate(
        self,
        actor: PersonalActor,
        evidence: CandidateEvidence,
        *,
        idempotency_key: str,
    ) -> InstrumentStatesView:
        if not idempotency_key.strip():
            raise ValueError("invalid_command")
        symbol = _normalize_symbol(evidence.symbol)
        relation_ids = _evidence_ids(evidence.relation_evidence_ids)
        fact_ids = _evidence_ids(evidence.fact_evidence_ids)
        now = self._clock()
        if (
            not relation_ids
            or not fact_ids
            or evidence.observed_at.tzinfo is None
            or evidence.observed_at > now
            or self._trading_days_elapsed(evidence.observed_at, now) >= 14
        ):
            raise ValueError("candidate_evidence_insufficient")

        def mutate(state: InstrumentStateSnapshot) -> None:
            current = state.items.get(symbol, StoredInstrumentState(symbol=symbol))
            merged_relation_ids = _merge_ids(
                current.relation_evidence_ids, relation_ids
            )
            merged_fact_ids = _merge_ids(current.fact_evidence_ids, fact_ids)
            if (
                merged_relation_ids == current.relation_evidence_ids
                and merged_fact_ids == current.fact_evidence_ids
            ):
                return
            refreshed_at = max(
                filter(None, (current.candidate_refreshed_at, evidence.observed_at))
            )
            state.items[symbol] = replace(
                current,
                candidate_status="active",
                relation_evidence_ids=merged_relation_ids,
                fact_evidence_ids=merged_fact_ids,
                candidate_refreshed_at=refreshed_at,
                candidate_archived_at=None,
            )

        state = self._store.revise(
            actor_id=actor.actor_id,
            expected_revision=evidence.expected_revision,
            idempotency_key=idempotency_key,
            action="consider_candidate",
            mutate=mutate,
        )
        return self._project(actor.actor_id, state)

    def archive_stale_candidates(
        self,
        actor: PersonalActor,
        *,
        as_of: datetime,
        expected_revision: int,
        idempotency_key: str,
    ) -> InstrumentStatesView:
        if as_of.tzinfo is None or not idempotency_key.strip():
            raise ValueError("invalid_command")

        def mutate(state: InstrumentStateSnapshot) -> None:
            for symbol, current in tuple(state.items.items()):
                refreshed_at = current.candidate_refreshed_at
                if (
                    current.candidate_status == "active"
                    and refreshed_at is not None
                    and self._trading_days_elapsed(refreshed_at, as_of) >= 14
                ):
                    state.items[symbol] = replace(
                        current,
                        candidate_status="archived",
                        candidate_archived_at=as_of,
                    )

        state = self._store.revise(
            actor_id=actor.actor_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            action="archive_stale_candidates",
            mutate=mutate,
        )
        return self._project(actor.actor_id, state)

    def _project(
        self, actor_id: str, state: InstrumentStateSnapshot
    ) -> InstrumentStatesView:
        holdings = {
            _normalize_symbol(symbol): holding
            for symbol, holding in self._holding_states_reader(actor_id).items()
        }
        symbols = sorted(set(holdings) | set(state.items))
        items = []
        for symbol in symbols:
            stored = state.items.get(symbol, StoredInstrumentState(symbol=symbol))
            holding = holdings.get(symbol)
            is_holding = holding is not None and holding.state == "active"
            was_holding = holding is not None
            if is_holding:
                is_followed, follow_source = True, "holding"
            elif stored.manual_following is True:
                is_followed, follow_source = True, "manual"
            elif (
                stored.manual_following is False
                and (
                    holding is None
                    or stored.manual_unfollow_holding_revision == holding.revision
                )
            ):
                is_followed, follow_source = False, "none"
            elif was_holding:
                is_followed, follow_source = True, "former_holding"
            else:
                is_followed, follow_source = False, "none"
            items.append(
                InstrumentStateView(
                    symbol=symbol,
                    is_holding=is_holding,
                    is_followed=is_followed,
                    follow_source=follow_source,
                    preset_reasons=stored.preset_reasons,
                    custom_reason=stored.custom_reason,
                    candidate_status=stored.candidate_status,
                    relation_evidence_ids=stored.relation_evidence_ids,
                    fact_evidence_ids=stored.fact_evidence_ids,
                    candidate_refreshed_at=stored.candidate_refreshed_at,
                    candidate_archived_at=stored.candidate_archived_at,
                )
            )
        return InstrumentStatesView(revision=state.revision, items=tuple(items))


def _normalize_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", symbol):
        raise ValueError("unsupported_instrument")
    return symbol


def _normalize_reason(value: str) -> str:
    reason = value.strip()
    if not reason or len(reason) > 80:
        raise ValueError("invalid_command")
    return reason


def _normalize_custom_reason(value: str | None) -> str | None:
    if value is None:
        return None
    reason = value.strip()
    if not reason:
        return None
    if len(reason) > 500:
        raise ValueError("invalid_command")
    return reason


def _evidence_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
    if any(len(value) > 200 for value in normalized):
        raise ValueError("candidate_evidence_insufficient")
    return normalized


def _merge_ids(existing: tuple[str, ...], incoming: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*existing, *incoming)))


def _instrument_idempotency_hash(actor_id: str, idempotency_key: str) -> str:
    return sha256(f"{actor_id}|instrument|{idempotency_key}".encode()).hexdigest()


def _stored_instrument_payload(item: StoredInstrumentState) -> dict:
    return {
        "symbol": item.symbol,
        "manual_following": item.manual_following,
        "manual_unfollow_holding_revision": item.manual_unfollow_holding_revision,
        "preset_reasons": list(item.preset_reasons),
        "custom_reason": item.custom_reason,
        "candidate_status": item.candidate_status,
        "relation_evidence_ids": list(item.relation_evidence_ids),
        "fact_evidence_ids": list(item.fact_evidence_ids),
        "candidate_refreshed_at": _optional_datetime_payload(
            item.candidate_refreshed_at
        ),
        "candidate_archived_at": _optional_datetime_payload(
            item.candidate_archived_at
        ),
    }


def _stored_instrument_state(payload: dict) -> StoredInstrumentState:
    return StoredInstrumentState(
        symbol=_normalize_symbol(str(payload["symbol"])),
        manual_following=payload.get("manual_following"),
        manual_unfollow_holding_revision=payload.get(
            "manual_unfollow_holding_revision"
        ),
        preset_reasons=tuple(str(item) for item in payload.get("preset_reasons", ())),
        custom_reason=payload.get("custom_reason"),
        candidate_status=payload.get("candidate_status"),
        relation_evidence_ids=tuple(
            str(item) for item in payload.get("relation_evidence_ids", ())
        ),
        fact_evidence_ids=tuple(
            str(item) for item in payload.get("fact_evidence_ids", ())
        ),
        candidate_refreshed_at=_optional_datetime(
            payload.get("candidate_refreshed_at")
        ),
        candidate_archived_at=_optional_datetime(
            payload.get("candidate_archived_at")
        ),
    )


def _instrument_state_payload(state: InstrumentStateSnapshot) -> dict:
    return {
        "instrument_revision": state.revision,
        "items": [
            _stored_instrument_payload(item)
            for item in sorted(state.items.values(), key=lambda value: value.symbol)
        ],
    }


def _optional_datetime_payload(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("invalid_instrument_state")
    return parsed


def _xnys_trading_days_elapsed(start: datetime, end: datetime) -> int:
    if end <= start:
        return 0
    import exchange_calendars as xcals
    import pandas as pd

    calendar = xcals.get_calendar("XNYS")
    sessions = calendar.sessions_in_range(
        pd.Timestamp(start.date()), pd.Timestamp(end.date())
    )
    return sum(session.date() > start.date() for session in sessions)
