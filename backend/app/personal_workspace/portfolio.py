from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, wait
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, time, timezone
from decimal import Decimal
from hashlib import sha256
import hmac
import json
import os
import re
from typing import Any, Callable, Literal, Mapping, Protocol, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.models import (
    PersonalAuditEvent,
    PersonalEquitySnapshot,
    PersonalHolding,
    PersonalPortfolioRevision,
    PersonalPriceObservation,
    PersonalRecordPrivateFragment,
    PersonalRedactionEvent,
    PersonalWorkspace,
)
from backend.app.market_observation.alpaca import MarketObservationError
from backend.app.market_observation.contracts import AuthorizationDenied

from .contracts import (
    AddHoldingCommand,
    EditHoldingCommand,
    PersonalActor,
    PortfolioCommand,
    PurgeHoldingCommand,
    RemoveHoldingCommand,
    RestoreHoldingCommand,
    SetUsdCashCommand,
)
from .crypto import EncryptedEnvelope, PersonalDataCipher


SourceHealth = Literal["fresh", "stale", "degraded", "unavailable"]
Availability = Literal["available", "not_available", "not_applicable"]


@dataclass(frozen=True)
class PortfolioPriceObservation:
    availability: Availability
    price: Decimal | None
    reason_code: str | None
    source_health: SourceHealth
    as_of: datetime | None
    feed: str | None
    delay_seconds: int | None
    source_ids: tuple[str, ...]
    cached: bool = False

    @classmethod
    def available(
        cls,
        *,
        price: Decimal,
        source_health: SourceHealth,
        as_of: datetime,
        feed: str,
        delay_seconds: int,
        source_ids: tuple[str, ...],
    ) -> "PortfolioPriceObservation":
        return cls(
            availability="available",
            price=price,
            reason_code=None,
            source_health=source_health,
            as_of=as_of,
            feed=feed,
            delay_seconds=delay_seconds,
            source_ids=source_ids,
        )

    @classmethod
    def unavailable(
        cls,
        reason_code: str,
        *,
        source_health: SourceHealth = "unavailable",
        as_of: datetime | None = None,
        source_ids: tuple[str, ...] = (),
    ) -> "PortfolioPriceObservation":
        return cls(
            availability="not_available",
            price=None,
            reason_code=reason_code,
            source_health=source_health,
            as_of=as_of,
            feed=None,
            delay_seconds=None,
            source_ids=source_ids,
        )


class PortfolioMarketReader(Protocol):
    def observe_price(self, symbol: str) -> PortfolioPriceObservation: ...


@dataclass(frozen=True)
class ObservedDecimalView:
    availability: Availability
    value: str | None
    reason_code: str | None
    source_health: SourceHealth
    as_of: datetime | None
    source_ids: tuple[str, ...]
    feed: str | None = None
    delay_seconds: int | None = None
    cached: bool = False


@dataclass(frozen=True)
class HoldingView:
    holding_id: str
    symbol: str
    name: str
    state: str
    revision: int
    quantity: str
    average_cost: str
    cost_amount: str
    currency: str
    verification_status: str
    market_price: ObservedDecimalView
    market_value: ObservedDecimalView
    unrealized_profit_loss: ObservedDecimalView
    unrealized_return: ObservedDecimalView
    weight: ObservedDecimalView


@dataclass(frozen=True)
class PortfolioView:
    workspace_id: str | None
    portfolio_revision: int
    currency: str
    usd_cash: str
    holdings: tuple[HoldingView, ...]
    total_market_value: ObservedDecimalView
    total_equity: ObservedDecimalView
    active_holding_count: int
    priced_holding_count: int
    issues: tuple[str, ...]


@dataclass(frozen=True)
class PurgeChallengeView:
    holding_id: str
    portfolio_revision: int
    challenge: str
    expires_at: datetime


@dataclass(frozen=True)
class DeletionReceipt:
    holding_id: str
    status: str
    portfolio_revision: int
    purged_at: datetime
    backup_status: str
    backup_expires_at: datetime


@dataclass
class HoldingState:
    holding_id: str
    symbol: str
    name: str
    quantity: Decimal
    average_cost: Decimal
    state: str = "active"
    revision: int = 1
    verification_status: str = "pending_verification"


@dataclass
class PortfolioState:
    workspace_id: str | None
    revision: int
    usd_cash: Decimal
    holdings: dict[str, HoldingState]


class PortfolioStore(Protocol):
    def load(self, *, actor_id: str) -> PortfolioState: ...

    def revise(
        self,
        *,
        actor_id: str,
        expected_revision: int,
        idempotency_key: str,
        action: str,
        mutate: Callable[[PortfolioState], None],
    ) -> PortfolioState: ...

    def purge(
        self,
        *,
        actor_id: str,
        holding_id: str,
        expected_revision: int,
        idempotency_key: str,
        receipt_factory: Callable[[int], DeletionReceipt],
    ) -> tuple[PortfolioState, DeletionReceipt]: ...

    def purge_receipt(
        self, *, actor_id: str, idempotency_key: str
    ) -> DeletionReceipt | None: ...


