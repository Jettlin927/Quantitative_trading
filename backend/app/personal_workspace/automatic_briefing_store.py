"""自动简报的幂等领取、调用状态与每日预算账本。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import logging
from threading import Lock
from typing import Any, Callable, Literal, Mapping, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.models import (
    PersonalAutomaticBriefing,
    PersonalAutomaticBriefingDailyBudget,
    PersonalWorkspace,
)

from .crypto import EncryptedEnvelope, PersonalDataCipher


LOGGER = logging.getLogger(__name__)


class BriefingMode(str, Enum):
    AUTOMATIC = "automatic"
    ACTIVE = "active"


class BriefingProviderState(str, Enum):
    PLANNED = "planned"
    STARTED = "started"
    COMPLETED = "completed"
    OUTCOME_UNKNOWN = "outcome_unknown"


@dataclass(frozen=True)
class DailyBudgetPolicy:
    revision: str
    fx_cny_per_usd: Decimal
    target_cny: Decimal
    soft_limit_cny: Decimal
    hard_limit_cny: Decimal


@dataclass(frozen=True)
class BriefingClaim:
    briefing_id: str
    actor_id: str
    trigger_key: str
    market_date: date
    trigger_kind: str
    provider_state: BriefingProviderState
    created: bool
    acquired: bool
    lease_token: str | None


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: Literal["reserved", "soft_limit", "hard_limit"]
    reservation_id: str | None
    projected_daily_cny: Decimal
    settled_daily_cny: Decimal
    reserved_daily_cny: Decimal


@dataclass(frozen=True)
class BriefingCost:
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0


@dataclass(frozen=True)
class StoredAutomaticBriefing:
    briefing_id: str
    actor_id: str
    trigger_key: str
    market_date: date
    trigger_kind: str
    provider_state: BriefingProviderState
    failure_code: str | None
    mode: BriefingMode | None
    reservation_id: str | None
    estimated_cost_usd: Decimal | None
    actual_cost_usd: Decimal | None
    actual_cost_cny: Decimal | None
    accounted_cost_usd: Decimal
    accounted_cost_cny: Decimal
    daily_cumulative_cny: Decimal
    policy: DailyBudgetPolicy | None
    private_payload: Mapping[str, Any] | None


@dataclass(frozen=True)
class ActiveAnalysisBudgetReservation:
    briefing_id: str
    reservation_id: str


class ActiveAnalysisBudgetGuard:
    """让主动分析与自动简报共享同一日硬预算账本。"""

    def __init__(
        self,
        *,
        store: "AutomaticBriefingStore",
        policy: DailyBudgetPolicy,
        lease_seconds: int = 120,
    ) -> None:
        self._store = store
        self._policy = policy
        self._lease_seconds = lease_seconds

    def start_call(
        self,
        *,
        actor_id: str,
        run_id: str,
        worker_id: str,
        estimated_cost_usd: Decimal,
        now: datetime,
    ) -> ActiveAnalysisBudgetReservation:
        market_date = now.astimezone(ZoneInfo("America/New_York")).date()
        claim = self._store.claim(
            actor_id=actor_id,
            trigger_key=f"active-analysis:{run_id}",
            market_date=market_date,
            trigger_kind="active_analysis",
            lease_owner=worker_id,
            lease_expires_at=now + timedelta(seconds=self._lease_seconds),
            now=now,
        )
        if not claim.acquired:
            raise ValueError("active_analysis_call_not_available")
        decision = self._store.reserve_budget(
            claim=claim,
            mode=BriefingMode.ACTIVE,
            estimated_cost_usd=estimated_cost_usd,
            policy=self._policy,
            now=now,
        )
        if not decision.allowed or decision.reservation_id is None:
            self._store.fail_before_provider(
                claim=claim,
                private_payload={"analysis_run_id": run_id},
                failure_code=decision.reason,
                now=now,
            )
            raise ValueError(decision.reason)
        self._store.mark_provider_started(
            claim=claim,
            reservation_id=decision.reservation_id,
            provider_deadline=now + timedelta(seconds=self._lease_seconds),
            now=now,
        )
        return ActiveAnalysisBudgetReservation(
            briefing_id=claim.briefing_id,
            reservation_id=decision.reservation_id,
        )

    def complete_call(
        self,
        reservation: ActiveAnalysisBudgetReservation,
        *,
        run_id: str,
        cost: BriefingCost,
        now: datetime,
        failure_code: str | None = None,
    ) -> None:
        self._store.complete(
            briefing_id=reservation.briefing_id,
            reservation_id=reservation.reservation_id,
            cost=cost,
            private_payload={"analysis_run_id": run_id},
            failure_code=failure_code,
            now=now,
        )

    def heartbeat(
        self,
        reservation: ActiveAnalysisBudgetReservation,
        *,
        now: datetime,
    ) -> None:
        self._store.renew_provider_deadline(
            briefing_id=reservation.briefing_id,
            reservation_id=reservation.reservation_id,
            provider_deadline=now + timedelta(seconds=self._lease_seconds),
            now=now,
        )

    def mark_outcome_unknown(
        self,
        reservation: ActiveAnalysisBudgetReservation,
        *,
        run_id: str,
        failure_code: str,
        now: datetime,
    ) -> None:
        self._store.mark_outcome_unknown(
            briefing_id=reservation.briefing_id,
            reservation_id=reservation.reservation_id,
            private_payload={
                "analysis_run_id": run_id,
                "failure_code": failure_code,
            },
            now=now,
        )


class AutomaticBriefingStore(Protocol):
    def reconcile_expired_started(self, *, actor_id: str, now: datetime) -> int: ...

    def claim(
        self,
        *,
        actor_id: str,
        trigger_key: str,
        market_date: date,
        trigger_kind: str,
        lease_owner: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> BriefingClaim: ...

    def reserve_budget(
        self,
        *,
        claim: BriefingClaim,
        mode: BriefingMode,
        estimated_cost_usd: Decimal,
        policy: DailyBudgetPolicy,
        now: datetime,
    ) -> BudgetDecision: ...

    def mark_provider_started(
        self,
        *,
        claim: BriefingClaim,
        reservation_id: str | None,
        provider_deadline: datetime,
        now: datetime,
    ) -> StoredAutomaticBriefing: ...

    def renew_provider_deadline(
        self,
        *,
        briefing_id: str,
        reservation_id: str | None,
        provider_deadline: datetime,
        now: datetime,
    ) -> None: ...

    def complete(
        self,
        *,
        briefing_id: str,
        reservation_id: str | None,
        cost: BriefingCost,
        private_payload: Mapping[str, Any],
        now: datetime,
        failure_code: str | None = None,
    ) -> StoredAutomaticBriefing: ...

    def mark_outcome_unknown(
        self,
        *,
        briefing_id: str,
        reservation_id: str | None,
        now: datetime,
        private_payload: Mapping[str, Any] | None = None,
    ) -> StoredAutomaticBriefing: ...

    def fail_before_provider(
        self,
        *,
        claim: BriefingClaim,
        private_payload: Mapping[str, Any],
        failure_code: str,
        now: datetime,
    ) -> StoredAutomaticBriefing: ...

    def get(
        self, *, actor_id: str, trigger_key: str
    ) -> StoredAutomaticBriefing | None: ...


@dataclass
class _MemoryBriefing:
    actor_id: str
    trigger_key: str
    market_date: date
    trigger_kind: str
    briefing_id: str
    provider_state: BriefingProviderState
    lease_owner: str | None
    lease_token: str | None
    lease_expires_at: datetime | None
    failure_code: str | None = None
    mode: BriefingMode | None = None
    reservation_id: str | None = None
    estimated_cost_usd: Decimal | None = None
    actual_cost_usd: Decimal | None = None
    actual_cost_cny: Decimal | None = None
    accounted_cost_usd: Decimal = Decimal("0")
    accounted_cost_cny: Decimal = Decimal("0")
    policy: DailyBudgetPolicy | None = None
    private_payload: Mapping[str, Any] | None = None


@dataclass
class _MemoryBudget:
    reserved_usd: Decimal = Decimal("0")
    reserved_cny: Decimal = Decimal("0")
    settled_usd: Decimal = Decimal("0")
    settled_cny: Decimal = Decimal("0")
    target_notified: bool = False


class InMemoryAutomaticBriefingStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._items: dict[tuple[str, str], _MemoryBriefing] = {}
        self._ids: dict[str, _MemoryBriefing] = {}
        self._budgets: dict[tuple[str, date], _MemoryBudget] = {}

    def claim(
        self,
        *,
        actor_id: str,
        trigger_key: str,
        market_date: date,
        trigger_kind: str,
        lease_owner: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> BriefingClaim:
        with self._lock:
            key = (actor_id, trigger_key)
            item = self._items.get(key)
            created = item is None
            acquired = False
            if item is None:
                item = _MemoryBriefing(
                    actor_id=actor_id,
                    trigger_key=trigger_key,
                    market_date=market_date,
                    trigger_kind=trigger_kind,
                    briefing_id=str(uuid4()),
                    provider_state=BriefingProviderState.PLANNED,
                    lease_owner=lease_owner,
                    lease_token=str(uuid4()),
                    lease_expires_at=lease_expires_at,
                )
                self._items[key] = item
                self._ids[item.briefing_id] = item
                acquired = True
            elif (
                item.provider_state == BriefingProviderState.PLANNED
                and item.lease_expires_at is not None
                and item.lease_expires_at <= now
            ):
                item.lease_owner = lease_owner
                item.lease_token = str(uuid4())
                item.lease_expires_at = lease_expires_at
                acquired = True
            elif (
                item.provider_state == BriefingProviderState.STARTED
                and item.lease_expires_at is not None
                and item.lease_expires_at <= now
            ):
                budget = self._budgets[(item.actor_id, item.market_date)]
                reserved_usd, reserved_cny = self._release_reservation(item, budget)
                budget.settled_usd += reserved_usd
                budget.settled_cny += reserved_cny
                item.accounted_cost_usd = reserved_usd
                item.accounted_cost_cny = reserved_cny
                item.provider_state = BriefingProviderState.OUTCOME_UNKNOWN
            return _claim_view(item, created=created, acquired=acquired)

    def reconcile_expired_started(self, *, actor_id: str, now: datetime) -> int:
        with self._lock:
            recovered = 0
            for item in self._items.values():
                if (
                    item.actor_id != actor_id
                    or item.provider_state != BriefingProviderState.STARTED
                    or item.lease_expires_at is None
                    or item.lease_expires_at > now
                ):
                    continue
                budget = self._budgets[(item.actor_id, item.market_date)]
                reserved_usd, reserved_cny = self._release_reservation(item, budget)
                budget.settled_usd += reserved_usd
                budget.settled_cny += reserved_cny
                item.accounted_cost_usd = reserved_usd
                item.accounted_cost_cny = reserved_cny
                item.provider_state = BriefingProviderState.OUTCOME_UNKNOWN
                recovered += 1
            return recovered

    def reserve_budget(
        self,
        *,
        claim: BriefingClaim,
        mode: BriefingMode,
        estimated_cost_usd: Decimal,
        policy: DailyBudgetPolicy,
        now: datetime,
    ) -> BudgetDecision:
        del now
        with self._lock:
            item = self._owned_claim(claim)
            budget = self._budgets.setdefault((item.actor_id, item.market_date), _MemoryBudget())
            if item.reservation_id is not None:
                return _budget_decision(budget, item.reservation_id, True, "reserved")
            estimate_usd = _cost(estimated_cost_usd)
            estimate_cny = _cost(estimate_usd * policy.fx_cny_per_usd)
            projected = budget.settled_cny + budget.reserved_cny + estimate_cny
            reason = _budget_block_reason(mode, projected, policy)
            if reason is not None:
                return _budget_decision(budget, None, False, reason, projected=projected)
            if not budget.target_notified and projected > policy.target_cny:
                budget.target_notified = True
                LOGGER.warning("personal_ai_daily_target_exceeded")
            item.mode = mode
            item.reservation_id = str(uuid4())
            item.estimated_cost_usd = estimate_usd
            item.policy = policy
            budget.reserved_usd += estimate_usd
            budget.reserved_cny += estimate_cny
            return _budget_decision(budget, item.reservation_id, True, "reserved")

    def mark_provider_started(
        self,
        *,
        claim: BriefingClaim,
        reservation_id: str | None,
        provider_deadline: datetime,
        now: datetime,
    ) -> StoredAutomaticBriefing:
        with self._lock:
            item = self._owned_claim(claim)
            _require_reservation(item.reservation_id, reservation_id)
            item.provider_state = BriefingProviderState.STARTED
            item.lease_expires_at = provider_deadline
            return self._stored(item)

    def renew_provider_deadline(
        self,
        *,
        briefing_id: str,
        reservation_id: str | None,
        provider_deadline: datetime,
        now: datetime,
    ) -> None:
        del now
        with self._lock:
            item = self._ids[briefing_id]
            _require_reservation(item.reservation_id, reservation_id)
            if item.provider_state != BriefingProviderState.STARTED:
                raise ValueError("provider_not_started")
            item.lease_expires_at = provider_deadline

    def complete(
        self,
        *,
        briefing_id: str,
        reservation_id: str | None,
        cost: BriefingCost,
        private_payload: Mapping[str, Any],
        now: datetime,
        failure_code: str | None = None,
    ) -> StoredAutomaticBriefing:
        del now
        with self._lock:
            item = self._ids[briefing_id]
            if item.provider_state == BriefingProviderState.COMPLETED:
                return self._stored(item)
            if item.provider_state != BriefingProviderState.STARTED:
                raise ValueError("provider_not_started")
            _require_reservation(item.reservation_id, reservation_id)
            budget = self._budgets[(item.actor_id, item.market_date)]
            self._release_reservation(item, budget)
            actual_usd = _cost(cost.cost_usd)
            actual_cny = _cost(actual_usd * item.policy.fx_cny_per_usd)
            budget.settled_usd += actual_usd
            budget.settled_cny += actual_cny
            item.actual_cost_usd = actual_usd
            item.actual_cost_cny = actual_cny
            item.accounted_cost_usd = actual_usd
            item.accounted_cost_cny = actual_cny
            item.failure_code = failure_code
            item.private_payload = _payload_with_usage(private_payload, cost)
            item.provider_state = BriefingProviderState.COMPLETED
            return self._stored(item)

    def mark_outcome_unknown(
        self,
        *,
        briefing_id: str,
        reservation_id: str | None,
        now: datetime,
        private_payload: Mapping[str, Any] | None = None,
    ) -> StoredAutomaticBriefing:
        del now
        with self._lock:
            item = self._ids[briefing_id]
            if item.provider_state == BriefingProviderState.OUTCOME_UNKNOWN:
                return self._stored(item)
            if item.provider_state != BriefingProviderState.STARTED:
                raise ValueError("provider_not_started")
            _require_reservation(item.reservation_id, reservation_id)
            budget = self._budgets[(item.actor_id, item.market_date)]
            reserved_usd, reserved_cny = self._release_reservation(item, budget)
            budget.settled_usd += reserved_usd
            budget.settled_cny += reserved_cny
            item.accounted_cost_usd = reserved_usd
            item.accounted_cost_cny = reserved_cny
            item.private_payload = deepcopy(private_payload)
            item.provider_state = BriefingProviderState.OUTCOME_UNKNOWN
            return self._stored(item)

    def fail_before_provider(
        self,
        *,
        claim: BriefingClaim,
        private_payload: Mapping[str, Any],
        failure_code: str,
        now: datetime,
    ) -> StoredAutomaticBriefing:
        del now
        with self._lock:
            item = self._owned_claim(claim)
            if item.reservation_id is not None:
                raise ValueError("budget_already_reserved")
            item.provider_state = BriefingProviderState.COMPLETED
            item.failure_code = failure_code
            item.private_payload = deepcopy(private_payload)
            return self._stored(item)

    def get(self, *, actor_id: str, trigger_key: str) -> StoredAutomaticBriefing | None:
        with self._lock:
            item = self._items.get((actor_id, trigger_key))
            return None if item is None else self._stored(item)

    def _owned_claim(self, claim: BriefingClaim) -> _MemoryBriefing:
        item = self._ids.get(claim.briefing_id)
        if (
            item is None
            or not claim.acquired
            or claim.lease_token is None
            or item.lease_token != claim.lease_token
            or item.provider_state != BriefingProviderState.PLANNED
        ):
            raise ValueError("claim_not_owned")
        return item

    def _release_reservation(
        self, item: _MemoryBriefing, budget: _MemoryBudget
    ) -> tuple[Decimal, Decimal]:
        reserved_usd = item.estimated_cost_usd or Decimal("0")
        reserved_cny = _cost(reserved_usd * item.policy.fx_cny_per_usd)
        budget.reserved_usd -= reserved_usd
        budget.reserved_cny -= reserved_cny
        return reserved_usd, reserved_cny

    def _stored(self, item: _MemoryBriefing) -> StoredAutomaticBriefing:
        budget = self._budgets.get((item.actor_id, item.market_date), _MemoryBudget())
        return _stored_view(item, budget.settled_cny)


class PostgresAutomaticBriefingStore:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        cipher: PersonalDataCipher,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    def claim(
        self,
        *,
        actor_id: str,
        trigger_key: str,
        market_date: date,
        trigger_kind: str,
        lease_owner: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> BriefingClaim:
        with self._session_factory() as session, session.begin():
            workspace = self._workspace_or_create(session, actor_id)
            key_hash = _identity_hash(trigger_key)
            row = session.scalar(
                select(PersonalAutomaticBriefing)
                .where(
                    PersonalAutomaticBriefing.workspace_id == workspace.id,
                    PersonalAutomaticBriefing.trigger_key_hash == key_hash,
                )
                .with_for_update()
            )
            created = row is None
            acquired = False
            if row is None:
                row_id = str(uuid4())
                token = str(uuid4())
                envelope = self._cipher.encrypt_json(
                    {
                        "actor_id": actor_id,
                        "trigger_key": trigger_key,
                        "private_payload": None,
                    },
                    aad=_aad("personal_automatic_briefings", row_id),
                )
                row = PersonalAutomaticBriefing(
                    id=row_id,
                    workspace_id=workspace.id,
                    trigger_key_hash=key_hash,
                    market_date=market_date,
                    trigger_kind=trigger_kind,
                    provider_state=BriefingProviderState.PLANNED.value,
                    lease_owner=lease_owner,
                    lease_token=token,
                    lease_expires_at=lease_expires_at,
                    **_envelope_values(envelope),
                )
                session.add(row)
                session.flush()
                acquired = True
            elif (
                row.provider_state == BriefingProviderState.PLANNED.value
                and row.lease_expires_at is not None
                and row.lease_expires_at <= now
            ):
                row.lease_owner = lease_owner
                row.lease_token = str(uuid4())
                row.lease_expires_at = lease_expires_at
                row.updated_at = now
                acquired = True
            elif (
                row.provider_state == BriefingProviderState.STARTED.value
                and row.lease_expires_at is not None
                and row.lease_expires_at <= now
            ):
                budget = self._budget_row(
                    session, workspace.id, row.market_date, now
                )
                reserved_usd, reserved_cny = self._release_row_reservation(
                    row, budget
                )
                budget.settled_cost_usd = (
                    Decimal(budget.settled_cost_usd) + reserved_usd
                )
                budget.settled_cost_cny = (
                    Decimal(budget.settled_cost_cny) + reserved_cny
                )
                budget.updated_at = now
                row.accounted_cost_usd = reserved_usd
                row.accounted_cost_cny = reserved_cny
                row.provider_state = BriefingProviderState.OUTCOME_UNKNOWN.value
                row.updated_at = now
            return _row_claim(
                row,
                actor_id=actor_id,
                trigger_key=trigger_key,
                created=created,
                acquired=acquired,
            )

    def reconcile_expired_started(self, *, actor_id: str, now: datetime) -> int:
        with self._session_factory() as session, session.begin():
            workspace = self._workspace(session, actor_id, lock=True)
            if workspace is None:
                return 0
            rows = session.scalars(
                select(PersonalAutomaticBriefing)
                .where(
                    PersonalAutomaticBriefing.workspace_id == workspace.id,
                    PersonalAutomaticBriefing.provider_state
                    == BriefingProviderState.STARTED.value,
                    PersonalAutomaticBriefing.lease_expires_at <= now,
                )
                .with_for_update(skip_locked=True)
            ).all()
            for row in rows:
                budget = self._budget_row(
                    session, workspace.id, row.market_date, now
                )
                reserved_usd, reserved_cny = self._release_row_reservation(
                    row, budget
                )
                budget.settled_cost_usd = (
                    Decimal(budget.settled_cost_usd) + reserved_usd
                )
                budget.settled_cost_cny = (
                    Decimal(budget.settled_cost_cny) + reserved_cny
                )
                budget.updated_at = now
                row.accounted_cost_usd = reserved_usd
                row.accounted_cost_cny = reserved_cny
                row.provider_state = BriefingProviderState.OUTCOME_UNKNOWN.value
                row.updated_at = now
            session.flush()
            return len(rows)

    def reserve_budget(
        self,
        *,
        claim: BriefingClaim,
        mode: BriefingMode,
        estimated_cost_usd: Decimal,
        policy: DailyBudgetPolicy,
        now: datetime,
    ) -> BudgetDecision:
        with self._session_factory() as session, session.begin():
            row, workspace = self._owned_row(session, claim)
            budget = self._budget_row(session, workspace.id, row.market_date, now)
            if row.reservation_id is not None:
                return _row_budget_decision(budget, row.reservation_id, True, "reserved")
            estimate_usd = _cost(estimated_cost_usd)
            estimate_cny = _cost(estimate_usd * policy.fx_cny_per_usd)
            projected = (
                Decimal(budget.settled_cost_cny)
                + Decimal(budget.reserved_cost_cny)
                + estimate_cny
            )
            reason = _budget_block_reason(mode, projected, policy)
            if reason is not None:
                return _row_budget_decision(
                    budget, None, False, reason, projected=projected
                )
            if not budget.target_notified and projected > policy.target_cny:
                budget.target_notified = True
                LOGGER.warning("personal_ai_daily_target_exceeded")
            row.mode = mode.value
            row.reservation_id = str(uuid4())
            row.estimated_cost_usd = estimate_usd
            row.estimated_cost_cny = estimate_cny
            row.fx_cny_per_usd = policy.fx_cny_per_usd
            row.policy_revision = policy.revision
            row.target_cny = policy.target_cny
            row.soft_limit_cny = policy.soft_limit_cny
            row.hard_limit_cny = policy.hard_limit_cny
            row.updated_at = now
            budget.reserved_cost_usd = Decimal(budget.reserved_cost_usd) + estimate_usd
            budget.reserved_cost_cny = Decimal(budget.reserved_cost_cny) + estimate_cny
            budget.updated_at = now
            session.flush()
            return _row_budget_decision(budget, row.reservation_id, True, "reserved")

    def mark_provider_started(
        self,
        *,
        claim: BriefingClaim,
        reservation_id: str | None,
        provider_deadline: datetime,
        now: datetime,
    ) -> StoredAutomaticBriefing:
        with self._session_factory() as session, session.begin():
            row, _ = self._owned_row(session, claim)
            _require_reservation(row.reservation_id, reservation_id)
            row.provider_state = BriefingProviderState.STARTED.value
            row.lease_expires_at = provider_deadline
            row.updated_at = now
            session.flush()
            return self._decode_stored(session, row, claim.actor_id)

    def renew_provider_deadline(
        self,
        *,
        briefing_id: str,
        reservation_id: str | None,
        provider_deadline: datetime,
        now: datetime,
    ) -> None:
        with self._session_factory() as session, session.begin():
            row = self._locked_row(session, briefing_id)
            _require_reservation(row.reservation_id, reservation_id)
            if row.provider_state != BriefingProviderState.STARTED.value:
                raise ValueError("provider_not_started")
            row.lease_expires_at = provider_deadline
            row.updated_at = now

    def complete(
        self,
        *,
        briefing_id: str,
        reservation_id: str | None,
        cost: BriefingCost,
        private_payload: Mapping[str, Any],
        now: datetime,
        failure_code: str | None = None,
    ) -> StoredAutomaticBriefing:
        with self._session_factory() as session, session.begin():
            row = self._locked_row(session, briefing_id)
            actor_id = self._row_actor_id(row)
            if row.provider_state == BriefingProviderState.COMPLETED.value:
                return self._decode_stored(session, row, actor_id)
            if row.provider_state != BriefingProviderState.STARTED.value:
                raise ValueError("provider_not_started")
            _require_reservation(row.reservation_id, reservation_id)
            budget = self._budget_row(session, row.workspace_id, row.market_date, now)
            self._release_row_reservation(row, budget)
            actual_usd = _cost(cost.cost_usd)
            actual_cny = _cost(actual_usd * Decimal(row.fx_cny_per_usd))
            budget.settled_cost_usd = Decimal(budget.settled_cost_usd) + actual_usd
            budget.settled_cost_cny = Decimal(budget.settled_cost_cny) + actual_cny
            budget.updated_at = now
            row.actual_cost_usd = actual_usd
            row.actual_cost_cny = actual_cny
            row.accounted_cost_usd = actual_usd
            row.accounted_cost_cny = actual_cny
            row.failure_code = failure_code
            row.provider_state = BriefingProviderState.COMPLETED.value
            row.updated_at = now
            self._save_payload(row, _payload_with_usage(private_payload, cost))
            session.flush()
            return self._decode_stored(session, row, actor_id)

    def mark_outcome_unknown(
        self,
        *,
        briefing_id: str,
        reservation_id: str | None,
        now: datetime,
        private_payload: Mapping[str, Any] | None = None,
    ) -> StoredAutomaticBriefing:
        with self._session_factory() as session, session.begin():
            row = self._locked_row(session, briefing_id)
            actor_id = self._row_actor_id(row)
            if row.provider_state == BriefingProviderState.OUTCOME_UNKNOWN.value:
                return self._decode_stored(session, row, actor_id)
            if row.provider_state != BriefingProviderState.STARTED.value:
                raise ValueError("provider_not_started")
            _require_reservation(row.reservation_id, reservation_id)
            budget = self._budget_row(session, row.workspace_id, row.market_date, now)
            reserved_usd, reserved_cny = self._release_row_reservation(row, budget)
            budget.settled_cost_usd = Decimal(budget.settled_cost_usd) + reserved_usd
            budget.settled_cost_cny = Decimal(budget.settled_cost_cny) + reserved_cny
            budget.updated_at = now
            row.accounted_cost_usd = reserved_usd
            row.accounted_cost_cny = reserved_cny
            row.provider_state = BriefingProviderState.OUTCOME_UNKNOWN.value
            row.updated_at = now
            self._save_payload(row, private_payload)
            session.flush()
            return self._decode_stored(session, row, actor_id)

    def fail_before_provider(
        self,
        *,
        claim: BriefingClaim,
        private_payload: Mapping[str, Any],
        failure_code: str,
        now: datetime,
    ) -> StoredAutomaticBriefing:
        with self._session_factory() as session, session.begin():
            row, _ = self._owned_row(session, claim)
            if row.reservation_id is not None:
                raise ValueError("budget_already_reserved")
            row.provider_state = BriefingProviderState.COMPLETED.value
            row.failure_code = failure_code
            row.updated_at = now
            self._save_payload(row, private_payload)
            session.flush()
            return self._decode_stored(session, row, claim.actor_id)

    def get(self, *, actor_id: str, trigger_key: str) -> StoredAutomaticBriefing | None:
        with self._session_factory() as session:
            workspace = self._workspace(session, actor_id, lock=False)
            if workspace is None:
                return None
            row = session.scalar(
                select(PersonalAutomaticBriefing).where(
                    PersonalAutomaticBriefing.workspace_id == workspace.id,
                    PersonalAutomaticBriefing.trigger_key_hash == _identity_hash(trigger_key),
                )
            )
            return None if row is None else self._decode_stored(session, row, actor_id)

    def _owned_row(
        self, session: Session, claim: BriefingClaim
    ) -> tuple[PersonalAutomaticBriefing, PersonalWorkspace]:
        workspace = self._workspace(session, claim.actor_id, lock=True)
        if workspace is None:
            raise ValueError("claim_not_owned")
        row = session.scalar(
            select(PersonalAutomaticBriefing)
            .where(
                PersonalAutomaticBriefing.id == claim.briefing_id,
                PersonalAutomaticBriefing.workspace_id == workspace.id,
            )
            .with_for_update()
        )
        if (
            row is None
            or not claim.acquired
            or claim.lease_token is None
            or row.lease_token != claim.lease_token
            or row.provider_state != BriefingProviderState.PLANNED.value
        ):
            raise ValueError("claim_not_owned")
        return row, workspace

    def _workspace_or_create(self, session: Session, actor_id: str) -> PersonalWorkspace:
        session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:actor_hash, 0))"),
            {"actor_hash": _identity_hash(actor_id)},
        )
        workspace = self._workspace(session, actor_id, lock=True)
        if workspace is not None:
            return workspace
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

    def _workspace(
        self, session: Session, actor_id: str, *, lock: bool
    ) -> PersonalWorkspace | None:
        statement = select(PersonalWorkspace).where(
            PersonalWorkspace.actor_identity_hash == _identity_hash(actor_id)
        )
        if lock:
            statement = statement.with_for_update()
        return session.scalar(statement)

    def _budget_row(
        self, session: Session, workspace_id: str, market_date: date, now: datetime
    ) -> PersonalAutomaticBriefingDailyBudget:
        row = session.scalar(
            select(PersonalAutomaticBriefingDailyBudget)
            .where(
                PersonalAutomaticBriefingDailyBudget.workspace_id == workspace_id,
                PersonalAutomaticBriefingDailyBudget.market_date == market_date,
            )
            .with_for_update()
        )
        if row is None:
            row = PersonalAutomaticBriefingDailyBudget(
                id=str(uuid4()),
                workspace_id=workspace_id,
                market_date=market_date,
                reserved_cost_usd=Decimal("0"),
                reserved_cost_cny=Decimal("0"),
                settled_cost_usd=Decimal("0"),
                settled_cost_cny=Decimal("0"),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
        return row

    def _locked_row(self, session: Session, briefing_id: str) -> PersonalAutomaticBriefing:
        row = session.scalar(
            select(PersonalAutomaticBriefing)
            .where(PersonalAutomaticBriefing.id == briefing_id)
            .with_for_update()
        )
        if row is None:
            raise ValueError("private_object_not_found")
        return row

    def _release_row_reservation(
        self,
        row: PersonalAutomaticBriefing,
        budget: PersonalAutomaticBriefingDailyBudget,
    ) -> tuple[Decimal, Decimal]:
        reserved_usd = Decimal(row.estimated_cost_usd or 0)
        reserved_cny = Decimal(row.estimated_cost_cny or 0)
        budget.reserved_cost_usd = Decimal(budget.reserved_cost_usd) - reserved_usd
        budget.reserved_cost_cny = Decimal(budget.reserved_cost_cny) - reserved_cny
        return reserved_usd, reserved_cny

    def _save_payload(
        self, row: PersonalAutomaticBriefing, private_payload: Mapping[str, Any] | None
    ) -> None:
        current = self._cipher.decrypt_json(
            _row_envelope(row), aad=_aad("personal_automatic_briefings", row.id)
        )
        envelope = self._cipher.encrypt_json(
            {
                "actor_id": current["actor_id"],
                "trigger_key": current["trigger_key"],
                "private_payload": deepcopy(private_payload),
            },
            aad=_aad("personal_automatic_briefings", row.id),
        )
        _apply_envelope(row, envelope)

    def _decode_stored(
        self, session: Session, row: PersonalAutomaticBriefing, actor_id: str
    ) -> StoredAutomaticBriefing:
        payload = self._cipher.decrypt_json(
            _row_envelope(row), aad=_aad("personal_automatic_briefings", row.id)
        )
        budget = session.scalar(
            select(PersonalAutomaticBriefingDailyBudget).where(
                PersonalAutomaticBriefingDailyBudget.workspace_id == row.workspace_id,
                PersonalAutomaticBriefingDailyBudget.market_date == row.market_date,
            )
        )
        return _stored_from_row(
            row,
            actor_id=actor_id,
            trigger_key=str(payload["trigger_key"]),
            private_payload=payload.get("private_payload"),
            daily_cumulative_cny=Decimal(budget.settled_cost_cny) if budget else Decimal("0"),
        )

    def _row_actor_id(self, row: PersonalAutomaticBriefing) -> str:
        payload = self._cipher.decrypt_json(
            _row_envelope(row), aad=_aad("personal_automatic_briefings", row.id)
        )
        return str(payload["actor_id"])


def _claim_view(
    item: _MemoryBriefing, *, created: bool, acquired: bool
) -> BriefingClaim:
    return BriefingClaim(
        briefing_id=item.briefing_id,
        actor_id=item.actor_id,
        trigger_key=item.trigger_key,
        market_date=item.market_date,
        trigger_kind=item.trigger_kind,
        provider_state=item.provider_state,
        created=created,
        acquired=acquired,
        lease_token=item.lease_token if acquired else None,
    )


def _row_claim(
    row: PersonalAutomaticBriefing,
    *,
    actor_id: str,
    trigger_key: str,
    created: bool,
    acquired: bool,
) -> BriefingClaim:
    return BriefingClaim(
        briefing_id=row.id,
        actor_id=actor_id,
        trigger_key=trigger_key,
        market_date=row.market_date,
        trigger_kind=row.trigger_kind,
        provider_state=BriefingProviderState(row.provider_state),
        created=created,
        acquired=acquired,
        lease_token=row.lease_token if acquired else None,
    )


def _budget_block_reason(
    mode: BriefingMode, projected_cny: Decimal, policy: DailyBudgetPolicy
) -> Literal["soft_limit", "hard_limit"] | None:
    if projected_cny > policy.hard_limit_cny:
        return "hard_limit"
    if mode == BriefingMode.AUTOMATIC and projected_cny > policy.soft_limit_cny:
        return "soft_limit"
    return None


def _budget_decision(
    budget: _MemoryBudget,
    reservation_id: str | None,
    allowed: bool,
    reason: Literal["reserved", "soft_limit", "hard_limit"],
    *,
    projected: Decimal | None = None,
) -> BudgetDecision:
    return BudgetDecision(
        allowed=allowed,
        reason=reason,
        reservation_id=reservation_id,
        projected_daily_cny=projected or budget.settled_cny + budget.reserved_cny,
        settled_daily_cny=budget.settled_cny,
        reserved_daily_cny=budget.reserved_cny,
    )


def _row_budget_decision(
    budget: PersonalAutomaticBriefingDailyBudget,
    reservation_id: str | None,
    allowed: bool,
    reason: Literal["reserved", "soft_limit", "hard_limit"],
    *,
    projected: Decimal | None = None,
) -> BudgetDecision:
    settled = Decimal(budget.settled_cost_cny)
    reserved = Decimal(budget.reserved_cost_cny)
    return BudgetDecision(
        allowed=allowed,
        reason=reason,
        reservation_id=reservation_id,
        projected_daily_cny=projected or settled + reserved,
        settled_daily_cny=settled,
        reserved_daily_cny=reserved,
    )


def _stored_view(item: _MemoryBriefing, daily_cumulative_cny: Decimal) -> StoredAutomaticBriefing:
    return StoredAutomaticBriefing(
        briefing_id=item.briefing_id,
        actor_id=item.actor_id,
        trigger_key=item.trigger_key,
        market_date=item.market_date,
        trigger_kind=item.trigger_kind,
        provider_state=item.provider_state,
        failure_code=item.failure_code,
        mode=item.mode,
        reservation_id=item.reservation_id,
        estimated_cost_usd=item.estimated_cost_usd,
        actual_cost_usd=item.actual_cost_usd,
        actual_cost_cny=item.actual_cost_cny,
        accounted_cost_usd=item.accounted_cost_usd,
        accounted_cost_cny=item.accounted_cost_cny,
        daily_cumulative_cny=daily_cumulative_cny,
        policy=item.policy,
        private_payload=deepcopy(item.private_payload),
    )


def _stored_from_row(
    row: PersonalAutomaticBriefing,
    *,
    actor_id: str,
    trigger_key: str,
    private_payload: Mapping[str, Any] | None,
    daily_cumulative_cny: Decimal,
) -> StoredAutomaticBriefing:
    policy = None
    if row.policy_revision is not None:
        policy = DailyBudgetPolicy(
            revision=row.policy_revision,
            fx_cny_per_usd=Decimal(row.fx_cny_per_usd),
            target_cny=Decimal(row.target_cny),
            soft_limit_cny=Decimal(row.soft_limit_cny),
            hard_limit_cny=Decimal(row.hard_limit_cny),
        )
    return StoredAutomaticBriefing(
        briefing_id=row.id,
        actor_id=actor_id,
        trigger_key=trigger_key,
        market_date=row.market_date,
        trigger_kind=row.trigger_kind,
        provider_state=BriefingProviderState(row.provider_state),
        failure_code=row.failure_code,
        mode=BriefingMode(row.mode) if row.mode else None,
        reservation_id=row.reservation_id,
        estimated_cost_usd=(
            Decimal(row.estimated_cost_usd)
            if row.estimated_cost_usd is not None
            else None
        ),
        actual_cost_usd=Decimal(row.actual_cost_usd) if row.actual_cost_usd is not None else None,
        actual_cost_cny=Decimal(row.actual_cost_cny) if row.actual_cost_cny is not None else None,
        accounted_cost_usd=Decimal(row.accounted_cost_usd),
        accounted_cost_cny=Decimal(row.accounted_cost_cny),
        daily_cumulative_cny=daily_cumulative_cny,
        policy=policy,
        private_payload=private_payload,
    )


def _payload_with_usage(
    private_payload: Mapping[str, Any], cost: BriefingCost
) -> Mapping[str, Any]:
    payload = deepcopy(dict(private_payload))
    payload["usage"] = {
        "input_tokens": cost.input_tokens,
        "output_tokens": cost.output_tokens,
        "cache_hit_tokens": cost.cache_hit_tokens,
        "cache_miss_tokens": cost.cache_miss_tokens,
    }
    return payload


def _require_reservation(actual: str | None, expected: str | None) -> None:
    if actual is None or expected is None or actual != expected:
        raise ValueError("reservation_mismatch")


def _cost(value: Decimal) -> Decimal:
    value = Decimal(value)
    if value < 0:
        raise ValueError("negative_cost")
    return value


def _identity_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _aad(table: str, row_id: str) -> str:
    return f"private_workbench|{table}|{row_id}|payload|1"


def _envelope_values(envelope: EncryptedEnvelope) -> dict[str, Any]:
    return {
        "ciphertext": envelope.ciphertext,
        "nonce": envelope.nonce,
        "key_id": envelope.key_id,
        "payload_schema": envelope.payload_schema,
    }


def _row_envelope(row: PersonalAutomaticBriefing) -> EncryptedEnvelope:
    return EncryptedEnvelope(
        ciphertext=bytes(row.ciphertext),
        nonce=bytes(row.nonce),
        key_id=row.key_id,
        payload_schema=row.payload_schema,
    )


def _apply_envelope(row: PersonalAutomaticBriefing, envelope: EncryptedEnvelope) -> None:
    for key, value in _envelope_values(envelope).items():
        setattr(row, key, value)
