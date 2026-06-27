from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.us_research import build_us_research_import_preview, build_us_research_overview


class UsResearchBackendTest(unittest.TestCase):
    def test_builds_sample_only_us_research_contract_from_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            us_root = root / "my_quant" / "us_research"
            (us_root / "config").mkdir(parents=True)
            (us_root / "data" / "snapshots").mkdir(parents=True)
            (us_root / "reports").mkdir(parents=True)
            (us_root / "data").mkdir(exist_ok=True)

            (us_root / "config" / "watchlist_symbols.csv").write_text(
                "\n".join(
                    [
                        "ticker,name,role,theme,subtheme,instrument_type,leverage_factor,risk_tag,notes",
                        "NVDA,NVIDIA,holding,AI infrastructure,accelerator,equity,1,core,sample only",
                        "SOXL,Direxion Daily Semiconductor Bull 3X Shares,watch,AI infrastructure,leveraged semiconductor ETF,leveraged ETF,3,leveraged,sample only",
                    ]
                ),
                encoding="utf-8",
            )
            (us_root / "data" / "holdings_sample.csv").write_text(
                "\n".join(
                    [
                        "ticker,instrument_type,quantity,cost_basis,theme,leverage_factor,risk_tag,notes",
                        "NVDA,equity,1,1000,AI infrastructure,1,core,sample holding only",
                    ]
                ),
                encoding="utf-8",
            )
            (us_root / "data" / "snapshots" / "us_snapshot_latest.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "source": "yfinance",
                        "fetched_at": "2026-06-26T11:53:29+00:00",
                        "symbols": [
                            {
                                "ticker": "NVDA",
                                "close": 195.74,
                                "latest_date": "2026-06-25",
                                "is_stale": False,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (us_root / "reports" / "latest_us_watchlist_backtest.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "rows": [
                            {
                                "ticker": "NVDA",
                                "strategy": "trend_pullback_no_chase",
                                "annual_return": 0.18,
                                "max_drawdown": -0.12,
                                "trade_count": 4,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            overview = build_us_research_overview(root)

        self.assertTrue(overview["isSample"])
        self.assertEqual(overview["source"], "file-sample")
        self.assertFalse(overview["dataBoundary"]["brokerConnected"])
        self.assertFalse(overview["dataBoundary"]["realHoldingsImported"])
        self.assertEqual(overview["marketSnapshot"]["status"], "ok")
        self.assertEqual(len(overview["watchlist"]), 2)
        self.assertEqual(len(overview["portfolioSnapshots"]), 1)
        self.assertEqual(overview["portfolioSnapshots"][0]["holdings"][0]["ticker"], "NVDA")

        nvda = next(asset for asset in overview["assets"] if asset["ticker"] == "NVDA")
        self.assertTrue(nvda["isSample"])
        self.assertEqual(nvda["role"], "holding")
        self.assertEqual(nvda["latestClose"], 195.74)
        self.assertEqual(nvda["sampleQuantity"], 1.0)
        self.assertEqual(nvda["sampleCostBasis"], 1000.0)
        self.assertEqual(nvda["backtest"]["strategy"], "trend_pullback_no_chase")

        soxl = next(asset for asset in overview["assets"] if asset["ticker"] == "SOXL")
        self.assertEqual(soxl["leverageFactor"], 3.0)
        self.assertIsNone(soxl["latestClose"])
        self.assertIsNone(soxl["sampleQuantity"])

    def test_builds_db_import_preview_without_enabling_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            us_root = root / "my_quant" / "us_research"
            (us_root / "config").mkdir(parents=True)
            (us_root / "data" / "snapshots").mkdir(parents=True)
            (us_root / "reports").mkdir(parents=True)
            (us_root / "data").mkdir(exist_ok=True)

            (us_root / "config" / "watchlist_symbols.csv").write_text(
                "\n".join(
                    [
                        "ticker,name,role,theme,subtheme,instrument_type,leverage_factor,risk_tag,notes",
                        "NVDA,NVIDIA,holding,AI infrastructure,accelerator,equity,1,core,sample only",
                        "SOXL,Direxion Daily Semiconductor Bull 3X Shares,watch,AI infrastructure,leveraged semiconductor ETF,leveraged ETF,3,leveraged,sample only",
                    ]
                ),
                encoding="utf-8",
            )
            (us_root / "data" / "holdings_sample.csv").write_text(
                "\n".join(
                    [
                        "ticker,instrument_type,quantity,cost_basis,theme,leverage_factor,risk_tag,notes",
                        "NVDA,equity,1,1000,AI infrastructure,1,core,sample holding only",
                    ]
                ),
                encoding="utf-8",
            )
            (us_root / "data" / "snapshots" / "us_snapshot_latest.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "source": "yfinance",
                        "fetched_at": "2026-06-26T11:53:29+00:00",
                        "symbols": [
                            {
                                "ticker": "NVDA",
                                "close": 195.74,
                                "latest_date": "2026-06-25",
                                "is_stale": False,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (us_root / "reports" / "latest_us_watchlist_backtest.json").write_text(
                json.dumps({"status": "ok", "rows": []}),
                encoding="utf-8",
            )

            preview = build_us_research_import_preview(root)

        self.assertEqual(preview["mode"], "preview")
        self.assertTrue(preview["isSample"])
        self.assertFalse(preview["writesEnabled"])
        self.assertFalse(preview["requiresConfirmation"])
        self.assertEqual(preview["importEndpoint"], "POST /api/us-research/import-sample")
        self.assertTrue(preview["validation"]["canExecute"])
        self.assertEqual(preview["validation"]["dbSchema"], "ready")
        self.assertEqual(preview["validation"]["blockers"], [])
        self.assertEqual(preview["summary"]["assets"], 2)
        self.assertEqual(preview["summary"]["assetDailyPrices"], 1)
        self.assertEqual(preview["summary"]["watchlistItems"], 2)
        self.assertEqual(preview["summary"]["portfolioSnapshots"], 1)
        self.assertEqual(preview["targetTables"][0]["table"], "assets")
        self.assertEqual(preview["records"]["assets"][0]["naturalKey"], "US:NVDA")
        self.assertEqual(preview["records"]["assetDailyPrices"][0]["naturalKey"], "US:NVDA:2026-06-25")
        self.assertEqual(preview["records"]["portfolioSnapshots"][0]["snapshotId"], "sample-latest")


if __name__ == "__main__":
    unittest.main()
