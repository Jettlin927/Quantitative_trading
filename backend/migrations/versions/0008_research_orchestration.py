"""add research orchestration state and leased work queue

Revision ID: 0008_research_orchestration
Revises: 0007_research_domain
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_research_orchestration"
down_revision = "0007_research_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_runs",
        sa.Column("orchestration_attempt_id", sa.Uuid(as_uuid=False), nullable=True),
    )
    op.create_index(
        "uq_research_runs_orchestration_attempt",
        "research_runs",
        ["orchestration_attempt_id"],
        unique=True,
    )
    op.create_table(
        "research_orchestrations",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("plan_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("formal_research_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        sa.Column(
            "state", sa.String(length=24), nullable=False, server_default="pending_approval"
        ),
        sa.Column("state_reason", sa.String(length=2000), nullable=True),
        sa.Column("last_issue_body_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "approval_invalidated", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("superseded_by_plan_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "state in ('pending_approval', 'approved', 'queued', 'running', 'stopping', "
            "'publishing', 'published', 'stopped', 'blocked')",
            name="ck_research_orchestrations_state",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["frozen_research_plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["formal_research_id"], ["formal_researches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_plan_id"], ["frozen_research_plans.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", name="uq_research_orchestrations_plan"),
        sa.UniqueConstraint("formal_research_id", name="uq_research_orchestrations_formal"),
    )
    op.create_index(
        "ix_research_orchestrations_issue_created",
        "research_orchestrations",
        ["issue_number", "created_at"],
    )
    op.create_index(
        "ix_research_orchestrations_state_updated",
        "research_orchestrations",
        ["state", "updated_at"],
    )

    op.create_table(
        "research_work_items",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("orchestration_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("formal_research_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_token", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_attempt_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("current_run_id", sa.String(length=36), nullable=True),
        sa.Column("resume_run_id", sa.String(length=36), nullable=True),
        sa.Column("stop_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_kind", sa.String(length=80), nullable=True),
        sa.Column("last_error", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status in ('queued', 'leased', 'running', 'succeeded', 'failed', 'interrupted')",
            name="ck_research_work_items_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_research_work_items_attempt_count"),
        sa.CheckConstraint(
            "attempt_count <= max_attempts", name="ck_research_work_items_attempt_budget"
        ),
        sa.CheckConstraint(
            "max_attempts between 1 and 3", name="ck_research_work_items_max_attempts"
        ),
        sa.CheckConstraint(
            "((status in ('leased', 'running') and lease_owner is not null and "
            "lease_token is not null and lease_expires_at is not null) or "
            "(status not in ('leased', 'running') and lease_owner is null and "
            "lease_token is null and lease_expires_at is null))",
            name="ck_research_work_items_lease_shape",
        ),
        sa.CheckConstraint(
            "status not in ('leased', 'running') or current_attempt_id is not null",
            name="ck_research_work_items_active_attempt",
        ),
        sa.ForeignKeyConstraint(
            ["orchestration_id"], ["research_orchestrations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["formal_research_id"], ["formal_researches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["current_run_id"], ["research_runs.run_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["resume_run_id"], ["research_runs.run_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("formal_research_id", name="uq_research_work_items_formal"),
    )
    op.create_index(
        "ix_research_work_items_queue",
        "research_work_items",
        ["status", "next_attempt_at", "created_at"],
    )
    op.create_index(
        "ix_research_work_items_lease",
        "research_work_items",
        ["status", "lease_expires_at"],
    )
    _create_consistency_triggers()


def downgrade() -> None:
    _drop_consistency_triggers()
    op.drop_index("ix_research_work_items_lease", table_name="research_work_items")
    op.drop_index("ix_research_work_items_queue", table_name="research_work_items")
    op.drop_table("research_work_items")
    op.drop_index("ix_research_orchestrations_state_updated", table_name="research_orchestrations")
    op.drop_index("ix_research_orchestrations_issue_created", table_name="research_orchestrations")
    op.drop_table("research_orchestrations")
    op.drop_index("uq_research_runs_orchestration_attempt", table_name="research_runs")
    op.drop_column("research_runs", "orchestration_attempt_id")


def _create_consistency_triggers() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE FUNCTION ensure_research_orchestration_consistency()
        RETURNS trigger AS $$
        BEGIN
          IF TG_TABLE_NAME = 'research_orchestrations' THEN
            IF NOT EXISTS (
              SELECT 1 FROM frozen_research_plans plan
              WHERE plan.id = NEW.plan_id
                AND plan.issue_number = NEW.issue_number
            ) OR (
              NEW.formal_research_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM formal_researches research
                WHERE research.id = NEW.formal_research_id
                  AND research.plan_id = NEW.plan_id
              )
            ) OR (
              NEW.superseded_by_plan_id IS NOT NULL AND NOT EXISTS (
                SELECT 1
                FROM frozen_research_plans current_plan
                JOIN frozen_research_plans next_plan
                  ON next_plan.id = NEW.superseded_by_plan_id
                WHERE current_plan.id = NEW.plan_id
                  AND next_plan.issue_number = current_plan.issue_number
                  AND next_plan.version > current_plan.version
              )
            ) THEN
              RAISE EXCEPTION 'research orchestration relation mismatch: %', TG_TABLE_NAME;
            END IF;
          ELSIF TG_TABLE_NAME = 'research_work_items' THEN
            IF NOT EXISTS (
              SELECT 1 FROM research_orchestrations orchestration
              WHERE orchestration.id = NEW.orchestration_id
                AND orchestration.formal_research_id = NEW.formal_research_id
            ) OR (
              NEW.current_run_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM research_runs run
                WHERE run.run_id = NEW.current_run_id
                  AND run.formal_research_id = NEW.formal_research_id
                  AND run.orchestration_attempt_id = NEW.current_attempt_id
              )
            ) OR (
              NEW.resume_run_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM research_runs run
                WHERE run.run_id = NEW.resume_run_id
                  AND run.formal_research_id = NEW.formal_research_id
                  AND run.orchestration_attempt_id = NEW.current_attempt_id
              )
            ) THEN
              RAISE EXCEPTION 'research orchestration relation mismatch: %', TG_TABLE_NAME;
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_research_orchestrations_consistency
        BEFORE INSERT OR UPDATE ON research_orchestrations
        FOR EACH ROW EXECUTE FUNCTION ensure_research_orchestration_consistency();

        CREATE TRIGGER trg_research_work_items_consistency
        BEFORE INSERT OR UPDATE ON research_work_items
        FOR EACH ROW EXECUTE FUNCTION ensure_research_orchestration_consistency();
        """
    )


def _drop_consistency_triggers() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_research_work_items_consistency ON research_work_items;
        DROP TRIGGER IF EXISTS trg_research_orchestrations_consistency ON research_orchestrations;
        DROP FUNCTION IF EXISTS ensure_research_orchestration_consistency();
        """
    )
