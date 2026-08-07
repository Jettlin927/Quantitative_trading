from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index as SqlIndex,
    Integer,
    JSON,
    LargeBinary,
    Numeric,
    String,
    Uuid,
    UniqueConstraint,
    event,
    func,
    inspect as sa_inspect,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, PrivateBase


RESEARCH_RUN_STATUS_VALUES = {
    "queued",
    "running",
    "retrying",
    "succeeded",
    "failed",
    "interrupted",
}
STRATEGY_LIFECYCLE_VALUES = {"活跃", "暂停", "停止研究", "已归档"}
RESEARCH_CONCLUSION_VALUES = {"研究通过", "有条件候选", "证据不足", "受阻", "不通过"}
FORMAL_RESEARCH_PHASE_VALUES = {"approved", "active", "evaluating", "published", "stopped"}
RESEARCH_ORCHESTRATION_STATE_VALUES = {
    "pending_approval",
    "approved",
    "queued",
    "running",
    "stopping",
    "publishing",
    "published",
    "stopped",
    "blocked",
}
RESEARCH_WORK_STATUS_VALUES = {
    "queued",
    "leased",
    "running",
    "succeeded",
    "failed",
    "interrupted",
}
PUBLICATION_STATUS_VALUES = {"pending", "published", "failed"}
FOLLOW_UP_PROPOSAL_STATUS_VALUES = {"proposed", "accepted", "rejected", "converted"}
RESEARCH_PLAN_ACTION_VALUES = {"approved", "invalidated", "stopped", "historical_import"}
FORMAL_RESEARCH_ORIGIN_VALUES = {"native", "historical_import"}


