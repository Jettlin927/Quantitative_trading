from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.strategy_lifecycle import load_strategy_lifecycle, lookup_strategy_lifecycle


class StrategyLifecycleTest(unittest.TestCase):
    def test_loads_lifecycle_index_and_filters_primary_dashboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lifecycle_path = root / "docs" / "research" / "strategy-lifecycle.json"
            lifecycle_path.parent.mkdir(parents=True)
            lifecycle_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "strategies": [
                            {
                                "strategyId": "active-risk8",
                                "label": "Risk8",
                                "lifecycleStatus": "active",
                                "showInPrimaryDashboard": True,
                                "evidenceRetention": "keep",
                            },
                            {
                                "strategyId": "old-ram",
                                "label": "Old RAM",
                                "lifecycleStatus": "archived_negative_evidence",
                                "showInPrimaryDashboard": False,
                                "evidenceRetention": "keep",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            lifecycle = load_strategy_lifecycle(root)

        self.assertEqual(lifecycle["source"], "docs/research/strategy-lifecycle.json")
        self.assertEqual(lifecycle["counts"]["total"], 2)
        self.assertEqual(lifecycle["counts"]["active"], 1)
        self.assertEqual(lifecycle["counts"]["archived_negative_evidence"], 1)
        self.assertEqual([item["strategyId"] for item in lifecycle["primaryDashboardStrategies"]], ["active-risk8"])

        archived = lookup_strategy_lifecycle(lifecycle, "old-ram")
        self.assertEqual(archived["lifecycleStatus"], "archived_negative_evidence")
        self.assertFalse(archived["showInPrimaryDashboard"])
        self.assertEqual(archived["evidenceRetention"], "keep")

    def test_lookup_returns_safe_default_for_unknown_strategy(self):
        lifecycle = {"strategies": []}

        result = lookup_strategy_lifecycle(lifecycle, "missing")

        self.assertEqual(result["strategyId"], "missing")
        self.assertEqual(result["lifecycleStatus"], "unknown")
        self.assertFalse(result["showInPrimaryDashboard"])
        self.assertEqual(result["evidenceRetention"], "keep")


if __name__ == "__main__":
    unittest.main()
