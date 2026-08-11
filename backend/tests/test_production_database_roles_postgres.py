from __future__ import annotations

import asyncio
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from backend.app.database import alembic_config
from backend.app.personal_workspace.agent.evidence import (
    EvidenceLedgerError,
    PostgresEvidenceStore,
)
from backend.app.personal_workspace.crypto import FixedKeyring, PersonalDataCipher
from backend.app.personal_workspace.market_runtime import PersonalMarketReaders
from backend.app.personal_workspace.mcp_composition import (
    PersonalMcpConfig,
    PersonalMcpConfigurationError,
    build_personal_mcp_gateway,
)
from backend.app.personal_workspace.mcp_gateway import PERSONAL_MCP_HTTP_POLICY
from backend.app.personal_workspace.portfolio import PostgresPortfolioStore


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_SQL_DIR = REPO_ROOT / "scripts" / "ops" / "postgres_roles"
ROLE_MATRIX = {
    "quant_api_runtime": (True, False),
    "quant_research_runtime": (True, False),
    "quant_personal_api": (False, True),
    "quant_personal_analysis": (True, True),
    "quant_personal_mcp": (False, True),
}

MCP_READ_TABLES = (
    "personal_workspaces",
    "personal_holdings",
    "personal_instrument_states",
    "personal_rule_revisions",
    "personal_rule_evaluations",
    "personal_tool_evidence_records",
    "personal_capability_audit_events",
)
MCP_LEDGER_TABLES = (
    "personal_tool_evidence_records",
    "personal_capability_audit_events",
)


def _sql_artifact(name: str) -> str:
    sql = "\n".join(
        line
        for line in (ROLE_SQL_DIR / name).read_text(encoding="utf-8").splitlines()
        if not line.startswith("\\")
    )
    return sql.replace("%", "%%")


