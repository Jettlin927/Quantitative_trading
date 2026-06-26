import json
import tempfile
import unittest
from pathlib import Path

from my_quant.strategy_research.experiment.kronos_archive_backtest import (
    load_prediction_archive,
    run_archive_backtest,
    summarize_archive_backtest,
)


def _archive_payload(predicted: list[float], actual: list[float]) -> dict:
    return {
        "timestamp": "2025-08-26T16:38:00",
        "file_path": "/tmp/BTC_USDT_USDT-5m-futures.feather",
        "prediction_params": {"start_date": "2025-08-02T13:24", "lookback": 400, "pred_len": len(predicted)},
        "input_data_summary": {"last_values": {"close": 100.0}},
        "prediction_results": [
            {"timestamp": f"2025-08-03T00:{index:02d}:00", "close": close}
            for index, close in enumerate(predicted)
        ],
        "actual_data": [
            {"timestamp": f"2025-08-03T00:{index:02d}:00", "close": close}
            for index, close in enumerate(actual)
        ],
    }


class KronosArchiveBacktestTest(unittest.TestCase):
    def test_load_prediction_archive_normalizes_symbol_and_paths(self):
        payload = _archive_payload([101, 102, 103], [100.5, 101.5, 102.5])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prediction.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            archive = load_prediction_archive(path)

        self.assertEqual(archive.symbol, "BTC_USDT")
        self.assertEqual(archive.last_close, 100.0)
        self.assertEqual(len(archive.predicted), 3)
        self.assertEqual(len(archive.actual), 3)

    def test_run_archive_backtest_reports_signal_and_realized_return(self):
        payload = _archive_payload(
            [100.5 + index * 0.4 for index in range(20)],
            [101.0 + index * 0.3 for index in range(20)],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prediction.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            archive = load_prediction_archive(path)

        row = run_archive_backtest(archive)

        self.assertEqual(row["symbol"], "BTC_USDT")
        self.assertEqual(row["action"], "buy")
        self.assertGreater(row["forecast_horizon_return"], 0.03)
        self.assertGreater(row["actual_horizon_return"], 0.0)
        self.assertTrue(row["direction_hit"])
        self.assertGreater(row["long_only_return"], 0.0)

    def test_summarize_archive_backtest_counts_actions_and_returns(self):
        rows = [
            {"action": "buy", "direction_hit": True, "long_only_return": 0.04, "actual_horizon_return": 0.04},
            {"action": "sell", "direction_hit": True, "long_only_return": 0.0, "actual_horizon_return": -0.03},
            {"action": "hold", "direction_hit": False, "long_only_return": 0.0, "actual_horizon_return": 0.01},
        ]

        summary = summarize_archive_backtest(rows)

        self.assertEqual(summary["prediction_count"], 3)
        self.assertEqual(summary["buy_count"], 1)
        self.assertEqual(summary["sell_count"], 1)
        self.assertEqual(summary["hold_count"], 1)
        self.assertAlmostEqual(summary["direction_hit_rate"], 2 / 3)
        self.assertAlmostEqual(summary["long_only_total_return"], 0.04)


if __name__ == "__main__":
    unittest.main()
