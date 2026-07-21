"""preserve point-in-time financial revisions

Revision ID: 0011_pit_financial_revisions
Revises: 0010_research_issue_mapping
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_pit_financial_revisions"
down_revision = "0010_research_issue_mapping"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("stock_financial_indicators") as batch_op:
        batch_op.add_column(
            sa.Column("source_update_flag", sa.String(length=8), nullable=True)
        )
        batch_op.add_column(
            sa.Column("source_revision_sha256", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("source_observed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("available_from", sa.Date(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "revision_status",
                sa.String(length=24),
                server_default=sa.text("'legacy_unverified'"),
                nullable=False,
            )
        )
        batch_op.drop_constraint(
            "uq_stock_financial_indicator_period",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_stock_financial_indicator_revision",
            [
                "ts_code",
                "end_date",
                "ann_date",
                "source_revision_sha256",
            ],
        )
        batch_op.create_check_constraint(
            "ck_stock_financial_indicator_revision_evidence",
            "(revision_status = 'legacy_unverified' "
            "AND source_revision_sha256 IS NULL "
            "AND source_observed_at IS NULL "
            "AND available_from IS NULL) OR "
            "(revision_status = 'observed' "
            "AND source_revision_sha256 IS NOT NULL "
            "AND source_observed_at IS NOT NULL "
            "AND available_from IS NOT NULL)",
        )
        batch_op.create_index(
            "ix_stock_financial_indicators_available_from",
            ["available_from"],
            unique=False,
        )
        batch_op.create_index(
            "ix_stock_financial_indicators_revision_status",
            ["revision_status"],
            unique=False,
        )


def downgrade() -> None:
    raise RuntimeError(
        "财务修订版本属于不可覆盖的 point-in-time 证据，"
        "禁止自动降级；请使用受控前向迁移。"
    )
