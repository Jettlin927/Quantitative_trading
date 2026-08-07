from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import unittest

from alembic import command
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from backend.app import models  # noqa: F401
from backend.app.models import PersonalPortfolioRevision
from backend.app.database import (
    PrivateBase,
    alembic_config,
    current_schema_heads,
    expected_schema_heads,
    schema_fingerprint,
)
from backend.app.personal_workspace.contracts import (
    AddHoldingCommand,
    BuyHoldingCommand,
    PersonalActor,
    PurgeHoldingCommand,
    SellHoldingCommand,
    SetUsdCashCommand,
)
from backend.app.personal_workspace.analysis import (
    AnalysisIntent,
    AnalysisWorkspace,
    EvidenceCandidate,
    PostgresAnalysisStore,
    ScriptedResponsesAdapter,
)
from backend.app.personal_workspace.crypto import (
    EncryptedEnvelope,
    FixedKeyring,
    PersonalDataCipher,
)
from backend.app.personal_workspace.journey import PersonalResearchJourney
from backend.app.personal_workspace.persistence import PostgresPersonalJourneyStore
from backend.app.personal_workspace.portfolio import (
    EquitySnapshot,
    HoldingState,
    PortfolioBook,
    PortfolioPriceObservation,
    PostgresEquitySnapshotStore,
    PostgresPortfolioStore,
    PostgresPriceObservationStore,
    PostgresRealizedTradeStore,
    UnavailablePortfolioMarketReader,
)
from backend.app.personal_workspace.synthetic import SyntheticWorkspaceAdapters


PRIVATE_TABLES = {
    "personal_workspaces",
    "personal_holdings",
    "personal_portfolio_revisions",
    "personal_audit_events",
    "personal_rule_evaluations",
    "personal_rule_instances",
    "personal_rule_revisions",
    "personal_rule_evaluation_batches",
    "personal_analysis_drafts",
    "personal_evidence_packs",
    "personal_evidence_refs",
    "personal_analysis_runs",
    "personal_analysis_attempts",
    "personal_analysis_events",
    "personal_ai_claims",
    "personal_record_versions",
    "personal_record_private_fragments",
    "personal_verification_items",
    "personal_verification_observations",
    "personal_redaction_events",
    "personal_research_records",
    "personal_price_observations",
    "personal_equity_snapshots",
    "personal_realized_trades",
}
RETIRED_RECORD_TABLES = {
    "personal_record_versions",
    "personal_record_private_fragments",
    "personal_verification_items",
    "personal_verification_observations",
    "personal_research_records",
}
ENCRYPTED_PRIVATE_TABLES = PRIVATE_TABLES - {
    "personal_audit_events",
    "personal_redaction_events",
}


