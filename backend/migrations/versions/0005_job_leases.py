"""add durable sync job leases and retries

Revision ID: 0005_job_leases
Revises: 0004_research_runs
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_job_leases"
down_revision = "0004_research_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("data_sync_jobs", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "data_sync_jobs",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "data_sync_jobs",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "data_sync_jobs",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column("data_sync_jobs", sa.Column("lease_owner", sa.String(length=128), nullable=True))
    op.add_column("data_sync_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("data_sync_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("data_sync_jobs", sa.Column("last_error", sa.String(length=1000), nullable=True))
    op.add_column(
        "data_sync_jobs",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_data_sync_jobs_claim",
        "data_sync_jobs",
        ["status", "next_attempt_at", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_data_sync_jobs_lease_expiry",
        "data_sync_jobs",
        ["status", "lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_data_sync_jobs_lease_expiry", table_name="data_sync_jobs")
    op.drop_index("ix_data_sync_jobs_claim", table_name="data_sync_jobs")
    op.drop_column("data_sync_jobs", "updated_at")
    op.drop_column("data_sync_jobs", "last_error")
    op.drop_column("data_sync_jobs", "heartbeat_at")
    op.drop_column("data_sync_jobs", "lease_expires_at")
    op.drop_column("data_sync_jobs", "lease_owner")
    op.drop_column("data_sync_jobs", "next_attempt_at")
    op.drop_column("data_sync_jobs", "max_attempts")
    op.drop_column("data_sync_jobs", "attempt_count")
    op.drop_column("data_sync_jobs", "last_attempt_at")
