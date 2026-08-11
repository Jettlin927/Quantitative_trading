from __future__ import annotations

import asyncio
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event, Lock
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from collections.abc import Iterator, Mapping

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from backend.app.personal_workspace.agent.domain_tools import (
    DomainToolRegistry,
    DomainToolResult,
    EvidenceEnvelope,
)
from backend.app.personal_workspace.agent.evidence import (
    EvidenceReadContext,
    InMemoryEvidenceStore,
)
from backend.app.personal_workspace.mcp_gateway import (
    PERSONAL_MCP_MAX_OUTPUT_BYTES,
    PERSONAL_MCP_TOOL_ALLOWLIST,
    PersonalMcpGatewayStopped,
    PersonalMcpGateway,
    _result_payload,
    encoded_call_tool_result_size,
)
from backend.app.personal_workspace.mcp_server import (
    PersonalMcpConfigurationError,
    PersonalMcpConfig,
    build_personal_mcp_gateway,
    load_mcp_config,
    main,
    run_from_environment,
)


class PersonalMcpServerTest(unittest.TestCase):
    def test_default_disabled_path_has_no_composition_or_stdio_side_effects(self) -> None:
        calls: list[str] = []

        exit_code = run_from_environment(
            {},
            services_builder=lambda _config: calls.append("composition"),
            stdio_runner=lambda _adapter: calls.append("stdio"),
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(calls, [])

    def test_discovery_is_exactly_the_five_canonical_read_only_tools(self) -> None:
        adapter = PersonalMcpGateway(
            registry=DomainToolRegistry(handlers={}),
            audit_store=InMemoryEvidenceStore(retention_by_authorization={}),
            actor_id="fixed-actor",
        )

        definitions = adapter.tool_definitions()

        self.assertEqual(
            tuple(item.name for item in definitions),
            tuple(sorted(PERSONAL_MCP_TOOL_ALLOWLIST)),
        )
        self.assertNotIn("search_web_evidence", PERSONAL_MCP_TOOL_ALLOWLIST)
        self.assertNotIn("get_holdings", PERSONAL_MCP_TOOL_ALLOWLIST)

    def test_tool_definitions_return_independent_nested_schemas(self) -> None:
        registry = DomainToolRegistry(handlers={})
        registry_projection = registry.projected_definitions(
            permissions=frozenset(
                {
                    "portfolio:read",
                    "market:read",
                    "news:read",
                    "evidence:read",
                }
            ),
            names=tuple(PERSONAL_MCP_TOOL_ALLOWLIST),
        )
        adapter = PersonalMcpGateway(
            registry=registry,
            audit_store=InMemoryEvidenceStore(retention_by_authorization={}),
            actor_id="fixed-actor",
        )
        expected = deepcopy(adapter.tool_definitions())
        first = {
            item.name: item for item in adapter.tool_definitions()
        }["get_symbol_dossier"]

        first.input_schema["properties"]["symbol"]["type"] = "integer"
        first.input_schema["required"].append("poisoned")

        self.assertEqual(adapter.tool_definitions(), expected)
        self.assertEqual(
            registry.projected_definitions(
                permissions=frozenset(
                    {
                        "portfolio:read",
                        "market:read",
                        "news:read",
                        "evidence:read",
                    }
                ),
                names=tuple(PERSONAL_MCP_TOOL_ALLOWLIST),
            ),
            registry_projection,
        )

    def test_enabled_configuration_requires_fixed_actor_and_all_local_sources(self) -> None:
        base = {
            "PERSONAL_MCP_ENABLED": "true",
            "PERSONAL_MCP_ACTOR_ID": "actor-a",
            "PRIVATE_DATABASE_URL": "postgresql+psycopg://localhost/private",
            "PERSONAL_DATA_KEYRING_FILE": "/private/keyring.json",
            "ALPACA_CREDENTIALS_FILE": "/private/alpaca.json",
            "ALPACA_AUTHORIZATION_FILE": "/private/authorization.json",
            "INVESTMENT_NEWS_DIR": "/private/investment-news",
        }

        config = load_mcp_config(base)

        self.assertTrue(config.enabled)
        self.assertEqual(config.actor_id, "actor-a")
        for field in tuple(base)[1:]:
            with self.subTest(field=field):
                incomplete = dict(base)
                incomplete.pop(field)
                with self.assertRaisesRegex(
                    PersonalMcpConfigurationError, "personal_mcp_unconfigured"
                ):
                    load_mcp_config(incomplete)
        for actor_id in ("actor with spaces", "a" * 129, "控制字符\n"):
            with self.subTest(actor_id=actor_id):
                with self.assertRaisesRegex(
                    PersonalMcpConfigurationError, "personal_mcp_actor_invalid"
                ):
                    load_mcp_config({**base, "PERSONAL_MCP_ACTOR_ID": actor_id})

    def test_disabled_and_invalid_startup_paths_never_pollute_stdout(self) -> None:
        root = Path(__file__).resolve().parents[2]
        base_environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(root),
        }

        disabled = subprocess.run(
            [sys.executable, "-m", "backend.app.personal_workspace.mcp_server"],
            cwd=root,
            env=base_environment,
            capture_output=True,
            check=False,
        )
        invalid = subprocess.run(
            [sys.executable, "-m", "backend.app.personal_workspace.mcp_server"],
            cwd=root,
            env={**base_environment, "PERSONAL_MCP_ENABLED": "yes"},
            capture_output=True,
            check=False,
        )

        self.assertEqual(disabled.returncode, 2)
        self.assertEqual(disabled.stdout, b"")
        self.assertEqual(disabled.stderr, b"personal_mcp_disabled\n")
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, b"")
        self.assertEqual(invalid.stderr, b"personal_mcp_enabled_invalid\n")

    def test_database_url_failures_are_redacted_by_real_module_entrypoint(self) -> None:
        root = Path(__file__).resolve().parents[2]
        base_environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(root),
            "PERSONAL_MCP_ENABLED": "true",
            "PERSONAL_MCP_ACTOR_ID": "fixed-actor",
            "PERSONAL_DATA_KEYRING_FILE": "/not-used/keyring.json",
            "ALPACA_CREDENTIALS_FILE": "/not-used/alpaca.json",
            "ALPACA_AUTHORIZATION_FILE": "/not-used/authorization.json",
            "INVESTMENT_NEWS_DIR": "/not-used/news",
        }
        database_urls = (
            "postgresql://actor:WRONG_DRIVER_SECRET@localhost/private",
            "postgresql+psycopg://actor:MALFORMED_SECRET@localhost:not-a-port/private",
        )

        for database_url in database_urls:
            with self.subTest(database_url=database_url.split(":", 1)[0]):
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "backend.app.personal_workspace.mcp_server",
                    ],
                    cwd=root,
                    env={**base_environment, "PRIVATE_DATABASE_URL": database_url},
                    capture_output=True,
                    check=False,
                )

                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, b"")
                self.assertEqual(
                    completed.stderr, b"personal_mcp_database_invalid\n"
                )
                self.assertNotIn(b"SECRET", completed.stderr)
                self.assertNotIn(b"Traceback", completed.stderr)

    def test_runtime_and_close_exceptions_are_redacted_by_main(self) -> None:
        environment = {
            "PERSONAL_MCP_ENABLED": "true",
            "PERSONAL_MCP_ACTOR_ID": "fixed-actor",
            "PRIVATE_DATABASE_URL": "postgresql+psycopg://localhost/private",
            "PERSONAL_DATA_KEYRING_FILE": "/not-used/keyring.json",
            "ALPACA_CREDENTIALS_FILE": "/not-used/alpaca.json",
            "ALPACA_AUTHORIZATION_FILE": "/not-used/authorization.json",
            "INVESTMENT_NEWS_DIR": "/not-used/news",
        }

        class CloseFailingGateway:
            def close(self) -> None:
                raise RuntimeError("CLOSE_SECRET")

        async def fail_from_close(gateway: CloseFailingGateway) -> None:
            gateway.close()

        cases = (
            (object(), AsyncMock(side_effect=RuntimeError("RUNTIME_SECRET"))),
            (
                object(),
                AsyncMock(
                    side_effect=ExceptionGroup(
                        "GROUP_SECRET", [RuntimeError("NESTED_SECRET")]
                    )
                ),
            ),
            (CloseFailingGateway(), AsyncMock(side_effect=fail_from_close)),
        )
        for gateway, stdio_runner in cases:
            with self.subTest(gateway=type(gateway).__name__):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    patch(
                        "backend.app.personal_workspace.mcp_server."
                        "build_personal_mcp_gateway",
                        return_value=gateway,
                    ),
                    patch(
                        "backend.app.personal_workspace.mcp_server."
                        "serve_stdio_gateway",
                        new=stdio_runner,
                    ),
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                ):
                    exit_code = main(environment)

                self.assertEqual(exit_code, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(
                    stderr.getvalue(), "personal_mcp_runtime_failed\n"
                )
                self.assertNotIn("SECRET", stderr.getvalue())
                self.assertNotIn("Traceback", stderr.getvalue())

        with (
            patch(
                "backend.app.personal_workspace.mcp_server."
                "build_personal_mcp_gateway",
                return_value=object(),
            ),
            patch(
                "backend.app.personal_workspace.mcp_server."
                "serve_stdio_gateway",
                new=AsyncMock(side_effect=SystemExit(7)),
            ),
        ):
            with self.assertRaisesRegex(SystemExit, "7"):
                main(environment)

    def test_unexpected_builder_exception_is_redacted_without_swallowing_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            news_dir = Path(temporary_directory)
            (news_dir / "scripts").mkdir()
            (news_dir / "scripts" / "fetch.py").touch()
            config = PersonalMcpConfig(
                enabled=True,
                actor_id="fixed-actor",
                database_url=(
                    "postgresql+psycopg://actor:BUILDER_SECRET@localhost/private"
                ),
                keyring_file="/not-used/keyring.json",
                alpaca_credentials_file="/not-used/alpaca.json",
                alpaca_authorization_file="/not-used/authorization.json",
                investment_news_dir=str(news_dir),
            )
            environment = {
                "PERSONAL_MCP_ENABLED": "true",
                "PERSONAL_MCP_ACTOR_ID": config.actor_id,
                "PRIVATE_DATABASE_URL": config.database_url,
                "PERSONAL_DATA_KEYRING_FILE": config.keyring_file,
                "ALPACA_CREDENTIALS_FILE": config.alpaca_credentials_file,
                "ALPACA_AUTHORIZATION_FILE": config.alpaca_authorization_file,
                "INVESTMENT_NEWS_DIR": config.investment_news_dir,
            }
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch(
                    "backend.app.personal_workspace.crypto.load_owner_only_keyring_file",
                    return_value=object(),
                ),
                patch(
                    "backend.app.personal_workspace.composition.build_personal_services",
                    side_effect=RuntimeError("BUILDER_SECRET must not escape"),
                ),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = main(environment)

            self.assertEqual(exit_code, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "personal_mcp_source_invalid\n")
            self.assertNotIn("SECRET", stderr.getvalue())
            self.assertNotIn(config.database_url, stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

            for terminal_exception in (KeyboardInterrupt(), SystemExit(7)):
                with (
                    patch(
                        "backend.app.personal_workspace.crypto.load_owner_only_keyring_file",
                        return_value=object(),
                    ),
                    patch(
                        "backend.app.personal_workspace.composition.build_personal_services",
                        side_effect=terminal_exception,
                    ),
                ):
                    with self.assertRaises(type(terminal_exception)):
                        build_personal_mcp_gateway(config)

    def test_build_requires_existing_actor_workspace_through_read_only_load(self) -> None:
        class ReadOnlyPortfolioStore:
            def __init__(self, workspace_id: str | None) -> None:
                self.workspace_id = workspace_id
                self.actor_ids: list[str] = []

            def load(self, *, actor_id: str):
                self.actor_ids.append(actor_id)
                return SimpleNamespace(workspace_id=self.workspace_id)

        with tempfile.TemporaryDirectory() as temporary_directory:
            news_dir = Path(temporary_directory)
            (news_dir / "scripts").mkdir()
            (news_dir / "scripts" / "fetch.py").touch()
            config = PersonalMcpConfig(
                enabled=True,
                actor_id="fixed-actor",
                database_url="postgresql+psycopg://localhost/private",
                keyring_file="/not-used/keyring.json",
                alpaca_credentials_file="/not-used/alpaca.json",
                alpaca_authorization_file="/not-used/authorization.json",
                investment_news_dir=str(news_dir),
            )

            def services(portfolio_store: ReadOnlyPortfolioStore):
                return SimpleNamespace(
                    portfolio_store=portfolio_store,
                    market_readers=SimpleNamespace(market=object()),
                    domain_tools=DomainToolRegistry(handlers={}),
                    evidence_store=InMemoryEvidenceStore(
                        retention_by_authorization={}
                    ),
                )

            unknown_store = ReadOnlyPortfolioStore(None)
            with (
                patch(
                    "backend.app.personal_workspace.crypto.load_owner_only_keyring_file",
                    return_value=object(),
                ),
                patch(
                    "backend.app.personal_workspace.composition.build_personal_services",
                    return_value=services(unknown_store),
                ),
            ):
                with self.assertRaisesRegex(
                    PersonalMcpConfigurationError,
                    "^personal_mcp_actor_unknown$",
                ):
                    build_personal_mcp_gateway(config)

            existing_store = ReadOnlyPortfolioStore("workspace-existing")
            with (
                patch(
                    "backend.app.personal_workspace.crypto.load_owner_only_keyring_file",
                    return_value=object(),
                ),
                patch(
                    "backend.app.personal_workspace.composition.build_personal_services",
                    return_value=services(existing_store),
                ),
            ):
                gateway = build_personal_mcp_gateway(config)
                gateway.close()

            self.assertEqual(unknown_store.actor_ids, ["fixed-actor"])
            self.assertEqual(existing_store.actor_ids, ["fixed-actor"])

    def test_build_uses_owner_only_keyring_and_market_source_loaders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            news_dir = Path(temporary_directory)
            (news_dir / "scripts").mkdir()
            (news_dir / "scripts" / "fetch.py").touch()
            config = PersonalMcpConfig(
                enabled=True,
                actor_id="fixed-actor",
                database_url="postgresql+psycopg://localhost/private",
                keyring_file="/strict/keyring.json",
                alpaca_credentials_file="/strict/alpaca.json",
                alpaca_authorization_file="/strict/authorization.json",
                investment_news_dir=str(news_dir),
            )
            market_readers = SimpleNamespace(market=object())
            services = SimpleNamespace(
                portfolio_store=SimpleNamespace(
                    load=lambda **_kwargs: SimpleNamespace(
                        workspace_id="workspace-existing"
                    )
                ),
                market_readers=market_readers,
                domain_tools=DomainToolRegistry(handlers={}),
                evidence_store=InMemoryEvidenceStore(
                    retention_by_authorization={}
                ),
            )
            with (
                patch(
                    "backend.app.personal_workspace.crypto."
                    "load_owner_only_keyring_file",
                    return_value=object(),
                ) as keyring_loader,
                patch(
                    "backend.app.personal_workspace.market_runtime."
                    "load_owner_only_personal_market_readers",
                    return_value=market_readers,
                ) as market_loader,
                patch(
                    "backend.app.personal_workspace.composition."
                    "build_personal_services",
                    return_value=services,
                ) as services_builder,
            ):
                gateway = build_personal_mcp_gateway(config)
                gateway.close()

            keyring_loader.assert_called_once_with(config.keyring_file)
            market_loader.assert_called_once_with(
                credentials_file=config.alpaca_credentials_file,
                authorization_file=config.alpaca_authorization_file,
            )
            self.assertIs(
                services_builder.call_args.kwargs["market_readers"],
                market_readers,
            )
            self.assertEqual(
                services_builder.call_args.kwargs["evidence_workspace_mode"],
                "existing_only",
            )


class PersonalMcpAdapterAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_call_binds_actor_permissions_purpose_and_minimal_audit(self) -> None:
        contexts = []
        audit_contexts: list[EvidenceReadContext] = []
        store = InMemoryEvidenceStore(retention_by_authorization={})
        original_append = store.append_audit

        def append_audit(context, event) -> None:
            audit_contexts.append(context)
            original_append(context, event)

        store.append_audit = append_audit  # type: ignore[method-assign]
        registry = DomainToolRegistry(
            handlers={
                "get_today_context": lambda context, _arguments: (
                    contexts.append(context)
                    or DomainToolResult.success(
                        data={"actor": context.actor_id},
                        evidence=(
                            EvidenceEnvelope(
                                evidence_id="evidence:test:fixed",
                                source="synthetic",
                                as_of=datetime(2026, 8, 11, tzinfo=timezone.utc),
                                content_sha256="a" * 64,
                                authorized_fields=("actor",),
                            ),
                        ),
                        field_coverage=Decimal("1"),
                    )
                )
            }
        )
        adapter = PersonalMcpGateway(
            registry=registry,
            audit_store=store,
            actor_id="fixed-actor",
            clock=lambda: datetime(2026, 8, 11, tzinfo=timezone.utc),
        )

        result = await adapter.call_tool(
            "get_today_context", {"as_of": "2026-08-11T00:00:00Z"}
        )

        self.assertEqual(result["data"], {"actor": "fixed-actor"})
        self.assertEqual(contexts[0].actor_id, "fixed-actor")
        self.assertEqual(
            contexts[0].granted_permissions,
            frozenset(
                {"portfolio:read", "market:read", "news:read", "evidence:read"}
            ),
        )
        audit = store.audits_for_actor("fixed-actor")[0]
        self.assertEqual(audit.channel, "mcp_stdio")
        self.assertEqual(audit.canonical_tool, "get_today_context")
        self.assertEqual(
            audit.arguments_sha256,
            hashlib.sha256(
                json.dumps(
                    {"as_of": "2026-08-11T00:00:00Z"},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
        )
        self.assertEqual(audit_contexts[0].purpose, "mcp_stdio")
        self.assertEqual(audit_contexts[0].permissions, contexts[0].granted_permissions)

    async def test_non_allowlisted_tool_is_not_invoked_and_is_audited(self) -> None:
        calls: list[str] = []
        store = InMemoryEvidenceStore(retention_by_authorization={})
        adapter = PersonalMcpGateway(
            registry=DomainToolRegistry(
                handlers={
                    "search_web_evidence": lambda _context, _arguments: (
                        calls.append("web")
                        or DomainToolResult.unavailable("unexpected", "web")
                    )
                }
            ),
            audit_store=store,
            actor_id="fixed-actor",
        )

        result = await adapter.call_tool(
            "search_web_evidence", {"query": "must-not-run"}
        )

        self.assertEqual(result["error_code"], "unknown_tool")
        self.assertEqual(calls, [])
        audit = store.audits_for_actor("fixed-actor")[0]
        self.assertEqual(audit.error_code, "unknown_tool")
        self.assertRegex(audit.canonical_tool, r"^rejected_tool:[0-9a-f]{16}$")
        self.assertNotIn("search_web_evidence", audit.canonical_tool)

    async def test_unbounded_unknown_name_is_reduced_to_bounded_audit_sentinel(self) -> None:
        store = InMemoryEvidenceStore(retention_by_authorization={})
        adapter = PersonalMcpGateway(
            registry=DomainToolRegistry(handlers={}),
            audit_store=store,
            actor_id="fixed-actor",
        )

        result = await adapter.call_tool("x" * (1024 * 1024), {})

        self.assertEqual(result["error_code"], "unknown_tool")
        audit = store.audits_for_actor("fixed-actor")[0]
        self.assertRegex(audit.canonical_tool, r"^rejected_tool:[0-9a-f]{16}$")
        self.assertLessEqual(len(audit.canonical_tool), 30)

    async def test_process_accepts_at_most_thirty_calls_per_minute(self) -> None:
        store = InMemoryEvidenceStore(retention_by_authorization={})
        adapter = PersonalMcpGateway(
            registry=DomainToolRegistry(handlers={}),
            audit_store=store,
            actor_id="fixed-actor",
            monotonic=lambda: 100.0,
        )

        results = [
            await adapter.call_tool("get_evidence", {"evidence_id": f"e-{index}"})
            for index in range(31)
        ]

        self.assertNotEqual(results[29]["error_code"], "rate_limited")
        self.assertEqual(results[30]["error_code"], "rate_limited")
        self.assertEqual(len(store.audits_for_actor("fixed-actor")), 31)

    async def test_rate_limited_attempts_keep_actual_stable_parameter_hashes_only(self) -> None:
        store = InMemoryEvidenceStore(retention_by_authorization={})
        adapter = PersonalMcpGateway(
            registry=DomainToolRegistry(handlers={}),
            audit_store=store,
            actor_id="fixed-actor",
            monotonic=lambda: 150.0,
        )
        for index in range(30):
            await adapter.call_tool(
                "get_evidence", {"evidence_id": f"warmup-{index}"}
            )

        first = await adapter.call_tool(
            "get_evidence", {"evidence_id": "audit-secret-alpha"}
        )
        second = await adapter.call_tool(
            "search_market_news", {"symbols": ["NVDA"], "limit": 3}
        )
        repeated = await adapter.call_tool(
            "get_evidence", {"evidence_id": "audit-secret-alpha"}
        )

        self.assertEqual(first["error_code"], "rate_limited")
        self.assertEqual(second["error_code"], "rate_limited")
        self.assertEqual(repeated["error_code"], "rate_limited")
        limited_audits = store.audits_for_actor("fixed-actor")[-3:]
        self.assertNotEqual(
            limited_audits[0].arguments_sha256,
            limited_audits[1].arguments_sha256,
        )
        self.assertEqual(
            limited_audits[0].arguments_sha256,
            limited_audits[2].arguments_sha256,
        )
        persisted_audit = repr(limited_audits)
        self.assertNotIn("audit-secret-alpha", persisted_audit)
        self.assertNotIn("NVDA", persisted_audit)

    async def test_all_invalid_attempts_count_and_rate_limit_precedes_validation(self) -> None:
        store = InMemoryEvidenceStore(retention_by_authorization={})
        adapter = PersonalMcpGateway(
            registry=DomainToolRegistry(handlers={}),
            audit_store=store,
            actor_id="fixed-actor",
            monotonic=lambda: 200.0,
        )

        invalid = [
            await adapter.call_tool("get_evidence", {"evidence_id": float("nan")})
            for _ in range(30)
        ]
        limited = await adapter.call_tool(
            "search_web_evidence", {"query": "not-discoverable"}
        )

        self.assertTrue(all(item["error_code"] == "invalid_arguments" for item in invalid))
        self.assertEqual(limited["error_code"], "rate_limited")
        self.assertEqual(len(store.audits_for_actor("fixed-actor")), 31)

    async def test_uncanonicalizable_arguments_fail_closed_without_raw_audit_data(self) -> None:
        class ExplodingArguments(Mapping[str, object]):
            def __getitem__(self, _key: str) -> object:
                raise RuntimeError("RAW_ARGUMENT_SECRET")

            def __iter__(self) -> Iterator[str]:
                raise RuntimeError("RAW_ARGUMENT_SECRET")

            def __len__(self) -> int:
                return 1

        store = InMemoryEvidenceStore(retention_by_authorization={})
        adapter = PersonalMcpGateway(
            registry=DomainToolRegistry(handlers={}),
            audit_store=store,
            actor_id="fixed-actor",
            monotonic=lambda: 300.0,
        )
        invalid = await adapter.call_tool(
            "get_evidence", ExplodingArguments()
        )
        for _ in range(29):
            await adapter.call_tool("get_evidence", {})

        limited = await adapter.call_tool(
            "get_evidence", ExplodingArguments()
        )

        self.assertEqual(invalid["error_code"], "invalid_arguments")
        self.assertEqual(limited["error_code"], "rate_limited")
        self.assertEqual(len(store.audits_for_actor("fixed-actor")), 31)
        first_audit = store.audits_for_actor("fixed-actor")[0]
        last_audit = store.audits_for_actor("fixed-actor")[-1]
        self.assertEqual(
            first_audit.arguments_sha256, last_audit.arguments_sha256
        )
        self.assertNotIn(
            "RAW_ARGUMENT_SECRET", repr((first_audit, last_audit))
        )

    async def test_timeout_keeps_both_slots_occupied_until_workers_exit(self) -> None:
        release = Event()
        started = Event()
        state_lock = Lock()
        active = 0
        maximum_active = 0

        def handler(context, _arguments):
            nonlocal active, maximum_active
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
                if active == 2:
                    started.set()
            release.wait(1)
            with state_lock:
                active -= 1
            return DomainToolResult.success(
                data={"actor": context.actor_id},
                evidence=(
                    EvidenceEnvelope(
                        evidence_id="evidence:test:worker",
                        source="synthetic",
                        as_of=datetime(2026, 8, 11, tzinfo=timezone.utc),
                        content_sha256="b" * 64,
                        authorized_fields=("actor",),
                    ),
                ),
            )

        store = InMemoryEvidenceStore(retention_by_authorization={})
        adapter = PersonalMcpGateway(
            registry=DomainToolRegistry(handlers={"get_today_context": handler}),
            audit_store=store,
            actor_id="fixed-actor",
            deadline_seconds=0.03,
        )

        first = asyncio.create_task(adapter.call_tool("get_today_context", {}))
        second = asyncio.create_task(adapter.call_tool("get_today_context", {}))
        self.assertTrue(await asyncio.to_thread(started.wait, 0.5))
        rejected = await adapter.call_tool("get_today_context", {})
        timed_out = await asyncio.gather(first, second)
        still_rejected = await adapter.call_tool("get_today_context", {})

        self.assertEqual(rejected["error_code"], "concurrency_limited")
        self.assertEqual(
            [item["error_code"] for item in timed_out],
            ["tool_deadline_exceeded", "tool_deadline_exceeded"],
        )
        self.assertEqual(still_rejected["error_code"], "concurrency_limited")
        self.assertEqual(maximum_active, 2)
        release.set()
        await asyncio.sleep(0.05)
        recovered = await adapter.call_tool("get_today_context", {})
        self.assertEqual(recovered["status"], "success")

    async def test_inflight_limit_holds_until_blocked_audit_reaches_terminal_state(self) -> None:
        audit_entered = Event()
        release_audit = Event()
        handlers_finished = Event()
        handler_lock = Lock()
        handler_calls = 0
        store = InMemoryEvidenceStore(retention_by_authorization={})
        original_append = store.append_audit

        def append_audit(context, event) -> None:
            audit_entered.set()
            release_audit.wait(1)
            original_append(context, event)

        def handler(context, _arguments):
            nonlocal handler_calls
            with handler_lock:
                handler_calls += 1
                if handler_calls == 2:
                    handlers_finished.set()
            return DomainToolResult.success(
                data={"actor": context.actor_id},
                evidence=(
                    EvidenceEnvelope(
                        evidence_id="evidence:test:inflight",
                        source="synthetic",
                        as_of=datetime(2026, 8, 11, tzinfo=timezone.utc),
                        content_sha256="f" * 64,
                        authorized_fields=("actor",),
                    ),
                ),
            )

        store.append_audit = append_audit  # type: ignore[method-assign]
        adapter = PersonalMcpGateway(
            registry=DomainToolRegistry(
                handlers={"get_today_context": handler}
            ),
            audit_store=store,
            actor_id="fixed-actor",
            deadline_seconds=1,
        )
        first = asyncio.create_task(
            adapter.call_tool("get_today_context", {})
        )
        second = asyncio.create_task(
            adapter.call_tool("get_today_context", {})
        )
        self.assertTrue(await asyncio.to_thread(handlers_finished.wait, 0.5))
        self.assertTrue(await asyncio.to_thread(audit_entered.wait, 0.5))

        third = asyncio.create_task(
            adapter.call_tool("get_today_context", {})
        )
        await asyncio.sleep(0.03)

        self.assertEqual(handler_calls, 2)
        release_audit.set()
        first_result, second_result, third_result = await asyncio.gather(
            first, second, third
        )
        self.assertEqual(first_result["status"], "success")
        self.assertEqual(second_result["status"], "success")
        self.assertEqual(third_result["error_code"], "concurrency_limited")
        self.assertEqual(handler_calls, 2)
        self.assertEqual(len(store.audits_for_actor("fixed-actor")), 3)

    async def test_complete_call_tool_envelope_over_256_kib_fails_without_truncation(self) -> None:
        store = InMemoryEvidenceStore(retention_by_authorization={})
        adapter = PersonalMcpGateway(
            registry=DomainToolRegistry(
                handlers={
                    "get_today_context": lambda _context, _arguments: (
                        DomainToolResult.success(
                            data={"private_payload": "x" * (300 * 1024)},
                            evidence=(
                                EvidenceEnvelope(
                                    evidence_id="evidence:test:large",
                                    source="synthetic",
                                    as_of=datetime(2026, 8, 11, tzinfo=timezone.utc),
                                    content_sha256="c" * 64,
                                    authorized_fields=("private_payload",),
                                ),
                            ),
                        )
                    )
                }
            ),
            audit_store=store,
            actor_id="fixed-actor",
        )

        result = await adapter.call_tool("get_today_context", {})

        self.assertEqual(result["error_code"], "tool_result_too_large")
        self.assertEqual(result["data"], {})
        self.assertNotIn("truncated", json.dumps(result))
        audit = store.audits_for_actor("fixed-actor")[0]
        self.assertEqual(audit.error_code, "tool_result_too_large")
        self.assertEqual(audit.evidence_ids, ())

    async def test_utf8_chinese_output_boundary_is_measured_in_envelope_bytes(self) -> None:
        catalog = DomainToolRegistry(handlers={})

        class ChineseRegistry:
            def projected_definitions(self, **kwargs):
                return catalog.projected_definitions(**kwargs)

            def invoke(self, _name, *, context, arguments):
                return DomainToolResult.success(
                    data={
                        "actor": context.actor_id,
                        "text": "中" * arguments["count"],
                    }
                )

        def envelope_size(count: int) -> int:
            return encoded_call_tool_result_size(
                _result_payload(
                    DomainToolResult.success(
                        data={"actor": "fixed-actor", "text": "中" * count}
                    )
                )
            )

        low, high = 0, PERSONAL_MCP_MAX_OUTPUT_BYTES
        while low < high:
            middle = (low + high + 1) // 2
            if envelope_size(middle) <= PERSONAL_MCP_MAX_OUTPUT_BYTES:
                low = middle
            else:
                high = middle - 1
        adapter = PersonalMcpGateway(
            registry=ChineseRegistry(),  # type: ignore[arg-type]
            audit_store=InMemoryEvidenceStore(retention_by_authorization={}),
            actor_id="fixed-actor",
        )

        accepted = await adapter.call_tool(
            "get_today_context", {"count": low}
        )
        rejected = await adapter.call_tool(
            "get_today_context", {"count": low + 1}
        )

        self.assertLessEqual(envelope_size(low), PERSONAL_MCP_MAX_OUTPUT_BYTES)
        self.assertGreater(
            envelope_size(low + 1), PERSONAL_MCP_MAX_OUTPUT_BYTES
        )
        self.assertEqual(accepted["status"], "success")
        self.assertEqual(rejected["error_code"], "tool_result_too_large")

    async def test_audit_failure_discards_private_success_and_fail_stops_gateway(self) -> None:
        class FailingAuditStore:
            def append_audit(self, _context, _event) -> None:
                raise RuntimeError("database unavailable")

        adapter = PersonalMcpGateway(
            registry=DomainToolRegistry(
                handlers={
                    "get_today_context": lambda _context, _arguments: (
                        DomainToolResult.success(
                            data={"private_payload": "must-not-return"},
                            evidence=(
                                EvidenceEnvelope(
                                    evidence_id="evidence:test:private",
                                    source="synthetic",
                                    as_of=datetime(2026, 8, 11, tzinfo=timezone.utc),
                                    content_sha256="d" * 64,
                                    authorized_fields=("private_payload",),
                                ),
                            ),
                        )
                    )
                }
            ),
            audit_store=FailingAuditStore(),  # type: ignore[arg-type]
            actor_id="fixed-actor",
        )

        failed = await adapter.call_tool("get_today_context", {})

        self.assertEqual(failed["error_code"], "capability_audit_unavailable")
        self.assertNotIn("must-not-return", json.dumps(failed))
        with self.assertRaisesRegex(
            PersonalMcpGatewayStopped, "capability_audit_unavailable"
        ):
            await adapter.call_tool("get_today_context", {})

    async def test_absolute_deadline_includes_audit_confirmation(self) -> None:
        class SlowAuditStore:
            def append_audit(self, _context, _event) -> None:
                time.sleep(0.2)

        adapter = PersonalMcpGateway(
            registry=DomainToolRegistry(handlers={}),
            audit_store=SlowAuditStore(),  # type: ignore[arg-type]
            actor_id="fixed-actor",
            deadline_seconds=0.04,
        )

        started = time.perf_counter()
        result = await adapter.call_tool("get_evidence", {"evidence_id": "missing"})
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.08)
        self.assertEqual(result["error_code"], "capability_audit_unavailable")
        with self.assertRaises(PersonalMcpGatewayStopped):
            await adapter.call_tool("get_evidence", {"evidence_id": "missing"})

    async def test_cancel_is_audited_once_while_worker_keeps_its_slot(self) -> None:
        release = Event()
        started = Event()

        def handler(_context, _arguments):
            started.set()
            release.wait(1)
            return DomainToolResult.unavailable("finished_after_cancel", "test")

        store = InMemoryEvidenceStore(retention_by_authorization={})
        adapter = PersonalMcpGateway(
            registry=DomainToolRegistry(handlers={"get_today_context": handler}),
            audit_store=store,
            actor_id="fixed-actor",
        )
        task = asyncio.create_task(adapter.call_tool("get_today_context", {}))
        self.assertTrue(await asyncio.to_thread(started.wait, 0.5))

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.03)

        audits = store.audits_for_actor("fixed-actor")
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].error_code, "tool_cancelled")
        release.set()

    async def test_cancelled_workers_do_not_release_execution_leases_early(self) -> None:
        release = Event()
        both_started = Event()
        state_lock = Lock()
        handler_calls = 0

        def handler(context, _arguments):
            nonlocal handler_calls
            with state_lock:
                handler_calls += 1
                if handler_calls == 2:
                    both_started.set()
            release.wait(1)
            return DomainToolResult.success(
                data={"actor": context.actor_id},
                evidence=(
                    EvidenceEnvelope(
                        evidence_id="evidence:test:cancel-worker",
                        source="synthetic",
                        as_of=datetime(2026, 8, 11, tzinfo=timezone.utc),
                        content_sha256="9" * 64,
                        authorized_fields=("actor",),
                    ),
                ),
            )

        store = InMemoryEvidenceStore(retention_by_authorization={})
        adapter = PersonalMcpGateway(
            registry=DomainToolRegistry(
                handlers={"get_today_context": handler}
            ),
            audit_store=store,
            actor_id="fixed-actor",
        )
        first = asyncio.create_task(
            adapter.call_tool("get_today_context", {})
        )
        second = asyncio.create_task(
            adapter.call_tool("get_today_context", {})
        )
        self.assertTrue(await asyncio.to_thread(both_started.wait, 0.5))

        first.cancel()
        second.cancel()
        for task in (first, second):
            with self.assertRaises(asyncio.CancelledError):
                await task
        await asyncio.sleep(0.03)
        rejected = await adapter.call_tool("get_today_context", {})

        self.assertEqual(rejected["error_code"], "concurrency_limited")
        self.assertEqual(handler_calls, 2)
        self.assertEqual(
            [item.error_code for item in store.audits_for_actor("fixed-actor")],
            ["tool_cancelled", "tool_cancelled", "concurrency_limited"],
        )
        release.set()
        await asyncio.sleep(0.05)
        recovered = await adapter.call_tool("get_today_context", {})
        self.assertEqual(recovered["status"], "success")

    async def test_executor_exception_is_stable_redacted_and_audited_once(self) -> None:
        catalog = DomainToolRegistry(handlers={})

        class ExplodingRegistry:
            def projected_definitions(self, **kwargs):
                return catalog.projected_definitions(**kwargs)

            def invoke(self, *_args, **_kwargs):
                raise RuntimeError("SECRET PROVIDER DETAIL")

        store = InMemoryEvidenceStore(retention_by_authorization={})
        adapter = PersonalMcpGateway(
            registry=ExplodingRegistry(),  # type: ignore[arg-type]
            audit_store=store,
            actor_id="fixed-actor",
        )

        result = await adapter.call_tool("get_today_context", {})

        self.assertEqual(result["error_code"], "tool_execution_failed")
        self.assertNotIn("SECRET", json.dumps(result))
        audits = store.audits_for_actor("fixed-actor")
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].error_code, "tool_execution_failed")

    async def test_result_serialization_failure_is_stable_and_audited_once(self) -> None:
        catalog = DomainToolRegistry(handlers={})

        class UnserializableValue:
            def __str__(self) -> str:
                raise RuntimeError("SECRET SERIALIZATION DETAIL")

        class UnserializableRegistry:
            def projected_definitions(self, **kwargs):
                return catalog.projected_definitions(**kwargs)

            def invoke(self, *_args, **_kwargs):
                return DomainToolResult.success(
                    data={"unsafe": UnserializableValue()}
                )

        store = InMemoryEvidenceStore(retention_by_authorization={})
        adapter = PersonalMcpGateway(
            registry=UnserializableRegistry(),  # type: ignore[arg-type]
            audit_store=store,
            actor_id="fixed-actor",
        )

        result = await adapter.call_tool("get_today_context", {})

        self.assertEqual(result["error_code"], "tool_serialization_failed")
        audits = store.audits_for_actor("fixed-actor")
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].error_code, "tool_serialization_failed")

    async def test_deadline_covers_serialization_worker_lifetime(self) -> None:
        serialization_started = Event()
        serialization_finished = Event()
        catalog = DomainToolRegistry(handlers={})

        class SlowSerializationValue:
            def __str__(self) -> str:
                serialization_started.set()
                time.sleep(0.15)
                serialization_finished.set()
                return "late"

        class SlowSerializationRegistry:
            def projected_definitions(self, **kwargs):
                return catalog.projected_definitions(**kwargs)

            def invoke(self, *_args, **_kwargs):
                return DomainToolResult.success(
                    data={"slow": SlowSerializationValue()}
                )

        adapter = PersonalMcpGateway(
            registry=SlowSerializationRegistry(),  # type: ignore[arg-type]
            audit_store=InMemoryEvidenceStore(retention_by_authorization={}),
            actor_id="fixed-actor",
            deadline_seconds=0.03,
        )

        started = time.perf_counter()
        result = await adapter.call_tool("get_today_context", {})
        elapsed = time.perf_counter() - started
        rejected = await adapter.call_tool("get_today_context", {})

        self.assertTrue(serialization_started.is_set())
        self.assertFalse(serialization_finished.is_set())
        self.assertLess(elapsed, 0.08)
        self.assertEqual(result["error_code"], "tool_deadline_exceeded")
        self.assertEqual(rejected["error_code"], "tool_deadline_exceeded")


class PersonalMcpStdioIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_official_client_initialize_list_call_and_shutdown_without_network(self) -> None:
        root = Path(__file__).resolve().parents[2]
        fixture = (
            root
            / "backend"
            / "tests"
            / "fixtures"
            / "personal_mcp_stdio_fixture.py"
        )
        stderr = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        self.addCleanup(stderr.close)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(fixture)],
            cwd=root,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": str(root),
            },
            encoding="utf-8",
            encoding_error_handler="strict",
        )

        async with stdio_client(parameters, errlog=stderr) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                called = await session.call_tool("get_today_context", {})

        self.assertEqual(initialized.serverInfo.name, "personal-investment-workbench")
        self.assertEqual(
            tuple(item.name for item in tools.tools),
            tuple(sorted(PERSONAL_MCP_TOOL_ALLOWLIST)),
        )
        self.assertFalse(called.isError)
        payload = json.loads(called.content[0].text)  # type: ignore[union-attr]
        self.assertEqual(payload["data"]["actor"], "stdio-fixture")
        self.assertEqual(payload["data"]["purpose"], "mcp_stdio")

    async def test_timeout_worker_finishes_before_stdio_eof_process_exit(self) -> None:
        root = Path(__file__).resolve().parents[2]
        fixture = (
            root
            / "backend"
            / "tests"
            / "fixtures"
            / "personal_mcp_stdio_fixture.py"
        )
        stderr = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        self.addCleanup(stderr.close)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(fixture)],
            cwd=root,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": str(root),
                "PERSONAL_MCP_TEST_TIMEOUT": "true",
            },
            encoding="utf-8",
            encoding_error_handler="strict",
        )

        started = time.perf_counter()
        async with stdio_client(parameters, errlog=stderr) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                called = await session.call_tool("get_today_context", {})
        elapsed = time.perf_counter() - started

        payload = json.loads(called.content[0].text)  # type: ignore[union-attr]
        self.assertEqual(payload["error_code"], "tool_deadline_exceeded")
        self.assertGreaterEqual(elapsed, 0.12)
        self.assertLess(elapsed, 5.0)

    async def test_unknown_tool_warning_is_redacted_without_breaking_stdio_framing(self) -> None:
        root = Path(__file__).resolve().parents[2]
        fixture = (
            root
            / "backend"
            / "tests"
            / "fixtures"
            / "personal_mcp_stdio_fixture.py"
        )
        stderr = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        self.addCleanup(stderr.close)
        with tempfile.TemporaryDirectory() as temporary_directory:
            audit_file = Path(temporary_directory) / "audit-tools.txt"
            parameters = StdioServerParameters(
                command=sys.executable,
                args=[str(fixture)],
                cwd=root,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONPATH": str(root),
                    "PERSONAL_MCP_TEST_AUDIT_FILE": str(audit_file),
                },
                encoding="utf-8",
                encoding_error_handler="strict",
            )
            unknown_name = "SECRET_IN_TOOL_NAME\nLOG_INJECTION"

            async with stdio_client(parameters, errlog=stderr) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    called = await session.call_tool(unknown_name, {})

            payload = json.loads(
                called.content[0].text  # type: ignore[union-attr]
            )
            stderr.seek(0)
            stderr_text = stderr.read()
            audit_tools = audit_file.read_text(encoding="utf-8").splitlines()

        self.assertTrue(called.isError)
        self.assertEqual(payload["error_code"], "unknown_tool")
        self.assertEqual(len(audit_tools), 1)
        self.assertRegex(audit_tools[0], r"^rejected_tool:[0-9a-f]{16}$")
        self.assertIn(audit_tools[0], stderr_text)
        self.assertNotIn("SECRET_IN_TOOL_NAME", stderr_text)
        self.assertNotIn("LOG_INJECTION", stderr_text)
        self.assertNotIn("Traceback", stderr_text)

    async def test_idle_stdio_process_can_be_terminated_by_supervisor(self) -> None:
        root = Path(__file__).resolve().parents[2]
        fixture = (
            root
            / "backend"
            / "tests"
            / "fixtures"
            / "personal_mcp_stdio_fixture.py"
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(fixture),
            cwd=root,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": str(root),
            },
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.sleep(0.05)

        process.terminate()
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=2)

        self.assertEqual(stdout, b"")
        self.assertNotEqual(process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
