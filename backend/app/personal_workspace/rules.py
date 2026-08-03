from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import math
import re
from statistics import median, pstdev
from typing import Any, Callable, Literal, Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    PersonalRuleEvaluation,
    PersonalRuleEvaluationBatch,
    PersonalRuleInstance,
    PersonalRuleRevision,
    PersonalWorkspace,
)

from .contracts import (
    CreateObservationRuleCommand,
    PersonalActor,
    SetObservationRuleStateCommand,
)
from .instrument import InstrumentBar, InstrumentEvent, InstrumentObservationReader
from .crypto import EncryptedEnvelope, PersonalDataCipher


RuleState = Literal["draft", "enabled", "paused", "archived"]
RuleResult = Literal["hit", "not_hit", "insufficient_data", "calculation_failed"]


@dataclass(frozen=True)
class RuleTemplateView:
    template_id: str
    version: int
    name: str
    description: str
    unit: str
    frequency: str
    timezone: str
    warmup: str
    research_eligible: bool
    default_parameters: dict[str, Any]


@dataclass(frozen=True)
class RuleInstanceView:
    rule_id: str
    template_id: str
    template_version: int
    symbol: str
    state: RuleState
    revision: int
    parameters: dict[str, Any]
    latest_evaluation: "RuleEvaluationView | None" = None


@dataclass(frozen=True)
class RuleEvaluationView:
    evaluation_id: str
    batch_id: str
    rule_id: str
    rule_revision: int
    symbol: str
    result: RuleResult
    as_of: datetime
    source_health: str
    evidence_ids: tuple[str, ...]
    observed_value: str | None
    threshold: str | None
    reason_code: str
    fingerprint: str


@dataclass(frozen=True)
class RuleEvaluationBatchView:
    batch_id: str
    symbol: str
    as_of: datetime
    status: str
    fingerprint: str
    evaluations: tuple[RuleEvaluationView, ...]


@dataclass(frozen=True)
class RuleEvaluationRequest:
    symbol: str
    as_of: datetime


@dataclass(frozen=True)
class RuleInput:
    symbol: str
    raw_bars: tuple[InstrumentBar, ...]
    adjusted_bars: tuple[InstrumentBar, ...]
    events: tuple[InstrumentEvent, ...]
    source_health: str
    evidence_ids: tuple[str, ...]
    corporate_actions_available: bool
    event_issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class AttentionItem:
    attention_id: str
    kind: str
    symbol: str
    label: str
    result: RuleResult
    as_of: datetime
    reason_code: str
    priority: int


class RuleInputReader(Protocol):
    def read(self, symbol: str, *, as_of: datetime) -> RuleInput: ...


class ObservationRuleStore(Protocol):
    def list_rules(self, *, actor_id: str) -> tuple[RuleInstanceView, ...]: ...
    def list_evaluations(self, *, actor_id: str) -> tuple[RuleEvaluationView, ...]: ...
    def create(
        self,
        *,
        actor_id: str,
        rule: RuleInstanceView,
        idempotency_key: str,
    ) -> RuleInstanceView: ...
    def change_state(
        self,
        *,
        actor_id: str,
        rule_id: str,
        expected_revision: int,
        state: RuleState,
        idempotency_key: str,
    ) -> RuleInstanceView: ...
    def append_batch(
        self,
        *,
        actor_id: str,
        batch: RuleEvaluationBatchView,
        idempotency_key: str,
    ) -> RuleEvaluationBatchView: ...


