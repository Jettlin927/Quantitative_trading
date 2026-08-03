"""add isolated synthetic personal workspace trust slice

Revision ID: 0013_personal_workspace_t0
Revises: 0012_us_experiment_daily
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0013_personal_workspace_t0"
down_revision = "0012_us_experiment_daily"
branch_labels = None
depends_on = None


def _envelope_columns() -> list[sa.Column]:
    return [
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("payload_schema", sa.String(length=32), nullable=False),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("CREATE SCHEMA IF NOT EXISTS private_workbench")
    op.execute("REVOKE ALL ON SCHEMA private_workbench FROM PUBLIC")

    op.create_table(
        "personal_workspaces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("actor_identity_hash", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        *_envelope_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("actor_identity_hash", name="uq_personal_workspace_actor"),
        schema="private_workbench",
    )
    op.create_table(
        "personal_holdings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("symbol_hmac", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        *_envelope_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["private_workbench.personal_workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "symbol_hmac", name="uq_personal_holding_workspace_symbol"),
        schema="private_workbench",
    )
    op.create_table(
        "personal_rule_evaluations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("result_summary", sa.String(length=64), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        *_envelope_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["private_workbench.personal_workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="private_workbench",
    )
    op.create_table(
        "personal_analysis_drafts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("preview_sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_hash", sa.String(length=64), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        *_envelope_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["private_workbench.personal_workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "idempotency_hash", name="uq_personal_analysis_idempotency"),
        schema="private_workbench",
    )
    op.create_table(
        "personal_research_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("analysis_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("idempotency_hash", sa.String(length=64), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        *_envelope_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["private_workbench.personal_workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["private_workbench.personal_analysis_drafts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "idempotency_hash", name="uq_personal_record_idempotency"),
        schema="private_workbench",
    )

    for table_name in (
        "personal_workspaces",
        "personal_holdings",
        "personal_rule_evaluations",
        "personal_analysis_drafts",
        "personal_research_records",
    ):
        op.execute(f"REVOKE ALL ON TABLE private_workbench.{table_name} FROM PUBLIC")


def downgrade() -> None:
    raise RuntimeError("private_workbench 是持久资产，禁止自动降级；请使用受控恢复流程。")
