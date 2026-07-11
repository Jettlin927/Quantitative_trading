"""remove verified duplicate non-unique indexes

Revision ID: 0003_drop_duplicate_indexes
Revises: 0002_quality_snapshot_registry
"""

from __future__ import annotations

from alembic import op


revision = "0003_drop_duplicate_indexes"
down_revision = "0002_quality_snapshot_registry"
branch_labels = None
depends_on = None


DUPLICATE_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("stock_limit_prices", "ix_stock_limit_prices_code_date", ("ts_code", "trade_date")),
    ("stock_adjust_factors", "ix_stock_adjust_factors_code_date", ("ts_code", "trade_date")),
    ("stock_daily_bars", "ix_stock_daily_bars_code_date", ("ts_code", "trade_date")),
    ("stock_daily_basic", "ix_stock_daily_basic_code_date", ("ts_code", "trade_date")),
    ("index_daily_bars", "ix_index_daily_bars_code_date", ("ts_code", "trade_date")),
    ("fund_adjust_factors", "ix_fund_adjust_factors_code_date", ("ts_code", "trade_date")),
    ("fund_daily_bars", "ix_fund_daily_bars_code_date", ("ts_code", "trade_date")),
    (
        "stock_financial_indicators",
        "ix_stock_financial_indicators_code_period",
        ("ts_code", "end_date", "ann_date"),
    ),
    ("trade_calendars", "ix_trade_calendars_exchange_date", ("exchange", "cal_date")),
    ("asset_daily_prices", "ix_asset_daily_prices_key_date", ("asset_natural_key", "trade_date")),
    ("assets", "ix_assets_market_symbol", ("market", "symbol")),
    ("stock_pool_members", "ix_stock_pool_members_pool_code", ("pool_id", "ts_code")),
    ("watchlist_items", "ix_watchlist_items_name_asset", ("watchlist_name", "asset_natural_key")),
)


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for _, index_name, _ in DUPLICATE_INDEXES:
                op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{index_name}"')
        return
    for table_name, index_name, _ in DUPLICATE_INDEXES:
        op.drop_index(index_name, table_name=table_name)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for table_name, index_name, columns in reversed(DUPLICATE_INDEXES):
                rendered_columns = ", ".join(f'"{column}"' for column in columns)
                op.execute(
                    f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{index_name}" '
                    f'ON "{table_name}" ({rendered_columns})'
                )
        return
    for table_name, index_name, columns in reversed(DUPLICATE_INDEXES):
        op.create_index(index_name, table_name, list(columns), unique=False)