class InMemoryObservationRuleStore:
    def __init__(self) -> None:
        self._rules: dict[str, dict[str, RuleInstanceView]] = {}
        self._evaluations: dict[str, list[RuleEvaluationView]] = {}
        self._results: dict[tuple[str, str], object] = {}

    def list_rules(self, *, actor_id: str) -> tuple[RuleInstanceView, ...]:
        evaluations = self.list_evaluations(actor_id=actor_id)
        latest = {item.rule_id: item for item in evaluations}
        return tuple(
            replace(rule, latest_evaluation=latest.get(rule.rule_id))
            for rule in self._rules.get(actor_id, {}).values()
        )

    def list_evaluations(self, *, actor_id: str) -> tuple[RuleEvaluationView, ...]:
        return tuple(self._evaluations.get(actor_id, ()))

    def create(
        self,
        *,
        actor_id: str,
        rule: RuleInstanceView,
        idempotency_key: str,
    ) -> RuleInstanceView:
        key = (actor_id, idempotency_key)
        if key in self._results:
            return self._results[key]  # type: ignore[return-value]
        self._rules.setdefault(actor_id, {})[rule.rule_id] = rule
        self._results[key] = rule
        return rule

    def change_state(
        self,
        *,
        actor_id: str,
        rule_id: str,
        expected_revision: int,
        state: RuleState,
        idempotency_key: str,
    ) -> RuleInstanceView:
        key = (actor_id, idempotency_key)
        if key in self._results:
            return self._results[key]  # type: ignore[return-value]
        rule = self._rules.get(actor_id, {}).get(rule_id)
        if rule is None:
            raise ValueError("private_object_not_found")
        if rule.revision != expected_revision:
            raise ValueError("revision_conflict")
        if not _valid_transition(rule.state, state):
            raise ValueError("invalid_command")
        updated = replace(rule, state=state, revision=rule.revision + 1)
        self._rules[actor_id][rule_id] = updated
        self._results[key] = updated
        return updated

    def append_batch(
        self,
        *,
        actor_id: str,
        batch: RuleEvaluationBatchView,
        idempotency_key: str,
    ) -> RuleEvaluationBatchView:
        key = (actor_id, idempotency_key)
        if key in self._results:
            return self._results[key]  # type: ignore[return-value]
        self._evaluations.setdefault(actor_id, []).extend(batch.evaluations)
        self._results[key] = batch
        return batch


