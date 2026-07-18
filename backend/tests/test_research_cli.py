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
                "a_share_b1_trend_pullback",
                "a_share_price_baseline",
                "etf_low_volatility_gate",
                "etf_trend_120d",
                "etf_volatility_managed",
                "sentinel_etf_baseline",
            ],
        )
        for definition in definitions:
            with self.subTest(strategy=definition.strategy_id):
                self.assertTrue((REPO_ROOT / definition.example_config).is_file())
                self.assertIn(
                    definition.walk_forward_benchmark_source,
                    {"config_market_reference", "universe_adjusted_etf"},
                )
        by_id = {definition.strategy_id: definition for definition in definitions}
        for strategy_id in (
            "etf_low_volatility_gate",
            "etf_trend_120d",
            "etf_volatility_managed",
        ):
            self.assertEqual(
                by_id[strategy_id].walk_forward_benchmark_source,
                "universe_adjusted_etf",
            )

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
        self.assertEqual(len(payload["strategies"]), 6)
        self.assertEqual(
            set(payload["strategies"][0]),
            {
                "strategyId",
                "strategyVersion",
                "scope",
                "requiredInputs",
                "exampleConfig",
                "walkForwardBenchmarkSource",
            },
        )
        self.assertTrue(payload["boundaries"]["researchOnly"])
        self.assertFalse(payload["boundaries"]["executionEnabled"])


if __name__ == "__main__":
    unittest.main()
