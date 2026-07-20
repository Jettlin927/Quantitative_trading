"""add explicit provenance for historical research imports

Revision ID: 0008_research_history_provenance
Revises: 0007_research_domain
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_research_history_provenance"
down_revision = "0007_research_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("research_plan_approvals") as batch_op:
        batch_op.drop_constraint("ck_research_plan_approvals_action", type_="check")
        batch_op.alter_column(
            "action",
            existing_type=sa.String(length=16),
            type_=sa.String(length=24),
            existing_nullable=False,
        )
        batch_op.alter_column("comment_id", existing_type=sa.BigInteger(), nullable=True)
        batch_op.add_column(sa.Column("source_uri", sa.String(length=1000), nullable=True))
        batch_op.create_check_constraint(
            "ck_research_plan_approvals_action",
            "action in ('approved', 'invalidated', 'stopped', 'historical_import')",
        )
        batch_op.create_check_constraint(
            "ck_research_plan_approvals_provenance",
            "(action = 'historical_import' and comment_id is null and source_uri is not null) "
            "or (action <> 'historical_import' and comment_id is not null and source_uri is null)",
        )

    with op.batch_alter_table("formal_researches") as batch_op:
        batch_op.add_column(
            sa.Column("origin", sa.String(length=24), nullable=False, server_default="native")
        )
        batch_op.create_check_constraint(
            "ck_formal_researches_origin",
            "origin in ('native', 'historical_import')",
        )
        batch_op.create_check_constraint(
            "ck_formal_researches_historical_phase",
            "origin <> 'historical_import' or phase in ('published', 'stopped')",
        )

    _replace_postgres_formal_research_guard()
    _install_postgres_historical_run_guard()


def _replace_postgres_formal_research_guard() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER trg_formal_researches_consistent ON formal_researches")
    op.execute(
        """
        CREATE FUNCTION ensure_formal_research_origin_consistency()
        RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'UPDATE' AND (
            NEW.id IS DISTINCT FROM OLD.id
            OR NEW.plan_id IS DISTINCT FROM OLD.plan_id
            OR NEW.approval_id IS DISTINCT FROM OLD.approval_id
            OR NEW.origin IS DISTINCT FROM OLD.origin
          ) THEN
            RAISE EXCEPTION 'formal research identity is immutable';
          END IF;
          IF NOT EXISTS (
            SELECT 1
            FROM research_plan_approvals approval
            JOIN frozen_research_plans plan ON plan.id = NEW.plan_id
            WHERE approval.id = NEW.approval_id
              AND approval.plan_id = NEW.plan_id
              AND approval.plan_sha256 = plan.plan_sha256
              AND (
                (NEW.origin = 'native' AND approval.action = 'approved')
                OR
                (NEW.origin = 'historical_import' AND approval.action = 'historical_import')
              )
          ) THEN
            RAISE EXCEPTION 'research origin or relation mismatch: formal_researches';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_formal_researches_consistent
        BEFORE INSERT OR UPDATE ON formal_researches
        FOR EACH ROW EXECUTE FUNCTION ensure_formal_research_origin_consistency()
        """
    )


def _install_postgres_historical_run_guard() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE FUNCTION ensure_historical_run_link_consistency()
        RETURNS trigger AS $$
        DECLARE
          research_origin text;
          research_created_at timestamptz;
          old_research_origin text;
          frozen_plan json;
        BEGIN
          IF TG_OP = 'UPDATE' AND OLD.formal_research_id IS NOT NULL THEN
            SELECT research.origin INTO old_research_origin
            FROM formal_researches research
            WHERE research.id = OLD.formal_research_id;
            IF old_research_origin = 'historical_import' THEN
              RAISE EXCEPTION 'historical research run is immutable';
            END IF;
          END IF;
          IF NEW.formal_research_id IS NULL THEN
            RETURN NEW;
          END IF;
          SELECT research.origin, research.created_at, plan.plan_json
            INTO research_origin, research_created_at, frozen_plan
          FROM formal_researches research
          JOIN frozen_research_plans plan ON plan.id = research.plan_id
          WHERE research.id = NEW.formal_research_id;
          IF research_origin = 'historical_import' AND (
            TG_OP = 'INSERT'
            OR NEW.started_at >= research_created_at
            OR NEW.status <> 'succeeded'
            OR NOT EXISTS (
              SELECT 1
              FROM json_array_elements(frozen_plan -> 'runIdentities') identity
              WHERE identity ->> 'runId' = NEW.run_id
                AND identity ->> 'strategyId' = NEW.strategy_id
                AND identity ->> 'codeCommit' = NEW.code_commit
                AND identity ->> 'reproducibilityKey' = NEW.reproducibility_key
                AND identity ->> 'resultFingerprint' = NEW.result_fingerprint
            )
          ) THEN
            RAISE EXCEPTION 'historical research cannot authorize new or mismatched run';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_research_runs_historical_consistent
        BEFORE INSERT OR UPDATE ON research_runs
        FOR EACH ROW EXECUTE FUNCTION ensure_historical_run_link_consistency()
        """
    )
def downgrade() -> None:
    raise RuntimeError("历史导入来源已进入不可变审计链，禁止自动降级；请使用受控前向迁移。")