class PostgresObservationRuleStore:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        cipher: PersonalDataCipher,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    def list_rules(self, *, actor_id: str) -> tuple[RuleInstanceView, ...]:
        with self._session_factory() as session:
            workspace = self._workspace(session, actor_id, lock=False)
            if workspace is None:
                return ()
            evaluations = self._list_evaluations(session, workspace.id)
            latest = {item.rule_id: item for item in evaluations}
            rows = session.scalars(
                select(PersonalRuleInstance)
                .where(PersonalRuleInstance.workspace_id == workspace.id)
                .order_by(PersonalRuleInstance.created_at, PersonalRuleInstance.id)
            ).all()
            return tuple(
                replace(self._rule_view(row), latest_evaluation=latest.get(row.id))
                for row in rows
            )

    def list_evaluations(self, *, actor_id: str) -> tuple[RuleEvaluationView, ...]:
        with self._session_factory() as session:
            workspace = self._workspace(session, actor_id, lock=False)
            return () if workspace is None else self._list_evaluations(session, workspace.id)

    def create(
        self,
        *,
        actor_id: str,
        rule: RuleInstanceView,
        idempotency_key: str,
    ) -> RuleInstanceView:
        with self._session_factory() as session, session.begin():
            workspace = self._workspace(session, actor_id, lock=True)
            if workspace is None:
                workspace = self._create_workspace(session, actor_id)
            key_hash = _idempotency_hash(actor_id, idempotency_key)
            existing = session.scalar(
                select(PersonalRuleRevision).where(
                    PersonalRuleRevision.workspace_id == workspace.id,
                    PersonalRuleRevision.idempotency_hash == key_hash,
                )
            )
            if existing is not None:
                return self._rule_view(session.get(PersonalRuleInstance, existing.rule_id))
            envelope = self._cipher.encrypt_json(
                {"symbol": rule.symbol, "parameters": rule.parameters},
                aad=_aad("personal_rule_instances", rule.rule_id),
            )
            row = PersonalRuleInstance(
                id=rule.rule_id,
                workspace_id=workspace.id,
                template_id=rule.template_id,
                template_version=rule.template_version,
                state=rule.state,
                revision=rule.revision,
                **_envelope_values(envelope),
            )
            session.add(row)
            self._append_revision(
                session,
                workspace_id=workspace.id,
                row=row,
                action="create_rule",
                idempotency_hash=key_hash,
                confirmed_at=None,
                payload={"symbol": rule.symbol, "parameters": rule.parameters, "state": rule.state},
            )
            session.flush()
            return rule

    def change_state(
        self,
        *,
        actor_id: str,
        rule_id: str,
        expected_revision: int,
        state: RuleState,
        idempotency_key: str,
    ) -> RuleInstanceView:
        with self._session_factory() as session, session.begin():
            workspace = self._workspace(session, actor_id, lock=True)
            if workspace is None:
                raise ValueError("private_object_not_found")
            key_hash = _idempotency_hash(actor_id, idempotency_key)
            existing = session.scalar(
                select(PersonalRuleRevision).where(
                    PersonalRuleRevision.workspace_id == workspace.id,
                    PersonalRuleRevision.idempotency_hash == key_hash,
                )
            )
            if existing is not None:
                return self._rule_view(session.get(PersonalRuleInstance, existing.rule_id))
            row = session.scalar(
                select(PersonalRuleInstance)
                .where(
                    PersonalRuleInstance.id == rule_id,
                    PersonalRuleInstance.workspace_id == workspace.id,
                )
                .with_for_update()
            )
            if row is None:
                raise ValueError("private_object_not_found")
            if row.revision != expected_revision:
                raise ValueError("revision_conflict")
            if not _valid_transition(row.state, state):
                raise ValueError("invalid_command")
            payload = self._decrypt_rule(row)
            row.state = state
            row.revision += 1
            self._append_revision(
                session,
                workspace_id=workspace.id,
                row=row,
                action="set_rule_state",
                idempotency_hash=key_hash,
                confirmed_at=datetime.now(timezone.utc) if state == "enabled" else None,
                payload={**payload, "state": state},
            )
            session.flush()
            return self._rule_view(row)

    def append_batch(
        self,
        *,
        actor_id: str,
        batch: RuleEvaluationBatchView,
        idempotency_key: str,
    ) -> RuleEvaluationBatchView:
        with self._session_factory() as session, session.begin():
            workspace = self._workspace(session, actor_id, lock=True)
            if workspace is None:
                raise ValueError("private_object_not_found")
            key_hash = _idempotency_hash(actor_id, idempotency_key)
            existing = session.scalar(
                select(PersonalRuleEvaluationBatch).where(
                    PersonalRuleEvaluationBatch.workspace_id == workspace.id,
                    PersonalRuleEvaluationBatch.idempotency_hash == key_hash,
                )
            )
            if existing is not None:
                return self._batch_view(session, existing)
            batch_envelope = self._cipher.encrypt_json(
                {"symbol": batch.symbol},
                aad=_aad("personal_rule_evaluation_batches", batch.batch_id),
            )
            session.add(
                PersonalRuleEvaluationBatch(
                    id=batch.batch_id,
                    workspace_id=workspace.id,
                    as_of=batch.as_of,
                    status=batch.status,
                    fingerprint=batch.fingerprint,
                    idempotency_hash=key_hash,
                    **_envelope_values(batch_envelope),
                )
            )
            session.flush()
            for evaluation in batch.evaluations:
                revision = session.scalar(
                    select(PersonalRuleRevision).where(
                        PersonalRuleRevision.rule_id == evaluation.rule_id,
                        PersonalRuleRevision.revision == evaluation.rule_revision,
                    )
                )
                if revision is None:
                    raise ValueError("revision_conflict")
                envelope = self._cipher.encrypt_json(
                    {
                        "symbol": evaluation.symbol,
                        "observed_value": evaluation.observed_value,
                        "threshold": evaluation.threshold,
                        "reason_code": evaluation.reason_code,
                        "source_health": evaluation.source_health,
                    },
                    aad=_aad("personal_rule_evaluations", evaluation.evaluation_id),
                )
                session.add(
                    PersonalRuleEvaluation(
                        id=evaluation.evaluation_id,
                        workspace_id=workspace.id,
                        result_summary=evaluation.result,
                        synthetic=False,
                        batch_id=batch.batch_id,
                        rule_revision_id=revision.id,
                        result=evaluation.result,
                        as_of=evaluation.as_of,
                        evidence_ids=list(evaluation.evidence_ids),
                        fingerprint=evaluation.fingerprint,
                        **_envelope_values(envelope),
                    )
                )
            session.flush()
            return batch

    def _workspace(
        self, session: Session, actor_id: str, *, lock: bool
    ) -> PersonalWorkspace | None:
        statement = select(PersonalWorkspace).where(
            PersonalWorkspace.actor_identity_hash == _identity_hash(actor_id)
        )
        if lock:
            statement = statement.with_for_update()
        return session.scalar(statement)

    def _create_workspace(self, session: Session, actor_id: str) -> PersonalWorkspace:
        workspace_id = str(uuid4())
        envelope = self._cipher.encrypt_json(
            {"usd_cash": "0.0000"}, aad=_aad("personal_workspaces", workspace_id)
        )
        workspace = PersonalWorkspace(
            id=workspace_id,
            actor_identity_hash=_identity_hash(actor_id),
            revision=0,
            **_envelope_values(envelope),
        )
        session.add(workspace)
        session.flush()
        return workspace

    def _rule_view(self, row: PersonalRuleInstance | None) -> RuleInstanceView:
        if row is None:
            raise ValueError("private_object_not_found")
        payload = self._decrypt_rule(row)
        return RuleInstanceView(
            rule_id=row.id,
            template_id=row.template_id,
            template_version=row.template_version,
            symbol=str(payload["symbol"]),
            state=row.state,
            revision=row.revision,
            parameters=dict(payload["parameters"]),
        )

    def _decrypt_rule(self, row: PersonalRuleInstance) -> dict[str, Any]:
        return self._cipher.decrypt_json(
            _row_envelope(row), aad=_aad("personal_rule_instances", row.id)
        )

    def _append_revision(
        self,
        session: Session,
        *,
        workspace_id: str,
        row: PersonalRuleInstance,
        action: str,
        idempotency_hash: str,
        confirmed_at: datetime | None,
        payload: dict[str, Any],
    ) -> None:
        revision_id = str(uuid4())
        envelope = self._cipher.encrypt_json(
            payload, aad=_aad("personal_rule_revisions", revision_id)
        )
        session.add(
            PersonalRuleRevision(
                id=revision_id,
                workspace_id=workspace_id,
                rule_id=row.id,
                revision=row.revision,
                action=action,
                idempotency_hash=idempotency_hash,
                confirmed_at=confirmed_at,
                **_envelope_values(envelope),
            )
        )

    def _list_evaluations(
        self, session: Session, workspace_id: str
    ) -> tuple[RuleEvaluationView, ...]:
        rows = session.scalars(
            select(PersonalRuleEvaluation)
            .where(
                PersonalRuleEvaluation.workspace_id == workspace_id,
                PersonalRuleEvaluation.synthetic.is_(False),
            )
            .order_by(PersonalRuleEvaluation.created_at, PersonalRuleEvaluation.id)
        ).all()
        values = []
        for row in rows:
            if row.rule_revision_id is None or row.batch_id is None or row.as_of is None:
                continue
            revision = session.get(PersonalRuleRevision, row.rule_revision_id)
            if revision is None:
                continue
            payload = self._cipher.decrypt_json(
                _row_envelope(row), aad=_aad("personal_rule_evaluations", row.id)
            )
            values.append(
                RuleEvaluationView(
                    evaluation_id=row.id,
                    batch_id=row.batch_id,
                    rule_id=revision.rule_id,
                    rule_revision=revision.revision,
                    symbol=str(payload["symbol"]),
                    result=row.result or row.result_summary,
                    as_of=row.as_of,
                    source_health=str(payload["source_health"]),
                    evidence_ids=tuple(row.evidence_ids or ()),
                    observed_value=payload.get("observed_value"),
                    threshold=payload.get("threshold"),
                    reason_code=str(payload["reason_code"]),
                    fingerprint=row.fingerprint or "",
                )
            )
        return tuple(values)

    def _batch_view(
        self, session: Session, row: PersonalRuleEvaluationBatch
    ) -> RuleEvaluationBatchView:
        payload = self._cipher.decrypt_json(
            _row_envelope(row), aad=_aad("personal_rule_evaluation_batches", row.id)
        )
        evaluations = tuple(
            item
            for item in self._list_evaluations(session, row.workspace_id)
            if item.batch_id == row.id
        )
        return RuleEvaluationBatchView(
            batch_id=row.id,
            symbol=str(payload["symbol"]),
            as_of=row.as_of,
            status=row.status,
            fingerprint=row.fingerprint,
            evaluations=evaluations,
        )


