from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import socket
import time

from backend.app.personal_workspace.agent.domain_tools import (
    DomainToolRegistry,
    DomainToolResult,
    EvidenceEnvelope,
)
from backend.app.personal_workspace.agent.evidence import InMemoryEvidenceStore
from backend.app.personal_workspace.mcp_gateway import PersonalMcpGateway
from backend.app.personal_workspace.mcp_server import (
    serve_stdio_gateway,
)


def _network_forbidden(*_args, **_kwargs):
    raise AssertionError("personal_mcp_network_forbidden")


socket.socket.bind = _network_forbidden  # type: ignore[method-assign]
socket.socket.connect = _network_forbidden  # type: ignore[method-assign]
socket.socket.connect_ex = _network_forbidden  # type: ignore[method-assign]
socket.socket.listen = _network_forbidden  # type: ignore[method-assign]


def _today(context, _arguments):
    if os.environ.get("PERSONAL_MCP_TEST_TIMEOUT") == "true":
        time.sleep(0.15)
    return DomainToolResult.success(
        data={"actor": context.actor_id, "purpose": context.purpose},
        evidence=(
            EvidenceEnvelope(
                evidence_id="evidence:stdio:1",
                source="synthetic",
                as_of=datetime(2026, 8, 11, tzinfo=timezone.utc),
                content_sha256="e" * 64,
                authorized_fields=("actor", "purpose"),
            ),
        ),
    )


audit_store = InMemoryEvidenceStore(retention_by_authorization={})
original_append_audit = audit_store.append_audit


def _append_audit(context, event) -> None:
    original_append_audit(context, event)
    output_file = os.environ.get("PERSONAL_MCP_TEST_AUDIT_FILE")
    if output_file:
        with Path(output_file).open("a", encoding="utf-8") as stream:
            stream.write(f"{event.canonical_tool}\n")


audit_store.append_audit = _append_audit  # type: ignore[method-assign]


gateway = PersonalMcpGateway(
    registry=DomainToolRegistry(handlers={"get_today_context": _today}),
    audit_store=audit_store,
    actor_id="stdio-fixture",
    deadline_seconds=(
        0.03
        if os.environ.get("PERSONAL_MCP_TEST_TIMEOUT") == "true"
        else 20.0
    ),
)


if __name__ == "__main__":
    asyncio.run(serve_stdio_gateway(gateway))
