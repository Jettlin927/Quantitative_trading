"""add reproducible research run registry

Revision ID: 0004_research_runs
Revises: 0003_drop_duplicate_indexes
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_research_runs"
down_revision = "0003_drop_duplicate_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_runs",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("reproducibility_key", sa.String(length=64), nullable=True),
        sa.Column("strategy_id", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("config_sha256", sa.String(length=64), nullable=False),
        sa.Column("data_snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("code_commit", sa.String(length=64), nullable=False),
        sa.Column("environment_sha256", sa.String(length=64), nullable=False),
        sa.Column("random_seed", sa.Integer(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("result_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("artifact_root", sa.String(length=500), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(length=2000), nullable=True),
        sa.CheckConstraint(
            "status in ('running', 'succeeded', 'failed', 'interrupted')",
            name="ck_research_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["data_snapshot_id"],
            ["data_snapshots.snapshot_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(
        "ix_research_runs_strategy_started",
        "research_runs",
        ["strategy_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_research_runs_reproducibility",
        "research_runs",
        ["reproducibility_key"],
        unique=False,
    )


def downgrade() -> None:
    raise RuntimeError("research_runs 含审计证据，禁止自动降级删除；请使用受控前向迁移。")
