#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from sqlalchemy import select


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.database import (  # noqa: E402
    SessionLocal,
    assert_schema_revision_at_head,
    engine,
)
from backend.app.models import (  # noqa: E402
    FormalResearch,
    FrozenResearchPlan,
    ResearchPublicationIssueMapping,
)
from backend.app.github_research import (  # noqa: E402
    HISTORICAL_RESEARCH_LABEL,
    GitHubIssueClient,
)


REQUIRED_ISSUE_LABELS = {"类型:策略研究", HISTORICAL_RESEARCH_LABEL}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "为一项历史导入研究登记独立 GitHub Issue；"
            "映射一旦写入不可修改。"
        )
    )
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--issue-number", required=True, type=int)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.issue_number <= 0:
            raise ValueError("issue-number 必须是正整数")
        assert_schema_revision_at_head(engine)
        issue = GitHubIssueClient.from_env().get_issue(args.issue_number)
        _validate_issue(issue, args.issue_number)
        with SessionLocal.begin() as db:
            formals = list(
                db.scalars(
                    select(FormalResearch)
                    .join(
                        FrozenResearchPlan,
                        FrozenResearchPlan.id == FormalResearch.plan_id,
                    )
                    .where(
                        FormalResearch.origin == "historical_import",
                        FrozenResearchPlan.strategy_id == args.strategy_id,
                    )
                ).all()
            )
            if len(formals) != 1:
                raise RuntimeError(
                    "策略必须唯一对应一项历史导入正式研究"
                )
            formal = formals[0]
            existing = db.get(ResearchPublicationIssueMapping, formal.id)
            if existing is None:
                db.add(
                    ResearchPublicationIssueMapping(
                        formal_research_id=formal.id,
                        issue_number=args.issue_number,
                    )
                )
                status = "created"
            elif existing.issue_number == args.issue_number:
                status = "unchanged"
            else:
                raise RuntimeError(
                    "该历史研究已映射其他 Issue，不可覆盖"
                )
        print(
            json.dumps(
                {
                    "status": status,
                    "strategyId": args.strategy_id,
                    "formalResearchId": formal.id,
                    "issueNumber": args.issue_number,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {"status": "failed", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        )
        return 3


def _validate_issue(issue: object, expected_number: int) -> None:
    if not isinstance(issue, dict) or issue.get("number") != expected_number:
        raise RuntimeError("GitHub 返回的 Issue 身份与待登记编号不一致")
    if "pull_request" in issue:
        raise RuntimeError("历史研究映射目标必须是 Issue，不能是 Pull Request")
    if str(issue.get("state") or "").lower() != "open":
        raise RuntimeError("历史研究映射只允许登记 OPEN Issue")
    labels = {
        str(item.get("name") or "") if isinstance(item, dict) else str(item)
        for item in issue.get("labels", [])
    }
    missing = sorted(REQUIRED_ISSUE_LABELS - labels)
    if missing:
        raise RuntimeError(
            "历史研究 Issue 缺少标签：" + "、".join(missing)
        )


if __name__ == "__main__":
    raise SystemExit(main())
