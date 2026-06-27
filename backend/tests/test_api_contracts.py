from __future__ import annotations

import unittest

from backend.app import main


class ApiContractTest(unittest.TestCase):
    def test_strategy_lifecycle_route_contract_resets_all_legacy_strategies(self):
        payload = main.get_strategy_lifecycle()

        self.assertEqual(payload["source"], "docs/research/strategy-lifecycle.json")
        self.assertEqual(payload["counts"]["total"], 14)
        self.assertEqual(payload["counts"]["legacy_reset"], 14)
        self.assertEqual(payload["primaryDashboardStrategies"], [])

    def test_us_research_import_preview_route_contract_stays_readonly(self):
        payload = main.get_us_research_import_preview()

        self.assertEqual(payload["mode"], "preview")
        self.assertTrue(payload["isSample"])
        self.assertFalse(payload["writesEnabled"])
        self.assertFalse(payload["requiresConfirmation"])
        self.assertEqual(payload["importEndpoint"], "POST /api/us-research/import-sample")
        self.assertTrue(payload["validation"]["canExecute"])
        self.assertEqual(payload["validation"]["dbSchema"], "ready")
        self.assertEqual(payload["validation"]["blockers"], [])
        self.assertEqual(payload["summary"]["assets"], 4)
        self.assertEqual(payload["summary"]["assetDailyPrices"], 4)
        self.assertEqual(payload["summary"]["watchlistItems"], 4)
        self.assertEqual(payload["summary"]["portfolioSnapshots"], 1)

    def test_strategy_evaluations_route_contract_has_no_active_legacy_strategy(self):
        old_query_daily_bar_coverage = main.query_daily_bar_coverage
        main.query_daily_bar_coverage = lambda db: {}
        try:
            payload = main.list_strategy_evaluations(db=None)
        finally:
            main.query_daily_bar_coverage = old_query_daily_bar_coverage

        self.assertEqual(payload["source"], "backend")
        self.assertEqual(payload["resetStatus"], "legacy_strategies_removed_from_primary")
        self.assertEqual(payload["evaluations"], [])
        self.assertEqual(
            [window["id"] for window in payload["evaluationWindows"]],
            ["train-2020-2024", "oos-2025-now", "bear-market-observe"],
        )
        self.assertEqual(payload["evaluationWindows"][0]["status"], "missing")
        self.assertEqual(payload["evaluationWindows"][1]["status"], "missing")
        self.assertEqual(payload["evaluationWindows"][2]["status"], "observation_pending")

    def test_legacy_executable_strategy_is_gone_from_direct_endpoint(self):
        with self.assertRaises(Exception) as context:
            main.get_executable_strategy("cross-section-strength-risk8", db=None)

        self.assertEqual(context.exception.status_code, 410)
        self.assertIn("退场", context.exception.detail)

    def test_research_dashboard_route_contract_unifies_frontend_data_sources(self):
        old_query_daily_bar_coverage = main.query_daily_bar_coverage
        main.query_daily_bar_coverage = lambda db: {}
        try:
            payload = main.get_research_dashboard(db=None, run_limit=5)
        finally:
            main.query_daily_bar_coverage = old_query_daily_bar_coverage

        self.assertEqual(payload["source"], "backend")
        self.assertEqual(payload["health"]["status"], "ok")
        self.assertIsNone(payload["baseline"])
        self.assertEqual(payload["strategyEvaluation"]["evaluations"], [])
        self.assertEqual(payload["strategyEvaluation"]["resetStatus"], "legacy_strategies_removed_from_primary")
        self.assertEqual(payload["strategyLifecycle"]["counts"]["legacy_reset"], 14)
        self.assertFalse(payload["usImportPreview"]["writesEnabled"])
        self.assertLessEqual(payload["researchRuns"]["count"], 5)


if __name__ == "__main__":
    unittest.main()
