from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import unittest

from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.app.database import alembic_config, current_schema_heads, expected_schema_heads
from backend.app.personal_workspace.contracts import (
    AddHoldingCommand,
    CreateObservationRuleCommand,
    PersonalActor,
    SetObservationRuleStateCommand,
)
from backend.app.personal_workspace.crypto import FixedKeyring, PersonalDataCipher
from backend.app.personal_workspace.portfolio import (
    PortfolioBook,
    PostgresPortfolioStore,
    UnavailablePortfolioMarketReader,
)
from backend.app.personal_workspace.rule_automation import HoldingRuleAutomation
from backend.app.personal_workspace.rules import (
    ObservationRuleBook,
    PostgresObservationRuleStore,
    RuleEvaluationRequest,
)
from backend.tests.test_personal_observation_rules import ScriptedRuleInputReader, bars
from backend.app.personal_workspace.rules import RuleInput


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "TEST_POSTGRES_URL 未配置")
class PersonalRulesPostgresTest(unittest.TestCase):
    def test_revisions_evaluations_concurrency_and_private_values_are_atomic(self) -> None:
        engine = create_engine(os.environ["TEST_POSTGRES_URL"])
        try:
            with engine.connect() as connection:
                command.upgrade(alembic_config(connection), "head")
                command.upgrade(alembic_config(connection), "head")
                self.assertEqual(current_schema_heads(connection), expected_schema_heads())
            with engine.begin() as connection:
                for table in (
                    "personal_verification_observations",
                    "personal_verification_items",
                    "personal_record_private_fragments",
                    "personal_record_versions",
                    "personal_redaction_events",
                    "personal_rule_evaluations",
                    "personal_rule_evaluation_batches",
                    "personal_rule_revisions",
                    "personal_rule_instances",
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
                    "personal_holdings",
                    "personal_workspaces",
                ):
                    connection.execute(text(f"DELETE FROM private_workbench.{table}"))

            cipher = PersonalDataCipher(
                FixedKeyring(
                    active_key_id="rules-key",
                    data_keys={"rules-key": bytes(range(32))},
                    lookup_key=b"rules-lookup-key-for-tests-only-32",
                )
            )
            session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
            book = ObservationRuleBook(
                store=PostgresObservationRuleStore(session_factory, cipher=cipher),
                inputs=ScriptedRuleInputReader(
                    RuleInput(
                        symbol="ACME",
                        raw_bars=bars(),
                        adjusted_bars=bars(),
                        events=(),
                        source_health="fresh",
                        evidence_ids=tuple(f"bar-{index}" for index in range(30)),
                        corporate_actions_available=True,
                    )
                ),
                clock=lambda: datetime(2026, 8, 3, 3, 0, tzinfo=timezone.utc),
            )
            actor = PersonalActor(actor_id="rules-owner")
            draft = book.revise(
                actor,
                CreateObservationRuleCommand(
                    type="create_rule",
                    template_id="price_threshold",
                    symbol="ACME",
                    parameters={"direction": "gte", "price": "110"},
                ),
                idempotency_key="pg-create-price",
            )
            enabled = book.revise(
                actor,
                SetObservationRuleStateCommand(
                    type="set_rule_state",
                    rule_id=draft.rule_id,
                    expected_revision=1,
                    state="enabled",
                ),
                idempotency_key="pg-initial-enable",
            )

            def change(state: str, key: str):
                try:
                    return book.revise(
                        actor,
                        SetObservationRuleStateCommand(
                            type="set_rule_state",
                            rule_id=draft.rule_id,
                            expected_revision=2,
                            state=state,
                        ),
                        idempotency_key=key,
                    )
                except ValueError as exc:
                    return str(exc)

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(
                    executor.map(
                        lambda values: change(*values),
                        (("paused", "pg-pause"), ("archived", "pg-archive")),
                    )
                )
            self.assertEqual(sum(value == "revision_conflict" for value in outcomes), 1)
            self.assertEqual(sum(not isinstance(value, str) for value in outcomes), 1)

            self.assertEqual(enabled.revision, 2)
            second = book.revise(
                actor,
                CreateObservationRuleCommand(
                    type="create_rule",
                    template_id="price_threshold",
                    symbol="ACME",
                    parameters={"direction": "gte", "price": "110"},
                ),
                idempotency_key="pg-create-second-price",
            )
            book.revise(
                actor,
                SetObservationRuleStateCommand(
                    type="set_rule_state",
                    rule_id=second.rule_id,
                    expected_revision=1,
                    state="enabled",
                ),
                idempotency_key="pg-enable-second-price",
            )
            batch = book.evaluate(
                actor,
                RuleEvaluationRequest(
                    symbol="ACME",
                    as_of=datetime(2026, 8, 3, 3, 0, tzinfo=timezone.utc),
                ),
                idempotency_key="pg-evaluate-price",
            )
            repeated = book.evaluate(
                actor,
                RuleEvaluationRequest(
                    symbol="ACME",
                    as_of=datetime(2026, 8, 3, 3, 0, tzinfo=timezone.utc),
                ),
                idempotency_key="pg-evaluate-price",
            )
            self.assertEqual(repeated, batch)
            self.assertEqual(book.open(actor)["evaluations"][0].result, "hit")

            portfolio = PortfolioBook(
                store=PostgresPortfolioStore(session_factory, cipher=cipher),
                market=UnavailablePortfolioMarketReader(),
            )
            portfolio.revise(
                actor,
                AddHoldingCommand(
                    type="add_holding",
                    symbol="ACME",
                    name="Acme",
                    quantity="1",
                    average_cost="100",
                    expected_portfolio_revision=0,
                ),
                idempotency_key="pg-add-automation-holding",
            )
            automation = HoldingRuleAutomation(portfolio=portfolio, rules=book)
            automatic_as_of = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
            first_automatic = automation.run_once(actor, as_of=automatic_as_of)
            repeated_automatic = automation.run_once(actor, as_of=automatic_as_of)
            self.assertEqual(repeated_automatic, first_automatic)

            with engine.connect() as connection:
                counts = connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM private_workbench.personal_rule_instances), "
                        "(SELECT count(*) FROM private_workbench.personal_rule_revisions), "
                        "(SELECT count(*) FROM private_workbench.personal_rule_evaluation_batches), "
                        "(SELECT count(*) FROM private_workbench.personal_rule_evaluations)"
                    )
                ).one()
                projection = "|".join(
                    connection.execute(
                        text(
                            "SELECT to_jsonb(row_value)::text FROM private_workbench.personal_rule_instances AS row_value "
                            "UNION ALL SELECT to_jsonb(row_value)::text FROM private_workbench.personal_rule_revisions AS row_value "
                            "UNION ALL SELECT to_jsonb(row_value)::text FROM private_workbench.personal_rule_evaluations AS row_value"
                        )
                    ).scalars()
                )
            self.assertEqual(counts, (2, 5, 2, 2))
            self.assertNotIn("ACME", projection)
            self.assertNotIn('"threshold": "110"', projection)
            self.assertNotIn('"price": "110"', projection)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
