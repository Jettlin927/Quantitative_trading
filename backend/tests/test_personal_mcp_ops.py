from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest

from backend.app.personal_workspace.mcp_gateway import PERSONAL_MCP_TOOL_ALLOWLIST


ROOT = Path(__file__).resolve().parents[2]
TUNNEL = ROOT / "scripts" / "ops" / "personal_mcp_tunnel.sh"


class PersonalMcpTunnelScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.state = self.root / "ssh-state"
        self.log = self.root / "ssh-log"
        self.control = self.root / "control.sock"
        self._write_executable(
            "ssh",
            """#!/usr/bin/env bash
set -eu
case " $* " in
  *" -O check "*) test -f "$TEST_SSH_STATE" ;;
  *" -O exit "*) rm -f "$TEST_SSH_STATE"; printf '%s\n' "$*" >> "$TEST_SSH_LOG" ;;
  *) touch "$TEST_SSH_STATE"; printf '%s\n' "$*" >> "$TEST_SSH_LOG" ;;
esac
""",
        )
        self._write_executable(
            "python3",
            """#!/usr/bin/env bash
test "${TEST_PORT_OCCUPIED:-0}" != 1
""",
        )
        self._write_executable(
            "curl",
            """#!/usr/bin/env bash
printf '%s' "${TEST_HTTP_STATUS:-401}"
""",
        )
        self.environment = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "TEST_SSH_STATE": str(self.state),
            "TEST_SSH_LOG": str(self.log),
            "PERSONAL_MCP_CONTROL_SOCKET": str(self.control),
        }

    def _write_executable(self, name: str, content: str) -> None:
        path = self.bin / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def run_script(self, action: str, **environment: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(TUNNEL), action],
            cwd=ROOT,
            env={**self.environment, **environment},
            capture_output=True,
            text=True,
            check=False,
        )

    def test_start_uses_control_master_and_fixed_loopback_forward(self) -> None:
        completed = self.run_script("start")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        invocation = self.log.read_text(encoding="utf-8")
        self.assertIn("-M", invocation)
        self.assertIn("ControlMaster=yes", invocation)
        self.assertIn("ControlPersist=no", invocation)
        self.assertIn("ExitOnForwardFailure=yes", invocation)
        self.assertIn("127.0.0.1:26174:127.0.0.1:16174", invocation)
        self.assertIn("quant-trading-prod", invocation)
        self.assertNotIn("0.0.0.0", invocation)

    def test_occupied_port_fails_before_starting_ssh(self) -> None:
        completed = self.run_script("start", TEST_PORT_OCCUPIED="1")

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("本机端口已占用", completed.stderr)
        self.assertFalse(self.log.exists())

    def test_status_fails_when_control_connection_or_remote_endpoint_is_down(self) -> None:
        disconnected = self.run_script("status")
        self.assertNotEqual(disconnected.returncode, 0)
        self.assertIn("隧道未运行或已断开", disconnected.stderr)

        self.state.touch()
        endpoint_down = self.run_script("status", TEST_HTTP_STATUS="000")
        self.assertNotEqual(endpoint_down.returncode, 0)
        self.assertIn("远端 MCP 不可达", endpoint_down.stderr)

    def test_start_closes_new_control_connection_when_endpoint_is_down(self) -> None:
        completed = self.run_script("start", TEST_HTTP_STATUS="000")

        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(self.state.exists())
        self.assertIn("-O exit", self.log.read_text(encoding="utf-8"))

    def test_stop_only_exits_control_connection_and_is_idempotent(self) -> None:
        self.state.touch()
        stopped = self.run_script("stop")
        again = self.run_script("stop")

        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertFalse(self.state.exists())
        self.assertIn("-O exit", self.log.read_text(encoding="utf-8"))


class PersonalMcpClientTemplateTest(unittest.TestCase):
    def test_codex_template_uses_local_http_and_token_environment_name_only(self) -> None:
        template = (
            ROOT / ".codex" / "config.personal-mcp.example.toml"
        ).read_text(encoding="utf-8")

        self.assertIn('url = "http://127.0.0.1:26174/mcp"', template)
        self.assertIn(
            'bearer_token_env_var = "PERSONAL_MCP_BEARER_TOKEN"', template
        )
        self.assertNotIn("Authorization", template)
        self.assertNotIn("<token", template.lower())
        payload = tomllib.loads(template)
        self.assertEqual(
            set(
                payload["mcp_servers"]["personal-investment-workbench"][
                    "enabled_tools"
                ]
            ),
            PERSONAL_MCP_TOOL_ALLOWLIST,
        )

    def test_claude_template_is_http_only_and_contains_no_secret(self) -> None:
        payload = json.loads((ROOT / ".mcp.json.example").read_text(encoding="utf-8"))
        server = payload["mcpServers"]["personal-investment-workbench"]

        self.assertEqual(server["type"], "http")
        self.assertEqual(server["url"], "http://127.0.0.1:26174/mcp")
        self.assertEqual(
            server["headers"]["Authorization"],
            "Bearer ${PERSONAL_MCP_BEARER_TOKEN}",
        )
        self.assertNotIn("sse", json.dumps(payload).lower())
        self.assertNotIn("websocket", json.dumps(payload).lower())


class PersonalMcpOperationsDocumentationTest(unittest.TestCase):
    def test_compose_has_no_actor_fallback_and_health_requires_exact_401(self) -> None:
        compose = (ROOT / "docker-compose.personal.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("PERSONAL_MCP_ACTOR_ID: ${PERSONAL_MCP_ACTOR_ID:-}", compose)
        self.assertNotIn("PERSONAL_MCP_ACTOR_ID:-disabled", compose)
        self.assertIn("else: raise SystemExit(1)", compose)

    def test_runbook_covers_service_tunnel_rotation_and_kill_switch(self) -> None:
        runbook = (
            ROOT / "docs" / "operations" / "personal-mcp-remote.md"
        ).read_text(encoding="utf-8")

        for required in (
            "--profile personal-mcp",
            "up -d --no-deps personal-mcp",
            "ps personal-mcp",
            "stop personal-mcp",
            "--force-recreate personal-mcp",
            "scripts/ops/personal_mcp_tunnel.sh start",
            "scripts/ops/personal_mcp_tunnel.sh status",
            "scripts/ops/personal_mcp_tunnel.sh stop",
            "ExitOnForwardFailure",
            "ControlMaster",
            "端口占用",
            "隧道断开",
            "token 轮换",
            "PERSONAL_MCP_ENABLED=false",
            "不执行 migration",
            "不删除 evidence/audit",
        ):
            with self.subTest(required=required):
                self.assertIn(required, runbook)
        self.assertNotIn("proxy_pass", runbook)
        self.assertNotIn("--transport sse", runbook)
        self.assertNotIn("ws://", runbook)

    def test_client_launch_instructions_read_ignored_owner_only_file(self) -> None:
        runbook = (
            ROOT / "docs" / "operations" / "personal-mcp-remote.md"
        ).read_text(encoding="utf-8")

        self.assertIn(".personal-mcp-token", runbook)
        self.assertIn("chmod 600 .personal-mcp-token", runbook)
        self.assertIn('PERSONAL_MCP_BEARER_TOKEN="$(<.personal-mcp-token)" codex', runbook)
        self.assertIn('PERSONAL_MCP_BEARER_TOKEN="$(<.personal-mcp-token)" claude', runbook)
        self.assertNotIn("PERSONAL_MCP_BEARER_TOKEN=<", runbook)


if __name__ == "__main__":
    unittest.main()
