from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy.orm import Session

from backend.app.quant_research.runner import run_quant_research
from backend.app.quant_research.snapshot import SnapshotCapacityPolicy
from backend.tests.research_test_support import create_golden_database, golden_run_config


class ResearchStrategyDispatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.engine, quality_run_id, universe_hash = create_golden_database(root / "golden.sqlite")
        self.config = golden_run_config(quality_run_id, universe_hash)
        self.output_root = root / "research-runs"

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def test_unknown_strategy_fails_before_quality_or_snapshot(self):
        config = {**self.config, "strategyId": "missing_strategy"}
        self._assert_rejected_before_data_access(config, "未登记")

    def test_path_or_expression_strategy_id_is_not_executable(self):
        for strategy_id in ("../../tmp/strategy.py", "package.module:run", "__import__('os')"):
            with self.subTest(strategy_id=strategy_id):
                config = {**self.config, "strategyId": strategy_id}
                self._assert_rejected_before_data_access(config, "策略 ID")

    def test_scope_mismatch_fails_before_quality_or_snapshot(self):
        config = {**self.config, "scope": "invalid_scope"}
        self._assert_rejected_before_data_access(config, "scope")

    def test_strategy_parameter_contract_fails_before_quality_or_snapshot(self):
        config = {**self.config, "featureParameters": {"lookbackGrid": [5, 10]}}
        self._assert_rejected_before_data_access(config, "featureParameters")

    def _assert_rejected_before_data_access(self, config: dict[str, object], message: str) -> None:
        with (
            patch("backend.app.quant_research.runner.validate_quality_gate") as quality_gate,
            patch("backend.app.quant_research.runner.freeze_input_snapshot") as snapshot_stage,
            Session(self.engine) as db,
            self.assertRaisesRegex(ValueError, message),
        ):
            run_quant_research(
                db,
                config,
                self.output_root,
                code_commit="golden-test-commit",
                schema_revision="test-schema",
                test_mode=True,
                capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
            )
        quality_gate.assert_not_called()
        snapshot_stage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