class ProductionDatabaseRoleArtifactTest(unittest.TestCase):
    def test_apply_readback_and_rollback_sql_are_reviewable_artifacts(self) -> None:
        expected = {
            "apply.sql",
            "readback.sql",
            "rollback.sql",
        }

        self.assertEqual(
            {path.name for path in ROLE_SQL_DIR.glob("*.sql")},
            expected,
        )

    def test_compose_exposes_current_runtime_database_urls(self) -> None:
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        for variable in (
            "API_DATABASE_URL",
            "PRIVATE_DATABASE_URL",
        ):
            self.assertIn(f"${{{variable}:-", compose)
        self.assertNotIn("RESEARCH_WORKER_DATABASE_URL", compose)

    def test_evidence_store_rejects_an_unknown_workspace_mode(self) -> None:
        cipher = PersonalDataCipher(
            FixedKeyring(
                active_key_id="mode-test-key",
                data_keys={"mode-test-key": bytes(range(32))},
                lookup_key=bytes(reversed(range(32))),
            )
        )

        with self.assertRaisesRegex(
            EvidenceLedgerError, "^invalid_evidence_workspace_mode$"
        ):
            PostgresEvidenceStore(
                lambda: None,  # type: ignore[arg-type]
                cipher=cipher,
                retention_by_authorization={},
                workspace_mode="existing-only",  # type: ignore[arg-type]
            )


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "TEST_POSTGRES_URL 未配置，跳过生产角色 PostgreSQL 集成测试")
class ProductionDatabaseRolePostgresIntegrationTest(unittest.TestCase):
    def test_runtime_roles_enforce_public_and_private_boundaries(self) -> None:
        admin_url = make_url(os.environ["TEST_POSTGRES_URL"])
        admin_engine = create_engine(admin_url, poolclass=NullPool)
        role_engines = []
        password = "synthetic-role-password-for-tests-only"
        applied = False
        try:
            with admin_engine.connect() as connection:
                command.upgrade(alembic_config(connection), "head")
            with admin_engine.begin() as connection:
                connection.exec_driver_sql(_sql_artifact("apply.sql"))
                applied = True
                for role_name in ROLE_MATRIX:
                    connection.exec_driver_sql(
                        f'ALTER ROLE "{role_name}" PASSWORD \'{password}\''
                    )
                password_hashes = dict(
                    connection.execute(
                        text("SELECT rolname, rolpassword FROM pg_authid WHERE rolname = ANY(:roles)"),
                        {"roles": list(ROLE_MATRIX)},
                    ).tuples().all()
                )
                connection.exec_driver_sql(
                    "GRANT quant_personal_api TO quant_personal_mcp"
                )
                connection.exec_driver_sql(_sql_artifact("apply.sql"))
                reapplied_password_hashes = dict(
                    connection.execute(
                        text("SELECT rolname, rolpassword FROM pg_authid WHERE rolname = ANY(:roles)"),
                        {"roles": list(ROLE_MATRIX)},
                    ).tuples().all()
                )
                self.assertEqual(reapplied_password_hashes, password_hashes)
                self.assertFalse(
                    connection.scalar(
                        text(
                            "SELECT pg_has_role('quant_personal_mcp', "
                            "'quant_personal_api', 'SET')"
                        )
                    )
                )
                self.assertFalse(
                    connection.scalar(
                        text(
                            "SELECT has_database_privilege('quant_personal_mcp', "
                            "current_database(), 'TEMPORARY')"
                        )
                    )
                )
                self.assertFalse(
                    connection.scalar(
                        text(
                            "SELECT has_schema_privilege('quant_personal_mcp', "
                            "'public', 'USAGE')"
                        )
                    )
                )
                connection.exec_driver_sql(_sql_artifact("readback.sql"))

                role_rows = {
                    row.rolname: row
                    for row in connection.execute(
                        text(
                            "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
                            "rolreplication, rolbypassrls "
                            "FROM pg_roles WHERE rolname = ANY(:roles)"
                        ),
                        {"roles": list(ROLE_MATRIX)},
                    )
                }
                self.assertEqual(set(role_rows), set(ROLE_MATRIX))
                for row in role_rows.values():
                    self.assertTrue(row.rolcanlogin)
                    self.assertFalse(row.rolsuper)
                    self.assertFalse(row.rolcreatedb)
                    self.assertFalse(row.rolcreaterole)
                    self.assertFalse(row.rolreplication)
                    self.assertFalse(row.rolbypassrls)

            for role_name, (can_read_public, can_read_private) in ROLE_MATRIX.items():
                role_url = admin_url.set(username=role_name, password=password)
                role_engine = create_engine(role_url, poolclass=NullPool)
                role_engines.append(role_engine)
                with role_engine.connect() as connection:
                    self.assertEqual(connection.scalar(text("SELECT current_user")), role_name)
                    if can_read_public:
                        connection.execute(text("SELECT count(*) FROM public.stocks"))
                    else:
                        with self.assertRaises(DBAPIError):
                            connection.execute(text("SELECT count(*) FROM public.stocks"))
                with role_engine.connect() as connection:
                    if can_read_private:
                        connection.execute(text("SELECT count(*) FROM private_workbench.personal_workspaces"))
                    else:
                        with self.assertRaises(DBAPIError):
                            connection.execute(
                                text("SELECT count(*) FROM private_workbench.personal_workspaces")
                            )
                with role_engine.connect() as connection:
                    with self.assertRaises(DBAPIError):
                        connection.execute(text(f"CREATE TABLE public.role_gate_{role_name} (id integer)"))

            mcp_engine = role_engines[list(ROLE_MATRIX).index("quant_personal_mcp")]
            with mcp_engine.connect() as connection:
                with self.assertRaises(DBAPIError):
                    connection.execute(text("SET ROLE quant_personal_api"))
            with mcp_engine.connect() as connection:
                with self.assertRaises(DBAPIError):
                    connection.execute(text("CREATE TEMP TABLE mcp_forbidden (id integer)"))
            with mcp_engine.connect() as connection:
                for table_name in MCP_READ_TABLES:
                    connection.execute(
                        text(f"SELECT count(*) FROM private_workbench.{table_name}")
                    )
                for table_name in MCP_LEDGER_TABLES:
                    self.assertTrue(
                        connection.scalar(
                            text(
                                "SELECT has_table_privilege(current_user, :table_name, 'INSERT')"
                            ),
                            {"table_name": f"private_workbench.{table_name}"},
                        )
                    )
                private_tables = tuple(
                    connection.scalars(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'private_workbench'"
                        )
                    )
                )
                for table_name in private_tables:
                    qualified_name = f"private_workbench.{table_name}"
                    self.assertEqual(
                        bool(
                            connection.scalar(
                                text(
                                    "SELECT has_table_privilege(current_user, :table_name, 'SELECT')"
                                ),
                                {"table_name": qualified_name},
                            )
                        ),
                        table_name in MCP_READ_TABLES,
                    )
                    for privilege in ("INSERT", "UPDATE", "DELETE"):
                        self.assertEqual(
                            bool(
                                connection.scalar(
                                    text(
                                        "SELECT has_table_privilege(current_user, :table_name, :privilege)"
                                    ),
                                    {
                                        "table_name": qualified_name,
                                        "privilege": privilege,
                                    },
                                )
                            ),
                            privilege == "INSERT" and table_name in MCP_LEDGER_TABLES,
                        )

            keyring = FixedKeyring(
                active_key_id="mcp-role-test-key",
                data_keys={"mcp-role-test-key": bytes(range(32))},
                lookup_key=bytes(reversed(range(32))),
            )
            cipher = PersonalDataCipher(keyring)
            actor_id = f"mcp-role-existing-{uuid4()}"
            admin_session = sessionmaker(bind=admin_engine, expire_on_commit=False)
            existing = PostgresPortfolioStore(admin_session, cipher=cipher).revise(
                actor_id=actor_id,
                expected_revision=0,
                idempotency_key="create-existing-workspace",
                action="set_cash",
                mutate=lambda _state: None,
            )
            self.assertIsNotNone(existing.workspace_id)
            market_readers = replace(
                PersonalMarketReaders.unavailable(), market=object()
            )
            role_url = admin_url.set(
                username="quant_personal_mcp", password=password
            ).render_as_string(hide_password=False)
            with tempfile.TemporaryDirectory() as temporary_directory:
                news_dir = Path(temporary_directory)
                (news_dir / "scripts").mkdir()
                (news_dir / "scripts" / "fetch.py").touch()

                def config(config_actor_id: str) -> PersonalMcpConfig:
                    return PersonalMcpConfig(
                        enabled=True,
                        actor_id=config_actor_id,
                        database_url=role_url,
                        keyring_file="/not-used/keyring.json",
                        alpaca_credentials_file="/not-used/alpaca.json",
                        alpaca_authorization_file="/not-used/authorization.json",
                        investment_news_dir=str(news_dir),
                    )

                with (
                    patch(
                        "backend.app.personal_workspace.crypto."
                        "load_owner_only_keyring_file",
                        return_value=keyring,
                    ),
                    patch(
                        "backend.app.personal_workspace.market_runtime."
                        "load_owner_only_personal_market_readers",
                        return_value=market_readers,
                    ),
                ):
                    gateway = build_personal_mcp_gateway(
                        config(actor_id), transport_policy=PERSONAL_MCP_HTTP_POLICY
                    )
                    asyncio.run(gateway.call_tool("get_today_context", {}))
                    gateway.close()

                    with admin_engine.connect() as connection:
                        workspace_count = connection.scalar(
                            text(
                                "SELECT count(*) FROM private_workbench.personal_workspaces"
                            )
                        )
                        evidence_count = connection.scalar(
                            text(
                                "SELECT count(*) FROM private_workbench."
                                "personal_tool_evidence_records WHERE workspace_id = :workspace_id"
                            ),
                            {"workspace_id": existing.workspace_id},
                        )
                        audit_count = connection.scalar(
                            text(
                                "SELECT count(*) FROM private_workbench."
                                "personal_capability_audit_events "
                                "WHERE workspace_id = :workspace_id "
                                "AND channel = 'mcp_streamable_http'"
                            ),
                            {"workspace_id": existing.workspace_id},
                        )
                        total_audit_count = connection.scalar(
                            text(
                                "SELECT count(*) FROM private_workbench."
                                "personal_capability_audit_events"
                            )
                        )
                    self.assertGreater(int(evidence_count or 0), 0)
                    self.assertEqual(audit_count, 1)

                    with self.assertRaisesRegex(
                        PersonalMcpConfigurationError,
                        "^personal_mcp_actor_unknown$",
                    ):
                        build_personal_mcp_gateway(config(f"unknown-{uuid4()}"))
                    with admin_engine.connect() as connection:
                        self.assertEqual(
                            connection.scalar(
                                text(
                                    "SELECT count(*) FROM private_workbench.personal_workspaces"
                                )
                            ),
                            workspace_count,
                        )
                        self.assertEqual(
                            connection.scalar(
                                text(
                                    "SELECT count(*) FROM private_workbench."
                                    "personal_capability_audit_events"
                                )
                            ),
                            total_audit_count,
                        )
        finally:
            for role_engine in role_engines:
                role_engine.dispose()
            if applied:
                with admin_engine.begin() as connection:
                    connection.exec_driver_sql(_sql_artifact("rollback.sql"))
            admin_engine.dispose()


if __name__ == "__main__":
    unittest.main()
