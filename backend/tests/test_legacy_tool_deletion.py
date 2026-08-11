from __future__ import annotations

from pathlib import Path
import unittest

from backend.app.personal_workspace.agent import protocol
from backend.app.personal_workspace.agent.skills import Skill


ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = ROOT / "backend/app/personal_workspace/agent"


class LegacyToolDeletionTest(unittest.TestCase):
    def test_legacy_implementations_and_characterization_tests_are_deleted(self) -> None:
        deleted = (
            AGENT_ROOT / "runtime.py",
            AGENT_ROOT / "tools.py",
            AGENT_ROOT / "tools_impl",
            ROOT / "backend/tests/test_agent_runtime.py",
            ROOT / "backend/tests/test_agent_tools.py",
        )
        self.assertEqual([str(path) for path in deleted if path.exists()], [])

    def test_legacy_protocol_types_are_gone_and_skill_has_a_stable_home(self) -> None:
        for name in (
            "Tool",
            "ToolContext",
            "ToolResult",
            "AgentTurnResult",
            "AgentProvider",
        ):
            self.assertFalse(hasattr(protocol, name), name)
        self.assertEqual(Skill.__module__, "backend.app.personal_workspace.agent.skills")

    def test_agent_source_boundaries_have_one_market_and_news_adapter(self) -> None:
        sources = {
            path.name: path.read_text(encoding="utf-8")
            for path in AGENT_ROOT.glob("*.py")
        }
        self.assertEqual(
            [name for name, text in sources.items() if "AlpacaMarketObservationAdapter" in text],
            ["fact_market.py"],
        )
        self.assertEqual(
            [name for name, text in sources.items() if ' / "data.js"' in text],
            ["fact_news.py"],
        )
        joined = "\n".join(sources.values())
        for legacy in ("build_agent_tools", "tools_impl", "from .protocol import"):
            self.assertNotIn(legacy, joined)


if __name__ == "__main__":
    unittest.main()
