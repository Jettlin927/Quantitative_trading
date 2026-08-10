"""今日工作台领域工具：组合事实、结构化新闻、候选与证据读取。"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
from threading import Lock
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from backend.app.market_observation.alpaca import AlpacaMarketObservationAdapter

from ..contracts import PersonalActor
from .domain_tools import (
    DomainToolContext,
    DomainToolRegistry,
    DomainToolResult,
    EvidenceEnvelope,
    ToolGap,
)
from .tools_impl.news import InvestmentNewsReader, SYMBOL_SECTORS


@dataclass(frozen=True)
class RawFactNews:
    title: str
    url: str
    published_at: datetime
    fetched_at: datetime
    summary: str
    source: str
    source_type: str
    sector: str
    related_symbols: tuple[str, ...]


@dataclass(frozen=True)
class NewsSourceSnapshot:
    items: tuple[RawFactNews, ...]
    gaps: tuple[str, ...] = ()


class StructuredNewsSource(Protocol):
    def read(self, *, now: datetime) -> NewsSourceSnapshot: ...


class InvestmentNewsStructuredSource:
    """将受控 investment-news 快照转换为稳定的结构化新闻来源合同。"""

    def __init__(self, reader: InvestmentNewsReader) -> None:
        self._reader = reader

    def read(self, *, now: datetime) -> NewsSourceSnapshot:
        try:
            self._reader.refresh(now=now)
            payload = self._reader.load()
            fetched_at = datetime.fromtimestamp(
                (self._reader.checkout_dir / "data.js").stat().st_mtime,
                tz=timezone.utc,
            )
        except (RuntimeError, OSError, ValueError):
            return NewsSourceSnapshot(items=(), gaps=("source_unavailable",))
        industries = payload.get("industries")
        if not isinstance(industries, list):
            return NewsSourceSnapshot(
                items=(), gaps=("source_contract_invalid",)
            )
        items: list[RawFactNews] = []
        gaps: list[str] = []
        for industry in industries:
            if not isinstance(industry, Mapping):
                gaps.append("source_contract_invalid")
                continue
            sector = str(industry.get("key", "")).strip()
            raw_items = industry.get("items")
            if not isinstance(raw_items, list):
                gaps.append("source_contract_invalid")
                continue
            for item in raw_items:
                try:
                    normalized = _raw_investment_news_item(
                        item,
                        sector=sector,
                        fetched_at=fetched_at,
                    )
                except (OSError, OverflowError, ValueError):
                    normalized = None
                if normalized is None:
                    gaps.append("source_contract_invalid")
                else:
                    items.append(normalized)
        return NewsSourceSnapshot(
            items=tuple(items), gaps=tuple(dict.fromkeys(gaps))
        )


class AiContextMarketDossierReader:
    """通过专用 ai_context 授权读取身份与日线，不复用 display 链路。"""

    def __init__(self, adapter: AlpacaMarketObservationAdapter | None) -> None:
        self._adapter = adapter

    def __call__(
        self,
        actor: PersonalActor,
        symbol: str,
        now: datetime,
        bar_days: int,
        bar_limit: int,
    ) -> Mapping[str, Any]:
        if self._adapter is None:
            raise RuntimeError("market_dossier_unavailable")
        identity = self._adapter.observe_asset(
            symbol, purpose="ai_context", fetched_at=now
        )
        bars = self._adapter.observe_daily_bars(
            symbol,
            start_date=now.date() - timedelta(days=bar_days),
            end_date=now.date(),
            fetched_at=now,
            purpose="ai_context",
        )
        selected = (
            bars.provider_adjusted
            if bars.provider_adjusted.availability == "available"
            else bars.raw
        )
        if identity.availability != "available" or identity.value is None:
            raise RuntimeError(identity.reason_code or "asset_identity_unavailable")
        if selected.availability != "available" or selected.value is None:
            raise RuntimeError(selected.reason_code or "daily_bars_unavailable")
        selected_bars = selected.value[-bar_limit:]
        return {
            "symbol": symbol,
            "name": identity.value.name,
            "asset_class": identity.value.asset_class,
            "adjustment": (
                "provider_adjusted"
                if selected is bars.provider_adjusted
                else "raw"
            ),
            "as_of": selected.as_of.isoformat() if selected.as_of else None,
            "source_health": selected.source_health,
            "bars": [
                {
                    "date": bar.trade_date.isoformat(),
                    "open": str(bar.open),
                    "high": str(bar.high),
                    "low": str(bar.low),
                    "close": str(bar.close),
                    "volume": bar.volume,
                }
                for bar in selected_bars
            ],
            "count": len(selected_bars),
            "authorization_snapshot_ids": sorted(
                {
                    identity.provenance.authorization_snapshot_id,
                    selected.provenance.authorization_snapshot_id,
                }
            ),
        }


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


@dataclass(frozen=True)
class _EvidenceRecord:
    envelope: EvidenceEnvelope
    data: Mapping[str, Any]
    owner_actor_id: str | None = None
    expires_at: datetime | None = None
    requires_news_source: bool = False
    required_permissions: frozenset[str] = frozenset()


class _EvidenceCatalog:
    def __init__(self, *, maximum_records: int = 10_000) -> None:
        self._lock = Lock()
        self._maximum_records = maximum_records
        self._records: OrderedDict[
            tuple[str | None, str], _EvidenceRecord
        ] = OrderedDict()

    def put(self, record: _EvidenceRecord) -> None:
        key = (record.owner_actor_id, record.envelope.evidence_id)
        with self._lock:
            self._records[key] = record
            self._records.move_to_end(key)
            while len(self._records) > self._maximum_records:
                self._records.popitem(last=False)

    def get(
        self, evidence_id: str, *, actor_id: str
    ) -> _EvidenceRecord | None:
        with self._lock:
            return self._records.get(
                (actor_id, evidence_id)
            ) or self._records.get((None, evidence_id))


class TodayDomainTools:
    """六个稳定领域工具的服务端实现，不暴露来源或供应商原始 envelope。"""

    def __init__(
        self,
        *,
        portfolio_store: Any,
        watchlist: Any,
        news_source: StructuredNewsSource | None,
        relation_map: Mapping[str, tuple[str, ...]] | None = None,
        dossier_reader: Callable[
            [PersonalActor, str, datetime, int, int], Any
        ]
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
        self._relation_map = {
            _symbol(symbol): tuple(_symbol(item) for item in related)
            for symbol, related in (relation_map or _default_relation_map()).items()
        }
        self._dossier_reader = dossier_reader
        self._allowed_news_source_types = allowed_news_source_types
        self._maximum_event_age = maximum_event_age
        self._maximum_fetch_age = maximum_fetch_age
        self._catalog = _EvidenceCatalog()

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
        now = _argument_as_of(arguments, context.clock())
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
        if "news:read" in context.granted_permissions:
            news_events, news_gaps = self._read_news(now=now)
        else:
            news_events, news_gaps = (), ("source_unauthorized",)
        relevant = tuple(
            event
            for event in news_events
            if set(event.related_symbols) & set((*active_holdings, *followed))
        )
        portfolio_record = self._record_evidence(
            source="personal_portfolio",
            as_of=now,
            data={
                "portfolio_revision": portfolio.revision,
                "instrument_revision": watchlist.revision,
                "active_holding_symbols": list(active_holdings),
                "followed_symbols": list(followed),
            },
            authorized_fields=(
                "portfolio_revision",
                "instrument_revision",
                "active_holding_symbols",
                "followed_symbols",
            ),
            prefix="today",
            owner_actor_id=context.actor_id,
            required_permissions=frozenset({"portfolio:read"}),
        )
        evidence = (portfolio_record.envelope,) + tuple(
            self._event_evidence(event).envelope for event in relevant
        )
        data = {
            "as_of": now.isoformat(),
            "period": _market_period(now),
            "portfolio_revision": portfolio.revision,
            "instrument_revision": watchlist.revision,
            "active_holding_count": len(active_holdings),
            "active_holding_symbols": list(active_holdings),
            "followed_symbols": list(followed),
            "fact_events": [event.data() for event in relevant],
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
            source="personal_portfolio",
            as_of=now,
            data=data,
            authorized_fields=("holdings", "count", "usd_cash"),
            prefix="legacy-holdings",
            owner_actor_id=context.actor_id,
            required_permissions=frozenset({"portfolio:read"}),
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
        if "news:read" in context.granted_permissions:
            news_events, news_gaps = self._read_news(
                now=now, symbols=(symbol,)
            )
        else:
            news_events, news_gaps = (), ("source_unauthorized",)
        dossier = None
        gaps = list(news_gaps)
        bar_days = int(arguments.get("bar_days", 90))
        bar_limit = int(arguments.get("bar_limit", 120))
        if self._dossier_reader is not None:
            try:
                dossier = _plain_data(
                    self._dossier_reader(
                        PersonalActor(context.actor_id),
                        symbol,
                        now,
                        bar_days,
                        bar_limit,
                    )
                )
            except PermissionError:
                gaps.append("source_unauthorized")
            except (RuntimeError, ValueError, OSError):
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
                    source="personal_instrument_state",
                    as_of=now,
                    data={"symbol": symbol, "states": state_data},
                    authorized_fields=("symbol", "states"),
                    prefix="dossier",
                    owner_actor_id=context.actor_id,
                    required_permissions=frozenset({"portfolio:read"}),
                ).envelope
            )
        if dossier is not None:
            evidence_items.append(
                self._record_evidence(
                    source="market_dossier",
                    as_of=now,
                    data=dossier,
                    authorized_fields=tuple(sorted(dossier)),
                    prefix="market-dossier",
                    required_permissions=frozenset({"market:read"}),
                ).envelope
            )
        evidence_items.extend(
            self._event_evidence(event).envelope for event in news_events
        )
        evidence = tuple(evidence_items)
        data = {
            "symbol": symbol,
            "states": state_data,
            "market": dossier,
            "fact_events": [event.data() for event in news_events],
        }
        if not evidence:
            return DomainToolResult.unavailable(
                gaps[0] if gaps else "tool_unavailable", symbol
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
        events, gaps = self._read_news(
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
            snapshot_record = self._news_snapshot_evidence(events, now)
            return DomainToolResult.success(
                data={"items": [], "count": 0},
                evidence=(snapshot_record.envelope,),
                field_coverage=Decimal("1"),
                freshness_seconds=0,
            )
        evidence = tuple(
            self._event_evidence(event).envelope for event in events
        )
        data = {"items": [event.data() for event in events], "count": len(events)}
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
        events, gaps = self._read_news(now=now)
        relation_records: dict[str, list[_EvidenceRecord]] = {}
        for subject in subjects:
            for candidate in self._relation_map.get(subject, ()):
                if candidate in subjects:
                    continue
                relation_records.setdefault(candidate, []).append(
                    self._relation_evidence(subject, candidate, now)
                )
        candidates = []
        evidence_records: list[_EvidenceRecord] = []
        if not gaps:
            evidence_records.append(self._news_snapshot_evidence(events, now))
        evidence_records.extend(
            record
            for records in relation_records.values()
            for record in records
        )
        for candidate, relations in sorted(relation_records.items()):
            facts = tuple(
                event for event in events if candidate in event.related_symbols
            )
            if not relations or not facts:
                continue
            fact_records = tuple(self._event_evidence(event) for event in facts)
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
        record = self._catalog.get(evidence_id, actor_id=context.actor_id)
        if record is None:
            self._read_news(now=now)
            record = self._catalog.get(
                evidence_id, actor_id=context.actor_id
            )
        if record is None:
            return DomainToolResult.unavailable("evidence_not_found", evidence_id)
        missing_permissions = (
            record.required_permissions - context.granted_permissions
        )
        if missing_permissions:
            return DomainToolResult.unavailable(
                "source_unauthorized", ",".join(sorted(missing_permissions))
            )
        source_gaps: tuple[str, ...] = ()
        if record.requires_news_source:
            current_events, gaps = self._read_news(now=now)
            current_ids = {event.evidence_id for event in current_events}
            if evidence_id.startswith("news:") and evidence_id not in current_ids:
                return DomainToolResult.unavailable(
                    gaps[0] if gaps else "evidence_not_found", evidence_id
                )
            if evidence_id.startswith("news-snapshot:") and gaps:
                return DomainToolResult.unavailable(gaps[0], evidence_id)
            source_gaps = gaps
            record = self._catalog.get(
                evidence_id, actor_id=context.actor_id
            ) or record
        if record.expires_at is not None and now >= record.expires_at:
            return DomainToolResult.unavailable("evidence_expired", evidence_id)
        result_kwargs = {
            "data": record.data,
            "evidence": (record.envelope,),
            "field_coverage": Decimal("1"),
            "freshness_seconds": max(
                0,
                int((now - record.envelope.as_of).total_seconds()),
            ),
        }
        if source_gaps:
            return DomainToolResult.partial(
                **result_kwargs,
                gaps=_tool_gaps(source_gaps),
            )
        return DomainToolResult.success(
            **result_kwargs,
        )

    def _read_news(
        self,
        *,
        now: datetime,
        symbols: tuple[str, ...] = (),
        query: str = "",
        sector: str = "",
    ) -> tuple[tuple[_FactNewsEvent, ...], tuple[str, ...]]:
        if self._news_source is None:
            return (), ("source_unavailable",)
        try:
            snapshot = self._news_source.read(now=now)
        except (RuntimeError, OSError, ValueError):
            return (), ("source_unavailable",)
        gaps = list(snapshot.gaps)
        deduplicated: dict[str, _FactNewsEvent] = {}
        for raw in snapshot.items:
            validation = self._validate_news(raw, now)
            if validation is not None:
                gaps.append(validation)
                continue
            try:
                event = _normalize_event(raw)
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
            self._event_evidence(event)
            events.append(event)
        events.sort(
            key=lambda item: (item.published_at, item.event_id), reverse=True
        )
        return tuple(events), tuple(dict.fromkeys(gaps))

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

    def _event_evidence(self, event: _FactNewsEvent) -> _EvidenceRecord:
        record = _EvidenceRecord(
            envelope=EvidenceEnvelope(
                evidence_id=event.evidence_id,
                source=event.source,
                as_of=event.published_at,
                content_sha256=event.content_sha256,
                authorized_fields=(
                    "title",
                    "url",
                    "published_at",
                    "fetched_at",
                    "summary",
                    "source",
                    "source_type",
                    "related_symbols",
                    "confirmation_state",
                ),
            ),
            data=event.data(),
            expires_at=min(
                event.published_at + self._maximum_event_age,
                event.fetched_at + self._maximum_fetch_age,
            ),
            requires_news_source=True,
            required_permissions=frozenset({"news:read"}),
        )
        self._catalog.put(record)
        return record

    def _relation_evidence(
        self, subject: str, candidate: str, now: datetime
    ) -> _EvidenceRecord:
        return self._record_evidence(
            source="instrument_relation_map",
            as_of=now,
            data={
                "subject_symbol": subject,
                "candidate_symbol": candidate,
                "relation": "configured_market_relation",
            },
            authorized_fields=(
                "subject_symbol",
                "candidate_symbol",
                "relation",
            ),
            prefix="relation",
            required_permissions=frozenset({"market:read"}),
        )

    def _news_snapshot_evidence(
        self, events: tuple[_FactNewsEvent, ...], now: datetime
    ) -> _EvidenceRecord:
        return self._record_evidence(
            source="structured_news_snapshot",
            as_of=now,
            data={
                "as_of": now.isoformat(),
                "event_count": len(events),
                "event_ids": [event.event_id for event in events],
            },
            authorized_fields=("as_of", "event_count", "event_ids"),
            prefix="news-snapshot",
            expires_at=now + self._maximum_fetch_age,
            requires_news_source=True,
            required_permissions=frozenset({"news:read"}),
        )

    def _record_evidence(
        self,
        *,
        source: str,
        as_of: datetime,
        data: Mapping[str, Any],
        authorized_fields: tuple[str, ...],
        prefix: str,
        owner_actor_id: str | None = None,
        expires_at: datetime | None = None,
        requires_news_source: bool = False,
        required_permissions: frozenset[str] = frozenset(),
    ) -> _EvidenceRecord:
        digest = _sha256(data)
        evidence_id = f"{prefix}:{digest[:24]}"
        record = _EvidenceRecord(
            envelope=EvidenceEnvelope(
                evidence_id=evidence_id,
                source=source,
                as_of=as_of,
                content_sha256=digest,
                authorized_fields=authorized_fields,
            ),
            data=dict(data),
            owner_actor_id=owner_actor_id,
            expires_at=expires_at,
            requires_news_source=requires_news_source,
            required_permissions=required_permissions,
        )
        self._catalog.put(record)
        return record


def _normalize_event(raw: RawFactNews) -> _FactNewsEvent:
    url = _canonical_url(raw.url)
    event_digest = _sha256({"url": url})
    content = {
        "title": raw.title.strip(),
        "url": url,
        "published_at": raw.published_at,
        "summary": raw.summary.strip(),
        "source": raw.source.strip(),
        "source_type": raw.source_type,
        "sector": raw.sector,
    }
    content_digest = _sha256(content)
    return _FactNewsEvent(
        event_id=f"event:{event_digest[:24]}",
        evidence_id=f"news:{content_digest[:24]}",
        title=raw.title.strip(),
        url=url,
        published_at=raw.published_at,
        fetched_at=raw.fetched_at,
        summary=raw.summary.strip(),
        content_sha256=content_digest,
        source=raw.source.strip(),
        source_type=raw.source_type,
        sector=raw.sector,
        related_symbols=tuple(sorted({_symbol(item) for item in raw.related_symbols})),
    )


_SYMBOL_TERMS: Mapping[str, tuple[str, ...]] = {
    "NVDA": ("nvda", "nvidia", "英伟达"),
    "AMD": ("amd", "advanced micro devices", "超威"),
    "TSM": ("tsm", "tsmc", "台积电"),
    "ASML": ("asml", "阿斯麦"),
    "MSFT": ("msft", "microsoft", "微软"),
    "GOOGL": ("googl", "google", "alphabet", "谷歌"),
    "META": ("meta", "facebook", "脸书"),
    "AMZN": ("amzn", "amazon", "亚马逊"),
    "AAPL": ("aapl", "apple", "苹果"),
    "TSLA": ("tsla", "tesla", "特斯拉"),
}


def _raw_investment_news_item(
    item: Any, *, sector: str, fetched_at: datetime
) -> RawFactNews | None:
    if not isinstance(item, Mapping):
        return None
    timestamp = item.get("ts")
    if not isinstance(timestamp, (int, float)):
        return None
    title = str(item.get("title", "")).strip()
    summary = str(item.get("summary", "")).strip()
    source = str(item.get("source", "")).strip()
    url = str(item.get("url", "")).strip()
    if not title or not summary or not source or not url:
        return None
    haystack = " ".join(
        (title, summary, str(item.get("zh", "")))
    ).lower()
    explicit = item.get("symbols", ())
    symbols: set[str] = set()
    if isinstance(explicit, (list, tuple)):
        for value in explicit:
            try:
                symbols.add(_symbol(str(value)))
            except ValueError:
                continue
    for symbol, terms in _SYMBOL_TERMS.items():
        if any(term in haystack for term in terms):
            symbols.add(symbol)
    return RawFactNews(
        title=title,
        url=url,
        published_at=datetime.fromtimestamp(timestamp, tz=timezone.utc),
        fetched_at=fetched_at,
        summary=summary,
        source=source,
        source_type="structured_news",
        sector=sector,
        related_symbols=tuple(sorted(symbols)),
    )


def _merge_event(left: _FactNewsEvent, right: _FactNewsEvent) -> _FactNewsEvent:
    return _FactNewsEvent(
        event_id=left.event_id,
        evidence_id=left.evidence_id,
        title=left.title,
        url=left.url,
        published_at=min(left.published_at, right.published_at),
        fetched_at=max(left.fetched_at, right.fetched_at),
        summary=left.summary,
        content_sha256=left.content_sha256,
        source=left.source,
        source_type=left.source_type,
        sector=left.sector,
        related_symbols=tuple(
            sorted(set(left.related_symbols) | set(right.related_symbols))
        ),
    )


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


def _argument_as_of(arguments: Mapping[str, Any], default: datetime) -> datetime:
    value = arguments.get("as_of")
    if value is None:
        return default
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("as_of_requires_timezone")
    return parsed


def _market_period(now: datetime) -> str:
    eastern = now.astimezone(ZoneInfo("America/New_York"))
    if eastern.weekday() >= 5:
        return "market_closed"
    minutes = eastern.hour * 60 + eastern.minute
    if minutes < 9 * 60 + 30:
        return "pre_market"
    if minutes < 16 * 60:
        return "market_hours"
    return "after_market"


def _plain_data(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value


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
