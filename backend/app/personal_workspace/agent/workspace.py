"""AgentAnalysisWorkspace：把 AnalysisWorkspace 的 provider 执行替换为 tool-use agent 循环。

复用基类的生命周期/存储/事件/租约机制；prepare/start 不再依赖冻结证据门禁，
改为工具与技能预览；run 走统一 Completion Runtime 多轮循环。与单发路径并行共存，部署时通过
PERSONAL_ANALYSIS_MODE=agent 显式选择。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from typing import Any, Callable
from uuid import uuid4

from ..analysis import (
    DEEPSEEK_MODEL,
    DEEPSEEK_PRICING_SNAPSHOT,
    DEEPSEEK_PRICING_SNAPSHOT_SHA256,
    DEEPSEEK_RETENTION,
    AnalysisDraftReceipt,
    AnalysisIntent,
    AnalysisRunView,
    AnalysisStore,
    AnalysisToolEvent,
    AnalysisUsage,
    FrozenEvidence,
    AnalysisWorkspace,
    ProviderFailure,
    StoredAnalysisDraft,
    StoredAnalysisRun,
    _append_event,
    _json_sha256,
)
from ..contracts import PersonalActor
from .ai_runtime import (
    RuntimeBudget,
    RuntimeEvent,
    RuntimeExecutionContext,
    RuntimeRequest,
    RuntimeResult,
    RuntimeToolEvidence,
    RuntimeUsage,
    run_runtime,
)
from .client_tool_runtime import (
    CLIENT_TOOL_BASE_SYSTEM_PROMPT,
    RegistryToolExecutor,
    finalize_claims,
)
from .completion_runtime import DeepSeekCompletionRuntime
from .domain_tools import DomainToolRegistry, RuntimeToolDefinition
from .evidence import EvidenceLedger, EvidenceReadContext
from .evidence import FrozenEvidence as LedgerFrozenEvidence
from .protocol import Skill

AGENT_CONFIG_REVISION = "personal-agent-deepseek-v1"
LEGACY_ANALYSIS_TOOL_NAMES = ("get_holdings", "get_kline", "get_news")
ANALYSIS_TOOL_PERMISSIONS = frozenset(
    {"portfolio:read", "market:read", "news:read", "evidence:read"}
)


class _AgentProviderShim:
    """基类构造要求 provider 参数；agent 路径不使用基类的单发 provider 执行。

    API 进程不持有 DeepSeek 密钥，只透传可用性；worker 传真实适配器。
    """

    def __init__(self, *, available: bool = True) -> None:
        self.available = available

    def create_response(self, request: dict) -> dict:
        raise RuntimeError("agent_provider_shim_not_callable")


class AgentAnalysisWorkspace(AnalysisWorkspace):
    def __init__(
        self,
        *,
        store: AnalysisStore,
        runtime: DeepSeekCompletionRuntime,
        domain_tools: DomainToolRegistry,
        evidence_ledger: EvidenceLedger,
        tools: tuple[RuntimeToolDefinition, ...],
        skills: tuple[Skill, ...],
        model: str = DEEPSEEK_MODEL,
        config_revision: str = AGENT_CONFIG_REVISION,
        preview_ttl: timedelta | None = None,
        monthly_soft_budget_usd: Decimal = Decimal("25"),
        monthly_spend_reader: Callable[[PersonalActor, datetime], Decimal]
        | None = None,
        clock: Callable[[], datetime] | None = None,
        lease_seconds: int = 600,
        daily_budget_guard: Any | None = None,
    ) -> None:
        if preview_ttl is None:
            preview_ttl = timedelta(minutes=30)
        super().__init__(
            store=store,
            evidence_reader=lambda actor, intent: (),
            provider=_AgentProviderShim(),
            clock=clock or (lambda: datetime.now(timezone.utc)),
            model=model,
            config_revision=config_revision,
            preview_ttl=preview_ttl,
            monthly_soft_budget_usd=monthly_soft_budget_usd,
            monthly_spend_reader=monthly_spend_reader,
            lease_seconds=lease_seconds,
            daily_budget_guard=daily_budget_guard,
        )
        self._runtime = runtime
        self._domain_tools = domain_tools
        self._evidence_ledger = evidence_ledger
        self._tools = tuple(tools)
        self._skills = tuple(skills)

    def prepare(
        self,
        actor: PersonalActor,
        intent: AnalysisIntent,
        *,
        idempotency_key: str,
    ) -> AnalysisDraftReceipt:
        question = intent.question.strip()
        if not question or not intent.subject_ids:
            raise ValueError("invalid_command")
        now = self._clock()
        draft_id = str(uuid4())
        tool_names = tuple(tool.name for tool in self._tools)
        skill_ids = tuple(skill.skill_id for skill in self._skills)
        normalized_intent = replace(intent, question=question)
        context = self._execution_context(
            actor_id=actor.actor_id,
            heartbeat=lambda: None,
            deadline=now + timedelta(seconds=self._lease_seconds),
        )
        estimated_cost = self._runtime.maximum_cost_usd(
            self._runtime_request(
                normalized_intent,
                remaining_usd=self._monthly_soft_budget_usd,
            ),
            context,
        )
        included_fields = ("user_question", *tool_names)
        preview_payload = {
            "question": question,
            "subject_ids": list(intent.subject_ids),
            "provider": "deepseek-agent",
            "model": self._model,
            "config_revision": self._config_revision,
            "included_fields": list(included_fields),
            "tools": list(tool_names),
            "skills": list(skill_ids),
            "retention": DEEPSEEK_RETENTION,
        }
        preview_sha256 = _json_sha256(preview_payload)
        receipt = AnalysisDraftReceipt(
            draft_id=draft_id,
            status="ready",
            provider="deepseek-agent",
            model=self._model,
            config_revision=self._config_revision,
            included_fields=included_fields,
            excluded_fields=(),
            gaps=(),
            preview_sha256=preview_sha256,
            retention=DEEPSEEK_RETENTION,
            estimated_cost_usd=format(estimated_cost, "f"),
            pricing_currency=DEEPSEEK_PRICING_SNAPSHOT["currency"],
            pricing_effective_on=DEEPSEEK_PRICING_SNAPSHOT["effective_on"],
            pricing_snapshot_sha256=DEEPSEEK_PRICING_SNAPSHOT_SHA256,
            expires_at=now + self._preview_ttl,
            consumed_at=None,
            evidence_ids=(),
            evidence=(),
        )
        stored = self._store.save_draft(
            StoredAnalysisDraft(
                actor_id=actor.actor_id,
                idempotency_key=idempotency_key,
                intent=normalized_intent,
                receipt=receipt,
                evidence=(),
            )
        )
        return stored.receipt

    def start(
        self,
        actor: PersonalActor,
        *,
        draft_id: str,
        preview_sha256: str,
        idempotency_key: str,
    ) -> AnalysisRunView:
        draft = self._store.get_draft(actor.actor_id, draft_id)
        if draft is None:
            raise ValueError("private_object_not_found")
        if not self._runtime.available:
            raise ValueError("provider_unavailable")
        projected_cost = self._monthly_spend_reader(actor, self._clock()) + Decimal(
            draft.receipt.estimated_cost_usd
        )
        if projected_cost > Decimal(self._monthly_soft_budget_usd):
            raise ValueError("budget_blocked")
        return self._store.consume_and_enqueue(
            actor_id=actor.actor_id,
            draft_id=draft_id,
            preview_sha256=preview_sha256,
            idempotency_key=idempotency_key,
            now=self._clock(),
            run_id=str(uuid4()),
        ).view

    def _execute_provider(
        self,
        validating: StoredAnalysisRun,
        draft: StoredAnalysisDraft,
        run: StoredAnalysisRun,
        *,
        budget_reservation: Any | None = None,
    ) -> AnalysisRunView | None:
        current_run = replace(
            validating,
            view=replace(
                validating.view,
                provider_call_state="started",
                accounted_cost_usd=(
                    validating.view.estimated_cost_usd
                    if budget_reservation is not None
                    else None
                ),
            ),
        )
        latest = self._store.get_run(draft.actor_id, run.view.run_id)
        if latest is not None and latest.view.status == "cancelled":
            current_run = latest
        else:
            self._store.save_run(current_run)

        def heartbeat() -> None:
            nonlocal current_run
            now = self._clock()
            current_run = self._store.heartbeat(
                current_run,
                now=now,
                lease_seconds=self._lease_seconds,
            )
            if budget_reservation is not None:
                self._daily_budget_guard.heartbeat(
                    budget_reservation,
                    now=now,
                )

        remaining = max(
            Decimal("0"),
            self._monthly_soft_budget_usd
            - self._monthly_spend_reader(
                PersonalActor(actor_id=draft.actor_id), self._clock()
            ),
        )
        context = self._execution_context(
            actor_id=draft.actor_id,
            heartbeat=heartbeat,
            deadline=self._clock() + timedelta(seconds=self._lease_seconds),
            run_id=run.view.run_id,
        )
        result = run_runtime(
            self._runtime,
            self._runtime_request(draft.intent, remaining_usd=remaining),
            context,
        )
        tool_events = _analysis_tool_events(result.events)
        evidence_failure = None
        try:
            tool_evidence = _freeze_analysis_evidence(
                result,
                ledger=self._evidence_ledger,
                actor_id=draft.actor_id,
                now=self._clock(),
            )
        except Exception:
            tool_evidence = ()
            evidence_failure = "tool_evidence_freeze_failed"
        current_run = replace(
            current_run,
            view=replace(
                current_run.view,
                tool_events=tool_events,
                tool_evidence=tool_evidence,
            ),
        )
        usage = _analysis_runtime_usage(result.usage)
        cost_usd = format(result.usage.cost_usd, "f") if result.usage else None

        if evidence_failure is not None and result.status == "completed":
            outcome_unknown = result.usage is None and (
                result.failure is None or result.failure.outcome_unknown
            )
            self._settle_runtime_budget(
                budget_reservation,
                run_id=run.view.run_id,
                usage=result.usage,
                failure_code=evidence_failure,
                outcome_unknown=outcome_unknown,
            )
            failed = replace(
                current_run,
                view=replace(
                    current_run.view,
                    provider_call_state=(
                        "outcome_unknown" if outcome_unknown else "completed"
                    ),
                    actual_cost_usd=cost_usd or (
                        "0" if not outcome_unknown else None
                    ),
                    accounted_cost_usd=(
                        cost_usd or "0"
                        if not outcome_unknown
                        else current_run.view.accounted_cost_usd
                    ),
                    usage=usage,
                ),
            )
            return self._fail_run(failed, evidence_failure)

        if result.status == "cancelled":
            failure = result.failure
            outcome_unknown = failure.outcome_unknown if failure is not None else True
            self._settle_runtime_budget(
                budget_reservation,
                run_id=run.view.run_id,
                usage=result.usage,
                failure_code="cancelled",
                outcome_unknown=outcome_unknown,
            )
            latest = self._store.get_run(draft.actor_id, run.view.run_id)
            if latest is not None and latest.view.status == "cancelled":
                known_cost = cost_usd or ("0" if not outcome_unknown else None)
                cancelled = replace(
                    latest,
                    view=replace(
                        latest.view,
                        provider_call_state=(
                            "outcome_unknown"
                            if outcome_unknown
                            else "not_started"
                            if result.usage is None
                            else "completed"
                        ),
                        actual_cost_usd=known_cost,
                        accounted_cost_usd=(
                            latest.view.accounted_cost_usd
                            if outcome_unknown
                            else known_cost
                        ),
                        usage=usage,
                        tool_events=tool_events,
                        tool_evidence=tool_evidence,
                    ),
                )
                return self._store.save_run(cancelled).view
            return self._fail_run(current_run, "cancelled")

        self._store.save_run(current_run)

        if result.status != "completed" or result.usage is None:
            failure = result.failure
            code = failure.code if failure is not None else "runtime_failed"
            outcome_unknown = failure.outcome_unknown if failure is not None else True
            self._settle_runtime_budget(
                budget_reservation,
                run_id=run.view.run_id,
                usage=result.usage,
                failure_code=code,
                outcome_unknown=outcome_unknown,
            )
            failed = replace(
                current_run,
                view=replace(
                    current_run.view,
                    provider_call_state=(
                        "outcome_unknown" if outcome_unknown else "completed"
                    ),
                    actual_cost_usd=cost_usd or (
                        "0" if not outcome_unknown else None
                    ),
                    accounted_cost_usd=(
                        cost_usd or "0"
                        if not outcome_unknown
                        else current_run.view.accounted_cost_usd
                    ),
                    usage=usage,
                ),
            )
            return self._fail_run(failed, code)

        output = next(
            event.text
            for event in reversed(result.events)
            if event.type == "output_completed"
        )
        try:
            claims = finalize_claims(output, tool_evidence)
        except (ProviderFailure, ValueError) as exc:
            code = exc.code if isinstance(exc, ProviderFailure) else str(exc)
            self._settle_runtime_budget(
                budget_reservation,
                run_id=run.view.run_id,
                usage=result.usage,
                failure_code=code,
                outcome_unknown=False,
            )
            failed = replace(
                current_run,
                view=replace(
                    current_run.view,
                    provider_call_state="completed",
                    actual_cost_usd=cost_usd,
                    accounted_cost_usd=cost_usd,
                    usage=usage,
                ),
            )
            return self._fail_run(failed, code)

        self._settle_runtime_budget(
            budget_reservation,
            run_id=run.view.run_id,
            usage=result.usage,
            failure_code=None,
            outcome_unknown=False,
        )
        completed = _append_event(current_run, "completed", "completed", self._clock())
        completed = replace(
            completed,
            lease_owner=None,
            lease_expires_at=None,
            view=replace(
                completed.view,
                claims=claims,
                actual_cost_usd=cost_usd,
                usage=usage,
                failure_code=None,
                cancellable=False,
                provider_call_state="completed",
                accounted_cost_usd=cost_usd,
                tool_events=tool_events,
                tool_evidence=tool_evidence,
            ),
        )
        return self._store.save_run(completed).view

    def _preflight_provider(
        self, draft: StoredAnalysisDraft, run: StoredAnalysisRun
    ) -> None:
        remaining = self._monthly_soft_budget_usd - self._monthly_spend_reader(
            PersonalActor(actor_id=draft.actor_id), self._clock()
        )
        request = self._runtime_request(draft.intent, remaining_usd=remaining)
        context = self._execution_context(
            actor_id=draft.actor_id,
            heartbeat=lambda: None,
            deadline=self._clock() + timedelta(seconds=self._lease_seconds),
        )
        if self._runtime.maximum_cost_usd(request, context) > remaining:
            raise ProviderFailure("budget_blocked", retryable=False)
        self._runtime.validate_request(request, context)

    def _runtime_request(
        self, intent: AnalysisIntent, *, remaining_usd: Decimal
    ) -> RuntimeRequest:
        return RuntimeRequest(
            model=self._model,
            instructions=self._system_prompt(),
            input_text=json.dumps(
                {
                    "question": intent.question,
                    "subject_ids": list(intent.subject_ids),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            budget=RuntimeBudget(remaining_usd=remaining_usd),
            tools=tuple(item.name for item in self._tools),
        )

    def _execution_context(
        self,
        *,
        actor_id: str,
        heartbeat: Callable[[], None],
        deadline: datetime,
        run_id: str | None = None,
    ) -> RuntimeExecutionContext:
        return RuntimeExecutionContext(
            tools=self._tools,
            executor=RegistryToolExecutor(
                registry=self._domain_tools,
                actor_id=actor_id,
                permissions=ANALYSIS_TOOL_PERMISSIONS,
                clock=self._clock,
            ),
            deadline=deadline,
            heartbeat=heartbeat,
            cancel_requested=(
                (lambda: _cancel_requested(self._store, actor_id, run_id))
                if run_id is not None
                else (lambda: False)
            ),
        )

    def _system_prompt(self) -> str:
        parts = [CLIENT_TOOL_BASE_SYSTEM_PROMPT]
        for skill in self._skills:
            parts.append(f"[技能：{skill.name}]\n{skill.system_prompt}")
        return "\n\n".join(parts)

    def _settle_runtime_budget(
        self,
        budget_reservation: Any | None,
        *,
        run_id: str,
        usage: RuntimeUsage | None,
        failure_code: str | None,
        outcome_unknown: bool,
    ) -> None:
        if budget_reservation is None:
            return
        if outcome_unknown:
            self._daily_budget_guard.mark_outcome_unknown(
                budget_reservation,
                run_id=run_id,
                failure_code=failure_code or "provider_cost_unknown",
                now=self._clock(),
            )
            return
        from ..automatic_briefing_store import BriefingCost

        self._daily_budget_guard.complete_call(
            budget_reservation,
            run_id=run_id,
            cost=BriefingCost(
                input_tokens=usage.input_tokens if usage is not None else 0,
                output_tokens=usage.output_tokens if usage is not None else 0,
                cache_hit_tokens=usage.cache_hit_tokens if usage is not None else 0,
                cache_miss_tokens=usage.cache_miss_tokens if usage is not None else 0,
                cost_usd=usage.cost_usd if usage is not None else Decimal("0"),
            ),
            failure_code=failure_code,
            now=self._clock(),
        )


def _freeze_analysis_evidence(
    result: RuntimeResult,
    *,
    ledger: EvidenceLedger,
    actor_id: str,
    now: datetime,
) -> tuple[FrozenEvidence, ...]:
    runtime_evidence = tuple(result.tool_evidence)
    if not runtime_evidence:
        return ()
    evidence_ids = tuple(item.evidence_id for item in runtime_evidence)
    frozen = ledger.freeze(
        EvidenceReadContext(
            actor_id=actor_id,
            permissions=ANALYSIS_TOOL_PERMISSIONS,
            purpose="domain_tool",
            now=now,
        ),
        evidence_ids,
    )
    if tuple(item.evidence_id for item in frozen) != evidence_ids:
        raise ValueError("tool_evidence_identity_mismatch")
    return tuple(
        _analysis_frozen_evidence(runtime_item, ledger_item)
        for runtime_item, ledger_item in zip(runtime_evidence, frozen, strict=True)
    )


def _analysis_frozen_evidence(
    runtime_item: RuntimeToolEvidence, ledger_item: LedgerFrozenEvidence
) -> FrozenEvidence:
    if (
        ledger_item.persistence != "encrypted_payload"
        or ledger_item.payload is None
        or ledger_item.content_sha256 != runtime_item.content_sha256
    ):
        raise ValueError("tool_evidence_identity_mismatch")
    as_of = (
        ledger_item.observed_at
        or ledger_item.published_at
        or ledger_item.effective_at
        or ledger_item.available_from
        or ledger_item.fetched_at
    )
    return FrozenEvidence(
        evidence_id=ledger_item.evidence_id,
        kind="tool_output",
        source=ledger_item.source,
        field=(
            ledger_item.authorized_fields[0]
            if ledger_item.authorized_fields
            else "tool_output"
        ),
        excerpt=json.dumps(
            _mutable_evidence_payload(ledger_item.payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )[:200],
        content_sha256=ledger_item.content_sha256,
        as_of=as_of,
    )


def _mutable_evidence_payload(value: Any) -> Any:
    if isinstance(value, dict) or hasattr(value, "items"):
        return {
            str(key): _mutable_evidence_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [_mutable_evidence_payload(item) for item in value]
    return value


def _analysis_tool_events(
    events: tuple[RuntimeEvent, ...],
) -> tuple[AnalysisToolEvent, ...]:
    projected: list[AnalysisToolEvent] = []
    for event in events:
        if event.type not in {"tool_completed", "tool_failed"}:
            continue
        projected.append(
            AnalysisToolEvent(
                sequence=len(projected) + 1,
                tool_name=event.tool_name or "unknown",
                tool_call_id=event.tool_call_id or "unknown",
                status="completed" if event.type == "tool_completed" else "failed",
                evidence_ids=event.evidence_ids,
                error_code=event.error_code,
            )
        )
    return tuple(projected)


def _analysis_runtime_usage(usage: RuntimeUsage | None) -> AnalysisUsage | None:
    if usage is None:
        return None
    return AnalysisUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_hit_tokens=usage.cache_hit_tokens,
        cache_miss_tokens=usage.cache_miss_tokens,
    )


def _cancel_requested(store: AnalysisStore, actor_id: str, run_id: str) -> bool:
    current = store.get_run(actor_id, run_id)
    return current is not None and current.view.status == "cancelled"


def build_agent_workspace(
    *,
    store: AnalysisStore,
    domain_tools: DomainToolRegistry,
    evidence_ledger: EvidenceLedger,
    provider: Any,
    monthly_soft_budget_usd: Decimal,
    monthly_spend_reader: Callable[[PersonalActor, datetime], Decimal],
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    daily_budget_guard: Any | None = None,
) -> AgentAnalysisWorkspace:
    """装配 agent 模式的完整工作区（worker/API runtime 共用）。

    领域能力只复用组合根已装配的唯一 registry。provider 由调用方注入：worker 传
    DeepSeekAgentChatAdapter，API 进程传可用性 shim（不持有密钥）。
    """
    from .skills import DEFAULT_ACTIVE_SKILLS

    tools = domain_tools.projected_definitions(
        permissions=ANALYSIS_TOOL_PERMISSIONS,
        names=LEGACY_ANALYSIS_TOOL_NAMES,
    )
    if tuple(item.name for item in tools) != LEGACY_ANALYSIS_TOOL_NAMES:
        raise ValueError("analysis_tools_unavailable")
    runtime = DeepSeekCompletionRuntime(provider=provider, clock=clock)
    return AgentAnalysisWorkspace(
        store=store,
        runtime=runtime,
        domain_tools=domain_tools,
        evidence_ledger=evidence_ledger,
        tools=tools,
        skills=DEFAULT_ACTIVE_SKILLS,
        model=DEEPSEEK_MODEL,
        clock=clock,
        monthly_soft_budget_usd=monthly_soft_budget_usd,
        monthly_spend_reader=monthly_spend_reader,
        daily_budget_guard=daily_budget_guard,
    )