class InMemoryPortfolioStore:
    def __init__(self) -> None:
        self._states: dict[str, PortfolioState] = {}
        self._revision_keys: dict[tuple[str, str], int] = {}
        self._purge_receipts: dict[tuple[str, str], DeletionReceipt] = {}

    def load(self, *, actor_id: str) -> PortfolioState:
        state = self._states.get(actor_id)
        if state is None:
            return PortfolioState(workspace_id=None, revision=0, usd_cash=Decimal("0"), holdings={})
        return deepcopy(state)

    def revise(
        self,
        *,
        actor_id: str,
        expected_revision: int,
        idempotency_key: str,
        action: str,
        mutate: Callable[[PortfolioState], None],
    ) -> PortfolioState:
        key = (actor_id, idempotency_key)
        if key in self._revision_keys:
            return self.load(actor_id=actor_id)
        current = self.load(actor_id=actor_id)
        if current.revision != expected_revision:
            raise ValueError("revision_conflict")
        if current.workspace_id is None:
            current.workspace_id = str(uuid4())
        mutate(current)
        current.revision += 1
        self._states[actor_id] = deepcopy(current)
        self._revision_keys[key] = current.revision
        return deepcopy(current)

    def purge(
        self,
        *,
        actor_id: str,
        holding_id: str,
        expected_revision: int,
        idempotency_key: str,
        receipt_factory: Callable[[int], DeletionReceipt],
    ) -> tuple[PortfolioState, DeletionReceipt]:
        existing = self.purge_receipt(actor_id=actor_id, idempotency_key=idempotency_key)
        if existing is not None:
            return self.load(actor_id=actor_id), existing
        current = self.load(actor_id=actor_id)
        if current.revision != expected_revision:
            raise ValueError("revision_conflict")
        if holding_id not in current.holdings:
            raise ValueError("private_object_not_found")
        del current.holdings[holding_id]
        current.revision += 1
        receipt = receipt_factory(current.revision)
        self._states[actor_id] = deepcopy(current)
        self._purge_receipts[(actor_id, idempotency_key)] = receipt
        return deepcopy(current), receipt

    def purge_receipt(
        self, *, actor_id: str, idempotency_key: str
    ) -> DeletionReceipt | None:
        return self._purge_receipts.get((actor_id, idempotency_key))


_US_MARKET_TZ = ZoneInfo("America/New_York")
_ET_CLOSE = time(16, 0)


@dataclass(frozen=True)
class EquitySnapshot:
    market_day: date
    total_equity: Decimal
    total_market_value: Decimal
    usd_cash: Decimal
    holdings_count: int
    priced_count: int
    after_close: bool
    observed_at: datetime
    payload: dict[str, Any]


@dataclass(frozen=True)
class EquitySnapshotView:
    market_day: str
    total_equity: str
    total_market_value: str
    usd_cash: str
    holdings_count: int
    priced_count: int
    after_close: bool
    observed_at: datetime


class PriceObservationStore(Protocol):
    def upsert(
        self, *, actor_id: str, observations: Mapping[str, PortfolioPriceObservation]
    ) -> None: ...

    def latest(
        self, *, actor_id: str, symbols: Sequence[str]
    ) -> dict[str, PortfolioPriceObservation]: ...


class InMemoryPriceObservationStore:
    def __init__(self) -> None:
        self._by_actor: dict[str, dict[str, PortfolioPriceObservation]] = {}

    def upsert(
        self, *, actor_id: str, observations: Mapping[str, PortfolioPriceObservation]
    ) -> None:
        bucket = self._by_actor.setdefault(actor_id, {})
        for symbol, observation in observations.items():
            if observation.availability == "available" and observation.price is not None:
                bucket[symbol] = observation

    def latest(
        self, *, actor_id: str, symbols: Sequence[str]
    ) -> dict[str, PortfolioPriceObservation]:
        bucket = self._by_actor.get(actor_id, {})
        return {
            symbol: replace(bucket[symbol], cached=True, source_health="stale")
            for symbol in symbols
            if symbol in bucket
        }


class PostgresPriceObservationStore:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        cipher: PersonalDataCipher,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    def upsert(
        self, *, actor_id: str, observations: Mapping[str, PortfolioPriceObservation]
    ) -> None:
        available = {
            symbol: observation
            for symbol, observation in observations.items()
            if observation.availability == "available" and observation.price is not None
        }
        if not available:
            return
        observed_at = datetime.now(timezone.utc)
        with self._session_factory() as session, session.begin():
            workspace = self._workspace(session, actor_id, lock=True)
            if workspace is None:
                return
            for symbol, observation in available.items():
                symbol_hmac = self._cipher.symbol_lookup(
                    workspace_id=workspace.id, normalized_symbol=symbol
                )
                row = session.scalar(
                    select(PersonalPriceObservation).where(
                        PersonalPriceObservation.workspace_id == workspace.id,
                        PersonalPriceObservation.symbol_hmac == symbol_hmac,
                    )
                )
                envelope = self._cipher.encrypt_json(
                    _price_payload(observation),
                    aad=_portfolio_aad(
                        "personal_price_observations", f"{workspace.id}|{symbol}"
                    ),
                )
                if row is None:
                    session.add(
                        PersonalPriceObservation(
                            id=str(uuid4()),
                            workspace_id=workspace.id,
                            symbol_hmac=symbol_hmac,
                            observed_at=observed_at,
                            ciphertext=envelope.ciphertext,
                            nonce=envelope.nonce,
                            key_id=envelope.key_id,
                            payload_schema=envelope.payload_schema,
                        )
                    )
                else:
                    row.observed_at = observed_at
                    row.ciphertext = envelope.ciphertext
                    row.nonce = envelope.nonce
                    row.key_id = envelope.key_id
                    row.payload_schema = envelope.payload_schema

    def latest(
        self, *, actor_id: str, symbols: Sequence[str]
    ) -> dict[str, PortfolioPriceObservation]:
        if not symbols:
            return {}
        with self._session_factory() as session:
            workspace = self._workspace(session, actor_id, lock=False)
            if workspace is None:
                return {}
            hmac_to_symbol = {
                self._cipher.symbol_lookup(
                    workspace_id=workspace.id, normalized_symbol=symbol
                ): symbol
                for symbol in symbols
            }
            rows = session.scalars(
                select(PersonalPriceObservation).where(
                    PersonalPriceObservation.workspace_id == workspace.id,
                    PersonalPriceObservation.symbol_hmac.in_(tuple(hmac_to_symbol)),
                )
            ).all()
            result: dict[str, PortfolioPriceObservation] = {}
            for row in rows:
                symbol = hmac_to_symbol.get(row.symbol_hmac)
                if symbol is None:
                    continue
                payload = self._cipher.decrypt_json(
                    _portfolio_row_envelope(row),
                    aad=_portfolio_aad(
                        "personal_price_observations", f"{workspace.id}|{symbol}"
                    ),
                )
                result[symbol] = PortfolioPriceObservation(
                    availability="available",
                    price=Decimal(str(payload["price"])),
                    reason_code=payload.get("reason_code") or "cached_price_fallback",
                    source_health="stale",
                    as_of=_payload_datetime(payload.get("as_of")),
                    feed=payload.get("feed"),
                    delay_seconds=payload.get("delay_seconds"),
                    source_ids=(),
                    cached=True,
                )
            return result

    @staticmethod
    def _workspace(
        session: Session, actor_id: str, *, lock: bool
    ) -> PersonalWorkspace | None:
        statement = select(PersonalWorkspace).where(
            PersonalWorkspace.actor_identity_hash == _portfolio_identity_hash(actor_id)
        )
        if lock:
            statement = statement.with_for_update()
        return session.scalar(statement)


