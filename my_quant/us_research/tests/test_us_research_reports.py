import datetime as dt
import tempfile
import unittest
from pathlib import Path


class UsResearchReportsTest(unittest.TestCase):
    def test_snapshot_marks_partial_when_one_symbol_fails_and_computes_trend(self):
        from my_quant.us_research.scripts.refresh_us_snapshot import build_snapshot

        watchlist = [
            {
                "ticker": "AAA",
                "name": "Alpha AI",
                "role": "holding",
                "theme": "AI infrastructure",
                "subtheme": "storage",
                "instrument_type": "equity",
                "leverage_factor": "1",
                "risk_tag": "core",
                "notes": "sample row",
            },
            {
                "ticker": "BBB",
                "name": "Beta 2x",
                "role": "watch",
                "theme": "AI infrastructure",
                "subtheme": "leveraged beta",
                "instrument_type": "2x ETF",
                "leverage_factor": "2",
                "risk_tag": "leveraged",
                "notes": "sample row",
            },
        ]
        history_by_ticker = {
            "AAA": [
                {"date": f"2026-01-{day:02d}", "close": 100 + day, "high": 101 + day}
                for day in range(1, 61)
            ],
            "BBB": RuntimeError("network down"),
        }
        fetched_at = dt.datetime(2026, 6, 26, 18, 45, tzinfo=dt.timezone.utc)

        snapshot = build_snapshot(watchlist, history_by_ticker, fetched_at=fetched_at)

        self.assertEqual(snapshot["status"], "partial")
        self.assertEqual(snapshot["source"], "yfinance")
        alpha = snapshot["symbols"][0]
        self.assertFalse(alpha["is_stale"])
        self.assertEqual(alpha["ticker"], "AAA")
        self.assertGreater(alpha["close"], alpha["ma20"])
        self.assertGreater(alpha["return_20d_pct"], 0)
        beta = snapshot["symbols"][1]
        self.assertTrue(beta["is_stale"])
        self.assertEqual(beta["stale_reason"], "RuntimeError: network down")

    def test_report_writes_html_and_markdown_with_actions_and_freshness(self):
        from my_quant.us_research.scripts.build_us_operations_report import build_report_text

        snapshot = {
            "status": "partial",
            "source": "yfinance",
            "fetched_at": "2026-06-26T18:45:00+00:00",
            "symbols": [
                {
                    "ticker": "AAA",
                    "name": "Alpha AI",
                    "role": "holding",
                    "theme": "AI infrastructure",
                    "subtheme": "storage",
                    "instrument_type": "equity",
                    "leverage_factor": 1.0,
                    "risk_tag": "core",
                    "close": 160.0,
                    "ma20": 150.0,
                    "ma50": 140.0,
                    "pct_from_52w_high": -4.0,
                    "return_20d_pct": 12.0,
                    "is_stale": False,
                    "stale_reason": "",
                },
                {
                    "ticker": "BBB",
                    "name": "Beta 2x",
                    "role": "watch",
                    "theme": "AI infrastructure",
                    "subtheme": "leveraged beta",
                    "instrument_type": "2x ETF",
                    "leverage_factor": 2.0,
                    "risk_tag": "leveraged",
                    "close": None,
                    "ma20": None,
                    "ma50": None,
                    "pct_from_52w_high": None,
                    "return_20d_pct": None,
                    "is_stale": True,
                    "stale_reason": "RuntimeError: network down",
                },
            ],
        }
        holdings = [
            {
                "ticker": "AAA",
                "instrument_type": "equity",
                "quantity": "10",
                "cost_basis": "1200",
                "theme": "AI infrastructure",
                "leverage_factor": "1",
                "risk_tag": "core",
                "notes": "sample only",
            }
        ]

        backtest_rows = [
            {
                "ticker": "AAA",
                "strategy": "trend_pullback_no_chase",
                "annual_return": 0.18,
                "max_drawdown": -0.12,
                "trade_count": 4,
                "evidence_label": "只等回调",
            }
        ]

        markdown, html = build_report_text(snapshot, holdings, backtest_rows=backtest_rows)

        self.assertIn("研究辅助", markdown)
        self.assertIn("partial", markdown)
        self.assertIn("继续持有", markdown)
        self.assertIn("观察不动", markdown)
        self.assertIn("规则证据", markdown)
        self.assertIn("18.00%", markdown)
        self.assertIn("数据新鲜度", html)
        self.assertIn("规则证据", html)
        self.assertIn("RuntimeError: network down", html)

    def test_us_watchlist_backtest_outputs_metrics_and_rule_label(self):
        from my_quant.us_research.scripts.build_us_watchlist_backtest import run_watchlist_backtest

        watchlist = [
            {
                "ticker": "AAA",
                "name": "Alpha AI",
                "role": "watch",
                "theme": "AI infrastructure",
                "subtheme": "storage",
                "instrument_type": "equity",
                "leverage_factor": "1",
                "risk_tag": "core",
                "notes": "sample row",
            }
        ]
        bars = []
        for index in range(260):
            close = 100 + index * 0.2
            if 80 <= index <= 90:
                close -= 4
            bars.append({"date": f"2025-01-{(index % 28) + 1:02d}", "close": close, "high": close * 1.01, "low": close * 0.99})

        rows = run_watchlist_backtest(watchlist, {"AAA": bars})

        self.assertEqual(rows[0]["ticker"], "AAA")
        self.assertEqual(rows[0]["strategy"], "trend_pullback_no_chase")
        self.assertEqual(rows[0]["evidence_label"], "只等回调")
        self.assertIn("annual_return", rows[0])
        self.assertIn("max_drawdown", rows[0])
        self.assertGreaterEqual(rows[0]["trade_count"], 0)

    def test_cli_round_trip_writes_snapshot_and_reports_without_real_holdings(self):
        from my_quant.us_research.scripts.build_us_operations_report import write_reports
        from my_quant.us_research.scripts.refresh_us_snapshot import write_snapshot

        snapshot = {
            "status": "stale",
            "source": "yfinance",
            "fetched_at": "2026-06-26T18:45:00+00:00",
            "symbols": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            json_path = root / "snapshot.json"
            csv_path = root / "snapshot.csv"
            html_path = root / "latest_us_operations.html"
            md_path = root / "latest_us_operations.md"

            write_snapshot(snapshot, json_path=json_path, csv_path=csv_path)
            write_reports(snapshot, holdings=[], html_path=html_path, md_path=md_path)

            self.assertTrue(json_path.exists())
            self.assertTrue(csv_path.exists())
            self.assertTrue(html_path.exists())
            self.assertTrue(md_path.exists())
            self.assertIn("stale", md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
