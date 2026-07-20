"""add immutable research publication issue mappings

Revision ID: 0010_research_issue_mapping
Revises: 0009_merge_research_heads
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_research_issue_mapping"
down_revision = "0009_merge_research_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_publication_issue_mappings",
        sa.Column("formal_research_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        sa.Column(
            "source",
            sa.String(length=24),
            server_default="historical_import",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source = 'historical_import'",
            name="ck_research_publication_issue_mappings_source",
        ),
        sa.ForeignKeyConstraint(
            ["formal_research_id"],
            ["formal_researches.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("formal_research_id"),
        sa.UniqueConstraint(
            "issue_number",
            name="uq_research_publication_issue_mappings_issue",
        ),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE TRIGGER trg_research_publication_issue_mappings_immutable
            BEFORE UPDATE OR DELETE ON research_publication_issue_mappings
            FOR EACH ROW EXECUTE FUNCTION prevent_immutable_research_mutation()
            """
        )


def downgrade() -> None:
    raise RuntimeError(
        "历史研究发布 Issue 映射属于不可变审计事实，"
        "禁止自动降级；请使用受控前向迁移。"
    )