class EquitySnapshotStore(Protocol):
    def upsert(self, *, actor_id: str, snapshot: EquitySnapshot) -> None: ...

    def history(self, *, actor_id: str, limit: int) -> tuple[EquitySnapshotView, ...]: ...


class InMemoryEquitySnapshotStore:
    def __init__(self) -> None:
        self._by_actor: dict[str, dict[date, EquitySnapshot]] = {}

    def upsert(self, *, actor_id: str, snapshot: EquitySnapshot) -> None:
        self._by_actor.setdefault(actor_id, {})[snapshot.market_day] = snapshot

    def history(
        self, *, actor_id: str, limit: int
    ) -> tuple[EquitySnapshotView, ...]:
        rows = sorted(
            self._by_actor.get(actor_id, {}).values(), key=lambda item: item.market_day
        )
        return tuple(_snapshot_view(item) for item in rows[-max(limit, 0) :])


class PostgresEquitySnapshotStore:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        cipher: PersonalDataCipher,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    def upsert(self, *, actor_id: str, snapshot: EquitySnapshot) -> None:
        with self._session_factory() as session, session.begin():
            workspace = self._workspace(session, actor_id, lock=True)
            if workspace is None:
                return
            aad = _portfolio_aad(
                "personal_equity_snapshots",
                f"{workspace.id}|{snapshot.market_day.isoformat()}",
            )
            envelope = self._cipher.encrypt_json(snapshot.payload, aad=aad)
            row = session.scalar(
                select(PersonalEquitySnapshot).where(
                    PersonalEquitySnapshot.workspace_id == workspace.id,
                    PersonalEquitySnapshot.market_day == snapshot.market_day,
                )
            )
            if row is None:
                session.add(
                    PersonalEquitySnapshot(
                        id=str(uuid4()),
                        workspace_id=workspace.id,
                        market_day=snapshot.market_day,
                        total_equity=snapshot.total_equity,
                        total_market_value=snapshot.total_market_value,
                        usd_cash=snapshot.usd_cash,
                        holdings_count=snapshot.holdings_count,
                        priced_count=snapshot.priced_count,
                        after_close=snapshot.after_close,
                        observed_at=snapshot.observed_at,
                        ciphertext=envelope.ciphertext,
                        nonce=envelope.nonce,
                        key_id=envelope.key_id,
                        payload_schema=envelope.payload_schema,
                    )
                )
            else:
                row.total_equity = snapshot.total_equity
                row.total_market_value = snapshot.total_market_value
                row.usd_cash = snapshot.usd_cash
                row.holdings_count = snapshot.holdings_count
                row.priced_count = snapshot.priced_count
                row.after_close = snapshot.after_close
                row.observed_at = snapshot.observed_at
                row.ciphertext = envelope.ciphertext
                row.nonce = envelope.nonce
                row.key_id = envelope.key_id
                row.payload_schema = envelope.payload_schema

    def history(
        self, *, actor_id: str, limit: int
    ) -> tuple[EquitySnapshotView, ...]:
        if limit <= 0:
            return ()
        with self._session_factory() as session:
            workspace = self._workspace(session, actor_id, lock=False)
            if workspace is None:
                return ()
            rows = session.scalars(
                select(PersonalEquitySnapshot)
                .where(PersonalEquitySnapshot.workspace_id == workspace.id)
                .order_by(PersonalEquitySnapshot.market_day.desc())
                .limit(limit)
            ).all()
            return tuple(
                EquitySnapshotView(
                    market_day=row.market_day.isoformat(),
                    total_equity=_money(row.total_equity),
                    total_market_value=_money(row.total_market_value),
                    usd_cash=_money(row.usd_cash),
                    holdings_count=row.holdings_count,
                    priced_count=row.priced_count,
                    after_close=row.after_close,
                    observed_at=row.observed_at,
                )
                for row in reversed(rows)
            )

    @staticmethod
    def _workspace(
        session: Session, actor_id: str, *, lock: bool
    ) -> PersonalWorkspace | None:
        statement = select(PersonalWorkspace).where(
            PersonalWorkspace.actor_identity_hash == _portfolio_identity_hash(actor_id)
        )
        if lock:
            statement = statement.with_for_update()
        return session.scalar(statement)


def _snapshot_view(snapshot: EquitySnapshot) -> EquitySnapshotView:
    return EquitySnapshotView(
        market_day=snapshot.market_day.isoformat(),
        total_equity=_money(snapshot.total_equity),
        total_market_value=_money(snapshot.total_market_value),
        usd_cash=_money(snapshot.usd_cash),
        holdings_count=snapshot.holdings_count,
        priced_count=snapshot.priced_count,
        after_close=snapshot.after_close,
        observed_at=snapshot.observed_at,
    )