class Stock(Base):
    __tablename__ = "stocks"

    ts_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(12), index=True)
    name: Mapped[str] = mapped_column(String(80), index=True)
    area: Mapped[str | None] = mapped_column(String(50))
    industry: Mapped[str | None] = mapped_column(String(80), index=True)
    market: Mapped[str | None] = mapped_column(String(50))
    list_date: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StockDailyBar(Base):
    __tablename__ = "stock_daily_bars"
    __table_args__ = (UniqueConstraint("ts_code", "trade_date", name="uq_stock_daily_bar_code_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    high: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    low: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    close: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    pre_close: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    change_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    pct_chg: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    vol: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StockDailyBasic(Base):
    __tablename__ = "stock_daily_basic"
    __table_args__ = (UniqueConstraint("ts_code", "trade_date", name="uq_stock_daily_basic_code_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    close: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    turnover_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    turnover_rate_f: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    volume_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    pe: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    pe_ttm: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    pb: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    ps: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    ps_ttm: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    dv_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    dv_ttm: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    total_share: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    float_share: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    free_share: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    total_mv: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    circ_mv: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StockFinancialIndicator(Base):
    __tablename__ = "stock_financial_indicators"
    __table_args__ = (
        UniqueConstraint(
            "ts_code",
            "end_date",
            "ann_date",
            "source_revision_sha256",
            name="uq_stock_financial_indicator_revision",
        ),
        CheckConstraint(
            "(revision_status = 'legacy_unverified' "
            "AND source_revision_sha256 IS NULL "
            "AND source_observed_at IS NULL "
            "AND available_from IS NULL) OR "
            "(revision_status = 'observed' "
            "AND source_revision_sha256 IS NOT NULL "
            "AND source_observed_at IS NOT NULL "
            "AND available_from IS NOT NULL)",
            name="ck_stock_financial_indicator_revision_evidence",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(16), index=True)
    ann_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    eps: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    dt_eps: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    bps: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    netprofit_margin: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    grossprofit_margin: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    roe: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    roe_waa: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    roa: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    debt_to_assets: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    current_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    quick_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    assets_turn: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    basic_eps_yoy: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    op_yoy: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    netprofit_yoy: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    tr_yoy: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    or_yoy: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    q_sales_yoy: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    q_profit_yoy: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    source_update_flag: Mapped[str | None] = mapped_column(String(8))
    source_revision_sha256: Mapped[str | None] = mapped_column(String(64))
    source_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_from: Mapped[date | None] = mapped_column(Date, index=True)
    revision_status: Mapped[str] = mapped_column(
        String(24),
        default="legacy_unverified",
        server_default="legacy_unverified",
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StockListing(Base):
    __tablename__ = "stock_listings"

    ts_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(12), index=True)
    name: Mapped[str] = mapped_column(String(80), index=True)
    area: Mapped[str | None] = mapped_column(String(50))
    industry: Mapped[str | None] = mapped_column(String(80), index=True)
    market: Mapped[str | None] = mapped_column(String(50), index=True)
    exchange: Mapped[str | None] = mapped_column(String(16), index=True)
    list_status: Mapped[str] = mapped_column(String(2), index=True)
    list_date: Mapped[date | None] = mapped_column(Date, index=True)
    delist_date: Mapped[date | None] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StockLimitPrice(Base):
    __tablename__ = "stock_limit_prices"
    __table_args__ = (UniqueConstraint("ts_code", "trade_date", name="uq_stock_limit_price_code_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    pre_close: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    up_limit: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    down_limit: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StockSuspendEvent(Base):
    __tablename__ = "stock_suspend_events"
    __table_args__ = (
        UniqueConstraint("ts_code", "trade_date", "suspend_type", "suspend_timing", name="uq_stock_suspend_event"),
        SqlIndex("ix_stock_suspend_events_code_date", "ts_code", "trade_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    suspend_type: Mapped[str] = mapped_column(String(2), index=True)
    suspend_timing: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PersonalWorkspace(PrivateBase):
    __tablename__ = "personal_workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_identity_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    revision: Mapped[int] = mapped_column(default=1, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PersonalHolding(PrivateBase):
    __tablename__ = "personal_holdings"
    __table_args__ = (
        UniqueConstraint("workspace_id", "symbol_hmac", name="uq_personal_holding_workspace_symbol"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    symbol_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(default=1, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PersonalPortfolioRevision(PrivateBase):
    __tablename__ = "personal_portfolio_revisions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "portfolio_revision", name="uq_personal_portfolio_revision"),
        UniqueConstraint("workspace_id", "idempotency_hash", name="uq_personal_portfolio_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    holding_id: Mapped[str | None] = mapped_column(String(36))
    portfolio_revision: Mapped[int] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    idempotency_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PersonalAuditEvent(PrivateBase):
    __tablename__ = "personal_audit_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_hash", name="uq_personal_audit_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    object_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    idempotency_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    portfolio_revision: Mapped[int] = mapped_column(nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    backup_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PersonalPriceObservation(PrivateBase):
    """最近一次成功行情落盘：workspace + symbol_hmac 唯一，观察数据整体加密。"""

    __tablename__ = "personal_price_observations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "symbol_hmac", name="uq_personal_price_observation_symbol"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    symbol_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema: Mapped[str] = mapped_column(String(32), nullable=False)


class PersonalEquitySnapshot(PrivateBase):
    """每日权益快照：workspace + 美股交易日唯一，持仓/价格快照加密落盘。"""

    __tablename__ = "personal_equity_snapshots"
    __table_args__ = (
        UniqueConstraint("workspace_id", "market_day", name="uq_personal_equity_snapshot_day"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    market_day: Mapped[date] = mapped_column(Date, nullable=False)
    total_equity: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    total_market_value: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    usd_cash: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    holdings_count: Mapped[int] = mapped_column(nullable=False)
    priced_count: Mapped[int] = mapped_column(nullable=False)
    after_close: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PersonalRealizedTrade(PrivateBase):
    """已实现交易（卖出）：workspace + portfolio_revision 唯一；私有业务值整体加密。"""

    __tablename__ = "personal_realized_trades"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "portfolio_revision",
            name="uq_personal_realized_trade_revision",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    portfolio_revision: Mapped[int] = mapped_column(nullable=False)
    symbol_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    sold_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PersonalRuleInstance(PrivateBase):
    __tablename__ = "personal_rule_instances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version: Mapped[int] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    revision: Mapped[int] = mapped_column(nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PersonalRuleRevision(PrivateBase):
    __tablename__ = "personal_rule_revisions"
    __table_args__ = (
        UniqueConstraint("rule_id", "revision", name="uq_personal_rule_revision"),
        UniqueConstraint("workspace_id", "idempotency_hash", name="uq_personal_rule_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    rule_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_rule_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PersonalRuleEvaluationBatch(PrivateBase):
    __tablename__ = "personal_rule_evaluation_batches"
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_hash", name="uq_personal_rule_batch_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PersonalRuleEvaluation(PrivateBase):
    __tablename__ = "personal_rule_evaluations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    result_summary: Mapped[str] = mapped_column(String(64), nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False)
    batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("private_workbench.personal_rule_evaluation_batches.id", ondelete="CASCADE")
    )
    rule_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("private_workbench.personal_rule_revisions.id", ondelete="CASCADE")
    )
    result: Mapped[str | None] = mapped_column(String(32))
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence_ids: Mapped[list[str] | None] = mapped_column(JSON)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PersonalAnalysisDraft(PrivateBase):
    __tablename__ = "personal_analysis_drafts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_hash", name="uq_personal_analysis_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    preview_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(80))
    config_revision: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PersonalEvidencePack(PrivateBase):
    __tablename__ = "personal_evidence_packs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    draft_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_analysis_drafts.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    context_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PersonalEvidenceRef(PrivateBase):
    __tablename__ = "personal_evidence_refs"
    __table_args__ = (
        UniqueConstraint("pack_id", "public_source_id", name="uq_personal_evidence_ref_source"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    pack_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_evidence_packs.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    public_source_id: Mapped[str] = mapped_column(String(160), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PersonalAnalysisRun(PrivateBase):
    __tablename__ = "personal_analysis_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_hash", name="uq_personal_analysis_run_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    draft_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_analysis_drafts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(nullable=False, default=2)
    idempotency_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PersonalAnalysisAttempt(PrivateBase):
    __tablename__ = "personal_analysis_attempts"
    __table_args__ = (
        UniqueConstraint("run_id", "attempt", name="uq_personal_analysis_attempt"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column()
    output_tokens: Mapped[int | None] = mapped_column()
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(16, 8))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PersonalAnalysisEvent(PrivateBase):
    __tablename__ = "personal_analysis_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_personal_analysis_event_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    code: Mapped[str | None] = mapped_column(String(64))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema: Mapped[str] = mapped_column(String(32), nullable=False)


class PersonalAiClaim(PrivateBase):
    __tablename__ = "personal_ai_claims"
    __table_args__ = (
        UniqueConstraint("run_id", "claim_order", name="uq_personal_ai_claim_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    claim_order: Mapped[int] = mapped_column(nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_schema: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PersonalRedactionEvent(PrivateBase):
    __tablename__ = "personal_redaction_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_hash", name="uq_personal_redaction_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("private_workbench.personal_workspaces.id", ondelete="CASCADE"), nullable=False
    )
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[str] = mapped_column(String(36), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    backup_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TradeCalendar(Base):
    __tablename__ = "trade_calendars"
    __table_args__ = (UniqueConstraint("exchange", "cal_date", name="uq_trade_calendar_exchange_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    exchange: Mapped[str] = mapped_column(String(16), index=True)
    cal_date: Mapped[date] = mapped_column(Date, index=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=False)
    pretrade_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StockAdjustFactor(Base):
    __tablename__ = "stock_adjust_factors"
    __table_args__ = (UniqueConstraint("ts_code", "trade_date", name="uq_stock_adjust_factor_code_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    adj_factor: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Index(Base):
    __tablename__ = "indices"

    ts_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    market: Mapped[str | None] = mapped_column(String(40), index=True)
    publisher: Mapped[str | None] = mapped_column(String(120))
    category: Mapped[str | None] = mapped_column(String(80), index=True)
    base_date: Mapped[date | None] = mapped_column(Date)
    list_date: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IndexDailyBar(Base):
    __tablename__ = "index_daily_bars"
    __table_args__ = (UniqueConstraint("ts_code", "trade_date", name="uq_index_daily_bar_code_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    high: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    low: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    close: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    pre_close: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    change_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    pct_chg: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    vol: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Fund(Base):
    __tablename__ = "funds"

    ts_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    market: Mapped[str | None] = mapped_column(String(40), index=True)
    fund_type: Mapped[str | None] = mapped_column(String(80), index=True)
    management: Mapped[str | None] = mapped_column(String(120))
    custodian: Mapped[str | None] = mapped_column(String(120))
    list_date: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class FundDailyBar(Base):
    __tablename__ = "fund_daily_bars"
    __table_args__ = (UniqueConstraint("ts_code", "trade_date", name="uq_fund_daily_bar_code_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    high: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    low: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    close: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    pre_close: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    change_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    pct_chg: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    vol: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class FundAdjustFactor(Base):
    __tablename__ = "fund_adjust_factors"
    __table_args__ = (UniqueConstraint("ts_code", "trade_date", name="uq_fund_adjust_factor_code_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    adj_factor: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IndustryClassification(Base):
    __tablename__ = "industry_classifications"

    index_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    industry_name: Mapped[str] = mapped_column(String(120), index=True)
    level: Mapped[str | None] = mapped_column(String(20), index=True)
    industry_code: Mapped[str | None] = mapped_column(String(40), index=True)
    parent_code: Mapped[str | None] = mapped_column(String(40), index=True)
    src: Mapped[str | None] = mapped_column(String(40), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IndustryMember(Base):
    __tablename__ = "industry_members"
    __table_args__ = (
        UniqueConstraint("index_code", "con_code", "in_date", name="uq_industry_member_period"),
        SqlIndex("ix_industry_members_index_con_code", "index_code", "con_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    index_code: Mapped[str] = mapped_column(ForeignKey("industry_classifications.index_code", ondelete="CASCADE"), index=True)
    con_code: Mapped[str] = mapped_column(String(16), index=True)
    con_name: Mapped[str | None] = mapped_column(String(120), index=True)
    in_date: Mapped[date] = mapped_column(Date, index=True)
    out_date: Mapped[date | None] = mapped_column(Date, index=True)
    is_new: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StockPool(Base):
    __tablename__ = "stock_pools"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StockPoolMember(Base):
    __tablename__ = "stock_pool_members"
    __table_args__ = (UniqueConstraint("pool_id", "ts_code", name="uq_stock_pool_member_pool_code"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("stock_pools.id", ondelete="CASCADE"), index=True)
    ts_code: Mapped[str] = mapped_column(ForeignKey("stocks.ts_code", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("market", "symbol", name="uq_asset_market_symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    natural_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str | None] = mapped_column(String(120))
    instrument_type: Mapped[str | None] = mapped_column(String(40))
    leverage_factor: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    risk_tag: Mapped[str | None] = mapped_column(String(80))
    theme: Mapped[str | None] = mapped_column(String(120))
    is_sample: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AssetDailyPrice(Base):
    __tablename__ = "asset_daily_prices"
    __table_args__ = (UniqueConstraint("asset_natural_key", "trade_date", name="uq_asset_daily_price_key_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    natural_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    asset_natural_key: Mapped[str] = mapped_column(ForeignKey("assets.natural_key", ondelete="CASCADE"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    close: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    ma20: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    ma50: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    ma200: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    return20d_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    return60d_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    volatility20d_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    is_sample: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str | None] = mapped_column(String(120))
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UsExperimentInstrument(Base):
    __tablename__ = "us_experiment_instruments"

    source_code: Mapped[str] = mapped_column(String(40), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    yahoo_symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str | None] = mapped_column(String(200))
    market_code: Mapped[str] = mapped_column(String(3), index=True)
    market_name: Mapped[str] = mapped_column(String(24))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    history_start_date: Mapped[date | None] = mapped_column(Date)
    history_end_date: Mapped[date | None] = mapped_column(Date)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_status: Mapped[str | None] = mapped_column(String(24), index=True)
    last_sync_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UsExperimentDailyBar(Base):
    __tablename__ = "us_experiment_daily_bars"
    __table_args__ = (
        UniqueConstraint("source_code", "trade_date", name="uq_us_experiment_daily_bar_code_date"),
        SqlIndex("ix_us_experiment_daily_bars_date_code", "trade_date", "source_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_code: Mapped[str] = mapped_column(
        ForeignKey("us_experiment_instruments.source_code", ondelete="CASCADE"),
        index=True,
    )
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    high: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    low: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    close: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    adj_close: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    volume: Mapped[int | None] = mapped_column(BigInteger)
    cash_dividend: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    split_ratio: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    source: Mapped[str] = mapped_column(String(24), default="yfinance", server_default="yfinance")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UsExperimentDailyCheck(Base):
    __tablename__ = "us_experiment_daily_checks"
    __table_args__ = (
        UniqueConstraint("source_code", "trade_date", name="uq_us_experiment_daily_check_code_date"),
        SqlIndex("ix_us_experiment_daily_checks_status_date", "status", "trade_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_code: Mapped[str] = mapped_column(
        ForeignKey("us_experiment_instruments.source_code", ondelete="CASCADE"),
        index=True,
    )
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    yfinance_open: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    yfinance_high: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    yfinance_low: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    yfinance_close: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    yfinance_volume: Mapped[int | None] = mapped_column(BigInteger)
    akshare_open: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    akshare_high: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    akshare_low: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    akshare_close: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    akshare_volume: Mapped[int | None] = mapped_column(BigInteger)
    max_price_relative_diff: Mapped[Decimal | None] = mapped_column(Numeric(18, 10))
    volume_relative_diff: Mapped[Decimal | None] = mapped_column(Numeric(18, 10))
    status: Mapped[str] = mapped_column(String(24), index=True)
    message: Mapped[str | None] = mapped_column(String(500))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("watchlist_name", "asset_natural_key", name="uq_watchlist_item_name_asset"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    natural_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    watchlist_name: Mapped[str] = mapped_column(String(80), index=True)
    asset_natural_key: Mapped[str] = mapped_column(ForeignKey("assets.natural_key", ondelete="CASCADE"), index=True)
    role: Mapped[str | None] = mapped_column(String(40))
    theme: Mapped[str | None] = mapped_column(String(120))
    subtheme: Mapped[str | None] = mapped_column(String(160))
    risk_tag: Mapped[str | None] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(String(1000))
    is_sample: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    source: Mapped[str | None] = mapped_column(String(120))
    is_sample: Mapped[bool] = mapped_column(Boolean, default=True)
    holding_count: Mapped[int] = mapped_column(default=0)
    total_sample_cost_basis: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    holdings: Mapped[list[dict] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DataSyncRun(Base):
    __tablename__ = "data_sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), default="tushare")
    target: Mapped[str] = mapped_column(String(80), index=True)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    rows_upserted: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default="ok")
    message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DataSyncJob(Base):
    __tablename__ = "data_sync_jobs"
    __table_args__ = (
        UniqueConstraint("active_key", name="uq_data_sync_jobs_active_key"),
        SqlIndex("ix_data_sync_jobs_status_created", "status", "created_at"),
        SqlIndex("ix_data_sync_jobs_claim", "status", "next_attempt_at", "created_at"),
        SqlIndex("ix_data_sync_jobs_lease_expiry", "status", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    action: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    active_key: Mapped[str | None] = mapped_column(String(64))
    rows_upserted: Mapped[int] = mapped_column(default=0)
    message: Mapped[str | None] = mapped_column(String(1000))
    result: Mapped[dict | list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(default=3, server_default="3")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SyncWorkerHeartbeat(Base):
    __tablename__ = "sync_worker_heartbeats"
    __table_args__ = (
        SqlIndex("ix_sync_worker_heartbeats_status_heartbeat", "status", "heartbeat_at"),
    )

    worker_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="starting", server_default="starting")
    current_job_id: Mapped[str | None] = mapped_column(String(36))
    process_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    code_commit: Mapped[str] = mapped_column(String(64), default="unknown", server_default="unknown")
    last_error: Mapped[str | None] = mapped_column(String(1000))


class DataOverviewSnapshot(Base):
    __tablename__ = "data_overview_snapshots"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DataSnapshot(Base):
    __tablename__ = "data_snapshots"
    __table_args__ = (
        CheckConstraint("status in ('building', 'complete', 'failed')", name="ck_data_snapshots_status"),
        SqlIndex("ix_data_snapshots_quality_run_created", "quality_run_id", "created_at"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # 数据质量注册表已退役；quality_run_id 仅保留为历史列，不再引用被删表。
    quality_run_id: Mapped[str] = mapped_column(String(36))
    scope: Mapped[str] = mapped_column(String(40))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    universe_hash: Mapped[str] = mapped_column(String(64))
    artifact_root: Mapped[str] = mapped_column(String(500))
    table_artifacts: Mapped[dict] = mapped_column(JSON, default=dict)
    row_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    source_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="building")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResearchRun(Base):
    __tablename__ = "research_runs"
    __table_args__ = (
        CheckConstraint(
            "status in ('queued', 'running', 'retrying', 'succeeded', 'failed', 'interrupted')",
            name="ck_research_runs_status",
        ),
        SqlIndex("ix_research_runs_strategy_started", "strategy_id", "started_at"),
        SqlIndex("ix_research_runs_reproducibility", "reproducibility_key"),
        SqlIndex("ix_research_runs_formal_research_started", "formal_research_id", "started_at"),
        SqlIndex(
            "uq_research_runs_orchestration_attempt",
            "orchestration_attempt_id",
            unique=True,
        ),
    )

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    formal_research_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("formal_researches.id", ondelete="RESTRICT")
    )
    orchestration_attempt_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    reproducibility_key: Mapped[str | None] = mapped_column(String(64))
    strategy_id: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16), default="running")
    stage: Mapped[str] = mapped_column(String(40))
    config: Mapped[dict] = mapped_column(JSON)
    config_sha256: Mapped[str] = mapped_column(String(64))
    data_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("data_snapshots.snapshot_id", ondelete="RESTRICT")
    )
    code_commit: Mapped[str] = mapped_column(String(64))
    environment_sha256: Mapped[str] = mapped_column(String(64))
    random_seed: Mapped[int] = mapped_column()
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    result_fingerprint: Mapped[str | None] = mapped_column(String(64))
    artifact_root: Mapped[str] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(2000))


class StrategyDefinition(Base):
    __tablename__ = "strategy_definitions"
    __table_args__ = (
        CheckConstraint(
            "lifecycle_status in ('活跃', '暂停', '停止研究', '已归档')",
            name="ck_strategy_definitions_lifecycle",
        ),
    )

    strategy_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(160))
    lifecycle_status: Mapped[str] = mapped_column(String(16), default="活跃", server_default="活跃")
    economic_thesis: Mapped[str] = mapped_column(String(2000))
    registry_version: Mapped[str] = mapped_column(String(40))
    code_commit: Mapped[str] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FrozenResearchPlan(Base):
    __tablename__ = "frozen_research_plans"
    __table_args__ = (
        UniqueConstraint("issue_number", "version", name="uq_frozen_research_plans_issue_version"),
        UniqueConstraint("plan_sha256", name="uq_frozen_research_plans_sha256"),
        CheckConstraint("version > 0", name="ck_frozen_research_plans_version"),
        SqlIndex("ix_frozen_research_plans_strategy_created", "strategy_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_definitions.strategy_id", ondelete="RESTRICT")
    )
    issue_number: Mapped[int] = mapped_column()
    version: Mapped[int] = mapped_column()
    schema_version: Mapped[str] = mapped_column(String(32))
    plan_sha256: Mapped[str] = mapped_column(String(64))
    code_commit: Mapped[str] = mapped_column(String(64))
    plan_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResearchPlanApproval(Base):
    __tablename__ = "research_plan_approvals"
    __table_args__ = (
        CheckConstraint(
            "action in ('approved', 'invalidated', 'stopped', 'historical_import')",
            name="ck_research_plan_approvals_action",
        ),
        CheckConstraint(
            "(action = 'historical_import' and comment_id is null and source_uri is not null) "
            "or (action <> 'historical_import' and comment_id is not null and source_uri is null)",
            name="ck_research_plan_approvals_provenance",
        ),
        UniqueConstraint("comment_id", name="uq_research_plan_approvals_comment"),
        SqlIndex("ix_research_plan_approvals_plan_created", "plan_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("frozen_research_plans.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(String(24))
    actor_login: Mapped[str] = mapped_column(String(80))
    comment_id: Mapped[int | None] = mapped_column(BigInteger)
    source_uri: Mapped[str | None] = mapped_column(String(1000))
    comment_body: Mapped[str] = mapped_column(String(500))
    plan_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FormalResearch(Base):
    __tablename__ = "formal_researches"
    __table_args__ = (
        CheckConstraint(
            "phase in ('approved', 'active', 'evaluating', 'published', 'stopped')",
            name="ck_formal_researches_phase",
        ),
        CheckConstraint(
            "origin in ('native', 'historical_import')",
            name="ck_formal_researches_origin",
        ),
        CheckConstraint(
            "origin <> 'historical_import' or phase in ('published', 'stopped')",
            name="ck_formal_researches_historical_phase",
        ),
        UniqueConstraint("plan_id", name="uq_formal_researches_plan"),
        UniqueConstraint("approval_id", name="uq_formal_researches_approval"),
        SqlIndex("ix_formal_researches_phase_created", "phase", "created_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("frozen_research_plans.id", ondelete="RESTRICT")
    )
    approval_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("research_plan_approvals.id", ondelete="RESTRICT")
    )
    origin: Mapped[str] = mapped_column(String(24), default="native", server_default="native")
    phase: Mapped[str] = mapped_column(String(16), default="approved", server_default="approved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResearchOrchestration(Base):
    __tablename__ = "research_orchestrations"
    __table_args__ = (
        CheckConstraint(
            "state in ('pending_approval', 'approved', 'queued', 'running', 'stopping', "
            "'publishing', 'published', 'stopped', 'blocked')",
            name="ck_research_orchestrations_state",
        ),
        UniqueConstraint("plan_id", name="uq_research_orchestrations_plan"),
        UniqueConstraint("formal_research_id", name="uq_research_orchestrations_formal"),
        SqlIndex("ix_research_orchestrations_issue_created", "issue_number", "created_at"),
        SqlIndex("ix_research_orchestrations_state_updated", "state", "updated_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("frozen_research_plans.id", ondelete="RESTRICT")
    )
    formal_research_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("formal_researches.id", ondelete="RESTRICT")
    )
    issue_number: Mapped[int] = mapped_column()
    state: Mapped[str] = mapped_column(
        String(24), default="pending_approval", server_default="pending_approval"
    )
    state_reason: Mapped[str | None] = mapped_column(String(2000))
    last_issue_body_sha256: Mapped[str] = mapped_column(String(64))
    approval_invalidated: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    superseded_by_plan_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("frozen_research_plans.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ResearchWorkItem(Base):
    __tablename__ = "research_work_items"
    __table_args__ = (
        CheckConstraint(
            "status in ('queued', 'leased', 'running', 'succeeded', 'failed', 'interrupted')",
            name="ck_research_work_items_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_research_work_items_attempt_count"),
        CheckConstraint(
            "attempt_count <= max_attempts", name="ck_research_work_items_attempt_budget"
        ),
        CheckConstraint(
            "max_attempts between 1 and 3", name="ck_research_work_items_max_attempts"
        ),
        CheckConstraint(
            "((status in ('leased', 'running') and lease_owner is not null and "
            "lease_token is not null and lease_expires_at is not null) or "
            "(status not in ('leased', 'running') and lease_owner is null and "
            "lease_token is null and lease_expires_at is null))",
            name="ck_research_work_items_lease_shape",
        ),
        CheckConstraint(
            "status not in ('leased', 'running') or current_attempt_id is not null",
            name="ck_research_work_items_active_attempt",
        ),
        UniqueConstraint("formal_research_id", name="uq_research_work_items_formal"),
        SqlIndex("ix_research_work_items_queue", "status", "next_attempt_at", "created_at"),
        SqlIndex("ix_research_work_items_lease", "status", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    orchestration_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("research_orchestrations.id", ondelete="RESTRICT")
    )
    formal_research_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("formal_researches.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    attempt_count: Mapped[int] = mapped_column(default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(default=3, server_default="3")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_token: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_attempt_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    current_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_runs.run_id", ondelete="RESTRICT")
    )
    resume_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_runs.run_id", ondelete="RESTRICT")
    )
    stop_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_kind: Mapped[str | None] = mapped_column(String(80))
    last_error: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ResearchEvent(Base):
    __tablename__ = "research_events"
    __table_args__ = (
        UniqueConstraint(
            "formal_research_id", "sequence_no", name="uq_research_events_research_sequence"
        ),
        CheckConstraint("sequence_no > 0", name="ck_research_events_sequence"),
        SqlIndex("ix_research_events_run_occurred", "run_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    formal_research_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("formal_researches.id", ondelete="RESTRICT")
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_runs.run_id", ondelete="RESTRICT")
    )
    sequence_no: Mapped[int] = mapped_column()
    event_type: Mapped[str] = mapped_column(String(80))
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResearchEvaluation(Base):
    __tablename__ = "research_evaluations"
    __table_args__ = (
        CheckConstraint(
            "conclusion in ('研究通过', '有条件候选', '证据不足', '受阻', '不通过')",
            name="ck_research_evaluations_conclusion",
        ),
        CheckConstraint("version > 0", name="ck_research_evaluations_version"),
        UniqueConstraint(
            "formal_research_id", "version", name="uq_research_evaluations_research_version"
        ),
        UniqueConstraint("evaluation_sha256", name="uq_research_evaluations_sha256"),
        UniqueConstraint(
            "supersedes_evaluation_id", name="uq_research_evaluations_supersedes"
        ),
        SqlIndex("ix_research_evaluations_research_created", "formal_research_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    formal_research_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("formal_researches.id", ondelete="RESTRICT")
    )
    version: Mapped[int] = mapped_column()
    conclusion: Mapped[str] = mapped_column(String(16))
    evaluation_sha256: Mapped[str] = mapped_column(String(64))
    supersedes_evaluation_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("research_evaluations.id", ondelete="RESTRICT")
    )
    supporting_evidence: Mapped[list[dict]] = mapped_column(JSON, default=list)
    opposing_evidence: Mapped[list[dict]] = mapped_column(JSON, default=list)
    missing_evidence: Mapped[list[dict]] = mapped_column(JSON, default=list)
    limitations: Mapped[list[dict]] = mapped_column(JSON, default=list)
    follow_up_recommendations: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResearchEvaluationRun(Base):
    __tablename__ = "research_evaluation_runs"

    evaluation_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("research_evaluations.id", ondelete="RESTRICT"), primary_key=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("research_runs.run_id", ondelete="RESTRICT"), primary_key=True
    )


class ResearchEvidenceRef(Base):
    __tablename__ = "research_evidence_refs"
    __table_args__ = (
        CheckConstraint(
            "kind in ('input_snapshot', 'code', 'environment', 'parameters', "
            "'ledger', 'statistics', 'report', 'limitation')",
            name="ck_research_evidence_refs_kind",
        ),
        UniqueConstraint(
            "evaluation_id", "kind", "uri", name="uq_research_evidence_refs_evaluation_kind_uri"
        ),
        SqlIndex("ix_research_evidence_refs_run_kind", "run_id", "kind"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    evaluation_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("research_evaluations.id", ondelete="RESTRICT")
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_runs.run_id", ondelete="RESTRICT")
    )
    kind: Mapped[str] = mapped_column(String(24))
    uri: Mapped[str] = mapped_column(String(1000))
    sha256: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResearchPublication(Base):
    __tablename__ = "research_publications"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'published', 'failed')",
            name="ck_research_publications_status",
        ),
        CheckConstraint("version > 0", name="ck_research_publications_version"),
        UniqueConstraint(
            "formal_research_id", "version", name="uq_research_publications_research_version"
        ),
        UniqueConstraint("publication_sha256", name="uq_research_publications_sha256"),
        UniqueConstraint(
            "supersedes_publication_id", name="uq_research_publications_supersedes"
        ),
        SqlIndex("ix_research_publications_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    formal_research_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("formal_researches.id", ondelete="RESTRICT")
    )
    evaluation_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("research_evaluations.id", ondelete="RESTRICT")
    )
    version: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    publication_sha256: Mapped[str] = mapped_column(String(64))
    supersedes_publication_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("research_publications.id", ondelete="RESTRICT")
    )
    artifact_manifest_uri: Mapped[str] = mapped_column(String(1000))
    issue_number: Mapped[int] = mapped_column()
    issue_comment_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResearchPublicationIssueMapping(Base):
    __tablename__ = "research_publication_issue_mappings"
    __table_args__ = (
        CheckConstraint(
            "source = 'historical_import'",
            name="ck_research_publication_issue_mappings_source",
        ),
        UniqueConstraint(
            "issue_number", name="uq_research_publication_issue_mappings_issue"
        ),
    )

    formal_research_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("formal_researches.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    issue_number: Mapped[int] = mapped_column()
    source: Mapped[str] = mapped_column(
        String(24), default="historical_import", server_default="historical_import"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FollowUpResearchProposal(Base):
    __tablename__ = "follow_up_research_proposals"
    __table_args__ = (
        CheckConstraint(
            "status in ('proposed', 'accepted', 'rejected', 'converted')",
            name="ck_follow_up_research_proposals_status",
        ),
        UniqueConstraint("converted_plan_id", name="uq_follow_up_research_proposals_converted_plan"),
        SqlIndex("ix_follow_up_research_proposals_strategy_created", "strategy_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_definitions.strategy_id", ondelete="RESTRICT")
    )
    source_evaluation_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("research_evaluations.id", ondelete="RESTRICT")
    )
    source_evidence_ref_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("research_evidence_refs.id", ondelete="RESTRICT")
    )
    title: Mapped[str] = mapped_column(String(200))
    rationale: Mapped[str] = mapped_column(String(2000))
    status: Mapped[str] = mapped_column(String(16), default="proposed", server_default="proposed")
    proposal_json: Mapped[dict] = mapped_column(JSON)
    converted_plan_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("frozen_research_plans.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _reject_immutable_update(_mapper: object, _connection: object, target: Base) -> None:
    raise RuntimeError(f"{target.__tablename__} 是不可变研究记录，不可原地修改")


def _reject_immutable_delete(_mapper: object, _connection: object, target: Base) -> None:
    raise RuntimeError(f"{target.__tablename__} 是不可变研究记录，不可删除")


def _reject_terminal_publication_update(
    _mapper: object, _connection: object, target: ResearchPublication
) -> None:
    history = sa_inspect(target).attrs.status.history
    previous_status = history.deleted[0] if history.deleted else target.status
    if previous_status in {"published", "failed"}:
        raise RuntimeError("研究发布已终态，不可原地修改")


for _immutable_model in (
    FrozenResearchPlan,
    ResearchPlanApproval,
    ResearchEvent,
    ResearchEvaluation,
    ResearchEvaluationRun,
    ResearchEvidenceRef,
    ResearchPublicationIssueMapping,
):
    event.listen(_immutable_model, "before_update", _reject_immutable_update)
    event.listen(_immutable_model, "before_delete", _reject_immutable_delete)

event.listen(ResearchPublication, "before_update", _reject_terminal_publication_update)
event.listen(ResearchPublication, "before_delete", _reject_immutable_delete)
