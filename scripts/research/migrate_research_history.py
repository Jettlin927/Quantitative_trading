#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.orm import Session

from backend.app.database import engine
from backend.app.research_history_migration import (
    apply_history_migration,
    build_history_migration_plan,
    load_history_source,
    migration_report,
    render_migration_report_markdown,
)


DEFAULT_CONTRACT = REPO_ROOT / "configs" / "research-history-migration-v1.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="预览或演练统一研究历史迁移；默认不写数据库。"
    )
    parser.add_argument(
        "--mode",
        choices=("preview", "rollback", "apply"),
        default="preview",
        help="preview 只读；rollback 写入后回滚；apply 显式提交。",
    )
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument(
        "--confirm-migration-fingerprint",
        help="apply 必须提供预览报告中的完整迁移指纹。",
    )
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-markdown", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source = load_history_source(REPO_ROOT, args.contract)
    result = None
    committed = False
    with Session(engine) as db:
        plan = build_history_migration_plan(db, source)
        if args.mode == "apply":
            if args.confirm_migration_fingerprint != plan.migration_fingerprint:
                raise SystemExit(
                    "拒绝 apply：必须先运行 preview，并通过 "
                    "--confirm-migration-fingerprint 提供当前完整迁移指纹。"
                )
            result = apply_history_migration(db, plan)
            db.commit()
            committed = True
        elif args.mode == "rollback":
            result = apply_history_migration(db, plan)
            db.flush()
            db.rollback()

    report = migration_report(plan, result, mode=args.mode, committed=committed)
    markdown = render_migration_report_markdown(report)
    if args.output_json:
        _write(args.output_json, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if args.output_markdown:
        _write(args.output_markdown, markdown)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
