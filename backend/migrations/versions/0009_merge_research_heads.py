"""merge research history and orchestration heads

Revision ID: 0009_merge_research_heads
Revises: 0008_research_history_provenance, 0008_research_orchestration
"""

from __future__ import annotations


revision = "0009_merge_research_heads"
down_revision = (
    "0008_research_history_provenance",
    "0008_research_orchestration",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError("研究历史与编排已收敛为单一 schema head，禁止自动降级；请使用受控前向迁移。")
