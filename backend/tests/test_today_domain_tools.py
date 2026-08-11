from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest

from backend.app.personal_workspace.agent.domain_tools import (
    DomainToolContext,
    DomainToolMetrics,
)
from backend.app.personal_workspace.agent.today_tools import (
    InvestmentNewsStructuredSource,
    NewsSourceSnapshot,
    RawFactNews,
    TodayDomainTools,
)
from backend.app.personal_workspace.agent.fact_news import FactNewsReadContext
from backend.app.personal_workspace.agent.evidence import (
    EvidenceLedgerError,
    EvidenceReadContext,
    InMemoryEvidenceStore,
)
from backend.app.personal_workspace.agent.fact_market import (
    MarketFactService,
)
from backend.app.personal_workspace.agent.fact_private import (
    ActorOwnedFactService,
    PRIVATE_FACT_POLICIES,
    PRIVATE_FACT_POLICY_HISTORY,
    PRIVATE_FACT_RETENTION_BY_AUTHORIZATION,
    _actor_owned_record,
)
from backend.app.personal_workspace.agent.fact_news import (
    FACT_NEWS_AUTHORIZATION_SNAPSHOT_ID,
    FACT_NEWS_RETENTION,
    FACT_NEWS_SOURCE,
)
from backend.app.personal_workspace.agent.fact_news import InvestmentNewsReader
from backend.app.personal_workspace.contracts import PersonalActor
from backend.app.market_observation.contracts import (
    AssetIdentity,
    DailyBar,
    DailyBarsObservation,
    ObservedValue,
    ProvenanceEnvelope,
)
from backend.app.market_observation.alpaca import MarketObservationError
from backend.app.personal_workspace.portfolio import (
    HoldingState,
    InMemoryPortfolioStore,
    PortfolioState,
)
from backend.app.personal_workspace.rules import AttentionItem
from backend.app.personal_workspace.watchlist import (
    HoldingWatchState,
    InMemoryInstrumentStateStore,
    InstrumentStateBook,
)


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


@dataclass
class SyntheticNewsSource:
    snapshot: NewsSourceSnapshot

    def read(
        self, *, context: FactNewsReadContext, now: datetime
    ) -> NewsSourceSnapshot:
        return self.snapshot


class CountingEvidenceStore(InMemoryEvidenceStore):
    def __init__(self) -> None:
        super().__init__(
            retention_by_authorization={
                (
                    FACT_NEWS_SOURCE,
                    FACT_NEWS_AUTHORIZATION_SNAPSHOT_ID,
                ): FACT_NEWS_RETENTION,
                **PRIVATE_FACT_RETENTION_BY_AUTHORIZATION,
            }
        )
        self.put_ids: list[str] = []

    def put(self, context, record):
        self.put_ids.append(record.evidence_id)
        return super().put(context, record)


def raw_news(
    *,
    url: str,
    summary: str,
    symbols: tuple[str, ...],
    published_at: datetime = NOW - timedelta(hours=2),
    fetched_at: datetime = NOW - timedelta(minutes=10),
    source_type: str = "structured_news",
    title: str = "芯片供应链发布结构化更新",
) -> RawFactNews:
    return RawFactNews(
        title=title,
        url=url,
        published_at=published_at,
        fetched_at=fetched_at,
        summary=summary,
        source="Synthetic Wire",
        source_type=source_type,
        sector="semi",
        related_symbols=symbols,
    )


def provenance(dataset: str) -> ProvenanceEnvelope:
    return ProvenanceEnvelope(
        source="alpaca",
        dataset=dataset,
        provider_record_id=None,
        source_url="https://data.example/test",
        fetched_at=NOW,
        content_sha256="a" * 64,
        authorization_snapshot_id=f"auth-{dataset}",
        qualification="online_observation",
        source_health="fresh",
        ai_context=True,
        formal_research=False,
    )


class FakeAiContextMarketAdapter:
    def __init__(self) -> None:
        self.calls = []

    def observe_asset(self, symbol, **kwargs):
        self.calls.append(("asset", symbol, kwargs))
        return ObservedValue(
            availability="available",
            value=AssetIdentity(
                provider_asset_id="asset-1",
                symbol=symbol,
                name="NVIDIA",
                asset_class="us_equity",
                exchange="NASDAQ",
                status="active",
                tradable=True,
                fractionable=True,
            ),
            reason_code=None,
            source_health="fresh",
            as_of=NOW,
            provenance=provenance("alpaca_assets"),
        )

    def observe_daily_bars(self, symbol, **kwargs):
        self.calls.append(("bars", symbol, kwargs))
        observed = ObservedValue(
            availability="available",
            value=(
                DailyBar(
                    symbol=symbol,
                    trade_date=(NOW - timedelta(days=1)).date(),
                    open=Decimal("90"),
                    high=Decimal("101"),
                    low=Decimal("89"),
                    close=Decimal("100"),
                    volume=1000,
                ),
            ),
            reason_code=None,
            source_health="fresh",
            as_of=NOW,
            provenance=provenance("alpaca_daily_bars"),
        )
        return DailyBarsObservation(raw=observed, provider_adjusted=observed)


class RawFallbackMarketAdapter(FakeAiContextMarketAdapter):
    def observe_daily_bars(self, symbol, **kwargs):
        observed = super().observe_daily_bars(symbol, **kwargs)
        adjusted = replace(
            observed.provider_adjusted,
            availability="not_available",
            value=None,
            reason_code="provider_timeout",
            source_health="unavailable",
        )
        return DailyBarsObservation(raw=observed.raw, provider_adjusted=adjusted)


class StaleMarketAdapter(FakeAiContextMarketAdapter):
    def observe_daily_bars(self, symbol, **kwargs):
        observed = super().observe_daily_bars(symbol, **kwargs)
        stale = replace(
            observed.provider_adjusted,
            source_health="stale",
            provenance=replace(
                observed.provider_adjusted.provenance,
                source_health="stale",
            ),
        )
        return DailyBarsObservation(raw=stale, provider_adjusted=stale)


class UnavailableBarsMarketAdapter(FakeAiContextMarketAdapter):
    def observe_daily_bars(self, symbol, **kwargs):
        observed = super().observe_daily_bars(symbol, **kwargs)
        return DailyBarsObservation(
            raw=replace(
                observed.raw,
                availability="not_available",
                value=None,
                reason_code="pagination_incomplete",
                source_health="unavailable",
            ),
            provider_adjusted=replace(
                observed.provider_adjusted,
                availability="not_available",
                value=None,
                reason_code="provider_timeout",
                source_health="unavailable",
            ),
        )


