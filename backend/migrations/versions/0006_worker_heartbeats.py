"""add sync worker heartbeats

Revision ID: 0006_worker_heartbeats
Revises: 0005_job_leases
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_worker_heartbeats"
down_revision = "0005_job_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_worker_heartbeats",
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="starting"),
        sa.Column("current_job_id", sa.String(length=36), nullable=True),
        sa.Column("process_started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("code_commit", sa.String(length=64), nullable=False, server_default="unknown"),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.PrimaryKeyConstraint("worker_id"),
    )
    op.create_index(
        "ix_sync_worker_heartbeats_status_heartbeat",
        "sync_worker_heartbeats",
        ["status", "heartbeat_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_sync_worker_heartbeats_status_heartbeat", table_name="sync_worker_heartbeats")
    op.drop_table("sync_worker_heartbeats")
