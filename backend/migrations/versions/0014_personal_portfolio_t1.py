"""add personal portfolio revisions and purge audit

Revision ID: 0014_personal_portfolio_t1
Revises: 0013_personal_workspace_t0
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0014_personal_portfolio_t1"
down_revision = "0013_personal_workspace_t0"
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

    op.add_column(
        "personal_holdings",
        sa.Column(
            "synthetic",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        schema="private_workbench",
    )
    op.alter_column(
        "personal_holdings",
        "synthetic",
        server_default=sa.false(),
        schema="private_workbench",
    )

    op.create_table(
        "personal_portfolio_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("holding_id", sa.String(length=36), nullable=True),
        sa.Column("portfolio_revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("idempotency_hash", sa.String(length=64), nullable=False),
        *_envelope_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["private_workbench.personal_workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "portfolio_revision",
            name="uq_personal_portfolio_revision",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_hash",
            name="uq_personal_portfolio_idempotency",
        ),
        schema="private_workbench",
    )
    op.create_table(
        "personal_audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("object_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("idempotency_hash", sa.String(length=64), nullable=False),
        sa.Column("portfolio_revision", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("backup_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["private_workbench.personal_workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_hash",
            name="uq_personal_audit_idempotency",
        ),
        schema="private_workbench",
    )
    op.execute(
        "REVOKE ALL ON TABLE private_workbench.personal_portfolio_revisions FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL ON TABLE private_workbench.personal_audit_events FROM PUBLIC"
    )


def downgrade() -> None:
    raise RuntimeError("private_workbench 是持久资产，禁止自动降级；请使用受控恢复流程。")