class UnavailableRuleInputReader:
    def read(self, symbol: str, *, as_of: datetime) -> RuleInput:
        return RuleInput(
            symbol=symbol,
            raw_bars=(),
            adjusted_bars=(),
            events=(),
            source_health="unavailable",
            evidence_ids=(),
            corporate_actions_available=False,
        )


class InstrumentRuleInputReader:
    def __init__(self, source: InstrumentObservationReader, *, limit: int = 600) -> None:
        self._source = source
        self._limit = limit

    def read(self, symbol: str, *, as_of: datetime) -> RuleInput:
        observation = self._source.open(symbol, as_of=as_of, limit=self._limit)
        evidence_ids = tuple(
            dict.fromkeys(
                [bar.evidence_id for bar in observation.raw_bars]
                + [bar.evidence_id for bar in observation.provider_adjusted_bars]
                + [identity for event in observation.events for identity in event.evidence_ids]
            )
        )
        return RuleInput(
            symbol=symbol,
            raw_bars=observation.raw_bars,
            adjusted_bars=observation.provider_adjusted_bars,
            events=observation.events,
            source_health=observation.source_health,
            evidence_ids=evidence_ids,
            corporate_actions_available="corporate_actions_unavailable" not in observation.issues,
            event_issues=observation.issues,
        )


