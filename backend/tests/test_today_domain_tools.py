from __future__ import annotations

from dataclasses import dataclass
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
    AiContextMarketDossierReader,
    InvestmentNewsStructuredSource,
    NewsSourceSnapshot,
    RawFactNews,
    TodayDomainTools,
)
from backend.app.personal_workspace.agent.tools_impl.news import InvestmentNewsReader
from backend.app.personal_workspace.contracts import PersonalActor
from backend.app.market_observation.contracts import (
    AssetIdentity,
    DailyBar,
    DailyBarsObservation,
    ObservedValue,
    ProvenanceEnvelope,
)
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

    def read(self, *, now: datetime) -> NewsSourceSnapshot:
        return self.snapshot


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

        evidence = self.invoke(
            "get_evidence", {"evidence_id": merged["evidence_id"]}
        )
        self.assertEqual(evidence.status, "success")
        self.assertEqual(evidence.data["url"], merged["url"])
        self.assertEqual(
            evidence.data["confirmation_state"],
            "source_summary_unconfirmed",
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

    def test_refetch_updates_metadata_without_changing_content_evidence_id(self) -> None:
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
        self.assertNotEqual(refreshed["fetched_at"], original["fetched_at"])
        self.assertEqual(evidence.data["fetched_at"], refreshed["fetched_at"])

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

    def test_cached_news_evidence_rechecks_current_source_and_ttl(self) -> None:
        news = self.invoke(
            "search_market_news", {"symbols": ["AMD"], "limit": 20}
        )
        evidence_id = news.data["items"][0]["evidence_id"]
        self.source.snapshot = NewsSourceSnapshot(
            items=(), gaps=("source_unavailable",)
        )
        unavailable = self.invoke(
            "get_evidence", {"evidence_id": evidence_id}
        )

        self.assertEqual(unavailable.status, "unavailable")
        self.assertEqual(unavailable.error_code, "source_unavailable")
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
        self.assertIn(expired.error_code, {"source_stale", "event_expired"})

    def test_legacy_kline_parameters_reach_market_reader_without_private_or_news(self) -> None:
        calls = []

        def dossier_reader(actor, symbol, now, bar_days, bar_limit):
            calls.append((actor.actor_id, symbol, now, bar_days, bar_limit))
            return {
                "symbol": symbol,
                "bars": [{"date": "2026-08-08", "close": "100"}],
                "count": 1,
            }

        registry = TodayDomainTools(
            portfolio_store=self.portfolio,
            watchlist=self.tools.watchlist,
            news_source=self.source,
            dossier_reader=dossier_reader,
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

        self.assertEqual(calls, [("actor-1", "NVDA", NOW, 30, 1)])
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
            runner=lambda argv, cwd: refresh_calls.append((argv, cwd)) or 0,
            cache_ttl_seconds=3600,
        )
        source = InvestmentNewsStructuredSource(reader)

        snapshot = source.read(now=NOW)

        self.assertEqual(snapshot.gaps, ())
        self.assertEqual(snapshot.items[0].related_symbols, ("NVDA",))
        self.assertEqual(snapshot.items[0].source_type, "structured_news")
        self.assertEqual(snapshot.items[0].fetched_at, fetched_at)

        refresh_calls.clear()
        cached = InvestmentNewsStructuredSource(
            reader, refresh_before_read=False
        ).read(now=NOW)
        self.assertEqual(cached.items[0].related_symbols, ("NVDA",))
        self.assertEqual(refresh_calls, [])

    def test_market_dossier_uses_ai_context_authorization_and_bounds(self) -> None:
        adapter = FakeAiContextMarketAdapter()
        reader = AiContextMarketDossierReader(adapter)

        result = reader(
            PersonalActor("actor-1"), "NVDA", NOW, 30, 1
        )

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["bars"][0]["close"], "100")
        self.assertEqual(adapter.calls[0][2]["purpose"], "ai_context")
        self.assertEqual(adapter.calls[1][2]["purpose"], "ai_context")
        self.assertEqual(
            adapter.calls[1][2]["start_date"],
            (NOW - timedelta(days=30)).date(),
        )


if __name__ == "__main__":
    unittest.main()
