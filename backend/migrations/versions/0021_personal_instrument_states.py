"""新增自选与 AI 候选状态

Revision ID: 0021_instrument_states
Revises: 0020_drop_data_quality_registry
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0021_instrument_states"
down_revision = "0020_drop_data_quality_registry"
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
        "personal_workspaces",
        sa.Column(
            "instrument_revision",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        schema="private_workbench",
    )
    op.create_table(
        "personal_instrument_states",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("symbol_hmac", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        *_envelope_columns(),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["private_workbench.personal_workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "symbol_hmac",
            name="uq_personal_instrument_state_symbol",
        ),
        schema="private_workbench",
    )
    op.create_table(
        "personal_instrument_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("instrument_revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("idempotency_hash", sa.String(length=64), nullable=False),
        *_envelope_columns(),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["private_workbench.personal_workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "instrument_revision",
            name="uq_personal_instrument_revision",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_hash",
            name="uq_personal_instrument_idempotency",
        ),
        schema="private_workbench",
    )
    for table_name in (
        "personal_instrument_states",
        "personal_instrument_revisions",
    ):
        op.execute(
            f"REVOKE ALL ON TABLE private_workbench.{table_name} FROM PUBLIC"
        )


def downgrade() -> None:
    raise RuntimeError(
        "自选与候选状态属于持久资产，禁止自动降级；请使用受控恢复流程。"
    )