_TEMPLATES = (
    RuleTemplateView("price_threshold", 1, "价格阈值", "最新合格收盘价达到阈值", "USD", "daily", "America/New_York", "1 个交易日", False, {"direction": "gte", "price": "100"}),
    RuleTemplateView("return_window", 1, "区间收益", "复权收盘价区间变化", "ratio", "daily", "America/New_York", "1–252 个交易日", False, {"window": 20, "direction": "gte", "threshold": "0.1"}),
    RuleTemplateView("moving_average_state", 1, "均线状态", "收盘价相对简单移动平均线", "state", "daily", "America/New_York", "5–252 个交易日", False, {"window": 20, "relation": "above"}),
    RuleTemplateView("realized_volatility", 1, "实现波动率", "日对数收益标准差年化", "ratio", "daily", "America/New_York", "10–252 个交易日", False, {"window": 20, "direction": "gte", "threshold": "0.3"}),
    RuleTemplateView("rolling_drawdown", 1, "滚动回撤", "相对滚动窗口高点的回撤", "ratio", "daily", "America/New_York", "20–504 个交易日", False, {"window": 60, "threshold": "0.1"}),
    RuleTemplateView("volume_ratio", 1, "量比", "当日成交量相对前窗中位数", "ratio", "daily", "America/New_York", "5–120 个交易日", False, {"window": 20, "ratio": "2"}),
    RuleTemplateView("confirmed_event_window", 1, "确认事件窗口", "已确认公司事件进入观察窗口", "days", "event", "America/New_York", "0–90 天", False, {"event_type": "sec_filing", "days": 7}),
    RuleTemplateView("macro_release_window", 1, "宏观发布窗口", "官方宏观发布进入观察窗口", "days", "event", "America/New_York", "0–30 天", False, {"series": "CPI", "days": 3}),
)
_TEMPLATE_BY_ID = {item.template_id: item for item in _TEMPLATES}


