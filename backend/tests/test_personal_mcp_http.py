from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import tempfile
import unittest

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from backend.app.personal_workspace.agent.domain_tools import (
    DomainToolRegistry,
    DomainToolResult,
    EvidenceEnvelope,
)
from backend.app.personal_workspace.agent.evidence import InMemoryEvidenceStore
from backend.app.personal_workspace.agent.fact_market import (
    MARKET_EVIDENCE_ALLOWED_PURPOSES,
    MARKET_EVIDENCE_PURPOSE_POLICY_HISTORY,
)
from backend.app.personal_workspace.agent.fact_news import FACT_NEWS_POLICY_HISTORY
from backend.app.personal_workspace.agent.fact_private import (
    PRIVATE_FACT_POLICIES,
    PRIVATE_FACT_POLICY_HISTORY,
)
from backend.app.personal_workspace.mcp_gateway import (
    PERSONAL_MCP_HTTP_POLICY,
    PERSONAL_MCP_MAX_OUTPUT_BYTES,
    PERSONAL_MCP_TOOL_ALLOWLIST,
    PersonalMcpGateway,
    _result_payload,
    encoded_call_tool_result_size,
)
from backend.app.personal_workspace.mcp_http import create_personal_mcp_http_app
from backend.app.personal_workspace.mcp_http import PersonalMcpHttpConfigurationError


class PersonalMcpHttpIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_official_client_initializes_lists_calls_and_shuts_down(self) -> None:
        contexts = []
        store = InMemoryEvidenceStore(retention_by_authorization={})
        gateway = PersonalMcpGateway(
            registry=DomainToolRegistry(
                handlers={
                    "get_today_context": lambda context, _arguments: (
                        contexts.append(context)
                        or DomainToolResult.success(
                            data={
                                "actor": context.actor_id,
                                "purpose": context.purpose,
                            },
                            evidence=(
                                EvidenceEnvelope(
                                    evidence_id="evidence:http:1",
                                    source="synthetic",
                                    as_of=datetime(
                                        2026, 8, 11, tzinfo=timezone.utc
                                    ),
                                    content_sha256="a" * 64,
                                    authorized_fields=("actor", "purpose"),
                                ),
                            ),
                        )
                    )
                }
            ),
            audit_store=store,
            actor_id="remote-fixture",
            transport_policy=PERSONAL_MCP_HTTP_POLICY,
            clock=lambda: datetime(2026, 8, 11, tzinfo=timezone.utc),
        )
        self.addCleanup(gateway.close)
        with tempfile.TemporaryDirectory() as temporary_directory:
            token_file = Path(temporary_directory) / "mcp.token"
            token_file.write_text("test-http-token\n", encoding="utf-8")
            token_file.chmod(0o600)
            app = create_personal_mcp_http_app(
                gateway=gateway,
                token_file=token_file,
                allowed_origins=("http://127.0.0.1:26001",),
            )
            transport = httpx.ASGITransport(app=app)
            http_client = httpx.AsyncClient(
                transport=transport,
                headers={"Authorization": "Bearer test-http-token"},
                base_url="http://mcp.test",
            )
            async with app.router.lifespan_context(app):
                async with http_client:
                    async with streamable_http_client(
                        "http://mcp.test/mcp", http_client=http_client
                    ) as (read, write, _session_id):
                        async with ClientSession(read, write) as session:
                            initialized = await session.initialize()
                            tools = await session.list_tools()
                            called = await session.call_tool(
                                "get_today_context", {}
                            )

        self.assertEqual(
            initialized.serverInfo.name, "personal-investment-workbench"
        )
        self.assertEqual(
            tuple(item.name for item in tools.tools),
            tuple(sorted(PERSONAL_MCP_TOOL_ALLOWLIST)),
        )
        payload = json.loads(called.content[0].text)  # type: ignore[union-attr]
        self.assertFalse(called.isError, payload)
        self.assertEqual(
            payload["data"],
            {"actor": "remote-fixture", "purpose": "mcp_remote_read"},
        )
        self.assertEqual(contexts[0].purpose, "mcp_remote_read")
        audit = store.audits_for_actor("remote-fixture")[0]
        self.assertEqual(audit.channel, "mcp_streamable_http")
        self.assertEqual(audit.policy_revision, "personal-mcp-remote-v1")

    async def test_official_client_preserves_utf8_size_rate_and_audit_limits(self) -> None:
        catalog = DomainToolRegistry(handlers={})

        class ChineseRegistry:
            def projected_definitions(self, **kwargs):
                return catalog.projected_definitions(**kwargs)

            def invoke(self, _name, *, context, arguments):
                return DomainToolResult.success(
                    data={
                        "purpose": context.purpose,
                        "text": "中" * arguments.get("count", 0),
                    }
                )

        def envelope_size(count: int) -> int:
            return encoded_call_tool_result_size(
                _result_payload(
                    DomainToolResult.success(
                        data={"purpose": "mcp_remote_read", "text": "中" * count}
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
        store = InMemoryEvidenceStore(retention_by_authorization={})
        gateway = PersonalMcpGateway(
            registry=ChineseRegistry(),  # type: ignore[arg-type]
            audit_store=store,
            actor_id="remote-limits",
            transport_policy=PERSONAL_MCP_HTTP_POLICY,
        )
        self.addCleanup(gateway.close)
        with tempfile.TemporaryDirectory() as temporary_directory:
            token_file = Path(temporary_directory) / "mcp.token"
            token_file.write_text("limits-token", encoding="utf-8")
            token_file.chmod(0o600)
            app = create_personal_mcp_http_app(
                gateway=gateway,
                token_file=token_file,
                allowed_origins=("http://127.0.0.1:26001",),
            )
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://mcp.test",
                    headers={
                        "Authorization": "Bearer limits-token",
                        "Origin": "http://127.0.0.1:26001",
                    },
                ) as http_client:
                    async with streamable_http_client(
                        "http://mcp.test/mcp", http_client=http_client
                    ) as (read, write, _session_id):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            accepted = await session.call_tool(
                                "get_today_context", {"count": low}
                            )
                            oversized = await session.call_tool(
                                "get_today_context", {"count": low + 1}
                            )
                            for _index in range(28):
                                await session.call_tool(
                                    "get_today_context", {"count": 0}
                                )
                            limited = await session.call_tool(
                                "get_today_context", {"count": 0}
                            )

        accepted_payload = json.loads(accepted.content[0].text)  # type: ignore[union-attr]
        oversized_payload = json.loads(oversized.content[0].text)  # type: ignore[union-attr]
        limited_payload = json.loads(limited.content[0].text)  # type: ignore[union-attr]
        self.assertEqual(accepted_payload["status"], "success")
        self.assertEqual(oversized_payload["error_code"], "tool_result_too_large")
        self.assertEqual(limited_payload["error_code"], "rate_limited")
        audits = store.audits_for_actor("remote-limits")
        self.assertEqual(len(audits), 31)
        self.assertTrue(
            all(event.channel == "mcp_streamable_http" for event in audits)
        )

    async def test_unknown_tool_is_redacted_in_http_protocol_logs(self) -> None:
        store = InMemoryEvidenceStore(retention_by_authorization={})
        gateway = PersonalMcpGateway(
            registry=DomainToolRegistry(handlers={}),
            audit_store=store,
            actor_id="remote-redaction",
            transport_policy=PERSONAL_MCP_HTTP_POLICY,
        )
        self.addCleanup(gateway.close)
        with tempfile.TemporaryDirectory() as temporary_directory:
            token_file = Path(temporary_directory) / "mcp.token"
            token_file.write_text("redaction-token", encoding="utf-8")
            token_file.chmod(0o600)
            app = create_personal_mcp_http_app(
                gateway=gateway,
                token_file=token_file,
                allowed_origins=("http://127.0.0.1:26001",),
            )
            records: list[logging.LogRecord] = []

            class Capture(logging.Handler):
                def emit(self, record: logging.LogRecord) -> None:
                    records.append(record)

            logger = logging.getLogger("mcp.server.lowlevel.server")
            handler = Capture()
            previous_level = logger.level
            previous_disabled = logger.disabled
            logger.setLevel(logging.WARNING)
            logger.disabled = False
            logger.addHandler(handler)
            try:
                async with app.router.lifespan_context(app):
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://mcp.test",
                        headers={"Authorization": "Bearer redaction-token"},
                    ) as http_client:
                        async with streamable_http_client(
                            "http://mcp.test/mcp", http_client=http_client
                        ) as (read, write, _session_id):
                            async with ClientSession(read, write) as session:
                                await session.initialize()
                                called = await session.call_tool(
                                    "PRIVATE_TOOL_NAME\nLOG_INJECTION", {}
                                )
            finally:
                logger.removeHandler(handler)
                logger.setLevel(previous_level)
                logger.disabled = previous_disabled

        payload = json.loads(called.content[0].text)  # type: ignore[union-attr]
        output = "\n".join(record.getMessage() for record in records)
        self.assertEqual(payload["error_code"], "unknown_tool")
        self.assertRegex(output, r"rejected_tool:[0-9a-f]{16}")
        self.assertNotIn("PRIVATE_TOOL_NAME", output)
        self.assertNotIn("LOG_INJECTION", output)


class PersonalMcpRemotePurposePolicyTest(unittest.TestCase):
    def test_each_source_rotates_policy_without_expanding_history(self) -> None:
        remote = "mcp_remote_read"
        for history in PRIVATE_FACT_POLICY_HISTORY.values():
            self.assertNotIn(remote, history[-2].allowed_purposes)
            self.assertIn(remote, history[-1].allowed_purposes)
        self.assertTrue(
            all(
                remote in policy.allowed_purposes
                for policy in PRIVATE_FACT_POLICIES.values()
            )
        )
        self.assertNotIn(remote, FACT_NEWS_POLICY_HISTORY[-2][1])
        self.assertIn(remote, FACT_NEWS_POLICY_HISTORY[-1][1])
        revisions = tuple(MARKET_EVIDENCE_PURPOSE_POLICY_HISTORY)
        self.assertNotIn(
            remote, MARKET_EVIDENCE_PURPOSE_POLICY_HISTORY[revisions[-2]]
        )
        self.assertIn(remote, MARKET_EVIDENCE_ALLOWED_PURPOSES)


class PersonalMcpHttpSecurityTest(unittest.IsolatedAsyncioTestCase):
    def test_token_file_and_origin_configuration_fail_closed(self) -> None:
        gateway = PersonalMcpGateway(
            registry=DomainToolRegistry(handlers={}),
            audit_store=InMemoryEvidenceStore(retention_by_authorization={}),
            actor_id="remote-fixture",
            transport_policy=PERSONAL_MCP_HTTP_POLICY,
        )
        self.addCleanup(gateway.close)
        with tempfile.TemporaryDirectory() as temporary_directory:
            token_file = Path(temporary_directory) / "mcp.token"
            invalid_tokens = (
                ("", 0o600),
                ("first\nsecond", 0o600),
                ("secret", 0o640),
            )
            for content, mode in invalid_tokens:
                with self.subTest(content=bool(content), mode=oct(mode)):
                    token_file.write_text(content, encoding="utf-8")
                    token_file.chmod(mode)
                    with self.assertRaises(PersonalMcpHttpConfigurationError):
                        create_personal_mcp_http_app(
                            gateway=gateway,
                            token_file=token_file,
                            allowed_origins=("http://127.0.0.1:26001",),
                        )
            token_file.write_text("secret", encoding="utf-8")
            token_file.chmod(0o600)
            for origins in (
                (),
                ("*",),
                ("null",),
                ("http://example.com/",),
                ("http://example.com:notaport",),
                ("http://[::1",),
            ):
                with self.subTest(origins=origins):
                    with self.assertRaisesRegex(
                        PersonalMcpHttpConfigurationError,
                        "personal_mcp_origins_invalid",
                    ):
                        create_personal_mcp_http_app(
                            gateway=gateway,
                            token_file=token_file,
                            allowed_origins=origins,
                        )

    async def test_duplicate_or_invalid_credentials_and_origins_are_rejected_stably(self) -> None:
        calls = []
        gateway = PersonalMcpGateway(
            registry=DomainToolRegistry(
                handlers={
                    "get_today_context": lambda _context, _arguments: calls.append(
                        "registry"
                    )
                }
            ),
            audit_store=InMemoryEvidenceStore(retention_by_authorization={}),
            actor_id="remote-fixture",
            transport_policy=PERSONAL_MCP_HTTP_POLICY,
        )
        self.addCleanup(gateway.close)
        with tempfile.TemporaryDirectory() as temporary_directory:
            token_file = Path(temporary_directory) / "mcp.token"
            token_file.write_text("request-secret\n", encoding="utf-8")
            token_file.chmod(0o600)
            app = create_personal_mcp_http_app(
                gateway=gateway,
                token_file=token_file,
                allowed_origins=("http://127.0.0.1:26001",),
            )
            sent: list[dict[str, object]] = []

            async def body_must_not_be_read():
                raise AssertionError("request_body_read_before_authentication")

            async def capture(message):
                sent.append(message)

            await app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": "POST",
                    "scheme": "http",
                    "path": "/mcp",
                    "raw_path": b"/mcp",
                    "query_string": b"",
                    "root_path": "",
                    "headers": [(b"authorization", b"Bearer wrong")],
                    "client": ("127.0.0.1", 1),
                    "server": ("mcp.test", 80),
                },
                body_must_not_be_read,
                capture,
            )
            self.assertEqual(sent[0]["status"], 401)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://mcp.test",
                ) as client:
                    cases = (
                        ([("authorization", "Bearer wrong")], 401),
                        (
                            [
                                ("authorization", "Bearer request-secret"),
                                ("authorization", "Bearer request-secret"),
                            ],
                            401,
                        ),
                        (
                            [
                                ("authorization", "Bearer request-secret"),
                                ("origin", "http://127.0.0.1:26001.evil"),
                            ],
                            403,
                        ),
                        (
                            [
                                ("authorization", "Bearer request-secret"),
                                ("origin", "http://127.0.0.1:26001"),
                                ("origin", "http://127.0.0.1:26001"),
                            ],
                            403,
                        ),
                    )
                    for headers, status in cases:
                        response = await client.post(
                            "/mcp", headers=headers, content=b"PRIVATE_BODY"
                        )
                        self.assertEqual(response.status_code, status)
                        self.assertEqual(
                            response.text,
                            "personal_mcp_unauthorized"
                            if status == 401
                            else "personal_mcp_origin_forbidden",
                        )
                        self.assertNotIn("request-secret", response.text)
                        self.assertNotIn("PRIVATE_BODY", response.text)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