class PersonalWorkspaceSchemaIdentityTest(unittest.TestCase):
    def test_private_orm_identity_is_separate_from_public_metadata(self) -> None:
        self.assertEqual(expected_schema_heads(), ("0020_drop_data_quality_registry",))
        self.assertEqual(
            set(PrivateBase.metadata.tables),
            {
                f"private_workbench.{table}"
                for table in PRIVATE_TABLES - RETIRED_RECORD_TABLES
            },
        )
        self.assertTrue(set(PrivateBase.metadata.tables).isdisjoint(models.Base.metadata.tables))


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "TEST_POSTGRES_URL 未配置，跳过私有 PostgreSQL 集成测试")
class PersonalWorkspacePostgresIntegrationTest(unittest.TestCase):
    def test_migration_creates_isolated_schema_with_fail_closed_public_grants(self) -> None:
        engine = create_engine(os.environ["TEST_POSTGRES_URL"])
        role_name = "synthetic_formal_research_role"
        try:
            with engine.connect() as connection:
                command.upgrade(alembic_config(connection), "head")
                command.upgrade(alembic_config(connection), "head")

            inspector = inspect(engine)
            with engine.connect() as connection:
                self.assertEqual(current_schema_heads(connection), expected_schema_heads())
                first_fingerprint = schema_fingerprint(connection, schema="private_workbench")
                second_fingerprint = schema_fingerprint(connection, schema="private_workbench")
            self.assertEqual(first_fingerprint, second_fingerprint)
            self.assertEqual(len(first_fingerprint["sha256"]), 64)
            self.assertEqual(set(first_fingerprint["tables"]), PRIVATE_TABLES)
            self.assertTrue(PRIVATE_TABLES.issubset(set(inspector.get_table_names(schema="private_workbench"))))

            cross_schema_foreign_keys = []
            for table in PRIVATE_TABLES:
                for foreign_key in inspector.get_foreign_keys(table, schema="private_workbench"):
                    if foreign_key.get("referred_schema") not in {None, "private_workbench"}:
                        cross_schema_foreign_keys.append((table, foreign_key))
            self.assertEqual(cross_schema_foreign_keys, [])

            with engine.begin() as connection:
                connection.execute(text(f"DROP ROLE IF EXISTS {role_name}"))
                connection.execute(text(f"CREATE ROLE {role_name} NOLOGIN"))
                schema_usage, table_select = connection.execute(
                    text(
                        "SELECT has_schema_privilege(:role, 'private_workbench', 'USAGE'), "
                        "has_table_privilege(:role, 'private_workbench.personal_holdings', 'SELECT')"
                    ),
                    {"role": role_name},
                ).one()
                self.assertFalse(schema_usage)
                self.assertFalse(table_select)
                connection.execute(text(f"DROP ROLE {role_name}"))
        finally:
            engine.dispose()

    def test_synthetic_journey_persists_only_encrypted_business_values(self) -> None:
        engine = create_engine(os.environ["TEST_POSTGRES_URL"])
        try:
            with engine.connect() as connection:
                command.upgrade(alembic_config(connection), "head")
            with engine.begin() as connection:
                for table in (
                    "personal_verification_observations",
                    "personal_verification_items",
                    "personal_record_private_fragments",
                    "personal_record_versions",
                    "personal_redaction_events",
                    "personal_research_records",
                    "personal_ai_claims",
                    "personal_analysis_events",
                    "personal_analysis_attempts",
                    "personal_analysis_runs",
                    "personal_evidence_refs",
                    "personal_evidence_packs",
                    "personal_analysis_drafts",
                    "personal_rule_evaluations",
                    "personal_rule_evaluation_batches",
                    "personal_rule_revisions",
                    "personal_rule_instances",
                    "personal_holdings",
                    "personal_workspaces",
                ):
                    connection.execute(text(f"DELETE FROM private_workbench.{table}"))

            cipher = PersonalDataCipher(
                FixedKeyring(
                    active_key_id="synthetic-key",
                    data_keys={"synthetic-key": bytes(range(32))},
                    lookup_key=b"synthetic-lookup-key-for-tests-only",
                )
            )
            journey = PersonalResearchJourney(
                store=PostgresPersonalJourneyStore(
                    sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
                ),
                cipher=cipher,
                adapters=SyntheticWorkspaceAdapters(provider_available=False),
            )
            actor = PersonalActor(actor_id="local-owner")
            trace = journey.create_synthetic_trace(
                actor,
                idempotency_key="pg-trace-001",
                question="PostgreSQL 合成问题正文",
            )
            analyses = AnalysisWorkspace(
                store=PostgresAnalysisStore(
                    sessionmaker(bind=engine, autoflush=False, expire_on_commit=False),
                    cipher=cipher,
                ),
                evidence_reader=lambda request_actor, intent: (
                    EvidenceCandidate(
                        evidence_id="sec:acme:revenue:2026q1",
                        kind="official_filing",
                        source="sec",
                        field="official_facts",
                        excerpt="ACME 2026-Q1 revenue was USD 100.",
                        content_sha256="a" * 64,
                        authorized_for_ai=True,
                        as_of=datetime(2026, 8, 3, 3, 0, tzinfo=timezone.utc),
                    ),
                ),
                provider=ScriptedResponsesAdapter.completed(claims=()),
                clock=lambda: datetime(2026, 8, 3, 4, 0, tzinfo=timezone.utc),
            )
            analyses.prepare(
                actor,
                AnalysisIntent(
                    question="真实分析草稿不能覆盖合成旅程。",
                    subject_ids=("ACME",),
                ),
                idempotency_key="pg-real-analysis-draft",
            )

            portfolio = PortfolioBook(
                store=PostgresPortfolioStore(
                    sessionmaker(bind=engine, autoflush=False, expire_on_commit=False),
                    cipher=cipher,
                ),
                market=UnavailablePortfolioMarketReader(),
                challenge_key=b"portfolio-challenge-key-for-tests" * 2,
            ).open(actor)

            today = journey.open_today(actor, include_synthetic=True)
            self.assertEqual(today.trace.analysis_id, trace.analysis_id)
            self.assertEqual(portfolio.holdings, ())
            with engine.connect() as connection:
                counts = {
                    table: connection.scalar(text(f"SELECT count(*) FROM private_workbench.{table}"))
                    for table in PRIVATE_TABLES
                }
                raw_ciphertexts = b"|".join(
                    bytes(value)
                    for table in ENCRYPTED_PRIVATE_TABLES
                    for value in connection.execute(
                        text(f"SELECT ciphertext FROM private_workbench.{table}")
                    ).scalars()
                )
                database_projection = "|".join(
                    value
                    for table in PRIVATE_TABLES
                    for value in connection.execute(
                        text(f"SELECT to_jsonb(row_value)::text FROM private_workbench.{table} AS row_value")
                    ).scalars()
                )
            self.assertEqual(
                counts,
                {
                    table: (
                        2
                        if table == "personal_analysis_drafts"
                        else 0
                        if table
                        in {
                            "personal_portfolio_revisions",
                            "personal_audit_events",
                            "personal_rule_instances",
                            "personal_rule_revisions",
                            "personal_rule_evaluation_batches",
                            "personal_analysis_runs",
                            "personal_analysis_attempts",
                            "personal_analysis_events",
                            "personal_ai_claims",
                            "personal_record_versions",
                            "personal_record_private_fragments",
                            "personal_verification_items",
                            "personal_verification_observations",
                            "personal_redaction_events",
                            "personal_price_observations",
                            "personal_equity_snapshots",
                            "personal_realized_trades",
                        }
                        | RETIRED_RECORD_TABLES
                        else 1
                    )
                    for table in PRIVATE_TABLES
                },
            )
            # 行情落盘与权益快照表在合成旅程中不应产生明文业务行：
            # 本场景行情不可用 → 无可用观察可落盘、权益不可计算 → 无快照。
            self.assertEqual(counts["personal_price_observations"], 0)
            self.assertEqual(counts["personal_equity_snapshots"], 0)
            self.assertNotIn(b"SYNTH-001", raw_ciphertexts)
            self.assertNotIn("PostgreSQL 合成问题正文".encode("utf-8"), raw_ciphertexts)
            self.assertNotIn("SYNTH-001", database_projection)
            self.assertNotIn("12.5000", database_projection)
            self.assertNotIn("PostgreSQL 合成问题正文", database_projection)
        finally:
            engine.dispose()

    def test_price_observation_and_equity_snapshot_round_trip_through_postgres(self) -> None:
        engine = create_engine(os.environ["TEST_POSTGRES_URL"])
        try:
            with engine.connect() as connection:
                command.upgrade(alembic_config(connection), "head")
            with engine.begin() as connection:
                for table in (
                    "personal_holdings",
                    "personal_workspaces",
                    "personal_price_observations",
                    "personal_equity_snapshots",
                ):
                    connection.execute(text(f"DELETE FROM private_workbench.{table}"))

            cipher = PersonalDataCipher(
                FixedKeyring(
                    active_key_id="synthetic-key",
                    data_keys={"synthetic-key": bytes(range(32))},
                    lookup_key=b"synthetic-lookup-key-for-tests-only",
                )
            )
            actor = PersonalActor(actor_id="local-owner")
            session_factory = sessionmaker(
                bind=engine, autoflush=False, expire_on_commit=False
            )
            store = PostgresPortfolioStore(session_factory, cipher=cipher)
            store.revise(
                actor_id=actor.actor_id,
                expected_revision=0,
                idempotency_key="pg-add-acme",
                action="add_holding",
                mutate=lambda state: state.holdings.__setitem__(
                    "holding-acme",
                    HoldingState(
                        holding_id="holding-acme",
                        symbol="ACME",
                        name="Acme Holdings",
                        quantity=Decimal("2"),
                        average_cost=Decimal("100.25"),
                    ),
                ),
            )

            observed_at = datetime(2026, 8, 3, 20, 5, tzinfo=timezone.utc)
            prices = PostgresPriceObservationStore(session_factory, cipher=cipher)
            prices.upsert(
                actor_id=actor.actor_id,
                observations={
                    "ACME": PortfolioPriceObservation(
                        availability="available",
                        price=Decimal("120.5000"),
                        reason_code=None,
                        source_health="fresh",
                        as_of=observed_at,
                        feed="delayed_sip",
                        delay_seconds=900,
                        source_ids=(),
                    )
                },
            )
            latest = prices.latest(actor_id=actor.actor_id, symbols=["ACME", "MISSING"])
            self.assertEqual(set(latest), {"ACME"})
            self.assertEqual(latest["ACME"].price, Decimal("120.5000"))
            self.assertEqual(latest["ACME"].feed, "delayed_sip")
            self.assertTrue(latest["ACME"].cached)
            self.assertEqual(latest["ACME"].source_health, "stale")

            snapshots = PostgresEquitySnapshotStore(session_factory, cipher=cipher)
            snapshots.upsert(
                actor_id=actor.actor_id,
                snapshot=EquitySnapshot(
                    market_day=observed_at.date(),
                    total_equity=Decimal("241.0000"),
                    total_market_value=Decimal("241.0000"),
                    usd_cash=Decimal("0"),
                    holdings_count=1,
                    priced_count=1,
                    after_close=True,
                    observed_at=observed_at,
                    payload={"holdings": [], "prices": {}},
                ),
            )
            history = snapshots.history(actor_id=actor.actor_id, limit=10)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].total_equity, "241.0000")
            self.assertEqual(history[0].market_day, observed_at.date().isoformat())
            self.assertTrue(history[0].after_close)

            with engine.connect() as connection:
                projection = "|".join(
                    connection.execute(
                        text(
                            "SELECT to_jsonb(row_value)::text FROM private_workbench."
                            "personal_price_observations AS row_value"
                        )
                    ).scalars().all()
                )
            self.assertNotIn("120.5000", projection)
            self.assertNotIn("delayed_sip", projection)
        finally:
            engine.dispose()

    def test_realized_trade_round_trip_through_postgres(self) -> None:
        engine = create_engine(os.environ["TEST_POSTGRES_URL"])
        try:
            with engine.connect() as connection:
                command.upgrade(alembic_config(connection), "head")
            with engine.begin() as connection:
                for table in (
                    "personal_holdings",
                    "personal_workspaces",
                    "personal_portfolio_revisions",
                    "personal_realized_trades",
                ):
                    connection.execute(text(f"DELETE FROM private_workbench.{table}"))

            cipher = PersonalDataCipher(
                FixedKeyring(
                    active_key_id="synthetic-key",
                    data_keys={"synthetic-key": bytes(range(32))},
                    lookup_key=b"synthetic-lookup-key-for-tests-only",
                )
            )
            actor = PersonalActor(actor_id="local-owner")
            session_factory = sessionmaker(
                bind=engine, autoflush=False, expire_on_commit=False
            )
            portfolio = PortfolioBook(
                store=PostgresPortfolioStore(session_factory, cipher=cipher),
                market=UnavailablePortfolioMarketReader(),
                trades=PostgresRealizedTradeStore(session_factory, cipher=cipher),
                challenge_key=b"portfolio-challenge-key-for-tests" * 2,
                clock=lambda: datetime(2026, 8, 5, 14, 30, tzinfo=timezone.utc),
            )
            portfolio.revise(
                actor,
                AddHoldingCommand(
                    type="add_holding",
                    symbol="ACME",
                    name="Acme Holdings",
                    quantity="4",
                    average_cost="100.25",
                    expected_portfolio_revision=0,
                ),
                idempotency_key="pg-add-acme",
            )
            sold = portfolio.revise(
                actor,
                SellHoldingCommand(
                    type="sell_holding",
                    holding_id=portfolio.open(actor).holdings[0].holding_id,
                    quantity="1.5",
                    price="120.00",
                    expected_portfolio_revision=1,
                ),
                idempotency_key="pg-sell-acme",
            )
            self.assertEqual(sold.usd_cash, "-221.0000")  # 首笔 4×100.25=−401，卖出 +1.5×120=+180
            self.assertEqual(sold.holdings[0].quantity, "2.5000")
            self.assertEqual(sold.holdings[0].state, "active")
            self.assertEqual(sold.realized_pnl_total.value, "29.6250")  # (120-100.25)×1.5
            self.assertEqual(sold.realized_trades[0].symbol, "ACME")
            self.assertEqual(sold.realized_trades[0].realized_pnl, "29.6250")

            # 幂等重放不重复落盘
            portfolio.revise(
                actor,
                SellHoldingCommand(
                    type="sell_holding",
                    holding_id=sold.holdings[0].holding_id,
                    quantity="1.5",
                    price="120.00",
                    expected_portfolio_revision=2,
                ),
                idempotency_key="pg-sell-acme",
            )

            # 全新 store 实例读回，验证持久化与加密
            trades = PostgresRealizedTradeStore(session_factory, cipher=cipher)
            recent = trades.recent(actor_id=actor.actor_id, limit=10)
            self.assertEqual(len(recent), 1)
            self.assertEqual(recent[0].symbol, "ACME")
            self.assertEqual(recent[0].shares, Decimal("1.5"))
            self.assertEqual(recent[0].price, Decimal("120.00"))
            self.assertEqual(recent[0].proceeds, Decimal("180.0000"))
            self.assertEqual(recent[0].cost_basis, Decimal("150.3750"))
            self.assertEqual(recent[0].realized_pnl, Decimal("29.6250"))
            self.assertEqual(trades.total(actor_id=actor.actor_id), Decimal("29.6250"))

            # 全量卖出 → 状态 sold，数量 0
            holding_id = sold.holdings[0].holding_id
            closed = portfolio.revise(
                actor,
                SellHoldingCommand(
                    type="sell_holding",
                    holding_id=holding_id,
                    quantity="2.5",
                    price="130",
                    expected_portfolio_revision=2,
                ),
                idempotency_key="pg-sell-acme-rest",
            )
            closed_holding = next(
                item for item in closed.holdings if item.holding_id == holding_id
            )
            self.assertEqual(closed_holding.state, "sold")
            self.assertEqual(closed_holding.quantity, "0.0000")
            # 累计已实现 = 29.6250 + (130−100.25)×2.5 = 104.0000
            self.assertEqual(trades.total(actor_id=actor.actor_id), Decimal("104.0000"))

            # 私有业务值不落明文：成交价/均价不在数据库投影中
            with engine.connect() as connection:
                projection = "|".join(
                    connection.execute(
                        text(
                            "SELECT to_jsonb(row_value)::text FROM private_workbench."
                            "personal_realized_trades AS row_value"
                        )
                    ).scalars().all()
                )
                hmacs = connection.execute(
                    text(
                        "SELECT symbol_hmac FROM private_workbench.personal_realized_trades"
                    )
                ).scalars().all()
            self.assertEqual(len(hmacs), 2)
            self.assertEqual(hmacs[0], cipher.symbol_lookup(workspace_id=portfolio.open(actor).workspace_id, normalized_symbol="ACME"))
            self.assertNotIn("120.00", projection)
            self.assertNotIn("29.6250", projection)
            self.assertNotIn("130.0000", projection)
            self.assertNotIn("ACME", projection)
        finally:
            engine.dispose()

    def test_portfolio_current_and_immutable_revision_are_atomic_under_concurrent_writes(self) -> None:
        engine = create_engine(os.environ["TEST_POSTGRES_URL"])
        try:
            with engine.connect() as connection:
                command.upgrade(alembic_config(connection), "head")
            with engine.begin() as connection:
                for table in (
                    "personal_verification_observations",
                    "personal_verification_items",
                    "personal_record_private_fragments",
                    "personal_record_versions",
                    "personal_redaction_events",
                    "personal_audit_events",
                    "personal_portfolio_revisions",
                    "personal_research_records",
                    "personal_ai_claims",
                    "personal_analysis_events",
                    "personal_analysis_attempts",
                    "personal_analysis_runs",
                    "personal_evidence_refs",
                    "personal_evidence_packs",
                    "personal_analysis_drafts",
                    "personal_rule_evaluations",
                    "personal_rule_evaluation_batches",
                    "personal_rule_revisions",
                    "personal_rule_instances",
                    "personal_holdings",
                    "personal_workspaces",
                ):
                    connection.execute(text(f"DELETE FROM private_workbench.{table}"))

            cipher = PersonalDataCipher(
                FixedKeyring(
                    active_key_id="portfolio-key",
                    data_keys={"portfolio-key": bytes(range(32))},
                    lookup_key=b"portfolio-lookup-key-for-tests-only",
                )
            )
            session_factory = sessionmaker(
                bind=engine, autoflush=False, expire_on_commit=False
            )

            class FixedMarket:
                def observe_price(self, symbol: str) -> PortfolioPriceObservation:
                    return PortfolioPriceObservation.available(
                        price=Decimal("120.50"),
                        source_health="fresh",
                        as_of=datetime(2026, 8, 3, 2, 45, tzinfo=timezone.utc),
                        feed="sip",
                        delay_seconds=900,
                        source_ids=("alpaca-acme",),
                    )

            book = PortfolioBook(
                store=PostgresPortfolioStore(session_factory, cipher=cipher),
                market=FixedMarket(),
                clock=lambda: datetime(2026, 8, 3, 3, 0, tzinfo=timezone.utc),
                challenge_key=b"portfolio-challenge-key-for-tests" * 2,
            )
            actor = PersonalActor(actor_id="portfolio-owner")
            created = book.revise(
                actor,
                AddHoldingCommand(
                    type="add_holding",
                    symbol="ACME",
                    name="Acme Private Holdings",
                    quantity="2",
                    average_cost="100.25",
                    expected_portfolio_revision=0,
                ),
                idempotency_key="pg-add-acme",
            )

            def update_cash(value: str, key: str):
                try:
                    return book.revise(
                        actor,
                        SetUsdCashCommand(
                            type="set_usd_cash",
                            usd_cash=value,
                            expected_portfolio_revision=1,
                        ),
                        idempotency_key=key,
                    )
                except ValueError as exc:
                    return str(exc)

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(
                    executor.map(
                        lambda args: update_cash(*args),
                        (("50", "pg-cash-50"), ("60", "pg-cash-60")),
                    )
                )

            self.assertEqual(created.portfolio_revision, 1)
            self.assertEqual(sum(result == "revision_conflict" for result in outcomes), 1)
            self.assertEqual(sum(not isinstance(result, str) for result in outcomes), 1)
            readback = book.open(actor)
            self.assertEqual(readback.portfolio_revision, 2)
            self.assertIn(readback.usd_cash, {"50.0000", "60.0000"})

            with engine.connect() as connection:
                holding_id = connection.scalar(
                    text("SELECT id FROM private_workbench.personal_holdings")
                )
                revision_count = connection.scalar(
                    text("SELECT count(*) FROM private_workbench.personal_portfolio_revisions")
                )
                raw_projection = "|".join(
                    connection.execute(
                        text(
                            "SELECT to_jsonb(row_value)::text "
                            "FROM private_workbench.personal_holdings AS row_value "
                            "UNION ALL "
                            "SELECT to_jsonb(row_value)::text "
                            "FROM private_workbench.personal_portfolio_revisions AS row_value "
                            "UNION ALL "
                            "SELECT to_jsonb(row_value)::text "
                            "FROM private_workbench.personal_workspaces AS row_value"
                        )
                    ).scalars()
                )
            self.assertEqual(revision_count, 2)
            for private_value in ("ACME", "Acme Private Holdings", "100.25"):
                self.assertNotIn(private_value, raw_projection)

            challenge = book.request_purge(
                actor,
                holding_id=holding_id,
                expected_portfolio_revision=2,
            )
            receipt = book.purge(
                actor,
                PurgeHoldingCommand(
                    holding_id=holding_id,
                    expected_portfolio_revision=2,
                    challenge=challenge.challenge,
                ),
                idempotency_key="pg-purge-acme",
            )
            with engine.connect() as connection:
                holding_count = connection.scalar(
                    text("SELECT count(*) FROM private_workbench.personal_holdings")
                )
                holding_revision_count = connection.scalar(
                    text(
                        "SELECT count(*) FROM private_workbench.personal_portfolio_revisions "
                        "WHERE holding_id = :holding_id"
                    ),
                    {"holding_id": holding_id},
                )
                audit_count = connection.scalar(
                    text("SELECT count(*) FROM private_workbench.personal_audit_events")
                )
            self.assertEqual(receipt.portfolio_revision, 3)
            self.assertEqual(holding_count, 0)
            self.assertEqual(holding_revision_count, 0)
            self.assertEqual(audit_count, 1)
            with session_factory() as session:
                remaining_revisions = session.scalars(
                    select(PersonalPortfolioRevision)
                ).all()
                remaining_payloads = [
                    cipher.decrypt_json(
                        EncryptedEnvelope(
                            ciphertext=bytes(row.ciphertext),
                            nonce=bytes(row.nonce),
                            key_id=row.key_id,
                            payload_schema=row.payload_schema,
                        ),
                        aad=(
                            "private_workbench|personal_portfolio_revisions|"
                            f"{row.id}|payload|1"
                        ),
                    )
                    for row in remaining_revisions
                ]
            self.assertEqual(len(remaining_payloads), 1)
            self.assertNotIn("holdings", remaining_payloads[0]["before"])
            self.assertNotIn("holdings", remaining_payloads[0]["after"])
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
