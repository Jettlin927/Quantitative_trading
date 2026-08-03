from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from typing import Any

from scripts.research.verify_entrypoint_inventory import (  # pyright: ignore[reportMissingImports]
    _verify_managed_executables,
    verify_inventory,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = REPO_ROOT / "configs" / "research-entrypoints-v1.json"


class ResearchEntrypointInventoryTest(unittest.TestCase):
    def test_versioned_inventory_is_complete_and_valid(self) -> None:
        self.assertEqual([], verify_inventory(INVENTORY_PATH, REPO_ROOT))

    def test_verifier_requires_versioned_root_identity_and_purpose(self) -> None:
        wrong_identity = self._verify_with_change(
            lambda inventory: inventory.update(inventory_id="research-entrypoints-v2")
        )
        self.assertTrue(any("inventory_id" in error for error in wrong_identity), wrong_identity)

        missing_purpose = self._verify_with_change(lambda inventory: inventory.pop("purpose"))
        self.assertTrue(any("purpose" in error for error in missing_purpose), missing_purpose)

    def test_verifier_requires_integer_schema_version(self) -> None:
        for invalid_version in (True, 1.0):
            with self.subTest(schema_version=invalid_version):
                errors = self._verify_with_change(
                    lambda inventory: inventory.update(schema_version=invalid_version)
                )
                self.assertTrue(any("schema_version" in error for error in errors), errors)

    def test_verifier_reports_missing_entry_path(self) -> None:
        errors = self._verify_with_change(
            lambda inventory: inventory["entries"][0].update(path="missing/entry.py")
        )
        self.assertTrue(any("入口路径不存在" in error for error in errors), errors)

    def test_verifier_reports_duplicate_id(self) -> None:
        def duplicate(inventory: dict[str, Any]) -> None:
            entries = inventory["entries"]
            entries[1]["id"] = entries[0]["id"]

        errors = self._verify_with_change(duplicate)
        self.assertTrue(any("入口 ID 重复" in error for error in errors), errors)

    def test_verifier_requires_each_expected_id_even_when_coverage_remains(self) -> None:
        def remove_audit(inventory: dict[str, Any]) -> None:
            inventory["entries"] = [
                entry
                for entry in inventory["entries"]
                if entry["id"] != "candidate.audit_cli"
            ]

        errors = self._verify_with_change(remove_audit)
        self.assertTrue(any("candidate.audit_cli" in error for error in errors), errors)

    def test_verifier_rejects_expected_id_at_another_path(self) -> None:
        def move_audit(inventory: dict[str, Any]) -> None:
            entry = next(
                entry
                for entry in inventory["entries"]
                if entry["id"] == "candidate.audit_cli"
            )
            entry["path"] = "scripts/research/run_quant_research.py"

        errors = self._verify_with_change(move_audit)
        self.assertTrue(any("预期入口路径不匹配" in error for error in errors), errors)

    def test_verifier_rejects_misclassified_expected_entry(self) -> None:
        def misclassify_registry(inventory: dict[str, Any]) -> None:
            entry = next(
                entry
                for entry in inventory["entries"]
                if entry["id"] == "active.strategy_registry"
            )
            entry["classification"] = "historical_evidence"

        errors = self._verify_with_change(misclassify_registry)
        self.assertTrue(any("预期入口分类不匹配" in error for error in errors), errors)

    def test_verifier_rejects_entry_id_outside_versioned_contract(self) -> None:
        def replace_registry_id(inventory: dict[str, Any]) -> None:
            inventory["entries"][0]["id"] = "active.unversioned_cli"

        errors = self._verify_with_change(replace_registry_id)
        self.assertTrue(any("未列入版本化入口合同" in error for error in errors), errors)

    def test_managed_discovery_rejects_unregistered_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts" / "research"
            scripts.mkdir(parents=True)
            registered = scripts / "registered.py"
            registered.write_text(
                'if __name__ == "__main__":\n    raise SystemExit(0)\n',
                encoding="utf-8",
            )
            unregistered = scripts / "unregistered.py"
            unregistered.write_text(
                'if __name__ == "__main__":\n    raise SystemExit(0)\n',
                encoding="utf-8",
            )
            errors = _verify_managed_executables(
                {"scripts/research/registered.py"},
                root,
                ("scripts/research",),
            )
        self.assertEqual(
            ["发现未登记的受管 executable：scripts/research/unregistered.py"],
            errors,
        )

    def test_default_managed_discovery_includes_backend_research_entrypoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = root / "backend" / "app" / "research_worker.py"
            worker.parent.mkdir(parents=True)
            worker.write_text(
                'if __name__ == "__main__":\n    raise SystemExit(0)\n',
                encoding="utf-8",
            )
            errors = _verify_managed_executables(set(), root)

        self.assertEqual(
            ["发现未登记的受管 executable：backend/app/research_worker.py"],
            errors,
        )

    def test_personal_analysis_worker_stays_outside_formal_research_inventory(self) -> None:
        inventory_paths = {
            entry["path"] for entry in self._load_inventory()["entries"]
        }
        self.assertNotIn("backend/app/personal_analysis_worker.py", inventory_paths)
        self.assertEqual([], verify_inventory(INVENTORY_PATH, REPO_ROOT))

    def test_managed_discovery_rejects_reversed_main_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "scripts" / "research" / "reversed.py"
            script.parent.mkdir(parents=True)
            script.write_text(
                'if "__main__" == __name__:\n    raise SystemExit(0)\n',
                encoding="utf-8",
            )
            errors = _verify_managed_executables(set(), root, ("scripts/research",))

        self.assertEqual(
            ["发现未登记的受管 executable：scripts/research/reversed.py"],
            errors,
        )

    def test_managed_discovery_recursively_rejects_nested_unregistered_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts" / "research"
            executable_source = 'if __name__ == "__main__":\n    raise SystemExit(0)\n'
            nested_paths = (
                scripts / "z_reports" / "run.py",
                scripts / "a_reports" / "run.py",
            )
            for nested in nested_paths:
                nested.parent.mkdir(parents=True)
                nested.write_text(executable_source, encoding="utf-8")
            excluded_paths = (
                scripts / "__pycache__" / "cached.py",
                scripts / "vendor" / "tool.py",
            )
            for excluded in excluded_paths:
                excluded.parent.mkdir(parents=True, exist_ok=True)
                excluded.write_text(executable_source, encoding="utf-8")
            (scripts / "not_a_file.py").mkdir()

            errors = _verify_managed_executables(set(), root, ("scripts/research",))

        self.assertEqual(
            [
                "发现未登记的受管 executable：scripts/research/a_reports/run.py",
                "发现未登记的受管 executable：scripts/research/z_reports/run.py",
            ],
            errors,
        )

    def test_verifier_requires_candidate_retirement_conditions(self) -> None:
        def remove_conditions(inventory: dict[str, Any]) -> None:
            candidate = next(
                entry
                for entry in inventory["entries"]
                if entry["classification"] == "legacy_executable_candidate"
            )
            candidate["retirement_conditions"] = []

        errors = self._verify_with_change(remove_conditions)
        self.assertTrue(any("候选入口缺少退役条件" in error for error in errors), errors)

    def test_verifier_reports_broken_stable_reference(self) -> None:
        errors = self._verify_with_change(
            lambda inventory: inventory["entries"][0]["stable_references"].append(
                "docs/missing-contract.md"
            )
        )
        self.assertTrue(any("稳定引用不存在" in error for error in errors), errors)

    def test_renderers_have_independent_identity_and_retirement_conditions(self) -> None:
        inventory = self._load_inventory()
        expected = {
            "compat.render_etf_volatility_managed": "scripts/research/render_etf_volatility_managed_report.py",
            "compat.render_etf_trend_120d": "scripts/research/render_etf_trend_120d_report.py",
            "compat.render_a_share_b1": "scripts/research/render_a_share_b1_report.py",
        }
        observed = {
            entry["id"]: entry
            for entry in inventory["entries"]
            if entry["id"] in expected
        }
        self.assertEqual(set(expected), set(observed))
        for entry_id, path in expected.items():
            self.assertEqual(path, observed[entry_id]["path"])
            self.assertGreaterEqual(len(observed[entry_id]["retirement_conditions"]), 2)
            self.assertTrue(observed[entry_id]["artifact_identity"])

    def test_active_b1_config_records_registry_runner_and_renderer_callers(self) -> None:
        entry = next(
            entry
            for entry in self._load_inventory()["entries"]
            if entry["id"] == "active.a_share_b1_long_history_config"
        )
        self.assertEqual("active_architecture", entry["classification"])
        callers = " ".join(entry["callers"])
        self.assertIn("strategy registry", callers)
        self.assertIn("runner", callers)
        self.assertIn("renderer", callers)
        self.assertIn("backend/app/quant_research/strategy_registry.py", entry["stable_references"])
        self.assertIn("scripts/research/render_a_share_b1_report.py", entry["stable_references"])

    def test_b1_renderer_records_existing_compatibility_metrics(self) -> None:
        entry = next(
            entry
            for entry in self._load_inventory()["entries"]
            if entry["id"] == "compat.render_a_share_b1"
        )
        self.assertIn("绩效统计兼容口径", entry["stable_behavior"])
        self.assertNotIn("不复制 runner 成交、成本或绩效公式", entry["stable_behavior"])

    def test_audit_cli_records_archived_command_callers_instead_of_ci_claim(self) -> None:
        entry = next(
            entry
            for entry in self._load_inventory()["entries"]
            if entry["id"] == "candidate.audit_cli"
        )
        self.assertIn("归档 handoff", " ".join(entry["callers"]))
        self.assertIn(
            "docs/archive/handoffs/agent-handoff-2026-07-19.md",
            entry["stable_references"],
        )
        self.assertNotIn(".github/workflows/ci.yml", entry["stable_references"])

    def test_cli_accepts_explicit_relative_inventory_path(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "scripts/research/verify_entrypoint_inventory.py",
                "configs/research-entrypoints-v1.json",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_accepts_valid_inventory_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            external = Path(tmp) / "inventory.json"
            external.write_bytes(INVENTORY_PATH.read_bytes())
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/research/verify_entrypoint_inventory.py",
                    str(external),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_ci_runs_inventory_verifier(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("python scripts/research/verify_entrypoint_inventory.py", workflow)

    def _load_inventory(self) -> dict[str, Any]:
        return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))

    def _verify_with_change(self, change) -> list[str]:
        inventory = self._load_inventory()
        change(inventory)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inventory.json"
            path.write_text(json.dumps(inventory, ensure_ascii=False), encoding="utf-8")
            return verify_inventory(path, REPO_ROOT)


if __name__ == "__main__":
    unittest.main()
