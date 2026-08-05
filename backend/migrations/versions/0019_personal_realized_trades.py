"""add personal realized trades

Revision ID: 0019_personal_realized_trades
Revises: 0018_personal_equity_tracking
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0019_personal_realized_trades"
down_revision = "0018_personal_equity_tracking"
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

    op.create_table(
        "personal_realized_trades",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("portfolio_revision", sa.Integer(), nullable=False),
        sa.Column("symbol_hmac", sa.String(length=64), nullable=False),
        sa.Column(
            "sold_at", sa.DateTime(timezone=True), nullable=False
        ),
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
            "portfolio_revision",
            name="uq_personal_realized_trade_revision",
        ),
        schema="private_workbench",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.drop_table("personal_realized_trades", schema="private_workbench")
