from __future__ import annotations

from datetime import date
import unittest

from backend.app.strategy_evaluation import build_evaluation_windows


class StrategyEvaluationTest(unittest.TestCase):
    def test_builds_three_windows_with_only_first_window_covered(self):
        spec = {"window": {"startDate": "2020-01-01", "endDate": "2024-12-31"}}
        analysis = {"targetMet": True}

        windows = build_evaluation_windows(spec, analysis, today=date(2026, 6, 27))

        self.assertEqual([item["id"] for item in windows], ["train-2020-2024", "oos-2025-now", "bear-market-observe"])
        self.assertEqual(windows[0]["status"], "pass")
        self.assertEqual(windows[1]["status"], "missing")
        self.assertEqual(windows[2]["status"], "observation_pending")
        self.assertFalse(windows[2]["qualifiesStrategy"])

    def test_covered_window_fails_when_target_not_met(self):
        spec = {"window": {"startDate": "2020-01-01", "endDate": "2024-12-31"}}
        analysis = {"targetMet": False, "strictTargetMet": False}

        windows = build_evaluation_windows(spec, analysis, today=date(2026, 6, 27))

        self.assertEqual(windows[0]["status"], "fail")
        self.assertTrue(windows[0]["qualifiesStrategy"])


if __name__ == "__main__":
    unittest.main()
