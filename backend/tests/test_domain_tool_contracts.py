from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from backend.app.personal_workspace.agent.domain_tools import (
    DOMAIN_TOOL_DEFINITIONS,
    LEGACY_TOOL_ALIASES,
    DomainToolContext,
    DomainToolRegistry,
    DomainToolResult,
    EvidenceEnvelope,
    ToolGap,
)


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def evidence(evidence_id: str = "evidence:test:1") -> EvidenceEnvelope:
    return EvidenceEnvelope(
        evidence_id=evidence_id,
        source="synthetic",
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

    def test_alias_uses_canonical_contract_and_records_success_metrics(self) -> None:
        observations = []

        def handler(context: DomainToolContext, arguments: dict) -> DomainToolResult:
            self.assertEqual(context.actor_id, "actor-1")
            self.assertEqual(arguments, {})
            return DomainToolResult.success(
                data={"holdings": []},
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

        def handler(_context: DomainToolContext, arguments: dict) -> DomainToolResult:
            captured.append(arguments)
            return DomainToolResult.success(data={"ok": True}, evidence=(evidence(),))

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


if __name__ == "__main__":
    unittest.main()
