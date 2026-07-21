from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
from io import StringIO
import json
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
    def test_all_revision_ids_fit_postgres_alembic_version_column(self):
        revision_dir = REPO_ROOT / "backend" / "migrations" / "versions"
        revisions = {}
        for path in revision_dir.glob("*.py"):
            match = re.search(r'^revision = "([^"]+)"$', path.read_text(encoding="utf-8"), re.MULTILINE)
            if match is not None:
                revisions[path.name] = match.group(1)

        self.assertTrue(revisions)
        self.assertEqual(
            {name: value for name, value in revisions.items() if len(value) > 32},
            {},
        )

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
            self.assertEqual(len(Base.metadata.tables), 46)
            self.assertIn("research_runs", Base.metadata.tables)
            self.assertIn("strategy_definitions", Base.metadata.tables)
            self.assertIn("research_publications", Base.metadata.tables)
            self.assertIn("research_publication_issue_mappings", Base.metadata.tables)
            self.assertIn("research_orchestrations", Base.metadata.tables)
            self.assertIn("research_work_items", Base.metadata.tables)
            self.assertIn("us_experiment_instruments", Base.metadata.tables)
            self.assertIn("us_experiment_daily_bars", Base.metadata.tables)
            self.assertIn("us_experiment_daily_checks", Base.metadata.tables)
            financial_columns = {
                column["name"]
                for column in inspect(engine).get_columns("stock_financial_indicators")
            }
            self.assertTrue(
                {
                    "source_update_flag",
                    "source_revision_sha256",
                    "source_observed_at",
                    "available_from",
                    "revision_status",
                }.issubset(financial_columns)
            )
            financial_unique_constraints = {
                constraint["name"]
                for constraint in inspect(engine).get_unique_constraints(
                    "stock_financial_indicators"
                )
            }
            self.assertIn(
                "uq_stock_financial_indicator_revision",
                financial_unique_constraints,
            )
            self.assertNotIn(
                "uq_stock_financial_indicator_period",
                financial_unique_constraints,
            )
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

    def test_research_domain_rejects_cross_aggregate_links(self):
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
                column_types = {
                    (row.table_name, row.column_name): row.udt_name
                    for row in connection.execute(
                        text(
                            "SELECT table_name, column_name, udt_name "
                            "FROM information_schema.columns WHERE table_schema = 'public'"
                        )
                    )
                }
                uuid_columns = {
                    ("frozen_research_plans", "id"),
                    ("research_plan_approvals", "id"),
                    ("research_plan_approvals", "plan_id"),
                    ("formal_researches", "id"),
                    ("formal_researches", "plan_id"),
                    ("formal_researches", "approval_id"),
                    ("research_runs", "formal_research_id"),
                    ("research_events", "id"),
                    ("research_events", "formal_research_id"),
                    ("research_evaluations", "id"),
                    ("research_evaluations", "formal_research_id"),
                    ("research_evaluations", "supersedes_evaluation_id"),
                    ("research_evaluation_runs", "evaluation_id"),
                    ("research_evidence_refs", "id"),
                    ("research_evidence_refs", "evaluation_id"),
                    ("research_publications", "id"),
                    ("research_publications", "formal_research_id"),
                    ("research_publications", "evaluation_id"),
                    ("research_publications", "supersedes_publication_id"),
                    ("follow_up_research_proposals", "id"),
                    ("follow_up_research_proposals", "source_evaluation_id"),
                    ("follow_up_research_proposals", "source_evidence_ref_id"),
                    ("follow_up_research_proposals", "converted_plan_id"),
                }
                self.assertTrue(
                    all(column_types.get(column) == "uuid" for column in uuid_columns),
                    column_types,
                )
                trigger_names = set(
                    connection.execute(
                        text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
                    ).scalars()
                )
                self.assertTrue(
                    {
                        "trg_research_plan_approvals_consistent",
                        "trg_formal_researches_consistent",
                        "trg_research_runs_consistent",
                        "trg_research_runs_historical_consistent",
                        "trg_research_events_consistent",
                        "trg_research_evaluations_consistent",
                        "trg_research_evaluation_runs_consistent",
                        "trg_research_evidence_refs_consistent",
                        "trg_research_publications_consistent",
                        "trg_follow_up_research_proposals_consistent",
                        "trg_research_evaluation_runs_published_immutable",
                        "trg_research_evidence_refs_published_immutable",
                    }.issubset(trigger_names)
                )
                approval_columns = {
                    row.column_name: row.is_nullable
                    for row in connection.execute(
                        text(
                            "SELECT column_name, is_nullable FROM information_schema.columns "
                            "WHERE table_schema = 'public' "
                            "AND table_name = 'research_plan_approvals'"
                        )
                    )
                }
                self.assertEqual(approval_columns["comment_id"], "YES")
                self.assertEqual(approval_columns["source_uri"], "YES")
                self.assertEqual(
                    connection.execute(
                        text(
                            "SELECT column_default FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND table_name = 'formal_researches' "
                            "AND column_name = 'origin'"
                        )
                    ).scalar_one(),
                    "'native'::character varying",
                )

            with engine.begin() as connection:
                first = self._insert_research_graph(connection, "a", 901, publish=True)
                second = self._insert_research_graph(connection, "b", 902, publish=False)
                historical = self._insert_historical_research_graph(connection)
                historical_native = self._insert_native_research_for_strategy(
                    connection,
                    historical["strategy_id"],
                )
                connection.execute(
                    text(
                        "INSERT INTO research_evaluations "
                        "(id, formal_research_id, version, conclusion, evaluation_sha256, "
                        "supersedes_evaluation_id, supporting_evidence, opposing_evidence, "
                        "missing_evidence, limitations, follow_up_recommendations) VALUES "
                        "(:id, :formal_id, 2, '证据不足', :sha, :previous_id, "
                        "'[]'::json, '[]'::json, '[]'::json, '[]'::json, '[]'::json)"
                    ),
                    {
                        "id": "a0000000-0000-0000-0000-000000000008",
                        "formal_id": first["formal_id"],
                        "sha": "8" * 64,
                        "previous_id": first["evaluation_id"],
                    },
                )

            invalid_statements = (
                (
                    "INSERT INTO research_plan_approvals "
                    "(id, plan_id, action, actor_login, comment_id, comment_body, plan_sha256) "
                    "VALUES ('c0000000-0000-0000-0000-000000000001', :plan_id, 'approved', "
                    "'Jettlin927', 9901, '批准研究 错误哈希', :sha)",
                    {"plan_id": first["plan_id"], "sha": "f" * 64},
                ),
                (
                    "INSERT INTO research_runs "
                    "(run_id, formal_research_id, strategy_id, status, stage, config, "
                    "config_sha256, code_commit, environment_sha256, random_seed, metrics, "
                    "artifact_root) VALUES "
                    "('c0000000-0000-0000-0000-000000000002', :formal_id, :strategy_id, "
                    "'queued', 'queued', '{}'::json, :sha, :code, :sha, 1, '{}'::json, "
                    "'artifacts://cross-run')",
                    {
                        "formal_id": first["formal_id"],
                        "strategy_id": second["strategy_id"],
                        "sha": "c" * 64,
                        "code": "c" * 40,
                    },
                ),
                (
                    "INSERT INTO research_events "
                    "(id, formal_research_id, run_id, sequence_no, event_type, payload_json) "
                    "VALUES ('c0000000-0000-0000-0000-000000000003', :formal_id, :run_id, "
                    "2, 'cross_run', '{}'::json)",
                    {"formal_id": first["formal_id"], "run_id": second["run_id"]},
                ),
                (
                    "INSERT INTO research_evaluation_runs (evaluation_id, run_id) "
                    "VALUES ('a0000000-0000-0000-0000-000000000008', :run_id)",
                    {"run_id": second["run_id"]},
                ),
                (
                    "INSERT INTO research_evidence_refs "
                    "(id, evaluation_id, run_id, kind, uri, metadata_json) VALUES "
                    "('c0000000-0000-0000-0000-000000000004', "
                    "'a0000000-0000-0000-0000-000000000008', :run_id, 'report', "
                    "'artifacts://cross-evidence', '{}'::json)",
                    {"run_id": second["run_id"]},
                ),
                (
                    "INSERT INTO research_publications "
                    "(id, formal_research_id, evaluation_id, version, status, publication_sha256, "
                    "artifact_manifest_uri, issue_number) VALUES "
                    "('c0000000-0000-0000-0000-000000000005', :formal_id, "
                    "'a0000000-0000-0000-0000-000000000008', 1, 'pending', :sha, "
                    "'artifacts://cross-publication', 9905)",
                    {"formal_id": second["formal_id"], "sha": "5" * 64},
                ),
                (
                    "INSERT INTO research_evaluations "
                    "(id, formal_research_id, version, conclusion, evaluation_sha256, "
                    "supersedes_evaluation_id, supporting_evidence, opposing_evidence, "
                    "missing_evidence, limitations, follow_up_recommendations) VALUES "
                    "('a0000000-0000-0000-0000-000000000009', :formal_id, 3, '证据不足', "
                    ":sha, :previous_id, '[]'::json, '[]'::json, '[]'::json, '[]'::json, "
                    "'[]'::json)",
                    {
                        "formal_id": first["formal_id"],
                        "sha": "9" * 64,
                        "previous_id": second["evaluation_id"],
                    },
                ),
                (
                    "INSERT INTO follow_up_research_proposals "
                    "(id, strategy_id, source_evaluation_id, title, rationale, status, "
                    "proposal_json) VALUES "
                    "('c0000000-0000-0000-0000-000000000006', :strategy_id, "
                    "'a0000000-0000-0000-0000-000000000008', '跨研究提案', '应被拒绝', "
                    "'proposed', '{}'::json)",
                    {"strategy_id": second["strategy_id"]},
                ),
            )
            for statement, parameters in invalid_statements:
                with self.subTest(statement=statement[:60]):
                    with self.assertRaisesRegex(DBAPIError, "research relation mismatch"):
                        with engine.begin() as connection:
                            connection.execute(text(statement), parameters)

            with self.assertRaisesRegex(DBAPIError, "research origin or relation mismatch"):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO formal_researches "
                            "(id, plan_id, approval_id, origin, phase) VALUES "
                            "('d0000000-0000-0000-0000-000000000004', :plan_id, "
                            ":approval_id, 'native', 'published')"
                        ),
                        historical,
                    )

            with self.assertRaisesRegex(
                DBAPIError,
                "ck_formal_researches_historical_phase",
            ):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO formal_researches "
                            "(id, plan_id, approval_id, origin, phase) VALUES "
                            "('d0000000-0000-0000-0000-000000000005', :plan_id, "
                            ":approval_id, 'historical_import', 'active')"
                        ),
                        historical,
                    )

            with self.assertRaisesRegex(
                DBAPIError,
                "historical research cannot authorize new or mismatched run",
            ):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO research_runs "
                            "(run_id, formal_research_id, reproducibility_key, strategy_id, status, "
                            "stage, config, config_sha256, code_commit, environment_sha256, "
                            "random_seed, metrics, result_fingerprint, artifact_root) VALUES "
                            "('d0000000-0000-0000-0000-000000000006', :formal_id, :sha, "
                            ":strategy_id, 'succeeded', 'finalized', '{}'::json, :sha, :code, :sha, "
                            "1, '{}'::json, :sha, 'artifacts://history-new-run')"
                        ),
                        {**historical, "sha": "d" * 64, "code": "c" * 40},
                    )

            for statement, parameters in (
                (
                    "UPDATE research_runs SET formal_research_id = NULL "
                    "WHERE run_id = :run_id",
                    historical,
                ),
                (
                    "UPDATE research_runs SET formal_research_id = :new_formal_id "
                    "WHERE run_id = :run_id",
                    {**historical, "new_formal_id": historical_native["formal_id"]},
                ),
            ):
                with self.subTest(history_run_update=statement):
                    with self.assertRaisesRegex(
                        DBAPIError,
                        "historical research run is immutable",
                    ):
                        with engine.begin() as connection:
                            connection.execute(text(statement), parameters)

            with self.assertRaisesRegex(DBAPIError, "formal research identity is immutable"):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE formal_researches SET plan_id = :new_plan_id, "
                            "approval_id = :new_approval_id WHERE id = :formal_id"
                        ),
                        {
                            **historical,
                            "new_plan_id": first["plan_id"],
                            "new_approval_id": first["approval_id"],
                        },
                    )

            with self.assertRaisesRegex(DBAPIError, "published evaluation is immutable"):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO research_evidence_refs "
                            "(id, evaluation_id, kind, uri, metadata_json) VALUES "
                            "('c0000000-0000-0000-0000-000000000007', :evaluation_id, "
                            "'limitation', 'artifacts://late-evidence', '{}'::json)"
                        ),
                        {"evaluation_id": first["evaluation_id"]},
                    )
        finally:
            engine.dispose()

    @staticmethod
    def _insert_research_graph(connection, prefix: str, issue_number: int, *, publish: bool):
        ids = {
            "strategy_id": f"migration-{prefix}",
            "plan_id": f"{prefix}0000000-0000-0000-0000-000000000001",
            "approval_id": f"{prefix}0000000-0000-0000-0000-000000000002",
            "formal_id": f"{prefix}0000000-0000-0000-0000-000000000003",
            "run_id": f"{prefix}0000000-0000-0000-0000-000000000004",
            "evaluation_id": f"{prefix}0000000-0000-0000-0000-000000000005",
            "evidence_id": f"{prefix}0000000-0000-0000-0000-000000000006",
            "publication_id": f"{prefix}0000000-0000-0000-0000-000000000007",
        }
        connection.execute(
            text(
                "INSERT INTO strategy_definitions "
                "(strategy_id, display_name, lifecycle_status, economic_thesis, registry_version, "
                "code_commit, metadata_json) VALUES "
                "(:strategy_id, :strategy_id, '活跃', '迁移关系哨兵', '1', :code, '{}'::json)"
            ),
            {**ids, "code": "c" * 40},
        )
        connection.execute(
            text(
                "INSERT INTO frozen_research_plans "
                "(id, strategy_id, issue_number, version, schema_version, plan_sha256, "
                "code_commit, plan_json) VALUES "
                "(:plan_id, :strategy_id, :issue_number, 1, '1', :plan_sha, :code, '{}'::json)"
            ),
            {
                **ids,
                "issue_number": issue_number,
                "plan_sha": prefix * 64,
                "code": "c" * 40,
            },
        )
        connection.execute(
            text(
                "INSERT INTO research_plan_approvals "
                "(id, plan_id, action, actor_login, comment_id, comment_body, plan_sha256) "
                "VALUES (:approval_id, :plan_id, 'approved', 'Jettlin927', :comment_id, "
                ":comment_body, :plan_sha)"
            ),
            {
                **ids,
                "comment_id": issue_number * 10,
                "comment_body": f"批准研究 {prefix * 64}",
                "plan_sha": prefix * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO formal_researches (id, plan_id, approval_id, phase) "
                "VALUES (:formal_id, :plan_id, :approval_id, 'evaluating')"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO research_runs "
                "(run_id, formal_research_id, strategy_id, status, stage, config, config_sha256, "
                "code_commit, environment_sha256, random_seed, metrics, artifact_root) VALUES "
                "(:run_id, :formal_id, :strategy_id, 'queued', 'queued', '{}'::json, :sha, "
                ":code, :sha, 1, '{}'::json, :artifact_root)"
            ),
            {
                **ids,
                "sha": prefix * 64,
                "code": "c" * 40,
                "artifact_root": f"artifacts://migration-{prefix}",
            },
        )
        connection.execute(
            text(
                "INSERT INTO research_evaluations "
                "(id, formal_research_id, version, conclusion, evaluation_sha256, "
                "supporting_evidence, opposing_evidence, missing_evidence, limitations, "
                "follow_up_recommendations) VALUES "
                "(:evaluation_id, :formal_id, 1, '证据不足', :sha, '[]'::json, '[]'::json, "
                "'[]'::json, '[]'::json, '[]'::json)"
            ),
            {**ids, "sha": prefix * 64},
        )
        connection.execute(
            text(
                "INSERT INTO research_evaluation_runs (evaluation_id, run_id) "
                "VALUES (:evaluation_id, :run_id)"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO research_evidence_refs "
                "(id, evaluation_id, run_id, kind, uri, metadata_json) VALUES "
                "(:evidence_id, :evaluation_id, :run_id, 'report', :uri, '{}'::json)"
            ),
            {**ids, "uri": f"artifacts://migration-{prefix}/report"},
        )
        if publish:
            connection.execute(
                text(
                    "INSERT INTO research_publications "
                    "(id, formal_research_id, evaluation_id, version, status, publication_sha256, "
                    "artifact_manifest_uri, issue_number) VALUES "
                    "(:publication_id, :formal_id, :evaluation_id, 1, 'published', :sha, :uri, "
                    ":issue_number)"
                ),
                {
                    **ids,
                    "sha": prefix * 64,
                    "uri": f"artifacts://migration-{prefix}/manifest",
                    "issue_number": issue_number,
                },
            )
        return ids

    @staticmethod
    def _insert_historical_research_graph(connection):
        ids = {
            "strategy_id": "migration-history",
            "plan_id": "d0000000-0000-0000-0000-000000000001",
            "approval_id": "d0000000-0000-0000-0000-000000000002",
            "formal_id": "d0000000-0000-0000-0000-000000000003",
            "run_id": "d0000000-0000-0000-0000-000000000004",
        }
        connection.execute(
            text(
                "INSERT INTO strategy_definitions "
                "(strategy_id, display_name, lifecycle_status, economic_thesis, registry_version, "
                "code_commit, metadata_json) VALUES "
                "(:strategy_id, :strategy_id, '已归档', '历史导入哨兵', 'history-import-v1', "
                ":code, '{}'::json)"
            ),
            {**ids, "code": "c" * 40},
        )
        connection.execute(
            text(
                "INSERT INTO frozen_research_plans "
                "(id, strategy_id, issue_number, version, schema_version, plan_sha256, "
                "code_commit, plan_json) VALUES "
                "(:plan_id, :strategy_id, 903, 1, 'history-import-v1', :sha, :code, "
                "CAST(:plan_json AS json))"
            ),
            {
                **ids,
                "sha": "d" * 64,
                "code": "c" * 40,
                "plan_json": json.dumps(
                    {
                        "runIdentities": [
                            {
                                "runId": ids["run_id"],
                                "strategyId": ids["strategy_id"],
                                "codeCommit": "c" * 40,
                                "reproducibilityKey": "d" * 64,
                                "resultFingerprint": "f" * 64,
                            }
                        ]
                    }
                ),
            },
        )
        connection.execute(
            text(
                "INSERT INTO research_plan_approvals "
                "(id, plan_id, action, actor_login, comment_id, source_uri, comment_body, "
                "plan_sha256) VALUES (:approval_id, :plan_id, 'historical_import', "
                "'history-migration-v1', NULL, 'repo://history.json', '历史导入', :sha)"
            ),
            {**ids, "sha": "d" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO research_runs "
                "(run_id, formal_research_id, reproducibility_key, strategy_id, status, stage, "
                "config, config_sha256, code_commit, environment_sha256, random_seed, metrics, "
                "result_fingerprint, artifact_root, started_at) VALUES "
                "(:run_id, NULL, :reproducibility_key, :strategy_id, 'succeeded', 'finalized', "
                "'{}'::json, :config_sha256, :code, :environment_sha256, 1, '{}'::json, "
                ":result_fingerprint, 'artifacts://history-existing-run', "
                "'2026-01-01 00:00:00+00')"
            ),
            {
                **ids,
                "reproducibility_key": "d" * 64,
                "config_sha256": "a" * 64,
                "code": "c" * 40,
                "environment_sha256": "e" * 64,
                "result_fingerprint": "f" * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO formal_researches "
                "(id, plan_id, approval_id, origin, phase) VALUES "
                "(:formal_id, :plan_id, :approval_id, 'historical_import', 'stopped')"
            ),
            ids,
        )
        connection.execute(
            text(
                "UPDATE research_runs SET formal_research_id = :formal_id "
                "WHERE run_id = :run_id"
            ),
            ids,
        )
        return ids

    @staticmethod
    def _insert_native_research_for_strategy(connection, strategy_id: str):
        ids = {
            "strategy_id": strategy_id,
            "plan_id": "d0000000-0000-0000-0000-000000000010",
            "approval_id": "d0000000-0000-0000-0000-000000000011",
            "formal_id": "d0000000-0000-0000-0000-000000000012",
        }
        connection.execute(
            text(
                "INSERT INTO frozen_research_plans "
                "(id, strategy_id, issue_number, version, schema_version, plan_sha256, "
                "code_commit, plan_json) VALUES "
                "(:plan_id, :strategy_id, 904, 1, '1', :sha, :code, '{}'::json)"
            ),
            {**ids, "sha": "e" * 64, "code": "c" * 40},
        )
        connection.execute(
            text(
                "INSERT INTO research_plan_approvals "
                "(id, plan_id, action, actor_login, comment_id, comment_body, plan_sha256) "
                "VALUES (:approval_id, :plan_id, 'approved', 'Jettlin927', 9040, "
                ":comment_body, :sha)"
            ),
            {
                **ids,
                "comment_body": f"批准研究 {'e' * 64}",
                "sha": "e" * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO formal_researches (id, plan_id, approval_id, origin, phase) "
                "VALUES (:formal_id, :plan_id, :approval_id, 'native', 'stopped')"
            ),
            ids,
        )
        return ids

    @staticmethod
    def _reset_ephemeral_database(engine) -> None:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))


if __name__ == "__main__":
    unittest.main()
