"""退役数据质量运行注册表

数据质量检查运行时（backend/app/data_quality/）已物理删除；其注册表
data_quality_runs / data_quality_results 一并移除。data_snapshots 是研究审计链
（research_runs → data_snapshots）的一环，保留为历史表，仅解除其对
data_quality_runs 的外键引用；quality_run_id 列保留为历史列。

Revision ID: 0020_drop_data_quality_registry
Revises: 0019_personal_realized_trades
"""

from __future__ import annotations

from alembic import op


revision = "0020_drop_data_quality_registry"
down_revision = "0019_personal_realized_trades"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint(
            "data_snapshots_quality_run_id_fkey",
            "data_snapshots",
            type_="foreignkey",
        )
    op.drop_index("ix_data_quality_results_run_status", table_name="data_quality_results")
    op.drop_table("data_quality_results")
    op.drop_index("ix_data_quality_runs_scope_started", table_name="data_quality_runs")
    op.drop_table("data_quality_runs")


def downgrade() -> None:
    raise RuntimeError(
        "数据质量注册表已退役，禁止自动降级重建；历史数据按审计保留策略另行恢复。"
    )
