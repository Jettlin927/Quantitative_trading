from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import os
import unittest

from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.app.database import alembic_config, current_schema_heads, expected_schema_heads
from backend.app.personal_workspace.contracts import PersonalActor
from backend.app.personal_workspace.crypto import FixedKeyring, PersonalDataCipher
from backend.app.personal_workspace.portfolio import HoldingState, PostgresPortfolioStore
from backend.app.personal_workspace.watchlist import (
    CandidateEvidence,
    FollowSymbol,
    HoldingWatchState,
    InstrumentStateBook,
    PostgresInstrumentStateStore,
)


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要隔离 PostgreSQL")
class PersonalWatchlistPostgresTest(unittest.TestCase):
    def test_encrypted_round_trip_idempotency_and_optimistic_concurrency(self) -> None:
        engine = create_engine(os.environ["TEST_POSTGRES_URL"], pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                command.upgrade(alembic_config(connection), "head")
                command.upgrade(alembic_config(connection), "head")
                self.assertEqual(current_schema_heads(connection), expected_schema_heads())
            with engine.begin() as connection:
                for actor_id in (
                    "watchlist-pg-owner",
                    "watchlist-pg-first-write-owner",
                    "watchlist-pg-first-idempotent-owner",
                ):
                    connection.execute(
                        text(
                            "DELETE FROM private_workbench.personal_workspaces "
                            "WHERE actor_identity_hash = :actor_hash"
                        ),
                        {"actor_hash": sha256(actor_id.encode()).hexdigest()},
                    )

            cipher = PersonalDataCipher(
                FixedKeyring(
                    active_key_id="watchlist-key",
                    data_keys={"watchlist-key": bytes(range(32))},
                    lookup_key=b"watchlist-postgres-lookup-key-for-tests",
                )
            )
            sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
            portfolio = PostgresPortfolioStore(sessions, cipher=cipher)
            portfolio.revise(
                actor_id="watchlist-pg-owner",
                expected_revision=0,
                idempotency_key="seed-holding",
                action="add_holding",
                mutate=lambda state: state.holdings.__setitem__(
                    "holding-nvda",
                    HoldingState(
                        holding_id="holding-nvda",
                        symbol="NVDA",
                        name="NVIDIA",
                        quantity=Decimal("1"),
                        average_cost=Decimal("100"),
                    ),
                ),
            )
            book = InstrumentStateBook(
                store=PostgresInstrumentStateStore(sessions, cipher=cipher),
                holding_states_reader=lambda actor_id: {
                    holding.symbol: HoldingWatchState(
                        holding.state, holding.revision
                    )
                    for holding in portfolio.load(actor_id=actor_id).holdings.values()
                },
            )
            actor = PersonalActor("watchlist-pg-owner")

            followed = book.revise(
                actor,
                FollowSymbol(
                    symbol="MSFT",
                    expected_revision=0,
                    preset_reasons=("财报观察",),
                ),
                idempotency_key="follow-msft",
            )
            repeated = book.revise(
                actor,
                FollowSymbol(
                    symbol="MSFT",
                    expected_revision=0,
                    preset_reasons=("财报观察",),
                ),
                idempotency_key="follow-msft",
            )
            candidate = book.consider_candidate(
                actor,
                CandidateEvidence(
                    symbol="AMD",
                    relation_evidence_ids=("relation:semis",),
                    fact_evidence_ids=("fact:earnings",),
                    observed_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
                    expected_revision=1,
                ),
                idempotency_key="candidate-amd",
            )

            self.assertEqual(repeated, followed)
            self.assertEqual(candidate.revision, 2)
            self.assertTrue(
                next(item for item in candidate.items if item.symbol == "NVDA").is_holding
            )
            self.assertFalse(
                next(item for item in candidate.items if item.symbol == "AMD").is_followed
            )

            def follow(symbol: str) -> str:
                try:
                    book.revise(
                        actor,
                        FollowSymbol(
                            symbol=symbol,
                            expected_revision=2,
                            preset_reasons=("并发验证",),
                        ),
                        idempotency_key=f"follow-{symbol.lower()}",
                    )
                except ValueError as exc:
                    return str(exc)
                return "ok"

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(follow, ("AAPL", "GOOG")))
            self.assertEqual(sorted(outcomes), ["ok", "revision_conflict"])

            first_write_book = InstrumentStateBook(
                store=PostgresInstrumentStateStore(sessions, cipher=cipher),
                holding_states_reader=lambda _actor_id: {},
            )
            first_write_actor = PersonalActor("watchlist-pg-first-write-owner")

            def first_write(symbol: str) -> str:
                try:
                    first_write_book.revise(
                        first_write_actor,
                        FollowSymbol(
                            symbol=symbol,
                            expected_revision=0,
                            preset_reasons=("首次并发",),
                        ),
                        idempotency_key=f"first-{symbol.lower()}",
                    )
                except ValueError as exc:
                    return str(exc)
                return "ok"

            with ThreadPoolExecutor(max_workers=2) as executor:
                first_write_outcomes = list(
                    executor.map(first_write, ("META", "AMZN"))
                )
            self.assertEqual(
                sorted(first_write_outcomes), ["ok", "revision_conflict"]
            )

            idempotent_actor = PersonalActor(
                "watchlist-pg-first-idempotent-owner"
            )

            def idempotent_first_write(_attempt: int):
                return first_write_book.revise(
                    idempotent_actor,
                    FollowSymbol(
                        symbol="TSLA",
                        expected_revision=0,
                        preset_reasons=("首次幂等",),
                    ),
                    idempotency_key="same-first-write",
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                idempotent_results = list(
                    executor.map(idempotent_first_write, (1, 2))
                )
            self.assertEqual(idempotent_results[0], idempotent_results[1])
            self.assertEqual(idempotent_results[0].revision, 1)

            reopened = InstrumentStateBook(
                store=PostgresInstrumentStateStore(sessions, cipher=cipher),
                holding_states_reader=lambda actor_id: {
                    holding.symbol: HoldingWatchState(
                        holding.state, holding.revision
                    )
                    for holding in portfolio.load(actor_id=actor_id).holdings.values()
                },
            ).open(actor)
            self.assertEqual(reopened.revision, 3)
            with engine.connect() as connection:
                projection = "|".join(
                    connection.execute(
                        text(
                            "SELECT to_jsonb(row_value)::text "
                            "FROM private_workbench.personal_instrument_states AS row_value"
                        )
                    ).scalars()
                )
                revision_count = connection.scalar(
                    text(
                        "SELECT count(*) "
                        "FROM private_workbench.personal_instrument_revisions AS revisions "
                        "JOIN private_workbench.personal_workspaces AS workspaces "
                        "ON workspaces.id = revisions.workspace_id "
                        "WHERE workspaces.actor_identity_hash = :actor_hash"
                    ),
                    {
                        "actor_hash": sha256(
                            b"watchlist-pg-owner"
                        ).hexdigest()
                    },
                )
            self.assertEqual(revision_count, 3)
            for private_value in ("MSFT", "AMD", "AAPL", "GOOG", "财报观察"):
                self.assertNotIn(private_value, projection)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
