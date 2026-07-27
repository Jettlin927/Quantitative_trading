from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from sqlalchemy.orm import Session

from .contracts import QualityCheckContract, QualityRuleResult


FamilyEvaluator = Callable[[Session, QualityCheckContract], list[QualityRuleResult]]


@dataclass(frozen=True)
class QualityRuleFamily:
    """一个静态质量规则族。

    evaluator 只能读取 runner 提供的 inspection session，并返回 QualityRuleResult
    值对象。它不得提交事务、写质量运行表或组装 API/CLI 投影。异常由外层 runner
    保持现有 engine.execution/failed 语义。
    """

    family_id: str
    evaluator: FamilyEvaluator
    stop_on_blocked: bool = False

    def __post_init__(self) -> None:
        if not self.family_id or self.family_id.strip() != self.family_id:
            raise ValueError("family_id 必须是非空规范字符串")


def evaluate_quality_families(
    db: Session,
    contract: QualityCheckContract,
    registry: Sequence[QualityRuleFamily],
) -> list[QualityRuleResult]:
    """按登记顺序同步执行规则族，并保持结果身份唯一。

    runner 负责在 PostgreSQL 上提供只读 REPEATABLE READ inspection session 和
    statement timeout；本 seam 不开启、提交或回滚事务。登记顺序就是执行顺序，
    标记 stop_on_blocked 的族产生 blocker 后会立即短路。
    """

    family_ids = [family.family_id for family in registry]
    if len(family_ids) != len(set(family_ids)):
        raise ValueError("family_id 在质量 registry 中必须唯一")

    results: list[QualityRuleResult] = []
    identities: set[tuple[str, str]] = set()
    for family in registry:
        family_results = family.evaluator(db, contract)
        for result in family_results:
            identity = (result.rule_id, result.table_name)
            if identity in identities:
                raise ValueError("(rule_id, table_name) 在一次质量运行中必须唯一")
            identities.add(identity)
            results.append(result)
        if family.stop_on_blocked and any(result.status == "blocked" for result in family_results):
            break
    return results
