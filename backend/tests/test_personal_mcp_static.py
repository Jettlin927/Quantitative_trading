from __future__ import annotations

import ast
from pathlib import Path
import unittest

from backend.app.personal_workspace.agent.domain_tools import DomainToolRegistry


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "backend" / "app" / "personal_workspace"


class PersonalMcpStaticContractTest(unittest.TestCase):
    def test_official_v1_sdk_is_pinned_and_no_handwritten_transport_exists(self) -> None:
        requirements = (ROOT / "backend" / "requirements.txt").read_text(
            encoding="utf-8"
        )
        server = (WORKSPACE / "mcp_server.py").read_text(encoding="utf-8")

        self.assertIn("mcp==1.29.0", requirements)
        self.assertIn("mcp.server.stdio.stdio_server", server)
        self.assertNotIn("FastMCP", server)
        for forbidden in (
            "streamable_http",
            "sse_app",
            "socket.",
            "Content-Length",
            '"jsonrpc"',
        ):
            self.assertNotIn(forbidden, server)

    def test_protocol_adapter_has_no_ai_provider_write_or_network_imports(self) -> None:
        for filename in ("mcp_server.py", "mcp_gateway.py"):
            tree = ast.parse((WORKSPACE / filename).read_text(encoding="utf-8"))
            imported = {
                node.module or ""
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            } | {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            self.assertFalse(
                {
                    "socket",
                    "backend.app.personal_workspace.agent.ai_runtime",
                    "backend.app.personal_workspace.agent.completion_runtime",
                    "backend.app.personal_workspace.agent.deepseek_provider",
                }
                & imported
            )

    def test_adapter_is_deletable_without_changing_domain_registry(self) -> None:
        offenders = []
        for path in WORKSPACE.rglob("*.py"):
            if path.name in {"mcp_server.py", "mcp_gateway.py"}:
                continue
            text = path.read_text(encoding="utf-8")
            if "mcp_server" in text or "mcp_gateway" in text:
                offenders.append(str(path.relative_to(WORKSPACE)))
        self.assertEqual(offenders, [])
        registry = DomainToolRegistry(handlers={})
        self.assertEqual(len(registry.definitions(permissions=frozenset())), 0)

    def test_direct_module_guard_is_the_last_statement(self) -> None:
        tree = ast.parse((WORKSPACE / "mcp_server.py").read_text(encoding="utf-8"))
        last = tree.body[-1]
        self.assertIsInstance(last, ast.If)
        self.assertEqual(ast.unparse(last.test), "__name__ == '__main__'")


if __name__ == "__main__":
    unittest.main()
