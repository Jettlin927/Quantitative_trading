from __future__ import annotations

from sqlalchemy.orm import Session

from .contracts import QualityCheckContract, QualityRuleResult
from .family_registry import QualityRuleFamily, evaluate_quality_families
from .rules import evaluate_remaining_quality_rules
from .schema_family import evaluate_schema_family


# 唯一静态登记点。后续 migrate 票按现有实际顺序把 legacy 逐族替换。
QUALITY_FAMILY_REGISTRY: tuple[QualityRuleFamily, ...] = (
    QualityRuleFamily("schema", evaluate_schema_family, stop_on_blocked=True),
    QualityRuleFamily("legacy", evaluate_remaining_quality_rules),
)


def evaluate_registered_quality_families(
    db: Session,
    contract: QualityCheckContract,
) -> list[QualityRuleResult]:
    return evaluate_quality_families(db, contract, QUALITY_FAMILY_REGISTRY)
