from __future__ import annotations

import re
import unittest
from pathlib import Path
from urllib.parse import unquote


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = REPO_ROOT / "docs"
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def markdown_without_fenced_code(text: str) -> str:
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    return "\n".join(lines)


class DocumentationContractTest(unittest.TestCase):
    def test_required_module_boundaries_exist(self) -> None:
        required_directories = [
            "product",
            "architecture",
            "data/a-share",
            "data/us",
            "research/contracts",
            "research/guides",
            "operations",
            "acceptance",
            "adr",
            "agents",
            "archive",
        ]
        for relative_path in required_directories:
            with self.subTest(relative_path=relative_path):
                path = DOCS_ROOT / relative_path
                self.assertTrue(path.is_dir(), f"缺少文档职责目录：{relative_path}")
                self.assertTrue((path / "README.md").is_file(), f"缺少模块入口：{relative_path}/README.md")

    def test_stable_contracts_and_archives_have_canonical_paths(self) -> None:
        required_files = [
            "architecture/code-map.md",
            "research/contracts/strategy-evaluation-standard.md",
            "research/contracts/quant-foundation-trust-contract.md",
            "operations/cicd.md",
            "acceptance/2026-07-11-production-migration-approval.md",
            "acceptance/2026-07-12-production-trustworthiness-acceptance.md",
            "archive/handoffs/agent-handoff-2026-07-19.md",
            "archive/code-maps/agent-code-map-2026-07-19.md",
        ]
        for relative_path in required_files:
            with self.subTest(relative_path=relative_path):
                self.assertTrue((DOCS_ROOT / relative_path).is_file(), f"缺少规范文件：{relative_path}")

    def test_readme_is_a_stable_human_entry(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("## 产品边界", readme)
        self.assertIn("## 快速开始", readme)
        self.assertIn("## 文档导航", readme)
        self.assertNotIn("## 当前服务器部署", readme)
        self.assertNotIn("## 主要 API", readme)
        self.assertNotIn("## 数据表", readme)
        self.assertIsNone(re.search(r"\b[0-9a-f]{40}\b", readme), "README 不得冻结易漂移提交哈希")

    def test_document_index_routes_every_module(self) -> None:
        index = (DOCS_ROOT / "index.md").read_text(encoding="utf-8")
        for target in [
            "product/",
            "architecture/",
            "data/a-share/",
            "data/us/",
            "research/contracts/",
            "research/guides/",
            "operations/",
            "acceptance/",
            "adr/",
            "agents/",
            "archive/",
        ]:
            with self.subTest(target=target):
                self.assertIn(f"]({target})", index)

    def test_strategy_result_path_remains_compatible(self) -> None:
        results_root = DOCS_ROOT / "research" / "strategy-results"
        self.assertTrue((results_root / "manifest.json").is_file())
        implementation = (REPO_ROOT / "backend" / "app" / "strategy_results.py").read_text(encoding="utf-8")
        self.assertIn('repo_root / "docs" / "research" / "strategy-results"', implementation)

    def test_strategy_conclusion_terms_match_the_domain_contract(self) -> None:
        standard = (
            DOCS_ROOT / "research" / "contracts" / "strategy-evaluation-standard.md"
        ).read_text(encoding="utf-8")
        for conclusion in ("研究通过", "有条件候选", "证据不足", "受阻", "不通过"):
            with self.subTest(conclusion=conclusion):
                self.assertIn(f"| `{conclusion}` |", standard)
        self.assertNotIn("| `blocked` |", standard)

    def test_all_local_markdown_links_resolve(self) -> None:
        markdown_files = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "AGENTS.md",
            REPO_ROOT / "CONTEXT.md",
            *sorted(DOCS_ROOT.rglob("*.md")),
        ]
        broken: list[str] = []
        for path in markdown_files:
            text = markdown_without_fenced_code(path.read_text(encoding="utf-8"))
            for raw_target in MARKDOWN_LINK.findall(text):
                target = raw_target.strip().strip("<>").split(maxsplit=1)[0]
                if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                    continue
                target = unquote(target.split("#", 1)[0].split("?", 1)[0])
                if not target:
                    continue
                resolved = (path.parent / target).resolve()
                if not resolved.exists():
                    broken.append(f"{path.relative_to(REPO_ROOT)} -> {raw_target}")
        self.assertEqual([], broken, "发现断开的本地 Markdown 链接：\n" + "\n".join(broken))


if __name__ == "__main__":
    unittest.main()
