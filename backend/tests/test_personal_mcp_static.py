from __future__ import annotations

import ast
from pathlib import Path
import unittest

from backend.app.personal_workspace.agent.domain_tools import DomainToolRegistry


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "backend" / "app" / "personal_workspace"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }


def imported_repo_modules(path: Path) -> set[str]:
    package = list(path.relative_to(ROOT).with_suffix("").parts[:-1])
    imported: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                parent = package[: len(package) - node.level + 1]
                module = ".".join((*parent, *(node.module or "").split(".")))
            else:
                module = node.module or ""
            imported.add(module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    return {module for module in imported if module.startswith("backend.")}


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

    def test_deepseek_runtime_and_mcp_adapters_do_not_import_each_other(self) -> None:
        mcp_paths = tuple(WORKSPACE.glob("mcp_*.py"))
        deepseek_paths = (
            ROOT / "backend" / "app" / "personal_analysis_worker.py",
            *tuple((WORKSPACE / "agent").rglob("*.py")),
        )

        for path in mcp_paths:
            with self.subTest(path=path.relative_to(ROOT)):
                modules = imported_repo_modules(path)
                self.assertFalse(
                    any(
                        boundary in module
                        for module in modules
                        for boundary in (
                            "personal_analysis_worker",
                            "client_tool_runtime",
                            "completion_runtime",
                            "deepseek_provider",
                        )
                    )
                )
        for path in deepseek_paths:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertFalse(
                    any(".mcp_" in module for module in imported_repo_modules(path))
                )

    def test_gateway_and_deepseek_tool_runtime_share_only_domain_tools(self) -> None:
        gateway = WORKSPACE / "mcp_gateway.py"
        tool_runtime = WORKSPACE / "agent" / "client_tool_runtime.py"
        shared_modules = imported_repo_modules(gateway) & imported_repo_modules(
            tool_runtime
        )

        self.assertEqual(
            shared_modules,
            {"backend.app.personal_workspace.agent.domain_tools"},
        )
        for path in (gateway, tool_runtime):
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertIn(
                    "DomainToolRegistry", path.read_text(encoding="utf-8")
                )

    def test_direct_module_guard_is_the_last_statement(self) -> None:
        tree = ast.parse((WORKSPACE / "mcp_server.py").read_text(encoding="utf-8"))
        last = tree.body[-1]
        self.assertIsInstance(last, ast.If)
        self.assertEqual(ast.unparse(last.test), "__name__ == '__main__'")


if __name__ == "__main__":
    unittest.main()
