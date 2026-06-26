import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from my_quant.strategy_research.web_report.build_b1_small_capital_report import (
    build_html,
    load_report_data,
)


def _write_report_inputs(results_dir: Path, prefix: str, trades: pd.DataFrame) -> None:
    pd.DataFrame(
        {
            "date": ["2025-01-02", "2025-01-03"],
            "nav": [1.0, 1.05],
        }
    ).to_csv(results_dir / f"{prefix}_full_nav.csv", index=False)
    trades.to_csv(results_dir / f"{prefix}_full_trades.csv", index=False)
    pd.DataFrame(
        {
            "strategy": [prefix],
            "windows": [1],
            "return_pass_windows": [1],
            "drawdown_pass_windows": [1],
            "mean_annual_return": [0.5],
            "min_annual_return": [0.5],
            "worst_drawdown": [-0.1],
            "mean_calmar": [5.0],
            "return_fail_windows": [0],
            "drawdown_fail_windows": [0],
            "passes_all_windows": [True],
        }
    ).to_csv(results_dir / f"{prefix}_summary.csv", index=False)
    pd.DataFrame(
        {
            "strategy": [prefix],
            "window": ["full"],
            "start": ["2025-01-01"],
            "end": ["2025-01-31"],
            "symbol_count": [1],
            "annual_return": [0.5],
            "max_drawdown": [-0.1],
            "calmar": [5.0],
            "passes_return_gate": [True],
            "passes_drawdown_gate": [True],
            "trade_count": [len(trades)],
            "candidate_count": [1],
        }
    ).to_csv(results_dir / f"{prefix}_details.csv", index=False)
    (results_dir / f"{prefix}_full_manifest.json").write_text(
        json.dumps(
            {
                "strategy": "unit",
                "start": "2025-01-01",
                "end": "2025-01-31",
                "symbol_count": 1,
            }
        ),
        encoding="utf-8",
    )


class B1SmallCapitalReportTest(unittest.TestCase):
    def test_html_contains_required_sections_without_display_capital_scaling(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            results_dir = Path(temp_dir)
            prefix = "unit_b1"
            trades = pd.DataFrame(
                {
                    "date": ["2025-01-02", "2025-01-03"],
                    "symbol": ["600000", "600000"],
                    "side": ["buy", "sell"],
                    "shares": [1000, 1000],
                    "price": [10.0, 10.5],
                    "value": [10000.0, 10500.0],
                    "reason": ["next_day_entry", "take_profit_5"],
                    "cash_after": [9990.0, 20479.5],
                }
            )
            _write_report_inputs(results_dir, prefix, trades)

            data = load_report_data(prefix, results_dir=results_dir)
            html = build_html(data, generated_at="2026-06-17 12:00 +08:00")

        self.assertIn("净值曲线", html)
        self.assertIn("每笔交易盈亏金额分布", html)
        self.assertIn("每笔交易盈亏比例分布", html)
        self.assertIn("买卖明细", html)
        self.assertIn("主板", html)
        self.assertIn("20,000", html)
        self.assertIn("10,000.00", html)
        self.assertNotIn("10,000,000", html)

    def test_raw_trade_table_keeps_engine_order_with_same_day_sell_before_buy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            results_dir = Path(temp_dir)
            prefix = "unit_b1_order"
            trades = pd.DataFrame(
                {
                    "date": ["2025-01-02", "2025-01-03", "2025-01-03"],
                    "symbol": ["600000", "600001", "600002"],
                    "side": ["buy", "sell", "buy"],
                    "shares": [1000, 1000, 100],
                    "price": [10.0, 10.5, 6.0],
                    "value": [10000.0, 10500.0, 600.0],
                    "reason": ["next_day_entry", "take_profit_5", "next_day_entry"],
                    "cash_after": [9990.0, 20479.5, 19879.5],
                }
            )
            _write_report_inputs(results_dir, prefix, trades)

            data = load_report_data(prefix, results_dir=results_dir)
            html = build_html(data, generated_at="2026-06-17 12:00 +08:00")

        sell_marker = "<td><code>600001</code></td><td class='sell'>卖出</td>"
        buy_marker = "<td><code>600002</code></td><td class='buy'>买入</td>"
        self.assertLess(html.index(sell_marker), html.index(buy_marker))


if __name__ == "__main__":
    unittest.main()
