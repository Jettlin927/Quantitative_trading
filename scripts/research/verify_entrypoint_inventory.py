#!/usr/bin/env python3
"""校验版本化研究入口清单；该清单不参与任何运行时分发。"""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, cast


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY = REPO_ROOT / "configs" / "research-entrypoints-v1.json"
EXPECTED_INVENTORY_ID = "research-entrypoints-v1"
EXPECTED_PURPOSE = (
    "CI-only research entrypoint inventory; never import this file for runtime dispatch."
)
CLASSIFICATIONS = {
    "active_architecture",
    "compatibility_entry",
    "historical_evidence",
    "legacy_executable_candidate",
}
REQUIRED_COVERAGE = {
    "registry",
    "cli",
    "renderer",
    "migration",
    "ma",
    "value_sector",
    "sample",
    "report",
    "config",
}
REQUIRED_ENTRY_FIELDS = {
    "id",
    "path",
    "classification",
    "coverage",
    "callers",
    "stable_behavior",
    "artifact_identity",
    "test_seams",
    "stable_references",
    "retirement_conditions",
}
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
EXPECTED_ENTRY_CLASSIFICATIONS = {
    "active.strategy_registry": "active_architecture",
    "active.research_cli": "active_architecture",
    "active.data_quality_cli": "active_architecture",
    "active.reproduction_cli": "active_architecture",
    "active.publication_cli": "active_architecture",
    "active.research_plan_cli": "active_architecture",
    "active.research_worker": "active_architecture",
    "compat.history_issue_mapping_cli": "compatibility_entry",
    "candidate.audit_cli": "legacy_executable_candidate",
    "compat.render_etf_volatility_managed": "compatibility_entry",
    "compat.render_etf_trend_120d": "compatibility_entry",
    "compat.render_a_share_b1": "compatibility_entry",
    "compat.history_migration_cli": "compatibility_entry",
    "candidate.ma_executable": "legacy_executable_candidate",
    "candidate.value_sector_executable": "legacy_executable_candidate",
    "active.inventory_verifier": "active_architecture",
    "active.sample_snapshot": "active_architecture",
    "compat.strategy_results_projection": "compatibility_entry",
    "historical.published_reports": "historical_evidence",
    "active.a_share_b1_long_history_config": "active_architecture",
    "historical.us_trade_migration_inventory": "historical_evidence",
}
EXPECTED_ENTRY_PATHS = {
    "active.strategy_registry": "backend/app/quant_research/strategy_registry.py",
    "active.research_cli": "scripts/research/run_quant_research.py",
    "active.data_quality_cli": "scripts/research/check_data_quality.py",
    "active.reproduction_cli": "scripts/research/reproduce_quant_research.py",
    "active.publication_cli": "scripts/research/publish_research_evaluation.py",
    "active.research_plan_cli": "backend/app/research_plan.py",
    "active.research_worker": "backend/app/research_worker.py",
    "compat.history_issue_mapping_cli": "scripts/research/register_historical_issue_mapping.py",
    "candidate.audit_cli": "scripts/research/audit_quant_research.py",
    "compat.render_etf_volatility_managed": "scripts/research/render_etf_volatility_managed_report.py",
    "compat.render_etf_trend_120d": "scripts/research/render_etf_trend_120d_report.py",
    "compat.render_a_share_b1": "scripts/research/render_a_share_b1_report.py",
    "compat.history_migration_cli": "scripts/research/migrate_research_history.py",
    "candidate.ma_executable": "scripts/research/run_ma_strategy_stats.py",
    "candidate.value_sector_executable": "scripts/research/run_value_sector_strategy.py",
    "active.inventory_verifier": "scripts/research/verify_entrypoint_inventory.py",
    "active.sample_snapshot": "my_quant/us_research/scripts/refresh_us_snapshot.py",
    "compat.strategy_results_projection": "backend/app/strategy_results.py",
    "historical.published_reports": "docs/research/strategy-results",
    "active.a_share_b1_long_history_config": "configs/research/a_share_b1_long_history.json",
    "historical.us_trade_migration_inventory": "docs/research/us-trade-migration-inventory-2026-07-22.md",
}
MANAGED_EXECUTABLE_ROOTS = (
    "scripts/research",
    "my_quant/us_research/scripts",
    "backend/app",
)
OUT_OF_SCOPE_DATA_EXECUTABLE_PATHS = {
    "backend/app/database.py",
    "backend/app/personal_analysis_worker.py",
    "backend/app/sync_worker.py",
}
EXCLUDED_DISCOVERY_DIRECTORIES = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "vendor",
    "vendors",
    "venv",
}


