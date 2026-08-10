"""新增自动简报幂等状态与每日预算账本

Revision ID: 0022_automatic_briefings
Revises: 0021_instrument_states
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0022_automatic_briefings"
down_revision = "0021_instrument_states"
branch_labels = None
depends_on = None


def _money(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.Numeric(16, 8), nullable=nullable)


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
        "personal_automatic_briefing_daily_budgets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("market_date", sa.Date(), nullable=False),
        _money("reserved_cost_usd", nullable=False),
        _money("reserved_cost_cny", nullable=False),
        _money("settled_cost_usd", nullable=False),
        _money("settled_cost_cny", nullable=False),
        sa.Column(
            "target_notified",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
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
            "market_date",
            name="uq_personal_automatic_briefing_daily_budget",
        ),
        schema="private_workbench",
    )
    op.create_table(
        "personal_automatic_briefings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("trigger_key_hash", sa.String(length=64), nullable=False),
        sa.Column("market_date", sa.Date(), nullable=False),
        sa.Column("trigger_kind", sa.String(length=32), nullable=False),
        sa.Column("provider_state", sa.String(length=24), nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=True),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reservation_id", sa.String(length=36), nullable=True),
        _money("estimated_cost_usd"),
        _money("estimated_cost_cny"),
        _money("actual_cost_usd"),
        _money("actual_cost_cny"),
        _money("accounted_cost_usd", nullable=False),
        _money("accounted_cost_cny", nullable=False),
        _money("fx_cny_per_usd"),
        sa.Column("policy_revision", sa.String(length=64), nullable=True),
        _money("target_cny"),
        _money("soft_limit_cny"),
        _money("hard_limit_cny"),
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
            "trigger_key_hash",
            name="uq_personal_automatic_briefing_trigger",
        ),
        sa.UniqueConstraint(
            "reservation_id",
            name="uq_personal_automatic_briefing_reservation",
        ),
        schema="private_workbench",
    )
    for table_name in (
        "personal_automatic_briefings",
        "personal_automatic_briefing_daily_budgets",
    ):
        op.execute(
            f"REVOKE ALL ON TABLE private_workbench.{table_name} FROM PUBLIC"
        )


def downgrade() -> None:
    raise RuntimeError(
        "自动简报与预算账本属于持久资产，禁止自动降级；请使用受控恢复流程。"
    )
