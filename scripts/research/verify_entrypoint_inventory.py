#!/usr/bin/env python3
"""校验版本化研究入口清单；该清单不参与任何运行时分发。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY = REPO_ROOT / "configs" / "research-entrypoints-v1.json"
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


def verify_inventory(inventory_path: Path, repo_root: Path = REPO_ROOT) -> list[str]:
    """返回全部校验错误；空列表表示清单 schema、引用和分类完整。"""

    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"无法读取入口清单：{exc}"]

    if not isinstance(inventory, dict):
        return ["入口清单根节点必须是对象"]

    errors: list[str] = []
    if inventory.get("schema_version") != 1:
        errors.append("schema_version 必须为 1")
    entries = inventory.get("entries")
    if not isinstance(entries, list) or not entries:
        errors.append("entries 必须是非空数组")
        return errors

    seen_ids: set[str] = set()
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

        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            errors.append(f"{prefix} 的入口路径无效")
        else:
            resolved_path = _resolve_repo_path(repo_root, raw_path)
            if resolved_path is None or not resolved_path.exists():
                errors.append(f"{prefix} 入口路径不存在：{raw_path}")

        coverage = entry.get("coverage")
        if not _non_empty_strings(coverage):
            errors.append(f"{prefix} 的 coverage 必须是非空字符串数组")
        else:
            unknown = sorted(set(coverage) - REQUIRED_COVERAGE)
            if unknown:
                errors.append(f"{prefix} 包含未知 coverage：{', '.join(unknown)}")
            seen_coverage.update(set(coverage) & REQUIRED_COVERAGE)

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

    missing_classifications = sorted(CLASSIFICATIONS - seen_classifications)
    if missing_classifications:
        errors.append("分类不完整，缺少：" + ", ".join(missing_classifications))
    missing_coverage = sorted(REQUIRED_COVERAGE - seen_coverage)
    if missing_coverage:
        errors.append("入口覆盖不完整，缺少：" + ", ".join(missing_coverage))
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
    print(f"入口清单校验通过：{args.inventory.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
