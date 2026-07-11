"""add data quality and snapshot registries

Revision ID: 0002_quality_snapshot_registry
Revises: 0001_existing_schema_baseline
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_quality_snapshot_registry"
down_revision = "0001_existing_schema_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_quality_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scope", sa.String(length=40), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("universe_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="running"),
        sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("code_commit", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('running', 'ready', 'ready_with_warnings', 'blocked', 'failed')",
            name="ck_data_quality_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_data_quality_runs_scope_started", "data_quality_runs", ["scope", "started_at"], unique=False)

    op.create_table(
        "data_quality_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("rule_id", sa.String(length=80), nullable=False),
        sa.Column("table_name", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("checked_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sample_issues", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.CheckConstraint("severity in ('blocker', 'warning', 'info')", name="ck_data_quality_results_severity"),
        sa.CheckConstraint("status in ('passed', 'warning', 'blocked', 'failed')", name="ck_data_quality_results_status"),
        sa.ForeignKeyConstraint(["run_id"], ["data_quality_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "rule_id", "table_name", name="uq_data_quality_result_run_rule_table"),
    )
    op.create_index("ix_data_quality_results_run_status", "data_quality_results", ["run_id", "status"], unique=False)

    op.create_table(
        "data_snapshots",
        sa.Column("snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("quality_run_id", sa.String(length=36), nullable=False),
        sa.Column("scope", sa.String(length=40), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("universe_hash", sa.String(length=64), nullable=False),
        sa.Column("artifact_root", sa.String(length=500), nullable=False),
        sa.Column("table_artifacts", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("row_counts", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("source_cutoff", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="building"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status in ('building', 'complete', 'failed')", name="ck_data_snapshots_status"),
        sa.ForeignKeyConstraint(["quality_run_id"], ["data_quality_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )
    op.create_index(
        "ix_data_snapshots_quality_run_created",
        "data_snapshots",
        ["quality_run_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_data_snapshots_quality_run_created", table_name="data_snapshots")
    op.drop_table("data_snapshots")
    op.drop_index("ix_data_quality_results_run_status", table_name="data_quality_results")
    op.drop_table("data_quality_results")
    op.drop_index("ix_data_quality_runs_scope_started", table_name="data_quality_runs")
    op.drop_table("data_quality_runs")