class MultiBarMarketAdapter(FakeAiContextMarketAdapter):
    def observe_daily_bars(self, symbol, **kwargs):
        observed = super().observe_daily_bars(symbol, **kwargs)
        values = tuple(
            replace(
                observed.raw.value[0],
                trade_date=(NOW - timedelta(days=offset)).date(),
                close=Decimal(str(100 + offset)),
            )
            for offset in (3, 2, 1)
        )
        complete = replace(observed.raw, value=values)
        return DailyBarsObservation(raw=complete, provider_adjusted=complete)


class TodayDomainToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.portfolio = InMemoryPortfolioStore()
        self.portfolio._states["actor-1"] = PortfolioState(
            workspace_id="workspace-1",
            revision=3,
            usd_cash=Decimal("500"),
            holdings={
                "holding-1": HoldingState(
                    holding_id="holding-1",
                    symbol="NVDA",
                    name="NVIDIA",
                    quantity=Decimal("2"),
                    average_cost=Decimal("100"),
                )
            },
        )
        watchlist = InstrumentStateBook(
            store=InMemoryInstrumentStateStore(),
            holding_states_reader=lambda _actor_id: {
                "NVDA": HoldingWatchState("active", 1)
            },
        )
        self.metrics = DomainToolMetrics()
        self.source = SyntheticNewsSource(
            NewsSourceSnapshot(
                items=(
                    raw_news(
                        url="https://wire.example/events/semis-1",
                        summary="来源摘要 A，不是服务端确认事实。",
                        symbols=("NVDA",),
                    ),
                    raw_news(
                        url="https://wire.example/events/semis-1",
                        summary="来源摘要 A，不是服务端确认事实。",
                        symbols=("AMD",),
                        title="同一 URL 的另一标的标题",
                    ),
                    raw_news(
                        url="https://wire.example/events/amd-earnings",
                        summary="AMD 发布近期结构化事实摘要。",
                        symbols=("AMD",),
                    ),
                ),
                gaps=(),
            )
        )
        self.tools = TodayDomainTools(
            portfolio_store=self.portfolio,
            watchlist=watchlist,
            news_source=self.source,
            relation_map={"NVDA": ("AMD",), "AMD": ("NVDA",)},
        )
        self.registry = self.tools.registry(
            observation_recorder=self.metrics.record
        )
        self.context = DomainToolContext(
            actor_id="actor-1",
            granted_permissions=frozenset(
                {
                    "portfolio:read",
                    "market:read",
                    "news:read",
                    "web_evidence:read",
                    "evidence:read",
                }
            ),
            clock=lambda: NOW,
        )

    def invoke(self, name: str, arguments: dict):
        return self.registry.invoke(
            name, context=self.context, arguments=arguments
        )

    def _invoke_legacy_kline(self, adapter):
        retention = {
            ("alpaca", "auth-alpaca_daily_bars"): "encrypted_payload",
        }
        ledger = InMemoryEvidenceStore(
            retention_by_authorization={
                **PRIVATE_FACT_RETENTION_BY_AUTHORIZATION,
                **retention,
            }
        )
        return TodayDomainTools(
            portfolio_store=self.portfolio,
            watchlist=self.tools.watchlist,
            news_source=None,
            evidence_ledger=ledger,
            market_facts=MarketFactService(
                adapter=adapter,
                evidence_ledger=ledger,
                retention_by_authorization=retention,
            ),
        ).registry().invoke(
            "get_kline",
            context=DomainToolContext(
                actor_id="actor-1",
                granted_permissions=frozenset({"market:read"}),
                clock=lambda: NOW,
            ),
            arguments={"symbol": "NVDA", "days": 30, "limit": 1},
        )

    def test_news_preserves_provenance_deduplicates_and_never_promotes_summary(self) -> None:
        result = self.invoke(
            "search_market_news",
            {"symbols": ["NVDA", "AMD"], "limit": 20},
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.data["items"]), 2)
        merged = next(
            item
            for item in result.data["items"]
            if item["url"].endswith("semis-1")
        )
        self.assertEqual(merged["related_symbols"], ["AMD", "NVDA"])
        self.assertEqual(merged["source_type"], "structured_news")
        self.assertEqual(merged["confirmation_state"], "source_summary_unconfirmed")
        self.assertEqual(len(merged["content_sha256"]), 64)
        self.assertEqual(merged["published_at"], "2026-08-10T10:00:00+00:00")
        self.assertEqual(merged["fetched_at"], "2026-08-10T11:50:00+00:00")
        merged_envelope = next(
            item
            for item in result.evidence
            if item.evidence_id == merged["evidence_id"]
        )
        self.assertEqual(
            set(merged), set(merged_envelope.authorized_fields)
        )

        evidence = self.invoke(
            "get_evidence", {"evidence_id": merged["evidence_id"]}
        )
        self.assertEqual(evidence.status, "success")
        self.assertEqual(evidence.data["url"], merged["url"])
        self.assertEqual(
            evidence.data["confirmation_state"],
            "source_summary_unconfirmed",
        )
        self.assertEqual(
            tuple(evidence.data), evidence.evidence[0].authorized_fields
        )
        self.assertTrue(
            {
                "event_id",
                "evidence_id",
                "fetched_at",
                "content_sha256",
            }.issubset(evidence.data)
        )

    def test_legacy_news_alias_keeps_safe_projection_on_persisted_path(self) -> None:
        result = self.invoke("get_news", {"symbol": "AMD", "limit": 20})

        self.assertEqual(result.status, "success")
        self.assertGreaterEqual(result.data["count"], 2)
        item = result.data["items"][0]
        self.assertEqual(
            set(item),
            {
                "evidence_id",
                "title",
                "url",
                "published_at",
                "fetched_at",
                "summary",
                "source",
                "source_type",
                "related_symbols",
                "confirmation_state",
            },
        )
        self.assertFalse(
            {"event_id", "content_sha256", "sector"} & set(item)
        )
        item_ids = [value["evidence_id"] for value in result.data["items"]]
        self.assertEqual(len(item_ids), len(set(item_ids)))
        self.assertEqual(item_ids, [value.evidence_id for value in result.evidence])
        evidence_by_id = {value.evidence_id: value for value in result.evidence}
        self.assertTrue(
            all(
                value["evidence_id"] in evidence_by_id
                for value in result.data["items"]
            )
        )

    def test_today_dossier_candidates_and_web_unavailable_are_independent(self) -> None:
        today = self.invoke("get_today_context", {})
        dossier = self.invoke("get_symbol_dossier", {"symbol": "NVDA"})
        candidates = self.invoke(
            "discover_related_candidates", {"subject_ids": ["NVDA"]}
        )
        web = self.invoke(
            "search_web_evidence", {"query": "NVDA latest filing"}
        )

        self.assertIn(today.status, {"success", "partial"})
        self.assertEqual(today.data["active_holding_count"], 1)
        self.assertEqual(dossier.data["symbol"], "NVDA")
        self.assertTrue(dossier.data["states"]["holding"])
        self.assertEqual(len(candidates.data["candidates"]), 1)
        candidate = candidates.data["candidates"][0]
        self.assertEqual(candidate["symbol"], "AMD")
        self.assertTrue(candidate["relation_evidence_ids"])
        self.assertTrue(candidate["fact_evidence_ids"])
        self.assertEqual(candidate["relation_evidence"][0]["title"], "NVDA → AMD")
        self.assertEqual(candidate["fact_evidence"][0]["source"], "Synthetic Wire")
        self.assertTrue(candidate["fact_evidence"][0]["summary"])
        self.assertTrue(candidate["fact_evidence"][0]["url"].startswith("https://"))
        self.assertEqual(web.status, "unavailable")
        self.assertEqual(web.error_code, "hosted_web_search_unavailable")
        self.assertFalse(
            any(
                item.symbol == "AMD"
                for item in self.tools.watchlist.open(
                    PersonalActor("actor-1")
                ).items
            )
        )

        snapshot = self.metrics.snapshot()
        self.assertEqual(snapshot["calls"], 4)
        self.assertEqual(snapshot["unavailable"], 1)
        self.assertIn("hosted_web_search_unavailable", snapshot["gap_reasons"])
        self.assertEqual(
            snapshot["by_tool"]["search_web_evidence"]["unavailable"], 1
        )

    def test_today_context_uses_current_snapshot_time_and_active_rule_attention(self) -> None:
        attention = (
            AttentionItem(
                attention_id="evaluation-1",
                kind="rule_hit",
                symbol="NVDA",
                label="规则命中",
                result="hit",
                as_of=NOW,
                reason_code="threshold_crossed",
                priority=0,
            ),
            AttentionItem(
                attention_id="evaluation-2",
                kind="data_gap",
                symbol="AMD",
                label="规则数据不足",
                result="insufficient_data",
                as_of=NOW,
                reason_code="bars_insufficient",
                priority=1,
            ),
        )
        registry = TodayDomainTools(
            portfolio_store=self.portfolio,
            watchlist=self.tools.watchlist,
            news_source=self.source,
            rule_attention_reader=lambda _actor: attention,
        ).registry()
        result = registry.invoke(
            "get_today_context",
            context=self.context,
            arguments={"as_of": "2020-01-01T00:00:00+00:00"},
        )
        portfolio_evidence = next(
            item for item in result.evidence if item.source == "personal_portfolio"
        )

        self.assertEqual(result.data["as_of"], NOW.isoformat())
        self.assertEqual(portfolio_evidence.as_of, NOW)
        self.assertEqual(
            [item["attention_id"] for item in result.data["attention_items"]],
            ["evaluation-1"],
        )
        self.assertTrue(
            any(item.source == "observation_rule_attention" for item in result.evidence)
        )

    def test_today_period_uses_xnys_holiday_calendar(self) -> None:
        holiday = datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)
        result = self.registry.invoke(
            "get_today_context",
            context=DomainToolContext(
                actor_id="actor-1",
                granted_permissions=self.context.granted_permissions,
                clock=lambda: holiday,
            ),
            arguments={},
        )
        self.assertEqual(result.data["period"], "market_closed")

    def test_refetch_keeps_immutable_persisted_evidence_version(self) -> None:
        first = self.invoke(
            "search_market_news", {"symbols": ["AMD"], "limit": 20}
        )
        original = next(
            item
            for item in first.data["items"]
            if item["url"].endswith("amd-earnings")
        )
        self.source.snapshot = NewsSourceSnapshot(
            items=(
                raw_news(
                    url="https://wire.example/events/amd-earnings",
                    summary="AMD 发布近期结构化事实摘要。",
                    symbols=("AMD",),
                    fetched_at=NOW - timedelta(minutes=1),
                ),
            )
        )

        refreshed = self.invoke(
            "search_market_news", {"symbols": ["AMD"], "limit": 20}
        ).data["items"][0]
        evidence = self.invoke(
            "get_evidence", {"evidence_id": original["evidence_id"]}
        )

        self.assertEqual(refreshed["evidence_id"], original["evidence_id"])
        self.assertEqual(refreshed["fetched_at"], original["fetched_at"])
        self.assertEqual(evidence.data["fetched_at"], original["fetched_at"])

    def test_expired_content_revalidation_creates_stable_readable_version(self) -> None:
        ledger = CountingEvidenceStore()
        tools = TodayDomainTools(
            portfolio_store=self.portfolio,
            watchlist=self.tools.watchlist,
            news_source=self.source,
            evidence_ledger=ledger,
        )
        registry = tools.registry()
        first = registry.invoke(
            "search_market_news",
            context=self.context,
            arguments={"symbols": ["AMD"], "limit": 20},
        )
        original = next(
            item
            for item in first.data["items"]
            if item["url"].endswith("amd-earnings")
        )
        later = NOW + timedelta(hours=3)
        revalidated_at = later - timedelta(minutes=10)
        self.source.snapshot = NewsSourceSnapshot(
            items=(
                raw_news(
                    url="https://wire.example/events/amd-earnings",
                    summary="AMD 发布近期结构化事实摘要。",
                    symbols=("AMD",),
                    fetched_at=revalidated_at,
                ),
            )
        )
        later_context = DomainToolContext(
            actor_id="actor-1",
            granted_permissions=self.context.granted_permissions,
            clock=lambda: later,
        )

        refreshed = registry.invoke(
            "search_market_news",
            context=later_context,
            arguments={"symbols": ["AMD"], "limit": 20},
        )
        repeated = registry.invoke(
            "search_market_news",
            context=later_context,
            arguments={"symbols": ["AMD"], "limit": 20},
        )
        item = refreshed.data["items"][0]
        evidence = registry.invoke(
            "get_evidence",
            context=later_context,
            arguments={"evidence_id": item["evidence_id"]},
        )
        frozen = ledger.freeze(
            EvidenceReadContext(
                actor_id="actor-1",
                permissions=frozenset({"news:read", "evidence:read"}),
                purpose="domain_tool",
                now=later,
            ),
            (item["evidence_id"],),
        )[0]

        self.assertEqual(refreshed.status, "success")
        self.assertNotEqual(item["evidence_id"], original["evidence_id"])
        self.assertEqual(
            repeated.data["items"][0]["evidence_id"], item["evidence_id"]
        )
        self.assertGreaterEqual(len(item["evidence_id"].rsplit(":", 1)[1]), 24)
        self.assertEqual(evidence.status, "success")
        self.assertEqual(evidence.data["fetched_at"], item["fetched_at"])
        self.assertEqual(frozen.fetched_at.isoformat(), item["fetched_at"])
        self.assertEqual(frozen.available_from, frozen.fetched_at)

    def test_old_empty_snapshot_is_stale_and_is_not_persisted_as_fresh(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "data.js").write_text(
            'window.DATA = {"industries": []}', encoding="utf-8"
        )
        old = NOW - timedelta(hours=3)
        os.utime(root / "data.js", (old.timestamp(), old.timestamp()))
        source = InvestmentNewsStructuredSource(
            InvestmentNewsReader(root), refresh_before_read=False
        )
        ledger = CountingEvidenceStore()
        registry = TodayDomainTools(
            portfolio_store=self.portfolio,
            watchlist=self.tools.watchlist,
            news_source=source,
            evidence_ledger=ledger,
        ).registry()

        result = registry.invoke(
            "search_market_news", context=self.context, arguments={}
        )

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.error_code, "source_stale")
        self.assertEqual(ledger.put_ids, [])

    def test_news_identity_is_order_independent_and_content_changes_version(self) -> None:
        original = self.invoke(
            "search_market_news", {"symbols": ["NVDA", "AMD"], "limit": 20}
        )
        original_item = next(
            item for item in original.data["items"] if item["url"].endswith("semis-1")
        )
        self.source.snapshot = NewsSourceSnapshot(
            items=tuple(reversed(self.source.snapshot.items))
        )
        reordered_item = next(
            item
            for item in self.invoke(
                "search_market_news", {"symbols": ["NVDA", "AMD"], "limit": 20}
            ).data["items"]
            if item["url"].endswith("semis-1")
        )
        self.source.snapshot = NewsSourceSnapshot(
            items=(
                raw_news(
                    url="https://wire.example/events/semis-1",
                    summary="来源摘要已经发生实质变化。",
                    symbols=("AMD", "NVDA"),
                ),
            )
        )
        changed_item = self.invoke(
            "search_market_news", {"symbols": ["NVDA", "AMD"], "limit": 20}
        ).data["items"][0]

        self.assertEqual(reordered_item["evidence_id"], original_item["evidence_id"])
        self.assertNotEqual(changed_item["evidence_id"], original_item["evidence_id"])

    def test_news_ledger_record_keeps_authorization_available_from_and_ttl(self) -> None:
        ledger = InMemoryEvidenceStore(
            retention_by_authorization={
                (FACT_NEWS_SOURCE, FACT_NEWS_AUTHORIZATION_SNAPSHOT_ID): FACT_NEWS_RETENTION
            }
        )
        registry = TodayDomainTools(
            portfolio_store=self.portfolio,
            watchlist=self.tools.watchlist,
            news_source=self.source,
            evidence_ledger=ledger,
        ).registry()
        searched = registry.invoke(
            "search_market_news",
            context=self.context,
            arguments={"symbols": ["AMD"], "limit": 20},
        )
        item = next(
            value
            for value in searched.data["items"]
            if value["url"].endswith("amd-earnings")
        )
        record = ledger.read(
            EvidenceReadContext(
                actor_id="actor-1",
                permissions=frozenset({"news:read", "evidence:read"}),
                purpose="domain_tool",
                now=NOW,
            ),
            item["evidence_id"],
        )

        self.assertEqual(
            record.authorization_snapshot_id,
            FACT_NEWS_AUTHORIZATION_SNAPSHOT_ID,
        )
        self.assertEqual(record.available_from, record.fetched_at)
        self.assertEqual(record.expires_at, NOW + timedelta(minutes=110))
        self.assertTrue(record.evidence_id.endswith(record.content_sha256[:24]))

    def test_search_persists_only_events_returned_after_limit(self) -> None:
        ledger = CountingEvidenceStore()
        registry = TodayDomainTools(
            portfolio_store=self.portfolio,
            watchlist=self.tools.watchlist,
            news_source=self.source,
            evidence_ledger=ledger,
        ).registry()

        result = registry.invoke(
            "search_market_news",
            context=self.context,
            arguments={"symbols": ["AMD", "NVDA"], "limit": 1},
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.data["count"], 1)
        self.assertEqual(ledger.put_ids, [result.data["items"][0]["evidence_id"]])

    def test_discover_persists_only_snapshot_and_referenced_fact_limit(self) -> None:
        self.source.snapshot = NewsSourceSnapshot(
            items=tuple(
                raw_news(
                    url=f"https://wire.example/events/amd-{index}",
                    summary=f"AMD 结构化摘要 {index}。",
                    symbols=("AMD",),
                    published_at=NOW - timedelta(minutes=index + 1),
                )
                for index in range(5)
            )
        )
        ledger = CountingEvidenceStore()
        registry = TodayDomainTools(
            portfolio_store=self.portfolio,
            watchlist=self.tools.watchlist,
            news_source=self.source,
            evidence_ledger=ledger,
            relation_map={"NVDA": ("AMD",)},
        ).registry()

        result = registry.invoke(
            "discover_related_candidates",
            context=self.context,
            arguments={"subject_ids": ["NVDA"]},
        )
        candidate = result.data["candidates"][0]
        persisted_result_ids = {item.evidence_id for item in result.evidence}

        self.assertEqual(result.status, "success")
        self.assertEqual(len(candidate["fact_evidence_ids"]), 3)
        self.assertEqual(set(ledger.put_ids), persisted_result_ids)
        self.assertEqual(len(ledger.put_ids), 5)

    def test_empty_snapshot_uses_its_exact_authorization_retention_policy(self) -> None:
        ledger = InMemoryEvidenceStore(
            retention_by_authorization={
                ("custom-news", "custom-auth-v2"): "metadata_only"
            }
        )
        registry = TodayDomainTools(
            portfolio_store=self.portfolio,
            watchlist=self.tools.watchlist,
            news_source=SyntheticNewsSource(
                NewsSourceSnapshot(
                    items=(),
                    fetched_at=NOW - timedelta(minutes=10),
                    source="custom-news",
                    authorization_snapshot_id="custom-auth-v2",
                    persistence="metadata_only",
                )
            ),
            evidence_ledger=ledger,
        ).registry()
        searched = registry.invoke(
            "search_market_news", context=self.context, arguments={}
        )
        evidence_id = searched.evidence[0].evidence_id
        record = ledger.read(
            EvidenceReadContext(
                actor_id="actor-1",
                permissions=frozenset({"news:read"}),
                purpose="domain_tool",
                now=NOW,
            ),
            evidence_id,
        )

        self.assertEqual(searched.status, "success")
        self.assertEqual(record.source, "custom-news")
        self.assertEqual(record.authorization_snapshot_id, "custom-auth-v2")
        self.assertEqual(record.persistence, "metadata_only")
        self.assertIsNone(record.payload)

    def test_optional_news_permission_and_private_evidence_are_actor_scoped(self) -> None:
        today = self.invoke("get_today_context", {})
        private_evidence_id = next(
            item.evidence_id
            for item in today.evidence
            if item.source == "personal_portfolio"
        )
        no_news_context = DomainToolContext(
            actor_id="actor-1",
            granted_permissions=frozenset(
                {"portfolio:read", "market:read", "evidence:read"}
            ),
            clock=lambda: NOW,
        )
        no_news = self.registry.invoke(
            "get_today_context", context=no_news_context, arguments={}
        )
        same_actor_without_source_permission = self.registry.invoke(
            "get_evidence",
            context=DomainToolContext(
                actor_id="actor-1",
                granted_permissions=frozenset({"evidence:read"}),
                clock=lambda: NOW,
            ),
            arguments={"evidence_id": private_evidence_id},
        )
        other_actor = self.registry.invoke(
            "get_evidence",
            context=DomainToolContext(
                actor_id="actor-2",
                granted_permissions=frozenset({"evidence:read"}),
                clock=lambda: NOW,
            ),
            arguments={"evidence_id": private_evidence_id},
        )

        self.assertEqual(no_news.data["fact_events"], [])
        self.assertIn("source_unauthorized", {gap.code for gap in no_news.gaps})
        self.assertEqual(
            same_actor_without_source_permission.error_code,
            "source_unauthorized",
        )
        self.assertEqual(other_actor.error_code, "evidence_not_found")

    def test_persisted_news_survives_source_failure_but_not_ttl(self) -> None:
        news = self.invoke(
            "search_market_news", {"symbols": ["AMD"], "limit": 20}
        )
        evidence_id = news.data["items"][0]["evidence_id"]
        self.source.snapshot = NewsSourceSnapshot(
            items=(), gaps=("source_unavailable",)
        )
        persisted = self.invoke(
            "get_evidence", {"evidence_id": evidence_id}
        )

        self.assertEqual(persisted.status, "success")
        self.assertEqual(persisted.data["evidence_id"], evidence_id)
        self.source.snapshot = NewsSourceSnapshot(
            items=(
                raw_news(
                    url="https://wire.example/events/semis-1",
                    summary="来源摘要 A，不是服务端确认事实。",
                    symbols=("AMD",),
                ),
            )
        )
        expired = self.registry.invoke(
            "get_evidence",
            context=DomainToolContext(
                actor_id="actor-1",
                granted_permissions=frozenset(
                    {"evidence:read", "news:read"}
                ),
                clock=lambda: NOW + timedelta(days=15),
            ),
            arguments={"evidence_id": evidence_id},
        )
        self.assertEqual(expired.status, "unavailable")
        self.assertEqual(expired.error_code, "evidence_expired")

    def test_legacy_kline_parameters_reach_market_reader_without_private_or_news(self) -> None:
        adapter = FakeAiContextMarketAdapter()
        market_retention = {
            ("alpaca", "auth-alpaca_assets"): "encrypted_payload",
            ("alpaca", "auth-alpaca_daily_bars"): "encrypted_payload",
        }
        ledger = InMemoryEvidenceStore(
            retention_by_authorization={
                **PRIVATE_FACT_RETENTION_BY_AUTHORIZATION,
                **market_retention,
            }
        )
        registry = TodayDomainTools(
            portfolio_store=self.portfolio,
            watchlist=self.tools.watchlist,
            news_source=self.source,
            evidence_ledger=ledger,
            market_facts=MarketFactService(
                adapter=adapter,
                evidence_ledger=ledger,
                retention_by_authorization=market_retention,
            ),
        ).registry()
        result = registry.invoke(
            "get_kline",
            context=DomainToolContext(
                actor_id="actor-1",
                granted_permissions=frozenset({"market:read"}),
                clock=lambda: NOW,
            ),
            arguments={"symbol": "NVDA", "days": 30, "limit": 1},
        )

        self.assertEqual([call[0] for call in adapter.calls], ["bars"])
        self.assertEqual(adapter.calls[0][0:2], ("bars", "NVDA"))
        self.assertEqual(
            adapter.calls[0][2]["start_date"],
            (NOW - timedelta(days=30)).date(),
        )
        self.assertEqual(result.data["symbol"], "NVDA")
        self.assertEqual(result.data["count"], 1)
        self.assertNotIn("states", result.data)
        holdings = registry.invoke(
            "get_holdings",
            context=DomainToolContext(
                actor_id="actor-1",
                granted_permissions=frozenset({"portfolio:read"}),
                clock=lambda: NOW,
            ),
            arguments={},
        )
        self.assertEqual(holdings.status, "success")
        self.assertEqual(holdings.data["count"], 1)
        self.assertEqual(holdings.data["usd_cash"], "500")
        self.assertEqual(holdings.data["holdings"][0]["symbol"], "NVDA")

    def test_legacy_kline_succeeds_without_asset_identity_io(self) -> None:
        class AssetUnavailableAdapter(FakeAiContextMarketAdapter):
            def observe_asset(self, symbol, **kwargs):
                raise AssertionError("get_kline must not read asset identity")

        adapter = AssetUnavailableAdapter()
        retention = {
            ("alpaca", "auth-alpaca_daily_bars"): "encrypted_payload",
        }
        ledger = InMemoryEvidenceStore(
            retention_by_authorization={
                **PRIVATE_FACT_RETENTION_BY_AUTHORIZATION,
                **retention,
            }
        )
        result = TodayDomainTools(
            portfolio_store=self.portfolio,
            watchlist=self.tools.watchlist,
            news_source=None,
            evidence_ledger=ledger,
            market_facts=MarketFactService(
                adapter=adapter,
                evidence_ledger=ledger,
                retention_by_authorization=retention,
            ),
        ).registry().invoke(
            "get_kline",
            context=DomainToolContext(
                actor_id="actor-1",
                granted_permissions=frozenset({"market:read"}),
                clock=lambda: NOW,
            ),
            arguments={"symbol": "NVDA", "days": 30, "limit": 1},
        )

        self.assertEqual(result.status, "success")
        self.assertEqual([call[0] for call in adapter.calls], ["bars"])
        self.assertEqual(len(result.evidence), 1)
        self.assertIn("bars", result.evidence[0].authorized_fields)

    def test_private_fact_identity_tracks_revision_and_payload_not_read_time(self) -> None:
        ledger = InMemoryEvidenceStore(
            retention_by_authorization=PRIVATE_FACT_RETENTION_BY_AUTHORIZATION
        )
        service = ActorOwnedFactService(ledger)
        first_context = EvidenceReadContext(
            actor_id="actor-1",
            permissions=frozenset({"portfolio:read"}),
            purpose="domain_tool",
            now=NOW,
        )
        later_context = replace(first_context, now=NOW + timedelta(hours=1))
        first = service.record(
            context=first_context,
            source="personal_portfolio",
            logical_identity="holdings:3",
            payload={"holdings": [], "count": 0},
            observed_at=NOW,
        )
        same = service.record(
            context=later_context,
            source="personal_portfolio",
            logical_identity="holdings:3",
            payload={"holdings": [], "count": 0},
            observed_at=later_context.now,
        )
        changed_revision = service.record(
            context=later_context,
            source="personal_portfolio",
            logical_identity="holdings:4",
            payload={"holdings": [], "count": 0},
            observed_at=later_context.now,
        )
        changed_payload = service.record(
            context=later_context,
            source="personal_portfolio",
            logical_identity="holdings:3",
            payload={"holdings": [], "count": 1},
            observed_at=later_context.now,
        )

        self.assertEqual(same.evidence_id, first.evidence_id)
        self.assertEqual(same.fetched_at, first.fetched_at)
        self.assertNotEqual(changed_revision.evidence_id, first.evidence_id)
        self.assertNotEqual(changed_payload.evidence_id, first.evidence_id)
        with self.assertRaisesRegex(EvidenceLedgerError, "evidence_not_found"):
            ledger.read(
                replace(later_context, actor_id="actor-2"), first.evidence_id
            )

    def test_private_policy_rotation_changes_identity_without_phantom_current(self) -> None:
        self.assertTrue(
            all(len(history) == 1 for history in PRIVATE_FACT_POLICY_HISTORY.values())
        )
        v1 = PRIVATE_FACT_POLICY_HISTORY["personal_portfolio"][0]
        self.assertIs(PRIVATE_FACT_POLICIES["personal_portfolio"], v1)
        v2 = replace(
            v1,
            authorization_snapshot_id="actor-owned-personal-portfolio-v2",
        )
        retention = {
            **PRIVATE_FACT_RETENTION_BY_AUTHORIZATION,
            (v2.source, v2.authorization_snapshot_id): v2.persistence,
        }
        ledger = InMemoryEvidenceStore(
            retention_by_authorization=retention
        )
        context = EvidenceReadContext(
            actor_id="actor-1",
            permissions=frozenset({"portfolio:read"}),
            purpose="domain_tool",
            now=NOW,
        )
        arguments = {
            "context": context,
            "logical_identity": "holdings:3",
            "payload": {"holdings": [], "count": 0},
            "observed_at": NOW,
        }
        old = ledger.put(
            context, _actor_owned_record(policy=v1, **arguments)
        )
        new = ledger.put(
            context, _actor_owned_record(policy=v2, **arguments)
        )

        self.assertNotEqual(old.evidence_id, new.evidence_id)
        self.assertNotEqual(old.logical_identity, new.logical_identity)
        self.assertEqual(old.content_sha256, new.content_sha256)
        unknown = replace(
            v2, authorization_snapshot_id="actor-owned-unknown"
        )
        with self.assertRaisesRegex(
            EvidenceLedgerError, "source_retention_unknown"
        ):
            ledger.put(
                context, _actor_owned_record(policy=unknown, **arguments)
            )

    def test_holdings_and_kline_use_the_same_ledger_and_can_freeze(self) -> None:
        market_retention = {
            ("alpaca", "auth-alpaca_assets"): "encrypted_payload",
            ("alpaca", "auth-alpaca_daily_bars"): "encrypted_payload",
        }
        ledger = InMemoryEvidenceStore(
            retention_by_authorization={
                **PRIVATE_FACT_RETENTION_BY_AUTHORIZATION,
                **market_retention,
            }
        )
        registry = TodayDomainTools(
            portfolio_store=self.portfolio,
            watchlist=self.tools.watchlist,
            news_source=None,
            evidence_ledger=ledger,
            market_facts=MarketFactService(
                adapter=FakeAiContextMarketAdapter(),
                evidence_ledger=ledger,
                retention_by_authorization=market_retention,
            ),
        ).registry()
        context = DomainToolContext(
            actor_id="actor-1",
            granted_permissions=frozenset({"portfolio:read", "market:read"}),
            clock=lambda: NOW,
        )

        holdings = registry.invoke("get_holdings", context=context, arguments={})
        kline = registry.invoke(
            "get_kline",
            context=context,
            arguments={"symbol": "NVDA", "days": 30, "limit": 1},
        )
        evidence_ids = tuple(
            item.evidence_id for item in (*holdings.evidence, *kline.evidence)
        )
        frozen = ledger.freeze(
            EvidenceReadContext(
                actor_id="actor-1",
                permissions=context.granted_permissions,
                purpose="domain_tool",
                now=NOW,
            ),
            evidence_ids,
        )

        self.assertEqual(holdings.status, "success")
        self.assertEqual(kline.status, "success")
        self.assertEqual(
            tuple(item.evidence_id for item in frozen), evidence_ids
        )
        self.assertTrue(all(item.payload is not None for item in frozen))

    def test_legacy_kline_raw_and_stale_remain_success_with_stable_fields(self) -> None:
        retention = {
            ("alpaca", "auth-alpaca_assets"): "encrypted_payload",
            ("alpaca", "auth-alpaca_daily_bars"): "encrypted_payload",
        }
        context = DomainToolContext(
            actor_id="actor-1",
            granted_permissions=frozenset({"market:read"}),
            clock=lambda: NOW,
        )

        def invoke(adapter):
            ledger = InMemoryEvidenceStore(
                retention_by_authorization=retention
            )
            registry = TodayDomainTools(
                portfolio_store=self.portfolio,
                watchlist=self.tools.watchlist,
                news_source=None,
                evidence_ledger=ledger,
                market_facts=MarketFactService(
                    adapter=adapter,
                    evidence_ledger=ledger,
                    retention_by_authorization=retention,
                ),
            ).registry()
            return registry.invoke(
                "get_kline",
                context=context,
                arguments={"symbol": "NVDA", "days": 30, "limit": 1},
            )

        raw = invoke(RawFallbackMarketAdapter())
        stale = invoke(StaleMarketAdapter())

        expected_fields = {
            "symbol",
            "adjustment",
            "as_of",
            "source_health",
            "bars",
            "count",
        }
        self.assertEqual(raw.status, "success")
        self.assertEqual(raw.gaps, ())
        self.assertEqual(raw.data["adjustment"], "raw")
        self.assertEqual(set(raw.data), expected_fields)
        self.assertTrue(raw.evidence)
        self.assertEqual(stale.status, "success")
        self.assertEqual(stale.gaps, ())
        self.assertEqual(stale.data["source_health"], "stale")
        self.assertEqual(set(stale.data), expected_fields)
        self.assertTrue(stale.evidence)

    def test_legacy_kline_preserves_specific_bars_failure_code(self) -> None:
        retention = {
            ("alpaca", "auth-alpaca_daily_bars"): "encrypted_payload",
        }
        ledger = InMemoryEvidenceStore(
            retention_by_authorization={
                **PRIVATE_FACT_RETENTION_BY_AUTHORIZATION,
                **retention,
            }
        )
        result = TodayDomainTools(
            portfolio_store=self.portfolio,
            watchlist=self.tools.watchlist,
            news_source=None,
            evidence_ledger=ledger,
            market_facts=MarketFactService(
                adapter=UnavailableBarsMarketAdapter(),
                evidence_ledger=ledger,
                retention_by_authorization=retention,
            ),
        ).registry().invoke(
            "get_kline",
            context=DomainToolContext(
                actor_id="actor-1",
                granted_permissions=frozenset({"market:read"}),
                clock=lambda: NOW,
            ),
            arguments={"symbol": "NVDA", "days": 30, "limit": 1},
        )

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.error_code, "pagination_incomplete")
        self.assertEqual(result.gaps[0].code, "pagination_incomplete")

    def test_legacy_kline_preserves_market_observation_error_code(self) -> None:
        class RaisingAdapter(FakeAiContextMarketAdapter):
            def observe_daily_bars(self, symbol, **kwargs):
                raise MarketObservationError("provider_pagination_incomplete")

        result = self._invoke_legacy_kline(RaisingAdapter())

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.error_code, "provider_pagination_incomplete")

    def test_legacy_kline_maps_plain_permission_error_to_authorization_denied(self) -> None:
        class RaisingAdapter(FakeAiContextMarketAdapter):
            def observe_daily_bars(self, symbol, **kwargs):
                raise PermissionError("denied")

        result = self._invoke_legacy_kline(RaisingAdapter())

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.error_code, "authorization_denied")

    def test_legacy_kline_prefers_permission_error_code(self) -> None:
        class CodedPermissionError(PermissionError):
            code = "entitlement_denied"

        class RaisingAdapter(FakeAiContextMarketAdapter):
            def observe_daily_bars(self, symbol, **kwargs):
                raise CodedPermissionError("denied")

        result = self._invoke_legacy_kline(RaisingAdapter())

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.error_code, "entitlement_denied")

    def test_legacy_kline_preserves_value_error_message(self) -> None:
        class RaisingAdapter(FakeAiContextMarketAdapter):
            def observe_daily_bars(self, symbol, **kwargs):
                raise ValueError("provider_symbol_mismatch")

        result = self._invoke_legacy_kline(RaisingAdapter())

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.error_code, "provider_symbol_mismatch")

    def test_legacy_kline_without_adapter_is_kline_unavailable(self) -> None:
        result = self._invoke_legacy_kline(None)

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.error_code, "kline_unavailable")

    def test_market_identity_includes_authorization_snapshot(self) -> None:
        class SnapshotAdapter(FakeAiContextMarketAdapter):
            def __init__(self, snapshot_id: str) -> None:
                super().__init__()
                self.snapshot_id = snapshot_id

            def observe_daily_bars(self, symbol, **kwargs):
                observed = super().observe_daily_bars(symbol, **kwargs)
                selected = replace(
                    observed.raw,
                    provenance=replace(
                        observed.raw.provenance,
                        authorization_snapshot_id=self.snapshot_id,
                    ),
                )
                return DailyBarsObservation(
                    raw=selected, provider_adjusted=selected
                )

        retention = {
            ("alpaca", "auth-bars-old"): "encrypted_payload",
            ("alpaca", "auth-bars-new"): "encrypted_payload",
        }
        ledger = InMemoryEvidenceStore(
            retention_by_authorization=retention
        )
        context = EvidenceReadContext(
            actor_id="actor-1",
            permissions=frozenset({"market:read"}),
            purpose="domain_tool",
            now=NOW,
        )
        old = MarketFactService(
            adapter=SnapshotAdapter("auth-bars-old"),
            evidence_ledger=ledger,
            retention_by_authorization=retention,
        ).read_bars(
            context=context, symbol="NVDA", bar_days=30, bar_limit=1
        )
        new = MarketFactService(
            adapter=SnapshotAdapter("auth-bars-new"),
            evidence_ledger=ledger,
            retention_by_authorization=retention,
        ).read_bars(
            context=context, symbol="NVDA", bar_days=30, bar_limit=1
        )

        self.assertNotEqual(
            old.records[0].evidence_id, new.records[0].evidence_id
        )
        self.assertEqual(
            old.records[0].content_sha256, new.records[0].content_sha256
        )
        self.assertEqual(
            {record.authorization_snapshot_id for record in (*old.records, *new.records)},
            {"auth-bars-old", "auth-bars-new"},
        )

    def test_market_observation_identity_is_shared_across_bar_limit_projections(self) -> None:
        retention = {
            ("alpaca", "auth-alpaca_daily_bars"): "encrypted_payload",
        }
        ledger = InMemoryEvidenceStore(
            retention_by_authorization=retention
        )
        service = MarketFactService(
            adapter=MultiBarMarketAdapter(),
            evidence_ledger=ledger,
            retention_by_authorization=retention,
        )
        context = EvidenceReadContext(
            actor_id="actor-1",
            permissions=frozenset({"market:read"}),
            purpose="domain_tool",
            now=NOW,
        )

        today = service.read_bars(
            context=context, symbol="NVDA", bar_days=30, bar_limit=1
        )
        analysis = service.read_bars(
            context=context, symbol="NVDA", bar_days=30, bar_limit=2
        )
        frozen = ledger.freeze(context, (today.records[0].evidence_id,))[0]

        self.assertEqual(today.records[0].evidence_id, analysis.records[0].evidence_id)
        self.assertEqual(len(today.data["bars"]), 1)
        self.assertEqual(len(analysis.data["bars"]), 2)
        self.assertEqual(frozen.payload["count"], 3)
        self.assertEqual(len(frozen.payload["bars"]), 3)

    def test_market_revalidation_is_stable_per_refetched_observation(self) -> None:
        class RefetchedAdapter(FakeAiContextMarketAdapter):
            def __init__(self, fetched_at: datetime) -> None:
                super().__init__()
                self.fetched_at = fetched_at

            def observe_daily_bars(self, symbol, **kwargs):
                observed = super().observe_daily_bars(symbol, **kwargs)
                selected = replace(
                    observed.raw,
                    provenance=replace(
                        observed.raw.provenance,
                        fetched_at=self.fetched_at,
                    ),
                )
                return DailyBarsObservation(
                    raw=selected, provider_adjusted=selected
                )

        retention = {
            ("alpaca", "auth-alpaca_daily_bars"): "encrypted_payload",
        }
        ledger = InMemoryEvidenceStore(
            retention_by_authorization=retention
        )

        def read(*, now: datetime, fetched_at: datetime):
            return MarketFactService(
                adapter=RefetchedAdapter(fetched_at),
                evidence_ledger=ledger,
                retention_by_authorization=retention,
            ).read_bars(
                context=EvidenceReadContext(
                    actor_id="actor-1",
                    permissions=frozenset({"market:read"}),
                    purpose="domain_tool",
                    now=now,
                ),
                symbol="NVDA",
                bar_days=30,
                bar_limit=1,
            ).records[0]

        first = read(now=NOW, fetched_at=NOW)
        refetched_at = NOW + timedelta(hours=3)
        revalidated = read(now=refetched_at, fetched_at=refetched_at)
        reused = read(
            now=refetched_at + timedelta(minutes=1),
            fetched_at=refetched_at,
        )
        fetched_again_at = NOW + timedelta(hours=4)
        fetched_again = read(
            now=fetched_again_at, fetched_at=fetched_again_at
        )
        freeze_context = EvidenceReadContext(
            actor_id="actor-1",
            permissions=frozenset({"market:read"}),
            purpose="domain_tool",
            now=fetched_again_at,
        )
        frozen = ledger.freeze(
            freeze_context,
            (revalidated.evidence_id, fetched_again.evidence_id),
        )

        self.assertNotEqual(first.evidence_id, revalidated.evidence_id)
        self.assertEqual(revalidated.evidence_id, reused.evidence_id)
        self.assertNotEqual(revalidated.evidence_id, fetched_again.evidence_id)
        self.assertEqual(revalidated.fetched_at, refetched_at)
        self.assertEqual(
            revalidated.expires_at, refetched_at + timedelta(hours=2)
        )
        self.assertEqual(
            tuple(record.evidence_id for record in frozen),
            (revalidated.evidence_id, fetched_again.evidence_id),
        )

    def test_unknown_market_authorization_snapshot_fails_closed(self) -> None:
        ledger = InMemoryEvidenceStore(retention_by_authorization={})
        context = EvidenceReadContext(
            actor_id="actor-1",
            permissions=frozenset({"market:read"}),
            purpose="domain_tool",
            now=NOW,
        )

        with self.assertRaisesRegex(
            EvidenceLedgerError, "source_retention_unknown"
        ):
            MarketFactService(
                adapter=FakeAiContextMarketAdapter(),
                evidence_ledger=ledger,
                retention_by_authorization={},
            ).read_bars(
                context=context, symbol="NVDA", bar_days=30, bar_limit=1
            )

    def test_unauthorized_stale_and_unavailable_news_keep_portfolio_facts_working(self) -> None:
        source = SyntheticNewsSource(
            NewsSourceSnapshot(
                items=(
                    raw_news(
                        url="https://untrusted.example/1",
                        summary="未授权来源摘要",
                        symbols=("NVDA",),
                        source_type="web_search_summary",
                    ),
                    raw_news(
                        url="https://wire.example/expired",
                        summary="已过期来源摘要",
                        symbols=("NVDA",),
                        published_at=NOW - timedelta(days=15),
                    ),
                    raw_news(
                        url="https://wire.example/stale",
                        summary="抓取已过期的来源摘要",
                        symbols=("NVDA",),
                        fetched_at=NOW - timedelta(hours=3),
                    ),
                ),
                gaps=("source_unavailable",),
            )
        )
        registry = TodayDomainTools(
            portfolio_store=self.portfolio,
            watchlist=self.tools.watchlist,
            news_source=source,
            relation_map={"NVDA": ("AMD",)},
        ).registry()

        news = registry.invoke(
            "search_market_news",
            context=self.context,
            arguments={"symbols": ["NVDA"]},
        )
        today = registry.invoke(
            "get_today_context", context=self.context, arguments={}
        )
        candidate = registry.invoke(
            "discover_related_candidates",
            context=self.context,
            arguments={"subject_ids": ["NVDA"]},
        )

        self.assertEqual(news.status, "unavailable")
        self.assertEqual(today.status, "partial")
        self.assertEqual(today.data["active_holding_count"], 1)
        self.assertEqual(candidate.data["candidates"], [])
        self.assertTrue(
            {
                "source_unavailable",
                "source_unauthorized",
                "event_expired",
                "source_stale",
            }
            .issubset({gap.code for gap in today.gaps})
        )

    def test_investment_news_adapter_normalizes_snapshot_without_raw_envelope(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "scripts").mkdir()
        (root / "scripts" / "fetch.py").write_text("", encoding="utf-8")
        payload = {
            "industries": [
                {
                    "key": "semi",
                    "items": [
                        {
                            "title": "NVIDIA 发布结构化更新",
                            "url": "https://wire.example/nvda",
                            "ts": int((NOW - timedelta(hours=2)).timestamp()),
                            "summary": "NVIDIA 来源摘要。",
                            "source": "Synthetic Wire",
                            "zh": "英伟达更新",
                        }
                    ],
                }
            ]
        }
        (root / "data.js").write_text(
            "window.DATA = " + json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        fetched_at = NOW - timedelta(minutes=10)
        os.utime(
            root / "data.js",
            (fetched_at.timestamp(), fetched_at.timestamp()),
        )
        refresh_calls = []
        reader = InvestmentNewsReader(
            root,
            runner=lambda argv, cwd, _env: refresh_calls.append((argv, cwd)) or 0,
            cache_ttl_seconds=3600,
        )
        source = InvestmentNewsStructuredSource(reader)

        snapshot = source.read(
            context=FactNewsReadContext(
                permissions=frozenset({"news:read"}), purpose="domain_tool"
            ),
            now=NOW,
        )

        self.assertEqual(snapshot.gaps, ())
        self.assertEqual(snapshot.items[0].related_symbols, ("NVDA",))
        self.assertEqual(snapshot.items[0].source_type, "structured_news")
        self.assertEqual(snapshot.items[0].fetched_at, fetched_at)

        refresh_calls.clear()
        cached = InvestmentNewsStructuredSource(
            reader, refresh_before_read=False
        ).read(
            context=FactNewsReadContext(
                permissions=frozenset({"news:read"}), purpose="domain_tool"
            ),
            now=NOW,
        )
        self.assertEqual(cached.items[0].related_symbols, ("NVDA",))
        self.assertEqual(refresh_calls, [])

    def test_market_dossier_uses_ai_context_authorization_and_bounds(self) -> None:
        adapter = FakeAiContextMarketAdapter()
        retention = {
            ("alpaca", "auth-alpaca_assets"): "encrypted_payload",
            ("alpaca", "auth-alpaca_daily_bars"): "encrypted_payload",
        }
        ledger = InMemoryEvidenceStore(
            retention_by_authorization=retention
        )
        reader = MarketFactService(
            adapter=adapter,
            evidence_ledger=ledger,
            retention_by_authorization=retention,
        )

        result = reader.read_dossier(
            context=EvidenceReadContext(
                actor_id="actor-1",
                permissions=frozenset({"market:read"}),
                purpose="domain_tool",
                now=NOW,
            ),
            symbol="NVDA",
            bar_days=30,
            bar_limit=1,
        )

        self.assertEqual(result.data["count"], 1)
        self.assertEqual(result.data["bars"][0]["close"], "100")
        self.assertEqual(adapter.calls[0][2]["purpose"], "ai_context")
        self.assertEqual(adapter.calls[1][2]["purpose"], "ai_context")
        self.assertEqual(
            adapter.calls[1][2]["start_date"],
            (NOW - timedelta(days=30)).date(),
        )


if __name__ == "__main__":
    unittest.main()
