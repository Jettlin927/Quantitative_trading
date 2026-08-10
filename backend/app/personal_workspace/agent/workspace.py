"""AgentAnalysisWorkspace：把 AnalysisWorkspace 的 provider 执行替换为 tool-use agent 循环。

复用基类的生命周期/存储/事件/租约机制；prepare/start 不再依赖冻结证据门禁，
改为工具与技能预览；run 走 AgentRuntime 多轮循环。与单发路径并行共存，部署时通过
PERSONAL_ANALYSIS_MODE=agent 显式选择。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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
    FrozenEvidence,
    AnalysisWorkspace,
    ProviderFailure,
    StoredAnalysisDraft,
    StoredAnalysisRun,
    _append_event,
    _json_sha256,
)
from ..contracts import PersonalActor
from .protocol import Skill, Tool
from .runtime import AgentRuntime

AGENT_CONFIG_REVISION = "personal-agent-deepseek-v1"


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
        runtime: AgentRuntime,
        tools: tuple[Tool, ...],
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
        estimated_cost = self._runtime.maximum_cost_usd(normalized_intent)
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
        spend_before = self._monthly_spend_reader(
            PersonalActor(actor_id=draft.actor_id), self._clock()
        )
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

        def audit(
            event: AnalysisToolEvent, evidence: tuple[FrozenEvidence, ...]
        ) -> None:
            nonlocal current_run
            current_run = replace(
                current_run,
                view=replace(
                    current_run.view,
                    tool_events=(*current_run.view.tool_events, event),
                    tool_evidence=(*current_run.view.tool_evidence, *evidence),
                ),
            )
            self._store.save_run(current_run)

        try:
            result = self._runtime.run(
                actor_id=draft.actor_id,
                intent=draft.intent,
                spend_before=spend_before,
                heartbeat=heartbeat,
                audit=audit,
            )
        except ProviderFailure as exc:
            if exc.response_completed and exc.cost_usd is not None:
                if budget_reservation is not None:
                    from ..automatic_briefing_store import BriefingCost

                    usage = exc.usage
                    self._daily_budget_guard.complete_call(
                        budget_reservation,
                        run_id=run.view.run_id,
                        cost=BriefingCost(
                            input_tokens=usage.input_tokens if usage else 0,
                            output_tokens=usage.output_tokens if usage else 0,
                            cache_hit_tokens=usage.cache_hit_tokens if usage else 0,
                            cache_miss_tokens=usage.cache_miss_tokens if usage else 0,
                            cost_usd=Decimal(exc.cost_usd),
                        ),
                        failure_code=exc.code,
                        now=self._clock(),
                    )
                failed = replace(
                    current_run,
                    view=replace(
                        current_run.view,
                        provider_call_state="completed",
                        actual_cost_usd=exc.cost_usd,
                        accounted_cost_usd=exc.cost_usd,
                        usage=exc.usage,
                    ),
                )
            else:
                if budget_reservation is not None:
                    self._daily_budget_guard.mark_outcome_unknown(
                        budget_reservation,
                        run_id=run.view.run_id,
                        failure_code=exc.code,
                        now=self._clock(),
                    )
                failed = replace(
                    current_run,
                    view=replace(
                        current_run.view, provider_call_state="outcome_unknown"
                    ),
                )
            return self._fail_run(failed, exc.code)
        except ValueError as exc:
            if budget_reservation is not None:
                self._daily_budget_guard.mark_outcome_unknown(
                    budget_reservation,
                    run_id=run.view.run_id,
                    failure_code=str(exc),
                    now=self._clock(),
                )
            failed = replace(
                current_run,
                view=replace(current_run.view, provider_call_state="outcome_unknown"),
            )
            return self._fail_run(failed, str(exc))
        if budget_reservation is not None:
            from ..automatic_briefing_store import BriefingCost

            self._daily_budget_guard.complete_call(
                budget_reservation,
                run_id=run.view.run_id,
                cost=BriefingCost(
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                    cache_hit_tokens=result.usage.cache_hit_tokens,
                    cache_miss_tokens=result.usage.cache_miss_tokens,
                    cost_usd=Decimal(result.cost_usd),
                ),
                now=self._clock(),
            )
        completed = _append_event(current_run, "completed", "completed", self._clock())
        completed = replace(
            completed,
            lease_owner=None,
            lease_expires_at=None,
            view=replace(
                completed.view,
                claims=result.claims,
                actual_cost_usd=result.cost_usd,
                usage=result.usage,
                failure_code=None,
                cancellable=False,
                provider_call_state="completed",
                accounted_cost_usd=result.cost_usd,
                tool_events=result.tool_events,
                tool_evidence=result.tool_evidence,
            ),
        )
        return self._store.save_run(completed).view

    def _preflight_provider(
        self, draft: StoredAnalysisDraft, run: StoredAnalysisRun
    ) -> None:
        self._runtime.validate(
            intent=draft.intent,
            spend_before=self._monthly_spend_reader(
                PersonalActor(actor_id=draft.actor_id), self._clock()
            ),
        )


def build_agent_workspace(
    *,
    store: AnalysisStore,
    portfolio_store: Any,
    price_reader: Any | None,
    market_adapter: Any | None,
    news_reader: Any | None,
    provider: Any,
    monthly_soft_budget_usd: Decimal,
    monthly_spend_reader: Callable[[PersonalActor, datetime], Decimal],
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    daily_budget_guard: Any | None = None,
) -> AgentAnalysisWorkspace:
    """装配 agent 模式的完整工作区（worker/API runtime 共用）。

    数据源由调用方注入（组合根已装配）：alpaca 未配置时 K 线/现价工具降级，
    INVESTMENT_NEWS_DIR 未配置时新闻工具降级。provider 由调用方注入：worker 传
    DeepSeekAgentChatAdapter，API 进程传可用性 shim（不持有密钥）。
    """
    from .runtime import AgentRuntime
    from .skills import DEFAULT_ACTIVE_SKILLS
    from .tools import build_agent_tools

    tools = build_agent_tools(
        portfolio_store=portfolio_store,
        price_reader=price_reader,
        market_adapter=market_adapter,
        news_reader=news_reader,
    )
    runtime = AgentRuntime(
        provider=provider,
        tools=tools,
        skills=DEFAULT_ACTIVE_SKILLS,
        model=DEEPSEEK_MODEL,
        clock=clock,
        monthly_soft_budget_usd=monthly_soft_budget_usd,
        monthly_spend_reader=lambda actor_id, now: monthly_spend_reader(
            PersonalActor(actor_id=actor_id), now
        ),
    )
    return AgentAnalysisWorkspace(
        store=store,
        runtime=runtime,
        tools=tools,
        skills=DEFAULT_ACTIVE_SKILLS,
        model=DEEPSEEK_MODEL,
        clock=clock,
        monthly_soft_budget_usd=monthly_soft_budget_usd,
        monthly_spend_reader=monthly_spend_reader,
        daily_budget_guard=daily_budget_guard,
    )
