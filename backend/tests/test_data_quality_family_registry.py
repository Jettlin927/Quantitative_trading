from __future__ import annotations

import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.database import Base
from backend.app.data_quality.contracts import QualityCheckContract, QualityRuleResult
from backend.app.data_quality.family_registry import QualityRuleFamily, evaluate_quality_families
from backend.app.data_quality.families import QUALITY_FAMILY_REGISTRY
from backend.app.data_quality.runner import run_data_quality_check


class DataQualityFamilyRegistryTest(unittest.TestCase):
    @staticmethod
    def contract() -> QualityCheckContract:
        return QualityCheckContract.create(
            scope="a_share_cross_section",
            start_date=date(2026, 1, 2),
            end_date=date(2026, 1, 5),
            universe=["000001.SZ"],
            universe_source="backend/tests/fixtures/quality-universe.txt",
            universe_as_of_date=date(2026, 1, 2),
            benchmark="000300.SH",
        )

    def test_registry_is_static_ordered_and_schema_is_first(self):
        self.assertIsInstance(QUALITY_FAMILY_REGISTRY, tuple)
        self.assertEqual(
            [family.family_id for family in QUALITY_FAMILY_REGISTRY],
            ["schema", "legacy"],
        )

    def test_schema_blocker_short_circuits_later_families(self):
        calls: list[str] = []

        def schema(db: Session, contract: QualityCheckContract) -> list[QualityRuleResult]:
            calls.append("schema")
            return [QualityRuleResult.blocked("schema.contract", "missing_table", failed_rows=1)]

        def later(db: Session, contract: QualityCheckContract) -> list[QualityRuleResult]:
            calls.append("later")
            return [QualityRuleResult.passed("later.rule", "later_table")]

        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            with Session(engine) as db:
                results = evaluate_quality_families(
                    db,
                    self.contract(),
                    (
                        QualityRuleFamily("schema", schema, stop_on_blocked=True),
                        QualityRuleFamily("later", later),
                    ),
                )
        finally:
            engine.dispose()

        self.assertEqual(calls, ["schema"])
        self.assertEqual([result.rule_id for result in results], ["schema.contract"])

    def test_four_universe_shapes_keep_their_contract_identity(self):
        common = {
            "scope": "a_share_cross_section",
            "start_date": date(2026, 1, 2),
            "end_date": date(2026, 1, 5),
            "benchmark": "000300.SH",
        }
        contracts = (
            QualityCheckContract.create(
                **common,
                universe=["000001.SZ"],
                universe_type="explicit_snapshot",
                universe_source="backend/tests/fixtures/quality-universe.txt",
                universe_as_of_date=date(2026, 1, 2),
            ),
            QualityCheckContract.create(
                **common,
                universe=["000001.SZ"],
                universe_type="static_current",
                universe_source="backend/tests/fixtures/quality-universe.txt",
            ),
            QualityCheckContract.create(
                **common,
                universe=[],
                universe_type="industry_membership",
                universe_source="industry_members",
                universe_source_key="801080.SI",
            ),
            QualityCheckContract.create(
                **common,
                universe=[],
                required_datasets=["industry_classifications"],
                universe_type="industry_level_membership",
                universe_source="industry_classifications+industry_members",
                universe_classification_src="SW2021",
                universe_classification_level="L1",
            ),
        )

        self.assertEqual(
            [
                (
                    contract.universe_type,
                    contract.universe_source,
                    contract.universe_source_key,
                    contract.universe_classification_src,
                    contract.universe_classification_level,
                    contract.universe_source_verified,
                    contract.universe_source_issue,
                )
                for contract in contracts
            ],
            [
                (
                    "explicit_snapshot",
                    "backend/tests/fixtures/quality-universe.txt",
                    None,
                    None,
                    None,
                    True,
                    None,
                ),
                (
                    "static_current",
                    "backend/tests/fixtures/quality-universe.txt",
                    None,
                    None,
                    None,
                    True,
                    None,
                ),
                (
                    "industry_membership",
                    "industry_members",
                    "801080.SI",
                    None,
                    None,
                    False,
                    "industry_membership_not_resolved",
                ),
                (
                    "industry_level_membership",
                    "industry_classifications+industry_members",
                    None,
                    "SW2021",
                    "L1",
                    False,
                    "industry_level_membership_not_resolved",
                ),
            ],
        )

    def test_run_seam_freezes_four_states_and_multi_result_ordering(self):
        cases = {
            "ready": [QualityRuleResult.passed("ready.rule", "ready_table")],
            "ready_with_warnings": [
                QualityRuleResult.warning("z.warning", "z_table", failed_rows=2),
                QualityRuleResult.warning("a.warning", "a_table", failed_rows=1),
            ],
            "blocked": [
                QualityRuleResult.warning("warning.rule", "warning_table", failed_rows=1),
                QualityRuleResult.blocked("blocked.rule", "blocked_table", failed_rows=1),
            ],
            "failed": [
                QualityRuleResult.blocked("blocked.rule", "blocked_table", failed_rows=1),
                QualityRuleResult.failed("engine.execution", "data_quality_runs", "timeout"),
            ],
        }
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        try:
            with Session(engine) as db:
                reports = {
                    expected_status: run_data_quality_check(
                        db,
                        self.contract(),
                        code_commit="characterization",
                        evaluator=lambda inspection_db, contract, fixture=fixture: fixture,
                    )
                    for expected_status, fixture in cases.items()
                }
        finally:
            engine.dispose()

        for expected_status, report in reports.items():
            with self.subTest(expected_status=expected_status):
                self.assertEqual(report["status"], expected_status)
                self.assertEqual(report["summary"]["status"], expected_status)
                self.assertEqual(report["summary"]["resultCount"], len(cases[expected_status]))
                self.assertEqual(report["codeCommit"], "characterization")
                self.assertEqual(
                    [(item["ruleId"], item["tableName"]) for item in report["results"]],
                    sorted((item.rule_id, item.table_name) for item in cases[expected_status]),
                )
        self.assertEqual(
            reports["ready_with_warnings"]["summary"]["warnings"],
            ["z.warning:z_table", "a.warning:a_table"],
        )
        self.assertEqual(
            reports["failed"]["summary"]["failedRules"],
            ["engine.execution:data_quality_runs"],
        )

    def test_registry_rejects_duplicate_family_and_result_identities(self):
        def first(db: Session, contract: QualityCheckContract) -> list[QualityRuleResult]:
            return [QualityRuleResult.passed("duplicate.rule", "same_table")]

        duplicate_families = (
            QualityRuleFamily("same", first),
            QualityRuleFamily("same", first),
        )
        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            with Session(engine) as db:
                with self.assertRaisesRegex(ValueError, "family_id"):
                    evaluate_quality_families(db, self.contract(), duplicate_families)

                with self.assertRaisesRegex(ValueError, "rule_id, table_name"):
                    evaluate_quality_families(
                        db,
                        self.contract(),
                        (
                            QualityRuleFamily("first", first),
                            QualityRuleFamily("second", first),
                        ),
                    )
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
