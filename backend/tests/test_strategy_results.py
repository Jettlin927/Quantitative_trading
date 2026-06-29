from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.strategy_results import build_strategy_results_overview


class StrategyResultsTest(unittest.TestCase):
    def test_builds_readonly_strategy_result_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results_root = root / "docs" / "research" / "strategy-results"
            results_root.mkdir(parents=True)
            (results_root / "manifest.json").write_text(
                json.dumps(
                    {
                        "source": "docs/research/strategy-results",
                        "mode": "readonly",
                        "executionEnabled": False,
                        "resultSets": [
                            {
                                "id": "b1",
                                "title": "B1",
                                "artifacts": {
                                    "phasedJson": "phases.json",
                                    "scoreScanCsv": "scan.csv",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (results_root / "phases.json").write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "phase": "a",
                                "annual_return": 0.2,
                                "max_drawdown": -0.1,
                                "passes_return_gate": False,
                                "passes_drawdown_gate": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (results_root / "scan.csv").write_text(
                "strategy,annual_return,passes_return_gate\nx,0.1,False\n",
                encoding="utf-8",
            )

            overview = build_strategy_results_overview(root)

        self.assertEqual(overview["mode"], "readonly")
        self.assertFalse(overview["executionEnabled"])
        self.assertEqual(overview["resultSets"][0]["summary"]["phaseCount"], 1)
        self.assertEqual(overview["resultSets"][0]["scoreScanTop"][0]["annual_return"], 0.1)
        self.assertFalse(overview["resultSets"][0]["scoreScanTop"][0]["passes_return_gate"])


if __name__ == "__main__":
    unittest.main()
