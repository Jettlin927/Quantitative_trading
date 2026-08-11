from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from hashlib import sha256
import os
from pathlib import Path

from .analysis import ScriptedResponsesAdapter
from .agent.domain_tools import DomainToolContext
from .composition import build_analysis_workspace, build_personal_services
from .contracts import (
    LOCAL_PERSONAL_ACTOR,
    TodayContextView,
    TodayEvidenceView,
    TodayFactEventView,
    TodayGapView,
)
from .crypto import load_keyring_file
from .journey import PersonalResearchJourney
from .official_evidence_runtime import load_official_analysis_evidence_reader
from .persistence import PostgresPersonalJourneyStore
from .router import PersonalRuntime
from .security import PersonalAccessConfig
from .synthetic import SyntheticWorkspaceAdapters


@lru_cache(maxsize=1)
def get_personal_runtime() -> PersonalRuntime:
    database_url = os.getenv("PRIVATE_DATABASE_URL", "").strip()
    gateway_path = os.getenv("PERSONAL_GATEWAY_TOKEN_FILE", "").strip()
    keyring_path = os.getenv("PERSONAL_DATA_KEYRING_FILE", "").strip()
    allowed_origins = frozenset(
        origin.strip()
        for origin in os.getenv("PERSONAL_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    )
    if not database_url or not gateway_path or not keyring_path or not allowed_origins:
        return PersonalRuntime.unconfigured()

    try:
        gateway_token = Path(gateway_path).read_text(encoding="utf-8").strip()
        keyring = load_keyring_file(keyring_path)
    except (OSError, ValueError, KeyError, TypeError):
        return PersonalRuntime.unconfigured()
    if not gateway_token:
        return PersonalRuntime.unconfigured()

    services = build_personal_services(
        database_url=database_url,
        keyring=keyring,
        challenge_key=sha256(
            f"personal-purge|{gateway_token}".encode("utf-8")
        ).digest(),
    )
    personal_analysis_provider = os.getenv(
        "PERSONAL_ANALYSIS_PROVIDER", "disabled"
    ).strip()
    provider_available = personal_analysis_provider == "deepseek"
    analysis_mode = os.getenv("PERSONAL_ANALYSIS_MODE", "legacy").strip().lower()
    if analysis_mode == "agent":
        from .agent.workspace import _AgentProviderShim

        provider = _AgentProviderShim(available=provider_available)
        evidence_reader = None
    else:
        provider = ScriptedResponsesAdapter(script=(), available=provider_available)
        evidence_reader = load_official_analysis_evidence_reader(
            query_file=os.getenv("OFFICIAL_ANALYSIS_QUERY_FILE", "").strip(),
            authorization_file=os.getenv(
                "OFFICIAL_ANALYSIS_AUTHORIZATION_FILE", ""
            ).strip(),
            sec_user_agent=os.getenv("SEC_USER_AGENT", "").strip(),
        )
    analyses = build_analysis_workspace(
        services=services,
        mode=analysis_mode,
        provider=provider,
        evidence_reader=evidence_reader,
        monthly_soft_budget_usd=Decimal(
            os.getenv("DEEPSEEK_MONTHLY_SOFT_BUDGET_USD", "5")
        ),
        monthly_spend_reader=lambda request_actor, now: services.analysis_store.monthly_spend_usd(
            request_actor.actor_id, now
        ),
    )
    return PersonalRuntime(
        access=PersonalAccessConfig(
            gateway_token=gateway_token,
            allowed_origins=allowed_origins,
            configured=True,
        ),
        actor=LOCAL_PERSONAL_ACTOR,
        journey=PersonalResearchJourney(
            store=PostgresPersonalJourneyStore(services.session_factory),
            cipher=services.cipher,
            adapters=SyntheticWorkspaceAdapters(provider_available=provider_available),
            portfolio=services.portfolio,
            rulebook=services.rules,
            instrument_states_reader=services.watchlist.open,
            today_context_reader=lambda request_actor: _today_context_view(
                services.domain_tools.invoke(
                    "get_today_context",
                    context=DomainToolContext(
                        actor_id=request_actor.actor_id,
                        granted_permissions=frozenset(
                            {"portfolio:read", "market:read", "news:read"}
                        ),
                        purpose="domain_tool",
                        clock=lambda: datetime.now(timezone.utc),
                    ),
                    arguments={},
                )
            ),
            equity_history_reader=lambda request_actor: services.portfolio.equity_history(
                request_actor, limit=30
            ),
        ),
        portfolio=services.portfolio,
        watchlist=services.watchlist,
        instruments=services.instruments,
        rules=services.rules,
        analyses=analyses,
        analysis_provider=personal_analysis_provider,
        analysis_dispatch_enabled=provider_available,
        analysis_disabled_reason=None if provider_available else "provider_disabled",
    )


def _today_context_view(result) -> TodayContextView:
    data = result.data
    return TodayContextView(
        status=result.status,
        as_of=data.get("as_of"),
        period=data.get("period"),
        field_coverage=result.field_coverage,
        freshness_seconds=result.freshness_seconds,
        fact_events=tuple(
            TodayFactEventView(
                event_id=str(item["event_id"]),
                evidence_id=str(item["evidence_id"]),
                title=str(item["title"]),
                url=str(item["url"]),
                published_at=str(item["published_at"]),
                fetched_at=str(item["fetched_at"]),
                summary=str(item["summary"]),
                content_sha256=str(item["content_sha256"]),
                source=str(item["source"]),
                source_type=str(item["source_type"]),
                sector=str(item["sector"]),
                related_symbols=tuple(item["related_symbols"]),
                confirmation_state=str(item["confirmation_state"]),
            )
            for item in data.get("fact_events", ())
        ),
        evidence=tuple(
            TodayEvidenceView(
                evidence_id=item.evidence_id,
                source=item.source,
                as_of=item.as_of,
                content_sha256=item.content_sha256,
                authorized_fields=item.authorized_fields,
            )
            for item in result.evidence
        ),
        gaps=tuple(TodayGapView(code=item.code, subject=item.subject) for item in result.gaps),
        error_code=result.error_code,
    )
