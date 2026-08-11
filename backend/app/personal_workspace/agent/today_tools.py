"""今日工作台领域工具：组合事实、结构化新闻、候选与证据读取。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import re
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

from ..contracts import PersonalActor
from ..rule_automation import personal_rule_evaluation_slot
from .domain_tools import (
    DomainToolContext,
    DomainToolRegistry,
    DomainToolResult,
    EvidenceEnvelope,
    ToolGap,
)
from .evidence import (
    EvidenceLedger,
    EvidenceLedgerError,
    EvidenceReadContext,
    EvidenceRecord,
    InMemoryEvidenceStore,
)
from .fact_market import MarketFactService
from .fact_news import (
    FACT_NEWS_AUTHORIZATION_SNAPSHOT_ID,
    FACT_NEWS_RETENTION,
    FACT_NEWS_SOURCE,
    FactNewsReadContext,
    InvestmentNewsStructuredSource,
    NewsSourceSnapshot,
    RawFactNews,
    StructuredNewsSource,
    SYMBOL_SECTORS,
)
from .fact_private import (
    ActorOwnedFactService,
    PRIVATE_FACT_RETENTION_BY_AUTHORIZATION,
)


@dataclass(frozen=True)
class _FactNewsEvent:
    event_id: str
    evidence_id: str
    title: str
    url: str
    published_at: datetime
    fetched_at: datetime
    summary: str
    content_sha256: str
    source: str
    source_type: str
    sector: str
    related_symbols: tuple[str, ...]
    ledger_source: str
    authorization_snapshot_id: str
    persistence: str
    allowed_purposes: frozenset[str]
    confirmation_state: str = "source_summary_unconfirmed"

    def data(self) -> Mapping[str, Any]:
        return {
            "event_id": self.event_id,
            "evidence_id": self.evidence_id,
            "title": self.title,
            "url": self.url,
            "published_at": self.published_at.isoformat(),
            "fetched_at": self.fetched_at.isoformat(),
            "summary": self.summary,
            "content_sha256": self.content_sha256,
            "source": self.source,
            "source_type": self.source_type,
            "sector": self.sector,
            "related_symbols": list(self.related_symbols),
            "confirmation_state": self.confirmation_state,
        }


# get_evidence 在授权 payload 之外返回的稳定证据元数据；它们不写入 payload。
_NEWS_EVIDENCE_METADATA_FIELDS = (
    "event_id",
    "evidence_id",
    "fetched_at",
    "content_sha256",
)


@dataclass(frozen=True)
class _EvidenceRecord:
    envelope: EvidenceEnvelope
    data: Mapping[str, Any]
    expires_at: datetime | None = None
    required_permissions: frozenset[str] = frozenset()


class TodayDomainTools:
    """六个稳定领域工具的服务端实现，不暴露来源或供应商原始 envelope。"""

    def __init__(
        self,
        *,
        portfolio_store: Any,
        watchlist: Any,
        news_source: StructuredNewsSource | None,
        evidence_ledger: EvidenceLedger | None = None,
        evidence_purpose: str = "domain_tool",
        relation_map: Mapping[str, tuple[str, ...]] | None = None,
        market_facts: MarketFactService | None = None,
        rule_attention_reader: Callable[[PersonalActor], tuple[Any, ...]]
        | None = None,
        allowed_news_source_types: frozenset[str] = frozenset(
            {"structured_news"}
        ),
        maximum_event_age: timedelta = timedelta(days=14),
        maximum_fetch_age: timedelta = timedelta(hours=2),
    ) -> None:
        self._portfolio_store = portfolio_store
        self.watchlist = watchlist
        self._news_source = news_source
        self._evidence_ledger = evidence_ledger or InMemoryEvidenceStore(
            retention_by_authorization={
                (FACT_NEWS_SOURCE, FACT_NEWS_AUTHORIZATION_SNAPSHOT_ID): FACT_NEWS_RETENTION,
                **PRIVATE_FACT_RETENTION_BY_AUTHORIZATION,
            }
        )
        self._private_facts = ActorOwnedFactService(self._evidence_ledger)
        self._evidence_purpose = evidence_purpose
        self._relation_map = {
            _symbol(symbol): tuple(_symbol(item) for item in related)
            for symbol, related in (relation_map or _default_relation_map()).items()
        }
        self._market_facts = market_facts
        self._rule_attention_reader = rule_attention_reader or (lambda _actor: ())
        self._allowed_news_source_types = allowed_news_source_types
        self._maximum_event_age = maximum_event_age
        self._maximum_fetch_age = maximum_fetch_age

    def registry(
        self,
        *,
        observation_recorder: Callable[[Any], None] | None = None,
    ) -> DomainToolRegistry:
        return DomainToolRegistry(
            handlers={
                "get_today_context": self.get_today_context,
                "get_symbol_dossier": self.get_symbol_dossier,
                "search_market_news": self.search_market_news,
                "search_web_evidence": self.search_web_evidence,
                "discover_related_candidates": self.discover_related_candidates,
                "get_evidence": self.get_evidence,
            },
            observation_recorder=observation_recorder,
        )

    def get_today_context(
        self, context: DomainToolContext, arguments: dict[str, Any]
    ) -> DomainToolResult:
        now = context.clock()
        portfolio = self._portfolio_store.load(actor_id=context.actor_id)
        if context.requested_name == "get_holdings":
            return self._legacy_holdings(context, portfolio, now)
        watchlist = self.watchlist.open(PersonalActor(context.actor_id))
        active_holdings = tuple(
            sorted(
                holding.symbol
                for holding in portfolio.holdings.values()
                if holding.state == "active"
            )
        )
        followed = tuple(
            item.symbol for item in watchlist.items if item.is_followed
        )
        attention_items = tuple(
            item
            for item in self._rule_attention_reader(PersonalActor(context.actor_id))
            if item.symbol in active_holdings
        )
        if "news:read" in context.granted_permissions:
            news_events, news_gaps, _ = self._read_news(
                context=context, now=now
            )
        else:
            news_events, news_gaps = (), ("source_unauthorized",)
        relevant = tuple(
            event
            for event in news_events
            if set(event.related_symbols) & set((*active_holdings, *followed))
        )
        portfolio_record = self._record_evidence(
            context=context,
            source="personal_portfolio",
            as_of=now,
            data={
                "portfolio_revision": portfolio.revision,
                "instrument_revision": watchlist.revision,
                "active_holding_symbols": list(active_holdings),
                "followed_symbols": list(followed),
            },
            logical_identity=(
                f"today:{portfolio.revision}:{watchlist.revision}"
            ),
        )
        attention_evidence = tuple(
            self._record_evidence(
                context=context,
                source="observation_rule_attention",
                as_of=item.as_of,
                data=_attention_data(item),
                logical_identity=item.attention_id,
            ).envelope
            for item in attention_items
        )
        event_records = tuple(
            self._event_evidence(context, event) for event in relevant
        )
        evidence = (portfolio_record.envelope,) + attention_evidence + tuple(
            record.envelope for record in event_records
        )
        data = {
            "as_of": now.isoformat(),
            "period": _market_period(now),
            "portfolio_revision": portfolio.revision,
            "instrument_revision": watchlist.revision,
            "active_holding_count": len(active_holdings),
            "active_holding_symbols": list(active_holdings),
            "followed_symbols": list(followed),
            "attention_items": [_attention_data(item) for item in attention_items],
            "fact_events": [dict(record.data) for record in event_records],
        }
        if news_gaps:
            return DomainToolResult.partial(
                data=data,
                gaps=_tool_gaps(news_gaps),
                evidence=evidence,
                field_coverage=Decimal("0.67"),
                freshness_seconds=_freshness_seconds(relevant, now),
            )
        return DomainToolResult.success(
            data=data,
            evidence=evidence,
            field_coverage=Decimal("1"),
            freshness_seconds=_freshness_seconds(relevant, now),
        )

    def _legacy_holdings(
        self, context: DomainToolContext, portfolio: Any, now: datetime
    ) -> DomainToolResult:
        holdings = [
            {
                "symbol": holding.symbol,
                "name": holding.name,
                "quantity": str(holding.quantity),
                "average_cost": str(holding.average_cost),
                "currency": "USD",
                "state": holding.state,
            }
            for holding in portfolio.holdings.values()
        ]
        data = {
            "holdings": holdings,
            "count": len(holdings),
            "usd_cash": str(portfolio.usd_cash),
        }
        record = self._record_evidence(
            context=context,
            source="personal_portfolio",
            as_of=now,
            data=data,
            logical_identity=f"holdings:{portfolio.revision}",
        )
        return DomainToolResult.success(
            data=data,
            evidence=(record.envelope,),
            field_coverage=Decimal("1"),
            freshness_seconds=0,
        )

    def get_symbol_dossier(
        self, context: DomainToolContext, arguments: dict[str, Any]
    ) -> DomainToolResult:
        now = context.clock()
        symbol = _symbol(str(arguments["symbol"]))
        can_read_private = "portfolio:read" in context.granted_permissions
        if can_read_private:
            portfolio = self._portfolio_store.load(actor_id=context.actor_id)
            holding = next(
                (
                    item
                    for item in portfolio.holdings.values()
                    if item.symbol == symbol
                ),
                None,
            )
            watchlist = self.watchlist.open(PersonalActor(context.actor_id))
            instrument_state = next(
                (item for item in watchlist.items if item.symbol == symbol), None
            )
        else:
            holding = None
            instrument_state = None
        if context.requested_name == "get_kline":
            news_events, news_gaps = (), ()
        elif "news:read" in context.granted_permissions:
            news_events, news_gaps, _ = self._read_news(
                context=context, now=now, symbols=(symbol,)
            )
        else:
            news_events, news_gaps = (), ("source_unauthorized",)
        dossier = None
        market_records: tuple[_EvidenceRecord, ...] = ()
        market_source_health: str | None = None
        gaps = list(news_gaps)
        bar_days = int(arguments.get("bar_days", 90))
        bar_limit = int(arguments.get("bar_limit", 120))
        if self._market_facts is not None:
            try:
                read_market = (
                    self._market_facts.read_bars
                    if context.requested_name == "get_kline"
                    else self._market_facts.read_dossier
                )
                market_fact = read_market(
                    context=self._ledger_context(context, now),
                    symbol=symbol,
                    bar_days=bar_days,
                    bar_limit=bar_limit,
                )
                dossier = _plain_data(market_fact.data)
                market_records = tuple(
                    _ledger_evidence_record(record) for record in market_fact.records
                )
                market_source_health = market_fact.source_health
                gaps.extend(market_fact.gaps)
            except PermissionError:
                gaps.append("source_unauthorized")
            except (EvidenceLedgerError, RuntimeError, ValueError, OSError):
                gaps.append("market_dossier_unavailable")
        else:
            gaps.append("market_dossier_unavailable")
        state_data = {
            "available": can_read_private,
            "holding": (
                holding is not None and holding.state == "active"
                if can_read_private
                else None
            ),
            "holding_state": holding.state if holding is not None else None,
            "followed": (
                instrument_state.is_followed
                if instrument_state is not None
                else False if can_read_private else None
            ),
            "candidate_status": instrument_state.candidate_status
            if instrument_state is not None
            else None,
        }
        evidence_items: list[EvidenceEnvelope] = []
        if can_read_private:
            evidence_items.append(
                self._record_evidence(
                    context=context,
                    source="personal_instrument_state",
                    as_of=now,
                    data={"symbol": symbol, "states": state_data},
                    logical_identity=f"instrument:{symbol}:{portfolio.revision}",
                ).envelope
            )
        evidence_items.extend(record.envelope for record in market_records)
        event_records = tuple(
            self._event_evidence(context, event) for event in news_events
        )
        evidence_items.extend(record.envelope for record in event_records)
        evidence = tuple(evidence_items)
        data = {
            "symbol": symbol,
            "states": state_data,
            "market": dossier,
            "fact_events": [dict(record.data) for record in event_records],
        }
        if not evidence:
            return DomainToolResult.unavailable(
                gaps[0] if gaps else "tool_unavailable", symbol
            )
        if market_source_health == "stale":
            return DomainToolResult.stale(
                data=data,
                gaps=_tool_gaps((*gaps, "source_stale")),
                evidence=evidence,
                field_coverage=Decimal("0.9"),
                freshness_seconds=_freshness_seconds(news_events, now),
            )
        if gaps:
            return DomainToolResult.partial(
                data=data,
                gaps=_tool_gaps(gaps),
                evidence=evidence,
                field_coverage=Decimal("0.67") if dossier is None else Decimal("0.9"),
                freshness_seconds=_freshness_seconds(news_events, now),
            )
        return DomainToolResult.success(
            data=data,
            evidence=evidence,
            field_coverage=Decimal("1"),
            freshness_seconds=_freshness_seconds(news_events, now),
        )

    def search_market_news(
        self, context: DomainToolContext, arguments: dict[str, Any]
    ) -> DomainToolResult:
        now = context.clock()
        symbols = tuple(
            _symbol(value) for value in arguments.get("symbols", ())
        )
        events, gaps, source_snapshot = self._read_news(
            context=context,
            now=now,
            symbols=symbols,
            query=str(arguments.get("query", "")).strip(),
            sector=str(arguments.get("sector", "")).strip(),
        )
        limit = int(arguments.get("limit", 8))
        events = events[:limit]
        if not events:
            if gaps:
                return DomainToolResult.unavailable(
                    gaps[0], "structured_news"
                )
            if source_snapshot is None:
                return DomainToolResult.unavailable(
                    "source_unavailable", "structured_news"
                )
            snapshot_record = self._news_snapshot_evidence(
                context, events, now, source_snapshot
            )
            return DomainToolResult.success(
                data={"items": [], "count": 0},
                evidence=(snapshot_record.envelope,),
                field_coverage=Decimal("1"),
                freshness_seconds=max(
                    0,
                    int(
                        (
                            now
                            - (
                                source_snapshot.fetched_at
                                or snapshot_record.envelope.as_of
                            )
                        ).total_seconds()
                    ),
                ),
            )
        event_records = tuple(
            self._event_evidence(context, event) for event in events
        )
        evidence = tuple(record.envelope for record in event_records)
        data = {
            "items": [dict(record.data) for record in event_records],
            "count": len(event_records),
        }
        freshness = _freshness_seconds(events, now)
        if gaps:
            return DomainToolResult.partial(
                data=data,
                gaps=_tool_gaps(gaps),
                evidence=evidence,
                field_coverage=Decimal(len(events))
                / Decimal(len(events) + len(gaps)),
                freshness_seconds=freshness,
            )
        return DomainToolResult.success(
            data=data,
            evidence=evidence,
            field_coverage=Decimal("1"),
            freshness_seconds=freshness,
        )

    @staticmethod
    def search_web_evidence(
        context: DomainToolContext, arguments: dict[str, Any]
    ) -> DomainToolResult:
        return DomainToolResult.unavailable(
            "hosted_web_search_unavailable", "search_web_evidence"
        )

    def discover_related_candidates(
        self, context: DomainToolContext, arguments: dict[str, Any]
    ) -> DomainToolResult:
        now = context.clock()
        subjects = tuple(_symbol(item) for item in arguments["subject_ids"])
        events, gaps, source_snapshot = self._read_news(
            context=context, now=now
        )
        relation_records: dict[str, list[_EvidenceRecord]] = {}
        for subject in subjects:
            for candidate in self._relation_map.get(subject, ()):
                if candidate in subjects:
                    continue
                relation_records.setdefault(candidate, []).append(
                    self._relation_evidence(context, subject, candidate, now)
                )
        candidates = []
        evidence_records: list[_EvidenceRecord] = []
        if not gaps and source_snapshot is not None:
            evidence_records.append(
                self._news_snapshot_evidence(
                    context, events, now, source_snapshot
                )
            )
        evidence_records.extend(
            record
            for records in relation_records.values()
            for record in records
        )
        for candidate, relations in sorted(relation_records.items()):
            facts = tuple(
                event for event in events if candidate in event.related_symbols
            )[:3]
            if not relations or not facts:
                continue
            fact_records = tuple(
                self._event_evidence(context, event) for event in facts
            )
            evidence_records.extend(fact_records)
            candidates.append(
                {
                    "symbol": candidate,
                    "relation_evidence_ids": [
                        record.envelope.evidence_id for record in relations
                    ],
                    "fact_evidence_ids": [
                        record.envelope.evidence_id for record in fact_records
                    ],
                    "relation_evidence": [
                        _relation_evidence_view(record) for record in relations
                    ],
                    "fact_evidence": [
                        _fact_evidence_view(record) for record in fact_records
                    ],
                    "latest_fact_at": max(
                        event.published_at for event in facts
                    ).isoformat(),
                    "ranking_basis": "relation_and_recent_fact",
                    "auto_followed": False,
                }
            )
        data = {"subject_ids": list(subjects), "candidates": candidates}
        evidence = tuple(record.envelope for record in evidence_records)
        if gaps and not evidence:
            return DomainToolResult.unavailable(gaps[0], "candidate_sources")
        if gaps:
            return DomainToolResult.partial(
                data=data,
                gaps=_tool_gaps(gaps),
                evidence=evidence,
                field_coverage=Decimal("0.5") if not candidates else Decimal("0.8"),
                freshness_seconds=_freshness_seconds(events, now),
            )
        return DomainToolResult.success(
            data=data,
            evidence=evidence,
            field_coverage=Decimal("1"),
            freshness_seconds=_freshness_seconds(events, now),
        )

    def get_evidence(
        self, context: DomainToolContext, arguments: dict[str, Any]
    ) -> DomainToolResult:
        evidence_id = str(arguments["evidence_id"])
        now = context.clock()
        try:
            persisted = self._evidence_ledger.read(
                self._ledger_context(context, now), evidence_id
            )
        except EvidenceLedgerError as exc:
            if exc.code != "evidence_not_found":
                return DomainToolResult.unavailable(
                    _evidence_gap_code(exc.code), evidence_id
                )
        else:
            if persisted.expires_at is not None and now >= persisted.expires_at:
                return DomainToolResult.unavailable(
                    "evidence_expired", evidence_id
                )
            if persisted.payload is None:
                return DomainToolResult.unavailable(
                    "evidence_payload_not_retained", evidence_id
                )
            data = _persisted_evidence_data(persisted)
            authorized_fields = tuple(data)
            envelope = EvidenceEnvelope(
                evidence_id=persisted.evidence_id,
                source=str(data.get("source", persisted.source)),
                as_of=persisted.published_at or persisted.fetched_at,
                content_sha256=persisted.content_sha256,
                authorized_fields=authorized_fields,
            )
            return DomainToolResult.success(
                data=data,
                evidence=(envelope,),
                field_coverage=Decimal("1"),
                freshness_seconds=max(
                    0, int((now - persisted.fetched_at).total_seconds())
                ),
            )
        return DomainToolResult.unavailable("evidence_not_found", evidence_id)

    def _read_news(
        self,
        *,
        context: DomainToolContext,
        now: datetime,
        symbols: tuple[str, ...] = (),
        query: str = "",
        sector: str = "",
    ) -> tuple[
        tuple[_FactNewsEvent, ...], tuple[str, ...], NewsSourceSnapshot | None
    ]:
        if self._news_source is None:
            return (), ("source_unavailable",), None
        try:
            snapshot = self._news_source.read(
                context=FactNewsReadContext(
                    permissions=context.granted_permissions,
                    purpose=self._evidence_purpose,
                ),
                now=now,
            )
        except (RuntimeError, OSError, ValueError):
            return (), ("source_unavailable",), None
        gaps = [gap.code for gap in snapshot.gaps]
        snapshot_fetched_at = snapshot.fetched_at
        if snapshot_fetched_at is None and snapshot.items:
            snapshot_fetched_at = max(item.fetched_at for item in snapshot.items)
        if snapshot_fetched_at is None:
            if not gaps:
                gaps.append("source_unavailable")
        elif snapshot_fetched_at.tzinfo is None or snapshot_fetched_at > now:
            gaps.append("source_contract_invalid")
        elif now >= snapshot_fetched_at + self._maximum_fetch_age:
            gaps.append("source_stale")
        else:
            snapshot = replace(snapshot, fetched_at=snapshot_fetched_at)
        deduplicated: dict[str, _FactNewsEvent] = {}
        for raw in snapshot.items:
            validation = self._validate_news(raw, now)
            if validation is not None:
                gaps.append(validation)
                continue
            try:
                event = _normalize_event(raw, snapshot=snapshot)
            except ValueError:
                gaps.append("source_contract_invalid")
                continue
            existing = deduplicated.get(event.event_id)
            if existing is not None:
                event = _merge_event(existing, event)
            deduplicated[event.event_id] = event
        normalized_query = query.lower()
        requested_symbols = set(symbols)
        events = []
        for event in deduplicated.values():
            if sector and event.sector != sector:
                continue
            if requested_symbols and not (
                requested_symbols & set(event.related_symbols)
            ):
                continue
            if normalized_query and normalized_query not in " ".join(
                (event.title, event.summary, event.source)
            ).lower():
                continue
            events.append(event)
        events.sort(
            key=lambda item: (item.published_at, item.event_id), reverse=True
        )
        return tuple(events), tuple(dict.fromkeys(gaps)), snapshot

    def _validate_news(
        self, raw: RawFactNews, now: datetime
    ) -> str | None:
        if raw.source_type not in self._allowed_news_source_types:
            return "source_unauthorized"
        if raw.published_at.tzinfo is None or raw.fetched_at.tzinfo is None:
            return "source_contract_invalid"
        if raw.published_at > now or raw.fetched_at > now:
            return "source_contract_invalid"
        if now - raw.fetched_at > self._maximum_fetch_age:
            return "source_stale"
        if now - raw.published_at >= self._maximum_event_age:
            return "event_expired"
        if not raw.title.strip() or not raw.summary.strip() or not raw.source.strip():
            return "source_contract_invalid"
        parsed = urlsplit(raw.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "source_contract_invalid"
        return None

    def _event_evidence(
        self, context: DomainToolContext, event: _FactNewsEvent
    ) -> _EvidenceRecord:
        expires_at = min(
            event.published_at + self._maximum_event_age,
            event.fetched_at + self._maximum_fetch_age,
        )
        payload = _event_payload(event)
        stored = self._evidence_ledger.put(
            self._ledger_context(context, context.clock()),
            EvidenceRecord(
                evidence_id=event.evidence_id,
                logical_identity=event.event_id,
                scope="actor",
                source=event.ledger_source,
                content_sha256=event.content_sha256,
                authorized_fields=tuple(payload),
                required_permissions=frozenset({"news:read"}),
                allowed_purposes=event.allowed_purposes,
                authorization_snapshot_id=event.authorization_snapshot_id,
                observed_at=None,
                published_at=event.published_at,
                effective_at=None,
                available_from=event.fetched_at,
                fetched_at=event.fetched_at,
                verified_at=None,
                expires_at=expires_at,
                persistence=event.persistence,  # type: ignore[arg-type]
                payload=payload,
            ),
        )
        now = context.clock()
        if stored.expires_at is not None and now >= stored.expires_at:
            version_digest = _sha256(
                {
                    "authorization_snapshot_id": event.authorization_snapshot_id,
                    "content_sha256": event.content_sha256,
                    "fetched_at": event.fetched_at.isoformat(),
                }
            )
            event = replace(
                event,
                evidence_id=f"news:{event.content_sha256[:16]}:{version_digest[:24]}",
            )
            stored = self._evidence_ledger.put(
                self._ledger_context(context, now),
                EvidenceRecord(
                    evidence_id=event.evidence_id,
                    logical_identity=event.event_id,
                    scope="actor",
                    source=event.ledger_source,
                    content_sha256=event.content_sha256,
                    authorized_fields=tuple(payload),
                    required_permissions=frozenset({"news:read"}),
                    allowed_purposes=event.allowed_purposes,
                    authorization_snapshot_id=event.authorization_snapshot_id,
                    observed_at=None,
                    published_at=event.published_at,
                    effective_at=None,
                    available_from=event.fetched_at,
                    fetched_at=event.fetched_at,
                    verified_at=None,
                    expires_at=expires_at,
                    persistence=event.persistence,  # type: ignore[arg-type]
                    payload=payload,
                ),
            )
        persisted_event = replace(
            event,
            evidence_id=stored.evidence_id,
            fetched_at=stored.fetched_at,
        )
        data = dict(persisted_event.data())
        record = _EvidenceRecord(
            envelope=EvidenceEnvelope(
                evidence_id=stored.evidence_id,
                source=persisted_event.source,
                as_of=stored.published_at or stored.fetched_at,
                content_sha256=stored.content_sha256,
                authorized_fields=tuple(data),
            ),
            data=data,
            expires_at=stored.expires_at,
            required_permissions=frozenset({"news:read"}),
        )
        return record

    def _ledger_context(
        self, context: DomainToolContext, now: datetime
    ) -> EvidenceReadContext:
        return EvidenceReadContext(
            actor_id=context.actor_id,
            permissions=context.granted_permissions,
            purpose=self._evidence_purpose,
            now=now,
        )

    def _relation_evidence(
        self,
        context: DomainToolContext,
        subject: str,
        candidate: str,
        now: datetime,
    ) -> _EvidenceRecord:
        return self._record_evidence(
            context=context,
            source="instrument_relation_map",
            as_of=now,
            data={
                "subject_symbol": subject,
                "candidate_symbol": candidate,
                "relation": "configured_market_relation",
            },
            logical_identity=f"{subject}:{candidate}",
        )

    def _news_snapshot_evidence(
        self,
        context: DomainToolContext,
        events: tuple[_FactNewsEvent, ...],
        now: datetime,
        source_snapshot: NewsSourceSnapshot,
    ) -> _EvidenceRecord:
        fetched_at = source_snapshot.fetched_at
        if fetched_at is None:
            raise EvidenceLedgerError("source_unavailable")
        data = {
            "as_of": fetched_at.isoformat(),
            "event_count": len(events),
            "event_ids": [event.event_id for event in events],
        }
        digest = _sha256(data)
        evidence_id = f"news-snapshot:{digest[:24]}"
        expires_at = fetched_at + self._maximum_fetch_age
        self._evidence_ledger.put(
            self._ledger_context(context, now),
            EvidenceRecord(
                evidence_id=evidence_id,
                logical_identity=evidence_id,
                scope="actor",
                source=source_snapshot.source,
                content_sha256=digest,
                authorized_fields=tuple(data),
                required_permissions=frozenset({"news:read"}),
                allowed_purposes=source_snapshot.allowed_purposes,
                authorization_snapshot_id=source_snapshot.authorization_snapshot_id,
                observed_at=fetched_at,
                published_at=None,
                effective_at=None,
                available_from=fetched_at,
                fetched_at=fetched_at,
                verified_at=None,
                expires_at=expires_at,
                persistence=source_snapshot.persistence,
                payload=data,
            ),
        )
        return _EvidenceRecord(
            envelope=EvidenceEnvelope(
                evidence_id=evidence_id,
                source="structured_news_snapshot",
                as_of=fetched_at,
                content_sha256=digest,
                authorized_fields=tuple(data),
            ),
            data=data,
            expires_at=expires_at,
            required_permissions=frozenset({"news:read"}),
        )

    def _record_evidence(
        self,
        *,
        context: DomainToolContext,
        source: str,
        as_of: datetime,
        data: Mapping[str, Any],
        logical_identity: str,
    ) -> _EvidenceRecord:
        stored = self._private_facts.record(
            context=self._ledger_context(context, context.clock()),
            source=source,
            logical_identity=logical_identity,
            payload=data,
            observed_at=as_of,
        )
        return _ledger_evidence_record(stored)


def _normalize_event(
    raw: RawFactNews, *, snapshot: NewsSourceSnapshot
) -> _FactNewsEvent:
    url = _canonical_url(raw.url)
    event_digest = _sha256({"url": url})
    event = _FactNewsEvent(
        event_id=f"event:{event_digest[:24]}",
        evidence_id="",
        title=raw.title.strip(),
        url=url,
        published_at=raw.published_at,
        fetched_at=raw.fetched_at,
        summary=raw.summary.strip(),
        content_sha256="",
        source=raw.source.strip(),
        source_type=raw.source_type,
        sector=raw.sector,
        related_symbols=tuple(sorted({_symbol(item) for item in raw.related_symbols})),
        ledger_source=snapshot.source,
        authorization_snapshot_id=snapshot.authorization_snapshot_id,
        persistence=snapshot.persistence,
        allowed_purposes=snapshot.allowed_purposes,
    )
    return _with_event_identity(event)


def _ledger_evidence_record(record: EvidenceRecord) -> _EvidenceRecord:
    if record.payload is None:
        raise EvidenceLedgerError("evidence_payload_not_retained")
    data = _plain_data(record.payload)
    as_of = (
        record.observed_at
        or record.published_at
        or record.effective_at
        or record.available_from
        or record.fetched_at
    )
    return _EvidenceRecord(
        envelope=EvidenceEnvelope(
            evidence_id=record.evidence_id,
            source=record.source,
            as_of=as_of,
            content_sha256=record.content_sha256,
            authorized_fields=tuple(data),
        ),
        data=data,
        expires_at=record.expires_at,
        required_permissions=record.required_permissions,
    )


def _relation_evidence_view(record: _EvidenceRecord) -> dict[str, Any]:
    subject = str(record.data["subject_symbol"])
    candidate = str(record.data["candidate_symbol"])
    return {
        "evidence_id": record.envelope.evidence_id,
        "title": f"{subject} → {candidate}",
        "summary": "系统关系图谱中配置的市场关联；它是候选发现线索，不代表因果结论。",
        "source": record.envelope.source,
        "as_of": record.envelope.as_of.isoformat(),
        "url": None,
    }


def _fact_evidence_view(record: _EvidenceRecord) -> dict[str, Any]:
    return {
        "evidence_id": record.envelope.evidence_id,
        "title": record.data["title"],
        "summary": record.data["summary"],
        "source": record.data["source"],
        "as_of": record.data["published_at"],
        "url": record.data["url"],
    }


def _merge_event(left: _FactNewsEvent, right: _FactNewsEvent) -> _FactNewsEvent:
    primary = min(
        (left, right),
        key=lambda item: (
            item.title,
            item.summary,
            item.source,
            item.sector,
            item.published_at,
        ),
    )
    return _with_event_identity(
        _FactNewsEvent(
            event_id=left.event_id,
            evidence_id="",
            title=primary.title,
            url=primary.url,
            published_at=min(left.published_at, right.published_at),
            fetched_at=max(left.fetched_at, right.fetched_at),
            summary=primary.summary,
            content_sha256="",
            source=primary.source,
            source_type=primary.source_type,
            sector=primary.sector,
            related_symbols=tuple(
                sorted(set(left.related_symbols) | set(right.related_symbols))
            ),
            ledger_source=primary.ledger_source,
            authorization_snapshot_id=primary.authorization_snapshot_id,
            persistence=primary.persistence,
            allowed_purposes=primary.allowed_purposes,
        )
    )


def _event_payload(event: _FactNewsEvent) -> dict[str, Any]:
    return {
        "title": event.title,
        "url": event.url,
        "published_at": event.published_at.isoformat(),
        "summary": event.summary,
        "source": event.source,
        "source_type": event.source_type,
        "sector": event.sector,
        "related_symbols": list(event.related_symbols),
        "confirmation_state": event.confirmation_state,
    }


def _with_event_identity(event: _FactNewsEvent) -> _FactNewsEvent:
    content_sha256 = _sha256(_event_payload(event))
    return replace(
        event,
        evidence_id=f"news:{content_sha256[:24]}",
        content_sha256=content_sha256,
    )


def _persisted_evidence_data(record: EvidenceRecord) -> dict[str, Any]:
    payload = dict(record.payload or {})
    if not record.evidence_id.startswith("news:"):
        return payload
    metadata = dict(
        zip(
            _NEWS_EVIDENCE_METADATA_FIELDS,
            (
                record.logical_identity,
                record.evidence_id,
                record.fetched_at.isoformat(),
                record.content_sha256,
            ),
            strict=True,
        )
    )
    return {**payload, **metadata}


def _evidence_gap_code(code: str) -> str:
    return {
        "evidence_permission_denied": "source_unauthorized",
        "evidence_purpose_denied": "source_purpose_denied",
    }.get(code, code)


def _canonical_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, "")
    )


def _symbol(value: str) -> str:
    normalized = value.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", normalized):
        raise ValueError("unsupported_instrument")
    return normalized


def _sha256(value: Any) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=lambda item: item.isoformat()
            if isinstance(item, datetime)
            else str(item),
        ).encode("utf-8")
    ).hexdigest()


def _tool_gaps(codes: list[str] | tuple[str, ...]) -> tuple[ToolGap, ...]:
    return tuple(
        ToolGap(code, "structured_news") for code in dict.fromkeys(codes)
    )


def _freshness_seconds(
    events: tuple[_FactNewsEvent, ...], now: datetime
) -> int | None:
    if not events:
        return None
    return max(0, int((now - max(item.fetched_at for item in events)).total_seconds()))


def _market_period(now: datetime) -> str:
    slot = personal_rule_evaluation_slot(now)
    if slot is None:
        return "market_closed"
    session = slot.split(":", 1)[1]
    return {
        "pre_market": "pre_market",
        "regular_market": "market_hours",
        "post_market": "after_market",
    }[session]


def _plain_data(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value


def _attention_data(item: Any) -> dict[str, Any]:
    return {
        "attention_id": item.attention_id,
        "kind": item.kind,
        "symbol": item.symbol,
        "label": item.label,
        "result": item.result,
        "as_of": item.as_of.isoformat(),
        "reason_code": item.reason_code,
        "priority": item.priority,
    }


def _default_relation_map() -> Mapping[str, tuple[str, ...]]:
    result: dict[str, set[str]] = {}
    for symbol, sectors in SYMBOL_SECTORS.items():
        for candidate, candidate_sectors in SYMBOL_SECTORS.items():
            if symbol != candidate and set(sectors) & set(candidate_sectors):
                result.setdefault(symbol, set()).add(candidate)
    return {
        symbol: tuple(sorted(candidates))
        for symbol, candidates in result.items()
    }
