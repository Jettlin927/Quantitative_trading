from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.app.quant_research.strategy_registry import list_strategy_definitions
from scripts.research.run_quant_research import main as research_cli_main


REPO_ROOT = Path(__file__).resolve().parents[2]


class ResearchCliTest(unittest.TestCase):
    def test_registered_strategies_have_existing_example_configs(self):
        definitions = list_strategy_definitions()
        self.assertEqual(
            [definition.strategy_id for definition in definitions],
            [
                "a_share_price_baseline",
                "etf_trend_120d",
                "sentinel_etf_baseline",
            ],
        )
        for definition in definitions:
            with self.subTest(strategy=definition.strategy_id):
                self.assertTrue((REPO_ROOT / definition.example_config).is_file())

    def test_list_strategies_is_static_json_and_does_not_connect_database(self):
        output = io.StringIO()
        with (
            patch(
                "scripts.research.run_quant_research.create_engine",
                side_effect=AssertionError("database must not be opened"),
            ),
            redirect_stdout(output),
        ):
            exit_code = research_cli_main(["--list-strategies"])
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(payload["strategies"]), 3)
        self.assertEqual(
            set(payload["strategies"][0]),
            {
                "strategyId",
                "strategyVersion",
                "scope",
                "requiredInputs",
                "exampleConfig",
            },
        )
        self.assertTrue(payload["boundaries"]["researchOnly"])
        self.assertFalse(payload["boundaries"]["executionEnabled"])


if __name__ == "__main__":
    unittest.main()
