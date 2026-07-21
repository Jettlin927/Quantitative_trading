"""add isolated experimental US daily market data

Revision ID: 0012_us_experiment_daily
Revises: 0011_pit_financial_revisions
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012_us_experiment_daily"
down_revision = "0011_pit_financial_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "us_experiment_instruments",
        sa.Column("source_code", sa.String(length=40), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("yahoo_symbol", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("market_code", sa.String(length=3), nullable=False),
        sa.Column("market_name", sa.String(length=24), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("history_start_date", sa.Date(), nullable=True),
        sa.Column("history_end_date", sa.Date(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(length=24), nullable=True),
        sa.Column("last_sync_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("source_code"),
    )
    for column in ("symbol", "yahoo_symbol", "market_code", "is_current", "last_sync_status"):
        op.create_index(f"ix_us_experiment_instruments_{column}", "us_experiment_instruments", [column])

    op.create_table(
        "us_experiment_daily_bars",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_code", sa.String(length=40), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("high", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("low", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("close", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("adj_close", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("cash_dividend", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("split_ratio", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("source", sa.String(length=24), server_default="yfinance", nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["source_code"], ["us_experiment_instruments.source_code"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_code", "trade_date", name="uq_us_experiment_daily_bar_code_date"),
    )
    op.create_index("ix_us_experiment_daily_bars_source_code", "us_experiment_daily_bars", ["source_code"])
    op.create_index("ix_us_experiment_daily_bars_trade_date", "us_experiment_daily_bars", ["trade_date"])
    op.create_index("ix_us_experiment_daily_bars_date_code", "us_experiment_daily_bars", ["trade_date", "source_code"])

    op.create_table(
        "us_experiment_daily_checks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_code", sa.String(length=40), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("yfinance_open", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("yfinance_high", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("yfinance_low", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("yfinance_close", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("yfinance_volume", sa.BigInteger(), nullable=True),
        sa.Column("akshare_open", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("akshare_high", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("akshare_low", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("akshare_close", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("akshare_volume", sa.BigInteger(), nullable=True),
        sa.Column("max_price_relative_diff", sa.Numeric(precision=18, scale=10), nullable=True),
        sa.Column("volume_relative_diff", sa.Numeric(precision=18, scale=10), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["source_code"], ["us_experiment_instruments.source_code"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_code", "trade_date", name="uq_us_experiment_daily_check_code_date"),
    )
    op.create_index("ix_us_experiment_daily_checks_source_code", "us_experiment_daily_checks", ["source_code"])
    op.create_index("ix_us_experiment_daily_checks_trade_date", "us_experiment_daily_checks", ["trade_date"])
    op.create_index("ix_us_experiment_daily_checks_status", "us_experiment_daily_checks", ["status"])
    op.create_index("ix_us_experiment_daily_checks_status_date", "us_experiment_daily_checks", ["status", "trade_date"])


def downgrade() -> None:
    op.drop_table("us_experiment_daily_checks")
    op.drop_table("us_experiment_daily_bars")
    op.drop_table("us_experiment_instruments")
