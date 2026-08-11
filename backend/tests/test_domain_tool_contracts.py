from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from backend.app.personal_workspace.agent.domain_tools import (
    DOMAIN_TOOL_DEFINITIONS,
    LEGACY_TOOL_DEFINITIONS,
    LEGACY_TOOL_ALIASES,
    DomainToolContext,
    DomainToolRegistry,
    DomainToolResult,
    EvidenceEnvelope,
    RuntimeToolDefinition,
    ToolGap,
)


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def evidence(
    evidence_id: str = "evidence:test:1", *, source: str = "synthetic"
) -> EvidenceEnvelope:
    return EvidenceEnvelope(
        evidence_id=evidence_id,
        source=source,
        as_of=NOW,
        content_sha256="a" * 64,
        authorized_fields=("symbol",),
    )


class DomainToolContractTest(unittest.TestCase):
    def test_registry_freezes_six_tools_and_legacy_aliases(self) -> None:
        self.assertEqual(
            tuple(item.name for item in DOMAIN_TOOL_DEFINITIONS),
            (
                "get_today_context",
                "get_symbol_dossier",
                "search_market_news",
                "search_web_evidence",
                "discover_related_candidates",
                "get_evidence",
            ),
        )
        self.assertEqual(
            LEGACY_TOOL_ALIASES,
            {
                "get_holdings": "get_today_context",
                "get_kline": "get_symbol_dossier",
                "get_news": "search_market_news",
            },
        )

    def test_default_discovery_returns_permitted_canonical_definitions_only(
        self,
    ) -> None:
        registry = DomainToolRegistry(handlers={})

        definitions = registry.definitions(
            permissions=frozenset(
                {
                    "portfolio:read",
                    "market:read",
                    "news:read",
                    "evidence:read",
                }
            )
        )

        self.assertEqual(
            tuple(item.name for item in definitions),
            (
                "discover_related_candidates",
                "get_evidence",
                "get_symbol_dossier",
                "get_today_context",
                "search_market_news",
            ),
        )
        self.assertNotIn("get_holdings", tuple(item.name for item in definitions))
        self.assertNotIn(
            "search_web_evidence", tuple(item.name for item in definitions)
        )

    def test_explicit_discovery_supports_aliases_and_silently_filters_unknown_or_denied(
        self,
    ) -> None:
        registry = DomainToolRegistry(handlers={})

        definitions = registry.definitions(
            permissions=frozenset({"portfolio:read"}),
            names=(
                "place_order",
                "get_symbol_dossier",
                "get_holdings",
                "get_holdings",
            ),
        )

        self.assertEqual(tuple(item.name for item in definitions), ("get_holdings",))
        self.assertEqual(definitions[0], LEGACY_TOOL_DEFINITIONS[0])
        self.assertEqual(
            definitions[0].required_permissions, frozenset({"portfolio:read"})
        )
        self.assertEqual(definitions[0].input_schema["properties"], {})
        self.assertNotIn("additionalProperties", definitions[0].input_schema)
        self.assertNotIn("最新价", definitions[0].description)

    def test_provider_neutral_projection_is_sorted_allowlisted_and_hides_permissions(
        self,
    ) -> None:
        registry = DomainToolRegistry(handlers={})

        projections = registry.projected_definitions(
            permissions=frozenset({"market:read", "news:read"}),
            names=(
                "search_market_news",
                "discover_related_candidates",
                "get_news",
                "get_today_context",
            ),
        )

        self.assertEqual(
            tuple(item.name for item in projections),
            ("discover_related_candidates", "get_news", "search_market_news"),
        )
        self.assertTrue(
            all(isinstance(item, RuntimeToolDefinition) for item in projections)
        )
        self.assertTrue(
            all(not hasattr(item, "required_permissions") for item in projections)
        )
        self.assertEqual(
            projections[1].input_schema["properties"],
            LEGACY_TOOL_DEFINITIONS[2].input_schema["properties"],
        )

    def test_unknown_and_permission_denied_discovery_have_identical_empty_projection(
        self,
    ) -> None:
        registry = DomainToolRegistry(handlers={})

        unknown = registry.projected_definitions(
            permissions=frozenset(), names=("place_order",)
        )
        denied = registry.projected_definitions(
            permissions=frozenset(), names=("get_evidence",)
        )

        self.assertEqual(unknown, ())
        self.assertEqual(denied, ())

    def test_alias_uses_canonical_contract_and_records_success_metrics(self) -> None:
        observations = []

        def handler(context: DomainToolContext, arguments: dict) -> DomainToolResult:
            self.assertEqual(context.actor_id, "actor-1")
            self.assertEqual(arguments, {})
            return DomainToolResult.success(
                data={"holdings": [], "count": 0, "usd_cash": "0"},
                evidence=(evidence("portfolio:snapshot:1"),),
                field_coverage=Decimal("1"),
                freshness_seconds=0,
                cost_usd=Decimal("0"),
            )

        registry = DomainToolRegistry(
            handlers={"get_today_context": handler},
            observation_recorder=observations.append,
        )
        result = registry.invoke(
            "get_holdings",
            context=DomainToolContext(
                actor_id="actor-1",
                granted_permissions=frozenset({"portfolio:read", "market:read"}),
                clock=lambda: NOW,
            ),
            arguments={},
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(result.evidence[0].evidence_id, "portfolio:snapshot:1")
        self.assertEqual(observations[0].tool_name, "get_today_context")
        self.assertEqual(observations[0].requested_name, "get_holdings")
        self.assertEqual(observations[0].field_coverage, Decimal("1"))
        self.assertEqual(observations[0].freshness_seconds, 0)

    def test_partial_and_stale_results_keep_proven_data_and_explicit_gaps(self) -> None:
        registry = DomainToolRegistry(
            handlers={
                "get_symbol_dossier": lambda _context, _arguments: DomainToolResult.partial(
                    data={"symbol": "NVDA", "price": None},
                    gaps=(ToolGap("field_missing", "price"),),
                    evidence=(evidence(),),
                    field_coverage=Decimal("0.5"),
                ),
                "search_market_news": lambda _context, _arguments: DomainToolResult.stale(
                    data={"items": [{"title": "已验证的旧事件"}]},
                    gaps=(ToolGap("source_stale", "structured_news"),),
                    evidence=(evidence(),),
                    freshness_seconds=7200,
                ),
            }
        )
        context = DomainToolContext(
            actor_id="actor-1",
            granted_permissions=frozenset({"portfolio:read", "market:read", "news:read"}),
            clock=lambda: NOW,
        )
        partial = registry.invoke(
            "get_symbol_dossier", context=context, arguments={"symbol": "NVDA"}
        )
        stale = registry.invoke(
            "search_market_news", context=context, arguments={"symbols": ["NVDA"]}
        )
        self.assertEqual(partial.status, "partial")
        self.assertEqual(partial.data["symbol"], "NVDA")
        self.assertEqual(partial.gaps[0].code, "field_missing")
        self.assertEqual(stale.status, "stale")
        self.assertEqual(stale.data["items"][0]["title"], "已验证的旧事件")
        self.assertEqual(stale.freshness_seconds, 7200)

    def test_unavailable_unauthorized_unknown_and_invalid_input_are_stable(self) -> None:
        registry = DomainToolRegistry(handlers={})
        no_permissions = DomainToolContext(
            actor_id="actor-1", granted_permissions=frozenset(), clock=lambda: NOW
        )
        allowed = DomainToolContext(
            actor_id="actor-1",
            granted_permissions=frozenset({"portfolio:read", "market:read"}),
            clock=lambda: NOW,
        )
        unauthorized = registry.invoke(
            "get_symbol_dossier",
            context=no_permissions,
            arguments={"symbol": "NVDA"},
        )
        unavailable = registry.invoke(
            "get_symbol_dossier", context=allowed, arguments={"symbol": "NVDA"}
        )
        unknown = registry.invoke("place_order", context=allowed, arguments={})
        invalid = registry.invoke("get_symbol_dossier", context=allowed, arguments={})
        self.assertEqual(unauthorized.error_code, "source_unauthorized")
        self.assertEqual(unauthorized.gaps[0].code, "source_unauthorized")
        self.assertEqual(unavailable.error_code, "tool_unavailable")
        self.assertEqual(unknown.error_code, "unknown_tool")
        self.assertEqual(invalid.error_code, "invalid_arguments")

    def test_web_search_is_explicitly_unavailable_without_a_handler(self) -> None:
        registry = DomainToolRegistry(handlers={})
        result = registry.invoke(
            "search_web_evidence",
            context=DomainToolContext(
                actor_id="actor-1",
                granted_permissions=frozenset({"web_evidence:read"}),
                clock=lambda: NOW,
            ),
            arguments={"query": "NVDA latest filing"},
        )
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.error_code, "tool_unavailable")
        self.assertEqual(result.data, {})

    def test_malformed_handler_output_is_rejected_at_the_registry_seam(self) -> None:
        registry = DomainToolRegistry(
            handlers={
                "get_today_context": lambda _context, _arguments: DomainToolResult.success(
                    data={"payload": {"choices": []}},
                    evidence=(
                        EvidenceEnvelope(
                            evidence_id="bad",
                            source="provider",
                            as_of=NOW,
                            content_sha256="not-a-sha256",
                            authorized_fields=(),
                        ),
                    ),
                    field_coverage=Decimal("2"),
                )
            }
        )
        result = registry.invoke(
            "get_today_context",
            context=DomainToolContext(
                actor_id="actor-1",
                granted_permissions=frozenset({"portfolio:read", "market:read"}),
                clock=lambda: NOW,
            ),
            arguments={},
        )
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.error_code, "tool_contract_invalid")

    def test_non_finite_nested_data_is_rejected_at_the_registry_seam(self) -> None:
        for value in (float("nan"), float("inf"), Decimal("-Infinity")):
            with self.subTest(value=value):
                registry = DomainToolRegistry(
                    handlers={
                        "get_today_context": lambda _context, _arguments: DomainToolResult.success(
                            data={"nested": [value]},
                            evidence=(evidence(),),
                        )
                    }
                )
                result = registry.invoke(
                    "get_today_context",
                    context=DomainToolContext(
                        actor_id="actor-1",
                        granted_permissions=frozenset(
                            {"portfolio:read", "market:read"}
                        ),
                        clock=lambda: NOW,
                    ),
                    arguments={},
                )
                self.assertEqual(result.error_code, "tool_contract_invalid")

    def test_each_canonical_tool_executes_its_input_and_permission_contract(self) -> None:
        registry = DomainToolRegistry(
            handlers={
                definition.name: (
                    lambda _context, _arguments: DomainToolResult.success(
                        data={"ok": True}, evidence=(evidence(),)
                    )
                )
                for definition in DOMAIN_TOOL_DEFINITIONS
            }
        )
        cases = (
            ("get_today_context", {}, {"portfolio:read", "market:read"}),
            ("get_symbol_dossier", {"symbol": "NVDA"}, {"portfolio:read", "market:read"}),
            ("search_market_news", {"symbols": ["NVDA"]}, {"news:read"}),
            ("search_web_evidence", {"query": "NVDA filing"}, {"web_evidence:read"}),
            (
                "discover_related_candidates",
                {"subject_ids": ["NVDA"]},
                {"market:read", "news:read"},
            ),
            ("get_evidence", {"evidence_id": "evidence:test:1"}, {"evidence:read"}),
        )
        for name, arguments, permissions in cases:
            with self.subTest(name=name):
                result = registry.invoke(
                    name,
                    context=DomainToolContext(
                        actor_id="actor-1",
                        granted_permissions=frozenset(permissions),
                        clock=lambda: NOW,
                    ),
                    arguments=arguments,
                )
                self.assertEqual(result.status, "success")

    def test_legacy_aliases_accept_legacy_arguments_and_permissions(self) -> None:
        captured = []

        responses = iter(
            (
                {"holdings": [], "count": 0, "usd_cash": "0"},
                {
                    "market": {
                        "symbol": "NVDA",
                        "adjustment": "raw",
                        "as_of": NOW.isoformat(),
                        "source_health": "fresh",
                        "bars": [],
                        "count": 0,
                    }
                },
                {"items": [], "count": 0},
            )
        )

        def handler(context: DomainToolContext, arguments: dict) -> DomainToolResult:
            captured.append(arguments)
            source = {
                "get_holdings": "personal_portfolio",
                "get_kline": "market_dossier",
            }.get(context.requested_name, "synthetic")
            return DomainToolResult.success(
                data=next(responses), evidence=(evidence(source=source),)
            )

        registry = DomainToolRegistry(
            handlers={
                "get_today_context": handler,
                "get_symbol_dossier": handler,
                "search_market_news": handler,
            }
        )
        cases = (
            ("get_holdings", {}, {"portfolio:read"}),
            ("get_kline", {"symbol": "NVDA", "days": 30, "limit": 20}, {"market:read"}),
            (
                "get_news",
                {"symbol": "NVDA", "keyword": "earnings", "sector": "semi", "limit": 5},
                {"news:read"},
            ),
        )
        for name, arguments, permissions in cases:
            result = registry.invoke(
                name,
                context=DomainToolContext(
                    actor_id="actor-1",
                    granted_permissions=frozenset(permissions),
                    clock=lambda: NOW,
                ),
                arguments=arguments,
            )
            self.assertEqual(result.status, "success")
        self.assertEqual(captured[0], {})
        self.assertEqual(captured[1], {"symbol": "NVDA", "bar_days": 30, "bar_limit": 20})
        self.assertEqual(
            captured[2],
            {"symbols": ["NVDA"], "query": "earnings", "sector": "semi", "limit": 5},
        )

    def test_legacy_aliases_ignore_extras_and_clamp_or_coerce_like_old_tools(self) -> None:
        captured = []

        def handler(context, arguments):
            captured.append((context.requested_name, arguments))
            if context.requested_name == "get_holdings":
                data = {"holdings": [], "count": 0, "usd_cash": "0"}
                source = "personal_portfolio"
            elif context.requested_name == "get_kline":
                data = {
                    "market": {
                        "symbol": "NVDA",
                        "adjustment": "raw",
                        "as_of": NOW.isoformat(),
                        "source_health": "fresh",
                        "bars": [],
                        "count": 0,
                    }
                }
                source = "market_dossier"
            else:
                data = {"items": [], "count": 0}
                source = "synthetic"
            return DomainToolResult.success(
                data=data, evidence=(evidence(source=source),)
            )

        registry = DomainToolRegistry(
            handlers={
                "get_today_context": handler,
                "get_symbol_dossier": handler,
                "search_market_news": handler,
            }
        )
        context = lambda permissions: DomainToolContext(
            actor_id="actor-1",
            granted_permissions=frozenset(permissions),
            clock=lambda: NOW,
        )

        holdings = registry.invoke(
            "get_holdings",
            context=context({"portfolio:read"}),
            arguments={"extra": "ignored"},
        )
        kline = registry.invoke(
            "get_kline",
            context=context({"market:read"}),
            arguments={"symbol": "nvda", "days": 1, "limit": "6", "extra": True},
        )
        news = registry.invoke(
            "get_news",
            context=context({"news:read"}),
            arguments={"limit": 21, "extra": "ignored"},
        )

        self.assertEqual((holdings.status, kline.status, news.status), ("success",) * 3)
        self.assertEqual(captured[0], ("get_holdings", {}))
        self.assertEqual(
            captured[1],
            ("get_kline", {"symbol": "NVDA", "bar_days": 10, "bar_limit": 6}),
        )
        self.assertEqual(captured[2], ("get_news", {"limit": 20}))
        self.assertEqual(
            news.data["note"], "未找到匹配新闻（可换关键词或赛道重试）"
        )

    def test_legacy_kline_strictly_projects_old_result_fields(self) -> None:
        registry = DomainToolRegistry(
            handlers={
                "get_symbol_dossier": lambda _context, _arguments: DomainToolResult.success(
                    data={
                        "market": {
                            "symbol": "NVDA",
                            "name": "NVIDIA",
                            "asset_class": "us_equity",
                            "adjustment": "raw",
                            "as_of": NOW.isoformat(),
                            "source_health": "fresh",
                            "bars": [
                                {
                                    "date": "2026-08-09",
                                    "open": "90",
                                    "high": "101",
                                    "low": "89",
                                    "close": "100",
                                    "volume": 1000,
                                    "internal_id": "bar-1",
                                }
                            ],
                            "count": 1,
                            "authorization_snapshot_ids": ["auth-1"],
                        },
                        "states": {"holding": True},
                    },
                    evidence=(evidence(source="market_dossier"),),
                )
            }
        )
        result = registry.invoke(
            "get_kline",
            context=DomainToolContext(
                actor_id="actor-1",
                granted_permissions=frozenset({"market:read"}),
                clock=lambda: NOW,
            ),
            arguments={"symbol": "NVDA"},
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(
            result.data,
            {
                "symbol": "NVDA",
                "adjustment": "raw",
                "as_of": NOW.isoformat(),
                "source_health": "fresh",
                "bars": [
                    {
                        "date": "2026-08-09",
                        "open": "90",
                        "high": "101",
                        "low": "89",
                        "close": "100",
                        "volume": 1000,
                    }
                ],
                "count": 1,
            },
        )
        self.assertEqual(result.evidence[0].source, "market_dossier")

    def test_legacy_holdings_only_projects_currently_equivalent_core_fields(self) -> None:
        registry = DomainToolRegistry(
            handlers={
                "get_today_context": lambda _context, _arguments: DomainToolResult.success(
                    data={
                        "holdings": [
                            {
                                "symbol": "NVDA",
                                "name": "NVIDIA",
                                "quantity": "10",
                                "average_cost": "90",
                                "currency": "USD",
                                "state": "active",
                                "current_price": "123.45",
                                "price_as_of": NOW.isoformat(),
                            }
                        ],
                        "count": 1,
                        "usd_cash": "500",
                    },
                    evidence=(evidence(source="personal_portfolio"),),
                )
            }
        )

        result = registry.invoke(
            "get_holdings",
            context=DomainToolContext(
                actor_id="actor-1",
                granted_permissions=frozenset({"portfolio:read"}),
                clock=lambda: NOW,
            ),
            arguments={},
        )

        self.assertEqual(
            result.data["holdings"][0],
            {
                "symbol": "NVDA",
                "name": "NVIDIA",
                "quantity": "10",
                "average_cost": "90",
                "currency": "USD",
                "state": "active",
            },
        )

    def test_legacy_news_deep_projects_authorized_safe_fields_without_shared_refs(
        self,
    ) -> None:
        related_symbols = ["NVDA"]
        item = {
            "event_id": "event-secret",
            "evidence_id": "news:1",
            "title": "NVIDIA 发布公告",
            "url": "https://example.com/news/1",
            "published_at": NOW.isoformat(),
            "fetched_at": NOW.isoformat(),
            "summary": "摘要",
            "content_sha256": "b" * 64,
            "source": "wire",
            "source_type": "structured_news",
            "sector": "semi",
            "related_symbols": related_symbols,
            "confirmation_state": "source_summary_unconfirmed",
            "internal": {"secret": "do-not-leak"},
        }
        envelope = EvidenceEnvelope(
            evidence_id="news:1",
            source="wire",
            as_of=NOW,
            content_sha256="a" * 64,
            authorized_fields=(
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
                "sector",
                "internal",
            ),
        )
        registry = DomainToolRegistry(
            handlers={
                "search_market_news": lambda _context, _arguments: DomainToolResult.success(
                    data={"items": [item], "count": 1}, evidence=(envelope,)
                )
            }
        )

        result = registry.invoke(
            "get_news",
            context=DomainToolContext(
                actor_id="actor-1",
                granted_permissions=frozenset({"news:read"}),
                clock=lambda: NOW,
            ),
            arguments={},
        )

        self.assertEqual(
            set(result.data["items"][0]),
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
        related_symbols.append("AMD")
        item["title"] = "已变异"
        self.assertEqual(result.data["items"][0]["title"], "NVIDIA 发布公告")
        self.assertEqual(result.data["items"][0]["related_symbols"], ["NVDA"])

    def test_legacy_news_rejects_incomplete_unmatched_or_untyped_items(self) -> None:
        safe_fields = (
            "title",
            "url",
            "published_at",
            "fetched_at",
            "summary",
            "source",
            "source_type",
            "related_symbols",
            "confirmation_state",
        )
        valid_item = {
            "evidence_id": "news:1",
            "title": "NVIDIA 发布公告",
            "url": "https://example.com/news/1",
            "published_at": NOW.isoformat(),
            "fetched_at": NOW.isoformat(),
            "summary": "摘要",
            "source": "wire",
            "source_type": "structured_news",
            "related_symbols": ["NVDA"],
            "confirmation_state": "source_summary_unconfirmed",
        }

        def envelope(*, evidence_id="news:1", authorized_fields=safe_fields):
            return EvidenceEnvelope(
                evidence_id=evidence_id,
                source="wire",
                as_of=NOW,
                content_sha256="a" * 64,
                authorized_fields=authorized_fields,
            )

        cases = (
            ({}, envelope()),
            ({**valid_item, "evidence_id": "news:missing"}, envelope()),
            (
                valid_item,
                envelope(
                    authorized_fields=tuple(
                        name for name in safe_fields if name != "summary"
                    )
                ),
            ),
            ({**valid_item, "related_symbols": "NVDA"}, envelope()),
        )
        for item, item_evidence in cases:
            with self.subTest(item=item, authorized=item_evidence.authorized_fields):
                registry = DomainToolRegistry(
                    handlers={
                        "search_market_news": lambda _context, _arguments, item=item, item_evidence=item_evidence: DomainToolResult.success(
                            data={"items": [item], "count": 1},
                            evidence=(item_evidence,),
                        )
                    }
                )
                result = registry.invoke(
                    "get_news",
                    context=DomainToolContext(
                        actor_id="actor-1",
                        granted_permissions=frozenset({"news:read"}),
                        clock=lambda: NOW,
                    ),
                    arguments={},
                )
                self.assertEqual(result.error_code, "tool_contract_invalid")

    def test_malformed_non_mapping_handler_data_is_contract_invalid(self) -> None:
        registry = DomainToolRegistry(
            handlers={
                "get_today_context": lambda _context, _arguments: DomainToolResult.success(
                    data=[], evidence=(evidence(),)  # type: ignore[arg-type]
                )
            }
        )

        result = registry.invoke(
            "get_today_context",
            context=DomainToolContext(
                actor_id="actor-1",
                granted_permissions=frozenset({"portfolio:read", "market:read"}),
                clock=lambda: NOW,
            ),
            arguments={},
        )

        self.assertEqual(result.error_code, "tool_contract_invalid")

    def test_legacy_results_require_complete_typed_count_consistent_contracts(
        self,
    ) -> None:
        valid_bar = {
            "date": "2026-08-09",
            "open": "90",
            "high": "101",
            "low": "89",
            "close": "100",
            "volume": 1000,
        }
        valid_kline = {
            "symbol": "NVDA",
            "adjustment": "raw",
            "as_of": NOW.isoformat(),
            "source_health": "fresh",
            "bars": [valid_bar],
            "count": 1,
        }
        valid_holding = {
            "symbol": "NVDA",
            "name": "NVIDIA",
            "quantity": "10",
            "average_cost": "90",
            "currency": "USD",
            "state": "active",
        }
        cases = (
            (
                "get_kline",
                "get_symbol_dossier",
                {"market": {name: value for name, value in valid_kline.items() if name != "as_of"}},
                {"symbol": "NVDA"},
                {"market:read"},
                "market_dossier",
            ),
            (
                "get_kline",
                "get_symbol_dossier",
                {"market": {**valid_kline, "count": 2}},
                {"symbol": "NVDA"},
                {"market:read"},
                "market_dossier",
            ),
            (
                "get_kline",
                "get_symbol_dossier",
                {"market": {**valid_kline, "bars": [{name: value for name, value in valid_bar.items() if name != "volume"}]}},
                {"symbol": "NVDA"},
                {"market:read"},
                "market_dossier",
            ),
            (
                "get_holdings",
                "get_today_context",
                {"holdings": [valid_holding], "count": 1},
                {},
                {"portfolio:read"},
                "personal_portfolio",
            ),
            (
                "get_holdings",
                "get_today_context",
                {"holdings": [{**valid_holding, "quantity": 10}], "count": 1, "usd_cash": "0"},
                {},
                {"portfolio:read"},
                "personal_portfolio",
            ),
            (
                "get_holdings",
                "get_today_context",
                {"holdings": [valid_holding], "count": 0, "usd_cash": "0"},
                {},
                {"portfolio:read"},
                "personal_portfolio",
            ),
            (
                "get_news",
                "search_market_news",
                {"items": [], "count": 1},
                {},
                {"news:read"},
                "structured_news",
            ),
            (
                "get_news",
                "search_market_news",
                {"items": "not-a-list", "count": 0},
                {},
                {"news:read"},
                "structured_news",
            ),
        )
        for alias, canonical, data, arguments, permissions, source in cases:
            with self.subTest(alias=alias, data=data):
                registry = DomainToolRegistry(
                    handlers={
                        canonical: lambda _context, _arguments, data=data, source=source: DomainToolResult.success(
                            data=data, evidence=(evidence(source=source),)
                        )
                    }
                )
                result = registry.invoke(
                    alias,
                    context=DomainToolContext(
                        actor_id="actor-1",
                        granted_permissions=frozenset(permissions),
                        clock=lambda: NOW,
                    ),
                    arguments=arguments,
                )
                self.assertEqual(result.error_code, "tool_contract_invalid")

    def test_registry_snapshots_catalogs_and_returned_schemas_are_isolated(self) -> None:
        registry = DomainToolRegistry(handlers={})
        canonical_format = DOMAIN_TOOL_DEFINITIONS[0].input_schema["properties"][
            "as_of"
        ]["format"]
        legacy_properties = LEGACY_TOOL_DEFINITIONS[0].input_schema["properties"]
        try:
            DOMAIN_TOOL_DEFINITIONS[0].input_schema["properties"]["as_of"][
                "format"
            ] = "date"
            legacy_properties["polluted"] = {"type": "string"}
            discovered = registry.definitions(
                permissions=frozenset({"portfolio:read", "market:read"})
            )
            aliases = registry.definitions(
                permissions=frozenset({"portfolio:read"}), names=("get_holdings",)
            )
            discovered[0].input_schema["properties"]["mutated"] = {
                "type": "string"
            }
        finally:
            DOMAIN_TOOL_DEFINITIONS[0].input_schema["properties"]["as_of"][
                "format"
            ] = canonical_format
            legacy_properties.pop("polluted", None)

        today = next(item for item in discovered if item.name == "get_today_context")
        self.assertEqual(today.input_schema["properties"]["as_of"]["format"], "date-time")
        self.assertNotIn("polluted", aliases[0].input_schema["properties"])
        fresh = registry.definitions(
            permissions=frozenset({"portfolio:read", "market:read"})
        )
        self.assertNotIn("mutated", fresh[0].input_schema["properties"])

    def test_canonical_date_time_format_is_validated_before_handler(self) -> None:
        called = []
        registry = DomainToolRegistry(
            handlers={
                "get_today_context": lambda _context, arguments: called.append(arguments)
            }
        )

        context = DomainToolContext(
            actor_id="actor-1",
            granted_permissions=frozenset({"portfolio:read", "market:read"}),
            clock=lambda: NOW,
        )
        for value in (
            "not-a-date",
            "2026-08-10T12:00Z",
            "20260810T120000Z",
            "2026-W33-1T12:00:00Z",
        ):
            with self.subTest(value=value):
                result = registry.invoke(
                    "get_today_context",
                    context=context,
                    arguments={"as_of": value},
                )
                self.assertEqual(result.error_code, "invalid_arguments")
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
