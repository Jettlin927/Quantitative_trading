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
            "operations/production-deployment-and-home-access.md",
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

    def test_remote_access_decision_stays_ssh_only(self) -> None:
        decision = (
            DOCS_ROOT / "operations" / "private-https-authentication-decision.md"
        ).read_text(encoding="utf-8")
        agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("SSH 隧道是研究系统唯一的远程访问入口", decision)
        self.assertIn("不购买或申请域名", decision)
        self.assertIn("不开放公网 IP 端口", agents)
        self.assertIn("除非用户明确变更决定", agents)
        self.assertNotIn("采用 **Cloudflare Tunnel + Cloudflare Access**", decision)

    def test_production_deployment_handoff_keeps_live_gates(self) -> None:
        handoff_path = (
            DOCS_ROOT / "operations" / "production-deployment-and-home-access.md"
        )
        handoff = handoff_path.read_text(encoding="utf-8")
        agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("本文是稳定操作合同", handoff)
        self.assertIn("不构成生产授权", handoff)
        self.assertIn("不得复制运行中的 PostgreSQL volume", handoff)
        self.assertIn("`quant-trading-prod` 是唯一生产服务器和唯一数据权威", handoff)
        self.assertIn("原旧服务器已经退出本系统", handoff)
        self.assertNotIn("旧服务器保持完整", handoff)
        self.assertIn("macOS、Linux 与 Windows 电脑可以同时建立独立隧道", handoff)
        self.assertIn("127.0.0.1:25173", handoff)
        self.assertIn("SSH 隧道只提供连接", handoff)
        self.assertIn(str(handoff_path.relative_to(REPO_ROOT)), agents)
        self.assertIn("`quant-trading-prod` 是唯一生产服务器和唯一数据权威", agents)

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

    def test_historical_strategy_results_remain_archived(self) -> None:
        results_root = DOCS_ROOT / "research" / "strategy-results"
        self.assertTrue((results_root / "manifest.json").is_file())

    def test_strategy_conclusion_terms_match_the_domain_contract(self) -> None:
        standard = (
            DOCS_ROOT / "research" / "contracts" / "strategy-evaluation-standard.md"
        ).read_text(encoding="utf-8")
        for conclusion in ("研究通过", "有条件候选", "证据不足", "受阻", "不通过"):
            with self.subTest(conclusion=conclusion):
                self.assertIn(f"| `{conclusion}` |", standard)
        self.assertNotIn("| `blocked` |", standard)

    def test_global_stock_data_skill_preserves_repository_research_boundary(self) -> None:
        skill = (REPO_ROOT / ".codex" / "skills" / "global-stock-data" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("researchEligible=false", skill)
        self.assertIn("不得输出买入、卖出、持有、评级或实盘指令", skill)
        self.assertIn("分析师评级与目标价只能作为带来源的市场观察", skill)

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
