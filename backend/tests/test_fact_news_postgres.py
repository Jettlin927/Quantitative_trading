from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import unittest
from uuid import uuid4

from alembic import command
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database import alembic_config
from backend.app.personal_workspace.agent.domain_tools import DomainToolContext
from backend.app.personal_workspace.agent.evidence import PostgresEvidenceStore
from backend.app.personal_workspace.agent.fact_news import (
    FACT_NEWS_AUTHORIZATION_SNAPSHOT_ID,
    FACT_NEWS_SOURCE,
    FactNewsReadContext,
    NewsSourceSnapshot,
    RawFactNews,
)
from backend.app.personal_workspace.agent.today_tools import TodayDomainTools
from backend.app.personal_workspace.crypto import FixedKeyring, PersonalDataCipher


NOW = datetime(2026, 8, 11, 6, 0, tzinfo=timezone.utc)


@dataclass
class _SyntheticSource:
    snapshot: NewsSourceSnapshot

    def read(
        self, *, context: FactNewsReadContext, now: datetime
    ) -> NewsSourceSnapshot:
        return self.snapshot


def _snapshot(*, persistence: str = "encrypted_payload") -> NewsSourceSnapshot:
    return NewsSourceSnapshot(
        items=(
            RawFactNews(
                title="AMD 发布结构化更新",
                url="https://wire.example/amd-update",
                published_at=NOW - timedelta(hours=1),
                fetched_at=NOW - timedelta(minutes=1),
                summary="AMD 来源摘要。",
                source="Synthetic Wire",
                source_type="structured_news",
                sector="semi",
                related_symbols=("AMD",),
            ),
        ),
        persistence=persistence,  # type: ignore[arg-type]
    )


def _context(actor_id: str, *, now: datetime = NOW) -> DomainToolContext:
    return DomainToolContext(
        actor_id=actor_id,
        granted_permissions=frozenset({"news:read", "evidence:read"}),
        purpose="domain_tool",
        clock=lambda: now,
    )


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要隔离 PostgreSQL")
class FactNewsPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(os.environ["TEST_POSTGRES_URL"], pool_pre_ping=True)
        with cls.engine.connect() as connection:
            command.upgrade(alembic_config(connection), "head")
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False)
        cls.cipher = PersonalDataCipher(
            FixedKeyring(
                active_key_id="fact-news-key",
                data_keys={"fact-news-key": bytes(range(32))},
                lookup_key=b"f" * 32,
            )
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def _store(self, persistence: str) -> PostgresEvidenceStore:
        return PostgresEvidenceStore(
            self.Session,
            cipher=self.cipher,
            retention_by_authorization={
                (FACT_NEWS_SOURCE, FACT_NEWS_AUTHORIZATION_SNAPSHOT_ID): persistence
            },
        )

    def test_search_then_restart_reads_same_actor_without_source(self) -> None:
        actor = f"fact-news-{uuid4()}"
        first = TodayDomainTools(
            portfolio_store=object(),
            watchlist=object(),
            news_source=_SyntheticSource(_snapshot()),
            evidence_ledger=self._store("encrypted_payload"),
        ).registry()
        searched = first.invoke(
            "search_market_news",
            context=_context(actor),
            arguments={"symbols": ["AMD"]},
        )
        evidence_id = searched.data["items"][0]["evidence_id"]

        restarted = TodayDomainTools(
            portfolio_store=object(),
            watchlist=object(),
            news_source=None,
            evidence_ledger=self._store("encrypted_payload"),
        ).registry()
        read = restarted.invoke(
            "get_evidence",
            context=_context(actor),
            arguments={"evidence_id": evidence_id},
        )
        other_actor = restarted.invoke(
            "get_evidence",
            context=_context(f"other-{uuid4()}"),
            arguments={"evidence_id": evidence_id},
        )
        expired = restarted.invoke(
            "get_evidence",
            context=_context(actor, now=NOW + timedelta(hours=3)),
            arguments={"evidence_id": evidence_id},
        )

        self.assertEqual(read.status, "success")
        self.assertEqual(read.data["evidence_id"], evidence_id)
        self.assertEqual(other_actor.error_code, "evidence_not_found")
        self.assertEqual(expired.error_code, "evidence_expired")

    def test_metadata_only_retention_returns_stable_typed_gap_after_restart(self) -> None:
        actor = f"fact-news-metadata-{uuid4()}"
        registry = TodayDomainTools(
            portfolio_store=object(),
            watchlist=object(),
            news_source=_SyntheticSource(_snapshot(persistence="metadata_only")),
            evidence_ledger=self._store("metadata_only"),
        ).registry()
        searched = registry.invoke(
            "search_market_news",
            context=_context(actor),
            arguments={"symbols": ["AMD"]},
        )
        evidence_id = searched.data["items"][0]["evidence_id"]

        restarted = TodayDomainTools(
            portfolio_store=object(),
            watchlist=object(),
            news_source=None,
            evidence_ledger=self._store("metadata_only"),
        ).registry()
        read = restarted.invoke(
            "get_evidence",
            context=_context(actor),
            arguments={"evidence_id": evidence_id},
        )

        self.assertEqual(read.status, "unavailable")
        self.assertEqual(read.error_code, "evidence_payload_not_retained")


if __name__ == "__main__":
    unittest.main()