class PostgresPortfolioStore:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        cipher: PersonalDataCipher,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    def load(self, *, actor_id: str) -> PortfolioState:
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
        mutate: Callable[[PortfolioState], None],
    ) -> PortfolioState:
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
                    **_portfolio_envelope_values(envelope),
                )
                session.add(workspace)
                session.flush()
            existing = session.scalar(
                select(PersonalPortfolioRevision).where(
                    PersonalPortfolioRevision.workspace_id == workspace.id,
                    PersonalPortfolioRevision.idempotency_hash
                    == _portfolio_idempotency_hash(actor_id, idempotency_key),
                )
            )
            if existing is not None:
                return self._load_state(session, workspace)
            if workspace.revision != expected_revision:
                raise ValueError("revision_conflict")

            state = self._load_state(session, workspace)
            before = _portfolio_state_payload(state)
            mutate(state)
            state.revision += 1
            changed_holding_id = _changed_holding_id(before, state)
            after = _portfolio_state_payload(state)
            self._write_state(session, workspace, state)
            revision_id = str(uuid4())
            revision_envelope = self._cipher.encrypt_json(
                _revision_change_payload(
                    before,
                    after,
                    changed_holding_id=changed_holding_id,
                ),
                aad=_portfolio_aad("personal_portfolio_revisions", revision_id),
            )
            session.add(
                PersonalPortfolioRevision(
                    id=revision_id,
                    workspace_id=workspace.id,
                    holding_id=changed_holding_id,
                    portfolio_revision=state.revision,
                    action=action,
                    source="manual",
                    idempotency_hash=_portfolio_idempotency_hash(actor_id, idempotency_key),
                    **_portfolio_envelope_values(revision_envelope),
                )
            )
            session.flush()
            return deepcopy(state)

    def purge(
        self,
        *,
        actor_id: str,
        holding_id: str,
        expected_revision: int,
        idempotency_key: str,
        receipt_factory: Callable[[int], DeletionReceipt],
    ) -> tuple[PortfolioState, DeletionReceipt]:
        with self._session_factory() as session, session.begin():
            workspace = self._workspace(session, actor_id, lock=True)
            if workspace is None:
                raise ValueError("private_object_not_found")
            existing = self._audit_by_key(session, workspace.id, actor_id, idempotency_key)
            if existing is not None:
                return self._load_state(session, workspace), _audit_receipt(existing)
            if workspace.revision != expected_revision:
                raise ValueError("revision_conflict")
            holding = session.scalar(
                select(PersonalHolding).where(
                    PersonalHolding.workspace_id == workspace.id,
                    PersonalHolding.id == holding_id,
                    PersonalHolding.synthetic.is_(False),
                )
            )
            if holding is None:
                raise ValueError("private_object_not_found")
            session.execute(
                delete(PersonalPortfolioRevision).where(
                    PersonalPortfolioRevision.workspace_id == workspace.id,
                    PersonalPortfolioRevision.holding_id == holding_id,
                )
            )
            session.delete(holding)
            session.execute(
                delete(PersonalRecordPrivateFragment).where(
                    PersonalRecordPrivateFragment.workspace_id == workspace.id,
                    PersonalRecordPrivateFragment.holding_id == holding_id,
                )
            )
            workspace.revision += 1
            state = self._load_state(session, workspace)
            state.revision = workspace.revision
            state.holdings.pop(holding_id, None)
            self._write_workspace(workspace, state.usd_cash)
            receipt = receipt_factory(workspace.revision)
            session.add(
                PersonalAuditEvent(
                    id=str(uuid4()),
                    workspace_id=workspace.id,
                    object_id=holding_id,
                    action="purge_holding",
                    status="completed",
                    idempotency_hash=_portfolio_idempotency_hash(actor_id, idempotency_key),
                    portfolio_revision=workspace.revision,
                    occurred_at=receipt.purged_at,
                    backup_expires_at=receipt.backup_expires_at,
                )
            )
            session.add(
                PersonalRedactionEvent(
                    id=str(uuid4()),
                    workspace_id=workspace.id,
                    object_type="holding",
                    object_id=holding_id,
                    reason="source_holding_purged",
                    idempotency_hash=sha256(
                        f"holding-redaction|{actor_id}|{idempotency_key}".encode("utf-8")
                    ).hexdigest(),
                    occurred_at=receipt.purged_at,
                    backup_expires_at=receipt.backup_expires_at,
                )
            )
            session.flush()
            return state, receipt

    def purge_receipt(
        self, *, actor_id: str, idempotency_key: str
    ) -> DeletionReceipt | None:
        with self._session_factory() as session:
            workspace = self._workspace(session, actor_id, lock=False)
            if workspace is None:
                return None
            row = self._audit_by_key(session, workspace.id, actor_id, idempotency_key)
            return _audit_receipt(row) if row is not None else None

    def _workspace(
        self, session: Session, actor_id: str, *, lock: bool
    ) -> PersonalWorkspace | None:
        statement = select(PersonalWorkspace).where(
            PersonalWorkspace.actor_identity_hash == _portfolio_identity_hash(actor_id)
        )
        if lock:
            statement = statement.with_for_update()
        return session.scalar(statement)

    def _load_state(
        self, session: Session, workspace: PersonalWorkspace | None
    ) -> PortfolioState:
        if workspace is None:
            return PortfolioState(
                workspace_id=None,
                revision=0,
                usd_cash=Decimal("0"),
                holdings={},
            )
        workspace_payload = self._cipher.decrypt_json(
            _portfolio_row_envelope(workspace),
            aad=_portfolio_aad("personal_workspaces", workspace.id),
        )
        rows = session.scalars(
            select(PersonalHolding)
            .where(
                PersonalHolding.workspace_id == workspace.id,
                PersonalHolding.synthetic.is_(False),
            )
            .order_by(PersonalHolding.id)
        ).all()
        holdings: dict[str, HoldingState] = {}
        for row in rows:
            payload = self._cipher.decrypt_json(
                _portfolio_row_envelope(row),
                aad=_portfolio_aad("personal_holdings", row.id),
            )
            holdings[row.id] = HoldingState(
                holding_id=row.id,
                symbol=str(payload["symbol"]),
                name=str(payload["name"]),
                quantity=Decimal(str(payload["quantity"])),
                average_cost=Decimal(str(payload["average_cost"])),
                state=row.state,
                revision=row.revision,
                verification_status=str(
                    payload.get("verification_status", "pending_verification")
                ),
            )
        return PortfolioState(
            workspace_id=workspace.id,
            revision=workspace.revision,
            usd_cash=Decimal(str(workspace_payload.get("usd_cash", "0"))),
            holdings=holdings,
        )

    def _write_state(
        self, session: Session, workspace: PersonalWorkspace, state: PortfolioState
    ) -> None:
        workspace.revision = state.revision
        self._write_workspace(workspace, state.usd_cash)
        existing = {
            row.id: row
            for row in session.scalars(
                select(PersonalHolding).where(
                    PersonalHolding.workspace_id == workspace.id,
                    PersonalHolding.synthetic.is_(False),
                )
            ).all()
        }
        for holding in state.holdings.values():
            payload = {
                "symbol": holding.symbol,
                "name": holding.name,
                "quantity": str(holding.quantity),
                "average_cost": str(holding.average_cost),
                "currency": "USD",
                "verification_status": holding.verification_status,
            }
            envelope = self._cipher.encrypt_json(
                payload,
                aad=_portfolio_aad("personal_holdings", holding.holding_id),
            )
            row = existing.get(holding.holding_id)
            if row is None:
                row = PersonalHolding(
                    id=holding.holding_id,
                    workspace_id=workspace.id,
                    symbol_hmac=self._cipher.symbol_lookup(
                        workspace_id=workspace.id,
                        normalized_symbol=holding.symbol,
                    ),
                    state=holding.state,
                    synthetic=False,
                    revision=holding.revision,
                    **_portfolio_envelope_values(envelope),
                )
                session.add(row)
            else:
                row.state = holding.state
                row.revision = holding.revision
                for key, value in _portfolio_envelope_values(envelope).items():
                    setattr(row, key, value)

    def _write_workspace(self, workspace: PersonalWorkspace, usd_cash: Decimal) -> None:
        envelope = self._cipher.encrypt_json(
            {"usd_cash": str(usd_cash)},
            aad=_portfolio_aad("personal_workspaces", workspace.id),
        )
        for key, value in _portfolio_envelope_values(envelope).items():
            setattr(workspace, key, value)

    @staticmethod
    def _audit_by_key(
        session: Session,
        workspace_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> PersonalAuditEvent | None:
        return session.scalar(
            select(PersonalAuditEvent).where(
                PersonalAuditEvent.workspace_id == workspace_id,
                PersonalAuditEvent.idempotency_hash
                == _portfolio_idempotency_hash(actor_id, idempotency_key),
            )
        )


class UnavailablePortfolioMarketReader:
    def observe_price(self, symbol: str) -> PortfolioPriceObservation:
        return PortfolioPriceObservation.unavailable("provider_unavailable")


class AlpacaPortfolioMarketReader:
    def __init__(self, *, adapter, clock: Callable[[], datetime] | None = None) -> None:
        self._adapter = adapter
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def observe_price(self, symbol: str) -> PortfolioPriceObservation:
        try:
            observed = self._adapter.observe_delayed_price(
                symbol,
                observed_at=self._clock(),
                purpose="display",
            )
        except (AuthorizationDenied, MarketObservationError) as exc:
            return PortfolioPriceObservation.unavailable(
                getattr(exc, "code", str(exc)) or "provider_unavailable"
            )
        provenance = observed.provenance
        source_identity = provenance.fallback_identity or provenance.provider_record_id
        source_ids = tuple(
            value
            for value in (source_identity, provenance.authorization_snapshot_id)
            if value
        )
        if observed.availability != "available" or observed.value is None:
            return PortfolioPriceObservation.unavailable(
                observed.reason_code or "provider_unavailable",
                source_health=observed.source_health,
                as_of=observed.as_of,
                source_ids=source_ids,
            )
        return PortfolioPriceObservation(
            availability="available",
            price=observed.value.price,
            reason_code=observed.reason_code,
            source_health=observed.source_health,
            as_of=observed.as_of,
            feed=observed.value.feed,
            delay_seconds=observed.value.delay_seconds,
            source_ids=source_ids,
        )


class PortfolioBook:
    def __init__(
        self,
        *,
        store: PortfolioStore,
        market: PortfolioMarketReader,
        clock: Callable[[], datetime] | None = None,
        challenge_key: bytes | None = None,
        provider_wait_seconds: float = 1.8,
        prices: PriceObservationStore | None = None,
        snapshots: EquitySnapshotStore | None = None,
        cached_price_max_age_days: int = 7,
    ) -> None:
        if provider_wait_seconds <= 0:
            raise ValueError("provider_wait_seconds_must_be_positive")
        if cached_price_max_age_days < 0:
            raise ValueError("cached_price_max_age_days_must_be_non_negative")
        self._store = store
        self._market = market
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._challenge_key = challenge_key or os.urandom(32)
        self._provider_wait_seconds = provider_wait_seconds
        self._prices = prices or InMemoryPriceObservationStore()
        self._snapshots = snapshots or InMemoryEquitySnapshotStore()
        self._cached_price_max_age_days = cached_price_max_age_days

    def open(self, actor: PersonalActor) -> PortfolioView:
        return self._project(actor, self._store.load(actor_id=actor.actor_id))

    def average_cost(self, actor: PersonalActor, symbol: str) -> Decimal | None:
        normalized_symbol = _normalize_symbol(symbol)
        state = self._store.load(actor_id=actor.actor_id)
        for holding in state.holdings.values():
            if holding.symbol == normalized_symbol and holding.state == "active":
                return holding.average_cost
        return None

    def equity_history(
        self, actor: PersonalActor, *, limit: int = 120
    ) -> tuple[EquitySnapshotView, ...]:
        return self._snapshots.history(
            actor_id=actor.actor_id, limit=max(1, min(limit, 1000))
        )

    def revise(
        self,
        actor: PersonalActor,
        command: PortfolioCommand,
        *,
        idempotency_key: str,
    ) -> PortfolioView:
        if not idempotency_key.strip():
            raise ValueError("invalid_command")
        state = self._store.revise(
            actor_id=actor.actor_id,
            expected_revision=command.expected_portfolio_revision,
            idempotency_key=idempotency_key,
            action=command.type,
            mutate=lambda current: self._apply(current, command),
        )
        return self._project(actor, state)

    def request_purge(
        self,
        actor: PersonalActor,
        *,
        holding_id: str,
        expected_portfolio_revision: int,
    ) -> PurgeChallengeView:
        state = self._store.load(actor_id=actor.actor_id)
        if state.revision != expected_portfolio_revision:
            raise ValueError("revision_conflict")
        if holding_id not in state.holdings:
            raise ValueError("private_object_not_found")
        expires_at = self._clock() + timedelta(minutes=10)
        payload = {
            "actor": sha256(actor.actor_id.encode("utf-8")).hexdigest(),
            "holding_id": holding_id,
            "portfolio_revision": expected_portfolio_revision,
            "expires_at": int(expires_at.timestamp()),
        }
        encoded = _b64url(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signature = _b64url(
            hmac.new(self._challenge_key, encoded.encode("ascii"), sha256).digest()
        )
        return PurgeChallengeView(
            holding_id=holding_id,
            portfolio_revision=expected_portfolio_revision,
            challenge=f"{encoded}.{signature}",
            expires_at=expires_at,
        )

    def purge(
        self,
        actor: PersonalActor,
        command: PurgeHoldingCommand,
        *,
        idempotency_key: str,
    ) -> DeletionReceipt:
        existing = self._store.purge_receipt(
            actor_id=actor.actor_id, idempotency_key=idempotency_key
        )
        if existing is not None:
            return existing
        self._verify_challenge(actor, command)
        purged_at = self._clock()
        _, receipt = self._store.purge(
            actor_id=actor.actor_id,
            holding_id=command.holding_id,
            expected_revision=command.expected_portfolio_revision,
            idempotency_key=idempotency_key,
            receipt_factory=lambda revision: DeletionReceipt(
                holding_id=command.holding_id,
                status="purged",
                portfolio_revision=revision,
                purged_at=purged_at,
                backup_status="expires_within_window",
                backup_expires_at=purged_at + timedelta(days=30),
            ),
        )
        return receipt

    def _apply(self, state: PortfolioState, command: PortfolioCommand) -> None:
        if isinstance(command, AddHoldingCommand):
            symbol = _normalize_symbol(command.symbol)
            if any(holding.symbol == symbol for holding in state.holdings.values()):
                raise ValueError("duplicate_symbol")
            holding_id = str(uuid4())
            state.holdings[holding_id] = HoldingState(
                holding_id=holding_id,
                symbol=symbol,
                name=_normalize_name(command.name),
                quantity=command.quantity,
                average_cost=command.average_cost,
            )
            return
        if isinstance(command, SetUsdCashCommand):
            state.usd_cash = command.usd_cash
            return
        holding_id = getattr(command, "holding_id", "")
        holding = state.holdings.get(holding_id)
        if holding is None:
            raise ValueError("private_object_not_found")
        if isinstance(command, EditHoldingCommand):
            if holding.state != "active":
                raise ValueError("invalid_command")
            state.holdings[holding_id] = replace(
                holding,
                name=_normalize_name(command.name),
                quantity=command.quantity,
                average_cost=command.average_cost,
                revision=holding.revision + 1,
            )
        elif isinstance(command, RemoveHoldingCommand):
            if holding.state != "active":
                raise ValueError("invalid_command")
            state.holdings[holding_id] = replace(
                holding, state="removed", revision=holding.revision + 1
            )
        elif isinstance(command, RestoreHoldingCommand):
            if holding.state != "removed":
                raise ValueError("invalid_command")
            state.holdings[holding_id] = replace(
                holding, state="active", revision=holding.revision + 1
            )
        else:
            raise ValueError("invalid_command")

    def _project(self, actor: PersonalActor, state: PortfolioState) -> PortfolioView:
        active = [holding for holding in state.holdings.values() if holding.state == "active"]
        observations: dict[str, PortfolioPriceObservation] = {}
        if active:
            executor = ThreadPoolExecutor(max_workers=min(len(active), 8))
            try:
                futures = {
                    holding.holding_id: executor.submit(
                        self._market.observe_price, holding.symbol
                    )
                    for holding in active
                }
                finished, unfinished = wait(
                    futures.values(), timeout=self._provider_wait_seconds
                )
                for future in unfinished:
                    future.cancel()
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            observations = {
                holding_id: (
                    future.result()
                    if future in finished
                    else PortfolioPriceObservation.unavailable("provider_timeout")
                )
                for holding_id, future in futures.items()
            }
            self._persist_available_prices(actor, active, observations)
            self._apply_cached_fallback(actor, active, observations)
        available = {
            holding.holding_id: observations[holding.holding_id]
            for holding in active
            if observations[holding.holding_id].availability == "available"
        }
        all_available = len(available) == len(active)
        issue_values = [
            observation.reason_code
            for observation in observations.values()
            if observation.reason_code is not None
        ]
        if available and not all_available:
            issue_values.append("partial_valuation")
        issues = tuple(dict.fromkeys(issue_values))
        total_equity: Decimal | None = None
        total_market: Decimal | None = None
        if all_available:
            total_market = sum(
                (
                    holding.quantity * observations[holding.holding_id].price
                    for holding in active
                ),
                Decimal("0"),
            )
            total_equity = total_market + state.usd_cash
            total_market_view = _observed_available(total_market, observations.values())
            total_equity_view = _observed_available(total_equity, observations.values())
        elif available:
            total_market = sum(
                (
                    holding.quantity * available[holding.holding_id].price
                    for holding in active
                    if holding.holding_id in available
                ),
                Decimal("0"),
            )
            covered_observations = available.values()
            total_market_view = _observed_available(
                total_market,
                covered_observations,
                reason_code="partial_valuation",
                source_health="degraded",
            )
            total_equity_view = _observed_available(
                total_market + state.usd_cash,
                covered_observations,
                reason_code="partial_valuation",
                source_health="degraded",
            )
        else:
            reason = issues[0] if issues else "provider_unavailable"
            source_health = _worst_health(observations.values())
            total_market_view = _observed_unavailable(reason, source_health=source_health)
            total_equity_view = _observed_unavailable(reason, source_health=source_health)

        self._write_equity_snapshot(
            actor,
            state,
            active=active,
            observations=observations,
            total_equity=total_equity,
            total_market_value=total_market,
            priced_count=len(available),
        )
        holdings = tuple(
            self._holding_view(
                holding,
                observations.get(holding.holding_id),
                total_equity=total_equity,
            )
            for holding in sorted(state.holdings.values(), key=lambda item: (item.state, item.symbol))
        )
        return PortfolioView(
            workspace_id=state.workspace_id,
            portfolio_revision=state.revision,
            currency="USD",
            usd_cash=_money(state.usd_cash),
            holdings=holdings,
            total_market_value=total_market_view,
            total_equity=total_equity_view,
            active_holding_count=len(active),
            priced_holding_count=len(available),
            issues=issues,
        )

    def _persist_available_prices(
        self,
        actor: PersonalActor,
        active: Sequence[HoldingState],
        observations: Mapping[str, PortfolioPriceObservation],
    ) -> None:
        self._prices.upsert(
            actor_id=actor.actor_id,
            observations={
                holding.symbol: observations[holding.holding_id]
                for holding in active
                if observations[holding.holding_id].availability == "available"
            },
        )

    def _apply_cached_fallback(
        self,
        actor: PersonalActor,
        active: Sequence[HoldingState],
        observations: dict[str, PortfolioPriceObservation],
    ) -> None:
        missing = [
            holding
            for holding in active
            if observations[holding.holding_id].availability != "available"
        ]
        if not missing or self._cached_price_max_age_days == 0:
            return
        cached = self._prices.latest(
            actor_id=actor.actor_id, symbols=[holding.symbol for holding in missing]
        )
        now = self._clock()
        for holding in missing:
            cached_observation = cached.get(holding.symbol)
            if cached_observation is None or cached_observation.price is None:
                continue
            quote_time = cached_observation.as_of or now
            if quote_time.tzinfo is None:
                quote_time = quote_time.replace(tzinfo=timezone.utc)
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            if (now - quote_time) > timedelta(days=self._cached_price_max_age_days):
                continue
            observations[holding.holding_id] = cached_observation

    def _write_equity_snapshot(
        self,
        actor: PersonalActor,
        state: PortfolioState,
        *,
        active: Sequence[HoldingState],
        observations: Mapping[str, PortfolioPriceObservation],
        total_equity: Decimal | None,
        total_market_value: Decimal | None,
        priced_count: int,
    ) -> None:
        if not active or total_equity is None or total_market_value is None:
            return
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        et_now = now.astimezone(_US_MARKET_TZ)
        if et_now.weekday() >= 5:
            return
        payload = {
            "holdings": [
                {
                    "symbol": holding.symbol,
                    "quantity": str(holding.quantity),
                    "average_cost": str(holding.average_cost),
                }
                for holding in active
            ],
            "prices": {
                holding.symbol: _price_payload(observations.get(holding.holding_id))
                for holding in active
            },
        }
        self._snapshots.upsert(
            actor_id=actor.actor_id,
            snapshot=EquitySnapshot(
                market_day=et_now.date(),
                total_equity=total_equity,
                total_market_value=total_market_value,
                usd_cash=state.usd_cash,
                holdings_count=len(active),
                priced_count=priced_count,
                after_close=et_now.time() >= _ET_CLOSE,
                observed_at=now,
                payload=payload,
            ),
        )

    def _holding_view(
        self,
        holding: HoldingState,
        observation: PortfolioPriceObservation | None,
        *,
        total_equity: Decimal | None,
    ) -> HoldingView:
        cost_amount = holding.quantity * holding.average_cost
        if holding.state != "active":
            not_applicable = _observed_not_applicable("holding_removed")
            return HoldingView(
                holding_id=holding.holding_id,
                symbol=holding.symbol,
                name=holding.name,
                state=holding.state,
                revision=holding.revision,
                quantity=_money(holding.quantity),
                average_cost=_money(holding.average_cost),
                cost_amount=_money(cost_amount),
                currency="USD",
                verification_status=holding.verification_status,
                market_price=not_applicable,
                market_value=not_applicable,
                unrealized_profit_loss=not_applicable,
                unrealized_return=not_applicable,
                weight=not_applicable,
            )
        if observation is None or observation.availability != "available" or observation.price is None:
            observation = observation or PortfolioPriceObservation.unavailable("provider_unavailable")
            unavailable = _observation_unavailable(observation)
            return HoldingView(
                holding_id=holding.holding_id,
                symbol=holding.symbol,
                name=holding.name,
                state=holding.state,
                revision=holding.revision,
                quantity=_money(holding.quantity),
                average_cost=_money(holding.average_cost),
                cost_amount=_money(cost_amount),
                currency="USD",
                verification_status=holding.verification_status,
                market_price=unavailable,
                market_value=unavailable,
                unrealized_profit_loss=unavailable,
                unrealized_return=unavailable,
                weight=unavailable,
            )
        market_value = holding.quantity * observation.price
        profit_loss = market_value - cost_amount
        return HoldingView(
            holding_id=holding.holding_id,
            symbol=holding.symbol,
            name=holding.name,
            state=holding.state,
            revision=holding.revision,
            quantity=_money(holding.quantity),
            average_cost=_money(holding.average_cost),
            cost_amount=_money(cost_amount),
            currency="USD",
            verification_status=holding.verification_status,
            market_price=_from_price(observation, observation.price, ratio=False),
            market_value=_from_price(observation, market_value, ratio=False),
            unrealized_profit_loss=_from_price(observation, profit_loss, ratio=False),
            unrealized_return=_from_price(observation, profit_loss / cost_amount, ratio=True),
            weight=(
                _from_price(observation, market_value / total_equity, ratio=True)
                if total_equity is not None and total_equity != 0
                else _observed_unavailable("portfolio_total_unavailable")
            ),
        )

    def _verify_challenge(self, actor: PersonalActor, command: PurgeHoldingCommand) -> None:
        try:
            encoded, signature = command.challenge.split(".", 1)
            expected = _b64url(
                hmac.new(self._challenge_key, encoded.encode("ascii"), sha256).digest()
            )
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            payload = json.loads(_b64url_decode(encoded))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("purge_challenge_invalid") from exc
        expected_actor = sha256(actor.actor_id.encode("utf-8")).hexdigest()
        if (
            payload.get("actor") != expected_actor
            or payload.get("holding_id") != command.holding_id
            or payload.get("portfolio_revision") != command.expected_portfolio_revision
        ):
            raise ValueError("purge_challenge_invalid")
        if self._clock().timestamp() > int(payload.get("expires_at", 0)):
            raise ValueError("purge_challenge_expired")


def _normalize_symbol(value: str) -> str:
    normalized = value.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", normalized):
        raise ValueError("unsupported_instrument")
    return normalized


def _normalize_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("invalid_command")
    return normalized


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.0001")), "f")


def _price_payload(
    observation: PortfolioPriceObservation | None,
) -> dict[str, Any] | None:
    if observation is None or observation.price is None:
        return None
    return {
        "price": str(observation.price),
        "feed": observation.feed,
        "as_of": observation.as_of.isoformat() if observation.as_of else None,
        "delay_seconds": observation.delay_seconds,
        "source_health": observation.source_health,
        "cached": observation.cached,
    }


def _payload_datetime(value: Any) -> datetime | None:
    if value is None or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _ratio(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000001")), "f")


def _from_price(
    observation: PortfolioPriceObservation,
    value: Decimal,
    *,
    ratio: bool,
) -> ObservedDecimalView:
    return ObservedDecimalView(
        availability="available",
        value=_ratio(value) if ratio else _money(value),
        reason_code=observation.reason_code,
        source_health=observation.source_health,
        as_of=observation.as_of,
        source_ids=observation.source_ids,
        feed=observation.feed,
        delay_seconds=observation.delay_seconds,
        cached=observation.cached,
    )


def _observation_unavailable(observation: PortfolioPriceObservation) -> ObservedDecimalView:
    return ObservedDecimalView(
        availability=observation.availability,
        value=None,
        reason_code=observation.reason_code,
        source_health=observation.source_health,
        as_of=observation.as_of,
        source_ids=observation.source_ids,
        feed=observation.feed,
        delay_seconds=observation.delay_seconds,
        cached=observation.cached,
    )


def _observed_unavailable(
    reason_code: str, *, source_health: SourceHealth = "unavailable"
) -> ObservedDecimalView:
    return ObservedDecimalView(
        availability="not_available",
        value=None,
        reason_code=reason_code,
        source_health=source_health,
        as_of=None,
        source_ids=(),
    )


def _observed_not_applicable(reason_code: str) -> ObservedDecimalView:
    return ObservedDecimalView(
        availability="not_applicable",
        value=None,
        reason_code=reason_code,
        source_health="unavailable",
        as_of=None,
        source_ids=(),
    )


def _observed_available(
    value: Decimal,
    observations,
    *,
    reason_code: str | None = None,
    source_health: SourceHealth | None = None,
) -> ObservedDecimalView:
    items = tuple(observations)
    if not items:
        return ObservedDecimalView(
            availability="available",
            value=_money(value),
            reason_code=reason_code,
            source_health=source_health or "fresh",
            as_of=None,
            source_ids=(),
        )
    return ObservedDecimalView(
        availability="available",
        value=_money(value),
        reason_code=reason_code,
        source_health=source_health or _worst_health(items),
        as_of=min(item.as_of for item in items if item.as_of is not None),
        source_ids=tuple(source_id for item in items for source_id in item.source_ids),
    )


def _worst_health(observations) -> SourceHealth:
    ranks = {"fresh": 0, "stale": 1, "degraded": 2, "unavailable": 3}
    items = tuple(observations)
    return max((item.source_health for item in items), key=ranks.get, default="fresh")


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding).decode("utf-8")


def _portfolio_identity_hash(actor_id: str) -> str:
    return sha256(actor_id.encode("utf-8")).hexdigest()


def _portfolio_idempotency_hash(actor_id: str, idempotency_key: str) -> str:
    return sha256(f"{actor_id}|{idempotency_key}".encode("utf-8")).hexdigest()


def _portfolio_aad(table: str, row_id: str) -> str:
    return f"private_workbench|{table}|{row_id}|payload|1"


def _portfolio_envelope_values(envelope: EncryptedEnvelope) -> dict:
    return {
        "ciphertext": envelope.ciphertext,
        "nonce": envelope.nonce,
        "key_id": envelope.key_id,
        "payload_schema": envelope.payload_schema,
    }


def _portfolio_row_envelope(row) -> EncryptedEnvelope:
    return EncryptedEnvelope(
        ciphertext=bytes(row.ciphertext),
        nonce=bytes(row.nonce),
        key_id=row.key_id,
        payload_schema=row.payload_schema,
    )


def _portfolio_state_payload(state: PortfolioState) -> dict:
    return {
        "portfolio_revision": state.revision,
        "usd_cash": str(state.usd_cash),
        "holdings": [
            {
                "holding_id": holding.holding_id,
                "symbol": holding.symbol,
                "name": holding.name,
                "quantity": str(holding.quantity),
                "average_cost": str(holding.average_cost),
                "state": holding.state,
                "revision": holding.revision,
                "verification_status": holding.verification_status,
            }
            for holding in sorted(state.holdings.values(), key=lambda item: item.holding_id)
        ],
    }


def _changed_holding_id(before: dict, state: PortfolioState) -> str | None:
    before_by_id = {item["holding_id"]: item for item in before["holdings"]}
    after_by_id = {
        item["holding_id"]: item for item in _portfolio_state_payload(state)["holdings"]
    }
    changed = [
        holding_id
        for holding_id in before_by_id.keys() | after_by_id.keys()
        if before_by_id.get(holding_id) != after_by_id.get(holding_id)
    ]
    return changed[0] if len(changed) == 1 else None


def _revision_change_payload(
    before: dict,
    after: dict,
    *,
    changed_holding_id: str | None,
) -> dict:
    if changed_holding_id is None:
        return {
            "before": {
                "portfolio_revision": before["portfolio_revision"],
                "usd_cash": before["usd_cash"],
            },
            "after": {
                "portfolio_revision": after["portfolio_revision"],
                "usd_cash": after["usd_cash"],
            },
        }
    before_by_id = {item["holding_id"]: item for item in before["holdings"]}
    after_by_id = {item["holding_id"]: item for item in after["holdings"]}
    return {
        "before": {
            "portfolio_revision": before["portfolio_revision"],
            "holding": before_by_id.get(changed_holding_id),
        },
        "after": {
            "portfolio_revision": after["portfolio_revision"],
            "holding": after_by_id.get(changed_holding_id),
        },
    }


def _audit_receipt(row: PersonalAuditEvent) -> DeletionReceipt:
    return DeletionReceipt(
        holding_id=row.object_id,
        status="purged",
        portfolio_revision=row.portfolio_revision,
        purged_at=row.occurred_at,
        backup_status="expires_within_window",
        backup_expires_at=row.backup_expires_at,
    )
