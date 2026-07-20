from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "configs" / "research" / "historical_publication_issues_v1.json"
CONTRACT_SCHEMA_VERSION = "historical-publication-issues/v1"
EXPECTED_REPOSITORY = "Jettlin927/Quantitative_trading"
REQUIRED_LABELS = frozenset({"类型:策略研究", "来源:历史导入"})


@dataclass(frozen=True)
class HistoricalPublicationIssue:
    strategy_id: str
    issue_number: int
    title: str


def resolve_historical_publication_issue(
    strategy_id: str,
    issue_number: int | None = None,
    *,
    contract_path: Path = CONTRACT_PATH,
) -> HistoricalPublicationIssue:
    mappings = _load_contract(contract_path)
    matches = [item for item in mappings if item.strategy_id == strategy_id]
    if len(matches) != 1:
        raise ValueError("策略不在冻结的历史研究 Issue 映射清单中")
    expected = matches[0]
    if issue_number is not None and issue_number != expected.issue_number:
        raise ValueError(
            f"历史研究冻结映射不一致：{strategy_id} 只允许 Issue #{expected.issue_number}"
        )
    return expected


def validate_historical_publication_issue_snapshot(
    issue: object,
    expected: HistoricalPublicationIssue,
    *,
    allow_published: bool = False,
) -> None:
    if not isinstance(issue, Mapping) or issue.get("number") != expected.issue_number:
        raise ValueError("GitHub 返回的 Issue 身份与冻结映射不一致")
    if "pull_request" in issue:
        raise ValueError("历史研究映射目标必须是 Issue，不能是 Pull Request")
    labels = {
        str(item.get("name") or "") if isinstance(item, Mapping) else str(item)
        for item in issue.get("labels", [])
    }
    state = str(issue.get("state") or "").lower()
    if state != "open" and not (allow_published and state == "closed"):
        raise ValueError("历史研究映射只允许 OPEN Issue 或已发布记录的补偿状态")
    if issue.get("title") != expected.title:
        raise ValueError("历史研究 Issue 标题与冻结映射不一致")
    missing = sorted(REQUIRED_LABELS - labels)
    if missing:
        raise ValueError("历史研究 Issue 缺少标签：" + "、".join(missing))


def _load_contract(path: Path) -> tuple[HistoricalPublicationIssue, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("历史研究 Issue 冻结映射清单无法读取") from exc
    expected_fields = {
        "schemaVersion",
        "repository",
        "mappings",
        "requiredLabels",
        "productionGate",
    }
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ValueError("历史研究 Issue 冻结映射清单字段无效")
    if (
        payload.get("schemaVersion") != CONTRACT_SCHEMA_VERSION
        or payload.get("repository") != EXPECTED_REPOSITORY
        or set(payload.get("requiredLabels") or []) != REQUIRED_LABELS
    ):
        raise ValueError("历史研究 Issue 冻结映射清单身份无效")
    raw_mappings = payload.get("mappings")
    if not isinstance(raw_mappings, list) or not raw_mappings:
        raise ValueError("历史研究 Issue 冻结映射清单不能为空")
    mappings: list[HistoricalPublicationIssue] = []
    for item in raw_mappings:
        if not isinstance(item, dict) or set(item) != {
            "strategyId",
            "issueNumber",
            "title",
        }:
            raise ValueError("历史研究 Issue 冻结映射条目无效")
        strategy = item["strategyId"]
        number = item["issueNumber"]
        title = item["title"]
        if (
            not isinstance(strategy, str)
            or not strategy
            or isinstance(number, bool)
            or not isinstance(number, int)
            or number <= 0
            or not isinstance(title, str)
            or not title
        ):
            raise ValueError("历史研究 Issue 冻结映射值无效")
        mappings.append(HistoricalPublicationIssue(strategy, number, title))
    if len({item.strategy_id for item in mappings}) != len(mappings) or len(
        {item.issue_number for item in mappings}
    ) != len(mappings):
        raise ValueError("历史研究 Issue 冻结映射必须严格一对一")
    return tuple(mappings)
