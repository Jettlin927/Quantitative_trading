from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


GLOBAL_HEAVY_WORK_CLAIM_LOCK = 782643910


def try_acquire_heavy_work_claim_lock(db: Session) -> bool:
    if db.get_bind().dialect.name != "postgresql":
        return True
    return bool(
        db.scalar(
            text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
            {"lock_id": GLOBAL_HEAVY_WORK_CLAIM_LOCK},
        )
    )
