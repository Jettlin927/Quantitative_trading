#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


AUDIT_TESTS = (
    "backend.tests.test_research_strategy_dispatch.ResearchStrategyDispatchTest.test_unknown_strategy_fails_before_quality_or_snapshot",
    "backend.tests.test_a_share_price_baseline.ASharePriceBaselineTest.test_appending_future_rows_does_not_change_targets_or_ledger_prefix",
    "backend.tests.test_research_risk.ResearchRiskTest.test_appending_future_returns_does_not_change_risk_prefix",
    "backend.tests.test_research_reproduction.ResearchReproductionTest.test_reproduce_rejects_corrupted_archived_outputs_and_audit_files",
    "backend.tests.test_research_reproduction.ResearchReproductionTest.test_v2_archive_rejects_resigned_semantically_invalid_ledger_before_recalculation",
    "backend.tests.test_research_risk.ResearchRiskTest.test_non_finite_inputs_and_tampered_contributions_fail",
    "backend.tests.test_a_share_research_snapshot.AShareResearchSnapshotTest.test_snapshot_re_resolves_membership_and_rejects_old_quality_after_change",
    "backend.tests.test_a_share_price_baseline.ASharePriceBaselineTest.test_a_share_execution_uses_frozen_limit_up_and_limit_down_prices",
    "backend.tests.test_research_allocation.ResearchAllocationTest.test_infeasible_or_invalid_inputs_fail_without_relaxing_constraints",
    "backend.tests.test_research_walk_forward.ResearchWalkForwardTest.test_formal_run_archives_only_oos_window_metrics_and_reproduces",
    "backend.tests.test_a_share_price_baseline.ASharePriceBaselineTest.test_formal_a_share_run_archives_and_reproduces_without_database",
    "backend.tests.test_research_resume.ResearchResumeTest.test_resume_after_simulation_skips_completed_computation",
)


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromNames(AUDIT_TESTS)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
