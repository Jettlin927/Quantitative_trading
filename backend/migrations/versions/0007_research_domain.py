"""add versioned research domain records

Revision ID: 0007_research_domain
Revises: 0006_worker_heartbeats
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_research_domain"
down_revision = "0006_worker_heartbeats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_definitions",
        sa.Column("strategy_id", sa.String(length=80), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("lifecycle_status", sa.String(length=16), nullable=False, server_default="活跃"),
        sa.Column("economic_thesis", sa.String(length=2000), nullable=False),
        sa.Column("registry_version", sa.String(length=40), nullable=False),
        sa.Column("code_commit", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "lifecycle_status in ('活跃', '暂停', '停止研究', '已归档')",
            name="ck_strategy_definitions_lifecycle",
        ),
        sa.PrimaryKeyConstraint("strategy_id"),
    )

    op.create_table(
        "frozen_research_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("strategy_id", sa.String(length=80), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("plan_sha256", sa.String(length=64), nullable=False),
        sa.Column("code_commit", sa.String(length=64), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("version > 0", name="ck_frozen_research_plans_version"),
        sa.ForeignKeyConstraint(
            ["strategy_id"], ["strategy_definitions.strategy_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issue_number", "version", name="uq_frozen_research_plans_issue_version"),
        sa.UniqueConstraint("plan_sha256", name="uq_frozen_research_plans_sha256"),
    )
    op.create_index(
        "ix_frozen_research_plans_strategy_created",
        "frozen_research_plans",
        ["strategy_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "research_plan_approvals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("actor_login", sa.String(length=80), nullable=False),
        sa.Column("comment_id", sa.BigInteger(), nullable=False),
        sa.Column("comment_body", sa.String(length=500), nullable=False),
        sa.Column("plan_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "action in ('approved', 'invalidated', 'stopped')",
            name="ck_research_plan_approvals_action",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["frozen_research_plans.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("comment_id", name="uq_research_plan_approvals_comment"),
    )
    op.create_index(
        "ix_research_plan_approvals_plan_created",
        "research_plan_approvals",
        ["plan_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "formal_researches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False, server_default="approved"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "phase in ('approved', 'active', 'evaluating', 'published', 'stopped')",
            name="ck_formal_researches_phase",
        ),
        sa.ForeignKeyConstraint(["approval_id"], ["research_plan_approvals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["plan_id"], ["frozen_research_plans.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approval_id", name="uq_formal_researches_approval"),
        sa.UniqueConstraint("plan_id", name="uq_formal_researches_plan"),
    )
    op.create_index(
        "ix_formal_researches_phase_created",
        "formal_researches",
        ["phase", "created_at"],
        unique=False,
    )

    with op.batch_alter_table("research_runs") as batch_op:
        batch_op.add_column(
            sa.Column("formal_research_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_research_runs_formal_research",
            "formal_researches",
            ["formal_research_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_research_runs_formal_research_started",
            ["formal_research_id", "started_at"],
            unique=False,
        )

    op.create_table(
        "research_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("formal_research_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("sequence_no > 0", name="ck_research_events_sequence"),
        sa.ForeignKeyConstraint(["formal_research_id"], ["formal_researches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.run_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "formal_research_id", "sequence_no", name="uq_research_events_research_sequence"
        ),
    )
    op.create_index(
        "ix_research_events_run_occurred",
        "research_events",
        ["run_id", "occurred_at"],
        unique=False,
    )

    op.create_table(
        "research_evaluations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("formal_research_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("conclusion", sa.String(length=16), nullable=False),
        sa.Column("evaluation_sha256", sa.String(length=64), nullable=False),
        sa.Column("supersedes_evaluation_id", sa.String(length=36), nullable=True),
        sa.Column("supporting_evidence", sa.JSON(), nullable=False),
        sa.Column("opposing_evidence", sa.JSON(), nullable=False),
        sa.Column("missing_evidence", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.JSON(), nullable=False),
        sa.Column("follow_up_recommendations", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "conclusion in ('研究通过', '有条件候选', '证据不足', '受阻', '不通过')",
            name="ck_research_evaluations_conclusion",
        ),
        sa.CheckConstraint("version > 0", name="ck_research_evaluations_version"),
        sa.ForeignKeyConstraint(["formal_research_id"], ["formal_researches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["supersedes_evaluation_id"], ["research_evaluations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evaluation_sha256", name="uq_research_evaluations_sha256"),
        sa.UniqueConstraint(
            "formal_research_id", "version", name="uq_research_evaluations_research_version"
        ),
        sa.UniqueConstraint(
            "supersedes_evaluation_id", name="uq_research_evaluations_supersedes"
        ),
    )
    op.create_index(
        "ix_research_evaluations_research_created",
        "research_evaluations",
        ["formal_research_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "research_evaluation_runs",
        sa.Column("evaluation_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["evaluation_id"], ["research_evaluations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.run_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("evaluation_id", "run_id"),
    )

    op.create_table(
        "research_evidence_refs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("evaluation_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("uri", sa.String(length=1000), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "kind in ('input_snapshot', 'code', 'environment', 'parameters', "
            "'ledger', 'statistics', 'report', 'limitation')",
            name="ck_research_evidence_refs_kind",
        ),
        sa.ForeignKeyConstraint(["evaluation_id"], ["research_evaluations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.run_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "evaluation_id", "kind", "uri", name="uq_research_evidence_refs_evaluation_kind_uri"
        ),
    )
    op.create_index(
        "ix_research_evidence_refs_run_kind",
        "research_evidence_refs",
        ["run_id", "kind"],
        unique=False,
    )

    op.create_table(
        "research_publications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("formal_research_id", sa.String(length=36), nullable=False),
        sa.Column("evaluation_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("publication_sha256", sa.String(length=64), nullable=False),
        sa.Column("supersedes_publication_id", sa.String(length=36), nullable=True),
        sa.Column("artifact_manifest_uri", sa.String(length=1000), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        sa.Column("issue_comment_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('pending', 'published', 'failed')",
            name="ck_research_publications_status",
        ),
        sa.CheckConstraint("version > 0", name="ck_research_publications_version"),
        sa.ForeignKeyConstraint(["evaluation_id"], ["research_evaluations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["formal_research_id"], ["formal_researches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["supersedes_publication_id"], ["research_publications.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("publication_sha256", name="uq_research_publications_sha256"),
        sa.UniqueConstraint(
            "formal_research_id", "version", name="uq_research_publications_research_version"
        ),
        sa.UniqueConstraint(
            "supersedes_publication_id", name="uq_research_publications_supersedes"
        ),
    )
    op.create_index(
        "ix_research_publications_status_created",
        "research_publications",
        ["status", "created_at"],
        unique=False,
    )

    op.create_table(
        "follow_up_research_proposals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("strategy_id", sa.String(length=80), nullable=False),
        sa.Column("source_evaluation_id", sa.String(length=36), nullable=False),
        sa.Column("source_evidence_ref_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("rationale", sa.String(length=2000), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="proposed"),
        sa.Column("proposal_json", sa.JSON(), nullable=False),
        sa.Column("converted_plan_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status in ('proposed', 'accepted', 'rejected', 'converted')",
            name="ck_follow_up_research_proposals_status",
        ),
        sa.ForeignKeyConstraint(
            ["converted_plan_id"], ["frozen_research_plans.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_evaluation_id"], ["research_evaluations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_evidence_ref_id"], ["research_evidence_refs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategy_definitions.strategy_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "converted_plan_id", name="uq_follow_up_research_proposals_converted_plan"
        ),
    )
    op.create_index(
        "ix_follow_up_research_proposals_strategy_created",
        "follow_up_research_proposals",
        ["strategy_id", "created_at"],
        unique=False,
    )

    _install_postgres_immutability_guards()


def _install_postgres_immutability_guards() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE FUNCTION prevent_immutable_research_mutation()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'immutable research record: %', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in (
        "frozen_research_plans",
        "research_plan_approvals",
        "research_events",
        "research_evaluations",
        "research_evaluation_runs",
        "research_evidence_refs",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_immutable_research_mutation()
            """
        )
    op.execute(
        """
        CREATE FUNCTION prevent_terminal_publication_mutation()
        RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' OR OLD.status IN ('published', 'failed') THEN
            RAISE EXCEPTION 'terminal research publication is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_research_publications_terminal_immutable
        BEFORE UPDATE OR DELETE ON research_publications
        FOR EACH ROW EXECUTE FUNCTION prevent_terminal_publication_mutation()
        """
    )


def downgrade() -> None:
    raise RuntimeError("研究领域记录含不可变审计证据，禁止自动降级；请使用受控前向迁移。")
