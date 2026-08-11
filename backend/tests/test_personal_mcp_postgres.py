from __future__ import annotations

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.app.database import alembic_config
from backend.app.models import PersonalWorkspace
from backend.app.personal_workspace.agent.domain_tools import DomainToolRegistry
from backend.app.personal_workspace.agent.evidence import InMemoryEvidenceStore
from backend.app.personal_workspace.crypto import FixedKeyring, PersonalDataCipher
from backend.app.personal_workspace.mcp_server import (
    PersonalMcpConfig,
    PersonalMcpConfigurationError,
    build_personal_mcp_gateway,
)
from backend.app.personal_workspace.portfolio import PostgresPortfolioStore


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要隔离 PostgreSQL")
class PersonalMcpActorGatePostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(
            os.environ["TEST_POSTGRES_URL"], pool_pre_ping=True
        )
        with cls.engine.connect() as connection:
            command.upgrade(alembic_config(connection), "head")
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False)
        cls.store = PostgresPortfolioStore(
            cls.Session,
            cipher=PersonalDataCipher(
                FixedKeyring(
                    active_key_id="mcp-actor-gate-key",
                    data_keys={"mcp-actor-gate-key": bytes(range(32))},
                    lookup_key=bytes(reversed(range(32))),
                )
            ),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def _workspace_count(self) -> int:
        with self.Session() as session:
            return int(session.scalar(select(func.count(PersonalWorkspace.id))) or 0)

    def _build(self, actor_id: str):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        news_dir = Path(temporary_directory.name)
        (news_dir / "scripts").mkdir()
        (news_dir / "scripts" / "fetch.py").touch()
        audit_store = InMemoryEvidenceStore(retention_by_authorization={})
        services = SimpleNamespace(
            portfolio_store=self.store,
            market_readers=SimpleNamespace(market=object()),
            domain_tools=DomainToolRegistry(handlers={}),
            evidence_store=audit_store,
        )
        config = PersonalMcpConfig(
            enabled=True,
            actor_id=actor_id,
            database_url=os.environ["TEST_POSTGRES_URL"],
            keyring_file="/not-used/keyring.json",
            alpaca_credentials_file="/not-used/alpaca.json",
            alpaca_authorization_file="/not-used/authorization.json",
            investment_news_dir=str(news_dir),
        )
        return config, services, audit_store

    def test_unknown_actor_is_rejected_without_creating_workspace_or_audit(self) -> None:
        actor_id = f"mcp-unknown-{uuid4()}"
        config, services, audit_store = self._build(actor_id)
        before = self._workspace_count()

        with (
            patch(
                "backend.app.personal_workspace.crypto.load_keyring_file",
                return_value=object(),
            ),
            patch(
                "backend.app.personal_workspace.composition.build_personal_services",
                return_value=services,
            ),
        ):
            with self.assertRaisesRegex(
                PersonalMcpConfigurationError,
                "^personal_mcp_actor_unknown$",
            ):
                build_personal_mcp_gateway(config)

        self.assertIsNone(self.store.load(actor_id=actor_id).workspace_id)
        self.assertEqual(self._workspace_count(), before)
        self.assertEqual(audit_store.audits_for_actor(actor_id), ())

    def test_existing_actor_can_build_gateway_without_creating_workspace(self) -> None:
        actor_id = f"mcp-existing-{uuid4()}"
        existing = self.store.revise(
            actor_id=actor_id,
            expected_revision=0,
            idempotency_key="create-existing-workspace",
            action="set_cash",
            mutate=lambda _state: None,
        )
        config, services, _audit_store = self._build(actor_id)
        before = self._workspace_count()

        with (
            patch(
                "backend.app.personal_workspace.crypto.load_keyring_file",
                return_value=object(),
            ),
            patch(
                "backend.app.personal_workspace.composition.build_personal_services",
                return_value=services,
            ),
        ):
            gateway = build_personal_mcp_gateway(config)
            gateway.close()

        self.assertIsNotNone(existing.workspace_id)
        self.assertEqual(
            self.store.load(actor_id=actor_id).workspace_id,
            existing.workspace_id,
        )
        self.assertEqual(self._workspace_count(), before)


if __name__ == "__main__":
    unittest.main()
