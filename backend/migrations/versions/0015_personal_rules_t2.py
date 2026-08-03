"""add personal observation rule revisions and evaluations

Revision ID: 0015_personal_rules_t2
Revises: 0014_personal_portfolio_t1
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0015_personal_rules_t2"
down_revision = "0014_personal_portfolio_t1"
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

    op.create_table(
        "personal_rule_instances",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("template_id", sa.String(length=64), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        *_envelope_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["private_workbench.personal_workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema="private_workbench",
    )
    op.create_table(
        "personal_rule_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("rule_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("idempotency_hash", sa.String(length=64), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        *_envelope_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["private_workbench.personal_workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_id"], ["private_workbench.personal_rule_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", "revision", name="uq_personal_rule_revision"),
        sa.UniqueConstraint("workspace_id", "idempotency_hash", name="uq_personal_rule_idempotency"),
        schema="private_workbench",
    )
    op.create_table(
        "personal_rule_evaluation_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("idempotency_hash", sa.String(length=64), nullable=False),
        *_envelope_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["private_workbench.personal_workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "idempotency_hash", name="uq_personal_rule_batch_idempotency"),
        schema="private_workbench",
    )
    op.add_column("personal_rule_evaluations", sa.Column("batch_id", sa.String(length=36), nullable=True), schema="private_workbench")
    op.add_column("personal_rule_evaluations", sa.Column("rule_revision_id", sa.String(length=36), nullable=True), schema="private_workbench")
    op.add_column("personal_rule_evaluations", sa.Column("result", sa.String(length=32), nullable=True), schema="private_workbench")
    op.add_column("personal_rule_evaluations", sa.Column("as_of", sa.DateTime(timezone=True), nullable=True), schema="private_workbench")
    op.add_column("personal_rule_evaluations", sa.Column("evidence_ids", sa.JSON(), nullable=True), schema="private_workbench")
    op.add_column("personal_rule_evaluations", sa.Column("fingerprint", sa.String(length=64), nullable=True), schema="private_workbench")
    op.create_foreign_key("fk_personal_rule_evaluation_batch", "personal_rule_evaluations", "personal_rule_evaluation_batches", ["batch_id"], ["id"], source_schema="private_workbench", referent_schema="private_workbench", ondelete="CASCADE")
    op.create_foreign_key("fk_personal_rule_evaluation_revision", "personal_rule_evaluations", "personal_rule_revisions", ["rule_revision_id"], ["id"], source_schema="private_workbench", referent_schema="private_workbench", ondelete="CASCADE")
    for table in (
        "personal_rule_instances",
        "personal_rule_revisions",
        "personal_rule_evaluation_batches",
    ):
        op.execute(f"REVOKE ALL ON TABLE private_workbench.{table} FROM PUBLIC")


def downgrade() -> None:
    raise RuntimeError("private_workbench 是持久资产，禁止自动降级；请使用受控恢复流程。")
