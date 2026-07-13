from __future__ import annotations

import unittest

from backend.app.database import Base
from backend.app.quant_research.readiness import evaluate_risk_capability_readiness


class ResearchRiskReadinessTest(unittest.TestCase):
    def test_basic_industry_exposure_uses_existing_membership_only(self):
        result = evaluate_risk_capability_readiness(
            "basic_industry_exposure",
            {"industry_members"},
            {"industry_members": 10},
        )
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["capabilityReady"])
        self.assertEqual(result["requiredTables"], ["industry_members"])

    def test_index_and_industry_benchmark_capabilities_do_not_use_false_fallbacks(self):
        available = {"industry_members", "index_daily_bars"}
        counts = {"industry_members": 10, "index_daily_bars": 100}
        index_result = evaluate_risk_capability_readiness(
            "full_index_constituent_attribution",
            available,
            counts,
        )
        industry_result = evaluate_risk_capability_readiness(
            "industry_benchmark_comparison",
            available,
            counts,
        )
        self.assertEqual(index_result["blockers"], ["missing_table:index_weights"])
        self.assertEqual(
            industry_result["blockers"],
            ["missing_table:industry_proxy_daily"],
        )
        self.assertFalse(index_result["capabilityReady"])
        self.assertFalse(industry_result["capabilityReady"])

    def test_required_table_must_be_nonempty_and_no_schema_is_added(self):
        empty = evaluate_risk_capability_readiness(
            "full_index_constituent_attribution",
            {"index_weights"},
            {"index_weights": 0},
        )
        ready = evaluate_risk_capability_readiness(
            "full_index_constituent_attribution",
            {"index_weights"},
            {"index_weights": 1},
        )
        self.assertEqual(empty["blockers"], ["empty_table:index_weights"])
        self.assertTrue(ready["capabilityReady"])
        self.assertNotIn("index_weights", Base.metadata.tables)
        self.assertNotIn("industry_proxy_daily", Base.metadata.tables)

    def test_unknown_capability_fails(self):
        with self.assertRaises(ValueError):
            evaluate_risk_capability_readiness("optimizer", set(), {})


if __name__ == "__main__":
    unittest.main()
