from __future__ import annotations

import unittest

import pandas as pd

from backend.app.research_engine.validation import build_walk_forward_windows


class ResearchEngineValidationTest(unittest.TestCase):
    def test_build_walk_forward_windows_returns_rolling_windows(self):
        index = pd.date_range("2024-01-01", periods=12)

        windows = build_walk_forward_windows(
            index,
            train_size=6,
            test_size=3,
            step_size=3,
            anchored=False,
        )

        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0], (index[0], index[5], index[6], index[8]))
        self.assertEqual(windows[1], (index[3], index[8], index[9], index[11]))

    def test_build_walk_forward_windows_returns_anchored_windows(self):
        index = pd.date_range("2024-01-01", periods=12)

        windows = build_walk_forward_windows(
            index,
            train_size=6,
            test_size=3,
            step_size=3,
            anchored=True,
        )

        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0], (index[0], index[5], index[6], index[8]))
        self.assertEqual(windows[1], (index[0], index[8], index[9], index[11]))


if __name__ == "__main__":
    unittest.main()