class ObservationRuleBook:
    def __init__(
        self,
        *,
        store: ObservationRuleStore,
        inputs: RuleInputReader,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._inputs = inputs
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def list_templates(self, actor: PersonalActor) -> tuple[RuleTemplateView, ...]:
        return _TEMPLATES

    def open(self, actor: PersonalActor) -> dict:
        return {
            "rules": self._store.list_rules(actor_id=actor.actor_id),
            "evaluations": self._store.list_evaluations(actor_id=actor.actor_id),
        }

    def revise(
        self,
        actor: PersonalActor,
        command: CreateObservationRuleCommand | SetObservationRuleStateCommand,
        *,
        idempotency_key: str,
    ) -> RuleInstanceView:
        if not idempotency_key.strip():
            raise ValueError("invalid_command")
        if isinstance(command, CreateObservationRuleCommand):
            template = _TEMPLATE_BY_ID.get(command.template_id)
            if template is None:
                raise ValueError("invalid_command")
            symbol = _normalize_symbol(command.symbol)
            parameters = _validate_parameters(template.template_id, command.parameters)
            return self._store.create(
                actor_id=actor.actor_id,
                rule=RuleInstanceView(
                    rule_id=str(uuid4()),
                    template_id=template.template_id,
                    template_version=template.version,
                    symbol=symbol,
                    state="draft",
                    revision=1,
                    parameters=parameters,
                ),
                idempotency_key=idempotency_key,
            )
        return self._store.change_state(
            actor_id=actor.actor_id,
            rule_id=command.rule_id,
            expected_revision=command.expected_revision,
            state=command.state,
            idempotency_key=idempotency_key,
        )

    def evaluate(
        self,
        actor: PersonalActor,
        request: RuleEvaluationRequest,
        *,
        idempotency_key: str,
    ) -> RuleEvaluationBatchView:
        symbol = _normalize_symbol(request.symbol)
        if request.as_of.tzinfo is None:
            raise ValueError("as_of_requires_timezone")
        source = self._inputs.read(symbol, as_of=request.as_of)
        batch_id = str(uuid4())
        evaluations = tuple(
            _evaluate_rule(batch_id, rule, source, request.as_of)
            for rule in self._store.list_rules(actor_id=actor.actor_id)
            if rule.symbol == symbol and rule.state == "enabled"
        )
        fingerprint = _fingerprint(
            {
                "symbol": symbol,
                "as_of": request.as_of.isoformat(),
                "evaluations": [item.fingerprint for item in evaluations],
            }
        )
        batch = RuleEvaluationBatchView(
            batch_id=batch_id,
            symbol=symbol,
            as_of=request.as_of,
            status="completed",
            fingerprint=fingerprint,
            evaluations=evaluations,
        )
        return self._store.append_batch(
            actor_id=actor.actor_id,
            batch=batch,
            idempotency_key=idempotency_key,
        )

    def attention(
        self, actor: PersonalActor, *, symbol: str | None = None
    ) -> tuple[AttentionItem, ...]:
        items = []
        for evaluation in self._store.list_evaluations(actor_id=actor.actor_id):
            if symbol is not None and evaluation.symbol != symbol:
                continue
            if evaluation.result not in {"hit", "insufficient_data", "calculation_failed"}:
                continue
            items.append(
                AttentionItem(
                    attention_id=evaluation.evaluation_id,
                    kind="rule_hit" if evaluation.result == "hit" else "data_gap",
                    symbol=evaluation.symbol,
                    label=(
                        "规则命中"
                        if evaluation.result == "hit"
                        else "规则数据不足"
                    ),
                    result=evaluation.result,
                    as_of=evaluation.as_of,
                    reason_code=evaluation.reason_code,
                    priority=0 if evaluation.result == "hit" else 1,
                )
            )
        return tuple(sorted(items, key=lambda item: (item.priority, -item.as_of.timestamp())))


def _evaluate_rule(
    batch_id: str,
    rule: RuleInstanceView,
    source: RuleInput,
    as_of: datetime,
) -> RuleEvaluationView:
    try:
        result, observed, threshold, reason = _evaluate(rule, source, as_of)
    except (ArithmeticError, InvalidOperation, ValueError, OverflowError):
        result, observed, threshold, reason = (
            "calculation_failed",
            None,
            None,
            "calculation_failed",
        )
    payload = {
        "rule_id": rule.rule_id,
        "rule_revision": rule.revision,
        "template": f"{rule.template_id}@{rule.template_version}",
        "symbol": source.symbol,
        "as_of": as_of.isoformat(),
        "result": result,
        "observed": observed,
        "threshold": threshold,
        "reason": reason,
        "evidence_ids": list(source.evidence_ids),
        "source_health": source.source_health,
    }
    return RuleEvaluationView(
        evaluation_id=str(uuid4()),
        batch_id=batch_id,
        rule_id=rule.rule_id,
        rule_revision=rule.revision,
        symbol=source.symbol,
        result=result,
        as_of=as_of,
        source_health=source.source_health,
        evidence_ids=source.evidence_ids,
        observed_value=observed,
        threshold=threshold,
        reason_code=reason,
        fingerprint=_fingerprint(payload),
    )


def _evaluate(
    rule: RuleInstanceView,
    source: RuleInput,
    as_of: datetime,
) -> tuple[RuleResult, str | None, str | None, str]:
    params = rule.parameters
    if rule.template_id == "price_threshold":
        if not source.raw_bars:
            return "insufficient_data", None, _fixed(params["price"]), "price_unavailable"
        observed = source.raw_bars[-1].close
        threshold = Decimal(str(params["price"]))
        return _comparison(observed, threshold, params["direction"], source.source_health)
    if rule.template_id in {
        "return_window",
        "moving_average_state",
        "realized_volatility",
        "rolling_drawdown",
    }:
        if not source.corporate_actions_available or not source.adjusted_bars:
            return "insufficient_data", None, None, "adjusted_series_unavailable"
    if rule.template_id == "return_window":
        window = int(params["window"])
        if len(source.adjusted_bars) <= window:
            return "insufficient_data", None, _ratio(params["threshold"]), "warmup_insufficient"
        observed = source.adjusted_bars[-1].close / source.adjusted_bars[-1 - window].close - 1
        return _comparison(observed, Decimal(str(params["threshold"])), params["direction"], source.source_health, ratio=True)
    if rule.template_id == "moving_average_state":
        window = int(params["window"])
        if len(source.adjusted_bars) < window:
            return "insufficient_data", None, None, "warmup_insufficient"
        close = source.adjusted_bars[-1].close
        average = sum((bar.close for bar in source.adjusted_bars[-window:]), Decimal("0")) / window
        hit = close > average if params["relation"] == "above" else close < average
        return ("hit" if hit else "not_hit", _fixed(close), _fixed(average), "condition_met" if hit else "condition_not_met")
    if rule.template_id == "realized_volatility":
        window = int(params["window"])
        closes = [float(bar.close) for bar in source.adjusted_bars[-(window + 1):]]
        if len(closes) <= window or any(value <= 0 for value in closes):
            return "insufficient_data", None, _ratio(params["threshold"]), "warmup_insufficient"
        observed = Decimal(str(pstdev(math.log(closes[index] / closes[index - 1]) for index in range(1, len(closes))) * math.sqrt(252)))
        return _comparison(observed, Decimal(str(params["threshold"])), params["direction"], source.source_health, ratio=True)
    if rule.template_id == "rolling_drawdown":
        window = int(params["window"])
        if len(source.adjusted_bars) < window:
            return "insufficient_data", None, _ratio(params["threshold"]), "warmup_insufficient"
        closes = [bar.close for bar in source.adjusted_bars[-window:]]
        drawdown = closes[-1] / max(closes) - 1
        threshold = -Decimal(str(params["threshold"]))
        hit = drawdown <= threshold
        return ("hit" if hit else "not_hit", _ratio(drawdown), _ratio(threshold), "condition_met" if hit else "condition_not_met")
    if rule.template_id == "volume_ratio":
        window = int(params["window"])
        if len(source.raw_bars) <= window:
            return "insufficient_data", None, _ratio(params["ratio"]), "warmup_insufficient"
        baseline = median(bar.volume for bar in source.raw_bars[-1 - window:-1])
        if baseline <= 0:
            return "calculation_failed", None, _ratio(params["ratio"]), "median_volume_zero"
        observed = Decimal(source.raw_bars[-1].volume) / Decimal(str(baseline))
        return _comparison(observed, Decimal(str(params["ratio"])), "gte", source.source_health, ratio=True)
    if rule.template_id in {"confirmed_event_window", "macro_release_window"}:
        days = int(params["days"])
        track = "corporate" if rule.template_id == "confirmed_event_window" else "macro"
        event_type = params.get("event_type")
        series = str(params.get("series", "")).lower()
        matches = [
            event
            for event in source.events
            if event.track == track
            and event.confirmation_state == "confirmed"
            and (event_type is None or event.event_type == event_type)
            and (
                not series
                or series in event.label.lower()
                or any(series in evidence_id.lower() for evidence_id in event.evidence_ids)
            )
            and 0 <= (as_of.date() - event.occurred_at.date()).days <= days
        ]
        relevant_unavailable = (
            "official_events_unavailable" in source.event_issues
            if track == "macro"
            else {
                "corporate_actions_unavailable",
                "official_events_unavailable",
            }.issubset(source.event_issues)
        )
        if (source.source_health == "unavailable" or relevant_unavailable) and not matches:
            return "insufficient_data", None, str(days), "event_source_unavailable"
        return ("hit" if matches else "not_hit", str(len(matches)), str(days), "confirmed_event_in_window" if matches else "no_confirmed_event_in_window")
    raise ValueError("unknown_template")


def _comparison(
    observed: Decimal,
    threshold: Decimal,
    direction: str,
    source_health: str,
    *,
    ratio: bool = False,
) -> tuple[RuleResult, str, str, str]:
    hit = observed >= threshold if direction == "gte" else observed <= threshold
    reason = "condition_met" if hit else "condition_not_met"
    if source_health == "stale":
        reason = f"{reason}_source_stale"
    formatter = _ratio if ratio else _fixed
    return "hit" if hit else "not_hit", formatter(observed), formatter(threshold), reason


def _validate_parameters(template_id: str, value: dict[str, Any]) -> dict[str, Any]:
    params = dict(value)
    try:
        if template_id == "price_threshold":
            _require_choice(params, "direction", {"gte", "lte"})
            _require_decimal(params, "price", Decimal("0"), Decimal("10000000"), exclusive_min=True)
        elif template_id == "return_window":
            _require_int(params, "window", 1, 252)
            _require_choice(params, "direction", {"gte", "lte"})
            _require_decimal(params, "threshold", Decimal("-1"), Decimal("10"))
        elif template_id == "moving_average_state":
            _require_int(params, "window", 5, 252)
            _require_choice(params, "relation", {"above", "below"})
        elif template_id == "realized_volatility":
            _require_int(params, "window", 10, 252)
            _require_choice(params, "direction", {"gte", "lte"})
            _require_decimal(params, "threshold", Decimal("0"), Decimal("5"))
        elif template_id == "rolling_drawdown":
            _require_int(params, "window", 20, 504)
            _require_decimal(params, "threshold", Decimal("0"), Decimal("1"))
        elif template_id == "volume_ratio":
            _require_int(params, "window", 5, 120)
            _require_decimal(params, "ratio", Decimal("1"), Decimal("100"))
        elif template_id == "confirmed_event_window":
            _require_int(params, "days", 0, 90)
            if not str(params.get("event_type", "")).strip():
                raise ValueError
        elif template_id == "macro_release_window":
            _require_int(params, "days", 0, 30)
            if not str(params.get("series", "")).strip():
                raise ValueError
    except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid_rule_parameters") from exc
    return params


def _require_int(params: dict[str, Any], key: str, low: int, high: int) -> None:
    value = int(params[key])
    if isinstance(params[key], bool) or value < low or value > high:
        raise ValueError
    params[key] = value


def _require_decimal(
    params: dict[str, Any],
    key: str,
    low: Decimal,
    high: Decimal,
    *,
    exclusive_min: bool = False,
) -> None:
    value = Decimal(str(params[key]))
    if (value <= low if exclusive_min else value < low) or value > high or not value.is_finite():
        raise ValueError
    params[key] = str(value)


def _require_choice(params: dict[str, Any], key: str, allowed: set[str]) -> None:
    if params.get(key) not in allowed:
        raise ValueError


def _valid_transition(current: RuleState, target: RuleState) -> bool:
    return target in {
        "draft": {"enabled", "archived"},
        "enabled": {"paused", "archived"},
        "paused": {"enabled", "archived"},
        "archived": set(),
    }[current]


def _normalize_symbol(value: str) -> str:
    normalized = value.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", normalized):
        raise ValueError("unsupported_instrument")
    return normalized


def _fixed(value: Any) -> str:
    return format(Decimal(str(value)).quantize(Decimal("0.0001")), "f")


def _ratio(value: Any) -> str:
    return format(Decimal(str(value)).quantize(Decimal("0.000001")), "f")


def _fingerprint(value: dict[str, Any]) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _identity_hash(actor_id: str) -> str:
    return sha256(actor_id.encode("utf-8")).hexdigest()


def _idempotency_hash(actor_id: str, idempotency_key: str) -> str:
    return sha256(f"{actor_id}|rule|{idempotency_key}".encode("utf-8")).hexdigest()


def _aad(table: str, row_id: str) -> str:
    return f"private_workbench|{table}|{row_id}|payload|1"


def _envelope_values(envelope: EncryptedEnvelope) -> dict[str, Any]:
    return {
        "ciphertext": envelope.ciphertext,
        "nonce": envelope.nonce,
        "key_id": envelope.key_id,
        "payload_schema": envelope.payload_schema,
    }


def _row_envelope(row: Any) -> EncryptedEnvelope:
    return EncryptedEnvelope(
        ciphertext=bytes(row.ciphertext),
        nonce=bytes(row.nonce),
        key_id=row.key_id,
        payload_schema=row.payload_schema,
    )
