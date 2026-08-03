"""add immutable personal notebook versions and verification loop

Revision ID: 0017_personal_notebook_t4
Revises: 0016_personal_analysis_t3
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0017_personal_notebook_t4"
down_revision = "0016_personal_analysis_t3"
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

    op.add_column("personal_research_records", sa.Column("current_version_id", sa.String(length=36)), schema="private_workbench")
    op.add_column("personal_research_records", sa.Column("source_run_id", sa.String(length=36)), schema="private_workbench")
    op.add_column("personal_research_records", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), schema="private_workbench")
    op.add_column("personal_research_records", sa.Column("purged_at", sa.DateTime(timezone=True)), schema="private_workbench")
    op.create_foreign_key(
        "fk_personal_record_source_run",
        "personal_research_records",
        "personal_analysis_runs",
        ["source_run_id"],
        ["id"],
        source_schema="private_workbench",
        referent_schema="private_workbench",
        ondelete="RESTRICT",
    )

    op.create_table(
        "personal_record_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("record_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", sa.String(length=36)),
        sa.Column("derived_relation", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_hash", sa.String(length=64), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        *_envelope_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["private_workbench.personal_workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["record_id"], ["private_workbench.personal_research_records.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("record_id", "version", name="uq_personal_record_version"),
        sa.UniqueConstraint("workspace_id", "idempotency_hash", name="uq_personal_record_version_idempotency"),
        schema="private_workbench",
    )
    op.create_table(
        "personal_record_private_fragments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("record_id", sa.String(length=36), nullable=False),
        sa.Column("record_version_id", sa.String(length=36), nullable=False),
        sa.Column("holding_id", sa.String(length=36), nullable=False),
        *_envelope_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["private_workbench.personal_workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["record_id"], ["private_workbench.personal_research_records.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["record_version_id"], ["private_workbench.personal_record_versions.id"], ondelete="CASCADE"),
        schema="private_workbench",
    )
    op.create_table(
        "personal_verification_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("record_id", sa.String(length=36), nullable=False),
        sa.Column("claim_id", sa.String(length=36)),
        sa.Column("initial_state", sa.String(length=24), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        *_envelope_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["private_workbench.personal_workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["record_id"], ["private_workbench.personal_research_records.id"], ondelete="CASCADE"),
        schema="private_workbench",
    )
    op.create_table(
        "personal_verification_observations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("record_version_id", sa.String(length=36), nullable=False),
        sa.Column("result", sa.String(length=24), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        *_envelope_columns(),
        sa.ForeignKeyConstraint(["workspace_id"], ["private_workbench.personal_workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["private_workbench.personal_verification_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["record_version_id"], ["private_workbench.personal_record_versions.id"], ondelete="CASCADE"),
        schema="private_workbench",
    )
    op.create_table(
        "personal_redaction_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("object_type", sa.String(length=32), nullable=False),
        sa.Column("object_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("idempotency_hash", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("backup_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["private_workbench.personal_workspaces.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("workspace_id", "idempotency_hash", name="uq_personal_redaction_idempotency"),
        schema="private_workbench",
    )
    for table in (
        "personal_record_versions",
        "personal_record_private_fragments",
        "personal_verification_items",
        "personal_verification_observations",
        "personal_redaction_events",
    ):
        op.execute(f"REVOKE ALL ON TABLE private_workbench.{table} FROM PUBLIC")


def downgrade() -> None:
    raise RuntimeError("private_workbench 是持久资产，禁止自动降级；请使用受控恢复流程。")
