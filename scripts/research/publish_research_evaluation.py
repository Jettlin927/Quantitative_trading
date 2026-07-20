#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.database import (  # noqa: E402
    SessionLocal,
    assert_schema_revision_at_head,
    engine,
)
from backend.app.research_publication import (  # noqa: E402
    parse_evaluation_contract,
    prepare_research_evaluation,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="提交并冻结研究评价合同；外部一致发布由 research-worker 独占执行。"
    )
    parser.add_argument("--contract", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        assert_schema_revision_at_head(engine)
        payload = json.loads(args.contract.read_text(encoding="utf-8"))
        formal_research_id, draft = parse_evaluation_contract(payload)
        projection = prepare_research_evaluation(
            SessionLocal,
            formal_research_id=formal_research_id,
            draft=draft,
        )
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {"status": "failed", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        )
        return 3
    print(
        json.dumps(
            {
                "status": projection.status,
                "publicationId": str(projection.publication_id),
                "evaluationId": str(projection.evaluation_id),
                "evaluationSha256": projection.evaluation_sha256,
                "conclusion": projection.conclusion,
                "message": (
                    "评价已冻结；research-worker 将串行完成工件、GitHub 与前端一致发布"
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
