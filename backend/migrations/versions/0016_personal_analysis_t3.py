"""add personal analysis previews runs evidence and claims

Revision ID: 0016_personal_analysis_t3
Revises: 0015_personal_rules_t2
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0016_personal_analysis_t3"
down_revision = "0015_personal_rules_t2"
branch_labels = None
depends_on = None


def _envelope_columns() -> list[sa.Column]:
    return [
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("payload_schema", sa.String(length=32), nullable=False),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for name, column_type in (
        ("provider", sa.String(length=32)),
        ("model", sa.String(length=80)),
        ("config_revision", sa.String(length=64)),
        ("expires_at", sa.DateTime(timezone=True)),
        ("consumed_at", sa.DateTime(timezone=True)),
    ):
        op.add_column(
            "personal_analysis_drafts",
            sa.Column(name, column_type, nullable=True),
            schema="private_workbench",
        )

    op.create_table(
        "personal_evidence_packs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("draft_id", sa.String(length=36), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("context_sha256", sa.String(length=64), nullable=False),
        *_envelope_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["private_workbench.personal_workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["draft_id"], ["private_workbench.personal_analysis_drafts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_id", name="uq_personal_evidence_pack_draft"),
        schema="private_workbench",
    )
    op.create_table(
        "personal_evidence_refs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("pack_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("public_source_id", sa.String(length=160), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        *_envelope_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["pack_id"], ["private_workbench.personal_evidence_packs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pack_id", "public_source_id", name="uq_personal_evidence_ref_source"),
        schema="private_workbench",
    )
    op.create_table(
        "personal_analysis_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("draft_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="2", nullable=False),
        sa.Column("idempotency_hash", sa.String(length=64), nullable=False),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        *_envelope_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["private_workbench.personal_workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["draft_id"], ["private_workbench.personal_analysis_drafts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "idempotency_hash", name="uq_personal_analysis_run_idempotency"),
        schema="private_workbench",
    )
    op.create_table(
        "personal_analysis_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(16, 8), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        *_envelope_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["private_workbench.personal_analysis_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "attempt", name="uq_personal_analysis_attempt"),
        schema="private_workbench",
    )
    op.create_table(
        "personal_analysis_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=True),
        *_envelope_columns(),
        sa.ForeignKeyConstraint(["run_id"], ["private_workbench.personal_analysis_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_personal_analysis_event_sequence"),
        schema="private_workbench",
    )
    op.create_table(
        "personal_ai_claims",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("claim_order", sa.Integer(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        *_envelope_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["private_workbench.personal_analysis_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "claim_order", name="uq_personal_ai_claim_order"),
        schema="private_workbench",
    )
    for table in (
        "personal_evidence_packs",
        "personal_evidence_refs",
        "personal_analysis_runs",
        "personal_analysis_attempts",
        "personal_analysis_events",
        "personal_ai_claims",
    ):
        op.execute(f"REVOKE ALL ON TABLE private_workbench.{table} FROM PUBLIC")


def downgrade() -> None:
    raise RuntimeError("private_workbench 是持久资产，禁止自动降级；请使用受控恢复流程。")
