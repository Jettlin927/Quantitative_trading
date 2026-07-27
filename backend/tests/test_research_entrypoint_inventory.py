from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.research.verify_entrypoint_inventory import verify_inventory


REPO_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = REPO_ROOT / "configs" / "research-entrypoints-v1.json"


class ResearchEntrypointInventoryTest(unittest.TestCase):
    def test_versioned_inventory_is_complete_and_valid(self) -> None:
        self.assertEqual([], verify_inventory(INVENTORY_PATH, REPO_ROOT))

    def test_verifier_reports_missing_entry_path(self) -> None:
        errors = self._verify_with_change(
            lambda inventory: inventory["entries"][0].update(path="missing/entry.py")
        )
        self.assertTrue(any("入口路径不存在" in error for error in errors), errors)

    def test_verifier_reports_duplicate_id(self) -> None:
        def duplicate(inventory: dict[str, object]) -> None:
            entries = inventory["entries"]
            entries[1]["id"] = entries[0]["id"]

        errors = self._verify_with_change(duplicate)
        self.assertTrue(any("入口 ID 重复" in error for error in errors), errors)

    def test_verifier_requires_candidate_retirement_conditions(self) -> None:
        def remove_conditions(inventory: dict[str, object]) -> None:
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

    def test_ci_runs_inventory_verifier(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("python scripts/research/verify_entrypoint_inventory.py", workflow)

    def _verify_with_change(self, change) -> list[str]:
        inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
        change(inventory)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inventory.json"
            path.write_text(json.dumps(inventory, ensure_ascii=False), encoding="utf-8")
            return verify_inventory(path, REPO_ROOT)


if __name__ == "__main__":
    unittest.main()
