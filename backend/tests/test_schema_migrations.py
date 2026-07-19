from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from backend.app import main, models  # noqa: F401  # Populate Base.metadata.
from backend.app.database import (
    BASELINE_REVISION,
    BASELINE_SCHEMA_FINGERPRINT,
    Base,
    SchemaFingerprintError,
    SchemaRevisionError,
    alembic_config,
    assert_schema_revision_at_head,
    current_schema_heads,
    expected_schema_heads,
    schema_fingerprint,
    stamp_existing_schema_baseline,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPO_ROOT / "backend" / "migrations" / "versions" / "0001_existing_schema_baseline.py"
DUPLICATE_INDEX_NAMES = {
    "ix_asset_daily_prices_key_date",
    "ix_assets_market_symbol",
    "ix_fund_adjust_factors_code_date",
    "ix_fund_daily_bars_code_date",
    "ix_index_daily_bars_code_date",
    "ix_stock_adjust_factors_code_date",
    "ix_stock_daily_bars_code_date",
    "ix_stock_daily_basic_code_date",
    "ix_stock_financial_indicators_code_period",
    "ix_stock_limit_prices_code_date",
    "ix_stock_pool_members_pool_code",
    "ix_trade_calendars_exchange_date",
    "ix_watchlist_items_name_asset",
}


class SchemaMigrationTest(unittest.TestCase):
    def test_offline_upgrade_is_explicit_and_does_not_connect(self):
        output = StringIO()
        config = alembic_config()
        config.attributes["database_url"] = "postgresql+psycopg://offline:offline@127.0.0.1:1/offline"
        config.output_buffer = output

        with redirect_stdout(StringIO()):
            command.upgrade(config, "head", sql=True)

        sql = output.getvalue()
        self.assertIn("CREATE TABLE stocks", sql)
        self.assertIn("CREATE TABLE alembic_version", sql)
        self.assertIn("INSERT INTO alembic_version", sql)
        self.assertIn("DROP INDEX CONCURRENTLY IF EXISTS", sql)

        migration_source = MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertEqual(migration_source.count("op.create_table("), 25)
        self.assertNotIn("Base.metadata", migration_source)
        self.assertNotIn("create_all", migration_source)
        self.assertNotIn("op.drop_table", migration_source)
        self.assertIn("禁止自动降级", migration_source)

    def test_empty_sqlite_upgrade_reaches_head_and_is_idempotent(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            with engine.connect() as connection:
                command.upgrade(alembic_config(connection), "head")
            expected_tables = set(Base.metadata.tables) | {"alembic_version"}
            self.assertEqual(set(inspect(engine).get_table_names()), expected_tables)
            self.assertEqual(len(Base.metadata.tables), 40)
            self.assertIn("research_runs", Base.metadata.tables)
            self.assertIn("strategy_definitions", Base.metadata.tables)
            self.assertIn("research_publications", Base.metadata.tables)
            actual_indexes = {
                index["name"]
                for table_name in Base.metadata.tables
                for index in inspect(engine).get_indexes(table_name)
            }
            self.assertTrue(DUPLICATE_INDEX_NAMES.isdisjoint(actual_indexes))
            assert_schema_revision_at_head(engine)

            with engine.connect() as connection:
                command.upgrade(alembic_config(connection), "head")
            self.assertEqual(set(inspect(engine).get_table_names()), expected_tables)
        finally:
            engine.dispose()

    def test_revision_behind_fails_with_clear_message(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        try:
            expected_message = rf"current=<none>, expected={re.escape(','.join(expected_schema_heads()))}"
            with self.assertRaisesRegex(SchemaRevisionError, expected_message):
                assert_schema_revision_at_head(engine)
        finally:
            engine.dispose()

    def test_lifespan_checks_revision_without_migrating(self):
        async def enter_lifespan() -> None:
            async with main.lifespan(main.app):
                pass

        with patch.object(main, "assert_schema_revision_at_head") as revision_check:
            asyncio.run(enter_lifespan())
        revision_check.assert_called_once_with(main.engine)

    def test_existing_stamp_rejects_sqlite(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        try:
            with self.assertRaisesRegex(SchemaFingerprintError, "仅允许 PostgreSQL"):
                stamp_existing_schema_baseline(engine, confirm_fingerprint=BASELINE_SCHEMA_FINGERPRINT)
        finally:
            engine.dispose()


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "TEST_POSTGRES_URL 未配置，跳过真实 PostgreSQL migration 集成测试")
class PostgresSchemaMigrationIntegrationTest(unittest.TestCase):
    def test_empty_upgrade_and_existing_schema_stamp(self):
        database_url = os.environ["TEST_POSTGRES_URL"]
        parsed_url = make_url(database_url)
        self.assertIn(parsed_url.host, {"127.0.0.1", "localhost"})
        self.assertEqual(parsed_url.database, "quant_migration_test")
        engine = create_engine(database_url)
        try:
            self._reset_ephemeral_database(engine)

            with engine.connect() as connection:
                command.upgrade(alembic_config(connection), "head")
            with engine.connect() as connection:
                self.assertEqual(current_schema_heads(connection), expected_schema_heads())
            assert_schema_revision_at_head(engine)

            with engine.connect() as connection:
                trigger_names = set(
                    connection.execute(
                        text(
                            "SELECT tgname FROM pg_trigger "
                            "WHERE NOT tgisinternal AND tgname LIKE 'trg_%_immutable'"
                        )
                    ).scalars()
                )
            self.assertTrue(
                {
                    "trg_frozen_research_plans_immutable",
                    "trg_research_plan_approvals_immutable",
                    "trg_research_events_immutable",
                    "trg_research_evaluations_immutable",
                    "trg_research_evaluation_runs_immutable",
                    "trg_research_evidence_refs_immutable",
                    "trg_research_publications_terminal_immutable",
                }.issubset(trigger_names)
            )
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO strategy_definitions "
                        "(strategy_id, display_name, lifecycle_status, economic_thesis, "
                        "registry_version, code_commit, metadata_json) VALUES "
                        "('migration-sentinel', '迁移哨兵', '活跃', '只验证不可变触发器', "
                        "'1', :code_commit, '{}'::json)"
                    ),
                    {"code_commit": "c" * 40},
                )
                connection.execute(
                    text(
                        "INSERT INTO frozen_research_plans "
                        "(id, strategy_id, issue_number, version, schema_version, plan_sha256, "
                        "code_commit, plan_json) VALUES "
                        "('90000000-0000-0000-0000-000000000001', 'migration-sentinel', 900, 1, "
                        "'1', :plan_sha256, :code_commit, '{}'::json)"
                    ),
                    {"plan_sha256": "a" * 64, "code_commit": "c" * 40},
                )
            with self.assertRaisesRegex(DBAPIError, "immutable research record"):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE frozen_research_plans SET version = 2 "
                            "WHERE id = '90000000-0000-0000-0000-000000000001'"
                        )
                    )

            with engine.connect() as connection:
                command.upgrade(alembic_config(connection), "head")
            expected_tables = set(Base.metadata.tables) | {"alembic_version"}
            self.assertEqual(set(inspect(engine).get_table_names()), expected_tables)

            self._reset_ephemeral_database(engine)
            with engine.connect() as connection:
                command.upgrade(alembic_config(connection), BASELINE_REVISION)
            with engine.connect() as connection:
                self.assertEqual(schema_fingerprint(connection)["sha256"], BASELINE_SCHEMA_FINGERPRINT)
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE alembic_version"))
            with engine.begin() as connection:
                connection.execute(text("CREATE TABLE unexpected_schema_drift (id integer PRIMARY KEY)"))
            with self.assertRaisesRegex(SchemaFingerprintError, "unexpected_schema_drift"):
                stamp_existing_schema_baseline(
                    engine,
                    confirm_fingerprint=BASELINE_SCHEMA_FINGERPRINT,
                )
            with engine.connect() as connection:
                self.assertEqual(current_schema_heads(connection), ())
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE unexpected_schema_drift"))

            stamped = stamp_existing_schema_baseline(
                engine,
                confirm_fingerprint=BASELINE_SCHEMA_FINGERPRINT,
            )
            self.assertEqual(stamped["revision"], BASELINE_REVISION)
            self.assertEqual(stamped["sha256"], BASELINE_SCHEMA_FINGERPRINT)
            with engine.connect() as connection:
                command.upgrade(alembic_config(connection), "head")
            assert_schema_revision_at_head(engine)
            inspector = inspect(engine)
            actual_indexes = {
                index["name"]
                for table_name in Base.metadata.tables
                for index in inspector.get_indexes(table_name)
            }
            self.assertTrue(DUPLICATE_INDEX_NAMES.isdisjoint(actual_indexes))
            unique_constraints = {
                constraint["name"]
                for table_name in Base.metadata.tables
                for constraint in inspector.get_unique_constraints(table_name)
            }
            self.assertIn("uq_stock_daily_bar_code_date", unique_constraints)
            self.assertIn("uq_stock_limit_price_code_date", unique_constraints)
        finally:
            engine.dispose()

    @staticmethod
    def _reset_ephemeral_database(engine) -> None:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))


if __name__ == "__main__":
    unittest.main()
