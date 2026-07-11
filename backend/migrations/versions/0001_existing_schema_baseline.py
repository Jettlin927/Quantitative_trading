"""Frozen existing schema baseline.

This revision is intentionally explicit and must not delegate schema creation
to application models at runtime.

Revision ID: 0001_existing_schema_baseline
Revises: None
Create Date: 2026-07-11 16:43:58.021298
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa



revision: str = '0001_existing_schema_baseline'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Apply the schema changes."""
    op.create_table('assets',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('natural_key', sa.String(length=80), nullable=False),
    sa.Column('market', sa.String(length=16), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=True),
    sa.Column('instrument_type', sa.String(length=40), nullable=True),
    sa.Column('leverage_factor', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('risk_tag', sa.String(length=80), nullable=True),
    sa.Column('theme', sa.String(length=120), nullable=True),
    sa.Column('is_sample', sa.Boolean(), nullable=False),
    sa.Column('source', sa.String(length=120), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('market', 'symbol', name='uq_asset_market_symbol')
    )
    op.create_index(op.f('ix_assets_market'), 'assets', ['market'], unique=False)
    op.create_index('ix_assets_market_symbol', 'assets', ['market', 'symbol'], unique=False)
    op.create_index(op.f('ix_assets_natural_key'), 'assets', ['natural_key'], unique=True)
    op.create_index(op.f('ix_assets_symbol'), 'assets', ['symbol'], unique=False)
    op.create_table('data_overview_snapshots',
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('data_sync_jobs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('action', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('payload_hash', sa.String(length=64), nullable=False),
    sa.Column('active_key', sa.String(length=64), nullable=True),
    sa.Column('rows_upserted', sa.Integer(), nullable=False),
    sa.Column('message', sa.String(length=1000), nullable=True),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('active_key', name='uq_data_sync_jobs_active_key')
    )
    op.create_index(op.f('ix_data_sync_jobs_action'), 'data_sync_jobs', ['action'], unique=False)
    op.create_index(op.f('ix_data_sync_jobs_payload_hash'), 'data_sync_jobs', ['payload_hash'], unique=False)
    op.create_index(op.f('ix_data_sync_jobs_status'), 'data_sync_jobs', ['status'], unique=False)
    op.create_index('ix_data_sync_jobs_status_created', 'data_sync_jobs', ['status', 'created_at'], unique=False)
    op.create_table('data_sync_runs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('target', sa.String(length=80), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=True),
    sa.Column('end_date', sa.Date(), nullable=True),
    sa.Column('rows_upserted', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('message', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_data_sync_runs_target'), 'data_sync_runs', ['target'], unique=False)
    op.create_table('fund_adjust_factors',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('ts_code', sa.String(length=16), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('adj_factor', sa.Numeric(precision=20, scale=6), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ts_code', 'trade_date', name='uq_fund_adjust_factor_code_date')
    )
    op.create_index('ix_fund_adjust_factors_code_date', 'fund_adjust_factors', ['ts_code', 'trade_date'], unique=False)
    op.create_index(op.f('ix_fund_adjust_factors_trade_date'), 'fund_adjust_factors', ['trade_date'], unique=False)
    op.create_index(op.f('ix_fund_adjust_factors_ts_code'), 'fund_adjust_factors', ['ts_code'], unique=False)
    op.create_table('fund_daily_bars',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('ts_code', sa.String(length=16), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('open', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('high', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('low', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('close', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('pre_close', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('change_amount', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('pct_chg', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('vol', sa.Numeric(precision=24, scale=4), nullable=True),
    sa.Column('amount', sa.Numeric(precision=24, scale=4), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ts_code', 'trade_date', name='uq_fund_daily_bar_code_date')
    )
    op.create_index('ix_fund_daily_bars_code_date', 'fund_daily_bars', ['ts_code', 'trade_date'], unique=False)
    op.create_index(op.f('ix_fund_daily_bars_trade_date'), 'fund_daily_bars', ['trade_date'], unique=False)
    op.create_index(op.f('ix_fund_daily_bars_ts_code'), 'fund_daily_bars', ['ts_code'], unique=False)
    op.create_table('funds',
    sa.Column('ts_code', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('market', sa.String(length=40), nullable=True),
    sa.Column('fund_type', sa.String(length=80), nullable=True),
    sa.Column('management', sa.String(length=120), nullable=True),
    sa.Column('custodian', sa.String(length=120), nullable=True),
    sa.Column('list_date', sa.Date(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('ts_code')
    )
    op.create_index(op.f('ix_funds_fund_type'), 'funds', ['fund_type'], unique=False)
    op.create_index(op.f('ix_funds_market'), 'funds', ['market'], unique=False)
    op.create_index(op.f('ix_funds_name'), 'funds', ['name'], unique=False)
    op.create_table('index_daily_bars',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('ts_code', sa.String(length=16), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('open', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('high', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('low', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('close', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('pre_close', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('change_amount', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('pct_chg', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('vol', sa.Numeric(precision=24, scale=4), nullable=True),
    sa.Column('amount', sa.Numeric(precision=24, scale=4), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ts_code', 'trade_date', name='uq_index_daily_bar_code_date')
    )
    op.create_index('ix_index_daily_bars_code_date', 'index_daily_bars', ['ts_code', 'trade_date'], unique=False)
    op.create_index(op.f('ix_index_daily_bars_trade_date'), 'index_daily_bars', ['trade_date'], unique=False)
    op.create_index(op.f('ix_index_daily_bars_ts_code'), 'index_daily_bars', ['ts_code'], unique=False)
    op.create_table('indices',
    sa.Column('ts_code', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('market', sa.String(length=40), nullable=True),
    sa.Column('publisher', sa.String(length=120), nullable=True),
    sa.Column('category', sa.String(length=80), nullable=True),
    sa.Column('base_date', sa.Date(), nullable=True),
    sa.Column('list_date', sa.Date(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('ts_code')
    )
    op.create_index(op.f('ix_indices_category'), 'indices', ['category'], unique=False)
    op.create_index(op.f('ix_indices_market'), 'indices', ['market'], unique=False)
    op.create_index(op.f('ix_indices_name'), 'indices', ['name'], unique=False)
    op.create_table('industry_classifications',
    sa.Column('index_code', sa.String(length=16), nullable=False),
    sa.Column('industry_name', sa.String(length=120), nullable=False),
    sa.Column('level', sa.String(length=20), nullable=True),
    sa.Column('industry_code', sa.String(length=40), nullable=True),
    sa.Column('parent_code', sa.String(length=40), nullable=True),
    sa.Column('src', sa.String(length=40), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('index_code')
    )
    op.create_index(op.f('ix_industry_classifications_industry_code'), 'industry_classifications', ['industry_code'], unique=False)
    op.create_index(op.f('ix_industry_classifications_industry_name'), 'industry_classifications', ['industry_name'], unique=False)
    op.create_index(op.f('ix_industry_classifications_level'), 'industry_classifications', ['level'], unique=False)
    op.create_index(op.f('ix_industry_classifications_parent_code'), 'industry_classifications', ['parent_code'], unique=False)
    op.create_index(op.f('ix_industry_classifications_src'), 'industry_classifications', ['src'], unique=False)
    op.create_table('portfolio_snapshots',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('snapshot_id', sa.String(length=80), nullable=False),
    sa.Column('source', sa.String(length=120), nullable=True),
    sa.Column('is_sample', sa.Boolean(), nullable=False),
    sa.Column('holding_count', sa.Integer(), nullable=False),
    sa.Column('total_sample_cost_basis', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('holdings', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_portfolio_snapshots_snapshot_id'), 'portfolio_snapshots', ['snapshot_id'], unique=True)
    op.create_table('stock_adjust_factors',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('ts_code', sa.String(length=16), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('adj_factor', sa.Numeric(precision=20, scale=6), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ts_code', 'trade_date', name='uq_stock_adjust_factor_code_date')
    )
    op.create_index('ix_stock_adjust_factors_code_date', 'stock_adjust_factors', ['ts_code', 'trade_date'], unique=False)
    op.create_index(op.f('ix_stock_adjust_factors_trade_date'), 'stock_adjust_factors', ['trade_date'], unique=False)
    op.create_index(op.f('ix_stock_adjust_factors_ts_code'), 'stock_adjust_factors', ['ts_code'], unique=False)
    op.create_table('stock_daily_bars',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('ts_code', sa.String(length=16), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('open', sa.Numeric(precision=12, scale=4), nullable=False),
    sa.Column('high', sa.Numeric(precision=12, scale=4), nullable=False),
    sa.Column('low', sa.Numeric(precision=12, scale=4), nullable=False),
    sa.Column('close', sa.Numeric(precision=12, scale=4), nullable=False),
    sa.Column('pre_close', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('change_amount', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('pct_chg', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('vol', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('amount', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ts_code', 'trade_date', name='uq_stock_daily_bar_code_date')
    )
    op.create_index('ix_stock_daily_bars_code_date', 'stock_daily_bars', ['ts_code', 'trade_date'], unique=False)
    op.create_index(op.f('ix_stock_daily_bars_trade_date'), 'stock_daily_bars', ['trade_date'], unique=False)
    op.create_index(op.f('ix_stock_daily_bars_ts_code'), 'stock_daily_bars', ['ts_code'], unique=False)
    op.create_table('stock_daily_basic',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('ts_code', sa.String(length=16), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('close', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('turnover_rate', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('turnover_rate_f', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('volume_ratio', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('pe', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('pe_ttm', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('pb', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('ps', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('ps_ttm', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('dv_ratio', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('dv_ttm', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('total_share', sa.Numeric(precision=24, scale=4), nullable=True),
    sa.Column('float_share', sa.Numeric(precision=24, scale=4), nullable=True),
    sa.Column('free_share', sa.Numeric(precision=24, scale=4), nullable=True),
    sa.Column('total_mv', sa.Numeric(precision=24, scale=4), nullable=True),
    sa.Column('circ_mv', sa.Numeric(precision=24, scale=4), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ts_code', 'trade_date', name='uq_stock_daily_basic_code_date')
    )
    op.create_index('ix_stock_daily_basic_code_date', 'stock_daily_basic', ['ts_code', 'trade_date'], unique=False)
    op.create_index(op.f('ix_stock_daily_basic_trade_date'), 'stock_daily_basic', ['trade_date'], unique=False)
    op.create_index(op.f('ix_stock_daily_basic_ts_code'), 'stock_daily_basic', ['ts_code'], unique=False)
    op.create_table('stock_financial_indicators',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('ts_code', sa.String(length=16), nullable=False),
    sa.Column('ann_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('eps', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('dt_eps', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('bps', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('netprofit_margin', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('grossprofit_margin', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('roe', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('roe_waa', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('roa', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('debt_to_assets', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('current_ratio', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('quick_ratio', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('assets_turn', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('basic_eps_yoy', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('op_yoy', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('netprofit_yoy', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('tr_yoy', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('or_yoy', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('q_sales_yoy', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('q_profit_yoy', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ts_code', 'end_date', 'ann_date', name='uq_stock_financial_indicator_period')
    )
    op.create_index(op.f('ix_stock_financial_indicators_ann_date'), 'stock_financial_indicators', ['ann_date'], unique=False)
    op.create_index('ix_stock_financial_indicators_code_period', 'stock_financial_indicators', ['ts_code', 'end_date', 'ann_date'], unique=False)
    op.create_index(op.f('ix_stock_financial_indicators_end_date'), 'stock_financial_indicators', ['end_date'], unique=False)
    op.create_index(op.f('ix_stock_financial_indicators_ts_code'), 'stock_financial_indicators', ['ts_code'], unique=False)
    op.create_table('stock_limit_prices',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('ts_code', sa.String(length=16), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('pre_close', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('up_limit', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('down_limit', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ts_code', 'trade_date', name='uq_stock_limit_price_code_date')
    )
    op.create_index('ix_stock_limit_prices_code_date', 'stock_limit_prices', ['ts_code', 'trade_date'], unique=False)
    op.create_index(op.f('ix_stock_limit_prices_trade_date'), 'stock_limit_prices', ['trade_date'], unique=False)
    op.create_index(op.f('ix_stock_limit_prices_ts_code'), 'stock_limit_prices', ['ts_code'], unique=False)
    op.create_table('stock_listings',
    sa.Column('ts_code', sa.String(length=16), nullable=False),
    sa.Column('symbol', sa.String(length=12), nullable=True),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('area', sa.String(length=50), nullable=True),
    sa.Column('industry', sa.String(length=80), nullable=True),
    sa.Column('market', sa.String(length=50), nullable=True),
    sa.Column('exchange', sa.String(length=16), nullable=True),
    sa.Column('list_status', sa.String(length=2), nullable=False),
    sa.Column('list_date', sa.Date(), nullable=True),
    sa.Column('delist_date', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('ts_code')
    )
    op.create_index(op.f('ix_stock_listings_delist_date'), 'stock_listings', ['delist_date'], unique=False)
    op.create_index(op.f('ix_stock_listings_exchange'), 'stock_listings', ['exchange'], unique=False)
    op.create_index(op.f('ix_stock_listings_industry'), 'stock_listings', ['industry'], unique=False)
    op.create_index(op.f('ix_stock_listings_list_date'), 'stock_listings', ['list_date'], unique=False)
    op.create_index(op.f('ix_stock_listings_list_status'), 'stock_listings', ['list_status'], unique=False)
    op.create_index(op.f('ix_stock_listings_market'), 'stock_listings', ['market'], unique=False)
    op.create_index(op.f('ix_stock_listings_name'), 'stock_listings', ['name'], unique=False)
    op.create_index(op.f('ix_stock_listings_symbol'), 'stock_listings', ['symbol'], unique=False)
    op.create_table('stock_pools',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.String(length=200), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_stock_pools_name'), 'stock_pools', ['name'], unique=True)
    op.create_table('stock_suspend_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('ts_code', sa.String(length=16), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('suspend_type', sa.String(length=2), nullable=False),
    sa.Column('suspend_timing', sa.String(length=40), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ts_code', 'trade_date', 'suspend_type', 'suspend_timing', name='uq_stock_suspend_event')
    )
    op.create_index('ix_stock_suspend_events_code_date', 'stock_suspend_events', ['ts_code', 'trade_date'], unique=False)
    op.create_index(op.f('ix_stock_suspend_events_suspend_type'), 'stock_suspend_events', ['suspend_type'], unique=False)
    op.create_index(op.f('ix_stock_suspend_events_trade_date'), 'stock_suspend_events', ['trade_date'], unique=False)
    op.create_index(op.f('ix_stock_suspend_events_ts_code'), 'stock_suspend_events', ['ts_code'], unique=False)
    op.create_table('stocks',
    sa.Column('ts_code', sa.String(length=16), nullable=False),
    sa.Column('symbol', sa.String(length=12), nullable=True),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('area', sa.String(length=50), nullable=True),
    sa.Column('industry', sa.String(length=80), nullable=True),
    sa.Column('market', sa.String(length=50), nullable=True),
    sa.Column('list_date', sa.Date(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('ts_code')
    )
    op.create_index(op.f('ix_stocks_industry'), 'stocks', ['industry'], unique=False)
    op.create_index(op.f('ix_stocks_name'), 'stocks', ['name'], unique=False)
    op.create_index(op.f('ix_stocks_symbol'), 'stocks', ['symbol'], unique=False)
    op.create_table('trade_calendars',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('exchange', sa.String(length=16), nullable=False),
    sa.Column('cal_date', sa.Date(), nullable=False),
    sa.Column('is_open', sa.Boolean(), nullable=False),
    sa.Column('pretrade_date', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('exchange', 'cal_date', name='uq_trade_calendar_exchange_date')
    )
    op.create_index(op.f('ix_trade_calendars_cal_date'), 'trade_calendars', ['cal_date'], unique=False)
    op.create_index(op.f('ix_trade_calendars_exchange'), 'trade_calendars', ['exchange'], unique=False)
    op.create_index('ix_trade_calendars_exchange_date', 'trade_calendars', ['exchange', 'cal_date'], unique=False)
    op.create_table('asset_daily_prices',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('natural_key', sa.String(length=120), nullable=False),
    sa.Column('asset_natural_key', sa.String(length=80), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('close', sa.Numeric(precision=20, scale=6), nullable=True),
    sa.Column('ma20', sa.Numeric(precision=20, scale=6), nullable=True),
    sa.Column('ma50', sa.Numeric(precision=20, scale=6), nullable=True),
    sa.Column('ma200', sa.Numeric(precision=20, scale=6), nullable=True),
    sa.Column('return20d_pct', sa.Numeric(precision=20, scale=6), nullable=True),
    sa.Column('return60d_pct', sa.Numeric(precision=20, scale=6), nullable=True),
    sa.Column('volatility20d_pct', sa.Numeric(precision=20, scale=6), nullable=True),
    sa.Column('is_sample', sa.Boolean(), nullable=False),
    sa.Column('source', sa.String(length=120), nullable=True),
    sa.Column('is_stale', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['asset_natural_key'], ['assets.natural_key'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('asset_natural_key', 'trade_date', name='uq_asset_daily_price_key_date')
    )
    op.create_index(op.f('ix_asset_daily_prices_asset_natural_key'), 'asset_daily_prices', ['asset_natural_key'], unique=False)
    op.create_index('ix_asset_daily_prices_key_date', 'asset_daily_prices', ['asset_natural_key', 'trade_date'], unique=False)
    op.create_index(op.f('ix_asset_daily_prices_natural_key'), 'asset_daily_prices', ['natural_key'], unique=True)
    op.create_index(op.f('ix_asset_daily_prices_trade_date'), 'asset_daily_prices', ['trade_date'], unique=False)
    op.create_table('industry_members',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('index_code', sa.String(length=16), nullable=False),
    sa.Column('con_code', sa.String(length=16), nullable=False),
    sa.Column('con_name', sa.String(length=120), nullable=True),
    sa.Column('in_date', sa.Date(), nullable=False),
    sa.Column('out_date', sa.Date(), nullable=True),
    sa.Column('is_new', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['index_code'], ['industry_classifications.index_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('index_code', 'con_code', 'in_date', name='uq_industry_member_period')
    )
    op.create_index(op.f('ix_industry_members_con_code'), 'industry_members', ['con_code'], unique=False)
    op.create_index(op.f('ix_industry_members_con_name'), 'industry_members', ['con_name'], unique=False)
    op.create_index(op.f('ix_industry_members_in_date'), 'industry_members', ['in_date'], unique=False)
    op.create_index(op.f('ix_industry_members_index_code'), 'industry_members', ['index_code'], unique=False)
    op.create_index('ix_industry_members_index_con_code', 'industry_members', ['index_code', 'con_code'], unique=False)
    op.create_index(op.f('ix_industry_members_out_date'), 'industry_members', ['out_date'], unique=False)
    op.create_table('stock_pool_members',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('pool_id', sa.Integer(), nullable=False),
    sa.Column('ts_code', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['pool_id'], ['stock_pools.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ts_code'], ['stocks.ts_code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('pool_id', 'ts_code', name='uq_stock_pool_member_pool_code')
    )
    op.create_index('ix_stock_pool_members_pool_code', 'stock_pool_members', ['pool_id', 'ts_code'], unique=False)
    op.create_index(op.f('ix_stock_pool_members_pool_id'), 'stock_pool_members', ['pool_id'], unique=False)
    op.create_index(op.f('ix_stock_pool_members_ts_code'), 'stock_pool_members', ['ts_code'], unique=False)
    op.create_table('watchlist_items',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('natural_key', sa.String(length=160), nullable=False),
    sa.Column('watchlist_name', sa.String(length=80), nullable=False),
    sa.Column('asset_natural_key', sa.String(length=80), nullable=False),
    sa.Column('role', sa.String(length=40), nullable=True),
    sa.Column('theme', sa.String(length=120), nullable=True),
    sa.Column('subtheme', sa.String(length=160), nullable=True),
    sa.Column('risk_tag', sa.String(length=80), nullable=True),
    sa.Column('notes', sa.String(length=1000), nullable=True),
    sa.Column('is_sample', sa.Boolean(), nullable=False),
    sa.Column('source', sa.String(length=120), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['asset_natural_key'], ['assets.natural_key'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('watchlist_name', 'asset_natural_key', name='uq_watchlist_item_name_asset')
    )
    op.create_index(op.f('ix_watchlist_items_asset_natural_key'), 'watchlist_items', ['asset_natural_key'], unique=False)
    op.create_index('ix_watchlist_items_name_asset', 'watchlist_items', ['watchlist_name', 'asset_natural_key'], unique=False)
    op.create_index(op.f('ix_watchlist_items_natural_key'), 'watchlist_items', ['natural_key'], unique=True)
    op.create_index(op.f('ix_watchlist_items_watchlist_name'), 'watchlist_items', ['watchlist_name'], unique=False)


def downgrade() -> None:
    """Refuse a destructive downgrade of a stamped existing database."""
    raise RuntimeError("0001_existing_schema_baseline 是既有生产 schema baseline，禁止自动降级并删除持久化表。")
