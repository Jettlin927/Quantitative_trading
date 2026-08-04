from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from hashlib import sha256
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .analysis import (
    AnalysisWorkspace,
    PostgresAnalysisStore,
    ScriptedResponsesAdapter,
)
from .contracts import PersonalActor
from .crypto import PersonalDataCipher, load_keyring_file
from .journey import PersonalResearchJourney
from .notebook import PostgresNotebookStore, ResearchNotebook
from .official_evidence_runtime import load_official_analysis_evidence_reader
from .instrument import (
    InstrumentEvent,
    InstrumentWorkbench,
)
from .market_runtime import load_personal_market_readers
from .persistence import PostgresPersonalJourneyStore
from .portfolio import (
    PortfolioBook,
    PostgresPortfolioStore,
)
from .router import PersonalRuntime
from .rules import (
    InstrumentRuleInputReader,
    ObservationRuleBook,
    PostgresObservationRuleStore,
)
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

    private_engine = create_engine(database_url, pool_pre_ping=True)
    session_factory = sessionmaker(
        bind=private_engine, autoflush=False, expire_on_commit=False
    )
    cipher = PersonalDataCipher(keyring)
    market_readers = load_personal_market_readers(
        credentials_file=os.getenv("ALPACA_CREDENTIALS_FILE", "").strip(),
        authorization_file=os.getenv("ALPACA_AUTHORIZATION_FILE", "").strip(),
    )
    portfolio = PortfolioBook(
        store=PostgresPortfolioStore(session_factory, cipher=cipher),
        market=market_readers.portfolio,
        challenge_key=sha256(
            f"personal-purge|{gateway_token}".encode("utf-8")
        ).digest(),
    )
    actor = PersonalActor(actor_id="local-owner")
    rules = ObservationRuleBook(
        store=PostgresObservationRuleStore(session_factory, cipher=cipher),
        inputs=InstrumentRuleInputReader(market_readers.instrument),
    )
    personal_analysis_provider = os.getenv(
        "PERSONAL_ANALYSIS_PROVIDER", "disabled"
    ).strip()
    provider_available = personal_analysis_provider == "deepseek"

    def read_cost(request_actor: PersonalActor, symbol: str):
        for holding in portfolio.open(request_actor).holdings:
            if holding.symbol == symbol and holding.state == "active":
                return Decimal(holding.average_cost)
        return None

    def read_rule_events(request_actor: PersonalActor, symbol: str):
        return tuple(
            InstrumentEvent(
                event_id=item.attention_id,
                track="personal_rule" if item.kind == "rule_hit" else "data_gap",
                event_type=item.kind,
                label=item.label,
                occurred_at=item.as_of,
                evidence_ids=(),
                confirmation_state=item.result,
            )
            for item in rules.attention(request_actor, symbol=symbol)
        )

    instruments = InstrumentWorkbench(
        source=market_readers.instrument,
        cost_reader=read_cost,
        rule_attention_reader=read_rule_events,
        formal_overlay_reader=lambda symbol: (),
    )
    analysis_store = PostgresAnalysisStore(session_factory, cipher=cipher)
    official_evidence_reader = load_official_analysis_evidence_reader(
        query_file=os.getenv("OFFICIAL_ANALYSIS_QUERY_FILE", "").strip(),
        authorization_file=os.getenv(
            "OFFICIAL_ANALYSIS_AUTHORIZATION_FILE", ""
        ).strip(),
        sec_user_agent=os.getenv("SEC_USER_AGENT", "").strip(),
    )
    analyses = AnalysisWorkspace(
        store=analysis_store,
        evidence_reader=official_evidence_reader,
        provider=ScriptedResponsesAdapter(script=(), available=provider_available),
        config_revision=official_evidence_reader.config_revision,
        monthly_soft_budget_usd=Decimal(
            os.getenv("DEEPSEEK_MONTHLY_SOFT_BUDGET_USD", "5")
        ),
        monthly_spend_reader=lambda request_actor, now: analysis_store.monthly_spend_usd(
            request_actor.actor_id, now
        ),
    )
    notebook = ResearchNotebook(
        store=PostgresNotebookStore(session_factory, cipher=cipher),
        analyses=analysis_store,
        challenge_key=sha256(
            f"personal-record-purge|{gateway_token}".encode("utf-8")
        ).digest(),
    )
    return PersonalRuntime(
        access=PersonalAccessConfig(
            gateway_token=gateway_token,
            allowed_origins=allowed_origins,
            configured=True,
        ),
        actor=actor,
        journey=PersonalResearchJourney(
            store=PostgresPersonalJourneyStore(session_factory),
            cipher=cipher,
            adapters=SyntheticWorkspaceAdapters(provider_available=provider_available),
            portfolio=portfolio,
            rulebook=rules,
        ),
        portfolio=portfolio,
        instruments=instruments,
        rules=rules,
        analyses=analyses,
        notebook=notebook,
        analysis_provider=personal_analysis_provider,
        analysis_dispatch_enabled=provider_available,
        analysis_disabled_reason=None if provider_available else "provider_disabled",
    )
