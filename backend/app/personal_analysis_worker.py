from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import signal
from stat import S_IMODE
from threading import Event
from typing import Callable

from .personal_workspace.analysis import AnalysisWorkspace
from .personal_workspace.automatic_briefing import (
    AutomaticBriefingAutomation,
    AutomaticBriefingCoordinator,
    BriefingBudgetPolicy,
)
from .personal_workspace.automatic_briefing_store import (
    ActiveAnalysisBudgetGuard,
)
from .personal_workspace.composition import (
    build_analysis_workspace,
    build_personal_services,
)
from .personal_workspace.candidate_automation import CandidateLifecycleAutomation
from .personal_workspace.contracts import LOCAL_PERSONAL_ACTOR, PersonalActor
from .personal_workspace.crypto import load_keyring_file
from .personal_workspace.rule_automation import (
    HoldingRuleAutomation,
    personal_rule_evaluation_slot,
)


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, repr=False)
class DeepSeekCredentials:
    api_key: str

    def __repr__(self) -> str:
        return "DeepSeekCredentials(api_key=<redacted>)"


def load_deepseek_credentials_file(path: str | Path) -> DeepSeekCredentials:
    credentials_path = Path(path)
    if not credentials_path.is_file():
        raise ValueError("deepseek_credentials_invalid")
    mode = S_IMODE(credentials_path.stat().st_mode)
    if mode & 0o077 or not mode & 0o400:
        raise ValueError("deepseek_credentials_mode_invalid")
    try:
        payload = json.loads(credentials_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("deepseek_credentials_invalid") from None
    if not isinstance(payload, dict) or set(payload) != {"api_key"}:
        raise ValueError("deepseek_credentials_invalid")
    api_key = payload["api_key"]
    if not isinstance(api_key, str) or not api_key.strip() or api_key != api_key.strip():
        raise ValueError("deepseek_credentials_invalid")
    return DeepSeekCredentials(api_key=api_key)


@dataclass(frozen=True)
class PersonalAnalysisWorker:
    workspace: AnalysisWorkspace
    worker_id: str
    rule_automation: HoldingRuleAutomation | None = None
    briefing_automation: object | None = None
    candidate_automation: object | None = None
    actor: PersonalActor = LOCAL_PERSONAL_ACTOR
    rule_slot_reader: Callable[[datetime], str | None] = personal_rule_evaluation_slot

    def run_once(self):
        return self.workspace.run_next(worker_id=self.worker_id)

    def run_forever(
        self,
        *,
        poll_seconds: float,
        stop_event: Event,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("personal_analysis_worker_poll_invalid")
        last_rule_slot = None
        while not stop_event.is_set():
            as_of = clock()
            rule_slot = None
            if self.rule_automation is not None:
                try:
                    rule_slot = self.rule_slot_reader(as_of)
                except Exception:
                    LOGGER.exception("personal_rule_schedule_failed")
            if (
                self.rule_automation is not None
                and rule_slot is not None
                and rule_slot != last_rule_slot
            ):
                try:
                    result = self.rule_automation.run_once(self.actor, as_of=as_of)
                    failed_symbols = getattr(result, "failed_symbols", ())
                    if failed_symbols:
                        LOGGER.warning(
                            "personal_rule_automation_partial_failure count=%s",
                            len(failed_symbols),
                        )
                except Exception:
                    LOGGER.exception("personal_rule_automation_failed")
                last_rule_slot = rule_slot
            if self.run_once() is not None:
                persist_scheduled = getattr(
                    self.briefing_automation, "persist_scheduled_trigger", None
                )
                if persist_scheduled is not None:
                    try:
                        persist_scheduled(
                            self.actor,
                            as_of=as_of,
                            worker_id=self.worker_id,
                        )
                    except Exception:
                        LOGGER.exception("personal_briefing_schedule_failed")
                continue
            if self.candidate_automation is not None:
                try:
                    candidate_result = self.candidate_automation.run_once(
                        self.actor, as_of=as_of
                    )
                    if getattr(candidate_result, "failed_count", 0):
                        LOGGER.warning(
                            "personal_candidate_automation_partial_failure count=%s",
                            candidate_result.failed_count,
                        )
                except Exception:
                    LOGGER.exception("personal_candidate_automation_failed")
            if self.briefing_automation is not None and as_of is not None:
                try:
                    run_pending = getattr(
                        self.briefing_automation, "run_pending_scheduled", None
                    )
                    if run_pending is not None:
                        run_pending(
                            self.actor,
                            as_of=as_of,
                            worker_id=self.worker_id,
                        )
                    briefing_result = self.briefing_automation.run_once(
                        self.actor,
                        as_of=as_of,
                        worker_id=self.worker_id,
                    )
                    failed_count = getattr(briefing_result, "failed_count", 0)
                    if failed_count:
                        LOGGER.warning(
                            "personal_briefing_automation_partial_failure count=%s",
                            failed_count,
                        )
                except Exception:
                    LOGGER.exception("personal_briefing_automation_failed")
            stop_event.wait(poll_seconds)


def _empty_evidence_reader(actor: PersonalActor, intent) -> tuple:
    return ()


def build_personal_analysis_worker_from_environment() -> PersonalAnalysisWorker:
    database_url = os.getenv("PERSONAL_ANALYSIS_DATABASE_URL", "").strip()
    keyring_path = os.getenv("PERSONAL_DATA_KEYRING_FILE", "").strip()
    credentials_path = os.getenv("DEEPSEEK_CREDENTIALS_FILE", "").strip()
    if not database_url or not keyring_path or not credentials_path:
        raise RuntimeError("personal_analysis_worker_unconfigured")
    keyring = load_keyring_file(Path(keyring_path))
    credentials = load_deepseek_credentials_file(credentials_path)
    monthly_budget = Decimal(os.getenv("DEEPSEEK_MONTHLY_SOFT_BUDGET_USD", "5"))
    if monthly_budget <= 0:
        raise RuntimeError("personal_analysis_worker_unconfigured")

    services = build_personal_services(
        database_url=database_url,
        keyring=keyring,
        challenge_key=sha256(b"personal-analysis-worker|no-gateway").digest(),
        refresh_news_before_read=True,
    )
    services.watchlist.bind_legacy_holding_lifecycles(LOCAL_PERSONAL_ACTOR)
    mode = os.getenv("PERSONAL_ANALYSIS_MODE", "legacy").strip().lower()
    briefing_policy = BriefingBudgetPolicy(
        usd_to_cny=Decimal(os.getenv("PERSONAL_AI_USD_TO_CNY", "7.20")),
        fx_snapshot=os.getenv(
            "PERSONAL_AI_FX_SNAPSHOT", "static-2026-08-10"
        ).strip(),
        target_cny=Decimal(os.getenv("PERSONAL_AI_DAILY_TARGET_CNY", "0.50")),
        soft_limit_cny=Decimal(
            os.getenv("PERSONAL_AI_DAILY_SOFT_LIMIT_CNY", "1.00")
        ),
        hard_limit_cny=Decimal(
            os.getenv("PERSONAL_AI_DAILY_HARD_LIMIT_CNY", "5.00")
        ),
    )
    daily_budget_guard = ActiveAnalysisBudgetGuard(
        store=services.automatic_briefing_store,
        policy=briefing_policy.store_policy(),
        lease_seconds=600 if mode == "agent" else 120,
    )
    if mode == "agent":
        from .personal_workspace.agent.deepseek_provider import DeepSeekAgentChatAdapter

        provider = DeepSeekAgentChatAdapter(api_key=credentials.api_key)
        evidence_reader = None
    else:
        from .personal_workspace.analysis import DeepSeekChatAdapter

        provider = DeepSeekChatAdapter(api_key=credentials.api_key)
        evidence_reader = _empty_evidence_reader
    workspace = build_analysis_workspace(
        services=services,
        mode=mode,
        provider=provider,
        evidence_reader=evidence_reader,
        monthly_soft_budget_usd=monthly_budget,
        monthly_spend_reader=lambda actor, now: services.analysis_store.monthly_spend_usd(
            actor.actor_id, now
        ),
        daily_budget_guard=daily_budget_guard,
    )
    from .personal_workspace.agent.completion_runtime import (
        DeepSeekCompletionRuntime,
    )

    briefing_coordinator = AutomaticBriefingCoordinator(
        tools=services.domain_tools,
        runtime=DeepSeekCompletionRuntime(api_key=credentials.api_key),
        store=services.automatic_briefing_store,
        policy=briefing_policy,
        clock=lambda: datetime.now(timezone.utc),
    )
    return PersonalAnalysisWorker(
        workspace=workspace,
        worker_id=os.getenv("PERSONAL_ANALYSIS_WORKER_ID", "personal-analysis-worker-1"),
        rule_automation=HoldingRuleAutomation(
            portfolio=services.portfolio,
            rules=services.rules,
        ),
        briefing_automation=AutomaticBriefingAutomation(
            coordinator=briefing_coordinator,
            tools=services.domain_tools,
        ),
        candidate_automation=CandidateLifecycleAutomation(
            watchlist=services.watchlist,
            tools=services.domain_tools,
        ),
    )


def main() -> None:
    worker = build_personal_analysis_worker_from_environment()
    poll_seconds = float(os.getenv("PERSONAL_ANALYSIS_WORKER_POLL_SECONDS", "5"))
    stop_event = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signum, _frame: stop_event.set())
    worker.run_forever(
        poll_seconds=poll_seconds,
        stop_event=stop_event,
    )


if __name__ == "__main__":
    main()
