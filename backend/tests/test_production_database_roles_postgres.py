from __future__ import annotations

import os
from pathlib import Path
import unittest

from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from backend.app.database import alembic_config


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_SQL_DIR = REPO_ROOT / "scripts" / "ops" / "postgres_roles"
ROLE_MATRIX = {
    "quant_api_runtime": (True, False),
    "quant_sync_runtime": (True, False),
    "quant_research_runtime": (True, False),
    "quant_personal_api": (False, True),
    "quant_personal_analysis": (True, True),
}


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

    def test_compose_exposes_a_distinct_database_url_per_runtime(self) -> None:
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        for variable in (
            "API_DATABASE_URL",
            "SYNC_WORKER_DATABASE_URL",
            "RESEARCH_WORKER_DATABASE_URL",
            "PRIVATE_DATABASE_URL",
        ):
            self.assertIn(f"${{{variable}:-", compose)


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
                connection.exec_driver_sql(_sql_artifact("apply.sql"))
                reapplied_password_hashes = dict(
                    connection.execute(
                        text("SELECT rolname, rolpassword FROM pg_authid WHERE rolname = ANY(:roles)"),
                        {"roles": list(ROLE_MATRIX)},
                    ).tuples().all()
                )
                self.assertEqual(reapplied_password_hashes, password_hashes)

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
        finally:
            for role_engine in role_engines:
                role_engine.dispose()
            if applied:
                with admin_engine.begin() as connection:
                    connection.exec_driver_sql(_sql_artifact("rollback.sql"))
            admin_engine.dispose()


if __name__ == "__main__":
    unittest.main()