def _non_empty_strings(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, str) and bool(item.strip()) for item in value
    )


def _resolve_repo_path(repo_root: Path, raw_path: str) -> Path | None:
    path = Path(raw_path)
    if path.is_absolute() or ".." in path.parts:
        return None
    resolved = (repo_root / path).resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError:
        return None
    return resolved


def _is_python_executable(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return False
    for node in tree.body:
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        comparison = node.test
        if len(comparison.ops) != 1 or not isinstance(comparison.ops[0], ast.Eq):
            continue
        if len(comparison.comparators) != 1:
            continue
        left = comparison.left
        right = comparison.comparators[0]
        name_on_left = (
            isinstance(left, ast.Name)
            and left.id == "__name__"
            and isinstance(right, ast.Constant)
            and right.value == "__main__"
        )
        name_on_right = (
            isinstance(right, ast.Name)
            and right.id == "__name__"
            and isinstance(left, ast.Constant)
            and left.value == "__main__"
        )
        if name_on_left or name_on_right:
            return True
    return False


def _verify_managed_executables(
    registered_paths: set[str],
    repo_root: Path,
    managed_roots: tuple[str, ...] = MANAGED_EXECUTABLE_ROOTS,
) -> list[str]:
    discovered: set[str] = set()
    resolved_repo_root = repo_root.resolve()
    for raw_root in managed_roots:
        root = _resolve_repo_path(repo_root, raw_root)
        if root is None or not root.is_dir():
            continue
        for current_root, directory_names, file_names in os.walk(
            root, topdown=True, followlinks=False
        ):
            current_path = Path(current_root)
            directory_names[:] = sorted(
                name
                for name in directory_names
                if name not in EXCLUDED_DISCOVERY_DIRECTORIES
                and not (current_path / name).is_symlink()
            )
            for file_name in sorted(file_names):
                if not file_name.endswith(".py"):
                    continue
                path = current_path / file_name
                if path.is_symlink() or not path.is_file():
                    continue
                relative_path = path.relative_to(resolved_repo_root).as_posix()
                if relative_path in OUT_OF_SCOPE_DATA_EXECUTABLE_PATHS:
                    continue
                if _is_python_executable(path):
                    discovered.add(relative_path)
    return [
        f"发现未登记的受管 executable：{path}"
        for path in sorted(discovered - registered_paths)
    ]


def _display_path(path: Path, repo_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def verify_inventory(inventory_path: Path, repo_root: Path = REPO_ROOT) -> list[str]:
    """返回全部校验错误；空列表表示清单 schema、引用和分类完整。"""

    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"无法读取入口清单：{exc}"]

    if not isinstance(inventory, dict):
        return ["入口清单根节点必须是对象"]

    errors: list[str] = []
    schema_version = inventory.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        errors.append("schema_version 必须为整数 1")
    if inventory.get("inventory_id") != EXPECTED_INVENTORY_ID:
        errors.append(f"inventory_id 必须为 {EXPECTED_INVENTORY_ID}")
    if inventory.get("purpose") != EXPECTED_PURPOSE:
        errors.append(f"purpose 必须为固定 CI-only 声明：{EXPECTED_PURPOSE}")
    entries = inventory.get("entries")
    if not isinstance(entries, list) or not entries:
        errors.append("entries 必须是非空数组")
        return errors

    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    seen_classifications: set[str] = set()
    seen_coverage: set[str] = set()

    for index, entry in enumerate(entries):
        prefix = f"entries[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix} 必须是对象")
            continue

        missing_fields = sorted(REQUIRED_ENTRY_FIELDS - set(entry))
        if missing_fields:
            errors.append(f"{prefix} 缺少字段：{', '.join(missing_fields)}")

        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not ID_PATTERN.fullmatch(entry_id):
            errors.append(f"{prefix} 的入口 ID 格式无效")
        elif entry_id in seen_ids:
            errors.append(f"入口 ID 重复：{entry_id}")
        else:
            seen_ids.add(entry_id)

        classification = entry.get("classification")
        if classification not in CLASSIFICATIONS:
            errors.append(f"{prefix} 的分类无效：{classification!r}")
        else:
            seen_classifications.add(classification)
        expected_classification = (
            EXPECTED_ENTRY_CLASSIFICATIONS.get(entry_id)
            if isinstance(entry_id, str)
            else None
        )
        if (
            expected_classification is not None
            and classification != expected_classification
        ):
            errors.append(
                "预期入口分类不匹配："
                f"{entry_id} 应为 {expected_classification}，实际为 {classification}"
            )

        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            errors.append(f"{prefix} 的入口路径无效")
        else:
            resolved_path = _resolve_repo_path(repo_root, raw_path)
            if resolved_path is None or not resolved_path.exists():
                errors.append(f"{prefix} 入口路径不存在：{raw_path}")
            if raw_path in seen_paths:
                errors.append(f"入口路径重复：{raw_path}")
            else:
                seen_paths.add(raw_path)
            expected_path = (
                EXPECTED_ENTRY_PATHS.get(entry_id)
                if isinstance(entry_id, str)
                else None
            )
            if expected_path is not None and raw_path != expected_path:
                errors.append(
                    f"预期入口路径不匹配：{entry_id} 应为 {expected_path}，实际为 {raw_path}"
                )

        coverage = entry.get("coverage")
        if not _non_empty_strings(coverage):
            errors.append(f"{prefix} 的 coverage 必须是非空字符串数组")
        else:
            coverage_values = set(cast(list[str], coverage))
            unknown = sorted(coverage_values - REQUIRED_COVERAGE)
            if unknown:
                errors.append(f"{prefix} 包含未知 coverage：{', '.join(unknown)}")
            seen_coverage.update(coverage_values & REQUIRED_COVERAGE)

        for field in ("callers", "artifact_identity", "test_seams", "stable_references"):
            if not _non_empty_strings(entry.get(field)):
                errors.append(f"{prefix} 的 {field} 必须是非空字符串数组")

        stable_behavior = entry.get("stable_behavior")
        if not isinstance(stable_behavior, str) or not stable_behavior.strip():
            errors.append(f"{prefix} 的 stable_behavior 必须是非空字符串")

        retirement_conditions = entry.get("retirement_conditions")
        if classification == "legacy_executable_candidate" and not _non_empty_strings(
            retirement_conditions
        ):
            errors.append(f"{prefix} 候选入口缺少退役条件")
        elif not _non_empty_strings(retirement_conditions):
            errors.append(f"{prefix} 的 retirement_conditions 必须是非空字符串数组")

        for field, label in (("test_seams", "测试 seam"), ("stable_references", "稳定引用")):
            references = entry.get(field)
            if not isinstance(references, list):
                continue
            for reference in references:
                if not isinstance(reference, str):
                    continue
                resolved_reference = _resolve_repo_path(repo_root, reference)
                if resolved_reference is None or not resolved_reference.exists():
                    errors.append(f"{prefix} {label}不存在：{reference}")

    expected_entry_ids = set(EXPECTED_ENTRY_PATHS)
    contract_table_drift = sorted(
        expected_entry_ids.symmetric_difference(EXPECTED_ENTRY_CLASSIFICATIONS)
    )
    if contract_table_drift:
        errors.append("版本化入口合同表不一致：" + ", ".join(contract_table_drift))
    unexpected_entry_ids = sorted(seen_ids - expected_entry_ids)
    if unexpected_entry_ids:
        errors.append("入口 ID 未列入版本化入口合同：" + ", ".join(unexpected_entry_ids))
    missing_entry_ids = sorted(expected_entry_ids - seen_ids)
    if missing_entry_ids:
        errors.append("缺少预期入口 ID：" + ", ".join(missing_entry_ids))
    missing_classifications = sorted(CLASSIFICATIONS - seen_classifications)
    if missing_classifications:
        errors.append("分类不完整，缺少：" + ", ".join(missing_classifications))
    missing_coverage = sorted(REQUIRED_COVERAGE - seen_coverage)
    if missing_coverage:
        errors.append("入口覆盖不完整，缺少：" + ", ".join(missing_coverage))
    errors.extend(_verify_managed_executables(seen_paths, repo_root))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验版本化研究入口清单")
    parser.add_argument("inventory", nargs="?", type=Path, default=DEFAULT_INVENTORY)
    args = parser.parse_args(argv)
    errors = verify_inventory(args.inventory, REPO_ROOT)
    if errors:
        for error in errors:
            print(f"错误：{error}", file=sys.stderr)
        return 1
    print(f"入口清单校验通过：{_display_path(args.inventory, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
