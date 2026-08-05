"""add personal price observations and equity snapshots

Revision ID: 0018_personal_equity_tracking
Revises: 0017_personal_notebook_t4
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0018_personal_equity_tracking"
down_revision = "0017_personal_notebook_t4"
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
        "personal_price_observations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("symbol_hmac", sa.String(length=64), nullable=False),
        sa.Column(
            "observed_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        *_envelope_columns(),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["private_workbench.personal_workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "symbol_hmac",
            name="uq_personal_price_observation_symbol",
        ),
        schema="private_workbench",
    )
    op.create_table(
        "personal_equity_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("market_day", sa.Date(), nullable=False),
        sa.Column("total_equity", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column(
            "total_market_value", sa.Numeric(precision=20, scale=4), nullable=False
        ),
        sa.Column("usd_cash", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("holdings_count", sa.Integer(), nullable=False),
        sa.Column("priced_count", sa.Integer(), nullable=False),
        sa.Column("after_close", sa.Boolean(), nullable=False),
        sa.Column(
            "observed_at", sa.DateTime(timezone=True), nullable=False
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
            "workspace_id", "market_day", name="uq_personal_equity_snapshot_day"
        ),
        schema="private_workbench",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.drop_table("personal_equity_snapshots", schema="private_workbench")
    op.drop_table("personal_price_observations", schema="private_workbench")
