"""新增持久工具证据与统一能力审计账本

Revision ID: 0023_tool_evidence
Revises: 0022_automatic_briefings
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0023_tool_evidence"
down_revision = "0022_automatic_briefings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.create_table(
        "personal_tool_evidence_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_id_hmac", sa.String(length=64), nullable=False),
        sa.Column("logical_identity_hmac", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("persistence", sa.String(length=24), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("authorization_snapshot_id", sa.String(length=160), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("payload_schema", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["private_workbench.personal_workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "evidence_id_hmac",
            name="uq_personal_tool_evidence_identity",
        ),
        schema="private_workbench",
    )
    op.create_index(
        "ix_personal_tool_evidence_logical_identity",
        "personal_tool_evidence_records",
        ["workspace_id", "logical_identity_hmac"],
        schema="private_workbench",
    )
    op.create_index(
        "ix_personal_tool_evidence_expires_at",
        "personal_tool_evidence_records",
        ["expires_at"],
        schema="private_workbench",
    )
    op.create_table(
        "personal_capability_audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=100), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("canonical_tool", sa.String(length=100), nullable=False),
        sa.Column("arguments_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("evidence_id_hmacs", sa.JSON(), nullable=False),
        sa.Column("field_coverage", sa.Numeric(8, 6), nullable=True),
        sa.Column("freshness_seconds", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(16, 8), nullable=True),
        sa.Column("policy_revision", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["private_workbench.personal_workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="private_workbench",
    )
    op.create_index(
        "ix_personal_capability_audit_request",
        "personal_capability_audit_events",
        ["workspace_id", "request_id"],
        schema="private_workbench",
    )
    for table_name in (
        "personal_tool_evidence_records",
        "personal_capability_audit_events",
    ):
        op.execute(
            f"REVOKE ALL ON TABLE private_workbench.{table_name} FROM PUBLIC"
        )


def downgrade() -> None:
    raise RuntimeError(
        "工具证据与能力审计属于持久资产，禁止自动降级；请使用受控恢复流程。"
    )
