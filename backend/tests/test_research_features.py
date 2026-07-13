from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from backend.app.quant_research.features import (
    cross_section_percentile_rank,
    cross_section_winsorize,
    equal_weight_targets,
    interval_returns,
    moving_average,
    rolling_volatility,
    rolling_zscore,
    simple_returns,
)


class ResearchFeaturesTest(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame(
            {
                "ts_code": ["AAA.SZ"] * 6 + ["BBB.SH"] * 6,
                "trade_date": list(pd.date_range("2026-01-05", periods=6, freq="B")) * 2,
                "adj_close": [10, 11, 12, 11, 13, 14, 20, 19, 21, 22, 22, 24],
            }
        )

    def test_time_series_features_preserve_full_window_warmup(self):
        returns = simple_returns(self.frame, "adj_close", "return_1d")
        interval = interval_returns(self.frame, "adj_close", window=3, output_column="return_3d")
        average = moving_average(self.frame, "adj_close", window=3, output_column="ma_3")
        volatility = rolling_volatility(self.frame, "adj_close", window=3, output_column="vol_3")
        zscore = rolling_zscore(self.frame, "adj_close", window=3, output_column="z_3")

        for code in ("AAA.SZ", "BBB.SH"):
            rows = self.frame["ts_code"].eq(code)
            self.assertEqual(returns.loc[rows, "return_1d"].isna().sum(), 1)
            self.assertEqual(interval.loc[rows, "return_3d"].isna().sum(), 3)
            self.assertEqual(average.loc[rows, "ma_3"].isna().sum(), 2)
            self.assertEqual(volatility.loc[rows, "vol_3"].isna().sum(), 3)
            self.assertEqual(zscore.loc[rows, "z_3"].isna().sum(), 2)

    def test_appending_future_rows_does_not_change_feature_prefix(self):
        prefix = self.frame.groupby("ts_code", sort=False).head(5).reset_index(drop=True)
        expected = moving_average(prefix, "adj_close", window=3, output_column="ma_3")
        actual = moving_average(self.frame, "adj_close", window=3, output_column="ma_3")
        actual_prefix = actual.groupby("ts_code", sort=False).head(5).reset_index(drop=True)
        pd.testing.assert_frame_equal(expected, actual_prefix)

    def test_cross_section_ties_nulls_and_winsorization_are_deterministic(self):
        frame = pd.DataFrame(
            {
                "trade_date": [pd.Timestamp("2026-01-05")] * 5,
                "ts_code": ["AAA.SZ", "BBB.SH", "CCC.SZ", "DDD.SH", "EEE.SZ"],
                "score": [1.0, 1.0, 3.0, 100.0, np.nan],
            }
        )
        ranked = cross_section_percentile_rank(frame, "score", output_column="rank")
        self.assertEqual(ranked.loc[0, "rank"], ranked.loc[1, "rank"])
        self.assertTrue(pd.isna(ranked.loc[4, "rank"]))
        winsorized = cross_section_winsorize(
            frame,
            "score",
            lower_quantile=0.25,
            upper_quantile=0.75,
            output_column="winsorized",
        )
        self.assertLess(winsorized.loc[3, "winsorized"], 100)
        self.assertTrue(pd.isna(winsorized.loc[4, "winsorized"]))

    def test_equal_weight_targets_use_stable_score_then_symbol_order(self):
        scores = pd.DataFrame(
            {
                "trade_date": [pd.Timestamp("2026-01-05")] * 3,
                "ts_code": ["BBB.SH", "AAA.SZ", "CCC.SZ"],
                "score": [1.0, 1.0, 0.5],
            }
        ).sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
        targets = equal_weight_targets(scores, "score", top_n=2, max_weight=0.4)
        self.assertEqual(targets["ts_code"].tolist(), ["AAA.SZ", "BBB.SH"])
        self.assertEqual(targets["target_weight"].tolist(), [0.4, 0.4])

    def test_rejects_unsorted_duplicate_or_infinite_inputs(self):
        unsorted = self.frame.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
        with self.assertRaisesRegex(ValueError, "排序"):
            moving_average(unsorted, "adj_close", window=3)
        duplicate = pd.concat([self.frame, self.frame.iloc[[0]]], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "重复"):
            simple_returns(duplicate, "adj_close")
        infinite = self.frame.copy()
        infinite["adj_close"] = infinite["adj_close"].astype(float)
        infinite.loc[0, "adj_close"] = np.inf
        with self.assertRaisesRegex(ValueError, "非有限"):
            simple_returns(infinite, "adj_close")


if __name__ == "__main__":
    unittest.main()
