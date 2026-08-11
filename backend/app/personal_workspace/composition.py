"""个人工作台进程级装配：API 与个人分析 Worker 共用的服务组合。

两个进程各自只读自己的环境变量（API 用 `PRIVATE_DATABASE_URL` + gateway token，
Worker 用 `PERSONAL_ANALYSIS_DATABASE_URL` + DeepSeek 凭据，二者按最小权限使用
不同的 PostgreSQL 角色），但共享的服务骨架——engine/session/cipher、行情 reader、
PortfolioBook（含价格/快照/成交依赖）、规则、标的工作台与分析 store——在这里
组装一次，避免两处装配漂移（缺依赖、重复构建、双份 cipher）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import os
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .analysis import AnalysisWorkspace, PostgresAnalysisStore
from .automatic_briefing_store import PostgresAutomaticBriefingStore
from .contracts import PersonalActor
from .crypto import PersonalDataCipher
from .instrument import (
    InstrumentEvent,
    InstrumentWorkbench,
)
from .market_runtime import (
    PersonalMarketReaders,
    load_personal_market_readers,
)
from .portfolio import (
    PortfolioBook,
    PostgresEquitySnapshotStore,
    PostgresPortfolioStore,
    PostgresPriceObservationStore,
    PostgresRealizedTradeStore,
)
from .rules import (
    InstrumentRuleInputReader,
    ObservationRuleBook,
    PostgresObservationRuleStore,
)
from .watchlist import (
    HoldingWatchState,
    InstrumentStateBook,
    PostgresInstrumentStateStore,
)
from .agent.domain_tools import DomainToolMetrics, DomainToolRegistry
from .agent.evidence import PostgresEvidenceStore
from .agent.fact_news import (
    FACT_NEWS_AUTHORIZATION_SNAPSHOT_ID,
    FACT_NEWS_RETENTION,
    FACT_NEWS_SOURCE,
    InvestmentNewsReader,
)
from .agent.today_tools import (
    AiContextMarketDossierReader,
    InvestmentNewsStructuredSource,
    TodayDomainTools,
)


@dataclass(frozen=True)
class PersonalServices:
    """一次构建、两进程共享的个人工作台服务骨架。"""

    session_factory: Any
    cipher: PersonalDataCipher
    market_readers: PersonalMarketReaders
    portfolio_store: PostgresPortfolioStore
    portfolio: PortfolioBook
    watchlist: InstrumentStateBook
    rules: ObservationRuleBook
    instruments: InstrumentWorkbench
    analysis_store: PostgresAnalysisStore
    automatic_briefing_store: PostgresAutomaticBriefingStore
    evidence_store: PostgresEvidenceStore
    news_reader: Any | None
    domain_tools: DomainToolRegistry
    domain_tool_metrics: DomainToolMetrics


def build_personal_services(
    *,
    database_url: str,
    keyring: Any,
    challenge_key: bytes,
    refresh_news_before_read: bool = False,
) -> PersonalServices:
    """从数据库 URL 与 keyring 装配服务骨架；Alpaca/新闻配置缺失时整体降级。

    challenge_key 由调用方按进程身份派生：API 用 gateway token 派生（供删除确认
    挑战），Worker 用固定派生值（Worker 永不发起删除，只读取）。
    """
    private_engine = create_engine(database_url, pool_pre_ping=True)
    session_factory = sessionmaker(
        bind=private_engine, autoflush=False, expire_on_commit=False
    )
    cipher = PersonalDataCipher(keyring)
    market_readers = load_personal_market_readers(
        credentials_file=os.getenv("ALPACA_CREDENTIALS_FILE", "").strip(),
        authorization_file=os.getenv("ALPACA_AUTHORIZATION_FILE", "").strip(),
    )
    portfolio_store = PostgresPortfolioStore(session_factory, cipher=cipher)
    portfolio = PortfolioBook(
        store=portfolio_store,
        market=market_readers.portfolio,
        prices=PostgresPriceObservationStore(session_factory, cipher=cipher),
        snapshots=PostgresEquitySnapshotStore(session_factory, cipher=cipher),
        trades=PostgresRealizedTradeStore(session_factory, cipher=cipher),
        challenge_key=challenge_key,
    )
    watchlist = InstrumentStateBook(
        store=PostgresInstrumentStateStore(session_factory, cipher=cipher),
        holding_states_reader=lambda actor_id: {
            holding.symbol: HoldingWatchState(
                state=holding.state,
                revision=holding.revision,
                holding_id=str(holding.holding_id),
            )
            for holding in portfolio_store.load(actor_id=actor_id).holdings.values()
        },
    )
    rules = ObservationRuleBook(
        store=PostgresObservationRuleStore(session_factory, cipher=cipher),
        inputs=InstrumentRuleInputReader(market_readers.instrument),
    )

    def read_cost(request_actor: PersonalActor, symbol: str):
        return portfolio.average_cost(request_actor, symbol)

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
    news_dir = os.getenv("INVESTMENT_NEWS_DIR", "").strip()
    news_reader = None
    if news_dir:
        news_reader = InvestmentNewsReader(Path(news_dir))
    evidence_store = PostgresEvidenceStore(
        session_factory,
        cipher=cipher,
        retention_by_authorization={
            (FACT_NEWS_SOURCE, FACT_NEWS_AUTHORIZATION_SNAPSHOT_ID): FACT_NEWS_RETENTION
        },
    )
    domain_tool_metrics = DomainToolMetrics()
    today_domain_tools = TodayDomainTools(
        portfolio_store=portfolio_store,
        watchlist=watchlist,
        news_source=(
            InvestmentNewsStructuredSource(
                news_reader, refresh_before_read=refresh_news_before_read
            )
            if news_reader is not None
            else None
        ),
        evidence_ledger=evidence_store,
        dossier_reader=AiContextMarketDossierReader(market_readers.market),
        rule_attention_reader=lambda actor: rules.attention(actor),
    )
    return PersonalServices(
        session_factory=session_factory,
        cipher=cipher,
        market_readers=market_readers,
        portfolio_store=portfolio_store,
        portfolio=portfolio,
        watchlist=watchlist,
        rules=rules,
        instruments=instruments,
        analysis_store=PostgresAnalysisStore(session_factory, cipher=cipher),
        automatic_briefing_store=PostgresAutomaticBriefingStore(
            session_factory, cipher=cipher
        ),
        evidence_store=evidence_store,
        news_reader=news_reader,
        domain_tools=today_domain_tools.registry(
            observation_recorder=domain_tool_metrics.record
        ),
        domain_tool_metrics=domain_tool_metrics,
    )


def build_analysis_workspace(
    *,
    services: PersonalServices,
    mode: str,
    provider: Any,
    evidence_reader: Any | None = None,
    monthly_soft_budget_usd: Decimal,
    monthly_spend_reader: Callable[[PersonalActor, Any], Decimal] | None = None,
    clock: Callable[[], Any] | None = None,
    daily_budget_guard: Any | None = None,
) -> AnalysisWorkspace:
    """按 PERSONAL_ANALYSIS_MODE 选择 agent 或单发路径，一次实现、两进程共用。

    provider 由调用方注入：API 进程传可用性 shim（不持有密钥），Worker 传真实
    DeepSeek 适配器。evidence_reader 仅单发路径使用；agent 路径由工具提供证据。
    """
    if mode == "agent":
        from .agent.workspace import build_agent_workspace

        kwargs: dict[str, Any] = {
            "store": services.analysis_store,
            "domain_tools": services.domain_tools,
            "provider": provider,
            "monthly_soft_budget_usd": monthly_soft_budget_usd,
            "monthly_spend_reader": monthly_spend_reader
            or (lambda actor, now: Decimal("0")),
            "daily_budget_guard": daily_budget_guard,
        }
        if clock is not None:
            kwargs["clock"] = clock
        return build_agent_workspace(**kwargs)
    kwargs: dict[str, Any] = {
        "store": services.analysis_store,
        "evidence_reader": evidence_reader or (lambda actor, intent: ()),
        "provider": provider,
        "monthly_soft_budget_usd": monthly_soft_budget_usd,
        "daily_budget_guard": daily_budget_guard,
    }
    if monthly_spend_reader is not None:
        kwargs["monthly_spend_reader"] = monthly_spend_reader
    config_revision = getattr(evidence_reader, "config_revision", None)
    if config_revision is not None:
        kwargs["config_revision"] = config_revision
    return AnalysisWorkspace(**kwargs)
