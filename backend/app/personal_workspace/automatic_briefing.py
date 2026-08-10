"""自动简报：持久触发、固定证据配方与单次 Completion 调用。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Callable, Literal, Mapping

from .analysis import (
    DEEPSEEK_MODEL,
    DEEPSEEK_MAX_OUTPUT_TOKENS,
    DEEPSEEK_PRICING_SNAPSHOT_SHA256,
    AnalysisClaim,
    FrozenEvidence,
    _estimate_deepseek_request_cost,
    _responses_request,
    _validate_response,
)
from .agent.ai_runtime import AIRuntime, RuntimeBudget, RuntimeRequest, run_runtime
from .agent.domain_tools import (
    DomainToolContext,
    DomainToolRegistry,
    EvidenceEnvelope,
    ToolGap,
)
from .contracts import PersonalActor
from .rule_automation import personal_rule_evaluation_slot
from .automatic_briefing_store import (
    AutomaticBriefingStore,
    BriefingCost,
    BriefingMode,
    DailyBudgetPolicy,
)


BriefingKind = Literal["premarket", "intraday_event", "postmarket"]
RECIPE_REVISION = "automatic-briefing-v1"
MAX_DOSSIERS = 5
ALL_TOOL_PERMISSIONS = frozenset(
    {"portfolio:read", "market:read", "news:read", "evidence:read"}
)


@dataclass(frozen=True)
class BriefingBudgetPolicy:
    usd_to_cny: Decimal
    fx_snapshot: str
    target_cny: Decimal = Decimal("0.50")
    soft_limit_cny: Decimal = Decimal("1.00")
    hard_limit_cny: Decimal = Decimal("5.00")
    revision: str = "daily-cny-budget-v1"

    def __post_init__(self) -> None:
        if self.usd_to_cny <= 0 or not self.fx_snapshot.strip():
            raise ValueError("fx_rate_invalid")
        if not (
            Decimal("0") < self.target_cny <= self.soft_limit_cny < self.hard_limit_cny
        ):
            raise ValueError("budget_policy_invalid")

    def store_policy(self) -> DailyBudgetPolicy:
        return DailyBudgetPolicy(
            revision=f"{self.revision}:{self.fx_snapshot}",
            fx_cny_per_usd=self.usd_to_cny,
            target_cny=self.target_cny,
            soft_limit_cny=self.soft_limit_cny,
            hard_limit_cny=self.hard_limit_cny,
        )


@dataclass(frozen=True)
class BriefingTrigger:
    kind: BriefingKind
    market_date: date
    as_of: datetime
    source_event_id: str | None = None
    evidence_id: str | None = None
    subject_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in {"premarket", "intraday_event", "postmarket"}:
            raise ValueError("briefing_trigger_invalid")
        if self.as_of.tzinfo is None:
            raise ValueError("briefing_trigger_invalid")
        if self.kind == "intraday_event" and (
            not self.source_event_id or not self.evidence_id
        ):
            raise ValueError("briefing_trigger_invalid")

    @property
    def trigger_key(self) -> str:
        if self.kind == "intraday_event":
            return f"{RECIPE_REVISION}:{self.kind}:{self.source_event_id}"
        return f"{RECIPE_REVISION}:{self.market_date.isoformat()}:{self.kind}"


@dataclass(frozen=True)
class BriefingToolEvent:
    sequence: int
    event_type: Literal["tool_completed", "tool_failed"]
    tool_name: str
    tool_call_id: str
    arguments: Mapping[str, Any]
    status: str
    evidence_ids: tuple[str, ...]
    gaps: tuple[ToolGap, ...]
    data_sha256: str
    cost_usd: Decimal


@dataclass(frozen=True)
class BriefingAutomationResult:
    schedule_slot: str | None
    trigger_count: int
    failed_count: int


class AutomaticBriefingAutomation:
    """将 XNYS 时段与结构化事实事件转换成持久 trigger。"""

    def __init__(
        self,
        *,
        coordinator: "AutomaticBriefingCoordinator",
        tools: DomainToolRegistry,
    ) -> None:
        self._coordinator = coordinator
        self._tools = tools
        self._last_event_poll_minute: tuple[date, int, int] | None = None

    def run_once(
        self,
        actor: PersonalActor,
        *,
        as_of: datetime,
        worker_id: str,
    ) -> BriefingAutomationResult:
        self._coordinator.reconcile(actor, as_of=as_of)
        slot = personal_rule_evaluation_slot(as_of)
        if slot is None:
            return BriefingAutomationResult(None, 0, 0)
        session_date_text, session = slot.split(":", 1)
        triggers: list[BriefingTrigger] = []
        if session == "pre_market":
            triggers.append(
                BriefingTrigger(
                    "premarket", date.fromisoformat(session_date_text), as_of
                )
            )
        elif session == "post_market":
            triggers.append(
                BriefingTrigger(
                    "postmarket", date.fromisoformat(session_date_text), as_of
                )
            )
        else:
            event_poll_minute = (as_of.date(), as_of.hour, as_of.minute)
            if event_poll_minute == self._last_event_poll_minute:
                return BriefingAutomationResult(slot, 0, 0)
            self._last_event_poll_minute = event_poll_minute
            context = DomainToolContext(
                actor_id=actor.actor_id,
                granted_permissions=ALL_TOOL_PERMISSIONS,
                clock=lambda: as_of,
            )
            today = self._tools.invoke(
                "get_today_context",
                context=context,
                arguments={"as_of": as_of.isoformat()},
            )
            items = (
                today.data.get("fact_events", ())
                if today.status != "unavailable"
                else ()
            )
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                event_id = str(item.get("event_id", "")).strip()
                evidence_id = str(item.get("evidence_id", "")).strip()
                if event_id and evidence_id:
                    triggers.append(
                        BriefingTrigger(
                            "intraday_event",
                            date.fromisoformat(session_date_text),
                            as_of,
                            source_event_id=event_id,
                            evidence_id=evidence_id,
                            subject_ids=_symbols(item.get("related_symbols", ())),
                        )
                    )
        failed = 0
        for trigger in triggers:
            try:
                self._coordinator.run(actor, trigger, worker_id=worker_id)
            except Exception:
                failed += 1
        return BriefingAutomationResult(slot, len(triggers), failed)


class AutomaticBriefingCoordinator:
    """系统选择证据，模型只基于冻结证据生成一次结构化简报。"""

    def __init__(
        self,
        *,
        tools: DomainToolRegistry,
        runtime: AIRuntime,
        store: AutomaticBriefingStore,
        policy: BriefingBudgetPolicy,
        clock: Callable[[], datetime],
        lease_seconds: int = 120,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("automatic_briefing_config_invalid")
        self._tools = tools
        self._runtime = runtime
        self._store = store
        self.policy = policy
        self._clock = clock
        self._lease_seconds = lease_seconds

    def reconcile(self, actor: PersonalActor, *, as_of: datetime) -> int:
        return self._store.reconcile_expired_started(
            actor_id=actor.actor_id,
            now=as_of,
        )

    def run(
        self,
        actor: PersonalActor,
        trigger: BriefingTrigger,
        *,
        worker_id: str,
    ) -> Any:
        now = self._clock()
        claim = self._store.claim(
            actor_id=actor.actor_id,
            trigger_key=trigger.trigger_key,
            market_date=trigger.market_date,
            trigger_kind=trigger.kind,
            lease_owner=worker_id,
            lease_expires_at=now + timedelta(seconds=self._lease_seconds),
            now=now,
        )
        if not claim.acquired:
            return self._store.get(
                actor_id=actor.actor_id, trigger_key=trigger.trigger_key
            )

        tool_events, evidence, gaps = self._gather(actor, trigger)
        if not evidence:
            return self._store.fail_before_provider(
                claim=claim,
                failure_code="evidence_insufficient",
                private_payload=_private_payload(
                    trigger, tool_events, evidence, gaps, ()
                ),
                now=self._clock(),
            )
        request, estimated_cost_usd = _runtime_request(
            trigger, tool_events, evidence, gaps
        )
        reservation = self._store.reserve_budget(
            claim=claim,
            estimated_cost_usd=estimated_cost_usd,
            policy=self.policy.store_policy(),
            mode=BriefingMode.AUTOMATIC,
            now=self._clock(),
        )
        if not reservation.allowed:
            return self._store.fail_before_provider(
                claim=claim,
                failure_code=reservation.reason,
                private_payload=_private_payload(
                    trigger, tool_events, evidence, gaps, ()
                ),
                now=self._clock(),
            )
        self._store.mark_provider_started(
            claim=claim,
            reservation_id=reservation.reservation_id,
            provider_deadline=self._clock()
            + timedelta(seconds=self._lease_seconds),
            now=self._clock(),
        )
        runtime_result = run_runtime(
            self._runtime,
            RuntimeRequest(
                model=request.model,
                instructions=request.instructions,
                input_text=request.input_text,
                budget=RuntimeBudget(remaining_usd=estimated_cost_usd),
            ),
        )
        if runtime_result.status != "completed" or runtime_result.usage is None:
            failure_code = (
                runtime_result.failure.code
                if runtime_result.failure is not None
                else "runtime_failed"
            )
            return self._store.mark_outcome_unknown(
                briefing_id=claim.briefing_id,
                reservation_id=reservation.reservation_id,
                private_payload=_private_payload(
                    trigger, tool_events, evidence, gaps, (), failure_code
                ),
                now=self._clock(),
            )
        try:
            claims = _claims(runtime_result, evidence)
        except ValueError as exc:
            return self._store.complete(
                briefing_id=claim.briefing_id,
                reservation_id=reservation.reservation_id,
                cost=_briefing_cost(runtime_result.usage),
                failure_code=str(exc),
                private_payload=_private_payload(
                    trigger, tool_events, evidence, gaps, (), str(exc)
                ),
                now=self._clock(),
            )
        return self._store.complete(
            briefing_id=claim.briefing_id,
            reservation_id=reservation.reservation_id,
            cost=_briefing_cost(runtime_result.usage),
            private_payload=_private_payload(
                trigger, tool_events, evidence, gaps, claims
            ),
            now=self._clock(),
        )

    def _gather(
        self, actor: PersonalActor, trigger: BriefingTrigger
    ) -> tuple[tuple[BriefingToolEvent, ...], tuple[FrozenEvidence, ...], tuple[ToolGap, ...]]:
        context = DomainToolContext(
            actor_id=actor.actor_id,
            granted_permissions=ALL_TOOL_PERMISSIONS,
            clock=self._clock,
        )
        planned: list[tuple[str, dict[str, Any]]] = []
        if trigger.kind == "intraday_event":
            planned.append(("get_evidence", {"evidence_id": trigger.evidence_id}))
            symbols = _symbols(trigger.subject_ids)
        else:
            planned.append(("get_today_context", {"as_of": trigger.as_of.isoformat()}))
            symbols = _symbols(trigger.subject_ids)

        events: list[BriefingToolEvent] = []
        evidence_by_id: dict[str, FrozenEvidence] = {}
        gaps: list[ToolGap] = []
        index = 0
        while index < len(planned):
            tool_name, arguments = planned[index]
            index += 1
            result = self._tools.invoke(
                tool_name, context=context, arguments=arguments
            )
            if tool_name == "get_today_context" and not symbols:
                symbols = _symbols(
                    (*result.data.get("active_holding_symbols", ()),
                     *result.data.get("followed_symbols", ()))
                )
                planned.extend(
                    ("get_symbol_dossier", {"symbol": symbol, "bar_limit": 30})
                    for symbol in symbols[:MAX_DOSSIERS]
                )
            elif tool_name == "get_evidence":
                planned.extend(
                    ("get_symbol_dossier", {"symbol": symbol, "bar_limit": 30})
                    for symbol in symbols[:MAX_DOSSIERS]
                )
            data_text = json.dumps(result.data, ensure_ascii=False, sort_keys=True)
            event = BriefingToolEvent(
                sequence=len(events) + 1,
                event_type=(
                    "tool_failed" if result.status == "unavailable" else "tool_completed"
                ),
                tool_name=tool_name,
                tool_call_id=f"recipe-{len(events) + 1}",
                arguments=arguments,
                status=result.status,
                evidence_ids=tuple(item.evidence_id for item in result.evidence),
                gaps=result.gaps,
                data_sha256=sha256(data_text.encode("utf-8")).hexdigest(),
                cost_usd=result.cost_usd,
            )
            events.append(event)
            gaps.extend(result.gaps)
            if tool_name == "get_evidence":
                for item in result.evidence:
                    evidence_by_id.setdefault(
                        item.evidence_id, _frozen(item, tool_name, data_text)
                    )
                continue
            for item in result.evidence:
                canonical_arguments = {"evidence_id": item.evidence_id}
                canonical = self._tools.invoke(
                    "get_evidence",
                    context=context,
                    arguments=canonical_arguments,
                )
                canonical_text = json.dumps(
                    canonical.data, ensure_ascii=False, sort_keys=True
                )
                events.append(
                    BriefingToolEvent(
                        sequence=len(events) + 1,
                        event_type=(
                            "tool_failed"
                            if canonical.status == "unavailable"
                            else "tool_completed"
                        ),
                        tool_name="get_evidence",
                        tool_call_id=f"recipe-{len(events) + 1}",
                        arguments=canonical_arguments,
                        status=canonical.status,
                        evidence_ids=tuple(
                            evidence.evidence_id for evidence in canonical.evidence
                        ),
                        gaps=canonical.gaps,
                        data_sha256=sha256(
                            canonical_text.encode("utf-8")
                        ).hexdigest(),
                        cost_usd=canonical.cost_usd,
                    )
                )
                gaps.extend(canonical.gaps)
                for canonical_item in canonical.evidence:
                    evidence_by_id.setdefault(
                        canonical_item.evidence_id,
                        _frozen(canonical_item, "get_evidence", canonical_text),
                    )
        return tuple(events), tuple(evidence_by_id.values()), tuple(gaps)


def _symbols(values: Any) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(value).strip().upper() for value in values if str(value).strip()
        )
    )


def _frozen(
    envelope: EvidenceEnvelope, tool_name: str, data_text: str
) -> FrozenEvidence:
    try:
        data = json.loads(data_text)
    except json.JSONDecodeError:
        data = {}
    confirmation_state = (
        str(data.get("confirmation_state", ""))
        if isinstance(data, Mapping)
        else ""
    )
    return FrozenEvidence(
        evidence_id=envelope.evidence_id,
        kind=(
            "unconfirmed_source_summary"
            if confirmation_state == "source_summary_unconfirmed"
            else "verified_tool_evidence"
        ),
        source=envelope.source,
        field=tool_name,
        excerpt=data_text,
        content_sha256=envelope.content_sha256,
        as_of=envelope.as_of,
    )


def _instructions() -> str:
    base = _responses_request(
        model=DEEPSEEK_MODEL, question="自动简报", evidence=()
    )["messages"][0]["content"]
    return (
        f"{base} "
        "标记为 unconfirmed_source_summary 的证据只能用于 inference、"
        "conditional_scenario 或 unknown，不得用于 confirmed_fact。"
    )


def _input_text(
    trigger: BriefingTrigger,
    events: tuple[BriefingToolEvent, ...],
    evidence: tuple[FrozenEvidence, ...],
    gaps: tuple[ToolGap, ...],
) -> str:
    return json.dumps(
        {
            "trigger": {
                "kind": trigger.kind,
                "market_date": trigger.market_date.isoformat(),
                "as_of": trigger.as_of.isoformat(),
            },
            "tool_events": [asdict(item) for item in events],
            "frozen_evidence": [asdict(item) for item in evidence],
            "gaps": [asdict(item) for item in gaps],
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _runtime_request(
    trigger: BriefingTrigger,
    events: tuple[BriefingToolEvent, ...],
    evidence: tuple[FrozenEvidence, ...],
    gaps: tuple[ToolGap, ...],
) -> tuple[RuntimeRequest, Decimal]:
    instructions = _instructions()
    input_text = _input_text(trigger, events, evidence, gaps)
    request = RuntimeRequest(
        model=DEEPSEEK_MODEL,
        instructions=instructions,
        input_text=input_text,
        budget=RuntimeBudget(remaining_usd=Decimal("1")),
    )
    provider_user_content = json.dumps(
        {"input": input_text},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    estimated = Decimal(
        _estimate_deepseek_request_cost(
            {
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": provider_user_content},
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": DEEPSEEK_MAX_OUTPUT_TOKENS,
                "stream": False,
                "thinking": {"type": "disabled"},
            }
        )
    )
    return request, estimated


def _briefing_cost(usage: Any) -> BriefingCost:
    return BriefingCost(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=usage.cost_usd,
        cache_hit_tokens=usage.cache_hit_tokens,
        cache_miss_tokens=usage.cache_miss_tokens,
    )


def _private_payload(
    trigger: BriefingTrigger,
    events: tuple[BriefingToolEvent, ...],
    evidence: tuple[FrozenEvidence, ...],
    gaps: tuple[ToolGap, ...],
    claims: tuple[AnalysisClaim, ...],
    failure_code: str | None = None,
) -> dict[str, Any]:
    return {
        "recipe_revision": RECIPE_REVISION,
        "trigger": asdict(trigger),
        "tool_events": [asdict(item) for item in events],
        "evidence": [asdict(item) for item in evidence],
        "gaps": [asdict(item) for item in gaps],
        "claims": [asdict(item) for item in claims],
        "failure_code": failure_code,
        "pricing_snapshot_sha256": DEEPSEEK_PRICING_SNAPSHOT_SHA256,
    }


def _claims(runtime_result: Any, evidence: tuple[FrozenEvidence, ...]) -> tuple[AnalysisClaim, ...]:
    outputs = [event.text for event in runtime_result.events if event.type == "output_completed"]
    if len(outputs) != 1 or not outputs[0]:
        raise ValueError("provider_claims_invalid_schema")
    try:
        payload = json.loads(outputs[0])
    except json.JSONDecodeError:
        raise ValueError("provider_claims_invalid_schema") from None
    if not isinstance(payload, dict):
        raise ValueError("provider_claims_invalid_schema")
    claims = _validate_response({"status": "completed", **payload}, evidence)
    expected = {"confirmed_fact", "inference", "conditional_scenario", "unknown"}
    if len(claims) != 4 or {claim.kind for claim in claims} != expected:
        raise ValueError("provider_claims_invalid_schema")
    trusted_ids = {
        item.evidence_id
        for item in evidence
        if item.kind != "unconfirmed_source_summary"
    }
    if any(
        claim.kind == "confirmed_fact"
        and not set(claim.evidence_ids).issubset(trusted_ids)
        for claim in claims
    ):
        raise ValueError("claim_evidence_unconfirmed")
    return claims
